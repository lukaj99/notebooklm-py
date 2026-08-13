"""Unit tests for ``scripts/podcast_queue_writer.py``.

This is the trusted side of the research trust boundary: everything it reads
came from a session that ingested arbitrary web pages, so the tests pin the
defensive behavior — extract JSON from noisy output, keep only known fields,
drop sources that fail their own URL check, and refuse to write a thin or
malformed episode.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from podcast_queue_writer import build_payload, extract_json  # noqa: E402


def _payload(n_sources: int = 5, **overrides) -> dict:
    payload = {
        "topic_id": "t",
        "title": "T — 2026-08-13",
        "audio_format": "debate",
        "sources": [
            {"url": f"https://example.com/{i}", "title": f"S{i}", "why": "because"}
            for i in range(n_sources)
        ],
        "rationale": "why now",
        "case_vignette": "a case",
    }
    payload.update(overrides)
    return payload


def test_extract_json_from_bare_object():
    assert extract_json(json.dumps({"a": 1})) == {"a": 1}


def test_extract_json_ignores_surrounding_prose():
    raw = 'Here is the payload:\n{"a": 1}\nHope that helps!'

    assert extract_json(raw) == {"a": 1}


def test_extract_json_handles_markdown_fences():
    raw = '```json\n{"a": 1}\n```'

    assert extract_json(raw) == {"a": 1}


def test_extract_json_picks_the_last_object_when_several_appear():
    raw = '{"a": 1}\nactually, corrected:\n{"a": 2}'

    assert extract_json(raw) == {"a": 2}


def test_extract_json_returns_none_on_garbage():
    assert extract_json("no json at all here") is None


def test_build_payload_keeps_only_known_fields():
    raw = _payload()
    raw["evil"] = "rm -rf /"
    raw["sources"][0]["extra"] = "junk"

    result, _ = build_payload(raw, topic_id="t", date="2026-08-13", url_ok=lambda u: True)

    assert "evil" not in result
    assert set(result) <= {
        "topic_id",
        "title",
        "audio_format",
        "sources",
        "rationale",
        "case_vignette",
        "style",
    }
    assert set(result["sources"][0]) == {"url", "title", "why"}


def test_build_payload_drops_sources_that_fail_their_url_check():
    raw = _payload(6)
    bad = raw["sources"][1]["url"]

    result, dropped = build_payload(raw, topic_id="t", date="2026-08-13", url_ok=lambda u: u != bad)

    assert len(result["sources"]) == 5
    assert bad not in [s["url"] for s in result["sources"]]
    assert dropped == [bad]


def test_build_payload_rejects_episode_with_too_few_surviving_sources():
    raw = _payload(5)
    good = {raw["sources"][0]["url"]}

    with pytest.raises(ValueError, match="only 1"):
        build_payload(
            raw, topic_id="t", date="2026-08-13", url_ok=lambda u: u in good, min_sources=4
        )


def test_build_payload_rejects_mismatched_topic_id():
    with pytest.raises(ValueError, match="topic_id"):
        build_payload(_payload(), topic_id="other", date="2026-08-13", url_ok=lambda u: True)


def test_build_payload_rejects_unknown_audio_format():
    with pytest.raises(ValueError, match="audio_format"):
        build_payload(
            _payload(audio_format="singalong"),
            topic_id="t",
            date="2026-08-13",
            url_ok=lambda u: True,
        )


def test_build_payload_rejects_non_http_urls():
    raw = _payload(5)
    raw["sources"][0]["url"] = "file:///etc/passwd"

    result, dropped = build_payload(raw, topic_id="t", date="2026-08-13", url_ok=lambda u: True)

    assert "file:///etc/passwd" not in [s["url"] for s in result["sources"]]
    assert "file:///etc/passwd" in dropped


def test_build_payload_preserves_optional_style_and_vignette():
    raw = _payload(style="two riders talking")

    result, _ = build_payload(raw, topic_id="t", date="2026-08-13", url_ok=lambda u: True)

    assert result["style"] == "two riders talking"
    assert result["case_vignette"] == "a case"


def test_build_payload_requires_a_sources_list():
    with pytest.raises(ValueError):
        build_payload(
            _payload(sources="not a list"),
            topic_id="t",
            date="2026-08-13",
            url_ok=lambda u: True,
        )
