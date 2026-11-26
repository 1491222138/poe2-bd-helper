"""PoE2 词缀库与官方市集双语词缀表自动更新器。

数据源：
1. RePoE PoE2 (https://repoe-fork.github.io/poe2/)
   - mods.min.json
   - base_items.min.json
   - mods_by_base.min.json
2. Path of Exile 2 官方 Trade2 静态词缀表
   - 英文: https://www.pathofexile.com/api/trade2/data/stats
   - 简体中文: https://poe.game.qq.com/api/trade2/data/stats
   - 繁体中文兜底: https://pathofexile.tw/api/trade2/data/stats

支持两级存储：
- 仓库内置基线文件：backend/data/affix_library.json 与 backend/data/trade_stats_cache.json
- 用户运行时热更新文件：~/.poe2-workbench/affix_library.json 与 ~/.poe2-workbench/trade_stats_cache.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.request
from typing import Any

from backend.config import REPO_ROOT
from backend.market.price_store import STATE_DIR

DATA_DIR = os.path.join(REPO_ROOT, "backend", "data")
BUNDLED_AFFIX_LIBRARY_PATH = os.path.join(DATA_DIR, "affix_library.json")
BUNDLED_TRADE_STATS_PATH = os.path.join(DATA_DIR, "trade_stats_cache.json")

RUNTIME_AFFIX_LIBRARY_PATH = os.path.join(STATE_DIR, "affix_library.json")
RUNTIME_TRADE_STATS_PATH = os.path.join(STATE_DIR, "trade_stats_cache.json")

REPOE_BASE_URL = "https://repoe-fork.github.io/poe2"
TRADE_STATS_EN_URL = "https://www.pathofexile.com/api/trade2/data/stats"
TRADE_STATS_ZH_CN_URL = "https://poe.game.qq.com/api/trade2/data/stats"
TRADE_STATS_ZH_TW_URL = "https://pathofexile.tw/api/trade2/data/stats"

DEFAULT_EQUIPMENT_CLASSES: tuple[str, ...] = (
    "Amulets",
    "Belts",
    "Body Armours",
    "Boots",
    "Bows",
    "Bucklers",
    "Claws",
    "Crossbows",
    "Daggers",
    "Flails",
    "Foci",
    "Gloves",
    "Helmets",
    "One Hand Axes",
    "One Hand Maces",
    "One Hand Swords",
    "Quarterstaves",
    "Quivers",
    "Rings",
    "Sceptres",
    "Shields",
    "Spears",
    "Staves",
    "Talismans",
    "Two Hand Axes",
    "Two Hand Maces",
    "Two Hand Swords",
    "Wands",
)

# Trade2 explicit 词缀分类优先级（数字越小越优先）
_TRADE_CATEGORY_PRIORITY: dict[str, int] = {
    "explicit": 0,
    "implicit": 1,
    "fractured": 2,
    "desecrated": 3,
    "enchant": 4,
    "rune": 5,
    "sanctum": 6,
    "pseudo": 9,
}

_NUM_RANGE_RE = re.compile(r"\(\s*[+-]?\d+(?:\.\d+)?\s*-\s*[+-]?\d+(?:\.\d+)?\s*\)|[+-]?\d+(?:\.\d+)?")
_RICH_PIPE_RE = re.compile(r"\[([^|\]]+)\|([^\]]+)\]")
_RICH_SINGLE_RE = re.compile(r"\[([^\]]+)\]")


def clean_repoe_text(text: str) -> str:
    """将 RePoE 的 `[Tag|Display]` 标记清理为可读英文文本。"""
    s = _RICH_PIPE_RE.sub(r"\2", text or "")
    s = _RICH_SINGLE_RE.sub(r"\1", s)
    return s.strip()


def normalize_stat_template(text: str) -> str:
    """将任意英文或中文词缀模板归一化为用于跨表对齐的骨架字符串（数字统一为 #）。"""
    s = clean_repoe_text(text)
    s = re.sub(r"\s*\(local\)\s*", " ", s, flags=re.I)
    s = s.replace("\n", " ")
    s = _NUM_RANGE_RE.sub("#", s)
    s = re.sub(r"\+#", "#", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _http_get_json(url: str, timeout: float = 25.0, retries: int = 2) -> Any:
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "PoE2Workbench/1.0 (+https://github.com/poe2-workbench)",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"请求 {url} 失败：{last_err}")


