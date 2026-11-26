"""PoB2 武器底材库解析与按武器分型的等价底材推荐器。

直接读取本机 PathOfBuilding-PoE2/src/Data/Bases/*.lua，提取每把武器底材的：
  - 武器大类（Bow / Crossbow / One Hand Mace / Two Hand Mace / Spear / Quarterstaff / Wand / Staff / Sceptre 等）
  - 基础单发均伤（physAvg / eleAvg / chaosAvg / avgHit）与基础 DPS（baseDps / basePdps）
  - 基础暴击率（critBase）、基础攻速（apsBase）、弩装填时间（reloadTimeBase）、权杖基础精魂（spiritBase）
  - 基底隐含词缀（implicit）、装备需求等级与三属性门槛（req）

用于：
  1. 按 PoB2 实算武器分型（attack_dps / attack_hit_no_aps / speed_tool / caster_or_minion / stat_stick_weapon）
     推荐同大类下最优的等价/平替底材（例如不吃攻速的流血/猛击推荐高单发慢速底材，而非高 DPS 快速底材）；
  2. 在搜白底/半成品/成品平替时量化原件底材与同大类顶级底材的差距。
"""
from __future__ import annotations

import os
import re
from typing import Any

from backend.config import pob2_src

_WEAPON_BASE_FILES: tuple[str, ...] = (
    "axe.lua",
    "bow.lua",
    "claw.lua",
    "crossbow.lua",
    "dagger.lua",
    "flail.lua",
    "mace.lua",
    "sceptre.lua",
    "spear.lua",
    "staff.lua",
    "sword.lua",
    "talisman.lua",
    "wand.lua",
)

_ENTRY_START_RE = re.compile(r'^\s*itemBases\["([^"]+)"\]\s*=\s*\{\s*$')
_TYPE_RE = re.compile(r'\btype\s*=\s*"([^"]+)"')
_IMPLICIT_RE = re.compile(r'\bimplicit\s*=\s*"([^"]+)"')
_SOCKET_LIMIT_RE = re.compile(r'\bsocketLimit\s*=\s*(\d+)')
_WEAPON_BLOCK_RE = re.compile(r'\bweapon\s*=\s*\{([^}]*)\}')
_REQ_BLOCK_RE = re.compile(r'\breq\s*=\s*\{([^}]*)\}')
_TAGS_BLOCK_RE = re.compile(r'\btags\s*=\s*\{([^}]*)\}')
_KV_NUM_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?)')
_TAG_TRUE_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*true')


def _parse_num_map(blob: str) -> dict[str, float]:
    return {k: float(v) for k, v in _KV_NUM_RE.findall(blob or "")}


