"""Authenticated add-on ingress routes for DAG Studio."""

from __future__ import annotations

import json
import secrets
from importlib.resources import files
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from ha_mcp.config import get_global_settings
from ha_mcp.settings_ui import _ingress_only
from ha_mcp.utils.data_paths import get_data_dir

from .models import DagDocument
from .repository import JsonDagRepository, RevisionConflict
from .service import DagStudioService

_CSRF = secrets.token_urlsafe(32)
_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'self'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _service() -> DagStudioService:
    settings = get_global_settings()
    root = (
        Path(settings.dag_studio_data_dir)
        if settings.dag_studio_data_dir
        else get_data_dir() / "dag_studio"
    )
    return DagStudioService(JsonDagRepository(root), read_only=False)


def _response(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"ok": status < 400, "data": data}, status_code=status, headers=_HEADERS
    )


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": {"code": code, "message": message}},
        status_code=status,
        headers=_HEADERS,
    )


def _csrf_ok(request: Request) -> bool:
    return secrets.compare_digest(request.headers.get("x-dag-csrf", ""), _CSRF)


async def _page(request: Request) -> Response:
    html = (
        files("ha_mcp.dag_studio.web.static").joinpath("index.html").read_text("utf-8")
    )
    html = html.replace("</head>", f'<meta name="dag-csrf" content="{_CSRF}"></head>')
    return HTMLResponse(html, headers=_HEADERS)


async def _asset(request: Request) -> Response:
    name = request.path_params["name"]
    if name not in {"app.js"}:
        return _error("NOT_FOUND", "Asset not found", 404)
    return Response(
        files("ha_mcp.dag_studio.web.static").joinpath(name).read_bytes(),
        media_type="text/javascript",
        headers=_HEADERS,
    )


async def _health(request: Request) -> Response:
    return _response({"status": "healthy", "ai_provider": "disabled"})


async def _documents(request: Request) -> Response:
    service = _service()
    if request.method == "GET":
        return _response([d.model_dump(mode="json") for d in service.list()])
    if not _csrf_ok(request):
        return _error("CSRF_INVALID", "Missing or invalid CSRF token", 403)
    try:
        body = await request.json()
        if len(json.dumps(body)) > get_global_settings().dag_studio_max_request_bytes:
            return _error("REQUEST_TOO_LARGE", "Request is too large", 413)
        doc = DagDocument.model_validate(body)
        saved = (
            service.create(doc)
            if doc.revision == 0
            else service.save(doc, doc.revision)
        )
        return _response(
            saved.model_dump(mode="json"), 201 if doc.revision == 0 else 200
        )
    except RevisionConflict as exc:
        return _error("DAG_REVISION_CONFLICT", str(exc), 409)
    except (ValueError, TypeError) as exc:
        return _error("DAG_INVALID", str(exc), 422)


async def _document(request: Request) -> Response:
    try:
        return _response(
            _service().get(request.path_params["document_id"]).model_dump(mode="json")
        )
    except (FileNotFoundError, ValueError):
        return _error("NOT_FOUND", "Document not found", 404)


async def _validate(request: Request) -> Response:
    if not _csrf_ok(request):
        return _error("CSRF_INVALID", "Missing or invalid CSRF token", 403)
    try:
        doc = DagDocument.model_validate(await request.json())
        return _response([f.model_dump() for f in _service().validate(doc)])
    except (ValueError, TypeError) as exc:
        return _error("DAG_INVALID", str(exc), 422)


def register_dag_studio_routes(mcp: FastMCP) -> None:
    """Mount the Studio only on add-on ingress root, never the public MCP path."""
    if not get_global_settings().enable_dag_studio:
        return
    routes = [
        ("/dag-studio", ["GET"], _page),
        ("/dag-studio/", ["GET"], _page),
        ("/dag-studio/{name}", ["GET"], _asset),
        ("/dag-studio/api/health", ["GET"], _health),
        ("/dag-studio/api/dags", ["GET", "POST", "PUT"], _documents),
        ("/dag-studio/api/dags/{document_id}", ["GET"], _document),
        ("/dag-studio/api/dags/{document_id}/validate", ["POST"], _validate),
    ]
    for path, methods, handler in routes:
        mcp.custom_route(path, methods=methods)(_ingress_only(handler))
