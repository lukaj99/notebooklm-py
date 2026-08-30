#!/usr/bin/env python3
"""Trusted primitives for the private evidence-first podcast workflow."""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.parse
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from filelock import FileLock

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


class RunStage(str, Enum):
    REQUESTED = "REQUESTED"
    DISCOVERING = "DISCOVERING"
    INGESTING = "INGESTING"
    GROUNDING = "GROUNDING"
    ADJUDICATING = "ADJUDICATING"
    READY_TO_GENERATE = "READY_TO_GENERATE"
    GENERATING = "GENERATING"
    DOWNLOADING = "DOWNLOADING"
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    REJECTED_QUALITY = "REJECTED_QUALITY"
    CANCELLED = "CANCELLED"


TRANSITIONS = {
    RunStage.REQUESTED: {RunStage.DISCOVERING, RunStage.CANCELLED},
    RunStage.DISCOVERING: {
        RunStage.INGESTING,
        RunStage.REJECTED_QUALITY,
        RunStage.RETRYABLE_FAILURE,
    },
    RunStage.INGESTING: {
        RunStage.GROUNDING,
        RunStage.REJECTED_QUALITY,
        RunStage.RETRYABLE_FAILURE,
    },
    RunStage.GROUNDING: {
        RunStage.ADJUDICATING,
        RunStage.REJECTED_QUALITY,
        RunStage.RETRYABLE_FAILURE,
    },
    RunStage.ADJUDICATING: {
        RunStage.READY_TO_GENERATE,
        RunStage.REJECTED_QUALITY,
        RunStage.RETRYABLE_FAILURE,
    },
    RunStage.READY_TO_GENERATE: {RunStage.GENERATING, RunStage.CANCELLED},
    RunStage.GENERATING: {RunStage.DOWNLOADING, RunStage.RETRYABLE_FAILURE},
    RunStage.DOWNLOADING: {RunStage.DELIVERING, RunStage.RETRYABLE_FAILURE},
    RunStage.DELIVERING: {RunStage.DELIVERED, RunStage.RETRYABLE_FAILURE},
    RunStage.RETRYABLE_FAILURE: {
        RunStage.DISCOVERING,
        RunStage.INGESTING,
        RunStage.GROUNDING,
        RunStage.ADJUDICATING,
        RunStage.GENERATING,
        RunStage.DOWNLOADING,
        RunStage.DELIVERING,
        RunStage.CANCELLED,
    },
}


@dataclass(frozen=True)
class PodcastRequest:
    prompt: str
    audience: str = "curious, technically literate listener"
    angle: str = ""
    risk: str = "auto"
    audio_format: str = "auto"
    language: str = "en"
    length: str = "long"
    min_sources: int = 6
    max_sources: int = 10
    schema_version: int = 2

    def __post_init__(self) -> None:
        if not self.prompt.strip() or len(self.prompt) > 2_000:
            raise ValueError("prompt must contain 1-2000 characters")
        if len(self.audience) > 500 or len(self.angle) > 2_000:
            raise ValueError("audience or angle is too long")
        if self.risk not in {"auto", "ordinary", "high"}:
            raise ValueError("risk must be auto, ordinary, or high")
        if self.audio_format not in {"auto", "deep-dive", "debate"}:
            raise ValueError("format must be auto, deep-dive, or debate")
        if self.language != "en" or self.length != "long":
            raise ValueError("the MVP supports English long-form audio only")
        if not (6 <= self.min_sources <= self.max_sources <= 10):
            raise ValueError("source bounds must satisfy 6 <= min <= max <= 10")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PodcastRequest:
        fields = set(cls.__dataclass_fields__)
        unknown = set(value) - fields
        if unknown:
            raise ValueError(f"unknown request field(s): {', '.join(sorted(unknown))}")
        return cls(**value)


