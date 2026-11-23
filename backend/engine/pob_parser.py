"""PoB2 分享码解码与完整结构化解析（装备 + 珠宝 + 技能 + 天赋树大点/升华/双武器天赋）。"""
from __future__ import annotations

import base64
import re
import xml.etree.ElementTree as ET
import zlib
from typing import Any

from backend.engine.tree_index import summarize_allocated_nodes

_META_PREFIXES = (
    "Rarity:", "Unique ID:", "Item Level:", "Quality:", "Sockets:",
    "LevelReq:", "Implicits:", "Armour:", "Evasion:", "Energy Shield:",
    "Ward:", "Physical Damage:", "Elemental Damage:", "Chaos Damage:",
    "Critical Strike Chance:", "Attacks per Second:", "Weapon Range:",
    "Radius:", "Limited to:", "Variant:", "Selected Variant:",
    "Has Alt Variant:", "League:", "Prefix:", "Suffix:", "Crafted:",
    "Rune:", "Charm Slots:",
)

_VARIANT_TAG_RE = re.compile(r"\{variant:([0-9,]+)\}", re.I)
_BRACE_TAG_RE = re.compile(r"\{[^}]*\}")
_RUNE_SUFFIX_RE = re.compile(r"\s*\(rune\)\s*$", re.I)


def decode_pob(code: str) -> str:
    """支持 PoB2 分享码（Base64 + zlib）或直接传入 XML。"""
    s = (code or "").strip()
    if not s:
        raise ValueError("PoB 分享码为空")
    if s.startswith("<?xml") or s.startswith("<PathOfBuilding"):
        return s
    b = re.sub(r"\s+", "", s).replace("-", "+").replace("_", "/")
    b += "=" * ((4 - len(b) % 4) % 4)
    raw = base64.b64decode(b)
    try:
        return zlib.decompress(raw).decode("utf-8")
    except zlib.error:
        return zlib.decompress(raw, -zlib.MAX_WBITS).decode("utf-8")


def parse_item_text(raw_text: str) -> dict[str, Any]:
    lines = [l.strip() for l in (raw_text or "").splitlines() if l.strip()]
    rarity = "UNKNOWN"
    item_level = ""
    quality = ""
    corrupted = False
    socket_count = 0
    socketed_runes: list[str] = []
    header_lines: list[str] = []
    mods: list[str] = []
    in_mods = False
    active_variants: set[str] = set()

    for line in lines:
        if line.startswith("Selected Variant:") or line.startswith("Alt Variant"):
            val = line.split(":", 1)[1].strip()
            if val and val != "0":
                active_variants.add(val)

    def variant_active(line: str) -> bool:
        if not active_variants:
            return True
        m = _VARIANT_TAG_RE.search(line)
        if not m:
            return True
        allowed = {v.strip() for v in m.group(1).split(",") if v.strip()}
        return bool(allowed & active_variants)

    for line in lines:
        if line.startswith("Rarity:"):
            rarity = line.split(":", 1)[1].strip().upper()
            continue
        if line.startswith("Quality:"):
            quality = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Item Level:"):
            item_level = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Sockets:"):
            socket_count = len(re.findall(r"[A-Za-z]", line.split(":", 1)[1]))
            continue
        if line.startswith("Rune:"):
            rt = line.split(":", 1)[1].strip()
            if rt:
                socketed_runes.append(rt)
            continue
        if line == "Corrupted":
            corrupted = True
            continue
        if line.startswith("Implicits:"):
            in_mods = True
            continue

        if not in_mods:
            if not line.startswith(_META_PREFIXES) and not line.startswith("{") and len(header_lines) < 2:
                header_lines.append(line)
        else:
            if not variant_active(line):
                continue
            is_rune = "{rune}" in line.lower() or bool(_RUNE_SUFFIX_RE.search(line))
            clean = _RUNE_SUFFIX_RE.sub("", _BRACE_TAG_RE.sub("", line)).strip()
            if clean and not clean.startswith("Note:"):
                if is_rune:
                    socketed_runes.append(clean)
                else:
                    mods.append(clean)

    if len(header_lines) >= 2:
        name, base_type = header_lines[0], header_lines[1]
    elif len(header_lines) == 1:
        name = base_type = header_lines[0]
    else:
        name = base_type = ""

    return {
        "rarity": rarity,
        "name": name,
        "baseType": base_type,
        "quality": quality,
        "itemLevel": item_level,
        "corrupted": corrupted,
        "socketCount": socket_count,
        "socketedRunes": list(dict.fromkeys(socketed_runes)),
        "mods": mods,
        "rawText": (raw_text or "").strip(),
    }