def _atomic_write_json(path: str, payload: Any, *, compact: bool = False) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json_safe(path: str) -> dict[str, Any] | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def resolve_active_file(bundled_path: str, runtime_path: str) -> str:
    """选择较新且可用的词缀/词条数据文件（运行时热更新优先于仓库基线）。"""
    b_data = _read_json_safe(bundled_path)
    r_data = _read_json_safe(runtime_path)
    if r_data and not b_data:
        return runtime_path
    if b_data and not r_data:
        return bundled_path
    if r_data and b_data:
        r_ts = str(r_data.get("generatedAt") or "")
        b_ts = str(b_data.get("generatedAt") or "")
        if r_ts >= b_ts:
            return runtime_path
    return bundled_path


def _class_singular_aliases(cls_name: str) -> set[str]:
    low = cls_name.lower()
    aliases = {low}
    if low.endswith("ies"):
        aliases.add(low[:-3] + "y")
    if low == "foci":
        aliases.add("focus")
    elif low == "staves":
        aliases.add("staff")
    elif low == "quarterstaves":
        aliases.add("quarterstaff")
    elif low == "body armours":
        aliases.add("body armour")
    elif low.endswith("s"):
        aliases.add(low[:-1])
    return aliases


def _pick_spawn_weight(mod_obj: dict[str, Any], class_tags: set[str]) -> int:
    weights = mod_obj.get("spawn_weights") or []
    best = 0
    for sw in weights:
        if not isinstance(sw, dict):
            continue
        tag = str(sw.get("tag") or "")
        w = int(sw.get("weight") or 0)
        if tag in class_tags and w > 0:
            best = max(best, w)
    if best > 0:
        return best
    for sw in weights:
        if isinstance(sw, dict) and int(sw.get("weight") or 0) > 0:
            return int(sw["weight"])
    return 1


def build_affix_library_from_repoe(
    mods_raw: dict[str, Any],
    base_items_raw: dict[str, Any],
    mods_by_base_raw: dict[str, Any],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """根据 RePoE 原始 JSON 构建与 affix_pool.py 完全兼容的 affix_library 结构。"""
    mods_out: dict[str, list[Any]] = {}
    classes_out: dict[str, dict[str, Any]] = {}

    def register_mod(mod_id: str) -> tuple[str, list[str], str] | None:
        m = mods_raw.get(mod_id)
        if not isinstance(m, dict):
            return None
        gen_type = str(m.get("generation_type") or "")
        if gen_type not in ("prefix", "suffix"):
            return None
        disp = clean_repoe_text(str(m.get("text") or ""))
        if not disp:
            return None
        groups = [str(g) for g in (m.get("groups") or []) if g] or [mod_id]
        mods_out[mod_id] = [disp, groups, gen_type]
        return disp, groups, gen_type

    # 预先把所有 item 域前后缀词缀（含精华专用词缀）注册进 mods 字典
    for mid, m in mods_raw.items():
        if isinstance(m, dict) and m.get("domain") == "item":
            register_mod(mid)

    for cls_name in DEFAULT_EQUIPMENT_CLASSES:
        tag_groups = mods_by_base_raw.get(cls_name)
        if not isinstance(tag_groups, dict):
            continue

        all_tags: set[str] = set()
        referenced_base_ids: set[str] = set()
        prefix_rows_by_id: dict[str, tuple[int, int, str]] = {}
        suffix_rows_by_id: dict[str, tuple[int, int, str]] = {}

        for tag_key, bundle in tag_groups.items():
            if not isinstance(bundle, dict):
                continue
            for t in str(tag_key).split(","):
                t_clean = t.strip()
                if t_clean and t_clean != "default":
                    all_tags.add(t_clean)
            for bid in bundle.get("bases") or []:
                referenced_base_ids.add(str(bid))

        for tag_key, bundle in tag_groups.items():
            if not isinstance(bundle, dict):
                continue
            mods_block = bundle.get("mods") or {}
            for side in ("prefix", "suffix"):
                groups_map = mods_block.get(side) or {}
                if not isinstance(groups_map, dict):
                    continue
                target_map = prefix_rows_by_id if side == "prefix" else suffix_rows_by_id
                for _grp_name, mod_lvl_map in groups_map.items():
                    if not isinstance(mod_lvl_map, dict):
                        continue
                    for mod_id, lvl in mod_lvl_map.items():
                        reg = register_mod(str(mod_id))
                        if not reg:
                            continue
                        disp, _groups, _gt = reg
                        req_lvl = int(lvl or 1)
                        w = _pick_spawn_weight(mods_raw[str(mod_id)], all_tags)
                        prev = target_map.get(str(mod_id))
                        if prev is None or req_lvl < prev[0]:
                            target_map[str(mod_id)] = (req_lvl, max(prev[1] if prev else 0, w), disp)

        # 收集基底清单
        cls_aliases = _class_singular_aliases(cls_name)
        bases: list[list[Any]] = []
        seen_base_ids: set[str] = set()
        for bid, binfo in base_items_raw.items():
            if not isinstance(binfo, dict):
                continue
            if binfo.get("release_state") != "released":
                continue
            item_cls_low = str(binfo.get("item_class") or "").lower()
            if bid in referenced_base_ids or item_cls_low in cls_aliases:
                if bid in seen_base_ids:
                    continue
                seen_base_ids.add(bid)
                bases.append([bid, str(binfo.get("name") or bid), int(binfo.get("drop_level") or 1)])

        bases.sort(key=lambda r: (int(r[2]), str(r[1])))
        prefixes = [
            [mid, row[0], row[1], row[2]]
            for mid, row in sorted(prefix_rows_by_id.items(), key=lambda kv: (kv[1][0], kv[0]))
        ]
        suffixes = [
            [mid, row[0], row[1], row[2]]
            for mid, row in sorted(suffix_rows_by_id.items(), key=lambda kv: (kv[1][0], kv[0]))
        ]

        classes_out[cls_name] = {
            "tags": sorted(all_tags),
            "bases": bases,
            "prefixes": prefixes,
            "suffixes": suffixes,
        }

    return {
        "generatedAt": generated_at or time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": {
            "mods": "repoe-fork/poe2 mods.min.json",
            "baseItems": "repoe-fork/poe2 base_items.min.json",
            "modsByBase": "repoe-fork/poe2 mods_by_base.min.json",
        },
        "mods": mods_out,
        "classes": classes_out,
    }


