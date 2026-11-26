"""保存扩展推送的市集价格快照与当前目标 BD 交接状态。"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from typing import Any, Optional

STATE_DIR = os.path.join(os.path.expanduser("~"), ".poe2-workbench")
PRICES_PATH = os.path.join(STATE_DIR, "prices.json")
TARGET_BUILD_PATH = os.path.join(STATE_DIR, "target_build.json")
TRADE_REQUESTS_PATH = os.path.join(STATE_DIR, "trade_requests.json")
COPY_PLANS_PATH = os.path.join(STATE_DIR, "copy_plans.json")
MAX_SAVED_COPY_PLANS = 40


def pob_fingerprint(pob_code: str) -> str:
    s = (pob_code or "").strip()
    if not s:
        return ""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


class StateStore:
    def __init__(self, state_dir: str = STATE_DIR):
        self.state_dir = state_dir
        self.prices_path = os.path.join(state_dir, "prices.json")
        self.target_path = os.path.join(state_dir, "target_build.json")
        self.my_build_path = os.path.join(state_dir, "my_build.json")
        self.trade_requests_path = os.path.join(state_dir, "trade_requests.json")
        self.copy_plans_path = os.path.join(state_dir, "copy_plans.json")
        self._lock = threading.Lock()
        self._prices: dict[str, Any] = self._read_json(self.prices_path) or {}
        self._target: Optional[dict[str, Any]] = self._read_json(self.target_path)
        self._my_build: Optional[dict[str, Any]] = self._read_json(self.my_build_path)
        self._trade_requests: list[dict[str, Any]] = self._read_json(self.trade_requests_path) or []
        raw_plans = self._read_json(self.copy_plans_path)
        self._copy_plans: list[dict[str, Any]] = raw_plans if isinstance(raw_plans, list) else []

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

    def save_my_build(self, payload: dict[str, Any]) -> dict[str, Any]:
        snap = {
            "pobCode": payload.get("pobCode") or "",
            "title": payload.get("title") or "",
            "league": payload.get("league") or "",
            "updatedAt": int(payload.get("updatedAt") or time.time() * 1000),
        }
        with self._lock:
            self._my_build = snap
            self._write_json(self.my_build_path, snap)
        return snap

    def get_my_build(self) -> Optional[dict[str, Any]]:
        with self._lock:
            return dict(self._my_build) if self._my_build else None

    def add_trade_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = {
            "id": str(payload.get("id") or f"tr_{secrets.token_hex(6)}"),
            "league": str(payload.get("league") or "Standard"),
            "slot": str(payload.get("slot") or ""),
            "kind": str(payload.get("kind") or "exact"),
            "itemName": str(payload.get("itemName") or ""),
            "label": str(payload.get("label") or ""),
            "tradeUrl": str(payload.get("tradeUrl") or ""),
            "query": payload.get("query") if isinstance(payload.get("query"), dict) else None,
            "createdAt": int(payload.get("createdAt") or time.time() * 1000),
            "acked": False,
        }
        with self._lock:
            self._trade_requests.insert(0, req)
            self._trade_requests = self._trade_requests[:20]
            self._write_json(self.trade_requests_path, self._trade_requests)
        return req

    def get_trade_requests(self, pending_only: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            if pending_only:
                return [dict(r) for r in self._trade_requests if not r.get("acked")]
            return [dict(r) for r in self._trade_requests]

    def ack_trade_request(self, req_id: str) -> bool:
        found = False
        with self._lock:
            for r in self._trade_requests:
                if r.get("id") == req_id:
                    r["acked"] = True
                    found = True
            if found:
                self._write_json(self.trade_requests_path, self._trade_requests)
        return found

    def list_copy_plans(
        self,
        pob_hash: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            out: list[dict[str, Any]] = []
            for p in self._copy_plans:
                if not isinstance(p, dict):
                    continue
                if pob_hash and str(p.get("pobHash") or "") != pob_hash:
                    continue
                if mode and str(p.get("mode") or "") != mode:
                    continue
                out.append(dict(p))
            out.sort(key=lambda x: int(x.get("updatedAt") or x.get("createdAt") or 0), reverse=True)
            return out

    def save_copy_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = int(time.time() * 1000)
        plan_id = str(payload.get("id") or "").strip() or f"cp_{secrets.token_hex(6)}"
        mode = str(payload.get("mode") or "copy").strip()
        if mode not in ("copy", "consult", "target", "gap"):
            mode = "copy"
        pob_hash = str(payload.get("pobHash") or "").strip()
        if not pob_hash and payload.get("pobCode"):
            pob_hash = pob_fingerprint(str(payload.get("pobCode") or ""))
        copy_ctx = payload.get("copyContext") if isinstance(payload.get("copyContext"), dict) else None
        copy_plan = payload.get("copyPlan") if isinstance(payload.get("copyPlan"), dict) else None

        with self._lock:
            existing_idx = next((i for i, p in enumerate(self._copy_plans) if p.get("id") == plan_id), -1)
            existing = self._copy_plans[existing_idx] if existing_idx >= 0 else None

            raw_item_states = payload.get("itemStates")
            if isinstance(raw_item_states, dict):
                item_states = dict(raw_item_states)
            elif existing and isinstance(existing.get("itemStates"), dict):
                item_states = dict(existing["itemStates"])
            else:
                item_states = {}

            record: dict[str, Any] = {
                "id": plan_id,
                "mode": mode,
                "pobHash": pob_hash,
                "buildTitle": str(payload.get("buildTitle") or (existing or {}).get("buildTitle") or "").strip(),
                "league": str(payload.get("league") or (existing or {}).get("league") or "Standard").strip(),
                "model": str(payload.get("model") or (existing or {}).get("model") or "").strip(),
                "copyContext": copy_ctx if copy_ctx is not None else (existing or {}).get("copyContext"),
                "text": str(payload.get("text") if payload.get("text") is not None else (existing or {}).get("text") or ""),
                "reasoning": str(payload.get("reasoning") if payload.get("reasoning") is not None else (existing or {}).get("reasoning") or ""),
                "copyPlan": copy_plan if copy_plan is not None else (existing or {}).get("copyPlan"),
                "itemStates": item_states,
                "createdAt": int(payload.get("createdAt") or (existing or {}).get("createdAt") or now),
                "updatedAt": int(payload.get("updatedAt") or now),
            }

            if existing_idx >= 0:
                self._copy_plans.pop(existing_idx)
            self._copy_plans.insert(0, record)
            self._copy_plans.sort(key=lambda x: int(x.get("updatedAt") or x.get("createdAt") or 0), reverse=True)
            self._copy_plans = self._copy_plans[:MAX_SAVED_COPY_PLANS]
            self._write_json(self.copy_plans_path, self._copy_plans)
            return dict(record)

    def update_copy_plan_item_state(
        self,
        plan_id: str,
        item_key: str,
        patch: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        if not plan_id or not item_key or not isinstance(patch, dict):
            return None
        with self._lock:
            target = next((p for p in self._copy_plans if p.get("id") == plan_id), None)
            if target is None:
                return None
            states = target.get("itemStates")
            if not isinstance(states, dict):
                states = {}
                target["itemStates"] = states
            prev = states.get(item_key)
            merged = dict(prev) if isinstance(prev, dict) else {}
            for k, v in patch.items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v
            states[item_key] = merged
            target["updatedAt"] = int(time.time() * 1000)
            self._write_json(self.copy_plans_path, self._copy_plans)
            return dict(target)

    def delete_copy_plan(self, plan_id: str) -> bool:
        if not plan_id:
            return False
        with self._lock:
            before = len(self._copy_plans)
            self._copy_plans = [p for p in self._copy_plans if p.get("id") != plan_id]
            deleted = len(self._copy_plans) < before
            if deleted:
                self._write_json(self.copy_plans_path, self._copy_plans)
            return deleted
