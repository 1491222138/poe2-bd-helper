"""统一本地 HTTP + SSE 服务（127.0.0.1:8788）。

启动：
    python3 -m backend.api.server

路由按领域拆分在 backend/api/routes/：
    - builds.py：构筑解析、消融账本、天赋树、双码差距诊断、预算路线、抄作业方案、单次模拟
    - market.py：poe.ninja / Trade2 核价、价格快照、做装决策、词典元数据、扩展搜货指令
    - llm.py   ：LLM 配置、目标 BD 评分、同步与 SSE 流式分析
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import signal
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from backend.api import auth
from backend.api.routes import builds as builds_routes
from backend.api.routes import engine as engine_routes
from backend.api.routes import llm as llm_routes
from backend.api.routes import market as market_routes
from backend.config import (
    burst_idle_seconds,
    desktop_web_dist_dir,
    get_env,
    hardware_profile,
    host,
    idle_minutes,
    port,
    workers_max,
    workers_min,
)
from backend.engine import engine_manager, tree_index
from backend.engine.worker_pool import WorkerError, WorkerPool
from backend.market.price_store import StateStore

_MIME_OVERRIDES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".txt": "text/plain; charset=utf-8",
}


def _resolve_static_web_file(req_path: str, dist_dir: str | None = None) -> tuple[str, str] | None:
    """将非 API 的静态 GET 请求安全映射到 desktop/web/dist/ 下的真实文件。"""
    base_dir = os.path.abspath(dist_dir or desktop_web_dist_dir())
    index_file = os.path.join(base_dir, "index.html")
    if not os.path.isfile(index_file):
        return None

    decoded = urllib.parse.unquote(req_path or "/")
    if "\x00" in decoded:
        return None
    parts = [p for p in decoded.replace("\\", "/").split("/") if p]
    if any(p in (".", "..") for p in parts):
        return None

    if not parts or (len(parts) == 1 and parts[0] == "index.html"):
        target = index_file
    else:
        target = os.path.abspath(os.path.join(base_dir, *parts))
        if not target.startswith(base_dir + os.sep):
            return None
        if not os.path.isfile(target):
            return None

    ext = os.path.splitext(target)[1].lower()
    ctype = _MIME_OVERRIDES.get(ext) or mimetypes.guess_type(target)[0] or "application/octet-stream"
    return target, ctype

# 向后兼容别名（供既有测试或外部模块引用）
_sanitize_target_positioning = builds_routes.sanitize_target_positioning
_persist_ai_positioning_if_matching = builds_routes.persist_ai_positioning_if_matching

_GET_ROUTERS = (
    builds_routes.handle_get,
    engine_routes.handle_get,
    market_routes.handle_get,
    llm_routes.handle_get,
)
_POST_ROUTERS = (
    builds_routes.handle_post,
    engine_routes.handle_post,
    market_routes.handle_post,
    llm_routes.handle_post,
)


class EventBus:
    """轻量 SSE 事件总线：用于扩展 -> 桌面端的毫秒级推送通知。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list[queue.Queue[str]] = []

    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=32)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event_name: str, data: dict[str, Any]) -> None:
        payload = f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        with self._lock:
            dead: list[queue.Queue[str]] = []
            for q in self._subs:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subs.remove(q)


class WorkbenchRuntime:
    def __init__(self, state_dir: str | None = None) -> None:
        self.origins = auth.allowed_origins()
        self.token = auth.load_or_create_token(override=get_env("POE2_TOKEN") or None)
        self.store = StateStore(state_dir=state_dir) if state_dir else StateStore()
        self.bus = EventBus()
        self._pool: WorkerPool | None = None
        self._pool_lock = threading.Lock()
        self._inflight_lock = threading.Lock()
        self._inflight: dict[str, tuple[threading.Event, dict[str, Any]]] = {}

    def run_singleflight(self, key: str, fn: Any) -> Any:
        """按 key 合并同 BD 并发计算请求，防止重复打满 LuaJIT Worker 池。"""
        with self._inflight_lock:
            existing = self._inflight.get(key)
            if existing is None:
                ev = threading.Event()
                box: dict[str, Any] = {}
                self._inflight[key] = (ev, box)
                leader = True
            else:
                ev, box = existing
                leader = False
        if not leader:
            ev.wait()
            if "error" in box:
                raise box["error"]
            return box.get("result")
        try:
            res = fn()
            box["result"] = res
            return res
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
            raise
        finally:
            with self._inflight_lock:
                self._inflight.pop(key, None)
            ev.set()

    def pool(self) -> WorkerPool:
        with self._pool_lock:
            if self._pool is None:
                self._pool = WorkerPool(
                    size=workers_max(),
                    min_size=workers_min(),
                    idle_timeout=idle_minutes() * 60.0,
                    burst_idle_timeout=burst_idle_seconds(),
                )
                self._pool.start()
            return self._pool

    def shutdown(self) -> None:
        with self._pool_lock:
            if self._pool is not None:
                self._pool.close()
                self._pool = None


