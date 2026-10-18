"""HTTP entry point for the local MENSIS workspace."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import Settings
from app.database import TASK_STATUSES, TaskStore
from app.engine import TaskEngine
from app.providers.ollama import OllamaProvider
from app.tools import build_tool_registry


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=2000)

    @field_validator("request")
    @classmethod
    def request_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter a task before submitting.")
        return value


TaskStatus = Literal[
    "queued",
    "planning",
    "running",
    "verifying",
    "completed",
    "blocked",
    "failed",
    "cancelled",
]


def create_app(
    settings: Settings | None = None,
    *,
    provider: OllamaProvider | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.prepare_directories()
    store = TaskStore(settings.database_path)
    store.initialize()
    tool_registry = build_tool_registry(settings)
    model_provider = provider or OllamaProvider.from_settings(settings)
    engine = TaskEngine(
        store,
        tool_registry,
        model_provider,
        max_workers=settings.max_workers,
    )

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        engine.start()
        try:
            yield
        finally:
            engine.stop()

    application = FastAPI(
        title="MENSIS",
        summary="A local-first personal assistant task workspace.",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.store = store
    application.state.engine = engine
    application.state.tools = tool_registry
    application.state.provider = model_provider

    static_dir = Path(__file__).resolve().parent.parent / "web"
    application.mount(
        "/static",
        StaticFiles(directory=static_dir, check_dir=True),
        name="static",
    )

    @application.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @application.get("/api/health")
    def health() -> dict[str, object]:
        # Touch SQLite so this also detects a database that cannot be opened.
        store.list_queued_ids()
        return {
            "status": "ok",
            "service": "mensis",
            "version": application.version,
            "provider": {
                "id": "ollama",
                "configured": bool(model_provider.configured),
                "model": model_provider.model_name,
            },
            "workspace_label": settings.workspace.name or "workspace",
            "max_workers": engine.max_workers,
            "available_tools": len(tool_registry.describe()),
        }

    @application.get("/api/tools")
    def list_tools() -> dict[str, object]:
        return {"tools": tool_registry.describe()}

    @application.post("/api/tasks", status_code=202)
    def create_task(body: TaskCreate) -> dict[str, object]:
        return engine.create_task(body.request)

    @application.get("/api/tasks")
    def list_tasks(
        limit: int = Query(default=50, ge=1, le=100),
        status: TaskStatus | None = Query(default=None),
    ) -> dict[str, object]:
        return {
            "tasks": store.list_tasks(limit=limit, status=status),
            "limit": limit,
            "statuses": sorted(TASK_STATUSES),
        }

    @application.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, object]:
        task = store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        return task

    @application.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str) -> dict[str, object]:
        accepted, task = engine.cancel_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        return {"accepted": accepted, "task": task}

    @application.post("/api/tasks/{task_id}/resume")
    def resume_task(task_id: str) -> dict[str, object]:
        resumed, task = engine.resume_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        if not resumed:
            raise HTTPException(
                status_code=409,
                detail=f"Only blocked or failed tasks can be resumed; current status is {task['status']}.",
            )
        return {"accepted": True, "task": task}

    @application.middleware("http")
    async def reject_oversized_api_payloads(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if request.url.path.startswith("/api/") and content_length:
            try:
                if int(content_length) > 256 * 1024:
                    return _error_response(413, "Request body exceeds the 256 KiB limit.")
            except ValueError:
                return _error_response(400, "Invalid Content-Length header.")
        return await call_next(request)

    return application


def _error_response(status_code: int, message: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"detail": message})


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.environ.get("MENSIS_HOST", "127.0.0.1"),
        port=int(os.environ.get("MENSIS_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    run()