def _parse_weapon_lua_file(path: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return out

    cur_name: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        nonlocal cur_name, buf
        if not cur_name:
            buf = []
            return
        body = "\n".join(buf)
        tm = _TYPE_RE.search(body)
        wtype = tm.group(1) if tm else ""
        if not wtype:
            cur_name = None
            buf = []
            return
        im = _IMPLICIT_RE.search(body)
        implicit = im.group(1).replace("\\n", " / ") if im else ""
        sm = _SOCKET_LIMIT_RE.search(body)
        sock_limit = int(sm.group(1)) if sm else 0

        wm = _WEAPON_BLOCK_RE.search(body)
        wmap = _parse_num_map(wm.group(1)) if wm else {}
        rm = _REQ_BLOCK_RE.search(body)
        rmap = _parse_num_map(rm.group(1)) if rm else {}
        tgm = _TAGS_BLOCK_RE.search(body)
        tags = sorted(_TAG_TRUE_RE.findall(tgm.group(1))) if tgm else []

        phys_avg = (wmap.get("PhysicalMin", 0.0) + wmap.get("PhysicalMax", 0.0)) / 2.0
        fire_avg = (wmap.get("FireMin", 0.0) + wmap.get("FireMax", 0.0)) / 2.0
        cold_avg = (wmap.get("ColdMin", 0.0) + wmap.get("ColdMax", 0.0)) / 2.0
        light_avg = (wmap.get("LightningMin", 0.0) + wmap.get("LightningMax", 0.0)) / 2.0
        chaos_avg = (wmap.get("ChaosMin", 0.0) + wmap.get("ChaosMax", 0.0)) / 2.0
        ele_avg = fire_avg + cold_avg + light_avg
        avg_hit = phys_avg + ele_avg + chaos_avg
        aps = wmap.get("AttackRateBase", 0.0)
        crit = wmap.get("CritChanceBase", 0.0)
        reload_time = wmap.get("ReloadTimeBase", 0.0)
        spirit_base = wmap.get("Spirit", 0.0)
        if spirit_base <= 0 and "sceptre" in wtype.lower():
            # PoE2 权杖默认基础精魂 100
            spirit_base = 100.0

        base_dps = round(avg_hit * aps, 1) if aps > 0 and avg_hit > 0 else 0.0
        base_pdps = round(phys_avg * aps, 1) if aps > 0 and phys_avg > 0 else 0.0

        out.append({
            "baseType": cur_name,
            "weaponClass": wtype,
            "physAvg": round(phys_avg, 1),
            "eleAvg": round(ele_avg, 1),
            "chaosAvg": round(chaos_avg, 1),
            "avgHit": round(avg_hit, 1),
            "apsBase": round(aps, 2),
            "critBase": round(crit, 2),
            "reloadTimeBase": round(reload_time, 2),
            "spiritBase": round(spirit_base, 1),
            "baseDps": base_dps,
            "basePdps": base_pdps,
            "implicit": implicit,
            "socketLimit": sock_limit,
            "reqLevel": int(rmap.get("level", 0)),
            "reqStr": int(rmap.get("str", 0)),
            "reqDex": int(rmap.get("dex", 0)),
            "reqInt": int(rmap.get("int", 0)),
            "tags": tags,
        })
        cur_name = None
        buf = []

    for line in lines:
        m = _ENTRY_START_RE.match(line)
        if m:
            _flush()
            cur_name = m.group(1)
            continue
        if cur_name is not None:
            if line.strip() == "}":
                _flush()
            else:
                buf.append(line)
    _flush()
    return out


_WEAPON_BASES_CACHE: dict[str, Any] | None = None


def clear_weapon_bases_cache() -> None:
    """清空武器底材内存缓存（用于切换 POB2_ROOT 后重加载）。"""
    global _WEAPON_BASES_CACHE
    _WEAPON_BASES_CACHE = None


def load_weapon_bases() -> dict[str, Any]:
    """加载并缓存全部 PoB2 武器底材，返回 {byName, byClass}。
    仅在成功读取到底材时缓存，避免冷启动 POB2_ROOT 尚未就绪时永久锁死空结果。
    """
    global _WEAPON_BASES_CACHE
    if _WEAPON_BASES_CACHE is not None:
        return _WEAPON_BASES_CACHE

    bases_dir = os.path.join(pob2_src(), "Data", "Bases")
    by_name: dict[str, dict[str, Any]] = {}
    by_class: dict[str, list[dict[str, Any]]] = {}
    if not os.path.isdir(bases_dir):
        return {"byName": by_name, "byClass": by_class}

    for fn in _WEAPON_BASE_FILES:
        fp = os.path.join(bases_dir, fn)
        if not os.path.isfile(fp):
            continue
        for entry in _parse_weapon_lua_file(fp):
            name_k = entry["baseType"].strip().lower()
            by_name[name_k] = entry
            cls_k = entry["weaponClass"].strip().lower()
            by_class.setdefault(cls_k, []).append(entry)

    result = {"byName": by_name, "byClass": by_class}
    if by_name:
        _WEAPON_BASES_CACHE = result
    return result


def get_weapon_base(base_type: str) -> dict[str, Any] | None:
    if not base_type:
        return None
    idx = load_weapon_bases()
    return idx["byName"].get(str(base_type).strip().lower())


def _is_special_variant_base(entry: dict[str, Any]) -> bool:
    name = str(entry.get("baseType") or "").lower()
    if any(k in name for k in ("twink", "unreleased", "royale", "demigod", "test")):
        return True
    tags = set(entry.get("tags") or [])
    if "not_for_sale" in tags:
        return True
    return False


def recommend_weapon_bases(
    base_type: str,
    archetype: str,
    *,
    damage_preference: str = "any_dps",
    crit_important: bool = False,
    reload_important: bool = False,
    top_k: int = 4,
) -> list[dict[str, Any]]:
    """根据武器大类与 PoB2 实算武器分型，推荐最适合的同大类终局底材（按真实机制排序，不再盲目按 DPS 排）。"""
    cur = get_weapon_base(base_type)
    if not cur:
        return []
    cls_k = str(cur.get("weaponClass") or "").strip().lower()
    idx = load_weapon_bases()
    siblings = [b for b in idx["byClass"].get(cls_k, []) if not _is_special_variant_base(b)]
    if not siblings:
        return []

    # 仅保留终局高物等档底材（若同类存在 >= 55 级底材则优先在终局池内排序，同时确保原件自身参与对照）
    max_lvl = max((int(b.get("reqLevel") or 0) for b in siblings), default=0)
    lvl_floor = max(45, max_lvl - 22) if max_lvl >= 55 else 0
    pool = [b for b in siblings if int(b.get("reqLevel") or 0) >= lvl_floor or b["baseType"] == cur["baseType"]]
    if not pool:
        pool = siblings

    def _score_and_reason(b: dict[str, Any]) -> tuple[float, str]:
        crit_mult = (1.0 + max(0.0, (b["critBase"] - 5.0) * 0.08)) if crit_important else 1.0
        reload_mult = (0.85 / max(0.45, b["reloadTimeBase"])) if (reload_important and b["reloadTimeBase"] > 0) else 1.0
        imp = str(b.get("implicit") or "").strip()

        if archetype == "attack_hit_no_aps":
            # 不吃攻速：核心看单发均伤（纯物理看 physAvg，混合看 avgHit），绝不乘攻速！
            hit_val = b["physAvg"] if damage_preference == "physical" else b["avgHit"]
            score = hit_val * crit_mult * reload_mult
            parts = [f"单发均伤 {hit_val:.0f}"]
            if crit_important:
                parts.append(f"基础暴击 {b['critBase']}%")
            if reload_important and b["reloadTimeBase"] > 0:
                parts.append(f"装填 {b['reloadTimeBase']}s")
            if imp:
                parts.append(f"基底：{imp}")
            return round(score, 2), " · ".join(parts)

        if archetype == "attack_dps":
            dps_val = b["basePdps"] if damage_preference == "physical" else b["baseDps"]
            score = dps_val * crit_mult * reload_mult
            parts = [f"基础{'物理' if damage_preference == 'physical' else '总'}DPS {dps_val:.0f}", f"攻速 {b['apsBase']}"]
            if crit_important:
                parts.append(f"基础暴击 {b['critBase']}%")
            if imp:
                parts.append(f"基底：{imp}")
            return round(score, 2), " · ".join(parts)

        if archetype == "speed_tool":
            score = b["apsBase"] * 100.0 - (b["reqStr"] + b["reqDex"] + b["reqInt"]) * 0.05
            parts = [f"基础攻速 {b['apsBase']}", f"需求 Lv.{b['reqLevel']}"]
            if imp:
                parts.append(f"基底：{imp}")
            return round(score, 2), " · ".join(parts)

        if archetype == "stat_stick_weapon":
            # 挂件武器优先低属性需求（易穿戴）与功能性基底
            req_sum = b["reqStr"] + b["reqDex"] + b["reqInt"]
            score = 500.0 - req_sum + (40.0 if imp else 0.0)
            parts = [f"低属性需求(总计 {req_sum})"]
            if imp:
                parts.append(f"基底：{imp}")
            return round(score, 2), " · ".join(parts)

        # caster_or_minion：优先基础精魂与基底技能/机制
        score = b["spiritBase"] + (50.0 if imp else 0.0) + b["reqLevel"] * 0.3
        parts = []
        if b["spiritBase"] > 0:
            parts.append(f"基础精魂 {b['spiritBase']:.0f}")
        if imp:
            parts.append(f"基底：{imp}")
        if not parts:
            parts.append(f"终局底材 Lv.{b['reqLevel']}")
        return round(score, 2), " · ".join(parts)

    scored: list[dict[str, Any]] = []
    for b in pool:
        sc, rsn = _score_and_reason(b)
        scored.append({
            "baseType": b["baseType"],
            "weaponClass": b["weaponClass"],
            "avgHit": b["avgHit"],
            "physAvg": b["physAvg"],
            "apsBase": b["apsBase"],
            "critBase": b["critBase"],
            "baseDps": b["baseDps"],
            "basePdps": b["basePdps"],
            "reloadTimeBase": b["reloadTimeBase"],
            "spiritBase": b["spiritBase"],
            "implicit": b["implicit"],
            "reqLevel": b["reqLevel"],
            "isOriginalBase": b["baseType"].lower() == cur["baseType"].lower(),
            "score": sc,
            "reason": rsn,
        })

    scored.sort(key=lambda x: (-x["score"], -x["reqLevel"], x["baseType"]))
    top = scored[:top_k]
    if not any(x["isOriginalBase"] for x in top):
        orig_row = next((x for x in scored if x["isOriginalBase"]), None)
        if orig_row:
            top = top[: max(1, top_k - 1)] + [orig_row]
    return top
