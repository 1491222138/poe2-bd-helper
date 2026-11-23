"""统一配置加载：优先读取环境变量，其次读取仓库根目录的 .env.local。

不依赖第三方库，保证在全新 Python 3 环境下开箱即用。
"""
from __future__ import annotations

import os
from functools import lru_cache

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_LOCAL_PATH = os.path.join(REPO_ROOT, ".env.local")


@lru_cache(maxsize=1)
def _load_dotenv_file(path: str = ENV_LOCAL_PATH) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.isfile(path):
        return values
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                values[k] = v
    return values


def get_env(key: str, default: str = "") -> str:
    if key in os.environ and os.environ[key] != "":
        return os.environ[key]
    return _load_dotenv_file().get(key, default)


def pob2_root() -> str:
    configured = get_env("POB2_ROOT")
    if configured:
        return os.path.expanduser(configured)
    return os.path.join(REPO_ROOT, "pob2-engine", "PathOfBuilding-PoE2")


def pob2_src() -> str:
    return os.path.join(pob2_root(), "src")


def host() -> str:
    return get_env("POE2_HOST", "127.0.0.1")


def port() -> int:
    return int(get_env("POE2_PORT", "8788"))


def workers_max() -> int:
    return max(1, int(get_env("POE2_WORKERS", "3")))


def workers_min() -> int:
    return max(0, int(get_env("POE2_WORKERS_MIN", "1")))


def idle_minutes() -> float:
    return max(0.0, float(get_env("POE2_IDLE_MIN", "30")))


def allowed_origins_raw() -> str:
    return get_env("POE2_ALLOWED_ORIGINS", "chrome-extension://*")