def canonicalize_url(url: str) -> str:
    """Canonicalize identity without changing resource semantics."""

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("source URL must use HTTP or HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("source URL must not contain credentials")
    hostname = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"[{hostname}]{port}" if ":" in hostname else f"{hostname}{port}"
    query = urllib.parse.urlencode(
        [
            (key, value)
            for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in TRACKING_QUERY_KEYS
        ],
        doseq=True,
    )
    return urllib.parse.urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", query, ""))


def round_robin_candidates(lenses: dict[str, list[dict]], *, limit: int) -> list[dict]:
    """Allocate verification slots fairly across research lenses."""

    selected: list[dict] = []
    seen: set[str] = set()
    depth = 0
    while len(selected) < limit:
        added = False
        for lens, candidates in lenses.items():
            if depth >= len(candidates):
                continue
            candidate = candidates[depth]
            try:
                key = canonicalize_url(str(candidate.get("url", "")))
            except ValueError:
                continue
            if key in seen:
                continue
            seen.add(key)
            selected.append({**candidate, "lens": lens})
            added = True
            if len(selected) == limit:
                break
        if not added and all(depth >= len(items) - 1 for items in lenses.values()):
            break
        depth += 1
    return selected


@dataclass(frozen=True)
class QualityResult:
    audio_format: str
    source_ids: tuple[str, ...]


class QualityRejected(ValueError):
    """The evidence or editorial gates rejected generation."""


def evaluate_quality(
    ledger: list[dict],
    claims: list[dict],
    *,
    risk: str,
    requested_format: str,
    critic: dict,
) -> QualityResult:
    usable = [row for row in ledger if row.get("ready") and row.get("sane")]
    if not 6 <= len(usable) <= 10:
        raise QualityRejected("final corpus must contain 6-10 sane READY sources")
    if len({row.get("publisher") for row in usable if row.get("publisher")}) < 4:
        raise QualityRejected("final corpus needs four independent publishers")
    channels = {channel for row in usable for channel in row.get("channels", [])}
    if not {"claude", "notebooklm"} <= channels:
        raise QualityRejected("both discovery channels must be represented")
    primary_count = sum(bool(row.get("primary")) for row in usable)
    if primary_count < (2 if risk == "high" else 1):
        raise QualityRejected("insufficient primary or official sources")
    publishers_by_id = {row["source_id"]: row.get("publisher") for row in usable}
    for claim in claims:
        if not claim.get("major"):
            continue
        support = claim.get("supporting_source_ids", [])
        independent = {publishers_by_id.get(source_id) for source_id in support}
        independent.discard(None)
        if len(independent) < 2:
            raise QualityRejected("every major claim needs two independent supporting sources")
        if risk == "high" and not any(
            row.get("primary") and row["source_id"] in support for row in usable
        ):
            raise QualityRejected("high-risk claims require primary or official support")
    scores = critic.get("scores", {})
    if not critic.get("passed") or any(
        scores.get(key, 0) < 4 for key in ("novelty", "tension", "audio_fit", "coverage")
    ):
        raise QualityRejected("independent critic rejected the editorial brief")
    audio_format = "deep-dive" if requested_format == "auto" else requested_format
    if audio_format == "debate":
        sides = {
            side: {row.get("publisher") for row in usable if row.get("side") == side}
            for side in ("for", "against")
        }
        if any(len(publishers) < 2 for publishers in sides.values()):
            audio_format = "deep-dive"
    return QualityResult(audio_format, tuple(row["source_id"] for row in usable))


class RunStore:
    """Atomic, private, lock-protected audit bundle storage."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = FileLock(str(path / ".lock"))

    @classmethod
    def create(cls, root: Path, request: PodcastRequest) -> RunStore:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug = re.sub(r"[^a-z0-9]+", "-", request.prompt.lower()).strip("-")[:40] or "podcast"
        path = root / f"{timestamp}-{slug}-{uuid.uuid4().hex[:8]}"
        path.mkdir(mode=0o700)
        store = cls(path)
        store.write_json("request.json", asdict(request))
        store.write_json(
            "state.json",
            {
                "stage": RunStage.REQUESTED.value,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return store

    def read_json(self, name: str) -> dict:
        return json.loads((self.path / name).read_text())

    def write_json(self, name: str, value: Any) -> None:
        target = self.path / name
        with self.lock:
            fd, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=self.path)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(value, stream, indent=2, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def transition(self, stage: RunStage, **updates: Any) -> None:
        state = self.read_json("state.json")
        current = RunStage(state["stage"])
        if stage not in TRANSITIONS.get(current, set()):
            raise ValueError(f"invalid transition: {current.value} -> {stage.value}")
        state.update(updates)
        state["stage"] = stage.value
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.write_json("state.json", state)
