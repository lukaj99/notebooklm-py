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


DEFAULT_AUDIO_FORMAT = "deep-dive"


@dataclass(frozen=True)
class PodcastRequest:
    prompt: str
    audience: str = "curious, technically literate listener"
    angle: str = ""
    risk: str = "auto"
    audio_format: str = DEFAULT_AUDIO_FORMAT
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
        if not isinstance(value, dict):
            raise ValueError("request payload must be a JSON object")
        fields = set(cls.__dataclass_fields__)
        unknown = set(value) - fields
        if unknown:
            raise ValueError(f"unknown request field(s): {', '.join(sorted(unknown))}")
        for str_field in (
            "prompt",
            "audience",
            "angle",
            "risk",
            "audio_format",
            "language",
            "length",
        ):
            if str_field in value and not isinstance(value[str_field], str):
                raise ValueError(f"{str_field} must be a string")
        for int_field in ("min_sources", "max_sources", "schema_version"):
            if int_field in value and (
                not isinstance(value[int_field], int) or isinstance(value[int_field], bool)
            ):
                raise ValueError(f"{int_field} must be an integer")
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


OFFICIAL_DOMAINS = frozenset(
    {
        "who.int",
        "nice.org.uk",
        "nih.gov",
        "cdc.gov",
        "fda.gov",
        "ema.europa.eu",
        "clinicaltrials.gov",
        "cochranelibrary.com",
    }
)
OFFICIAL_SUFFIXES = (".gov", ".gov.uk", ".mil")

PRIMARY_STUDY_PATTERNS = (
    r"\brandomized\s+(?:controlled\s+)?trial\b",
    r"\bclinical\s+trial\b",
    r"\bdouble-blind\b",
    r"\bmulticenter\b",
    r"\bmeta-analysis\b",
    r"\bsystematic\s+review\b",
    r"\bprospective\s+cohort\b",
    r"\bcohort\s+study\b",
    r"\btrial\s+registration\b",
    r"\bnct\d{8}\b",
    r"\bisrctn\d{8}\b",
)

COMMENTARY_PATTERNS = (
    r"\beditorial\b",
    r"\bletter\s+to\s+the\s+editor\b",
    r"\bcommentary\b",
    r"\bopinion\b",
    r"\bperspective\b",
    r"\bviewpoint\b",
    r"\bnews\s+and\s+views\b",
)


INDEXING_ARCHIVES = frozenset({"pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov"})


def classify_source_provenance(url: str, title: str, content: str = "") -> dict[str, Any]:
    """Classify provenance and primary/official evidence status from identity and document type."""
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    combined = f"{title} {content[:4000]}".lower()

    # 1. Official international and government regulatory bodies (excluding literature archives like PubMed)
    is_archive = any(host == a or host.endswith(f".{a}") for a in INDEXING_ARCHIVES)
    is_official = (not is_archive) and (
        any(host == d or host.endswith(f".{d}") for d in OFFICIAL_DOMAINS)
        or any(host.endswith(s) for s in OFFICIAL_SUFFIXES)
    )
    if is_official:
        return {
            "primary": True,
            "evidence_tier": "official",
            "publisher": host,
        }

    # 2. Check for explicit commentary/opinion indicators in title
    title_lower = title.lower()
    is_opinion = any(re.search(pat, title_lower) for pat in COMMENTARY_PATTERNS)

    # 3. Check for primary empirical study markers
    has_primary_markers = any(re.search(pat, combined) for pat in PRIMARY_STUDY_PATTERNS)

    if has_primary_markers and not (is_opinion and not re.search(r"\bmethods\b", combined)):
        return {
            "primary": True,
            "evidence_tier": "primary_empirical",
            "publisher": host or "academic",
        }

    return {
        "primary": False,
        "evidence_tier": "secondary_or_commentary",
        "publisher": host,
    }


def curate_usable_sources(
    usable: list[dict],
    *,
    max_sources: int = 10,
    risk: str = "ordinary",
    requested_format: str = "auto",
) -> list[dict]:
    """Curate excess usable sources down to max_sources while preserving balance and evidence gates."""
    if len(usable) <= max_sources:
        return list(usable)

    selected: list[dict] = []
    selected_ids: set[str] = set()

    def _add(row: dict) -> bool:
        if row["source_id"] in selected_ids:
            return False
        selected.append(row)
        selected_ids.add(row["source_id"])
        return True

    # 1. First preserve required primary sources (up to 2 for high risk, 1 for ordinary)
    needed_primary = 2 if risk == "high" else 1
    for row in usable:
        if row.get("primary") and len([r for r in selected if r.get("primary")]) < needed_primary:
            _add(row)

    # 2. Ensure both discovery channels remain represented
    for channel in ("claude", "notebooklm"):
        if not any(channel in r.get("channels", []) for r in selected):
            for row in usable:
                if channel in row.get("channels", []):
                    if _add(row):
                        break

    # 3. Ensure side balance if debate requested
    if requested_format == "debate":
        for side in ("for", "against"):
            for row in usable:
                if (
                    row.get("side") == side
                    and len([r for r in selected if r.get("side") == side]) < 2
                ):
                    _add(row)

    # 4. Maximize independent publishers
    for row in usable:
        if len(selected) >= max_sources:
            break
        pub = row.get("publisher")
        if pub and pub not in {r.get("publisher") for r in selected}:
            _add(row)

    # 5. Fill remaining slots with remaining usable sources
    for row in usable:
        if len(selected) >= max_sources:
            break
        _add(row)

    return selected


def evaluate_quality(
    ledger: list[dict],
    claims: list[dict],
    *,
    risk: str,
    requested_format: str,
    critic: dict,
    min_sources: int = 6,
    max_sources: int = 10,
) -> QualityResult:
    usable = [row for row in ledger if row.get("ready") and row.get("sane")]
    if len(usable) < min_sources:
        raise QualityRejected(
            f"final corpus must contain at least {min_sources} sane READY sources (found {len(usable)})"
        )
    if len(usable) > max_sources:
        usable = curate_usable_sources(
            usable, max_sources=max_sources, risk=risk, requested_format=requested_format
        )
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

    def _write_json_unlocked(self, target: Path, value: Any) -> None:
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=self.path)
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

    def write_json(self, name: str, value: Any) -> None:
        target = self.path / name
        with self.lock:
            self._write_json_unlocked(target, value)

    def transition(self, stage: RunStage, **updates: Any) -> None:
        target = self.path / "state.json"
        with self.lock:
            state = json.loads(target.read_text())
            current = RunStage(state["stage"])
            if stage not in TRANSITIONS.get(current, set()):
                raise ValueError(f"invalid transition: {current.value} -> {stage.value}")
            state.update(updates)
            state["stage"] = stage.value
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._write_json_unlocked(target, state)
