"""PoB2 计算内核自检、本地目录绑定、GitHub 一键下载与冒烟校验路由。"""
from __future__ import annotations

from typing import Any, Optional

from backend.engine import engine_manager


def handle_get(rt: Any, path: str, _raw_url: str) -> Optional[tuple[int, dict[str, Any]]]:
    if path == "/engine/status":
        st = engine_manager.detect_engine_status(state_dir=rt.store.state_dir)
        return 200, {"ok": True, "engine": st}
    return None


def handle_post(rt: Any, path: str, body: dict[str, Any]) -> Optional[tuple[int, dict[str, Any]]]:
    if path == "/engine/detect":
        st = engine_manager.detect_engine_status(state_dir=rt.store.state_dir)
        return 200, {"ok": True, "engine": st}

    if path == "/engine/use-path":
        raw_path = str(body.get("path") or "").strip()
        if not raw_path:
            raise ValueError("请提供本机 PoB2 目录路径")
        st = engine_manager.use_local_pob2(
            raw_path,
            state_dir=rt.store.state_dir,
            runtime=rt,
        )
        return 200, {"ok": True, "engine": st}

    if path == "/engine/install":
        ref = str(body.get("ref") or "").strip() or None
        mirror_prefix = str(body.get("mirrorPrefix") or "").strip()
        install_state = engine_manager.start_async_install(
            ref=ref,
            mirror_prefix=mirror_prefix,
            state_dir=rt.store.state_dir,
            runtime=rt,
        )
        return 200, {"ok": True, "installState": install_state}

    if path == "/engine/install-luajit":
        res = engine_manager.install_luajit(
            state_dir=rt.store.state_dir,
            runtime=rt,
        )
        return 200, {"ok": True, **res}

    if path == "/engine/test":
        res = engine_manager.test_live_engine()
        return 200, {"ok": True, "test": res}

    return None
