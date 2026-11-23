"""保存扩展推送的市集价格快照与当前目标 BD 交接状态。"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Optional

STATE_DIR = os.path.join(os.path.expanduser("~"), ".poe2-workbench")
PRICES_PATH = os.path.join(STATE_DIR, "prices.json")
TARGET_BUILD_PATH = os.path.join(STATE_DIR, "target_build.json")


class StateStore:
    def __init__(self, state_dir: str = STATE_DIR):
        self.state_dir = state_dir
        self.prices_path = os.path.join(state_dir, "prices.json")
        self.target_path = os.path.join(state_dir, "target_build.json")
        self._lock = threading.Lock()
        self._prices: dict[str, Any] = self._read_json(self.prices_path) or {}
        self._target: Optional[dict[str, Any]] = self._read_json(self.target_path)

    @staticmethod
    def _read_json(path: str) -> Any:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _write_json(self, path: str, data: Any) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def save_prices(self, payload: dict[str, Any]) -> dict[str, Any]:
        prices = payload.get("prices") or {}
        entries = payload.get("entries") or []
        snap = {
            "league": payload.get("league") or "Standard",
            "source": payload.get("source") or "extension",
            "updatedAt": int(payload.get("updatedAt") or time.time() * 1000),
            "prices": {str(k): float(v) for k, v in prices.items() if isinstance(v, (int, float))},
            "entries": entries,
            "count": len(prices) or len(entries),
        }
        with self._lock:
            self._prices = snap
            self._write_json(self.prices_path, snap)
        return snap

    def get_prices(self) -> dict[str, Any]:
        with self._lock:
            if not self._prices:
                return {"prices": {}, "entries": [], "count": 0, "empty": True}
            return dict(self._prices)

    def save_target_build(self, payload: dict[str, Any]) -> dict[str, Any]:
        snap = {
            "pobCode": payload.get("pobCode") or "",
            "sourceUrl": payload.get("sourceUrl") or "",
            "title": payload.get("title") or "",
            "league": payload.get("league") or "",
            "positioning": payload.get("positioning"),
            "pushedAt": int(payload.get("pushedAt") or time.time() * 1000),
        }
        with self._lock:
            self._target = snap
            self._write_json(self.target_path, snap)
        return snap

    def get_target_build(self) -> Optional[dict[str, Any]]:
        with self._lock:
            return dict(self._target) if self._target else None
