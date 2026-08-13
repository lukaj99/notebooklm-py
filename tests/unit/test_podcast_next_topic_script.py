"""Unit tests for ``scripts/podcast_next_topic.py``.

The selector is the deterministic half of the scheduled deep-research run: it
picks which topic is due across every domain and gathers the URLs already
spent on it, so the agent session that follows only has to do research.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from podcast_next_topic import collect_avoid_urls, select_topic  # noqa: E402


def _topic(tid: str, domain: str = "medicine", debate: bool = False) -> dict:
    return {
        "id": tid,
        "title": tid.replace("-", " ").title(),
        "domain": domain,
        "query": f"{tid} query",
        "debate": debate,
    }


def _write_queue(queue_dir: Path, topic_id: str, date: str, urls: list[str]) -> None:
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / f"{topic_id}-{date}.json").write_text(
        json.dumps({"topic_id": topic_id, "sources": [{"url": u} for u in urls]})
    )


def test_selects_first_topic_when_queue_is_empty(tmp_path):
    topics = [_topic("a"), _topic("b", domain="motorcycling")]

    picked = select_topic(topics, tmp_path, today="2026-08-13", cooldown_days=18)

    assert picked["id"] == "a"


def test_skips_topic_covered_within_cooldown(tmp_path):
    topics = [_topic("a"), _topic("b", domain="motorcycling")]
    _write_queue(tmp_path, "a", "2026-08-10", ["https://x/1"])

    picked = select_topic(topics, tmp_path, today="2026-08-13", cooldown_days=18)

    assert picked["id"] == "b"


def test_reselects_topic_once_cooldown_expires(tmp_path):
    topics = [_topic("a")]
    _write_queue(tmp_path, "a", "2026-06-01", ["https://x/1"])

    picked = select_topic(topics, tmp_path, today="2026-08-13", cooldown_days=18)

    assert picked["id"] == "a"


def test_returns_none_when_every_topic_is_on_cooldown(tmp_path):
    topics = [_topic("a"), _topic("b")]
    _write_queue(tmp_path, "a", "2026-08-12", [])
    _write_queue(tmp_path, "b", "2026-08-12", [])

    assert select_topic(topics, tmp_path, today="2026-08-13", cooldown_days=18) is None


def test_prefers_the_least_recently_covered_topic(tmp_path):
    topics = [_topic("a"), _topic("b"), _topic("c")]
    _write_queue(tmp_path, "a", "2026-07-01", [])
    _write_queue(tmp_path, "b", "2026-05-01", [])
    _write_queue(tmp_path, "c", "2026-06-01", [])

    picked = select_topic(topics, tmp_path, today="2026-08-13", cooldown_days=18)

    assert picked["id"] == "b"


def test_rotation_spans_all_domains(tmp_path):
    topics = [_topic("med"), _topic("moto", domain="motorcycling")]
    _write_queue(tmp_path, "med", "2026-08-12", [])

    picked = select_topic(topics, tmp_path, today="2026-08-13", cooldown_days=18)

    assert picked["domain"] == "motorcycling"


def test_alternates_domains_when_several_topics_are_equally_stale(tmp_path):
    # All four never covered: without domain interleaving the two medicine
    # topics would run back to back purely because they come first in the
    # file, which makes for a monotonous run of episodes.
    topics = [
        _topic("med1"),
        _topic("med2"),
        _topic("moto1", domain="motorcycling"),
        _topic("photo1", domain="photography"),
    ]
    _write_queue(tmp_path, "med1", "2026-08-13", [])

    picked = select_topic(topics, tmp_path, today="2026-08-14", cooldown_days=0)

    assert picked["domain"] != "medicine"


def test_domain_interleaving_does_not_override_staleness(tmp_path):
    # A long-neglected topic still wins over domain variety.
    topics = [_topic("med1"), _topic("moto1", domain="motorcycling")]
    _write_queue(tmp_path, "moto1", "2026-08-13", [])
    _write_queue(tmp_path, "med1", "2026-01-01", [])

    picked = select_topic(topics, tmp_path, today="2026-08-14", cooldown_days=0)

    assert picked["id"] == "med1"


def test_collect_avoid_urls_gathers_prior_sources_for_that_topic_only(tmp_path):
    _write_queue(tmp_path, "a", "2026-01-01", ["https://x/1", "https://x/2"])
    _write_queue(tmp_path, "a", "2026-02-01", ["https://x/3"])
    _write_queue(tmp_path, "b", "2026-02-01", ["https://y/1"])

    urls = collect_avoid_urls("a", tmp_path)

    assert sorted(urls) == ["https://x/1", "https://x/2", "https://x/3"]


def test_collect_avoid_urls_is_empty_for_unseen_topic(tmp_path):
    assert collect_avoid_urls("never-run", tmp_path) == []


def test_collect_avoid_urls_ignores_unreadable_files(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "a-2026-01-01.json").write_text("{not json")
    _write_queue(tmp_path, "a", "2026-02-01", ["https://x/1"])

    assert collect_avoid_urls("a", tmp_path) == ["https://x/1"]


def test_select_topic_handles_missing_queue_dir(tmp_path):
    topics = [_topic("a")]

    picked = select_topic(topics, tmp_path / "nope", today="2026-08-13", cooldown_days=18)

    assert picked["id"] == "a"


@pytest.mark.parametrize("bad_name", ["notes.md", "a.json", "-2026-08-13.json"])
def test_ignores_files_that_are_not_queue_payloads(tmp_path, bad_name):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / bad_name).write_text("{}")
    topics = [_topic("a")]

    picked = select_topic(topics, tmp_path, today="2026-08-13", cooldown_days=18)

    assert picked["id"] == "a"
