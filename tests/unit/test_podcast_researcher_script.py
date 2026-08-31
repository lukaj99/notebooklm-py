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


def test_queue_created_at_defaults_naive_timestamp_to_utc(tmp_path):
    from datetime import datetime, timezone

    import podcast_researcher

    naive_payload = {"created_at": "2026-08-30T17:00:00"}
    dt = podcast_researcher._queue_created_at(naive_payload, tmp_path / "test.json")
    assert dt.tzinfo == timezone.utc
    assert dt == datetime(2026, 8, 30, 17, 0, 0, tzinfo=timezone.utc)


def test_compute_payload_hash_is_deterministic():
    import podcast_researcher

    payload1 = {
        "topic_id": "crp",
        "title": "CRP Norm",
        "audio_format": "deep-dive",
        "sources": [{"url": "https://b.com"}, {"url": "https://a.com"}],
    }
    payload2 = {
        "title": "CRP Norm",
        "audio_format": "deep-dive",
        "topic_id": "crp",
        "sources": [{"url": "https://a.com"}, {"url": "https://b.com"}],
    }

    assert podcast_researcher.compute_payload_hash(
        payload1
    ) == podcast_researcher.compute_payload_hash(payload2)


def test_next_queue_item_skips_matching_content_hash(tmp_path, monkeypatch):
    import podcast_researcher

    payload = {"topic_id": "crp", "title": "CRP", "audio_format": "deep-dive"}
    (tmp_path / "crp-2026-08-29.json").write_text(json.dumps(payload))
    monkeypatch.setattr(podcast_researcher, "sync_queue_repo", lambda: tmp_path)

    chash = podcast_researcher.compute_payload_hash(payload)
    state = {
        "processed_queue": {
            "crp-2026-08-29.json": {"content_hash": chash, "audio_format": "deep-dive"}
        }
    }

    assert podcast_researcher.next_queue_item(state) is None


def test_next_queue_item_reprocesses_when_content_hash_changes(tmp_path, monkeypatch):
    import podcast_researcher

    modified_payload = {"topic_id": "crp", "title": "CRP", "audio_format": "deep-dive"}
    (tmp_path / "crp-2026-08-29.json").write_text(json.dumps(modified_payload))
    monkeypatch.setattr(podcast_researcher, "sync_queue_repo", lambda: tmp_path)

    old_hash = "old_stale_hash_from_debate_run"
    state = {
        "processed_queue": {
            "crp-2026-08-29.json": {"content_hash": old_hash, "audio_format": "debate"}
        }
    }

    candidate = podcast_researcher.next_queue_item(state)
    assert candidate is not None
    res_payload, res_filename = candidate
    assert res_filename == "crp-2026-08-29.json"
    assert res_payload["audio_format"] == "deep-dive"


def test_next_queue_item_migrates_legacy_processed_queue_files(tmp_path, monkeypatch):
    import podcast_researcher

    payload = {"topic_id": "legacy", "title": "Legacy"}
    (tmp_path / "legacy-2026-08-13.json").write_text(json.dumps(payload))
    monkeypatch.setattr(podcast_researcher, "sync_queue_repo", lambda: tmp_path)
    monkeypatch.setattr(podcast_researcher, "STATE_PATH", tmp_path / "state" / "state.json")

    state = {"processed_queue_files": ["legacy-2026-08-13.json"]}
    # First check: recognizes legacy entry, records hash in processed_queue, skips execution
    assert podcast_researcher.next_queue_item(state) is None
    assert "legacy-2026-08-13.json" in state["processed_queue"]
    assert state["processed_queue"]["legacy-2026-08-13.json"]["migrated_from_legacy"] is True


def test_next_queue_item_persists_legacy_migration_to_disk(tmp_path, monkeypatch):
    """Migration must survive a run that returns before any other save_state()."""

    import podcast_researcher

    payload = {"topic_id": "legacy", "title": "Legacy"}
    (tmp_path / "legacy-2026-08-13.json").write_text(json.dumps(payload))
    state_path = tmp_path / "state" / "state.json"
    monkeypatch.setattr(podcast_researcher, "sync_queue_repo", lambda: tmp_path)
    monkeypatch.setattr(podcast_researcher, "STATE_PATH", state_path)

    state = {"processed_queue_files": ["legacy-2026-08-13.json"]}
    assert podcast_researcher.next_queue_item(state) is None

    persisted = json.loads(state_path.read_text())
    entry = persisted["processed_queue"]["legacy-2026-08-13.json"]
    assert entry["content_hash"] == podcast_researcher.compute_payload_hash(payload)
    assert entry["migrated_from_legacy"] is True


