#!/usr/bin/env python3
"""Personal CLI for private, evidence-first NotebookLM podcast runs."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from podcast_workflow import PodcastRequest, RunStage, RunStore


async def execute_run(store: RunStore) -> None:
    """Load the expensive runner lazily so status/enqueue remain lightweight."""

    from podcast_evidence_runner import EvidencePodcastRunner

    await EvidencePodcastRunner(store).execute()


def _request_from_args(args: argparse.Namespace) -> PodcastRequest:
    return PodcastRequest(
        prompt=args.prompt,
        audience=args.audience,
        angle=args.angle,
        risk=args.risk,
        audio_format=args.audio_format,
        language=args.language,
        length=args.length,
        min_sources=args.min_sources,
        max_sources=args.max_sources,
    )


def _add_request_arguments(parser: argparse.ArgumentParser, *, dry_run: bool = False) -> None:
    parser.add_argument("prompt")
    parser.add_argument("--audience", default="curious, technically literate listener")
    parser.add_argument("--angle", default="")
    parser.add_argument("--risk", choices=("auto", "ordinary", "high"), default="auto")
    parser.add_argument(
        "--format",
        dest="audio_format",
        choices=("auto", "deep-dive", "debate"),
        default="deep-dive",
    )
    parser.add_argument("--language", default="en")
    parser.add_argument("--length", default="long")
    parser.add_argument("--min-sources", type=int, default=6)
    parser.add_argument("--max-sources", type=int, default=10)
    if dry_run:
        parser.add_argument(
            "--dry-run", action="store_true", help="create and validate the private run only"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home() / ".notebooklm")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    _add_request_arguments(run, dry_run=True)
    enqueue = subparsers.add_parser("enqueue")
    _add_request_arguments(enqueue)
    status = subparsers.add_parser("status")
    status.add_argument("run_id")
    status.add_argument("--json", action="store_true")
    resume = subparsers.add_parser("resume")
    resume.add_argument("run_id")
    return parser


def _existing_store(home: Path, run_id: str) -> RunStore:
    if Path(run_id).name != run_id:
        raise ValueError("invalid run id")
    path = home / "podcast_runs" / run_id
    if not path.is_dir():
        raise ValueError(f"run not found: {run_id}")
    return RunStore(path)


def run_cli(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "enqueue":
            request = _request_from_args(args)
            inbox = args.home / "podcast_inbox"
            inbox.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(inbox, 0o700)
            created = datetime.now(timezone.utc)
            run_id = f"{created.strftime('%Y%m%dT%H%M%SZ')}-{os.urandom(4).hex()}"
            payload = {**asdict(request), "run_id": run_id, "created_at": created.isoformat()}
            target = inbox / f"{run_id}.json"
            target.write_text(json.dumps(payload, indent=2) + "\n")
            os.chmod(target, 0o600)
            print(run_id)
            return 0
        if args.command == "run":
            store = RunStore.create(args.home / "podcast_runs", _request_from_args(args))
            if not args.dry_run:
                asyncio.run(execute_run(store))
            print(store.path.name)
            return 0
        store = _existing_store(args.home, args.run_id)
        state = store.read_json("state.json")
        if args.command == "status":
            print(json.dumps(state, indent=2) if args.json else state["stage"])
            return 0
        if RunStage(state["stage"]) is not RunStage.RETRYABLE_FAILURE:
            print(f"error: run is not retryable ({state['stage']})", file=sys.stderr)
            return 2
        asyncio.run(execute_run(store))
        print(store.path.name)
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
