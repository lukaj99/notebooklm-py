# Walkthrough: Content-Addressed Queue Architecture & Preference Inversion

This document records the architectural improvements implemented on 2026-08-31 for the NotebookLM podcast pipeline, addressing post-mortem review findings regarding queue state lifecycle management, user preference inheritance, and cross-platform test reliability.

---

## 1. Key Changes Made

### A. Content-Addressed Queue State Management
- **Canonical Payload Hashing (`scripts/podcast_researcher.py`)**:
  Added `compute_payload_hash(payload: dict) -> str` using deterministic, normalized JSON SHA-256 digests over `topic_id`, `title`, `audio_format`, `style`, `rationale`, `case_vignette`, and sorted `sources`.
- **Content-Addressed Candidate Selection**:
  Updated `next_queue_item(state)`:
  - If a file exists in `state["processed_queue"]`, it compares the stored `content_hash` against the current file's hash.
  - If the content changed (e.g. format changed from debate to deep-dive, prompt rewritten, or sources updated), it is **automatically detected as an active candidate** and re-run without requiring manual surgery on `podcast_pipeline_state.json`.
- **Automatic Migration & Backward Compatibility**:
  - Legacy `processed_queue_files: list[str]` entries are preserved. When first seen, their current hash is recorded into `state["processed_queue"]`, preventing accidental re-runs of historical episodes while enabling automatic re-runs if any file is edited in the future.
- **Detailed Execution Ledger**:
  Completed runs now save `{ "content_hash", "audio_format", "processed_at", "title", "output_file" }` directly into `state["processed_queue"][filename]`.

### B. User Preference First-Class Defaults
- **Workflow & Request Contracts (`scripts/podcast_workflow.py` & `scripts/podcast_request.py`)**:
  - Defined `DEFAULT_AUDIO_FORMAT = "deep-dive"`.
  - Defaulted `PodcastRequest.audio_format` and CLI `--format` to `"deep-dive"`.
- **Topic Rotation (`scripts/podcast_topics.json`)**:
  - Changed default across all topics from `"debate": true` to `"debate": false`.
- **Deep Research Prompt (`.claude/workflows/podcast-deep-research.js`)**:
  - Instructed the research agent that the listener strongly prefers investigative deep dives over adversarial debates, reserving `"debate"` strictly for explicit, verified controversies.

### C. Cross-Platform Test Hardening
- **Permission Assertion Helper (`tests/unit/test_podcast_workflow_script.py`)**:
  - Added `assert_private_fs_mode(path, expected_posix_mode)` ensuring test assertions are safe on both POSIX systems and Windows runners where NTFS octal permissions are not supported.

---

## 2. Verification & Validation Results

### Unit Tests
- Tested `compute_payload_hash` determinism and key sorting.
- Tested `next_queue_item` skipping matching content hashes.
- Tested `next_queue_item` re-processing modified content hashes.
- Tested seamless migration of legacy `processed_queue_files`.
- Tested against live `~/.notebooklm/podcast_pipeline_state.json`:
  - Successfully recognized and migrated all 23 historical files into `processed_queue` without re-triggering them.
  - Correctly identified new queued items as next eligible candidates.

```bash
uv run pytest tests/unit/test_podcast_*.py
# Result: 104 passed in 0.41s

uv run pytest tests/unit/
# Result: 13,489 passed in 192s

uv run ruff check scripts/ tests/
# Result: All checks passed!

uv run mypy src/notebooklm scripts/_live_auth_scenarios --ignore-missing-imports
# Result: Success: no issues found in 364 source files
```

All changes committed and pushed to `main` (`3a8c193e`).
