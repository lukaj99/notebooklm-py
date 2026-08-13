#!/usr/bin/env python3
"""Proactive podcast researcher + curator.

Run unattended (systemd timer) with no arguments. Each run:

0. Checks for a curator queue item first — see "Cloud-routine handoff" below.
   If one is due, it wins: its sources/title/format are used directly
   (richer research via Semantic Scholar/UpToDate/Exa/Consensus beats the
   PubMed fallback).
1. Otherwise, picks the next due topic from ``podcast_topics.json``
   (round-robin, skips any topic generated within the last
   ``--cooldown-days``) and researches it directly via PubMed E-utilities:
   recent, relevant articles, excluding PMIDs already used for that topic in
   a previous run (state file).
2. Either way, hands the curated sources off to
   ``podcast_pipeline.build_podcast`` (the orchestrator) to create the
   notebook, ingest sources, generate the EM-Cases-style audio overview, and
   download it.
3. Delivers: uploads the finished mp3 to Google Drive (rclone) and sends an
   ntfy notification with a short curator blurb explaining what's new and why
   these sources were picked.

Cloud-routine handoff
----------------------
A scheduled cloud agent (see ``scripts/podcast_researcher_agent.md``) has
access to connectors this local script doesn't (Semantic Scholar, UpToDate,
Exa, Consensus) but can't reach NotebookLM directly (no local Google auth,
and NotebookLM isn't a claude.ai-level connector — only a local/Desktop
custom connector). It does the *research and curation* and commits its
findings as ``scripts/podcast_queue/<topic-id>-<date>.json`` in this repo.
This script pulls a dedicated read-only clone of the repo
(``~/.notebooklm/podcast_queue_repo``, kept separate from the dev working
tree) each run and consumes the oldest unprocessed queue file, so the two
sides never touch each other's state directly — the queue directory in git
is the entire interface.

All state (rotation position, per-topic PMID history, processed queue files)
lives in ``~/.notebooklm/podcast_pipeline_state.json`` so reruns don't repeat
the same topic, resurface the same papers, or reprocess a queue file twice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from notebooklm import NotebookLMClient
from notebooklm.rpc import AudioFormat, AudioLength

sys.path.insert(0, str(Path(__file__).parent))
from podcast_pipeline import EM_CASES_STYLE, PodcastPipelineError, build_podcast  # noqa: E402

logger = logging.getLogger("podcast_researcher")

TOPICS_PATH = Path(__file__).parent / "podcast_topics.json"
STATE_PATH = Path.home() / ".notebooklm" / "podcast_pipeline_state.json"
OUT_DIR = Path.home() / "podcasts"
GDRIVE_REMOTE = "gdrive:Podcasts"
NTFY_URL = "http://localhost:2586/agent"
PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CONTACT_EMAIL = "luka.jovanovic67@gmail.com"
TOOL_NAME = "notebooklm-py-podcast-researcher"
MAX_SOURCES = 5
COOLDOWN_DAYS_DEFAULT = 21

QUEUE_REPO_DIR = Path.home() / ".notebooklm" / "podcast_queue_repo"
QUEUE_REPO_URL = "https://github.com/lukaj99/notebooklm-py.git"
QUEUE_SUBDIR = "scripts/podcast_queue"


def sync_queue_repo() -> Path | None:
    """Clone or fast-forward the dedicated read-only queue clone.

    Kept entirely separate from the dev working tree at
    ``/home/luka/projects/notebooklm-py`` — this script must never mutate the
    repo it lives in. Returns the queue directory path, or ``None`` if the
    sync failed (network down, repo unreachable) — callers should fall back
    to direct research rather than error out.
    """

    try:
        if not (QUEUE_REPO_DIR / ".git").exists():
            QUEUE_REPO_DIR.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            subprocess.run(
                ["git", "clone", "--depth", "1", QUEUE_REPO_URL, str(QUEUE_REPO_DIR)],
                check=True,
                capture_output=True,
                timeout=120,
            )
        else:
            subprocess.run(
                ["git", "-C", str(QUEUE_REPO_DIR), "fetch", "--depth", "1", "origin", "main"],
                check=True,
                capture_output=True,
                timeout=60,
            )
            subprocess.run(
                ["git", "-C", str(QUEUE_REPO_DIR), "reset", "--hard", "origin/main"],
                check=True,
                capture_output=True,
                timeout=30,
            )
    except Exception:
        logger.exception("queue repo sync failed")
        return None
    return QUEUE_REPO_DIR / QUEUE_SUBDIR


def next_queue_item(state: dict) -> tuple[dict, str] | None:
    """Return the oldest unprocessed queue item as ``(payload, filename)``, or None."""

    queue_dir = sync_queue_repo()
    if queue_dir is None or not queue_dir.is_dir():
        return None

    processed = set(state.setdefault("processed_queue_files", []))
    for path in sorted(queue_dir.glob("*.json")):
        if path.name in processed:
            continue
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            logger.exception("skipping unreadable queue file %s", path.name)
            processed.add(path.name)
            continue
        return payload, path.name
    return None


@dataclass
class Topic:
    id: str
    title: str
    pubmed_query: str
    debate: bool


def load_topics() -> list[Topic]:
    data = json.loads(TOPICS_PATH.read_text())
    return [Topic(t["id"], t["title"], t["pubmed_query"], t["debate"]) for t in data["topics"]]


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"rotation_index": 0, "topics": {}}
    return json.loads(STATE_PATH.read_text())


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def pick_topic(topics: list[Topic], state: dict, cooldown_days: float) -> Topic | None:
    """Round-robin starting at rotation_index, skipping topics on cooldown."""

    now = time.time()
    n = len(topics)
    start = state.get("rotation_index", 0) % n
    for offset in range(n):
        idx = (start + offset) % n
        topic = topics[idx]
        last_run = state.get("topics", {}).get(topic.id, {}).get("last_run_ts")
        if last_run is None or (now - last_run) >= cooldown_days * 86400:
            state["rotation_index"] = (idx + 1) % n
            return topic
    return None


async def research_topic(topic: Topic, exclude_pmids: set[str], limit: int) -> list[dict]:
    """Query PubMed for recent articles on the topic, excluding already-used PMIDs."""

    async with httpx.AsyncClient(timeout=30.0) as http:
        search_resp = await http.get(
            f"{PUBMED_BASE}/esearch.fcgi",
            params={
                "db": "pubmed",
                "term": topic.pubmed_query,
                "retmax": str(limit * 4),
                "sort": "date",
                "datetype": "pdat",
                "reldate": "730",
                "retmode": "json",
                "tool": TOOL_NAME,
                "email": CONTACT_EMAIL,
            },
        )
        search_resp.raise_for_status()
        ids = search_resp.json().get("esearchresult", {}).get("idlist", [])
        candidate_ids = [pmid for pmid in ids if pmid not in exclude_pmids][:limit]
        if not candidate_ids:
            return []

        summary_resp = await http.get(
            f"{PUBMED_BASE}/esummary.fcgi",
            params={
                "db": "pubmed",
                "id": ",".join(candidate_ids),
                "retmode": "json",
                "tool": TOOL_NAME,
                "email": CONTACT_EMAIL,
            },
        )
        summary_resp.raise_for_status()
        result = summary_resp.json().get("result", {})

    articles = []
    for pmid in candidate_ids:
        doc = result.get(pmid)
        if not doc:
            continue
        articles.append(
            {
                "pmid": pmid,
                "title": doc.get("title", "").strip() or f"PubMed article {pmid}",
                "pubdate": doc.get("pubdate", ""),
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            }
        )
    return articles


def deliver(mp3_path: Path, topic: Topic, articles: list[dict]) -> None:
    """Upload to Drive and send an ntfy notification with a curator blurb."""

    try:
        subprocess.run(
            ["rclone", "copy", str(mp3_path), GDRIVE_REMOTE, "--checksum"],
            check=True,
            capture_output=True,
            timeout=300,
        )
        drive_note = f"Uploaded to Google Drive: {GDRIVE_REMOTE}/{mp3_path.name}"
    except Exception:
        logger.exception("rclone upload failed")
        drive_note = "Google Drive upload failed — file is still on arch-vps at " + str(mp3_path)

    picks = "\n".join(
        f"- {a['title']}" + (f" ({a['pubdate']})" if a.get("pubdate") else "") for a in articles
    )
    message = (
        f"New podcast: {topic.title}\n\n"
        f"{len(articles)} new source(s) since last time:\n{picks}\n\n{drive_note}"
    )
    try:
        httpx.post(
            NTFY_URL,
            content=message.encode(),
            headers={"Title": f"Podcast ready: {topic.title}".encode("latin-1", "ignore")},
            timeout=10.0,
        )
    except Exception:
        logger.exception("ntfy notification failed")


def deliver_failure(topic: Topic, error: str) -> None:
    try:
        httpx.post(
            NTFY_URL,
            content=f"Podcast generation failed for '{topic.title}': {error}".encode(),
            headers={"Title": b"Podcast pipeline error"},
            timeout=10.0,
        )
    except Exception:
        logger.exception("ntfy failure notification failed")


async def _run_from_queue(payload: dict, filename: str, state: dict) -> int:
    """Process one cloud-curated queue item. Returns the process exit code."""

    topic = Topic(
        id=payload.get("topic_id", filename),
        title=payload.get("title", filename),
        pubmed_query="",
        debate=(payload.get("audio_format") == "debate"),
    )
    sources = payload.get("sources", [])
    if not sources:
        logger.warning("queue file %s has no sources, marking processed and skipping", filename)
        state.setdefault("processed_queue_files", []).append(filename)
        save_state(state)
        return 0

    articles = [{"pmid": None, "title": s.get("title", s["url"]), "pubdate": "", "url": s["url"]} for s in sources]
    instructions = EM_CASES_STYLE
    rationale = payload.get("rationale")
    if rationale:
        instructions += f"\n\nEditorial context for this episode: {rationale}"

    logger.info("processing queue item %s: %d source(s) for %s", filename, len(sources), topic.title)

    async with NotebookLMClient.from_storage() as client:
        try:
            mp3_path = await build_podcast(
                client,
                title=payload.get("title") or f"{topic.title} — {time.strftime('%Y-%m-%d')}",
                source_urls=[s["url"] for s in sources],
                instructions=instructions,
                audio_format=AudioFormat.DEBATE if topic.debate else AudioFormat.DEEP_DIVE,
                audio_length=AudioLength.LONG,
                out_dir=OUT_DIR,
            )
        except PodcastPipelineError as exc:
            logger.error("pipeline failed for queue item %s: %s", filename, exc)
            deliver_failure(topic, str(exc))
            return 1

    state.setdefault("processed_queue_files", []).append(filename)
    save_state(state)

    deliver(mp3_path, topic, articles)
    logger.info("done: %s", mp3_path)
    return 0


async def _run_from_pubmed_fallback(state: dict, cooldown_days: float) -> int:
    topics = load_topics()
    topic = pick_topic(topics, state, cooldown_days)
    if topic is None:
        logger.info("all topics on cooldown, nothing to do")
        return 0

    topic_state = state.setdefault("topics", {}).setdefault(topic.id, {"used_pmids": []})
    exclude_pmids = set(topic_state.get("used_pmids", []))

    articles = await research_topic(topic, exclude_pmids, MAX_SOURCES)
    if not articles:
        logger.info("no new articles for topic %s, skipping this cycle", topic.id)
        save_state(state)
        return 0

    logger.info("curated %d sources for %s (pubmed fallback)", len(articles), topic.title)

    async with NotebookLMClient.from_storage() as client:
        try:
            mp3_path = await build_podcast(
                client,
                title=f"{topic.title} — {time.strftime('%Y-%m-%d')}",
                source_urls=[a["url"] for a in articles],
                instructions=EM_CASES_STYLE,
                audio_format=AudioFormat.DEBATE if topic.debate else AudioFormat.DEEP_DIVE,
                audio_length=AudioLength.LONG,
                out_dir=OUT_DIR,
            )
        except PodcastPipelineError as exc:
            logger.error("pipeline failed for %s: %s", topic.id, exc)
            deliver_failure(topic, str(exc))
            save_state(state)
            return 1

    topic_state["last_run_ts"] = time.time()
    topic_state["used_pmids"] = list(exclude_pmids | {a["pmid"] for a in articles})
    save_state(state)

    deliver(mp3_path, topic, articles)
    logger.info("done: %s", mp3_path)
    return 0


async def run_once(cooldown_days: float = COOLDOWN_DAYS_DEFAULT) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    state = load_state()
    queued = next_queue_item(state)
    if queued is not None:
        payload, filename = queued
        return await _run_from_queue(payload, filename, state)

    return await _run_from_pubmed_fallback(state, cooldown_days)


def main() -> None:
    raise SystemExit(asyncio.run(run_once()))


if __name__ == "__main__":
    main()
