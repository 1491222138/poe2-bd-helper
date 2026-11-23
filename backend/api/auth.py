"""本地服务的 Origin 白名单与 X-Auth-Token 鉴权。"""
from __future__ import annotations

import hmac
import os
import secrets

from backend.config import allowed_origins_raw

DEFAULT_ORIGINS = (
    "http://127.0.0.1:8788",
    "http://localhost:8788",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "tauri://localhost",
    "http://tauri.localhost",
)

TOKEN_DIR = os.path.join(os.path.expanduser("~"), ".poe2-workbench")
TOKEN_FILE = os.path.join(TOKEN_DIR, "token")


def allowed_origins(extra: str | None = None) -> set[str]:
    out: set[str] = set(DEFAULT_ORIGINS)
    raw = extra if extra is not None else allowed_origins_raw()
    for item in raw.split(","):
        item = item.strip()
        if item:
            out.add(item.rstrip("/"))
    return out


def _matches(origin: str, origins: set[str]) -> bool:
    o = origin.rstrip("/")
    if o in origins:
        return True
    for pat in origins:
        if pat.endswith("*") and pat.startswith(("chrome-extension://", "extension://")) and o.startswith(pat[:-1]):
            return True
    return False


def origin_allowed(origin: str | None, origins: set[str]) -> bool:
    if not origin:
        return True
    return _matches(origin, origins)


def cors_origin(origin: str | None, origins: set[str]) -> str | None:
    if origin and _matches(origin, origins):
        return origin
    return None


def load_or_create_token(path: str = TOKEN_FILE, override: str | None = None) -> str:
    if override:
        return override
    try:
        with open(path, encoding="utf-8") as f:
            tok = f.read().strip()
        if tok:
            return tok
    except FileNotFoundError:
        pass
    tok = secrets.token_urlsafe(24)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(tok)
    return tok


def token_ok(header_value: str | None, token: str) -> bool:
    if not header_value or not token:
        return False
    return hmac.compare_digest(header_value.strip(), token)
