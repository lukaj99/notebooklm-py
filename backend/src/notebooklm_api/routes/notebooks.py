"""Notebook CRUD endpoints."""

from fastapi import APIRouter, Depends
from notebooklm import NotebookLMClient

from ..deps import get_client
from ..models.notebooks import (
    CreateNotebookRequest,
    NotebookDescriptionResponse,
    NotebookResponse,
    RenameNotebookRequest,
    ShareNotebookRequest,
    ShareResponse,
)

router = APIRouter(prefix="/notebooks", tags=["notebooks"])


def _notebook_response(nb) -> NotebookResponse:
    return NotebookResponse(
        id=nb.id,
        title=nb.title,
        created_at=nb.created_at,
        sources_count=nb.sources_count,
        is_owner=nb.is_owner,
    )


@router.get("", response_model=list[NotebookResponse])
async def list_notebooks(client: NotebookLMClient = Depends(get_client)):
    notebooks = await client.notebooks.list()
    return [_notebook_response(nb) for nb in notebooks]


@router.post("", response_model=NotebookResponse, status_code=201)
async def create_notebook(
    body: CreateNotebookRequest,
    client: NotebookLMClient = Depends(get_client),
):
    nb = await client.notebooks.create(body.title)
    return _notebook_response(nb)


@router.get("/{notebook_id}", response_model=NotebookResponse)
async def get_notebook(
    notebook_id: str,
    client: NotebookLMClient = Depends(get_client),
):
    nb = await client.notebooks.get(notebook_id)
    return _notebook_response(nb)


@router.delete("/{notebook_id}", status_code=204)
async def delete_notebook(
    notebook_id: str,
    client: NotebookLMClient = Depends(get_client),
):
    await client.notebooks.delete(notebook_id)


@router.patch("/{notebook_id}", response_model=NotebookResponse)
async def rename_notebook(
    notebook_id: str,
    body: RenameNotebookRequest,
    client: NotebookLMClient = Depends(get_client),
):
    nb = await client.notebooks.rename(notebook_id, body.title)
    return _notebook_response(nb)


@router.get("/{notebook_id}/description", response_model=NotebookDescriptionResponse)
async def get_description(
    notebook_id: str,
    client: NotebookLMClient = Depends(get_client),
):
    desc = await client.notebooks.get_description(notebook_id)
    return NotebookDescriptionResponse(
        summary=desc.summary,
        suggested_topics=[
            {"question": t.question, "prompt": t.prompt} for t in desc.suggested_topics
        ],
    )


@router.post("/{notebook_id}/share", response_model=ShareResponse)
async def share_notebook(
    notebook_id: str,
    body: ShareNotebookRequest,
    client: NotebookLMClient = Depends(get_client),
):
    result = await client.notebooks.share(
        notebook_id,
        public=body.public,
        artifact_id=body.artifact_id,
    )
    return ShareResponse(**result)
