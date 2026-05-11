"""Optional env-based credentials or one-time stored admin hash."""

from __future__ import annotations

import os
import secrets

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app import database as db

security = HTTPBasic(auto_error=False)

SETUP_ADMIN_USER = "admin_user"
SETUP_ADMIN_HASH = "admin_pass_hash"
SETUP_DONE = "setup_done"


def env_auth_configured() -> bool:
    u = os.environ.get("APP_USERNAME", "").strip()
    p = os.environ.get("APP_PASSWORD", "")
    return bool(u and p)


def stored_setup_complete() -> bool:
    return db.get_setting(SETUP_DONE) == "1"


def needs_first_run_setup() -> bool:
    if env_auth_configured():
        return False
    return not stored_setup_complete()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain.encode("utf-8"),
            hashed.encode("utf-8"),
        )
    except ValueError:
        return False


def hash_password(plain: str) -> str:
    digest = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12))
    return digest.decode("utf-8")


def save_initial_credentials(username: str, password: str) -> None:
    db.set_setting(SETUP_ADMIN_USER, username.strip())
    db.set_setting(SETUP_ADMIN_HASH, hash_password(password))
    db.set_setting(SETUP_DONE, "1")


def check_stored_credentials(username: str, password: str) -> bool:
    u = db.get_setting(SETUP_ADMIN_USER)
    h = db.get_setting(SETUP_ADMIN_HASH)
    if not u or not h:
        return False
    return secrets.compare_digest(username, u) and verify_password(password, h)


def check_env_credentials(username: str, password: str) -> bool:
    eu = os.environ.get("APP_USERNAME", "").strip()
    ep = os.environ.get("APP_PASSWORD", "")
    if not eu:
        return False
    return secrets.compare_digest(username, eu) and secrets.compare_digest(
        password, ep
    )


def authenticate(credentials: HTTPBasicCredentials | None) -> str:
    if needs_first_run_setup():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Initial setup required. Open /setup in your browser.",
        )
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": 'Basic realm="Proxy Manager"'},
        )
    if env_auth_configured():
        ok = check_env_credentials(credentials.username, credentials.password)
    else:
        ok = check_stored_credentials(credentials.username, credentials.password)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="Proxy Manager"'},
        )
    return credentials.username


async def require_login(
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    return authenticate(credentials)