def build_trade_stats_cache(
    en_payload: dict[str, Any],
    zh_payload: dict[str, Any] | None = None,
    zh_fallback_payload: dict[str, Any] | None = None,
    *,
    generated_at: str | None = None,
    zh_source_url: str = TRADE_STATS_ZH_CN_URL,
) -> dict[str, Any]:
    """合并官方 Trade2 英文与中文 stats 表，输出轻量双语索引。"""
    zh_by_id: dict[str, str] = {}
    for fallback_payload in (zh_fallback_payload, zh_payload):
        if not isinstance(fallback_payload, dict):
            continue
        for cat in fallback_payload.get("result") or []:
            if not isinstance(cat, dict):
                continue
            for entry in cat.get("entries") or []:
                if not isinstance(entry, dict):
                    continue
                sid = str(entry.get("id") or "").strip()
                txt = str(entry.get("text") or "").strip()
                if sid and txt:
                    zh_by_id[sid] = txt

    entries_out: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    zh_count = 0

    for cat in en_payload.get("result") or []:
        if not isinstance(cat, dict):
            continue
        cat_id = str(cat.get("id") or "").strip().lower()
        if cat_id not in _TRADE_CATEGORY_PRIORITY:
            continue
        for entry in cat.get("entries") or []:
            if not isinstance(entry, dict):
                continue
            sid = str(entry.get("id") or "").strip()
            en_text = str(entry.get("text") or "").strip()
            if not sid or not en_text or sid in seen_ids:
                continue
            seen_ids.add(sid)
            zh_text = zh_by_id.get(sid, "")
            if zh_text:
                zh_count += 1
            entries_out.append({
                "id": sid,
                "type": str(entry.get("type") or cat_id),
                "en": en_text,
                "zh": zh_text,
                "enNorm": normalize_stat_template(en_text),
                "zhNorm": normalize_stat_template(zh_text) if zh_text else "",
            })

    return {
        "generatedAt": generated_at or time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": {
            "en": TRADE_STATS_EN_URL,
            "zh": zh_source_url,
        },
        "count": len(entries_out),
        "zhCount": zh_count,
        "entries": entries_out,
    }