def _is_jewel(slot_name: str, item: dict[str, Any]) -> bool:
    s = slot_name.lower()
    b = (item.get("baseType") or "").lower()
    return any(k in s for k in ("jewel", "socket")) or any(
        k in b for k in ("jewel", "diamond", "emerald", "ruby", "sapphire", "time-lost")
    )


def _is_flask_or_charm(slot_name: str) -> bool:
    s = slot_name.lower()
    return "flask" in s or "charm" in s


def _active_spec(root: ET.Element) -> ET.Element | None:
    tree_el = root.find("Tree")
    if tree_el is None:
        return None
    specs = tree_el.findall("Spec")
    if not specs:
        return None
    try:
        idx = int(tree_el.attrib.get("activeSpec") or "1")
    except ValueError:
        idx = 1
    return specs[idx - 1] if 1 <= idx <= len(specs) else specs[0]


def parse_pob_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text)

    # 1. 角色基础信息与 XML 自带面板
    build_el = root.find("Build")
    stats: dict[str, float] = {}
    if build_el is not None:
        for st in build_el.findall("PlayerStat"):
            k = st.attrib.get("stat")
            v = st.attrib.get("value")
            if k and v is not None:
                try:
                    stats[k] = float(v)
                except ValueError:
                    pass

    character = {
        "className": build_el.attrib.get("className", "Unknown") if build_el is not None else "Unknown",
        "ascendancy": build_el.attrib.get("ascendClassName", "None") if build_el is not None else "None",
        "level": int(build_el.attrib.get("level", "0") or 0) if build_el is not None else 0,
        "mainSocketGroup": int(build_el.attrib.get("mainSocketGroup", "1") or 1) if build_el is not None else 1,
        "stats": stats,
    }

    # 2. 装备与珠宝
    items_el = root.find("Items")
    items_by_id: dict[str, dict[str, Any]] = {}
    if items_el is not None:
        for it in items_el.findall("Item"):
            iid = it.attrib.get("id")
            if iid:
                items_by_id[iid] = parse_item_text(it.text or "")

    active_set = None
    if items_el is not None:
        aid = items_el.attrib.get("activeItemSet") or "1"
        active_set = items_el.find(f"ItemSet[@id='{aid}']") or items_el.find("ItemSet")

    equipped: list[dict[str, Any]] = []
    jewels: list[dict[str, Any]] = []
    flasks: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    slot_nodes = active_set.findall("Slot") if active_set is not None else (items_el.findall("Slot") if items_el is not None else [])
    for slot_el in slot_nodes:
        slot_name = slot_el.attrib.get("name") or ""
        iid = slot_el.attrib.get("itemId") or "0"
        if not slot_name or iid == "0" or iid not in items_by_id:
            continue
        used_ids.add(iid)
        entry = {"slot": slot_name, "itemId": iid, **items_by_id[iid]}
        if _is_jewel(slot_name, entry):
            jewels.append(entry)
        elif _is_flask_or_charm(slot_name):
            flasks.append(entry)
        else:
            equipped.append(entry)

    # 3. 天赋树解析（核心天赋 Keystone / 升华 / Notable / 双武器组天赋 + 天赋珠宝孔）
    spec = _active_spec(root)
    tree_summary: dict[str, Any] = {
        "treeVersion": "",
        "totalAllocated": 0,
        "keystones": [],
        "ascendancyNodes": [],
        "notables": [],
        "weaponSet1Notables": [],
        "weaponSet2Notables": [],
    }
    if spec is not None:
        raw_nodes = [n.strip() for n in (spec.attrib.get("nodes") or "").split(",") if n.strip()]
        ws1_el = spec.find("WeaponSet1")
        ws2_el = spec.find("WeaponSet2")
        ws1_ids = {n.strip() for n in (ws1_el.attrib.get("nodes", "") if ws1_el is not None else "").split(",") if n.strip()}
        ws2_ids = {n.strip() for n in (ws2_el.attrib.get("nodes", "") if ws2_el is not None else "").split(",") if n.strip()}
        tree_summary = summarize_allocated_nodes(
            raw_nodes,
            tree_version=spec.attrib.get("treeVersion"),
            weapon_set1_ids=ws1_ids,
            weapon_set2_ids=ws2_ids,
        )
        for sock in spec.findall("./Sockets/Socket"):
            iid = sock.attrib.get("itemId") or "0"
            nid = sock.attrib.get("nodeId") or ""
            if iid == "0" or iid not in items_by_id or iid in used_ids:
                continue
            used_ids.add(iid)
            jewels.append({
                "slot": f"Tree Jewel (Node {nid or '?'})",
                "nodeId": nid,
                "itemId": iid,
                **items_by_id[iid],
            })

    # 4. 技能连法
    skills_el = root.find("Skills")
    skill_groups: list[dict[str, Any]] = []
    if skills_el is not None:
        asid = skills_el.attrib.get("activeSkillSet") or "1"
        active_sk = skills_el.find(f"SkillSet[@id='{asid}']") or skills_el
        for idx, sk in enumerate(active_sk.findall("Skill"), start=1):
            if sk.attrib.get("enabled") == "false":
                continue
            slot = sk.attrib.get("slot") or ""
            ws = sk.attrib.get("weaponSet") or sk.attrib.get("activeWeaponSet") or ("2" if "swap" in slot.lower() else "")
            gems = []
            for gem in sk.findall("Gem"):
                if gem.attrib.get("enabled") == "false":
                    continue
                gems.append({
                    "name": gem.attrib.get("nameSpec") or gem.attrib.get("skillId") or gem.attrib.get("gemId") or "Unknown",
                    "skillId": gem.attrib.get("skillId") or "",
                    "level": int(gem.attrib.get("level") or 1),
                    "quality": int(gem.attrib.get("quality") or 0),
                    "weaponSet": gem.attrib.get("weaponSet") or "",
                })
            if gems:
                skill_groups.append({
                    "index": idx,
                    "isMain": idx == character["mainSocketGroup"],
                    "slot": slot,
                    "label": sk.attrib.get("label") or "",
                    "weaponSet": ws,
                    "gems": gems,
                })

    # 5. 双武器组概览
    set1 = [it for it in equipped if it["slot"].lower() in ("weapon 1", "weapon 2")]
    set2 = [it for it in equipped if it["slot"].lower() in ("weapon 1 swap", "weapon 2 swap")]

    notes_el = root.find("Notes")
    notes = (notes_el.text or "").strip()[:1200] if notes_el is not None else ""

    return {
        "character": character,
        "passiveTree": tree_summary,
        "weaponSets": {"hasDualWeaponSet": bool(set2), "set1": set1, "set2": set2},
        "equippedItems": equipped,
        "jewels": jewels,
        "flasksAndCharms": flasks,
        "skillGroups": skill_groups,
        "notes": notes,
    }


def parse_pob_code(code: str) -> dict[str, Any]:
    return parse_pob_xml(decode_pob(code))
