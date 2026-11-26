"""PoE2 实时物价核价引擎（poe.ninja 全品类暗金/珠宝/宝石/符文实时价 + 官方 Trade2 防 429 实价查询）。

核心职责：
  1. fetch_active_leagues：拉取当前活跃赛季列表（优先主交易赛季，避免误落到 0 挂单的 Standard）；
  2. fetch_ninja_item_index：并发拉取 poe.ninja PoE2 全暗金装备、暗金珠宝、药剂/护符、血脉辅助宝石、符文/灵核/通货实时价（30 分钟本地缓存）；
  3. price_single_item_trade2 / price_build_trade2_batch：直接调用官方 Trade2 搜索与拉取接口，带全局速率限制器、429 冷却熔断锁与防低价钓鱼（剔除首条离群低价后取前段中位数）；
  4. 自动换算所有通货单位（Divine / Exalted / Chaos / Mirror / Annul / Regal 等）为神圣石 (D)。
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from backend.domain import affix_analyzer, trade_url
from backend.market import ninja_rates
from backend.market.price_store import STATE_DIR, StateStore

TTL_SECONDS = 30 * 60
LEAGUES_TTL_SECONDS = 60 * 60

_EXCHANGE_TYPES = (
    "Currency",
    "SoulCores",
    "Runes",
    "Omens",
    "UncutGems",
    "LineageSupportGems",
    "Fragments",
    "Talismans",
    "Essences",
)
_STASH_TYPES = (
    "UniqueArmours",
    "UniqueWeapons",
    "UniqueAccessories",
    "UniqueJewels",
    "UniqueFlasks",
    "UniqueCharms",
    "UniqueRelics",
)

_UA = "PoE2Workbench/1.0 (local personal build analyzer)"


class TradeRateLimiter:
    """进程级官方 Trade2 API 速率限制器与 429 全局冷却锁。"""

    def __init__(self, min_interval: float = 1.6, cooldown_sec: float = 60.0) -> None:
        self.min_interval = max(0.0, float(min_interval))
        self.cooldown_sec = max(1.0, float(cooldown_sec))
        self._lock = threading.Lock()
        self._last_request_at = 0.0
        self._cooldown_until = 0.0

    def remaining_cooldown(self) -> float:
        with self._lock:
            rem = self._cooldown_until - time.monotonic()
            return max(0.0, rem)

    def wait_turn(self) -> float:
        """若处于 429 冷却期则返回剩余秒数；否则等待至满足最小请求间隔后返回 0.0。"""
        with self._lock:
            now = time.monotonic()
            if now < self._cooldown_until:
                return self._cooldown_until - now
            wait = self.min_interval - (now - self._last_request_at)
            if wait > 0 and self._last_request_at > 0:
                time.sleep(wait)
                now = time.monotonic()
                if now < self._cooldown_until:
                    return self._cooldown_until - now
            self._last_request_at = time.monotonic()
            return 0.0

    def note_rate_limited(self, retry_after: float | None = None) -> float:
        sec = self.cooldown_sec
        if retry_after is not None and retry_after > 0:
            sec = max(sec, float(retry_after))
        with self._lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + sec)
            return max(0.0, self._cooldown_until - time.monotonic())

    def reset(self) -> None:
        with self._lock:
            self._last_request_at = 0.0
            self._cooldown_until = 0.0


TRADE_RATE_LIMITER = TradeRateLimiter(min_interval=1.6, cooldown_sec=60.0)


def _safe_league(league: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (league or "Standard").strip()) or "Standard"


def norm_price_key(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").lower().replace("'", "").replace("’", "").replace("`", "")).strip()


def _atomic_write_json(path: str, payload: Any, *, indent: int | None = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=indent)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _parse_retry_after(headers: Any) -> float | None:
    if not headers:
        return None
    raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    if raw is None:
        return None
    try:
        val = float(str(raw).strip())
        return val if val > 0 else None
    except ValueError:
        return None


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 8.0,
) -> tuple[int, dict[str, Any] | None, str]:
    st, data, err, _ = _http_json_ex(url, method=method, body=body, timeout=timeout)
    return st, data, err


def _http_json_ex(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 8.0,
) -> tuple[int, dict[str, Any] | None, str, float | None]:
    headers = {
        "Accept": "application/json",
        "User-Agent": _UA,
    }
    data_bytes = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data_bytes = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw), "", _parse_retry_after(resp.headers)
    except urllib.error.HTTPError as exc:
        err_txt = ""
        try:
            err_txt = exc.read().decode("utf-8", errors="ignore")[:220]
        except Exception:
            pass
        retry_after = _parse_retry_after(exc.headers)
        return exc.code, None, err_txt or f"HTTP {exc.code}", retry_after
    except Exception as exc:  # noqa: BLE001
        return 0, None, str(exc), None


def _trade_http_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any] | None, str, float]:
    """受全局 TradeRateLimiter 保护的官方 Trade2 HTTP 请求，返回 (status, data, err, cooldown_rem)。"""
    rem = TRADE_RATE_LIMITER.wait_turn()
    if rem > 0:
        return 429, None, f"官方市集处于 429 冷却保护期（剩余约 {int(round(rem))} 秒）", rem
    st, data, err, retry_after = _http_json_ex(url, method=method, body=body, timeout=timeout)
    if st == 429:
        locked = TRADE_RATE_LIMITER.note_rate_limited(retry_after)
        return 429, None, err or "HTTP 429", locked
    return st, data, err, 0.0


def fetch_active_leagues(
    *,
    force_refresh: bool = False,
    allow_network: bool = True,
    state_dir: str = STATE_DIR,
) -> list[dict[str, str]]:
    """拉取当前活跃 PoE2 赛季（主软核交易赛季排第一，Standard 次之，HC/SSF 靠后）。"""
    cpath = os.path.join(state_dir, "ninja_leagues.json")
    if not force_refresh and os.path.isfile(cpath):
        try:
            with open(cpath, encoding="utf-8") as f:
                cached = json.load(f)
            if (
                isinstance(cached, dict)
                and time.time() - (cached.get("updatedAt", 0) / 1000.0) < LEAGUES_TTL_SECONDS
                and isinstance(cached.get("leagues"), list)
                and cached["leagues"]
            ):
                return cached["leagues"]
        except Exception:
            pass

    leagues: list[dict[str, str]] = []
    if allow_network:
        status, data, _ = _http_json("https://poe.ninja/poe2/api/data/index-state", timeout=6.0)
        if status == 200 and isinstance(data, dict):
            for l in data.get("economyLeagues") or []:
                if isinstance(l, dict) and l.get("name"):
                    leagues.append({
                        "name": str(l["name"]),
                        "displayName": str(l.get("displayName") or l["name"]),
                        "url": str(l.get("url") or ""),
                    })

    if not leagues:
        leagues.append({"name": "Standard", "displayName": "Standard", "url": "standard"})

    def _rank(x: dict[str, str]) -> int:
        n = x["name"].lower()
        if "ssf" in n or "hc" in n or "hardcore" in n:
            return 3
        if n == "standard":
            return 2
        return 1

    leagues.sort(key=_rank)

    if allow_network and len(leagues) > 1:
        try:
            _atomic_write_json(
                cpath,
                {"updatedAt": int(time.time() * 1000), "leagues": leagues},
                indent=2,
            )
        except Exception:
            pass

    return leagues


def default_trade_league(*, allow_network: bool = True, state_dir: str = STATE_DIR) -> str:
    leagues = fetch_active_leagues(allow_network=allow_network, state_dir=state_dir)
    return leagues[0]["name"] if leagues else "Standard"


def _ingest_payload(
    payload: dict[str, Any] | None,
    type_name: str,
    by_name: dict[str, dict[str, Any]],
    rates_state: dict[str, float | None],
) -> int:
    if not payload or not isinstance(payload.get("lines"), list):
        return 0

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

    if primary == "divine":
        if core_rates.get("exalted"):
            rates_state["exaltPerDivine"] = float(core_rates["exalted"])
        if core_rates.get("chaos"):
            rates_state["chaosPerDivine"] = float(core_rates["chaos"])
    elif primary == "exalted" and core_rates.get("divine"):
        rates_state["exaltPerDivine"] = 1.0 / float(core_rates["divine"])
    elif primary == "chaos" and core_rates.get("divine") and core_rates.get("exalted"):
        rates_state["exaltPerDivine"] = float(core_rates["exalted"]) / float(core_rates["divine"])
        rates_state["chaosPerDivine"] = 1.0 / float(core_rates["divine"])

    added = 0
    for line in payload["lines"]:
        if not isinstance(line, dict):
            continue
        meta = items_by_id.get(str(line.get("id") or "")) or {}
        name = str(line.get("name") or meta.get("name") or line.get("currencyTypeName") or line.get("id") or "").strip()
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
                if rates_state.get("exaltPerDivine"):
                    ex_val = pv * float(rates_state["exaltPerDivine"])
            elif primary == "exalted":
                ex_val = pv
                if rates_state.get("exaltPerDivine"):
                    div_val = pv / float(rates_state["exaltPerDivine"])
            elif primary == "chaos" and core_rates.get("divine"):
                div_val = pv * float(core_rates["divine"])
                if core_rates.get("exalted"):
                    ex_val = pv * float(core_rates["exalted"])

        nk = norm_price_key(name)
        if nk == "divine orb" and ex_val and ex_val > 1:
            rates_state["exaltPerDivine"] = ex_val
        if div_val is None and ex_val and rates_state.get("exaltPerDivine"):
            div_val = ex_val / float(rates_state["exaltPerDivine"])
        if div_val is None or div_val <= 0:
            continue

        # 过滤极端异常标价（单件挂 99999D 以上的钓鱼/展示品）
        if div_val > 50000 and int(line.get("listingCount") or line.get("count") or 0) <= 2:
            continue

        listings = int(line.get("listingCount") or line.get("count") or line.get("volumePrimaryValue") or 0)
        existing = by_name.get(nk)
        # 多条同名暗金（如 Morior Invictus / Glimpse of Chaos 不同腐化变体）优先保留挂单数最多的主流底价
        if existing is None or listings > existing.get("listingCount", -1):
            by_name[nk] = {
                "name": name,
                "baseType": str(line.get("baseType") or meta.get("baseType") or ""),
                "divineValue": round(div_val, 4),
                "exaltedValue": round(ex_val, 1) if ex_val is not None else 0.0,
                "listingCount": listings,
                "type": type_name,
                "source": "poe.ninja",
            }
            added += 1

    return added


def format_divine_label(divine_val: float, exalt_per_divine: float | None = None) -> str:
    if divine_val >= 10:
        return f"{round(divine_val, 1):g} D"
    if divine_val >= 0.8:
        return f"{round(divine_val, 2):g} D"
    if exalt_per_divine and exalt_per_divine > 0:
        ex = round(divine_val * exalt_per_divine)
        if ex >= 1:
            return f"{ex} E (~{round(divine_val, 2):g} D)"
    if divine_val > 0:
        return f"{round(divine_val, 2):g} D"
    return "< 1 E"


def fetch_ninja_item_index(
    league: str = "",
    *,
    force_refresh: bool = False,
    allow_network: bool = True,
    state_dir: str = STATE_DIR,
) -> dict[str, Any]:
    """拉取并缓存 poe.ninja PoE2 全品类物价索引（暗金装备/珠宝/药剂/护符/血脉宝石/符文/通货）。"""
    league_name = (league or "").strip()
    if not league_name:
        league_name = default_trade_league(allow_network=allow_network, state_dir=state_dir)

    cpath = os.path.join(state_dir, f"ninja_items_{_safe_league(league_name)}.json")
    if not force_refresh and os.path.isfile(cpath):
        try:
            with open(cpath, encoding="utf-8") as f:
                cached = json.load(f)
            if (
                isinstance(cached, dict)
                and time.time() - (cached.get("updatedAt", 0) / 1000.0) < TTL_SECONDS
                and cached.get("itemCount", 0) > 0
            ):
                return cached
        except Exception:
            pass

    if not allow_network:
        return {
            "league": league_name,
            "source": "offline",
            "updatedAt": int(time.time() * 1000),
            "exaltPerDivine": ninja_rates.DEFAULT_RATES["exaltPerDivine"],
            "chaosPerDivine": ninja_rates.DEFAULT_RATES["chaosPerDivine"],
            "itemCount": 0,
            "byName": {},
        }

    enc = urllib.parse.quote(league_name)
    by_name: dict[str, dict[str, Any]] = {}
    rates_state: dict[str, float | None] = {"exaltPerDivine": None, "chaosPerDivine": None}

    # 先拉 Currency 建立准确汇率
    _, cur_payload, _ = _http_json(
        f"https://poe.ninja/poe2/api/economy/exchange/current/overview?league={enc}&type=Currency",
        timeout=8.0,
    )
    _ingest_payload(cur_payload, "Currency", by_name, rates_state)

    urls: list[tuple[str, str]] = [
        (t, f"https://poe.ninja/poe2/api/economy/exchange/current/overview?league={enc}&type={t}")
        for t in _EXCHANGE_TYPES[1:]
    ] + [
        (t, f"https://poe.ninja/poe2/api/economy/stash/current/item/overview?league={enc}&type={t}")
        for t in _STASH_TYPES
    ]

    def _fetch_one(pair: tuple[str, str]) -> tuple[str, dict[str, Any] | None]:
        tname, u = pair
        st, payload, _ = _http_json(u, timeout=8.0)
        return tname, (payload if st == 200 else None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for tname, payload in ex.map(_fetch_one, urls):
            _ingest_payload(payload, tname, by_name, rates_state)

    ex_per_d = rates_state.get("exaltPerDivine") or ninja_rates.DEFAULT_RATES["exaltPerDivine"]
    chaos_per_d = rates_state.get("chaosPerDivine") or ninja_rates.DEFAULT_RATES["chaosPerDivine"]

    for rec in by_name.values():
        rec["formatted"] = format_divine_label(rec["divineValue"], ex_per_d)

    result = {
        "league": league_name,
        "source": "poe.ninja" if by_name else "empty",
        "updatedAt": int(time.time() * 1000),
        "exaltPerDivine": round(float(ex_per_d), 1),
        "chaosPerDivine": round(float(chaos_per_d), 1),
        "itemCount": len(by_name),
        "byName": by_name,
    }

    if result["itemCount"] > 0:
        try:
            _atomic_write_json(cpath, result)
        except Exception:
            pass

    return result


def lookup_ninja_item(name: str, index: dict[str, Any] | None) -> dict[str, Any] | None:
    if not index or not name:
        return None
    by_name = index.get("byName") or {}
    nk = norm_price_key(name)
    if nk in by_name:
        return by_name[nk]
    simplified = re.sub(r"\s+(i|ii|iii|iv|v)$", "", nk, flags=re.I).strip()
    if simplified and simplified in by_name:
        return by_name[simplified]
    return None


def match_build_ninja_prices(
    parsed_build: dict[str, Any],
    ninja_index: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """为目标 BD 的所有暗金装备、暗金珠宝、暗金药剂/护符与高价值血脉辅助宝石自动匹配 poe.ninja 实价。"""
    if not ninja_index or not ninja_index.get("byName"):
        return []
    now = int(time.time() * 1000)
    entries: list[dict[str, Any]] = []

    for group_label, items in (
        ("装备", parsed_build.get("equippedItems") or []),
        ("珠宝", parsed_build.get("jewels") or []),
        ("药剂/护符", parsed_build.get("flasksAndCharms") or []),
    ):
        for it in items:
            if str(it.get("rarity") or "").upper() != "UNIQUE":
                continue
            name = str(it.get("name") or "")
            hit = lookup_ninja_item(name, ninja_index)
            if hit and hit.get("divineValue", 0) > 0:
                dv_raw = float(hit["divineValue"])
                entries.append({
                    "slot": str(it.get("slot") or ""),
                    "itemName": name,
                    "kind": "exact",
                    "divineValue": round(dv_raw, 4) if dv_raw < 0.1 else round(dv_raw, 2),
                    "currencyLabel": hit.get("formatted") or format_divine_label(dv_raw),
                    "listings": int(hit.get("listingCount") or 0),
                    "source": "poe.ninja",
                    "note": f"{group_label} · {name} (poe.ninja 实时)",
                    "updatedAt": now,
                })

    seen_gems: set[str] = set()
    for sg in parsed_build.get("skillGroups") or []:
        for gem in sg.get("gems") or []:
            gname = str(gem.get("name") or "").strip()
            if not gname:
                continue
            gk = norm_price_key(gname)
            if gk in seen_gems:
                continue
            hit = lookup_ninja_item(gname, ninja_index)
            if hit and float(hit.get("divineValue") or 0) >= 0.05:
                seen_gems.add(gk)
                entries.append({
                    "slot": f"Gem:{gname}",
                    "itemName": gname,
                    "kind": "exact",
                    "divineValue": round(float(hit["divineValue"]), 2),
                    "currencyLabel": hit.get("formatted") or format_divine_label(hit["divineValue"]),
                    "listings": int(hit.get("listingCount") or 0),
                    "source": "poe.ninja",
                    "note": f"血脉/特殊宝石 · {gname} (poe.ninja 实时)",
                    "updatedAt": now,
                })

    return entries


def _currency_to_divine(amount: float, currency: str, ninja_index: dict[str, Any] | None) -> float | None:
    if amount <= 0:
        return None
    c = (currency or "").strip().lower()
    ex_per_d = float((ninja_index or {}).get("exaltPerDivine") or 100.0)
    chaos_per_d = float((ninja_index or {}).get("chaosPerDivine") or 20.0)

    if c in ("divine", "div", "divine-orb"):
        return amount
    if c in ("exalted", "exalt", "ex", "exalted-orb"):
        return amount / max(1.0, ex_per_d)
    if c in ("chaos", "c", "chaos-orb"):
        return amount / max(1.0, chaos_per_d)
    if c in ("mirror", "mirror-of-kalandra"):
        hit = lookup_ninja_item("Mirror of Kalandra", ninja_index)
        return amount * (float(hit["divineValue"]) if hit else 800.0)
    if c in ("annul", "orb-of-annulment"):
        hit = lookup_ninja_item("Orb of Annulment", ninja_index)
        return amount * (float(hit["divineValue"]) if hit else 0.1)
    if c in ("alch", "alchemy", "orb-of-alchemy"):
        return amount * 0.005
    if c in ("regal", "regal-orb"):
        return amount * 0.005
    if c in ("vaal", "vaal-orb"):
        hit = lookup_ninja_item("Vaal Orb", ninja_index)
        return amount * (float(hit["divineValue"]) if hit else 0.01)
    # 兜底查表
    hit = lookup_ninja_item(c.replace("-", " "), ninja_index)
    if hit and hit.get("divineValue"):
        return amount * float(hit["divineValue"])
    return None


def _scale_affix_min(text: str, scale: float) -> dict[str, Any]:
    nums = [float(x) for x in re.findall(r"[+-]?\d+(?:\.\d+)?", text or "")]
    min_val: float | None = None
    if len(nums) == 1 and nums[0] > 0:
        raw = nums[0] * scale
        min_val = int(raw) if raw >= 5 else round(raw, 1)
    elif len(nums) >= 2 and (nums[0] + nums[1]) > 0:
        avg = ((nums[0] + nums[1]) / 2.0) * scale
        min_val = int(avg) if avg >= 5 else round(avg, 1)
    return {"text": text, "min": min_val, "required": True}


def _stat_suffix(stat_id: str) -> str:
    """提取 Trade2 statId 的通用后缀（如 'stat.desecrated.stat_3981240776' / 'explicit.stat_3981240776' -> 'stat_3981240776'）。"""
    m = re.search(r"(stat_\d+(?:\|\d+)?)$", str(stat_id or ""))
    return m.group(1) if m else str(stat_id or "").strip().lower()


def _score_listing_weighted(
    row: dict[str, Any],
    weighted_terms: list[dict[str, Any]],
    *,
    slot: str,
    base_type: str,
) -> float:
    """对 Trade2 拉取的真实挂单逐条按权重公式打分（兼容 PoE2 对象格式 explicitMods 与纯字符串格式）。"""
    if not weighted_terms:
        return 0.0
    item_obj = row.get("item") or {}
    if not isinstance(item_obj, dict):
        return 0.0

    weight_by_sid: dict[str, float] = {}
    weight_by_suffix: dict[str, float] = {}
    for t in weighted_terms:
        sid = str(t.get("statId") or "")
        w = float(t.get("weight") or 0.0)
        if sid and w > 0:
            weight_by_sid[sid] = w
            weight_by_suffix[_stat_suffix(sid)] = w

    score = 0.0
    for key in ("explicitMods", "fracturedMods", "desecratedMods", "implicitMods", "craftedMods"):
        vals = item_obj.get(key)
        if not isinstance(vals, list):
            continue
        for entry in vals:
            if isinstance(entry, dict):
                desc = str(entry.get("description") or entry.get("text") or "")
                h = str(entry.get("hash") or "")
                w = weight_by_sid.get(h) or weight_by_suffix.get(_stat_suffix(h))
                if not w and desc:
                    res = trade_url.resolve_affix_to_trade_stat(desc, slot=slot, base_type=base_type)
                    sid = str(res.get("statId") or "")
                    w = weight_by_sid.get(sid) or weight_by_suffix.get(_stat_suffix(sid))
                if not w:
                    continue
                val = trade_url._extract_numeric_value(desc)
                if val and val > 0:
                    score += w * float(val)
            elif isinstance(entry, str) and entry:
                res = trade_url.resolve_affix_to_trade_stat(entry, slot=slot, base_type=base_type)
                sid = str(res.get("statId") or "")
                w = weight_by_sid.get(sid) or weight_by_suffix.get(_stat_suffix(sid))
                if not w:
                    continue
                val = trade_url._extract_numeric_value(entry)
                if val and val > 0:
                    score += w * float(val)
    return round(score, 2)


def price_single_item_trade2(
    item: dict[str, Any],
    *,
    league: str,
    kind: str = "budget",
    parsed_build: dict[str, Any] | None = None,
    dossier_item: dict[str, Any] | None = None,
    ninja_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """直接向官方 PoE2 Trade2 API 查单件装备实价：
    - 使用官方 `status: available`（含直购 Instant Buyout + 在线）、`total_augment_sockets`、`skill.*` 与 `equipment_filters`；
    - 黄装平替/原件采用「免登录 200 OK 的 count 池 + 本地按 PoB2 消融权重给每条挂单打分」，不再死卡单个词条；
    - 受全局 TradeRateLimiter 保护（跨请求最小间隔 1.6s，触发 429 后全局冷却熔断 ≥60s）；
    - 同时返回浏览器一键直达的官方 `type: "weight"` 加权求和链接。
    """
    slot = str(item.get("slot") or "")
    name = str(item.get("name") or item.get("baseType") or "")
    base_type = str(item.get("baseType") or "")
    rarity = str(item.get("rarity") or "RARE").upper()
    is_unique = rarity == "UNIQUE"
    kind_norm = "exact" if is_unique else (kind if kind in ("exact", "budget", "semi", "base") else "budget")

    plan = trade_url.build_weighted_substitute_search(
        item,
        league=league,
        kind=kind_norm,
        parsed_build=parsed_build,
        dossier_item=dossier_item,
        status_option="available",
    )

    rem_cd = TRADE_RATE_LIMITER.remaining_cooldown()
    if rem_cd > 0:
        return {
            "ok": False,
            "rateLimited": True,
            "error": f"官方市集处于 429 冷却保护期（剩余约 {int(round(rem_cd))} 秒），请稍后再试。",
            "tradeUrl": plan["tradeUrl"],
            "weightedTradeUrl": plan["tradeUrl"],
        }

    enc_league = urllib.parse.quote(league or "Standard")
    search_url = f"https://www.pathofexile.com/api/trade2/search/poe2/{enc_league}"

    def _run_search(payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None, str, float]:
        return _trade_http_json(search_url, method="POST", body=payload, timeout=10.0)

    st, sdata, serr, cd = _run_search(plan["apiSearchQuery"])
    relaxed_used = False
    if st == 429:
        return {
            "ok": False,
            "rateLimited": True,
            "error": f"官方市集触发 429 限流，已全局熔断保护约 {int(round(cd or 60))} 秒，请稍候再试。",
            "tradeUrl": plan["tradeUrl"],
            "weightedTradeUrl": plan["tradeUrl"],
        }

    # 若首轮查 0 件或触发复杂度 400，经全局限流器安全间隔后自动放宽 1 次（绝不死循环）
    need_relax = (
        (st == 200 and isinstance(sdata, dict) and int(sdata.get("total") or 0) == 0)
        or (st == 400 and plan.get("apiRelaxedQuery") and plan["apiRelaxedQuery"] != plan["apiSearchQuery"])
    )
    if need_relax and plan.get("apiRelaxedQuery"):
        st2, sdata2, serr2, cd2 = _run_search(plan["apiRelaxedQuery"])
        if st2 == 429:
            return {
                "ok": False,
                "rateLimited": True,
                "error": f"官方市集触发 429 限流，已全局熔断保护约 {int(round(cd2 or 60))} 秒，请稍候再试。",
                "tradeUrl": plan["tradeUrl"],
                "weightedTradeUrl": plan["tradeUrl"],
            }
        if st2 == 200 and isinstance(sdata2, dict):
            st, sdata, serr = st2, sdata2, serr2
            relaxed_used = True

    if st != 200 or not isinstance(sdata, dict):
        return {
            "ok": False,
            "rateLimited": False,
            "error": f"官方市集查询失败 ({st}): {serr}",
            "tradeUrl": plan["tradeUrl"],
            "weightedTradeUrl": plan["tradeUrl"],
        }

    total = int(sdata.get("total") or 0)
    qid = str(sdata.get("id") or "")
    ids = [str(x) for x in (sdata.get("result") or [])[:10]]
    live_web_url = f"https://www.pathofexile.com/trade2/search/poe2/{enc_league}/{qid}" if qid else plan["tradeUrl"]

    if not ids:
        return {
            "ok": True,
            "rateLimited": False,
            "quote": None,
            "totalListings": 0,
            "tradeUrl": plan["tradeUrl"],
            "weightedTradeUrl": plan["tradeUrl"],
            "weightedTerms": plan.get("weightedTerms") or [],
            "targetScore": plan.get("targetScore") or 0.0,
            "minScore": plan.get("minScore") or 0.0,
            "message": "放宽筛选后仍 0 件在售，可点「直达加权搜平替」在市集手动微调权重或放开基底。",
        }

    fetch_url = f"https://www.pathofexile.com/api/trade2/fetch/{','.join(ids)}?query={urllib.parse.quote(qid)}&realm=poe2"
    fst, fdata, ferr, fcd = _trade_http_json(fetch_url, method="GET", timeout=10.0)
    if fst == 429:
        return {
            "ok": False,
            "rateLimited": True,
            "error": f"官方市集拉取详情触发 429 限流，已全局熔断保护约 {int(round(fcd or 60))} 秒。",
            "tradeUrl": live_web_url,
            "weightedTradeUrl": plan["tradeUrl"],
        }
    if fst != 200 or not isinstance(fdata, dict):
        return {
            "ok": False,
            "rateLimited": False,
            "error": f"拉取市集挂单失败 ({fst}): {ferr}",
            "tradeUrl": live_web_url,
            "weightedTradeUrl": plan["tradeUrl"],
        }

    weighted_terms = plan.get("weightedTerms") or []
    target_score = float(plan.get("targetScore") or 0.0)
    scored_rows: list[tuple[float, float, str]] = []  # (divine_val, weight_ratio, raw_label)

    for row in fdata.get("result") or []:
        if not isinstance(row, dict):
            continue
        price_obj = (row.get("listing") or {}).get("price") or {}
        amt = price_obj.get("amount")
        cur = str(price_obj.get("currency") or "")
        if not isinstance(amt, (int, float)) or amt <= 0 or not cur:
            continue
        dv = _currency_to_divine(float(amt), cur, ninja_index)
        if dv is None or dv <= 0:
            continue
        w_ratio = 1.0
        if not is_unique and weighted_terms and target_score > 0:
            sc = _score_listing_weighted(row, weighted_terms, slot=slot, base_type=base_type)
            w_ratio = min(1.5, sc / target_score)
        scored_rows.append((dv, w_ratio, f"{amt:g} {cur}"))

    if not scored_rows:
        return {
            "ok": True,
            "rateLimited": False,
            "quote": None,
            "totalListings": total,
            "tradeUrl": live_web_url,
            "weightedTradeUrl": plan["tradeUrl"],
            "message": "命中挂单但未标明可识别通货价格。",
        }

    # 若启用了加权打分，优先用权重匹配度达到合理下限（>=50%，原件 >=65%）的挂单核价，防止 1E 残次品拉低均价
    min_ratio = 0.65 if kind_norm == "exact" else 0.50
    valid_rows = [r for r in scored_rows if r[1] >= min_ratio] if (not is_unique and weighted_terms and target_score > 0) else scored_rows
    if len(valid_rows) < 2:
        # 若严格过滤后样本过少，取权重匹配度排前一半的挂单
        by_w = sorted(scored_rows, key=lambda x: (-x[1], x[0]))
        valid_rows = by_w[: max(2, len(by_w) // 2)]

    valid_rows.sort(key=lambda x: x[0])
    # 防低价钓鱼：>=4 条挂单时剔除第 1 条异常低价，取第 2~5 条的中位数
    core_slice = valid_rows[1:5] if len(valid_rows) >= 4 else valid_rows[:4]
    ref_d = round(float(statistics.median([x[0] for x in core_slice])), 2)
    avg_match_pct = (
        int(round(statistics.mean([x[1] for x in core_slice]) * 100))
        if (not is_unique and weighted_terms and target_score > 0)
        else None
    )
    sample_labels = [x[2] for x in core_slice[:4]]

    ex_per_d = float((ninja_index or {}).get("exaltPerDivine") or 100.0)
    mode_labels = {
        "exact": "原件市集实价",
        "budget": "加权平替实价",
        "semi": "半成品市集实价",
        "base": "白底市集实价",
    }
    match_txt = f" · 均权重匹配 {avg_match_pct}%" if avg_match_pct is not None else ""
    relax_txt = " · 自动放宽命中" if relaxed_used else ""
    now = int(time.time() * 1000)
    quote = {
        "slot": slot,
        "itemName": name,
        "kind": kind_norm,
        "divineValue": ref_d,
        "currencyLabel": format_divine_label(ref_d, ex_per_d),
        "listings": total,
        "tradeUrl": plan["tradeUrl"] if (plan.get("mode") == "weighted") else live_web_url,
        "liveTradeUrl": live_web_url,
        "avgWeightMatchPct": avg_match_pct,
        "source": "trade",
        "note": f"{mode_labels.get(kind_norm, '市集实价')}{match_txt}{relax_txt} · {total} 件在售（样本：{' / '.join(sample_labels)}）",
        "samples": sample_labels,
        "updatedAt": now,
    }
    return {
        "ok": True,
        "rateLimited": False,
        "quote": quote,
        "totalListings": total,
        "tradeUrl": live_web_url,
        "weightedTradeUrl": plan["tradeUrl"],
        "weightedTerms": weighted_terms,
        "targetScore": target_score,
        "minScore": plan.get("minScore") or 0.0,
        "relaxed": relaxed_used,
    }


def merge_price_entries(
    store: StateStore,
    *,
    league: str,
    new_entries: list[dict[str, Any]],
    source: str = "backend-market",
) -> dict[str, Any]:
    """将新核价条目按 (slot, kind) 无损合并进 StateStore，保留同赛季其他槽位已有报价。"""
    existing = store.get_prices()
    same_league = not existing.get("empty") and (existing.get("league") or "").lower() == (league or "").lower()

    merged_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    if same_league:
        for e in existing.get("entries") or []:
            if not isinstance(e, dict) or not e.get("slot"):
                continue
            k = (str(e["slot"]).lower(), str(e.get("kind") or "exact").lower())
            merged_by_key[k] = dict(e)

    for e in new_entries:
        if not isinstance(e, dict) or not e.get("slot"):
            continue
        val = e.get("divineValue")
        if not isinstance(val, (int, float)) or val <= 0:
            continue
        k = (str(e["slot"]).lower(), str(e.get("kind") or "exact").lower())
        merged_by_key[k] = dict(e)

    entries_list = list(merged_by_key.values())
    prices_map: dict[str, float] = {}
    if same_league and isinstance(existing.get("prices"), dict):
        for k, v in existing["prices"].items():
            if isinstance(v, (int, float)) and v > 0:
                prices_map[str(k)] = round(float(v), 2)

    # 快捷映射优先级：manual > exact > budget > semi
    prio = {"manual": 4, "exact": 3, "budget": 2, "semi": 1, "base": 0}
    best_per_slot: dict[str, tuple[int, float]] = {}
    for e in entries_list:
        sl = str(e["slot"])
        ev = round(float(e["divineValue"]), 2)
        p = 4 if str(e.get("source") or "").lower() == "manual" else prio.get(str(e.get("kind") or "exact").lower(), 1)
        cur = best_per_slot.get(sl)
        if cur is None or p >= cur[0]:
            best_per_slot[sl] = (p, ev)

    for sl, (_, val) in best_per_slot.items():
        prices_map[sl] = val

    return store.save_prices({
        "league": league or "Standard",
        "source": source,
        "updatedAt": int(time.time() * 1000),
        "prices": prices_map,
        "entries": entries_list,
    })


def price_build_trade2_batch(
    parsed_build: dict[str, Any],
    *,
    league: str,
    kind: str = "budget",
    slots: list[str] | None = None,
    dossier: dict[str, Any] | None = None,
    ninja_index: dict[str, Any] | None = None,
    store: StateStore | None = None,
    max_items: int = 7,
    delay_sec: float = 1.65,
) -> dict[str, Any]:
    """防 429 串行批量查官方 Trade2 价格（默认单批上限 7 件，仅查黄装与未被 poe.ninja 覆盖的暗金，自动复用 PoB2 消融权重）。"""
    all_items = list(parsed_build.get("equippedItems") or []) + list(parsed_build.get("jewels") or [])
    slot_filter = {s.strip().lower() for s in (slots or []) if s.strip()}
    dossier_by_slot: dict[str, dict[str, Any]] = {}
    if isinstance(dossier, dict):
        for di in list(dossier.get("items") or []) + list(dossier.get("jewels") or []):
            sl = str(di.get("slot") or "").lower()
            if sl and sl not in dossier_by_slot:
                dossier_by_slot[sl] = di

    targets: list[dict[str, Any]] = []
    for it in all_items:
        sl = str(it.get("slot") or "")
        if not sl:
            continue
        if slot_filter:
            if sl.lower() in slot_filter:
                targets.append(it)
            continue
        # 跳过纯正常白底肢体组件等无词缀物品
        rarity = str(it.get("rarity") or "").upper()
        if rarity == "NORMAL":
            continue
        if rarity == "UNIQUE" and lookup_ninja_item(str(it.get("name") or ""), ninja_index):
            continue
        targets.append(it)

    targets = targets[:max_items]
    quotes: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    rate_limited = False

    for i, it in enumerate(targets):
        if i > 0 and delay_sec > 0:
            time.sleep(delay_sec)
        sl_l = str(it.get("slot") or "").lower()
        res = price_single_item_trade2(
            it,
            league=league,
            kind=kind,
            parsed_build=parsed_build,
            dossier_item=dossier_by_slot.get(sl_l),
            ninja_index=ninja_index,
        )
        if res.get("rateLimited"):
            rate_limited = True
            errors.append({"slot": str(it.get("slot") or ""), "error": str(res.get("error") or "429 限流")})
            break
        if res.get("quote"):
            quotes.append(res["quote"])
            if store is not None:
                merge_price_entries(store, league=league, new_entries=[res["quote"]], source="backend-trade")
        elif not res.get("ok"):
            errors.append({"slot": str(it.get("slot") or ""), "error": str(res.get("error") or "查询失败")})

    snapshot = store.get_prices() if store is not None else None
    return {
        "league": league,
        "kind": kind,
        "attempted": len(targets),
        "pricedCount": len(quotes),
        "rateLimited": rate_limited,
        "quotes": quotes,
        "errors": errors,
        "snapshot": snapshot,
    }
