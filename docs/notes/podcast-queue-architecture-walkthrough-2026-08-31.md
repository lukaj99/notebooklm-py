# Walkthrough: Content-Addressed Queue Architecture & Preference Inversion

This document records the architectural improvements implemented on 2026-08-31 for the NotebookLM podcast pipeline, addressing post-mortem review findings regarding queue state lifecycle management, user preference inheritance, and cross-platform test reliability.

---

## 1. Key Changes Made

### A. Content-Addressed Queue State Management
- **Canonical Payload Hashing (`scripts/podcast_researcher.py`)**:
  Added `compute_payload_hash(payload: dict) -> str` using deterministic, normalized JSON SHA-256 digests over `topic_id`, `title`, `audio_format`, `style`, `rationale`, `case_vignette`, and the sorted set of source **URLs**.

  Scope limitation: only the URL of each source is hashed. A source's `title` and `why` fields are *not* part of the digest, so retitling a source — even though the title reaches `articles` and the delivery message — does not mark the item as changed. Editing the URL set does.
- **Content-Addressed Candidate Selection**:
  Updated `next_queue_item(state)`:
  - If a file exists in `state["processed_queue"]`, it compares the stored `content_hash` against the current file's hash.
  - If the content changed (e.g. format changed from debate to deep-dive, prompt rewritten, or a source URL added/removed/replaced), it is **automatically detected as an active candidate** and re-run without requiring manual surgery on `podcast_pipeline_state.json`.
- **Automatic Migration & Backward Compatibility**:
  - Legacy `processed_queue_files: list[str]` entries are preserved. When first seen, their current hash is recorded into `state["processed_queue"]`, preventing accidental re-runs of historical episodes while enabling automatic re-runs if any file is edited in the future.
  - The migration is persisted by `next_queue_item()` itself, via a `save_state(state)` guarded on an actual migration having occurred. This matters because every caller can return before reaching its own `save_state()` — `_run_from_queue` on pipeline failure, and `_run_from_pubmed_fallback` when all topics are on cooldown. Without the immediate write the ledger silently lagged the queue for another cycle. (The migration is idempotent, so the pre-fix behaviour was benign, not corrupting.)
- **Detailed Execution Ledger**:
  Completed runs now save `{ "content_hash", "audio_format", "processed_at", "title", "output_file" }` directly into `state["processed_queue"][filename]`. The empty-sources path writes a `"status": "SKIPPED_EMPTY_SOURCES"` entry instead, and a pipeline failure writes nothing at all, so a failed episode stays eligible for the next run.

  `audio_format` records the payload vocabulary (`"deep-dive"` / `"debate"`). `AudioFormat` is an *int* enum, so the original `resolved_format.value` wrote the opaque RPC code (`1` / `4`) here while the migration path wrote the string — the same field carried two types depending on which branch produced it. It is normalised to the string form on both paths.
- **Unreadable Queue Files**:
  A corrupt or unreadable queue file is recorded in the durable ledger as `{"status": "UNREADABLE", "recorded_at": ...}` rather than added to a local `set` copy that never reached `state`. It carries no `content_hash`, so repairing the file makes it an eligible candidate again; while it stays broken it is logged once and then skipped quietly, instead of raising a fresh exception trace on every run.

### B. User Preference First-Class Defaults
- **Workflow & Request Contracts (`scripts/podcast_workflow.py` & `scripts/podcast_request.py`)**:
  - Defined `DEFAULT_AUDIO_FORMAT = "deep-dive"`.
  - Defaulted `PodcastRequest.audio_format` and CLI `--format` from `"auto"` to `"deep-dive"`.
  - **This is a declarative change, not a behavioural one.** `resolve_quality()` already mapped `"auto"` to `"deep-dive"` (`podcast_workflow.py:396`), so the resolved format is unchanged either way. The value is that the preference is now legible in the contract rather than buried in a resolution branch; `"auto"` remains accepted for backward compatibility.
- **Topic Rotation (`scripts/podcast_topics.json`)**:
  - Changed default across all 22 topics from `"debate": true` to `"debate": false`. **This is the real behavioural inversion** for the cron-driven PubMed fallback path.
- **Deep Research Prompt (`.claude/workflows/podcast-deep-research.js`)**:
  - Instructed the research agent that the listener strongly prefers investigative deep dives over adversarial debates, reserving `"debate"` strictly for explicit, verified controversies. **This is the real behavioural inversion** for the cloud-curated queue path, since the agent's chosen `audio_format` is what `_run_from_queue` reads.

### C. Cross-Platform Test Hardening
- **Permission Assertion Helper (`tests/unit/test_podcast_workflow_script.py`)**:
  - Added `assert_private_fs_mode(path, expected_posix_mode)` so the suite runs on Windows, where NTFS does not carry POSIX octal modes. On Windows the helper asserts only that the path exists — it **skips** the permission check rather than adapting it, so private-mode enforcement has no coverage on that platform.

---

## 2. Verification & Validation Results

### Unit Tests
- Tested `compute_payload_hash` determinism and key sorting.
- Tested `next_queue_item` skipping matching content hashes.
- Tested `next_queue_item` re-processing modified content hashes.
- Tested seamless migration of legacy `processed_queue_files`.
- Tested that the migration is written through to the state file, and that a queue with nothing to migrate leaves the state file untouched.
- Tested unreadable-file handling: first sighting is recorded durably, a known-broken file does not re-dirty the state, and a repaired file becomes a candidate again.
- Tested the ledger write paths directly: a completed run records hash/format/title/output path and delivers; an explicit `debate` payload resolves to `AudioFormat.DEBATE`; an empty-sources payload is marked `SKIPPED_EMPTY_SOURCES` without invoking the pipeline; and a `PodcastPipelineError` leaves no ledger entry.

```bash
uv run pytest tests/unit/test_podcast_*.py
# Result: 113 passed
```

### Live State Dry Run
Verified against a **copy** of `~/.notebooklm/podcast_pipeline_state.json` (the live file was deliberately left untouched):

- All 23 historical files in `processed_queue_files` were recognised, hashed, and migrated into `processed_queue` without being re-triggered.
- The migration was written through to disk by `next_queue_item()` alone.
- The one genuinely unprocessed queue file (`ed-sepsis-bundle-controversies-2026-08-31.json`, of 24 on disk) was correctly returned as the next candidate.

The live state file itself is still un-migrated: it holds `processed_queue_files` only. It will migrate on the pipeline's next real run. An earlier revision of this document claimed the live file had already been migrated; that was incorrect.

### Static Analysis
```bash
uv run ruff check scripts/ tests/
# Result: All checks passed!

uv run mypy src/notebooklm scripts/_live_auth_scenarios --ignore-missing-imports
# Result: Success: no issues found in 364 source files
```

Note the mypy invocation's scope is `src/notebooklm` and `scripts/_live_auth_scenarios`. It does **not** cover `scripts/podcast_researcher.py`, `scripts/podcast_workflow.py`, or `scripts/podcast_request.py`, where every change described above lives — a clean mypy run is not evidence about this change. Ruff does cover `scripts/`.

### Full CI Gate
The unit-only run above is **not** the merge gate. Per `CLAUDE.md`, a narrow install/selection silently skips the `importorskip`-guarded MCP and server suites and understates coverage. The CI-equivalent command must be run before pushing:

```bash
uv run pytest -n auto --dist loadgroup --cov=src/notebooklm \
  --cov-report=term-missing --cov-fail-under=90
# Result: 16836 passed, 64 skipped, 1 xfailed in 84.04s
# Required test coverage of 90% reached. Total coverage: 96.70%
```
