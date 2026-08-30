#!/usr/bin/env python3
"""Opt-in dual-discovery runner for evidence-first NotebookLM podcasts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import urllib.parse
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from podcast_queue_writer import extract_json
from podcast_workflow import (
    PodcastRequest,
    QualityRejected,
    RunStage,
    RunStore,
    canonicalize_url,
    classify_source_provenance,
    evaluate_quality,
)

from notebooklm import NotebookLMClient, resolve_chat_reference_passage
from notebooklm.rpc import AudioFormat, AudioLength

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".claude/workflows/podcast-deep-research.js"
GROUNDING_QUESTIONS = (
    "Build an evidence map for the central question. Identify the major claims and cite sources.",
    "What is the strongest counterevidence, contradiction, or limitation in this corpus? Cite sources.",
    "What surprising, well-supported connections would make a compelling audio story? Cite sources.",
)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}  # type: ignore[arg-type]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _sane_fulltext(content: str) -> bool:
    lowered = content.lower()
    blocked = ("access denied", "enable javascript", "checking your browser", "page not found")
    return len(content.strip()) >= 500 and not any(marker in lowered[:2_000] for marker in blocked)


async def _run_process(args: list[str], *, timeout: float) -> str:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"process timed out after {timeout:.0f}s") from None
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        detail = stderr.decode(errors="replace")[-2_000:]
        raise RuntimeError(f"process exited {process.returncode}: {detail}")
    return stdout.decode(errors="replace")


def _claude_result(raw: str) -> dict:
    outer = extract_json(raw)
    if outer is None:
        raise RuntimeError("Claude returned no JSON")
    for key in ("structured_output", "result"):
        nested = outer.get(key)
        if isinstance(nested, dict):
            return nested
        if isinstance(nested, str):
            parsed = extract_json(nested)
            if parsed is not None:
                return parsed
    return outer


class EvidencePodcastRunner:
    """Execute one auditable, fail-closed podcast run."""

    def __init__(self, store: RunStore):
        self.store = store
        self.request = PodcastRequest.from_dict(store.read_json("request.json"))

    async def execute(self) -> None:
        try:
            await self._execute()
        except QualityRejected as exc:
            self._fail(RunStage.REJECTED_QUALITY, str(exc))
            raise
        except asyncio.CancelledError:
            self._fail(RunStage.CANCELLED, "run cancelled")
            raise
        except Exception as exc:
            self._fail(RunStage.RETRYABLE_FAILURE, str(exc))
            raise

    def _fail(self, stage: RunStage, reason: str) -> None:
        state = self.store.read_json("state.json")
        current = RunStage(state["stage"])
        if current in {RunStage.REJECTED_QUALITY, RunStage.DELIVERED, RunStage.CANCELLED}:
            return
        self.store.transition(stage, error=reason, resume_stage=current.value)

    async def _execute(self) -> None:
        current = RunStage(self.store.read_json("state.json")["stage"])
        if current is RunStage.RETRYABLE_FAILURE:
            state = self.store.read_json("state.json")
            resume_stage = state.get("resume_stage")
            output_file = self.store.path / f"{self.store.path.name}.mp3"

            # If failed during delivery and audio already downloaded on disk:
            if resume_stage == RunStage.DELIVERING.value and (
                state.get("audio_path") or output_file.is_file()
            ):
                output = Path(state.get("audio_path") or output_file)
                if output.is_file() and output.stat().st_size > 0:
                    self.store.transition(RunStage.DELIVERING)
                    await self._deliver(output)
                    self.store.transition(RunStage.DELIVERED)
                    return

            # If failed while downloading/generating and task_id was already persisted:
            generation_task_id = state.get("generation_task_id")
            notebook_id = state.get("notebook_id")
            if (
                resume_stage in {RunStage.DOWNLOADING.value, RunStage.GENERATING.value}
                and generation_task_id
                and notebook_id
            ):
                self.store.transition(RunStage.DOWNLOADING)
                async with NotebookLMClient.from_storage() as client:
                    await self._download_and_deliver(
                        client,
                        notebook_id,
                        generation_task_id,
                        state.get("final_source_ids", []),
                    )
                return

            self.store.transition(RunStage.DISCOVERING, retrying=True)
        elif current is RunStage.REQUESTED:
            self.store.transition(RunStage.DISCOVERING)
        else:
            raise ValueError(f"cannot execute run from {current.value}")

        async with NotebookLMClient.from_storage() as client:
            notebook = await client.notebooks.create(self.request.prompt[:120])
            notebook_id = notebook.id
            self.store.transition(RunStage.INGESTING, notebook_id=notebook_id)

            claude_task = asyncio.create_task(self._claude_discovery())
            notebook_task = asyncio.create_task(self._notebook_discovery(client, notebook_id))
            claude_result, notebook_result = await asyncio.gather(claude_task, notebook_task)
            self.store.write_json("claude_discovery.json", claude_result)
            self.store.write_json("notebooklm_research.json", notebook_result)

            claude_urls = {
                canonicalize_url(source["url"])
                for source in claude_result.get("payload", claude_result).get("sources", [])
                if isinstance(source, dict) and isinstance(source.get("url"), str)
            }
            notebook_urls = {
                canonicalize_url(source["url"])
                for task in notebook_result["tasks"]
                for source in task["sources"]
                if source.get("url")
            }
            remaining_quota = max(0, (self.request.max_sources + 2) - len(notebook_urls))
            for url in sorted(claude_urls - notebook_urls)[:remaining_quota]:
                await client.sources.add_url(notebook_id, url, wait=True, wait_timeout=180)

            self.store.transition(RunStage.GROUNDING)
            ledger = await self._source_ledger(client, notebook_id, claude_urls, notebook_urls)
            self.store.write_json("source_ledger.json", ledger)
            sane_ids = [row["source_id"] for row in ledger if row["ready"] and row["sane"]]
            if len(sane_ids) < self.request.min_sources:
                raise QualityRejected(
                    f"only {len(sane_ids)} sources became READY with sane full text"
                )
            grounding = await self._ground(client, notebook_id, sane_ids)
            self.store.write_json("grounding.json", grounding)

            self.store.transition(RunStage.ADJUDICATING)
            adjudication = await self._adjudicate(ledger, grounding)
            critic = await self._critic(ledger, grounding, adjudication)
            self.store.write_json("claim_matrix.json", adjudication)
            self.store.write_json("critic.json", critic)
            quality = evaluate_quality(
                ledger,
                adjudication["claims"],
                risk=self._effective_risk(),
                requested_format=adjudication.get("audio_format", self.request.audio_format),
                critic=critic,
                min_sources=self.request.min_sources,
                max_sources=self.request.max_sources,
            )
            brief = adjudication["editorial_brief"]
            self.store.write_json("editorial_brief.json", brief)
            (self.store.path / "editorial_brief.md").write_text(brief["instructions"] + "\n")
            os.chmod(self.store.path / "editorial_brief.md", 0o600)
            self.store.transition(
                RunStage.READY_TO_GENERATE,
                final_source_ids=list(quality.source_ids),
                audio_format=quality.audio_format,
            )

            self.store.transition(RunStage.GENERATING)
            status = await client.artifacts.generate_audio(
                notebook_id,
                source_ids=list(quality.source_ids),
                language=self.request.language,
                instructions=brief["instructions"],
                audio_format=(
                    AudioFormat.DEBATE
                    if quality.audio_format == "debate"
                    else AudioFormat.DEEP_DIVE
                ),
                audio_length=AudioLength.LONG,
            )
            self.store.transition(RunStage.DOWNLOADING, generation_task_id=status.task_id)
            await self._download_and_deliver(
                client, notebook_id, status.task_id, list(quality.source_ids)
            )

    async def _download_and_deliver(
        self,
        client: NotebookLMClient,
        notebook_id: str,
        task_id: str,
        source_ids: list[str],
    ) -> None:
        status = await client.artifacts.wait_for_completion(notebook_id, task_id, timeout=3_600)
        if not status.is_complete:
            raise RuntimeError(f"audio generation ended in {status.status}")
        output = self.store.path / f"{self.store.path.name}.mp3"
        await client.artifacts.download_audio(notebook_id, str(output), artifact_id=status.task_id)
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError("downloaded audio is empty")
        digest = hashlib.sha256(output.read_bytes()).hexdigest()
        self.store.transition(RunStage.DELIVERING, audio_path=str(output), sha256=digest)
        self.store.write_json(
            "manifest.json",
            {
                "run_id": self.store.path.name,
                "notebook_id": notebook_id,
                "generation_task_id": status.task_id,
                "source_ids": source_ids,
                "audio_path": str(output),
                "sha256": digest,
            },
        )
        await self._deliver(output)
        self.store.transition(RunStage.DELIVERED)

    async def _claude_discovery(self) -> dict:
        topic_id = self.store.path.name[-8:]
        workflow_args = {
            "topic": {
                "id": topic_id,
                "title": self.request.prompt,
                "query": self.request.prompt,
                "debate": self.request.audio_format == "debate",
                "domain": "general",
            },
            "date": self.store.path.name[:8].replace("T", ""),
            "avoidUrls": [],
            "maxSources": min(8, self.request.max_sources),
            "angle": self.request.angle,
        }
        prompt = (
            f"Call the Workflow tool exactly once with scriptPath {str(WORKFLOW_PATH)!r} "
            f"and args {json.dumps(workflow_args)}. Return only its JSON result."
        )
        raw = await _run_process(
            [
                "claude",
                "-p",
                prompt,
                "--effort",
                "ultracode",
                "--max-budget-usd",
                "6",
                "--output-format",
                "json",
                "--no-session-persistence",
                "--allowedTools",
                "Workflow",
                "Read",
                "ToolSearch",
                "WebFetch",
                "mcp__claude_ai_Exa__*",
                "mcp__claude_ai_Semantic_Scholar__*",
                "mcp__claude_ai_Consensus__*",
            ],
            timeout=1_200,
        )
        return _claude_result(raw)

    async def _notebook_discovery(self, client: NotebookLMClient, notebook_id: str) -> dict:
        queries = (
            f"{self.request.prompt}: strongest primary evidence and authoritative sources",
            f"{self.request.prompt}: strongest counterevidence, limitations, and disagreements",
        )
        starts = await asyncio.gather(
            *(
                client.research.start(notebook_id, query, source="web", mode="deep")
                for query in queries
            )
        )
        tasks = await asyncio.gather(
            *(
                client.research.wait_for_completion(notebook_id, start.task_id, timeout=1_800)
                for start in starts
            )
        )
        result = []
        max_per_task = max(2, self.request.max_sources // 2)
        for start, task in zip(starts, tasks, strict=True):
            sources = [source for source in task.sources if not source.is_report and source.url][
                :max_per_task
            ]
            if sources:
                await client.research.import_sources_with_verification(
                    notebook_id, start.task_id, sources, max_elapsed=1_800
                )
            result.append(
                {
                    "task_id": start.task_id,
                    "sources": [_jsonable(source) for source in sources],
                    "reports": [_jsonable(source) for source in task.sources if source.is_report],
                }
            )
        return {"tasks": result}

    async def _source_ledger(
        self,
        client: NotebookLMClient,
        notebook_id: str,
        claude_urls: set[str],
        notebook_urls: set[str],
    ) -> list[dict]:
        rows = []
        for source in await client.sources.list(notebook_id):
            url = getattr(source, "url", "") or ""
            try:
                canonical = canonicalize_url(url)
            except ValueError:
                canonical = ""
            fulltext_content = ""
            try:
                fulltext = await client.sources.get_fulltext(notebook_id, source.id)
                fulltext_content = fulltext.content
                sane = _sane_fulltext(fulltext_content)
            except Exception:
                sane = False
            channels = []
            if canonical in claude_urls:
                channels.append("claude")
            if canonical in notebook_urls:
                channels.append("notebooklm")
            host = urllib.parse.urlsplit(canonical).hostname if canonical else ""
            provenance = classify_source_provenance(canonical, source.title, fulltext_content)
            rows.append(
                {
                    "source_id": source.id,
                    "url": canonical,
                    "title": source.title,
                    "publisher": provenance.get("publisher") or host or source.id,
                    "channels": channels,
                    "primary": provenance.get("primary", False),
                    "evidence_tier": provenance.get("evidence_tier", "secondary_or_commentary"),
                    "side": "neutral",
                    "ready": True,
                    "sane": sane,
                }
            )
        return rows

    async def _ground(
        self, client: NotebookLMClient, notebook_id: str, source_ids: list[str]
    ) -> dict:
        suggestions = await client.notebooks.suggest_prompts(notebook_id, source_ids=source_ids)
        answers = []
        for question in GROUNDING_QUESTIONS:
            answer = await client.chat.ask(notebook_id, question, source_ids=source_ids)
            references = []
            for reference in answer.references:
                passage = await resolve_chat_reference_passage(client, notebook_id, reference)
                references.append({"reference": _jsonable(reference), "passage": passage[:1_500]})
            answers.append(
                {"question": question, "answer": answer.answer, "references": references}
            )
        return {"suggestions": _jsonable(suggestions), "answers": answers}

    async def _adjudicate(self, ledger: list[dict], grounding: dict) -> dict:
        schema = {
            "type": "object",
            "required": ["claims", "audio_format", "editorial_brief"],
            "properties": {
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["claim", "major", "supporting_source_ids"],
                        "properties": {
                            "claim": {"type": "string"},
                            "major": {"type": "boolean"},
                            "supporting_source_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                },
                "audio_format": {"enum": ["deep-dive", "debate"]},
                "editorial_brief": {
                    "type": "object",
                    "required": ["instructions"],
                    "properties": {"instructions": {"type": "string"}},
                },
            },
        }
        prompt = (
            "Create a source-ID-bound claim matrix and fascinating evidence-led audio brief. "
            "Never cite an ID outside the ledger. Include counterevidence and uncertainty.\n"
            + json.dumps(
                {"request": _jsonable(self.request), "ledger": ledger, "grounding": grounding}
            )
        )
        return await self._tool_less_claude(prompt, schema, budget="1.5")

    async def _critic(self, ledger: list[dict], grounding: dict, adjudication: dict) -> dict:
        schema = {
            "type": "object",
            "required": ["passed", "scores", "findings"],
            "properties": {
                "passed": {"type": "boolean"},
                "scores": {
                    "type": "object",
                    "required": ["novelty", "tension", "audio_fit", "coverage"],
                    "properties": {
                        key: {"type": "integer", "minimum": 1, "maximum": 5}
                        for key in ("novelty", "tension", "audio_fit", "coverage")
                    },
                },
                "findings": {"type": "array", "items": {"type": "string"}},
            },
        }
        prompt = (
            "Adversarially audit this proposed episode. Fail on unsupported claims or false balance.\n"
            + json.dumps({"ledger": ledger, "grounding": grounding, "adjudication": adjudication})
        )
        return await self._tool_less_claude(prompt, schema, budget="0.5")

    async def _tool_less_claude(self, prompt: str, schema: dict, *, budget: str) -> dict:
        raw = await _run_process(
            [
                "claude",
                "-p",
                prompt,
                "--effort",
                "max",
                "--tools",
                "",
                "--max-budget-usd",
                budget,
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(schema),
                "--no-session-persistence",
            ],
            timeout=600,
        )
        return _claude_result(raw)

    def _effective_risk(self) -> str:
        if self.request.risk != "auto":
            return self.request.risk
        prompt = self.request.prompt.lower()
        high_risk = (
            "medical",
            "medicine",
            "health",
            "legal",
            "law",
            "finance",
            "investment",
            "safety",
        )
        return "high" if any(term in prompt for term in high_risk) else "ordinary"

    async def _deliver(self, output: Path) -> None:
        process = await asyncio.create_subprocess_exec(
            "rclone",
            "copyto",
            str(output),
            f"gdrive:Podcasts/{output.name}",
            "--checksum",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await process.communicate()
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError(f"rclone delivery failed: {stderr.decode(errors='replace')[-1000:]}")
