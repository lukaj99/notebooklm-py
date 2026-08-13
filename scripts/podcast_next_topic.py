#!/usr/bin/env python3
"""Pick the next due podcast topic and print workflow args as JSON.

The deterministic half of the scheduled deep-research run. A scheduled Claude
Code session calls this, feeds the JSON straight into the
``podcast-deep-research`` workflow (``.claude/workflows/podcast-deep-research.js``),
and commits the resulting queue file — so the session itself never has to
decide what to cover or remember what it already used.

Selection is least-recently-covered across *every* domain (medicine,
motorcycling, photography, tech, ...), using the committed queue files
themselves as the history — no separate state file to drift out of sync. A
topic whose most recent queue file is newer than ``--cooldown-days`` is
skipped; if every topic is on cooldown, this exits 0 having printed nothing,
which the caller should treat as "nothing due".

Usage:
    uv run scripts/podcast_next_topic.py                 # next due topic
    uv run scripts/podcast_next_topic.py --topic-id moto-tires-and-grip
    uv run scripts/podcast_next_topic.py --cooldown-days 30
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

TOPICS_PATH = Path(__file__).parent / "podcast_topics.json"
QUEUE_DIR = Path(__file__).parent / "podcast_queue"
COOLDOWN_DAYS_DEFAULT = 18

# <topic-id>-<YYYY-MM-DD>.json — topic ids may contain hyphens, so the date is
# anchored to the end rather than split on the first hyphen.
QUEUE_NAME_RE = re.compile(r"^(?P<topic_id>.+)-(?P<date>\d{4}-\d{2}-\d{2})\.json$")


def _queue_files(queue_dir: Path) -> list[tuple[str, date, Path]]:
    """Return ``(topic_id, date, path)`` for every well-formed queue file."""

    if not queue_dir.is_dir():
        return []
    found = []
    for path in sorted(queue_dir.glob("*.json")):
        match = QUEUE_NAME_RE.match(path.name)
        if not match:
            continue
        try:
            covered = datetime.strptime(match.group("date"), "%Y-%m-%d").date()
        except ValueError:
            continue
        found.append((match.group("topic_id"), covered, path))
    return found


def select_topic(
    topics: list[dict],
    queue_dir: Path,
    *,
    today: str,
    cooldown_days: int = COOLDOWN_DAYS_DEFAULT,
) -> dict | None:
    """Return the least-recently-covered topic that is off cooldown, or None."""

    now = datetime.strptime(today, "%Y-%m-%d").date()
    last_covered: dict[str, date] = {}
    for topic_id, covered, _ in _queue_files(queue_dir):
        if topic_id not in last_covered or covered > last_covered[topic_id]:
            last_covered[topic_id] = covered

    domain_of = {t["id"]: t.get("domain", "medicine") for t in topics}
    domain_last_covered: dict[str, date] = {}
    for topic_id, covered in last_covered.items():
        domain = domain_of.get(topic_id)
        if domain is None:
            continue
        if domain not in domain_last_covered or covered > domain_last_covered[domain]:
            domain_last_covered[domain] = covered

    never = 10**6
    eligible = []
    for index, topic in enumerate(topics):
        covered = last_covered.get(topic["id"])
        if covered is not None and (now - covered).days < cooldown_days:
            continue
        age = (now - covered).days if covered is not None else never
        domain_covered = domain_last_covered.get(topic.get("domain", "medicine"))
        domain_age = (now - domain_covered).days if domain_covered is not None else never
        # Staleness first, so a long-neglected topic always wins. Among
        # equally stale topics — which is every never-covered one — prefer the
        # domain heard least recently, so consecutive episodes vary instead of
        # marching through medicine and then through motorcycling. Index last
        # keeps the order stable run to run.
        eligible.append((-age, -domain_age, index, topic))

    if not eligible:
        return None
    eligible.sort(key=lambda item: item[:3])
    return eligible[0][3]


def collect_avoid_urls(topic_id: str, queue_dir: Path) -> list[str]:
    """Every source URL already spent on this topic, across all prior episodes."""

    urls: list[str] = []
    seen: set[str] = set()
    for found_id, _, path in _queue_files(queue_dir):
        if found_id != topic_id:
            continue
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for source in payload.get("sources", []):
            url = source.get("url")
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def build_workflow_args(
    topic: dict,
    queue_dir: Path,
    *,
    today: str,
    max_sources: int,
) -> dict:
    """Shape one topic into the argument object the deep-research workflow takes."""

    args = {
        "topic": {
            "id": topic["id"],
            "title": topic["title"],
            "query": topic.get("query") or topic.get("pubmed_query", ""),
            "debate": topic.get("debate", False),
            "domain": topic.get("domain", "medicine"),
        },
        "date": today,
        "avoidUrls": collect_avoid_urls(topic["id"], queue_dir),
        "maxSources": max_sources,
    }
    # Optional: the specific questions this episode has to answer. Only topics
    # that define one carry it, so the general case stays a broad survey.
    if topic.get("angle"):
        args["angle"] = topic["angle"]
    return args


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--topic-id", default=None, help="Force a specific topic id")
    parser.add_argument("--cooldown-days", type=int, default=COOLDOWN_DAYS_DEFAULT)
    parser.add_argument("--date", default=None, help="Override today's date (YYYY-MM-DD, UTC)")
    parser.add_argument("--max-sources", type=int, default=6)
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    today = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    topics = json.loads(TOPICS_PATH.read_text())["topics"]

    if args.topic_id:
        matches = [t for t in topics if t["id"] == args.topic_id]
        if not matches:
            print(f"error: no topic with id {args.topic_id!r}", file=sys.stderr)
            raise SystemExit(1)
        topic = matches[0]
    else:
        topic = select_topic(topics, QUEUE_DIR, today=today, cooldown_days=args.cooldown_days)
        if topic is None:
            print("nothing due: every topic covered within cooldown", file=sys.stderr)
            raise SystemExit(0)

    print(
        json.dumps(
            build_workflow_args(topic, QUEUE_DIR, today=today, max_sources=args.max_sources),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
