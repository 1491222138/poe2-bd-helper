"""PoB2 分享码解码与完整结构化解析（装备 + 珠宝 + 技能 + 天赋树大点/升华/双武器天赋）。"""
from __future__ import annotations

import base64
import binascii
import re
import xml.etree.ElementTree as ET
import zlib
from typing import Any

from backend.engine.tree_index import lookup_notable_by_name, summarize_allocated_nodes

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
    s = (code or "").replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").strip()
    if not s:
        raise ValueError("PoB 分享码为空")
    if s.startswith("<?xml") or s.startswith("<PathOfBuilding"):
        return s
    if re.match(r"^https?://", s, re.I):
        raise ValueError(
            "检测到粘贴的是网页链接而非 PoB2 分享码：请在 poe.ninja / pobb.in 页面点击「Copy PoB code」复制完整分享码，或使用浏览器扩展导入。"
        )
    b = re.sub(r"\s+", "", s).replace("-", "+").replace("_", "/")
    if not b or not re.fullmatch(r"[A-Za-z0-9+/=]+", b):
        preview = s[:40] + ("..." if len(s) > 40 else "")
        raise ValueError(
            f"PoB2 分享码格式无效（包含非法字符，长度 {len(s)}，片段：{preview!r}）：请粘贴完整的 PoB2 分享码或 XML。"
        )
    b = b.rstrip("=")
    if len(b) % 4 == 1:
        preview = s[:40] + ("..." if len(s) > 40 else "")
        raise ValueError(
            f"PoB2 分享码无效或被截断（有效字符 {len(b)}，片段：{preview!r}）：请重新复制完整的 PoB2 分享码。"
        )
    b += "=" * ((4 - len(b) % 4) % 4)
    try:
        raw = base64.b64decode(b, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(
            f"PoB2 分享码 Base64 解码失败（长度 {len(s)}）：请确认粘贴的是完整的 PoB2 分享码。"
        ) from exc
    try:
        return zlib.decompress(raw).decode("utf-8")
    except zlib.error:
        try:
            return zlib.decompress(raw, -zlib.MAX_WBITS).decode("utf-8")
        except (zlib.error, UnicodeDecodeError) as exc:
            raise ValueError(
                "PoB2 分享码解压失败：内容不是有效的 PoB2 导出数据，请重新从 PoB2 或 poe.ninja 复制分享码。"
            ) from exc
    except UnicodeDecodeError as exc:
        raise ValueError("PoB2 分享码解码后不是有效的 UTF-8 XML，请重新复制完整的 PoB2 分享码。") from exc


def encode_pob(xml_text: str) -> str:
    """将 PoB2 XML 编码为标准 PoB2 分享码（zlib + urlsafe base64）。"""
    compressed = zlib.compress(xml_text.encode("utf-8"), level=9)
    return base64.urlsafe_b64encode(compressed).decode("ascii")


def parse_item_text(raw_text: str) -> dict[str, Any]:
    lines = [l.strip() for l in (raw_text or "").splitlines() if l.strip()]
    rarity = "UNKNOWN"
    item_level = ""
    item_level_num = 0
    level_req = 0
    quality = ""
    corrupted = False
    socket_count = 0
    socketed_runes: list[str] = []
    rune_entries: list[str] = []
    header_lines: list[str] = []
    properties: dict[str, str] = {}
    mods: list[str] = []
    mod_details: list[dict[str, Any]] = []
    in_mods = False
    implicits_remaining = 0
    active_variants: set[str] = set()
    _KEEP_PROPS = (
        "Ward:",
        "Energy Shield:",
        "Evasion:",
        "Armour:",
        "Spirit:",
        "Charm Slots:",
        "Physical Damage:",
        "Elemental Damage:",
        "Critical Strike Chance:",
        "Attacks per Second:",
    )

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
            try:
                item_level_num = int(re.findall(r"\d+", item_level)[0])
            except Exception:
                item_level_num = 0
            continue
        if line.startswith("LevelReq:"):
            try:
                level_req = int(re.findall(r"\d+", line.split(":", 1)[1])[0])
            except Exception:
                level_req = 0
            continue
        if line.startswith("Sockets:"):
            socket_count = len(re.findall(r"[A-Za-z]", line.split(":", 1)[1]))
            continue
        if line.startswith("Rune:"):
            rt = line.split(":", 1)[1].strip()
            if rt and rt.lower() != "none":
                socketed_runes.append(rt)
                rune_entries.append(rt)
            continue
        if line == "Corrupted":
            corrupted = True
            continue
        if line.startswith("Implicits:"):
            in_mods = True
            try:
                implicits_remaining = int(re.findall(r"\d+", line.split(":", 1)[1])[0])
            except Exception:
                implicits_remaining = 0
            continue

        if not in_mods:
            matched_prop = False
            for pref in _KEEP_PROPS:
                if line.startswith(pref):
                    properties[pref[:-1]] = line.split(":", 1)[1].strip()
                    matched_prop = True
                    break
            if matched_prop:
                continue
            if not line.startswith(_META_PREFIXES) and not line.startswith("{") and len(header_lines) < 2:
                header_lines.append(line)
        else:
            if not variant_active(line):
                continue
            low = line.lower()
            is_rune = "{rune}" in low or bool(_RUNE_SUFFIX_RE.search(line))
            clean = _RUNE_SUFFIX_RE.sub("", _BRACE_TAG_RE.sub("", line)).strip()
            if clean and not clean.startswith("Note:"):
                was_implicit_slot = implicits_remaining > 0
                if implicits_remaining > 0:
                    implicits_remaining -= 1
                is_anoint = bool(re.match(r"^allocates\s+", clean, re.I))
                is_bonded = bool(re.match(r"^bonded\s*:", clean, re.I))
                if is_rune:
                    origin = "rune"
                    socketed_runes.append(clean)
                elif "{fractured}" in low:
                    origin = "fractured"
                    mods.append(clean)
                elif "{desecrated}" in low:
                    origin = "desecrated"
                    mods.append(clean)
                elif "{crafted}" in low:
                    origin = "crafted"
                    mods.append(clean)
                elif "{enchant}" in low or is_anoint:
                    origin = "enchant"
                    mods.append(clean)
                elif was_implicit_slot:
                    origin = "implicit"
                    mods.append(clean)
                else:
                    origin = "explicit"
                    mods.append(clean)
                detail: dict[str, Any] = {"text": clean, "origin": origin}
                if is_anoint:
                    detail["isAnoint"] = True
                if is_bonded:
                    detail["isBonded"] = True
                mod_details.append(detail)

    if len(header_lines) >= 2:
        name, base_type = header_lines[0], header_lines[1]
    elif len(header_lines) == 1:
        name = base_type = header_lines[0]
    else:
        name = base_type = ""

    out: dict[str, Any] = {
        "rarity": rarity,
        "name": name,
        "baseType": base_type,
        "quality": quality,
        "itemLevel": item_level_num if item_level_num > 0 else item_level,
        "corrupted": corrupted,
        "socketCount": socket_count,
        "socketedRunes": list(dict.fromkeys(socketed_runes)),
        "runeEntries": rune_entries,
        "mods": mods,
        "modDetails": mod_details,
        "rawText": (raw_text or "").strip(),
    }
    if level_req > 0:
        out["levelReq"] = level_req
    if properties:
        out["properties"] = properties
    return out


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
                def _opt_int(raw_v: str | None) -> int | None:
                    if not raw_v or raw_v == "nil":
                        return None
                    try:
                        return int(float(raw_v))
                    except ValueError:
                        return None
                gem_entry: dict[str, Any] = {
                    "name": gem.attrib.get("nameSpec") or gem.attrib.get("skillId") or gem.attrib.get("gemId") or "Unknown",
                    "skillId": gem.attrib.get("skillId") or "",
                    "level": int(gem.attrib.get("level") or 1),
                    "quality": int(gem.attrib.get("quality") or 0),
                    "weaponSet": gem.attrib.get("weaponSet") or "",
                }
                g_cnt = _opt_int(gem.attrib.get("count"))
                if g_cnt is not None and g_cnt > 1:
                    gem_entry["count"] = g_cnt
                g_part = _opt_int(gem.attrib.get("skillPart"))
                if g_part is not None and g_part > 0:
                    gem_entry["skillPart"] = g_part
                g_stage = _opt_int(gem.attrib.get("skillStageCount"))
                if g_stage is not None and g_stage > 0:
                    gem_entry["skillStageCount"] = g_stage
                gems.append(gem_entry)
            if gems:
                raw_mas = sk.attrib.get("mainActiveSkill") or sk.attrib.get("mainActiveSkillCalcs") or ""
                try:
                    main_active_idx = int(raw_mas) if raw_mas and raw_mas != "nil" else None
                except ValueError:
                    main_active_idx = None
                skill_groups.append({
                    "index": idx,
                    "isMain": idx == character["mainSocketGroup"],
                    "mainActiveSkill": main_active_idx,
                    "includeInFullDPS": (sk.attrib.get("includeInFullDPS") or "").lower() == "true",
                    "slot": slot,
                    "label": sk.attrib.get("label") or "",
                    "weaponSet": ws,
                    "gems": gems,
                })

    # 5. 双武器组概览与涂油大点解析
    set1 = [it for it in equipped if it["slot"].lower() in ("weapon 1", "weapon 2")]
    set2 = [it for it in equipped if it["slot"].lower() in ("weapon 1 swap", "weapon 2 swap")]
    use_second_ws = (
        (active_set.attrib.get("useSecondWeaponSet") if active_set is not None else None)
        or (items_el.attrib.get("useSecondWeaponSet") if items_el is not None else None)
        or ""
    ).lower() == "true"
    has_dual_gear = bool(set1) and bool(set2)
    has_dual_tree = bool(tree_summary.get("weaponSet1Notables")) and bool(tree_summary.get("weaponSet2Notables"))

    anointed: list[dict[str, Any]] = []
    seen_anoints: set[str] = set()
    alloc_re = re.compile(r"^Allocates\s+(.+)$", re.I)
    for it in equipped:
        for mod in it.get("mods") or []:
            m = alloc_re.match(mod.strip())
            if not m:
                continue
            aname = m.group(1).strip()
            if not aname or aname.lower() in seen_anoints:
                continue
            seen_anoints.add(aname.lower())
            info = lookup_notable_by_name(aname, tree_version=tree_summary.get("treeVersion") or None)
            anointed.append({
                "slot": it.get("slot") or "",
                "itemName": it.get("name") or it.get("baseType") or "",
                "name": info["name"] if info else aname,
                "stats": list(info.get("stats") or []) if info else [],
            })
    tree_summary["anointedNotables"] = anointed

    # 6. Config 实战勾选配置解析
    config_inputs: dict[str, Any] = {}
    cfg_el = root.find("Config")
    if cfg_el is not None:
        acid = cfg_el.attrib.get("activeConfigSet") or "1"
        active_cfg = cfg_el.find(f"ConfigSet[@id='{acid}']") or cfg_el.find("ConfigSet") or cfg_el
        for inp in active_cfg.findall("Input"):
            name = (inp.attrib.get("name") or "").strip()
            if not name or name.startswith("quest"):
                continue
            if "boolean" in inp.attrib:
                config_inputs[name] = inp.attrib["boolean"].lower() == "true"
            elif "number" in inp.attrib:
                try:
                    n = float(inp.attrib["number"])
                    config_inputs[name] = int(n) if n.is_integer() else n
                except ValueError:
                    config_inputs[name] = inp.attrib["number"]
            elif "string" in inp.attrib:
                config_inputs[name] = inp.attrib["string"]

    notes_el = root.find("Notes")
    notes = (notes_el.text or "").strip()[:1200] if notes_el is not None else ""

    return {
        "character": character,
        "passiveTree": tree_summary,
        "weaponSets": {
            "hasDualWeaponSet": bool(has_dual_gear or has_dual_tree),
            "hasDualWeaponGear": has_dual_gear,
            "hasDualWeaponTree": has_dual_tree,
            "activeWeaponSet": 2 if use_second_ws else 1,
            "set1": set1,
            "set2": set2,
        },
        "equippedItems": equipped,
        "jewels": jewels,
        "flasksAndCharms": flasks,
        "skillGroups": skill_groups,
        "config": config_inputs,
        "notes": notes,
    }


def extract_allocated_node_ids(xml_text: str) -> list[str]:
    """提取当前激活天赋树 <Spec nodes="..."> 中的全部已分配节点 ID（含升华大小点与起点）。"""
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []
    spec = _active_spec(root)
    if spec is None:
        return []
    return [n.strip() for n in (spec.attrib.get("nodes") or "").split(",") if n.strip()]


def parse_pob_code(code: str) -> dict[str, Any]:
    return parse_pob_xml(decode_pob(code))
