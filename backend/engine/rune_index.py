"""PoB2 装备插槽物品（符文 / 灵核 / 神像 / 碎片 / 遗物）元数据索引。

直接读取 PathOfBuilding-PoE2 自带的 Data/ModRunes.lua，提供每个镶嵌物在不同槽位上的：
  - 物品类型（Rune / SoulCore / Idol / CongealedMist / Augment 等）
  - 生效词条（mods）与 Bonded 词条（bondedMods）
  - 专属词池解锁（unlockPools，如 Marksman / Destruction / Chronomancy）
  - 绑定限制（isSocketBound / limit / levelReq）
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any

from backend.config import pob2_src

_ENTRY_START_RE = re.compile(r'^\t\["([^"]+)"\]\s*=\s*\{')
_SLOT_START_RE = re.compile(r'^\t\t\["([^"]+)"\]\s*=\s*\{')
_TYPE_RE = re.compile(r'type\s*=\s*"([^"]+)"')
_LEVEL_REQ_RE = re.compile(r"levelReq\s*=\s*(\d+)")
_QUOTED_LINE_RE = re.compile(r'^\s*"((?:[^"\\]|\\.)*)"\s*,?\s*$')
_RUNE_POOL_RE = re.compile(r"^can\s+roll\s+(.+?)\s+modifiers?$", re.I)


def _unescape_lua_str(s: str) -> str:
    return s.replace('\\"', '"').replace("\\n", " ").replace("\\\\", "\\").strip()


@lru_cache(maxsize=1)
def load_rune_index() -> dict[str, dict[str, dict[str, Any]]]:
    """解析 Data/ModRunes.lua -> {name_lower: {"_name": orig, "<slot_key>": {...}}}。"""
    path = os.path.join(pob2_src(), "Data", "ModRunes.lua")
    if not os.path.isfile(path):
        return {}

    index: dict[str, dict[str, Any]] = {}
    cur_name: str | None = None
    cur_slot: str | None = None
    cur_obj: dict[str, Any] | None = None
    in_bonded = False
    in_skip_block = False
    skip_depth = 0

    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for raw_line in f:
                line = raw_line.rstrip("\r\n")
                m_entry = _ENTRY_START_RE.match(line)
                if m_entry:
                    cur_name = _unescape_lua_str(m_entry.group(1))
                    cur_slot = None
                    cur_obj = None
                    in_bonded = False
                    in_skip_block = False
                    index[cur_name.lower()] = {"_name": cur_name}
                    continue

                if cur_name is None:
                    continue

                m_slot = _SLOT_START_RE.match(line)
                if m_slot:
                    cur_slot = m_slot.group(1).strip().lower()
                    cur_obj = {
                        "type": "Rune",
                        "mods": [],
                        "bondedMods": [],
                        "unlockPools": [],
                        "isSocketBound": False,
                        "levelReq": 0,
                    }
                    in_bonded = False
                    in_skip_block = False
                    index[cur_name.lower()][cur_slot] = cur_obj
                    continue

                if cur_obj is None:
                    continue

                stripped = line.strip()
                if in_skip_block:
                    skip_depth += stripped.count("{") - stripped.count("}")
                    if skip_depth <= 0:
                        in_skip_block = False
                    continue

                if stripped.startswith("bonded = {"):
                    in_bonded = True
                    continue

                if in_bonded:
                    if stripped.startswith("},") or stripped == "}":
                        in_bonded = False
                        continue
                    if stripped.startswith("statOrder"):
                        continue
                    mq = _QUOTED_LINE_RE.match(line)
                    if mq:
                        txt = _unescape_lua_str(mq.group(1))
                        if txt:
                            cur_obj["bondedMods"].append(txt)
                    continue

                if stripped.startswith(("tradeHashes", "statOrder")):
                    if "{" in stripped and stripped.count("{") > stripped.count("}"):
                        in_skip_block = True
                        skip_depth = stripped.count("{") - stripped.count("}")
                    continue

                if stripped.startswith("type ="):
                    mt = _TYPE_RE.search(stripped)
                    if mt:
                        cur_obj["type"] = mt.group(1)
                    continue

                if "isSocketBound = true" in stripped:
                    cur_obj["isSocketBound"] = True
                    continue

                if stripped.startswith("levelReq"):
                    ml = _LEVEL_REQ_RE.search(stripped)
                    if ml:
                        cur_obj["levelReq"] = int(ml.group(1))
                    continue

                if stripped == "},":
                    cur_slot = None
                    cur_obj = None
                    continue

                mq = _QUOTED_LINE_RE.match(line)
                if mq:
                    txt = _unescape_lua_str(mq.group(1))
                    if txt:
                        cur_obj["mods"].append(txt)
                        pm = _RUNE_POOL_RE.match(txt)
                        if pm and pm.group(1) not in cur_obj["unlockPools"]:
                            cur_obj["unlockPools"].append(pm.group(1))
    except Exception:
        return {}

    return index


def _candidate_slot_keys(slot: str, base_type: str = "") -> list[str]:
    s = (slot or "").strip().lower()
    b = (base_type or "").strip().lower()
    keys: list[str] = []

    if "body" in s or "chest" in s:
        keys.extend(["body armour", "armour"])
    elif "helmet" in s:
        keys.extend(["helmet", "armour"])
    elif "gloves" in s:
        keys.extend(["gloves", "armour"])
    elif "boots" in s:
        keys.extend(["boots", "armour"])
    elif "shield" in s or "buckler" in b or "shield" in b:
        keys.extend(["shield", "armour"])
    elif "focus" in s or "focus" in b:
        keys.extend(["focus", "caster", "armour"])
    elif "ring" in s:
        keys.extend(["ring", "jewellery"])
    elif "amulet" in s:
        keys.extend(["amulet", "jewellery"])
    elif "belt" in s:
        keys.extend(["belt", "jewellery"])
    elif "weapon" in s:
        if "wand" in b:
            keys.extend(["wand", "caster", "weapon"])
        elif "sceptre" in b:
            keys.extend(["sceptre", "caster", "weapon"])
        elif "quarterstaff" in b or "warstaff" in b:
            keys.extend(["quarterstaff", "martial weapon", "weapon"])
        elif "staff" in b:
            keys.extend(["staff", "caster", "weapon"])
        elif "bow" in b and "crossbow" not in b:
            keys.extend(["bow", "martial weapon", "weapon"])
        elif "crossbow" in b:
            keys.extend(["crossbow", "martial weapon", "weapon"])
        elif "spear" in b:
            keys.extend(["spear", "martial weapon", "weapon"])
        elif "talisman" in b:
            keys.extend(["talisman", "weapon"])
        elif "shield" in b or "buckler" in b:
            keys.extend(["shield", "armour"])
        elif "focus" in b:
            keys.extend(["focus", "caster", "armour"])
        else:
            keys.extend(["martial weapon", "weapon", "caster"])

    keys.append("all")
    return keys


def rune_type_label(raw_type: str, rune_name: str = "") -> str:
    t = (raw_type or "").strip().lower()
    n = (rune_name or "").strip().lower()
    if "legacy of " in n:
        return "遗物"
    if t == "soulcore" or "soul core" in n:
        return "灵核"
    if t == "idol" or "idol" in n:
        return "神像"
    if t == "congealedmist" or "shard" in n:
        return "碎片"
    if "talisman" in n or t == "talisman":
        return "护符"
    return "符文"


def lookup_socketed_item(
    name: str,
    *,
    slot: str = "",
    base_type: str = "",
) -> dict[str, Any] | None:
    """按镶嵌物英文名 + 装备槽位/基底查询其在 PoB2 ModRunes.lua 中的精确效果与类型。"""
    key = (name or "").strip().lower()
    if not key:
        return None
    idx = load_rune_index()
    entry = idx.get(key)
    if not entry:
        return None

    chosen: dict[str, Any] | None = None
    matched_slot = ""
    for sk in _candidate_slot_keys(slot, base_type):
        cand = entry.get(sk)
        if isinstance(cand, dict):
            chosen = cand
            matched_slot = sk
            break

    if chosen is None:
        for k, v in entry.items():
            if k != "_name" and isinstance(v, dict):
                chosen = v
                matched_slot = k
                break

    if chosen is None:
        return None

    orig_name = str(entry.get("_name") or name)
    rtype = str(chosen.get("type") or "Rune")
    return {
        "name": orig_name,
        "runeType": rtype,
        "runeTypeLabel": rune_type_label(rtype, orig_name),
        "matchedSlot": matched_slot,
        "mods": list(chosen.get("mods") or []),
        "bondedMods": list(chosen.get("bondedMods") or []),
        "unlockPools": list(chosen.get("unlockPools") or []),
        "isSocketBound": bool(chosen.get("isSocketBound")),
        "levelReq": int(chosen.get("levelReq") or 0),
    }
