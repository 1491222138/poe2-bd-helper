"""poe.ninja PoE2 通货、高阶/完美通货、精华与预兆实时汇率快照（本地 30 分钟 TTL 缓存 + 离线兜底）。"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Any

from backend.market.price_store import STATE_DIR

TTL_SECONDS = 30 * 60

# PoE2 常用做装预兆离线参考价（Divine）
DEFAULT_OMENS_DIVINE: dict[str, float] = {
    "omen of sinistral exaltation": 0.12,
    "omen of dextral exaltation": 0.12,
    "omen of greater exaltation": 0.18,
    "omen of homogenising exaltation": 0.45,
    "omen of sinistral annulment": 0.85,
    "omen of dextral annulment": 0.85,
    "omen of whittling": 1.6,
    "omen of sinistral erasure": 2.2,
    "omen of dextral erasure": 2.2,
    "omen of sinistral crystallisation": 0.35,
    "omen of dextral crystallisation": 0.35,
    "omen of abyssal echoes": 0.25,
}

# 离线/网络不可达时的合理参考汇率（避免做装计算因断网崩溃）
DEFAULT_RATES = {
    "exaltPerDivine": 100.0,
    "chaosPerDivine": 40.0,
    "currencyDivine": {
        "divine": 1.0,
        "exalted": 0.01,
        "greater_exalted": 0.06,
        "perfect_exalted": 0.45,
        "chaos": 0.025,
        "greater_chaos": 0.12,
        "perfect_chaos": 0.55,
        "annul": 0.08,
        "regal": 0.005,
        "greater_regal": 0.03,
        "perfect_regal": 0.2,
        "alchemy": 0.004,
        "transmutation": 0.001,
        "greater_transmutation": 0.008,
        "perfect_transmutation": 0.05,
        "augmentation": 0.001,
        "greater_augmentation": 0.008,
        "perfect_augmentation": 0.05,
        "fracturing": 1.8,
    },
    "essencesDivine": {},
    "omensDivine": dict(DEFAULT_OMENS_DIVINE),
}


def _safe_league(league: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (league or "Standard").strip()) or "Standard"


def _cache_path(league: str, state_dir: str = STATE_DIR) -> str:
    return os.path.join(state_dir, f"ninja_rates_{_safe_league(league)}.json")


def _norm_key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").lower().replace("'", "").replace("’", "")).strip()


def _http_get_json(url: str, timeout: float = 6.0) -> dict[str, Any] | None:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "PoE2Workbench/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _parse_exchange_payload(
    payload: dict[str, Any] | None,
    rates_state: dict[str, float | None],
) -> dict[str, float]:
    """解析 poe.ninja PoE2 exchange/current/overview，返回 norm_name -> divineValue。"""
    out: dict[str, float] = {}
    if not payload or not isinstance(payload.get("lines"), list):
        return out

    items_by_id: dict[str, dict[str, Any]] = {}
    raw_items = payload.get("items")
    if isinstance(raw_items, list):
        for it in raw_items:
            if isinstance(it, dict) and it.get("id"):
                items_by_id[str(it["id"])] = it
    elif isinstance(raw_items, dict):
        for k, v in raw_items.items():
            if isinstance(v, dict):
                items_by_id[str(k)] = v

    core = payload.get("core") or {}
    primary = str(core.get("primary") or "").lower()
    core_rates = core.get("rates") or {}

    if primary == "exalted" and core_rates.get("divine"):
        rates_state["exaltPerDivine"] = 1.0 / float(core_rates["divine"])
    elif primary == "divine" and core_rates.get("exalted"):
        rates_state["exaltPerDivine"] = float(core_rates["exalted"])
    elif primary == "chaos" and core_rates.get("divine") and core_rates.get("exalted"):
        rates_state["exaltPerDivine"] = float(core_rates["exalted"]) / float(core_rates["divine"])

    for line in payload["lines"]:
        if not isinstance(line, dict):
            continue
        meta = items_by_id.get(str(line.get("id") or "")) or {}
        name = str(line.get("name") or meta.get("name") or line.get("currencyTypeName") or line.get("id") or "")
        if not name:
            continue
        div_val: float | None = None
        ex_val: float | None = None

        if isinstance(line.get("divineValue"), (int, float)) and line["divineValue"] > 0:
            div_val = float(line["divineValue"])
        if isinstance(line.get("exaltedValue"), (int, float)) and line["exaltedValue"] > 0:
            ex_val = float(line["exaltedValue"])

        if div_val is None and isinstance(line.get("primaryValue"), (int, float)) and line["primaryValue"] > 0:
            pv = float(line["primaryValue"])
            if primary == "divine":
                div_val = pv
            elif primary == "exalted":
                ex_val = pv
                if rates_state.get("exaltPerDivine"):
                    div_val = pv / float(rates_state["exaltPerDivine"])
            elif primary == "chaos" and core_rates.get("divine"):
                div_val = pv * float(core_rates["divine"])

        nk = _norm_key(name)
        if nk == "divine orb" and ex_val and ex_val > 1:
            rates_state["exaltPerDivine"] = ex_val
        if div_val is None and ex_val and rates_state.get("exaltPerDivine"):
            div_val = ex_val / float(rates_state["exaltPerDivine"])

        if div_val is not None and div_val > 0:
            out[nk] = round(div_val, 6)

    return out


def fetch_ninja_rates(
    league: str = "Standard",
    *,
    force_refresh: bool = False,
    allow_network: bool = True,
    state_dir: str = STATE_DIR,
) -> dict[str, Any]:
    """拉取并缓存 poe.ninja PoE2 通货、高阶/完美通货、精华与预兆汇率。"""
    league_name = (league or "Standard").strip() or "Standard"
    cpath = _cache_path(league_name, state_dir=state_dir)

    if not force_refresh and os.path.isfile(cpath):
        try:
            with open(cpath, encoding="utf-8") as f:
                cached = json.load(f)
            if time.time() - (cached.get("updatedAt", 0) / 1000.0) < TTL_SECONDS:
                # 兼容旧缓存：补齐新增字段
                cached.setdefault("omensDivine", dict(DEFAULT_OMENS_DIVINE))
                cur = cached.setdefault("currencyDivine", {})
                for k, v in DEFAULT_RATES["currencyDivine"].items():
                    cur.setdefault(k, v)
                return cached
        except Exception:
            pass

    if not allow_network:
        return {
            "league": league_name,
            "source": "fallback",
            "updatedAt": int(time.time() * 1000),
            "exaltPerDivine": DEFAULT_RATES["exaltPerDivine"],
            "chaosPerDivine": DEFAULT_RATES["chaosPerDivine"],
            "currencyDivine": dict(DEFAULT_RATES["currencyDivine"]),
            "essencesDivine": {},
            "omensDivine": dict(DEFAULT_OMENS_DIVINE),
        }

    enc = urllib.parse.quote(league_name)
    rates_state: dict[str, float | None] = {"exaltPerDivine": None}
    cur_payload = _http_get_json(
        f"https://poe.ninja/poe2/api/economy/exchange/current/overview?league={enc}&type=Currency"
    )
    cur_map = _parse_exchange_payload(cur_payload, rates_state)

    ess_payload = _http_get_json(
        f"https://poe.ninja/poe2/api/economy/exchange/current/overview?league={enc}&type=Essences"
    )
    ess_map = _parse_exchange_payload(ess_payload, rates_state)

    omen_payload = _http_get_json(
        f"https://poe.ninja/poe2/api/economy/exchange/current/overview?league={enc}&type=Ritual"
    )
    omen_map = _parse_exchange_payload(omen_payload, rates_state)
    if not omen_map:
        omen_payload2 = _http_get_json(
            f"https://poe.ninja/poe2/api/economy/exchange/current/overview?league={enc}&type=Omens"
        )
        omen_map = _parse_exchange_payload(omen_payload2, rates_state)

    if not cur_map and not ess_map and not omen_map:
        return {
            "league": league_name,
            "source": "fallback",
            "updatedAt": int(time.time() * 1000),
            "exaltPerDivine": DEFAULT_RATES["exaltPerDivine"],
            "chaosPerDivine": DEFAULT_RATES["chaosPerDivine"],
            "currencyDivine": dict(DEFAULT_RATES["currencyDivine"]),
            "essencesDivine": {},
            "omensDivine": dict(DEFAULT_OMENS_DIVINE),
        }

    ex_per_d = float(rates_state.get("exaltPerDivine") or 100.0)
    exalted_d = cur_map.get("exalted orb") or (1.0 / ex_per_d)
    chaos_d = cur_map.get("chaos orb") or (exalted_d * 2.5)
    chaos_per_d = round(1.0 / chaos_d, 1) if chaos_d > 0 else 40.0

    currency_divine = {
        "divine": 1.0,
        "exalted": round(exalted_d, 6),
        "greater_exalted": round(cur_map.get("greater exalted orb") or exalted_d * 6.0, 6),
        "perfect_exalted": round(cur_map.get("perfect exalted orb") or max(0.35, exalted_d * 40.0), 6),
        "chaos": round(chaos_d, 6),
        "greater_chaos": round(cur_map.get("greater chaos orb") or chaos_d * 4.5, 6),
        "perfect_chaos": round(cur_map.get("perfect chaos orb") or max(0.4, chaos_d * 20.0), 6),
        "annul": round(cur_map.get("orb of annulment") or exalted_d * 8.0, 6),
        "regal": round(cur_map.get("regal orb") or exalted_d * 0.5, 6),
        "greater_regal": round(cur_map.get("greater regal orb") or exalted_d * 3.0, 6),
        "perfect_regal": round(cur_map.get("perfect regal orb") or max(0.15, exalted_d * 18.0), 6),
        "alchemy": round(cur_map.get("orb of alchemy") or exalted_d * 0.4, 6),
        "transmutation": round(cur_map.get("orb of transmutation") or exalted_d * 0.1, 6),
        "greater_transmutation": round(cur_map.get("greater orb of transmutation") or exalted_d * 0.8, 6),
        "perfect_transmutation": round(cur_map.get("perfect orb of transmutation") or exalted_d * 5.0, 6),
        "augmentation": round(cur_map.get("orb of augmentation") or exalted_d * 0.1, 6),
        "greater_augmentation": round(cur_map.get("greater orb of augmentation") or exalted_d * 0.8, 6),
        "perfect_augmentation": round(cur_map.get("perfect orb of augmentation") or exalted_d * 5.0, 6),
        "fracturing": round(cur_map.get("fracturing orb") or 1.8, 6),
    }

    merged_omens = dict(DEFAULT_OMENS_DIVINE)
    for k, v in omen_map.items():
        if v > 0:
            merged_omens[k] = round(v, 6)

    snap = {
        "league": league_name,
        "source": "poe.ninja",
        "updatedAt": int(time.time() * 1000),
        "exaltPerDivine": round(ex_per_d, 1),
        "chaosPerDivine": chaos_per_d,
        "currencyDivine": currency_divine,
        "essencesDivine": ess_map,
        "omensDivine": merged_omens,
    }

    try:
        os.makedirs(state_dir, exist_ok=True)
        tmp = cpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        os.replace(tmp, cpath)
    except Exception:
        pass

    return snap


def lookup_currency_price(key: str, rates: dict[str, Any]) -> float:
    """按通货键名查询单价（Divine），带默认兜底。"""
    cur = rates.get("currencyDivine") or {}
    val = cur.get(key)
    if isinstance(val, (int, float)) and val > 0:
        return float(val)
    return float(DEFAULT_RATES["currencyDivine"].get(key, 0.01))


def lookup_omen_price(omen_name: str, rates: dict[str, Any]) -> float:
    """按预兆英文名查询单价（Divine），缺失时使用 PoE2 参考价兜底。"""
    nk = _norm_key(omen_name)
    omen_map = rates.get("omensDivine") or {}
    if nk in omen_map and omen_map[nk] > 0:
        return float(omen_map[nk])
    if nk in DEFAULT_OMENS_DIVINE:
        return float(DEFAULT_OMENS_DIVINE[nk])
    return 0.5


def lookup_essence_price(essence_name: str, rates: dict[str, Any]) -> float:
    """按精华名查询单价（Divine），缺失时按等级给出合理保守估算。"""
    nk = _norm_key(essence_name)
    ess_map = rates.get("essencesDivine") or {}
    if nk in ess_map and ess_map[nk] > 0:
        return float(ess_map[nk])
    ex_d = lookup_currency_price("exalted", rates)
    if nk.startswith("perfect"):
        return round(ex_d * 40, 4)
    if nk.startswith("greater"):
        return round(ex_d * 15, 4)
    if nk.startswith("lesser"):
        return round(ex_d * 1.5, 4)
    return round(ex_d * 5, 4)
