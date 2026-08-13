"""Unit tests for the pure curation logic in ``scripts/podcast_researcher.py``.

Covers topic rotation and per-topic cooldown only — the parts that don't touch
the network or NotebookLM. PubMed research, orchestration, and delivery are
exercised manually end-to-end (see the module docstring in
``scripts/podcast_pipeline.py``), not here.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from podcast_researcher import Topic, pick_topic  # noqa: E402


def _topics(n: int) -> list[Topic]:
    return [Topic(id=f"t{i}", title=f"Topic {i}", pubmed_query="q", debate=False) for i in range(n)]


def test_pick_topic_starts_at_rotation_index():
    topics = _topics(3)
    state = {"rotation_index": 1, "topics": {}}

    picked = pick_topic(topics, state, cooldown_days=21)

    assert picked.id == "t1"
    assert state["rotation_index"] == 2


def test_pick_topic_wraps_around():
    topics = _topics(2)
    state = {"rotation_index": 1, "topics": {}}

    picked = pick_topic(topics, state, cooldown_days=21)

    assert picked.id == "t1"
    assert state["rotation_index"] == 0


def test_pick_topic_skips_topics_on_cooldown():
    topics = _topics(2)
    state = {
        "rotation_index": 0,
        "topics": {"t0": {"last_run_ts": time.time()}},
    }

    picked = pick_topic(topics, state, cooldown_days=21)

    assert picked.id == "t1"


def test_pick_topic_returns_none_when_all_on_cooldown():
    topics = _topics(2)
    now = time.time()
    state = {
        "rotation_index": 0,
        "topics": {"t0": {"last_run_ts": now}, "t1": {"last_run_ts": now}},
    }

    assert pick_topic(topics, state, cooldown_days=21) is None


def test_pick_topic_reruns_after_cooldown_expires():
    topics = _topics(1)
    state = {
        "rotation_index": 0,
        "topics": {"t0": {"last_run_ts": time.time() - 22 * 86400}},
    }

    picked = pick_topic(topics, state, cooldown_days=21)

    assert picked.id == "t0"


@pytest.mark.parametrize("cooldown_days", [0, 21, 365])
def test_pick_topic_never_raises_on_empty_topic_history(cooldown_days):
    topics = _topics(4)
    state = {"rotation_index": 0, "topics": {}}

    picked = pick_topic(topics, state, cooldown_days=cooldown_days)

    assert picked is not None
