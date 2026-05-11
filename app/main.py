"""FastAPI application: dashboard and REST API for managed TCP proxies."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError, field_validator

from app import auth_logic, database as db
from app.auth_logic import (
    authenticate,
    needs_first_run_setup,
    require_login,
    security,
)
from app.proxy_manager import ProxyManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class ProxyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    listen_port: int
    target_host: str = Field(..., min_length=1, max_length=255)
    target_port: int
    auto_start: bool = False

    @field_validator("listen_port")
    @classmethod
    def listen_range(cls, v: int) -> int:
        if v < 1024 or v > 65535:
            raise ValueError("listen_port must be between 1024 and 65535")
        return v

    @field_validator("target_port")
    @classmethod
    def target_range(cls, v: int) -> int:
        if v < 1 or v > 65535:
            raise ValueError("target_port must be between 1 and 65535")
        return v

    @field_validator("target_host")
    @classmethod
    def host_strip(cls, v: str) -> str:
        return v.strip()


class SetupBody(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=8, max_length=72)


def _load_external_route_specs() -> list[dict]:
    specs: list[dict] = []
    raw_path = os.environ.get("PROXY_ROUTES_FILE", "").strip()
    if raw_path:
        file_path = Path(raw_path)
    else:
        file_path = None
    if file_path and file_path.is_file():
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                specs.extend(data)
            else:
                logger.warning("PROXY_ROUTES_FILE must contain a JSON array")
        except (OSError, json.JSONDecodeError):
            logger.exception("could not read PROXY_ROUTES_FILE")
    raw = os.environ.get("PROXY_ROUTES_JSON", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                specs.extend(data)
            else:
                logger.warning("PROXY_ROUTES_JSON must be a JSON array")
        except json.JSONDecodeError:
            logger.exception("invalid PROXY_ROUTES_JSON")
    return specs


def _apply_external_route_definitions() -> None:
    for spec in _load_external_route_specs():
        try:
            body = ProxyCreate(**spec)
        except (ValidationError, TypeError):
            logger.warning("skipped invalid external route spec: %s", spec)
            continue
        try:
            db.insert_proxy(
                name=body.name.strip(),
                listen_port=body.listen_port,
                target_host=body.target_host,
                target_port=body.target_port,
                auto_start=body.auto_start,
            )
            logger.info(
                "imported route from file/env: %s :%s -> %s:%s",
                body.name,
                body.listen_port,
                body.target_host,
                body.target_port,
            )
        except sqlite3.IntegrityError:
            logger.info(
                "external route skipped (listen_port %s already defined)",
                body.listen_port,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    _apply_external_route_definitions()
    app.state.proxy_manager = ProxyManager()
    pm: ProxyManager = app.state.proxy_manager
    for proxy_id in db.list_auto_start_ids():
        row = db.get_proxy(proxy_id)
        if not row:
            continue
        try:
            await pm.start_proxy(
                proxy_id=row["id"],
                listen_port=int(row["listen_port"]),
                target_host=row["target_host"],
                target_port=int(row["target_port"]),
            )
            logger.info("auto-started proxy id=%s port=%s", row["id"], row["listen_port"])
        except Exception:
            logger.exception("failed to auto-start proxy id=%s", proxy_id)
    yield
    ids = list(pm.snapshot().keys())
    for pid in ids:
        await pm.remove_runtime(pid)


app = FastAPI(
    title="TCP Proxy Manager",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def get_pm(request: Request) -> ProxyManager:
    return request.app.state.proxy_manager


def _merge_status(rows: list[dict], pm: ProxyManager) -> list[dict]:
    snap = pm.snapshot()
    out = []
    for r in rows:
        d = dict(r)
        d["auto_start"] = bool(d.get("auto_start"))
        st = snap.get(int(d["id"]), {})
        d["running"] = st.get("running", False)
        d["active_connections"] = st.get("active_connections", 0)
        out.append(d)
    return out


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/setup/status")
def api_setup_status():
    return {
        "needs_setup": needs_first_run_setup(),
        "auth_via_env": auth_logic.env_auth_configured(),
    }


@app.post("/api/setup")
def api_setup(body: SetupBody):
    if not needs_first_run_setup():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Initial setup is already complete.",
        )
    if auth_logic.env_auth_configured():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="APP_USERNAME and APP_PASSWORD are set; stored setup is disabled.",
        )
    auth_logic.save_initial_credentials(body.username, body.password)
    return {"ok": True, "message": "Credentials saved. Sign in from the home page."}


@app.get("/")
async def page_index(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
):
    if needs_first_run_setup():
        return RedirectResponse("/setup", status_code=302)
    authenticate(credentials)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "show_env_auth_tip": not auth_logic.env_auth_configured(),
        },
    )


@app.get("/setup")
async def page_setup(request: Request):
    if not needs_first_run_setup():
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        "setup.html",
        {
            "request": request,
        },
    )


@app.get("/api/proxies")
def api_list_proxies(
    request: Request,
    _: str = Depends(require_login),
):
    pm = get_pm(request)
    rows = db.list_proxies()
    return _merge_status(rows, pm)


@app.post("/api/proxies", status_code=status.HTTP_201_CREATED)
async def api_create_proxy(
    request: Request,
    body: ProxyCreate,
    _: str = Depends(require_login),
):
    pm = get_pm(request)
    try:
        new_id = db.insert_proxy(
            name=body.name.strip(),
            listen_port=body.listen_port,
            target_host=body.target_host,
            target_port=body.target_port,
            auto_start=body.auto_start,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another route already uses this listen port.",
        )
    row = db.get_proxy(new_id)
    assert row is not None
    return _merge_status([row], pm)[0]


@app.post("/api/proxies/{proxy_id}/start")
async def api_start_proxy(
    request: Request,
    proxy_id: int,
    _: str = Depends(require_login),
):
    pm = get_pm(request)
    row = db.get_proxy(proxy_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown id")
    try:
        await pm.start_proxy(
            proxy_id=row["id"],
            listen_port=int(row["listen_port"]),
            target_host=row["target_host"],
            target_port=int(row["target_port"]),
        )
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot bind listen port: {e}",
        ) from e
    return _merge_status([row], pm)[0]


@app.post("/api/proxies/{proxy_id}/stop")
async def api_stop_proxy(
    request: Request,
    proxy_id: int,
    _: str = Depends(require_login),
):
    pm = get_pm(request)
    row = db.get_proxy(proxy_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown id")
    await pm.stop_proxy(proxy_id)
    return _merge_status([row], pm)[0]


@app.delete("/api/proxies/{proxy_id}")
async def api_delete_proxy(
    request: Request,
    proxy_id: int,
    _: str = Depends(require_login),
):
    pm = get_pm(request)
    row = db.get_proxy(proxy_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown id")
    await pm.remove_runtime(proxy_id)
    db.delete_proxy(proxy_id)
    return JSONResponse({"ok": True})