def update_all_libraries(
    *,
    write_repo: bool = False,
    update_repoe: bool = True,
    update_trade_stats: bool = True,
    timeout: float = 25.0,
) -> dict[str, Any]:
    """在线拉取最新 RePoE 词缀库与官方 Trade2 中英词缀表并写入持久化缓存。"""
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    affix_target = BUNDLED_AFFIX_LIBRARY_PATH if write_repo else RUNTIME_AFFIX_LIBRARY_PATH
    trade_target = BUNDLED_TRADE_STATS_PATH if write_repo else RUNTIME_TRADE_STATS_PATH

    result: dict[str, Any] = {
        "ok": True,
        "updatedAt": now_str,
        "target": "repo" if write_repo else "runtime",
        "errors": [],
    }

    if update_repoe:
        try:
            mods_raw = _http_get_json(f"{REPOE_BASE_URL}/mods.min.json", timeout=timeout)
            base_items_raw = _http_get_json(f"{REPOE_BASE_URL}/base_items.min.json", timeout=timeout)
            mods_by_base_raw = _http_get_json(f"{REPOE_BASE_URL}/mods_by_base.min.json", timeout=timeout)
            lib = build_affix_library_from_repoe(
                mods_raw,
                base_items_raw,
                mods_by_base_raw,
                generated_at=now_str,
            )
            _atomic_write_json(affix_target, lib, compact=True)
            result["affixLibrary"] = {
                "path": affix_target,
                "generatedAt": lib["generatedAt"],
                "modsCount": len(lib["mods"]),
                "classesCount": len(lib["classes"]),
            }
        except Exception as exc:
            result["errors"].append(f"RePoE 更新失败：{exc}")

    if update_trade_stats:
        try:
            en_payload = _http_get_json(TRADE_STATS_EN_URL, timeout=timeout)
            zh_cn_payload = None
            zh_tw_payload = None
            zh_src = TRADE_STATS_ZH_CN_URL
            try:
                zh_cn_payload = _http_get_json(TRADE_STATS_ZH_CN_URL, timeout=timeout)
            except Exception:
                zh_cn_payload = None
            try:
                zh_tw_payload = _http_get_json(TRADE_STATS_ZH_TW_URL, timeout=timeout)
                if zh_cn_payload is None:
                    zh_src = TRADE_STATS_ZH_TW_URL
            except Exception:
                zh_tw_payload = None

            cache = build_trade_stats_cache(
                en_payload,
                zh_cn_payload,
                zh_tw_payload,
                generated_at=now_str,
                zh_source_url=zh_src,
            )
            _atomic_write_json(trade_target, cache, compact=True)
            result["tradeStats"] = {
                "path": trade_target,
                "generatedAt": cache["generatedAt"],
                "count": cache["count"],
                "zhCount": cache["zhCount"],
                "zhSource": zh_src,
            }
        except Exception as exc:
            result["errors"].append(f"Trade2 词缀表更新失败：{exc}")

    if result["errors"] and "affixLibrary" not in result and "tradeStats" not in result:
        result["ok"] = False

    # 清除内存缓存，确保新词缀库立刻生效
    from backend.domain.crafting import affix_pool
    from backend.data import game_terms

    affix_pool.clear_library_caches()
    if update_trade_stats or update_repoe:
        try:
            mods_map = game_terms.build_mods_zh_map_from_trade_cache()
            mods_target = game_terms.BUNDLED_MODS_ZH_MAP_PATH if write_repo else game_terms.RUNTIME_MODS_ZH_MAP_PATH
            _atomic_write_json(mods_target, mods_map, compact=True)
            game_terms.clear_terms_cache()
        except Exception:
            pass
    return result


def get_library_status() -> dict[str, Any]:
    """返回当前生效词缀库与官方双语词缀表的版本和统计信息。"""
    affix_path = resolve_active_file(BUNDLED_AFFIX_LIBRARY_PATH, RUNTIME_AFFIX_LIBRARY_PATH)
    trade_path = resolve_active_file(BUNDLED_TRADE_STATS_PATH, RUNTIME_TRADE_STATS_PATH)
    affix_data = _read_json_safe(affix_path) or {}
    trade_data = _read_json_safe(trade_path) or {}
    return {
        "affixLibrary": {
            "generatedAt": affix_data.get("generatedAt") or "",
            "modsCount": len(affix_data.get("mods") or {}),
            "classesCount": len(affix_data.get("classes") or {}),
            "source": affix_data.get("source") or {},
            "isRuntimeOverride": affix_path == RUNTIME_AFFIX_LIBRARY_PATH,
        },
        "tradeStats": {
            "generatedAt": trade_data.get("generatedAt") or "",
            "count": int(trade_data.get("count") or len(trade_data.get("entries") or [])),
            "zhCount": int(trade_data.get("zhCount") or 0),
            "source": trade_data.get("source") or {},
            "isRuntimeOverride": trade_path == RUNTIME_TRADE_STATS_PATH,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="更新 PoE2 RePoE 词缀库与 Trade2 中英对照表")
    parser.add_argument("--write-repo", action="store_true", help="直接更新仓库内 backend/data/*.json")
    parser.add_argument("--skip-repoe", action="store_true", help="跳过 RePoE 词缀库拉取")
    parser.add_argument("--skip-trade", action="store_true", help="跳过官方 Trade2 词缀表拉取")
    parser.add_argument("--timeout", type=float, default=25.0, help="单次 HTTP 请求超时秒数")
    args = parser.parse_args()

    res = update_all_libraries(
        write_repo=args.write_repo,
        update_repoe=not args.skip_repoe,
        update_trade_stats=not args.skip_trade,
        timeout=args.timeout,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
