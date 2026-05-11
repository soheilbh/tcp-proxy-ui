"""SQLite persistence for proxy definitions and optional first-run credentials."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def _db_path() -> Path:
    base = Path(os.environ.get("DATA_DIR", "/data"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "proxy.db"


@contextmanager
def get_conn():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                listen_port INTEGER NOT NULL UNIQUE,
                target_host TEXT NOT NULL,
                target_port INTEGER NOT NULL,
                auto_start INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )


def get_setting(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def list_proxies() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, listen_port, target_host, target_port, auto_start FROM proxies ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def get_proxy(proxy_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, name, listen_port, target_host, target_port, auto_start FROM proxies WHERE id = ?",
            (proxy_id,),
        ).fetchone()
        return dict(row) if row else None


def insert_proxy(
    name: str, listen_port: int, target_host: str, target_port: int, auto_start: bool
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO proxies (name, listen_port, target_host, target_port, auto_start)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, listen_port, target_host, target_port, 1 if auto_start else 0),
        )
        return int(cur.lastrowid)


def update_proxy_auto_start(proxy_id: int, auto_start: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE proxies SET auto_start = ? WHERE id = ?",
            (1 if auto_start else 0, proxy_id),
        )


def delete_proxy(proxy_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM proxies WHERE id = ?", (proxy_id,))
        return cur.rowcount > 0


def list_auto_start_ids() -> list[int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM proxies WHERE auto_start = 1"
        ).fetchall()
        return [int(r["id"]) for r in rows]