def test_next_queue_item_does_not_write_state_when_nothing_migrates(tmp_path, monkeypatch):
    """A queue with no legacy entries must not touch the state file."""

    import podcast_researcher

    payload = {"topic_id": "fresh", "title": "Fresh"}
    (tmp_path / "fresh-2026-08-29.json").write_text(json.dumps(payload))
    state_path = tmp_path / "state" / "state.json"
    monkeypatch.setattr(podcast_researcher, "sync_queue_repo", lambda: tmp_path)
    monkeypatch.setattr(podcast_researcher, "STATE_PATH", state_path)

    candidate = podcast_researcher.next_queue_item({})

    assert candidate is not None
    assert not state_path.exists()


def test_next_queue_item_records_unreadable_file_in_durable_ledger(tmp_path, monkeypatch):
    """A corrupt queue file must land in state, not a throwaway set copy."""

    import podcast_researcher

    (tmp_path / "broken-2026-08-13.json").write_text("{not json")
    state_path = tmp_path / "state" / "state.json"
    monkeypatch.setattr(podcast_researcher, "sync_queue_repo", lambda: tmp_path)
    monkeypatch.setattr(podcast_researcher, "STATE_PATH", state_path)

    state: dict = {}
    assert podcast_researcher.next_queue_item(state) is None

    entry = json.loads(state_path.read_text())["processed_queue"]["broken-2026-08-13.json"]
    assert entry["status"] == "UNREADABLE"
    assert "content_hash" not in entry


def test_next_queue_item_does_not_rewrite_known_unreadable_file(tmp_path, monkeypatch):
    """A file already recorded as broken must not dirty the state on every run."""

    import podcast_researcher

    (tmp_path / "broken-2026-08-13.json").write_text("{not json")
    state_path = tmp_path / "state" / "state.json"
    monkeypatch.setattr(podcast_researcher, "sync_queue_repo", lambda: tmp_path)
    monkeypatch.setattr(podcast_researcher, "STATE_PATH", state_path)

    state = {
        "processed_queue": {
            "broken-2026-08-13.json": {"status": "UNREADABLE", "recorded_at": "2026-08-13T00:00:00"}
        }
    }

    assert podcast_researcher.next_queue_item(state) is None
    assert not state_path.exists()


def test_next_queue_item_reconsiders_repaired_file(tmp_path, monkeypatch):
    """Fixing a corrupt queue file makes it eligible again."""

    import podcast_researcher

    payload = {"topic_id": "repaired", "title": "Repaired"}
    (tmp_path / "broken-2026-08-13.json").write_text(json.dumps(payload))
    monkeypatch.setattr(podcast_researcher, "sync_queue_repo", lambda: tmp_path)
    monkeypatch.setattr(podcast_researcher, "STATE_PATH", tmp_path / "state" / "state.json")

    state = {
        "processed_queue": {
            "broken-2026-08-13.json": {"status": "UNREADABLE", "recorded_at": "2026-08-13T00:00:00"}
        }
    }

    candidate = podcast_researcher.next_queue_item(state)

    assert candidate is not None
    assert candidate[1] == "broken-2026-08-13.json"


class _FakeClientSession:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc_info):
        return False


class _FakeClient:
    @staticmethod
    def from_storage():
        return _FakeClientSession()


def _stub_queue_run(monkeypatch, tmp_path, *, build_podcast):
    """Wire _run_from_queue's I/O to fakes. Returns the captured-calls dict."""

    import podcast_researcher

    calls: dict = {"delivered": [], "failed": []}
    monkeypatch.setattr(podcast_researcher, "NotebookLMClient", _FakeClient)
    monkeypatch.setattr(podcast_researcher, "build_podcast", build_podcast)
    monkeypatch.setattr(
        podcast_researcher,
        "deliver",
        lambda mp3, topic, articles: calls["delivered"].append((mp3, topic.id)),
    )
    monkeypatch.setattr(
        podcast_researcher,
        "deliver_failure",
        lambda topic, error: calls["failed"].append((topic.id, error)),
    )
    monkeypatch.setattr(podcast_researcher, "STATE_PATH", tmp_path / "state" / "state.json")
    return calls


