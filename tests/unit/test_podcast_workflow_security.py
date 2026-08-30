"""Static trust-boundary regressions for the Claude research producer."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_untrusted_workflow_does_not_construct_curl_commands():
    workflow = (ROOT / ".claude/workflows/podcast-deep-research.js").read_text()

    assert "curl -sL" not in workflow
    assert '"${c.url}"' not in workflow


def test_headless_runner_does_not_allow_bash():
    runner = (ROOT / "scripts/podcast_research_run.sh").read_text()

    assert "Bash(" not in runner


def test_workflow_does_not_accept_alternative_url_as_verified():
    workflow = (ROOT / ".claude/workflows/podcast-deep-research.js").read_text()

    assert "v.alternative_url && v.content_matches" not in workflow
    assert "rescuedCandidates" in workflow
