"""Unit tests for ``scripts/podcast_pipeline.build_podcast`` source tolerance.

The orchestrator runs unattended, so a single flaky source (a publisher that
403s an automated fetcher, a transient timeout) must not throw away an
otherwise good episode. These tests pin the partial-failure contract using a
fake client; the real NotebookLM flow is exercised end-to-end manually.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from podcast_pipeline import PodcastPipelineError, build_podcast  # noqa: E402


class _FakeNotebooks:
    def __init__(self, notebook_id: str = "nb-1") -> None:
        self._id = notebook_id

    async def create(self, title: str):
        return type("Notebook", (), {"id": self._id})()


class _FakeSources:
    def __init__(self, failing_urls: set[str]) -> None:
        self.failing_urls = failing_urls
        self.added: list[str] = []

    async def add_url(self, notebook_id: str, url: str, **kwargs):
        if url in self.failing_urls:
            raise RuntimeError(f"403 fetching {url}")
        self.added.append(url)
        return type("Source", (), {"id": f"source-{len(self.added)}"})()


class _FakeStatus:
    def __init__(self) -> None:
        self.is_complete = True
        self.is_failed = False
        self.error = None
        self.task_id = "task-1"


class _FakeArtifacts:
    def __init__(self) -> None:
        self.instructions = None
        self.source_ids = None

    async def generate_audio(self, notebook_id: str, *, instructions=None, **kwargs):
        self.instructions = instructions
        self.source_ids = kwargs.get("source_ids")
        return _FakeStatus()

    async def download_audio(self, notebook_id: str, path: str, **kwargs):
        Path(path).write_bytes(b"fake-mp3")
        return path


class _FakeClient:
    def __init__(self, failing_urls: set[str] | None = None) -> None:
        self.notebooks = _FakeNotebooks()
        self.sources = _FakeSources(failing_urls or set())
        self.artifacts = _FakeArtifacts()


async def _build(client, urls, tmp_path, **kwargs):
    return await build_podcast(
        client,
        title="Test Episode",
        source_urls=urls,
        instructions=None,
        audio_format=object(),
        audio_length=object(),
        out_dir=tmp_path,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_all_sources_succeed(tmp_path):
    client = _FakeClient()
    urls = [f"https://example.com/{i}" for i in range(5)]

    path = await _build(client, urls, tmp_path)

    assert Path(path).exists()
    assert client.sources.added == urls
    assert client.artifacts.source_ids == [
        "source-1",
        "source-2",
        "source-3",
        "source-4",
        "source-5",
    ]


@pytest.mark.asyncio
async def test_proceeds_when_a_minority_of_sources_fail(tmp_path):
    urls = [f"https://example.com/{i}" for i in range(6)]
    client = _FakeClient(failing_urls={urls[2]})

    path = await _build(client, urls, tmp_path)

    assert Path(path).exists()
    assert urls[2] not in client.sources.added
    assert len(client.sources.added) == 5


@pytest.mark.asyncio
async def test_aborts_when_too_few_sources_survive(tmp_path):
    urls = [f"https://example.com/{i}" for i in range(5)]
    client = _FakeClient(failing_urls=set(urls[:3]))

    with pytest.raises(PodcastPipelineError, match="only 2 of 5 source"):
        await _build(client, urls, tmp_path)


@pytest.mark.asyncio
async def test_min_sources_is_configurable(tmp_path):
    urls = [f"https://example.com/{i}" for i in range(5)]
    client = _FakeClient(failing_urls=set(urls[:3]))

    path = await _build(client, urls, tmp_path, min_sources=2)

    assert Path(path).exists()


@pytest.mark.asyncio
async def test_error_names_the_failed_sources(tmp_path):
    urls = [f"https://example.com/{i}" for i in range(4)]
    client = _FakeClient(failing_urls=set(urls))

    with pytest.raises(PodcastPipelineError) as excinfo:
        await _build(client, urls, tmp_path)

    assert urls[0] in str(excinfo.value)


@pytest.mark.asyncio
async def test_empty_source_list_rejected(tmp_path):
    with pytest.raises(PodcastPipelineError, match="at least one source"):
        await _build(_FakeClient(), [], tmp_path)
