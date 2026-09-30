from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from notebooklm_mcp.server import _add_source_for_type


class RecordingSources:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def add_url(self, notebook_id: str, content: str):
        self.calls.append(("add_url", (notebook_id, content)))
        return SimpleNamespace(
            id="src",
            title="title",
            url=content,
            kind=SimpleNamespace(value="web_page"),
            status=2,
            created_at=None,
        )

    async def add_text(self, notebook_id: str, title: str, content: str):
        self.calls.append(("add_text", (notebook_id, title, content)))
        return SimpleNamespace(
            id="src",
            title=title,
            url=None,
            kind=SimpleNamespace(value="text"),
            status=2,
            created_at=None,
        )

    async def add_file(self, notebook_id: str, path: Path):
        self.calls.append(("add_file", (notebook_id, path)))
        return SimpleNamespace(
            id="src",
            title=path.name,
            url=None,
            kind=SimpleNamespace(value="file"),
            status=2,
            created_at=None,
        )


class RecordingClient:
    def __init__(self) -> None:
        self.sources = RecordingSources()


@pytest.mark.asyncio
async def test_add_source_dispatches_to_correct_client_methods(tmp_path: Path) -> None:
    client = RecordingClient()

    await _add_source_for_type(
        client,
        notebook_id="nb-1",
        source_type="url",
        content="https://example.com",
        title=None,
    )
    await _add_source_for_type(
        client,
        notebook_id="nb-1",
        source_type="text",
        content="body text",
        title="Notes",
    )
    await _add_source_for_type(
        client,
        notebook_id="nb-1",
        source_type="youtube",
        content="https://youtube.com/watch?v=abc123",
        title=None,
    )
    await _add_source_for_type(
        client,
        notebook_id="nb-1",
        source_type="file",
        content=str(tmp_path / "sample.txt"),
        title=None,
    )

    assert client.sources.calls == [
        ("add_url", ("nb-1", "https://example.com")),
        ("add_text", ("nb-1", "Notes", "body text")),
        ("add_url", ("nb-1", "https://youtube.com/watch?v=abc123")),
        ("add_file", ("nb-1", Path(tmp_path / "sample.txt").expanduser())),
    ]
