"""Source management endpoints."""

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from notebooklm import NotebookLMClient

from ..deps import get_client
from ..models.sources import (
    AddDriveRequest,
    AddTextRequest,
    AddUrlRequest,
    SourceFulltextResponse,
    SourceGuideResponse,
    SourceResponse,
)

router = APIRouter(prefix="/notebooks/{notebook_id}/sources", tags=["sources"])


def _source_response(src) -> SourceResponse:
    return SourceResponse(
        id=src.id,
        title=src.title,
        url=src.url,
        kind=src.kind.value,
        created_at=src.created_at,
        status=src.status,
        is_ready=src.is_ready,
        is_processing=src.is_processing,
    )


@router.get("", response_model=list[SourceResponse])
async def list_sources(
    notebook_id: str,
    client: NotebookLMClient = Depends(get_client),
):
    sources = await client.sources.list(notebook_id)
    return [_source_response(s) for s in sources]


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(
    notebook_id: str,
    source_id: str,
    client: NotebookLMClient = Depends(get_client),
):
    source = await client.sources.get(notebook_id, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source {source_id} not found")
    return _source_response(source)


@router.post("/url", response_model=SourceResponse, status_code=201)
async def add_url_source(
    notebook_id: str,
    body: AddUrlRequest,
    client: NotebookLMClient = Depends(get_client),
):
    source = await client.sources.add_url(
        notebook_id, body.url, wait=body.wait, wait_timeout=body.wait_timeout
    )
    return _source_response(source)


@router.post("/text", response_model=SourceResponse, status_code=201)
async def add_text_source(
    notebook_id: str,
    body: AddTextRequest,
    client: NotebookLMClient = Depends(get_client),
):
    source = await client.sources.add_text(
        notebook_id, body.title, body.content, wait=body.wait, wait_timeout=body.wait_timeout
    )
    return _source_response(source)


@router.post("/drive", response_model=SourceResponse, status_code=201)
async def add_drive_source(
    notebook_id: str,
    body: AddDriveRequest,
    client: NotebookLMClient = Depends(get_client),
):
    source = await client.sources.add_drive(
        notebook_id,
        body.file_id,
        body.title,
        body.mime_type,
        wait=body.wait,
        wait_timeout=body.wait_timeout,
    )
    return _source_response(source)


@router.post("/file", response_model=SourceResponse, status_code=201)
async def add_file_source(
    notebook_id: str,
    file: UploadFile,
    client: NotebookLMClient = Depends(get_client),
):
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(
        suffix=Path(file.filename or "upload").suffix, delete=False
    ) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        source = await client.sources.add_file(notebook_id, tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return _source_response(source)


@router.delete("/{source_id}", status_code=204)
async def delete_source(
    notebook_id: str,
    source_id: str,
    client: NotebookLMClient = Depends(get_client),
):
    await client.sources.delete(notebook_id, source_id)


@router.get("/{source_id}/guide", response_model=SourceGuideResponse)
async def get_source_guide(
    notebook_id: str,
    source_id: str,
    client: NotebookLMClient = Depends(get_client),
):
    guide = await client.sources.get_guide(notebook_id, source_id)
    return SourceGuideResponse(**guide)


@router.get("/{source_id}/fulltext", response_model=SourceFulltextResponse)
async def get_source_fulltext(
    notebook_id: str,
    source_id: str,
    client: NotebookLMClient = Depends(get_client),
):
    ft = await client.sources.get_fulltext(notebook_id, source_id)
    return SourceFulltextResponse(
        source_id=ft.source_id,
        title=ft.title,
        content=ft.content,
        kind=ft.kind.value,
        url=ft.url,
        char_count=ft.char_count,
    )
