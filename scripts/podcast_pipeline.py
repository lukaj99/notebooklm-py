#!/usr/bin/env python3
"""End-to-end podcast orchestrator: notebook -> sources -> audio overview -> download.

This is the "orchestrator" half of the researcher/curator/orchestrator pipeline.
It takes an already-curated list of source URLs (research and curation happen
upstream — see the scheduled agent prompt in ``scripts/podcast_researcher_agent.md``)
and drives NotebookLM mechanically and reliably: create the notebook, ingest and
wait for every source, generate the audio overview with a style prompt, poll to
completion, and download the finished file.

Usage:
    uv run scripts/podcast_pipeline.py \\
        --title "CCB/Beta-Blocker Overdose — What's New" \\
        --source https://example.com/paper1 \\
        --source https://example.com/paper2 \\
        --style em-cases \\
        --format debate \\
        --length long \\
        --out-dir ~/podcasts

Exits non-zero (with a message on stderr) on any source or generation failure —
designed to be run unattended from a scheduled agent or cron job.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from notebooklm import NotebookLMClient
from notebooklm.rpc import AudioFormat, AudioLength

EM_CASES_STYLE = (
    "Frame this as a case-based discussion in the style of the EM Cases podcast: "
    "two co-hosts working through the material together, with 'what would you "
    "actually do at the bedside' framing rather than a textbook read-through. "
    "Include moments of genuine disagreement that get resolved by citing the "
    "evidence. Keep it conversational and energetic, but always land on clear, "
    "actionable pearls."
)

STYLE_PRESETS: dict[str, str | None] = {
    "em-cases": EM_CASES_STYLE,
    "plain": None,
}

AUDIO_FORMAT_CHOICES: dict[str, AudioFormat] = {
    "deep-dive": AudioFormat.DEEP_DIVE,
    "brief": AudioFormat.BRIEF,
    "critique": AudioFormat.CRITIQUE,
    "debate": AudioFormat.DEBATE,
}
AUDIO_LENGTH_CHOICES: dict[str, AudioLength] = {
    "short": AudioLength.SHORT,
    "default": AudioLength.DEFAULT,
    "long": AudioLength.LONG,
}


class PodcastPipelineError(RuntimeError):
    """Raised when the pipeline cannot produce a finished podcast."""


async def build_podcast(
    client: NotebookLMClient,
    *,
    title: str,
    source_urls: list[str],
    instructions: str | None,
    audio_format: AudioFormat,
    audio_length: AudioLength,
    out_dir: Path,
    source_wait_timeout: float = 180.0,
    poll_interval: float = 5.0,
    poll_timeout: float = 1800.0,
) -> Path:
    """Create a notebook, ingest sources, generate audio, and download it.

    Returns the path to the downloaded audio file. Raises
    ``PodcastPipelineError`` if any source fails to ingest or generation fails
    or times out.
    """

    if not source_urls:
        raise PodcastPipelineError("at least one source URL is required")

    notebook = await client.notebooks.create(title)
    notebook_id = notebook.id

    failed_sources: list[tuple[str, str]] = []
    for url in source_urls:
        try:
            await client.sources.add_url(notebook_id, url, wait=True, wait_timeout=source_wait_timeout)
        except Exception as exc:  # noqa: BLE001 - collected and reported together below
            failed_sources.append((url, str(exc)))

    if failed_sources:
        detail = "; ".join(f"{url}: {err}" for url, err in failed_sources)
        raise PodcastPipelineError(f"{len(failed_sources)} source(s) failed to ingest: {detail}")

    status = await client.artifacts.generate_audio(
        notebook_id,
        instructions=instructions,
        audio_format=audio_format,
        audio_length=audio_length,
    )

    elapsed = 0.0
    while not status.is_complete:
        if status.is_failed:
            raise PodcastPipelineError(f"audio generation failed: {status.error or 'unknown error'}")
        if elapsed >= poll_timeout:
            raise PodcastPipelineError(f"audio generation timed out after {poll_timeout:.0f}s")
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        status = await client.artifacts.poll_status(notebook_id, status.task_id)

    out_dir.mkdir(parents=True, exist_ok=True)
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip() or notebook_id
    output_path = out_dir / f"{safe_title}.mp3"
    downloaded = await client.artifacts.download_audio(
        notebook_id, str(output_path), artifact_id=status.task_id
    )
    return Path(downloaded)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", required=True, help="Notebook / podcast title")
    parser.add_argument(
        "--source", dest="sources", action="append", required=True, help="Source URL (repeatable)"
    )
    parser.add_argument(
        "--style",
        choices=sorted(STYLE_PRESETS),
        default="em-cases",
        help="Named instructions preset (default: em-cases). Use --instructions to override freely.",
    )
    parser.add_argument("--instructions", default=None, help="Raw instructions text, overrides --style")
    parser.add_argument("--format", choices=sorted(AUDIO_FORMAT_CHOICES), default="deep-dive")
    parser.add_argument("--length", choices=sorted(AUDIO_LENGTH_CHOICES), default="long")
    parser.add_argument("--out-dir", type=Path, default=Path.home() / "podcasts")
    parser.add_argument("--source-wait-timeout", type=float, default=180.0)
    parser.add_argument("--poll-timeout", type=float, default=1800.0)
    return parser.parse_args(argv)


async def _main_async(args: argparse.Namespace) -> int:
    instructions = args.instructions if args.instructions is not None else STYLE_PRESETS[args.style]
    async with NotebookLMClient.from_storage() as client:
        try:
            path = await build_podcast(
                client,
                title=args.title,
                source_urls=args.sources,
                instructions=instructions,
                audio_format=AUDIO_FORMAT_CHOICES[args.format],
                audio_length=AUDIO_LENGTH_CHOICES[args.length],
                out_dir=args.out_dir,
                source_wait_timeout=args.source_wait_timeout,
                poll_timeout=args.poll_timeout,
            )
        except PodcastPipelineError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print(str(path))
    return 0


def main() -> None:
    args = _parse_args()
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
