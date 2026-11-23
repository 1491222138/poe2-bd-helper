"""统一本地 HTTP + SSE 服务（127.0.0.1:8788）。

启动：
    python3 -m backend.api.server

Stage 1 端点：
    GET  /health          引擎与 worker 状态、可用天赋树版本
    GET  /token           白名单来源获取 X-Auth-Token
    GET  /events          SSE 实时推送（扩展推来新目标 BD 或新价格时通知桌面端）
    POST /builds/parse    解码并解析 PoB2 码（含完整天赋树 Keystone/升华/Notable）
    POST /builds/target   扩展推送目标 BD（并广播 SSE）
    GET  /builds/target   获取最近一次推送的目标 BD
    POST /prices          扩展推送市集价格快照（并广播 SSE）
    GET  /prices          获取最新价格快照
    POST /simulate        使用常驻 PoB2 引擎单次实算 45+ 项完整面板
"""
from __future__ import annotations

import json
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from backend.api import auth
from backend.config import get_env, host, idle_minutes, port, workers_max, workers_min
from backend.engine import pob_parser, runefix, tree_index
from backend.engine.worker_pool import WorkerError, WorkerPool
from backend.market.price_store import StateStore


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
    def __init__(self) -> None:
        self.origins = auth.allowed_origins()
        self.token = auth.load_or_create_token(override=get_env("POE2_TOKEN") or None)
        self.store = StateStore()
        self.bus = EventBus()
        self._pool: WorkerPool | None = None
        self._pool_lock = threading.Lock()

    def pool(self) -> WorkerPool:
        with self._pool_lock:
            if self._pool is None:
                self._pool = WorkerPool(
                    size=workers_max(),
                    min_size=workers_min(),
                    idle_timeout=idle_minutes() * 60.0,
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
                self._json(200, {
                    "ok": True,
                    "service": "poe2-workbench",
                    "engineStarted": p is not None,
                    "workersRunning": p.running_count if p else 0,
                    "bootMs": p.boot_ms if p else [],
                    "treeVersions": tree_index.available_tree_versions(),
                })
                return
            if path == "/prices":
                self._json(200, {"ok": True, **rt.store.get_prices()})
                return
            if path == "/builds/target":
                tgt = rt.store.get_target_build()
                self._json(200, {"ok": True, "target": tgt})
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
                    self.wfile.write(b"event: connected\ndata: {\"ok\":true}\n\n")
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
                if path == "/builds/parse":
                    raw = body.get("xml") or body.get("pobCode") or ""
                    parsed = pob_parser.parse_pob_code(raw)
                    self._json(200, {"ok": True, "build": parsed})
                elif path == "/builds/target":
                    saved = rt.store.save_target_build(body)
                    rt.bus.publish("target_updated", {"title": saved.get("title"), "pushedAt": saved.get("pushedAt")})
                    self._json(200, {"ok": True, "target": saved})
                elif path == "/prices":
                    saved = rt.store.save_prices(body)
                    rt.bus.publish("prices_updated", {"count": saved["count"], "updatedAt": saved["updatedAt"]})
                    self._json(200, {"ok": True, "prices": saved})
                elif path == "/simulate":
                    raw = body.get("xml") or body.get("pobCode") or ""
                    xml = pob_parser.decode_pob(raw)
                    xml, fixed = runefix.normalize_runes(xml)
                    res = rt.pool().simulate(
                        xml,
                        lock_dps_key=body.get("lockDpsKey"),
                        include_audit=bool(body.get("includeAudit", True)),
                    )
                    self._json(200, {"ok": True, "fixedRunes": fixed, "result": res})
                else:
                    self._json(404, {"ok": False, "error": "not found"})
            except WorkerError as exc:
                self._json(500, {"ok": False, "error": f"engine: {exc}"})
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    return Handler


def run_server() -> None:
    rt = WorkbenchRuntime()
    srv = ThreadingHTTPServer((host(), port()), make_handler(rt))
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
