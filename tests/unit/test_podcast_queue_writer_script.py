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

from podcast_queue_writer import build_payload, extract_json, is_public_url  # noqa: E402


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


def test_extract_json_returns_the_outer_object_not_a_nested_one():
    # Regression: a real payload ends with its last source object, so
    # "take the last object that parses" returned {"url": ...} instead of
    # the episode, and every field came back None.
    payload = _payload(3)

    assert extract_json(json.dumps(payload)) == payload


def test_extract_json_finds_outer_object_amid_prose_and_nesting():
    payload = _payload(2)
    raw = f"Here you go:\n{json.dumps(payload)}\nDone."

    assert extract_json(raw) == payload


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


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:2586/agent",
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/admin",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://[::1]/",
        "file:///etc/passwd",
        "gopher://example.com/",
    ],
)
def test_is_public_url_rejects_internal_and_non_http_targets(url):
    assert is_public_url(url) is False


@pytest.mark.parametrize("url", ["https://pubmed.ncbi.nlm.nih.gov/1/", "http://example.com/x"])
def test_is_public_url_accepts_ordinary_public_urls(url, monkeypatch):
    # Stub DNS so the check is exercised without depending on the network.
    monkeypatch.setattr(
        "podcast_queue_writer.socket.getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )

    assert is_public_url(url) is True


def test_is_public_url_rejects_a_public_name_resolving_to_a_private_ip(monkeypatch):
    # DNS rebinding: the hostname looks fine, the address does not.
    monkeypatch.setattr(
        "podcast_queue_writer.socket.getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("127.0.0.1", 0))],
    )

    assert is_public_url("https://totally-legit.example/paper") is False


def test_build_payload_drops_internal_urls_without_fetching_them():
    raw = _payload(5)
    raw["sources"][0]["url"] = "http://169.254.169.254/latest/meta-data/"
    fetched: list[str] = []

    def url_ok(url: str) -> bool:
        fetched.append(url)
        return True

    result, dropped = build_payload(raw, topic_id="t", date="2026-08-13", url_ok=url_ok)

    assert "http://169.254.169.254/latest/meta-data/" in dropped
    assert "http://169.254.169.254/latest/meta-data/" not in fetched
    assert len(result["sources"]) == 4


def test_build_payload_requires_a_sources_list():
    with pytest.raises(ValueError):
        build_payload(
            _payload(sources="not a list"),
            topic_id="t",
            date="2026-08-13",
            url_ok=lambda u: True,
        )
