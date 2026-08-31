"""Tests for the evidence-first personal podcast workflow primitives."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from podcast_workflow import (  # noqa: E402
    PodcastRequest,
    QualityRejected,
    RunStage,
    RunStore,
    canonicalize_url,
    evaluate_quality,
    round_robin_candidates,
)


def test_request_rejects_unknown_fields_and_invalid_source_bounds():
    with pytest.raises(ValueError, match="unknown request field"):
        PodcastRequest.from_dict({"prompt": "x", "surprise": True})
    with pytest.raises(ValueError, match="source bounds"):
        PodcastRequest(prompt="x", min_sources=11, max_sources=10)


def test_request_from_dict_validates_field_types():
    with pytest.raises(ValueError, match="JSON object"):
        PodcastRequest.from_dict("not-a-dict")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="prompt must be a string"):
        PodcastRequest.from_dict({"prompt": 12345})
    with pytest.raises(ValueError, match="min_sources must be an integer"):
        PodcastRequest.from_dict({"prompt": "Valid", "min_sources": True})
    with pytest.raises(ValueError, match="min_sources must be an integer"):
        PodcastRequest.from_dict({"prompt": "Valid", "min_sources": "6"})


def test_request_defaults_are_stable():
    request = PodcastRequest(prompt="Why do agents fail?")

    assert request.audience == "curious, technically literate listener"
    assert request.risk == "auto"
    assert request.audio_format == "deep-dive"
    assert (request.min_sources, request.max_sources) == (6, 10)


def test_canonicalize_url_preserves_path_case_and_functional_query():
    assert (
        canonicalize_url("HTTPS://Example.COM/Paper/ABC?utm_source=x&id=CaseSensitive&fbclid=y")
        == "https://example.com/Paper/ABC?id=CaseSensitive"
    )


def test_canonicalize_url_does_not_force_http_to_https():
    assert canonicalize_url("http://Example.com/A") == "http://example.com/A"


def test_round_robin_candidates_prevents_early_lens_starvation():
    lenses = {
        "authority": [{"url": f"https://a/{i}"} for i in range(6)],
        "context": [{"url": f"https://c/{i}"} for i in range(6)],
        "evidence": [{"url": f"https://e/{i}"} for i in range(6)],
        "controversy": [{"url": f"https://x/{i}"} for i in range(6)],
    }

    selected = round_robin_candidates(lenses, limit=12)

    assert {item["lens"] for item in selected} == set(lenses)
    assert [item["lens"] for item in selected[:4]] == list(lenses)


def assert_private_fs_mode(path: Path, expected_posix_mode: int) -> None:
    """Assert file/directory permissions are private across POSIX and Windows."""
    assert path.exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(path.stat().st_mode) == expected_posix_mode


def test_run_store_is_private_atomic_and_enforces_transitions(tmp_path):
    store = RunStore.create(tmp_path, PodcastRequest(prompt="A useful prompt"))

    assert_private_fs_mode(store.path, 0o700)
    assert_private_fs_mode(store.path / "request.json", 0o600)
    store.transition(RunStage.DISCOVERING, notebook_id="nb-1")
    state = json.loads((store.path / "state.json").read_text())
    assert state["stage"] == "DISCOVERING"
    assert state["notebook_id"] == "nb-1"
    assert not list(store.path.glob("*.tmp"))
    with pytest.raises(ValueError, match="invalid transition"):
        store.transition(RunStage.DELIVERED)


def _ledger(*, sides: bool = True) -> list[dict]:
    rows = []
    for index in range(6):
        rows.append(
            {
                "source_id": f"s{index}",
                "publisher": f"publisher-{index % 4}",
                "channels": ["claude"] if index < 3 else ["notebooklm"],
                "primary": index == 0,
                "side": "for" if index < 3 else ("against" if sides else "for"),
                "ready": True,
                "sane": True,
            }
        )
    return rows


def test_quality_gate_passes_supported_deep_dive():
    result = evaluate_quality(
        _ledger(),
        [{"claim": "major", "major": True, "supporting_source_ids": ["s0", "s1"]}],
        risk="ordinary",
        requested_format="deep-dive",
        critic={
            "passed": True,
            "scores": dict.fromkeys(("novelty", "tension", "audio_fit", "coverage"), 4),
        },
    )

    assert result.audio_format == "deep-dive"


def test_quality_gate_downgrades_false_balance_debate():
    result = evaluate_quality(
        _ledger(sides=False),
        [{"claim": "major", "major": True, "supporting_source_ids": ["s0", "s1"]}],
        risk="ordinary",
        requested_format="debate",
        critic={
            "passed": True,
            "scores": dict.fromkeys(("novelty", "tension", "audio_fit", "coverage"), 5),
        },
    )

    assert result.audio_format == "deep-dive"


def test_quality_gate_rejects_unsupported_major_claim():
    with pytest.raises(QualityRejected, match="two independent"):
        evaluate_quality(
            _ledger(),
            [{"claim": "major", "major": True, "supporting_source_ids": ["s0"]}],
            risk="ordinary",
            requested_format="deep-dive",
            critic={
                "passed": True,
                "scores": dict.fromkeys(("novelty", "tension", "audio_fit", "coverage"), 5),
            },
        )


def test_quality_gate_curates_excess_sources_down_to_max():
    ledger = []
    for i in range(14):
        ledger.append(
            {
                "source_id": f"s{i}",
                "publisher": f"publisher-{i % 5}",
                "channels": ["claude"] if i < 7 else ["notebooklm"],
                "primary": i < 2,
                "side": "for",
                "ready": True,
                "sane": True,
            }
        )
    result = evaluate_quality(
        ledger,
        [{"claim": "major", "major": True, "supporting_source_ids": ["s0", "s1"]}],
        risk="high",
        requested_format="deep-dive",
        critic={
            "passed": True,
            "scores": dict.fromkeys(("novelty", "tension", "audio_fit", "coverage"), 5),
        },
        min_sources=6,
        max_sources=8,
    )
    assert len(result.source_ids) == 8
    assert "s0" in result.source_ids and "s1" in result.source_ids


def test_source_provenance_recognizes_official_bodies():
    from podcast_workflow import classify_source_provenance

    who = classify_source_provenance(
        "https://www.who.int/news/item/123",
        "Clinical management of bacterial meningitis",
        "WHO clinical guideline for health systems",
    )
    assert who["primary"] is True
    assert who["evidence_tier"] == "official"

    nice = classify_source_provenance(
        "https://www.nice.org.uk/guidance/ng240",
        "Meningitis (bacterial) and meningococcal disease",
        "NICE guideline published 2024",
    )
    assert nice["primary"] is True
    assert nice["evidence_tier"] == "official"


def test_source_provenance_distinguishes_empirical_trials_from_opinion():
    from podcast_workflow import classify_source_provenance

    rct = classify_source_provenance(
        "https://pubmed.ncbi.nlm.nih.gov/12345678/",
        "Dexamethasone in Adults with Bacterial Meningitis: A Randomized Double-Blind Trial",
        "Methods: In a double-blind, randomized multicenter trial... Results: 301 patients...",
    )
    assert rct["primary"] is True
    assert rct["evidence_tier"] == "primary_empirical"

    opinion = classify_source_provenance(
        "https://pubmed.ncbi.nlm.nih.gov/87654321/",
        "Editorial: Rethinking Meningitis Protocols",
        "In this commentary, we reflect on recent controversies...",
    )
    assert opinion["primary"] is False
    assert opinion["evidence_tier"] == "secondary_or_commentary"
