"""Artifact management and generation endpoints."""

from fastapi import APIRouter, Depends
from notebooklm import NotebookLMClient

from ..deps import get_client
from ..models.artifacts import (
    ArtifactResponse,
    GenerateAudioRequest,
    GenerateReportRequest,
    GenerateVideoRequest,
    GenerationStatusResponse,
)

router = APIRouter(prefix="/notebooks/{notebook_id}/artifacts", tags=["artifacts"])


def _artifact_response(a) -> ArtifactResponse:
    return ArtifactResponse(
        id=a.id,
        title=a.title,
        kind=a.kind.value,
        status=a.status,
        status_str=a.status_str,
        created_at=a.created_at,
        url=a.url,
        is_completed=a.is_completed,
    )


def _gen_status_response(gs) -> GenerationStatusResponse:
    return GenerationStatusResponse(
        task_id=gs.task_id,
        status=gs.status,
        url=gs.url,
        error=gs.error,
        is_complete=gs.is_complete,
        is_failed=gs.is_failed,
    )


@router.get("", response_model=list[ArtifactResponse])
async def list_artifacts(
    notebook_id: str,
    client: NotebookLMClient = Depends(get_client),
):
    artifacts = await client.artifacts.list(notebook_id)
    return [_artifact_response(a) for a in artifacts]


@router.post("/generate/audio", response_model=GenerationStatusResponse, status_code=202)
async def generate_audio(
    notebook_id: str,
    body: GenerateAudioRequest | None = None,
    client: NotebookLMClient = Depends(get_client),
):
    kwargs = {}
    if body:
        if body.instructions:
            kwargs["instructions"] = body.instructions
        if body.source_ids:
            kwargs["source_ids"] = body.source_ids
    gs = await client.artifacts.generate_audio(notebook_id, **kwargs)
    return _gen_status_response(gs)


@router.post("/generate/video", response_model=GenerationStatusResponse, status_code=202)
async def generate_video(
    notebook_id: str,
    body: GenerateVideoRequest | None = None,
    client: NotebookLMClient = Depends(get_client),
):
    kwargs = {}
    if body:
        if body.instructions:
            kwargs["instructions"] = body.instructions
        if body.source_ids:
            kwargs["source_ids"] = body.source_ids
    gs = await client.artifacts.generate_video(notebook_id, **kwargs)
    return _gen_status_response(gs)


@router.post("/generate/report", response_model=GenerationStatusResponse, status_code=202)
async def generate_report(
    notebook_id: str,
    body: GenerateReportRequest | None = None,
    client: NotebookLMClient = Depends(get_client),
):
    kwargs = {}
    if body:
        if body.prompt:
            kwargs["custom_prompt"] = body.prompt
        if body.source_ids:
            kwargs["source_ids"] = body.source_ids
    gs = await client.artifacts.generate_report(notebook_id, **kwargs)
    return _gen_status_response(gs)


@router.get("/status/{task_id}", response_model=GenerationStatusResponse)
async def check_generation_status(
    notebook_id: str,
    task_id: str,
    client: NotebookLMClient = Depends(get_client),
):
    gs = await client.artifacts.poll_status(notebook_id, task_id)
    return _gen_status_response(gs)


@router.delete("/{artifact_id}", status_code=204)
async def delete_artifact(
    notebook_id: str,
    artifact_id: str,
    client: NotebookLMClient = Depends(get_client),
):
    await client.artifacts.delete(notebook_id, artifact_id)
