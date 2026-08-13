#!/usr/bin/env python3
"""Turn a research session's stdout into a validated podcast queue file.

The trusted half of the research trust boundary (see
``scripts/podcast_research_run.sh``). Everything on stdin came from a Claude
session that ingested arbitrary web pages, so nothing here trusts its shape,
its field names, or its own claim that a URL works:

* the JSON is extracted from whatever prose or fences surround it,
* only the fields the queue format defines survive — anything else the model
  decided to add is dropped rather than written to disk,
* every source URL is re-checked here with a real HTTP request, regardless of
  what the session's verification agents concluded,
* and an episode that ends up too thin is refused outright rather than
  queued in a degraded state.

Reads the payload on stdin; writes the queue file named by ``$QUEUE_FILE``.
Prints a one-line summary on success, exits non-zero with a reason otherwise.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

PAYLOAD_FIELDS = (
    "topic_id",
    "title",
    "audio_format",
    "sources",
    "rationale",
    "case_vignette",
    "style",
)
SOURCE_FIELDS = ("url", "title", "why")
AUDIO_FORMATS = {"debate", "deep-dive"}
MIN_SOURCES_DEFAULT = 4
URL_TIMEOUT = 25.0


def extract_json(raw: str) -> dict | None:
    """Pull the last well-formed JSON object out of noisy model output."""

    text = re.sub(r"```(?:json)?|```", "", raw)
    decoder = json.JSONDecoder()
    found: dict | None = None
    for match in re.finditer(r"\{", text):
        try:
            candidate, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            found = candidate
    return found


def url_is_reachable(url: str) -> bool:
    """True when the URL answers 200 to a plain automated fetch.

    Deliberately the same shape of request NotebookLM's own ingester makes —
    a page that needs a real browser to render is a page the pipeline cannot
    use, however genuinely open-access it may be.
    """

    request = urllib.request.Request(  # noqa: S310 - scheme is validated by the caller
        url,
        headers={"User-Agent": "notebooklm-py-podcast-pipeline/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=URL_TIMEOUT) as response:  # noqa: S310
            return response.status == 200
    except Exception:
        return False


def build_payload(
    raw: dict,
    *,
    topic_id: str,
    date: str,
    url_ok: Callable[[str], bool],
    min_sources: int = MIN_SOURCES_DEFAULT,
) -> tuple[dict, list[str]]:
    """Validate and narrow a raw payload. Returns ``(payload, dropped_urls)``."""

    if raw.get("topic_id") != topic_id:
        raise ValueError(f"payload topic_id {raw.get('topic_id')!r} != expected {topic_id!r}")
    if raw.get("audio_format") not in AUDIO_FORMATS:
        raise ValueError(f"unknown audio_format {raw.get('audio_format')!r}")
    sources = raw.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("payload has no sources list")

    kept: list[dict] = []
    dropped: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        url = source.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            if isinstance(url, str):
                dropped.append(url)
            continue
        if not url_ok(url):
            dropped.append(url)
            continue
        kept.append({field: str(source.get(field, "")) for field in SOURCE_FIELDS})

    if len(kept) < min_sources:
        raise ValueError(
            f"only {len(kept)} of {len(sources)} source(s) reachable, need {min_sources}"
        )

    payload = {
        field: raw[field]
        for field in PAYLOAD_FIELDS
        if field in raw and field != "sources" and isinstance(raw[field], str)
    }
    payload["topic_id"] = topic_id
    payload["sources"] = kept
    if not payload.get("title"):
        payload["title"] = f"{topic_id} — {date}"
    return payload, dropped


def main() -> None:
    queue_file = os.environ.get("QUEUE_FILE")
    topic_id = os.environ.get("TOPIC_ID")
    date = os.environ.get("DATE")
    if not queue_file or not topic_id or not date:
        print("error: QUEUE_FILE, TOPIC_ID and DATE must be set", file=sys.stderr)
        raise SystemExit(2)
    min_sources = int(os.environ.get("MIN_SOURCES", MIN_SOURCES_DEFAULT))

    raw = extract_json(sys.stdin.read())
    if raw is None:
        print("error: no JSON object found in research session output", file=sys.stderr)
        raise SystemExit(1)

    try:
        payload, dropped = build_payload(
            raw,
            topic_id=topic_id,
            date=date,
            url_ok=url_is_reachable,
            min_sources=min_sources,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    Path(queue_file).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    summary = f"{len(payload['sources'])} sources, format {payload['audio_format']}"
    if dropped:
        summary += f" ({len(dropped)} dropped as unreachable)"
    print(summary)


if __name__ == "__main__":
    main()
