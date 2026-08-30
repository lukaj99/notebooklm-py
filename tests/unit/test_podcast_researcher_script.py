"""Unit tests for the pure curation logic in ``scripts/podcast_researcher.py``.

Covers topic rotation and per-topic cooldown only — the parts that don't touch
the network or NotebookLM. PubMed research, orchestration, and delivery are
exercised manually end-to-end (see the module docstring in
``scripts/podcast_pipeline.py``), not here.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from podcast_pipeline import EM_CASES_STYLE  # noqa: E402
from podcast_researcher import (  # noqa: E402
    Topic,
    build_queue_instructions,
    load_topics,
    pick_topic,
)


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


def test_build_queue_instructions_bare_payload_is_base_style():
    assert build_queue_instructions({}) == EM_CASES_STYLE


def test_build_queue_instructions_appends_rationale():
    result = build_queue_instructions({"rationale": "big new trial dropped"})

    assert result.startswith(EM_CASES_STYLE)
    assert "Editorial context for this episode: big new trial dropped" in result


def test_build_queue_instructions_appends_case_vignette():
    result = build_queue_instructions(
        {"case_vignette": "54M, bradycardic, amlodipine bottle empty"}
    )

    assert result.startswith(EM_CASES_STYLE)
    assert "54M, bradycardic, amlodipine bottle empty" in result
    assert "case vignette" in result


def test_build_queue_instructions_rationale_precedes_vignette():
    result = build_queue_instructions({"rationale": "why now", "case_vignette": "the case"})

    assert result.index("why now") < result.index("the case")


def test_build_queue_instructions_style_overrides_base():
    result = build_queue_instructions(
        {"style": "Two riders talking wrenching.", "rationale": "new tire data"}
    )

    assert result.startswith("Two riders talking wrenching.")
    assert EM_CASES_STYLE not in result
    assert "new tire data" in result


def test_load_topics_skips_non_medicine_domains(tmp_path, monkeypatch):
    import podcast_researcher

    topics_file = tmp_path / "topics.json"
    topics_file.write_text(
        json.dumps(
            {
                "topics": [
                    {"id": "med", "title": "Med", "pubmed_query": "q", "debate": False},
                    {
                        "id": "moto",
                        "title": "Moto",
                        "domain": "motorcycling",
                        "query": "tires",
                        "debate": True,
                    },
                    {
                        "id": "med2",
                        "title": "Med 2",
                        "domain": "medicine",
                        "pubmed_query": "q2",
                        "debate": True,
                    },
                ]
            }
        )
    )
    monkeypatch.setattr(podcast_researcher, "TOPICS_PATH", topics_file)

    topics = load_topics()

    assert [t.id for t in topics] == ["med", "med2"]


def test_next_queue_item_uses_embedded_date_not_filename_order(tmp_path, monkeypatch):
    import podcast_researcher

    (tmp_path / "aaa-2026-08-25.json").write_text(
        json.dumps({"topic_id": "new", "created_at": "2026-08-25T01:00:00Z"})
    )
    (tmp_path / "zzz-2026-08-13.json").write_text(json.dumps({"topic_id": "old"}))
    monkeypatch.setattr(podcast_researcher, "sync_queue_repo", lambda: tmp_path)

    payload, filename = podcast_researcher.next_queue_item({})

    assert payload["topic_id"] == "old"
    assert filename == "zzz-2026-08-13.json"


def test_next_queue_item_prefers_created_at_and_breaks_ties_by_name(tmp_path, monkeypatch):
    import podcast_researcher

    (tmp_path / "b.json").write_text(
        json.dumps({"topic_id": "b", "created_at": "2026-08-13T09:00:00Z"})
    )
    (tmp_path / "a.json").write_text(
        json.dumps({"topic_id": "a", "created_at": "2026-08-13T09:00:00Z"})
    )
    monkeypatch.setattr(podcast_researcher, "sync_queue_repo", lambda: tmp_path)

    payload, filename = podcast_researcher.next_queue_item({})

    assert payload["topic_id"] == "a"
    assert filename == "a.json"
