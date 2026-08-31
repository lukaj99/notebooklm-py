"""Tests for the personal podcast request CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import podcast_request  # noqa: E402


def test_enqueue_writes_private_versioned_request(tmp_path, capsys):
    exit_code = podcast_request.run_cli(
        ["--home", str(tmp_path), "enqueue", "Why do coding agents fail?", "--risk", "ordinary"]
    )

    assert exit_code == 0
    queued = list((tmp_path / "podcast_inbox").glob("*.json"))
    assert len(queued) == 1
    payload = json.loads(queued[0].read_text())
    assert payload["schema_version"] == 2
    assert payload["prompt"] == "Why do coding agents fail?"
    assert payload["risk"] == "ordinary"
    assert payload["audio_format"] == "deep-dive"
    assert capsys.readouterr().out.strip() == queued[0].stem


def test_run_dry_run_creates_audit_bundle_without_runner(tmp_path, capsys):
    exit_code = podcast_request.run_cli(["--home", str(tmp_path), "run", "A prompt", "--dry-run"])

    assert exit_code == 0
    run_id = capsys.readouterr().out.strip()
    state = json.loads((tmp_path / "podcast_runs" / run_id / "state.json").read_text())
    assert state["stage"] == "REQUESTED"


def test_status_json_reads_existing_run(tmp_path, capsys):
    podcast_request.run_cli(["--home", str(tmp_path), "run", "A prompt", "--dry-run"])
    run_id = capsys.readouterr().out.strip()

    assert podcast_request.run_cli(["--home", str(tmp_path), "status", run_id, "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["stage"] == "REQUESTED"


def test_resume_rejects_non_retryable_run(tmp_path, capsys):
    podcast_request.run_cli(["--home", str(tmp_path), "run", "A prompt", "--dry-run"])
    run_id = capsys.readouterr().out.strip()

    assert podcast_request.run_cli(["--home", str(tmp_path), "resume", run_id]) == 2
    assert "not retryable" in capsys.readouterr().err


def test_live_run_delegates_to_runner(tmp_path, monkeypatch, capsys):
    seen = []

    async def fake_execute(store):
        seen.append(store.path.name)

    monkeypatch.setattr(podcast_request, "execute_run", fake_execute)

    assert podcast_request.run_cli(["--home", str(tmp_path), "run", "A prompt"]) == 0
    assert seen == [capsys.readouterr().out.strip()]


def test_resume_delivers_existing_audio_without_regeneration(tmp_path, monkeypatch):
    import asyncio

    import podcast_evidence_runner
    from podcast_workflow import PodcastRequest, RunStage, RunStore

    store = RunStore.create(tmp_path / "podcast_runs", PodcastRequest(prompt="A prompt"))
    audio_file = store.path / f"{store.path.name}.mp3"
    audio_file.write_bytes(b"existing audio content")

    store.transition(RunStage.DISCOVERING)
    store.transition(
        RunStage.RETRYABLE_FAILURE,
        resume_stage=RunStage.DELIVERING.value,
        audio_path=str(audio_file),
    )

    delivered = []

    async def fake_deliver(self, output):
        delivered.append(str(output))

    monkeypatch.setattr(podcast_evidence_runner.EvidencePodcastRunner, "_deliver", fake_deliver)

    runner = podcast_evidence_runner.EvidencePodcastRunner(store)
    asyncio.run(runner.execute())

    state = store.read_json("state.json")
    assert state["stage"] == RunStage.DELIVERED.value
    assert delivered == [str(audio_file)]


def test_runner_records_cancelled_on_cancellation(tmp_path, monkeypatch):
    import asyncio

    import podcast_evidence_runner
    import pytest
    from podcast_workflow import PodcastRequest, RunStage, RunStore

    store = RunStore.create(tmp_path / "podcast_runs", PodcastRequest(prompt="A prompt"))

    async def fake_execute(self):
        raise asyncio.CancelledError()

    monkeypatch.setattr(podcast_evidence_runner.EvidencePodcastRunner, "_execute", fake_execute)

    runner = podcast_evidence_runner.EvidencePodcastRunner(store)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner.execute())

    state = store.read_json("state.json")
    assert state["stage"] == RunStage.CANCELLED.value