def make_handler(rt: WorkbenchRuntime) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PoE2Workbench/1.0"

        def _origin(self) -> str | None:
            return self.headers.get("Origin")

        def _write_cors(self) -> None:
            allow = auth.cors_origin(self._origin(), rt.origins)
            if allow:
                self.send_header("Access-Control-Allow-Origin", allow)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Auth-Token")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

        def _send(self, status: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self._write_cors()
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: dict[str, Any]) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _blocked_origin(self) -> bool:
            if auth.origin_allowed(self._origin(), rt.origins):
                return False
            self._json(403, {"ok": False, "error": "origin not allowed"})
            return True

        def _unauthorized(self) -> bool:
            if auth.token_ok(self.headers.get("X-Auth-Token"), rt.token):
                return False
            self._json(401, {"ok": False, "error": "缺少或错误的 X-Auth-Token"})
            return True

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

        def do_OPTIONS(self) -> None:
            if not auth.origin_allowed(self._origin(), rt.origins):
                self._send(403, b"")
                return
            self._send(204, b"")

        def do_GET(self) -> None:
            if self._blocked_origin():
                return
            path = self.path.split("?", 1)[0]
            if path == "/token":
                self._json(200, {"ok": True, "token": rt.token})
                return
            if path == "/health":
                p = rt._pool
                tgt = rt.store.get_target_build()
                prices = rt.store.get_prices()
                pending_trades = rt.store.get_trade_requests(pending_only=True)
                eng = engine_manager.detect_engine_status(state_dir=rt.store.state_dir)
                self._json(200, {
                    "ok": True,
                    "service": "poe2-workbench",
                    "engineStarted": p is not None,
                    "pob2Ready": eng["ready"],
                    "pob2Found": eng["pob2Found"],
                    "luajitFound": eng["luajitFound"],
                    "pob2Root": eng["activeRoot"],
                    "pob2Source": eng["source"],
                    "pob2Layout": eng["layout"],
                    "workersRunning": p.running_count if p else 0,
                    "workersMax": workers_max(),
                    "burstIdleSec": burst_idle_seconds(),
                    "hardware": hardware_profile(),
                    "bootMs": p.boot_ms if p else [],
                    "treeVersions": eng["treeVersions"] or tree_index.available_tree_versions(),
                    "targetBuildTitle": (tgt or {}).get("title") or "",
                    "pricesCount": prices.get("count", 0),
                    "pendingTradeRequests": len(pending_trades),
                    "webUiReady": os.path.isfile(os.path.join(desktop_web_dist_dir(), "index.html")),
                })
                return
            if path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self._write_cors()
                self.end_headers()
                q = rt.bus.subscribe()
                try:
                    self.wfile.write(b'event: connected\ndata: {"ok":true}\n\n')
                    self.wfile.flush()
                    while True:
                        try:
                            msg = q.get(timeout=20.0)
                            self.wfile.write(msg.encode("utf-8"))
                            self.wfile.flush()
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                except Exception:
                    pass
                finally:
                    rt.bus.unsubscribe(q)
                return

            for router in _GET_ROUTERS:
                res = router(rt, path, self.path)
                if res is not None:
                    self._json(res[0], res[1])
                    return

            static_hit = _resolve_static_web_file(path)
            if static_hit is not None:
                fpath, ctype = static_hit
                try:
                    with open(fpath, "rb") as f:
                        data = f.read()
                    self._send(200, data, ctype=ctype)
                    return
                except OSError:
                    pass

            self._json(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:
            if self._blocked_origin() or self._unauthorized():
                return
            try:
                body = self._read_json()
            except Exception as exc:
                self._json(400, {"ok": False, "error": f"bad json: {exc}"})
                return

            path = self.path.split("?", 1)[0]
            try:
                if path == "/llm/analyze/stream":
                    llm_routes.handle_stream(self, rt, body)
                    return
                if path == "/shutdown":
                    self._json(200, {"ok": True, "shuttingDown": True})
                    threading.Thread(
                        target=self.server.shutdown,
                        name="poe2-http-shutdown",
                        daemon=True,
                    ).start()
                    return

                for router in _POST_ROUTERS:
                    res = router(rt, path, body)
                    if res is not None:
                        self._json(res[0], res[1])
                        return

                self._json(404, {"ok": False, "error": "not found"})
            except WorkerError as exc:
                self._json(500, {"ok": False, "error": f"engine: {exc}"})
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    return Handler


class _WorkbenchHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_server() -> None:
    rt = WorkbenchRuntime()
    srv = _WorkbenchHTTPServer((host(), port()), make_handler(rt))

    def _graceful_signal_handler(signum: int, _frame: Any) -> None:
        # 在独立线程中调用 srv.shutdown()，避免在信号处理中死锁 serve_forever 主循环
        threading.Thread(target=srv.shutdown, name="poe2-shutdown", daemon=True).start()

    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _graceful_signal_handler)
            except Exception:
                pass

    print(f"PoE2 Workbench API listening on http://{host()}:{port()} (pid={os.getpid()})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        rt.shutdown()


if __name__ == "__main__":
    run_server()