async def test_run_from_queue_writes_full_ledger_entry(tmp_path, monkeypatch):
    """A completed run records hash, format, title and output path in the ledger."""

    import podcast_researcher

    mp3 = tmp_path / "episode.mp3"
    captured: dict = {}

    async def fake_build_podcast(client, **kwargs):
        captured.update(kwargs)
        return mp3

    calls = _stub_queue_run(monkeypatch, tmp_path, build_podcast=fake_build_podcast)
    payload = {
        "topic_id": "crp",
        "title": "CRP Norm",
        "audio_format": "deep-dive",
        "sources": [{"url": "https://a.example", "title": "A"}],
    }
    state: dict = {}

    rc = await podcast_researcher._run_from_queue(payload, "crp-2026-08-29.json", state)

    assert rc == 0
    assert captured["audio_format"] is podcast_researcher.AudioFormat.DEEP_DIVE
    entry = state["processed_queue"]["crp-2026-08-29.json"]
    assert entry["content_hash"] == podcast_researcher.compute_payload_hash(payload)
    assert entry["audio_format"] == "deep-dive"
    assert entry["title"] == "CRP Norm"
    assert entry["output_file"] == str(mp3)
    assert entry["processed_at"]
    assert state["processed_queue_files"] == ["crp-2026-08-29.json"]
    assert calls["delivered"] == [(mp3, "crp")]
    # ledger is durable, not just in-memory
    persisted = json.loads((tmp_path / "state" / "state.json").read_text())
    assert persisted["processed_queue"]["crp-2026-08-29.json"]["output_file"] == str(mp3)


async def test_run_from_queue_honours_debate_format(tmp_path, monkeypatch):
    """An explicit debate payload still resolves to the debate format."""

    import podcast_researcher

    captured: dict = {}

    async def fake_build_podcast(client, **kwargs):
        captured.update(kwargs)
        return tmp_path / "episode.mp3"

    _stub_queue_run(monkeypatch, tmp_path, build_podcast=fake_build_podcast)
    payload = {
        "topic_id": "sepsis",
        "title": "Sepsis",
        "audio_format": "debate",
        "sources": [{"url": "https://a.example"}],
    }
    state: dict = {}

    assert await podcast_researcher._run_from_queue(payload, "sepsis.json", state) == 0
    assert captured["audio_format"] is podcast_researcher.AudioFormat.DEBATE
    assert state["processed_queue"]["sepsis.json"]["audio_format"] == "debate"


async def test_run_from_queue_marks_empty_sources_skipped(tmp_path, monkeypatch):
    """A payload with no sources is recorded as skipped without running the pipeline."""

    import podcast_researcher

    async def unreachable_build_podcast(client, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("build_podcast must not be called for an empty payload")

    calls = _stub_queue_run(monkeypatch, tmp_path, build_podcast=unreachable_build_podcast)
    state: dict = {}

    rc = await podcast_researcher._run_from_queue({"topic_id": "empty"}, "empty.json", state)

    assert rc == 0
    entry = state["processed_queue"]["empty.json"]
    assert entry["status"] == "SKIPPED_EMPTY_SOURCES"
    assert entry["content_hash"]
    assert state["processed_queue_files"] == ["empty.json"]
    assert calls["delivered"] == []


async def test_run_from_queue_failure_leaves_no_ledger_entry(tmp_path, monkeypatch):
    """A pipeline failure must not mark the item processed."""

    import podcast_researcher
    from podcast_pipeline import PodcastPipelineError

    async def failing_build_podcast(client, **kwargs):
        raise PodcastPipelineError("notebook creation failed")

    calls = _stub_queue_run(monkeypatch, tmp_path, build_podcast=failing_build_podcast)
    payload = {"topic_id": "crp", "title": "CRP", "sources": [{"url": "https://a.example"}]}
    state: dict = {}

    rc = await podcast_researcher._run_from_queue(payload, "crp.json", state)

    assert rc == 1
    assert state.get("processed_queue", {}) == {}
    assert state.get("processed_queue_files", []) == []
    assert calls["failed"] == [("crp", "notebook creation failed")]
