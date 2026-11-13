"""HTTP backend for gateway-pricing, mounted by Hermes at ``/api/plugins/<id>/``.

Hermes applies the ``?profile=`` scope before these handlers run, so every handler resolves the
Hermes home, settings and ``state.db`` per request. Handlers are sync on purpose: FastAPI runs
them in a worker thread that inherits the request's profile scope. JSON only; no secrets are read
or returned. Rules are saved through Hermes' own ``plugins.manage`` settings action (the desktop
SDK's ``host.request``), not through this API.

Hermes imports this file as a standalone module, so the plugin package beside it is loaded by
path (reusing the copy Hermes already imported, when there is one).
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

_ROOT = Path(__file__).resolve().parent.parent


def _load_package():
    init = _ROOT / "__init__.py"
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if path and getattr(module, "__path__", None) is not None:
            try:
                if Path(path).resolve() == init:
                    return module
            except OSError:
                continue
    name = "gateway_pricing_" + hashlib.sha256(str(_ROOT).encode()).hexdigest()[:10]
    spec = importlib.util.spec_from_file_location(name, init, submodule_search_locations=[str(_ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_package = _load_package()
_service = importlib.import_module(_package.__name__ + ".service")
_errors = importlib.import_module(_package.__name__ + ".errors")
PLUGIN_ID = importlib.import_module(_package.__name__ + ".version").manifest_name()

router = APIRouter()


def _call(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except _errors.HTTP_ERRORS as exc:
        raise HTTPException(status_code=_errors.status_for(exc), detail=str(exc)) from None


@router.get("/status")
def get_status() -> dict:
    return _call(_service.status, PLUGIN_ID)


@router.get("/detected")
def get_detected(days: int = Query(30, ge=1, le=3650)) -> list:
    return _call(_service.detected, PLUGIN_ID, days=days)


@router.post("/preview")
def post_preview(body: dict = Body(...)) -> dict:
    return _call(
        _service.preview,
        PLUGIN_ID,
        body.get("model"),
        body.get("base_url"),
        rules=body.get("rules"),
        aliases=body.get("aliases"),
        usage=body.get("usage"),
    )


@router.post("/backfill")
def post_backfill(body: dict | None = Body(None)) -> dict:
    body = body or {}
    days = body.get("days")
    if days is not None and (isinstance(days, bool) or not isinstance(days, int)):
        raise HTTPException(status_code=400, detail="days must be an integer")
    return _call(
        _service.backfill,
        PLUGIN_ID,
        dry_run=body.get("dry_run", True),
        days=days,
        rules=body.get("rules"),
        confirm=body.get("confirm", False),
    )
