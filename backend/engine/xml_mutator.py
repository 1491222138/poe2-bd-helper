"""纯函数 PoB2 XML 变异器：为脱装消融、双码试穿、换天赋、换技能、单词缀剥离提供可单测的 XML 生成。"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from typing import Optional

_MOD_TAG_PREFIX = re.compile(r"^(?:\{[^}]*\})+")
_ATTR_LINE = re.compile(
    r"^(?:\+\d+\s+to\s+(?:(?:Strength|Dexterity|Intelligence)(?:\s+and\s+(?:Strength|Dexterity|Intelligence))?|all Attributes)"
    r"|\d+%\s+increased\s+(?:Attributes|Strength|Dexterity|Intelligence))$",
    re.I,
)
_SPIRIT_LINE = re.compile(r"^(?:\+\d+\s+to\s+Spirit|\d+%\s+increased\s+Spirit)$", re.I)
_GRANTS_SKILL_LINE = re.compile(
    r"^(?:\{[^}]*\})*Grants\s+Skill:\s*(?:Level\s+(\d+)\s+)?(.+?)(?:\s*\((?:implicit|crafted|fractured|desecrated)\))?$",
    re.I,
)
_IMPLICITS_HEADER = re.compile(r"^Implicits:\s*(\d+)\s*$")


def extract_granted_skill(mod_line: str) -> tuple[str, int | None] | None:
    """从 'Grants Skill: Level 18 Eternal Rage' 中提取 (skill_name, level)。"""
    cleaned = re.sub(r"\{[^}]*\}", "", (mod_line or "").strip()).strip()
    m = _GRANTS_SKILL_LINE.match(cleaned)
    if not m:
        return None
    lv = int(m.group(1)) if m.group(1) else None
    name = m.group(2).strip()
    return (name, lv) if name else None


def _norm_skill_match_name(name: str) -> str:
    s = re.sub(r"\s+", " ", (name or "").strip().lower())
    # PoB2 基地写 "Skeletal Warrior Minion"，宝石名写 "Skeletal Warrior"
    s = re.sub(r"\s+minion$", "", s)
    return s


def _active_item_set(root: ET.Element) -> Optional[ET.Element]:
    items = root.find("Items")
    if items is None:
        return None
    aid = items.attrib.get("activeItemSet") or "1"
    return items.find(f"ItemSet[@id='{aid}']") or items.find("ItemSet")


def _active_spec(root: ET.Element) -> Optional[ET.Element]:
    tree = root.find("Tree")
    if tree is None:
        return None
    specs = tree.findall("Spec")
    if not specs:
        return None
    try:
        idx = int(tree.attrib.get("activeSpec") or "1")
    except ValueError:
        idx = 1
    return specs[idx - 1] if 1 <= idx <= len(specs) else specs[0]


def _next_item_id(items_el: ET.Element) -> str:
    max_id = 0
    for it in items_el.findall("Item"):
        try:
            max_id = max(max_id, int(it.attrib.get("id") or "0"))
        except ValueError:
            pass
    return str(max_id + 1)


def _find_slot_el(root: ET.Element, slot_name: str) -> Optional[ET.Element]:
    iset = _active_item_set(root)
    container = iset if iset is not None else root.find("Items")
    if container is None:
        return None
    for s in container.findall("Slot"):
        if s.attrib.get("name") == slot_name:
            return s
    return None


def _item_el_by_id(root: ET.Element, item_id: str) -> Optional[ET.Element]:
    items = root.find("Items")
    if items is None:
        return None
    for it in items.findall("Item"):
        if it.attrib.get("id") == item_id:
            return it
    return None


def ablate_slot(xml: str, slot_name: str) -> str:
    """脱掉指定槽位的装备（itemId 置 0）。"""
    root = ET.fromstring(xml)
    slot_el = _find_slot_el(root, slot_name)
    if slot_el is None:
        raise ValueError(f"找不到槽位：{slot_name}")
    slot_el.set("itemId", "0")
    return ET.tostring(root, encoding="unicode")


def ablate_tree_jewel(xml: str, node_id: str) -> str:
    """拔掉指定天赋树珠宝孔上的珠宝（itemId 置 0）。"""
    root = ET.fromstring(xml)
    spec = _active_spec(root)
    if spec is None:
        raise ValueError("XML 中缺少 <Spec> 节点")
    for sock in spec.findall("./Sockets/Socket"):
        if sock.attrib.get("nodeId") == str(node_id):
            sock.set("itemId", "0")
            return ET.tostring(root, encoding="unicode")
    raise ValueError(f"找不到天赋树珠宝孔：nodeId={node_id}")


def dependency_lines(raw_item_text: str) -> list[str]:
    """提取可能触发“穿戴门槛断裂”的三属性（含全属性/双属性/百分比属性）与精魂词缀。"""
    out = []
    for line in (raw_item_text or "").splitlines():
        s = _MOD_TAG_PREFIX.sub("", line.strip()).strip()
        if not s:
            continue
        if _ATTR_LINE.match(s) or _SPIRIT_LINE.match(s):
            out.append(s)
    return out


def inject_threshold_compensation(
    xml: str,
    *,
    str_deficit: int = 0,
    dex_deficit: int = 0,
    int_deficit: int = 0,
    spirit_deficit: int = 0,
) -> str:
    """往 <ConfigSet> 注入精确的属性/精魂门槛缺口补偿词缀，用于测算补足穿戴门槛后单件换装的真实潜力。"""
    mods: list[str] = []
    if str_deficit > 0:
        mods.append(f"+{int(str_deficit)} to Strength")
    if dex_deficit > 0:
        mods.append(f"+{int(dex_deficit)} to Dexterity")
    if int_deficit > 0:
        mods.append(f"+{int(int_deficit)} to Intelligence")
    if spirit_deficit > 0:
        mods.append(f"+{int(spirit_deficit)} to Spirit")
    if not mods:
        return xml
    return inject_custom_mods(xml, mods, title="WorkbenchThresholdComp")


def add_item_mod(xml: str, item_id: str, mod_line: str) -> str:
    """往指定 itemId 的词缀区追加一条词缀。"""
    root = ET.fromstring(xml)
    it = _item_el_by_id(root, item_id)
    if it is None:
        raise ValueError(f"找不到 itemId={item_id}")
    text = (it.text or "").rstrip("\n")
    it.text = f"\n{text.strip()}\n{mod_line}\n"
    return ET.tostring(root, encoding="unicode")


def remove_item_mod(xml: str, item_id: str, mod_line: str) -> str:
    """从指定 itemId 中精确移除一条词缀（若位于 Implicits 段内，同步将 Implicits 计数减 1，防止显性词缀误移入基底）。"""
    root = ET.fromstring(xml)
    it = _item_el_by_id(root, item_id)
    if it is None:
        raise ValueError(f"找不到 itemId={item_id}")
    lines = (it.text or "").splitlines()
    removed = False
    removed_in_implicits = False
    implicits_header_idx: int | None = None
    implicits_remaining = 0
    kept: list[str] = []
    target = mod_line.strip()
    for line in lines:
        s = line.strip()
        im = _IMPLICITS_HEADER.match(s)
        if im:
            implicits_header_idx = len(kept)
            implicits_remaining = int(im.group(1))
            kept.append(line)
            continue
        in_imp_section = implicits_header_idx is not None and implicits_remaining > 0 and bool(s)
        if not removed and target in line:
            removed = True
            if in_imp_section:
                removed_in_implicits = True
                implicits_remaining -= 1
            continue
        if in_imp_section:
            implicits_remaining -= 1
        kept.append(line)
    if not removed:
        raise ValueError(f"itemId={item_id} 中未找到词缀：{mod_line}")
    if removed_in_implicits and implicits_header_idx is not None:
        hdr = kept[implicits_header_idx]
        m = _IMPLICITS_HEADER.match(hdr.strip())
        if m:
            new_cnt = max(0, int(m.group(1)) - 1)
            prefix = hdr[: len(hdr) - len(hdr.lstrip())]
            kept[implicits_header_idx] = f"{prefix}Implicits: {new_cnt}"
    it.text = "\n".join(kept) + "\n"
    return ET.tostring(root, encoding="unicode")


def _disable_granted_skill_groups_in_root(
    root: ET.Element,
    *,
    skill_names: list[str],
    item_id: str = "",
    slot_name: str = "",
) -> int:
    """在 <Skills> 中禁用由装备基底赋予的对应技能组（包含 source='Item:<id>:...' 以及 poe.ninja 导出的同名无 source 技能组）。"""
    container = _active_skill_container(root)
    if container is None:
        return 0
    targets = {_norm_skill_match_name(n) for n in skill_names if n}
    if not targets:
        return 0
    slot_l = (slot_name or "").strip().lower()
    item_src_prefix = f"item:{item_id}:" if item_id else ""
    disabled_cnt = 0

    for sk in container.findall("Skill"):
        if sk.attrib.get("enabled") == "false":
            continue
        src = (sk.attrib.get("source") or "").strip().lower()
        sk_slot = (sk.attrib.get("slot") or "").strip().lower()
        gems = sk.findall("Gem")
        if not gems:
            continue
        gem_names = {
            _norm_skill_match_name(g.attrib.get("nameSpec") or g.attrib.get("skillId") or "")
            for g in gems
        }
        primary_name = _norm_skill_match_name(gems[0].attrib.get("nameSpec") or gems[0].attrib.get("skillId") or "")

        matched = False
        if item_src_prefix and src.startswith(item_src_prefix):
            matched = bool(gem_names & targets)
        elif slot_l and sk_slot == slot_l and (gem_names & targets):
            matched = True
        elif not src and (primary_name in targets):
            # poe.ninja 导出的 PoB2 会把装备自带技能及其连上的辅助宝石放进无 source 的 <Skill> 组
            matched = True

        if matched:
            sk.set("enabled", "false")
            disabled_cnt += 1

    return disabled_cnt


def ablate_item_granted_skill(xml: str, item_id: str, slot_name: str, mod_line: str) -> str:
    """剥离装备基底自带技能：既从 <Item> 删除 Grants Skill 行，又同步禁用 <Skills> 中对应的自带技能组（含连上的辅助宝石）。"""
    mut = remove_item_mod(xml, item_id, mod_line)
    parsed_sk = extract_granted_skill(mod_line)
    if not parsed_sk:
        return mut
    skill_name, _ = parsed_sk
    root = ET.fromstring(mut)
    _disable_granted_skill_groups_in_root(
        root,
        skill_names=[skill_name],
        item_id=item_id,
        slot_name=slot_name,
    )
    return ET.tostring(root, encoding="unicode")


def ablate_slot_with_granted_skills(
    xml: str,
    slot_name: str,
    granted_skill_names: list[str],
    item_id: str = "",
) -> str:
    """脱掉指定槽位装备，并同步禁用该装备赋予的活跃自带技能组，避免 poe.ninja 重复导出的无 source 技能组残留造成贡献低估。"""
    mut = ablate_slot(xml, slot_name)
    if not granted_skill_names:
        return mut
    root = ET.fromstring(mut)
    _disable_granted_skill_groups_in_root(
        root,
        skill_names=granted_skill_names,
        item_id=item_id,
        slot_name=slot_name,
    )
    return ET.tostring(root, encoding="unicode")


def strip_item_runes(xml: str, item_id: str) -> str:
    """移除指定装备上的全部符文声明（Rune: 行与 {rune} 行），并同步维护 Implicits 计数，用于 PoB2 引擎实算符文插槽真实贡献。"""
    root = ET.fromstring(xml)
    it = _item_el_by_id(root, item_id)
    if it is None:
        raise ValueError(f"找不到 itemId={item_id}")
    lines = (it.text or "").splitlines()
    kept: list[str] = []
    removed_any = False
    implicits_header_idx: int | None = None
    implicits_cnt = 0
    implicits_remaining = 0
    removed_in_implicits = 0
    for line in lines:
        s = line.strip()
        low = s.lower()
        if s.startswith("Rune:"):
            removed_any = True
            continue
        im = _IMPLICITS_HEADER.match(s)
        if im:
            implicits_header_idx = len(kept)
            implicits_cnt = int(im.group(1))
            implicits_remaining = implicits_cnt
            kept.append(line)
            continue
        in_imp = implicits_header_idx is not None and implicits_remaining > 0 and bool(s)
        if "{rune}" in low or low.endswith("(rune)"):
            removed_any = True
            if in_imp:
                removed_in_implicits += 1
                implicits_remaining -= 1
            continue
        if in_imp:
            implicits_remaining -= 1
        kept.append(line)
    if not removed_any:
        raise ValueError(f"itemId={item_id} 未检测到可剥离的符文行")
    if removed_in_implicits > 0 and implicits_header_idx is not None:
        hdr = kept[implicits_header_idx]
        prefix = hdr[: len(hdr) - len(hdr.lstrip())]
        kept[implicits_header_idx] = f"{prefix}Implicits: {max(0, implicits_cnt - removed_in_implicits)}"
    it.text = "\n".join(kept) + "\n"
    return ET.tostring(root, encoding="unicode")


def ablate_item_rune_at(xml: str, item_id: str, rune_index: int) -> str:
    """精确移除指定装备的第 rune_index (0-based) 颗插槽镶嵌物（符文/灵核/神像/碎片/遗物）。

    PoB2 0.4/0.5+ 格式带 `Rune: <name>` 行时，将该条改为 `Rune: None` 并剥离 `{rune}` 文本行
    （同步维护 Implicits 计数），由 PoB2 引擎根据剩余 `Rune:` 行原生重建 runeModLines；
    无 `Rune:` 行的旧格式则精准移除第 rune_index 条 `{rune}` 行。
    """
    root = ET.fromstring(xml)
    it = _item_el_by_id(root, item_id)
    if it is None:
        raise ValueError(f"找不到 itemId={item_id}")
    lines = (it.text or "").splitlines()
    has_native_runes = any(
        l.strip().startswith("Rune:") and l.strip().split(":", 1)[1].strip().lower() not in ("", "none")
        for l in lines
    )

    kept: list[str] = []
    implicits_header_idx: int | None = None
    implicits_cnt = 0
    implicits_remaining = 0
    removed_in_implicits = 0
    removed = False

    if has_native_runes:
        seen_rune_idx = 0
        for line in lines:
            s = line.strip()
            low = s.lower()
            if s.startswith("Rune:"):
                val = s.split(":", 1)[1].strip()
                if val and val.lower() != "none":
                    if seen_rune_idx == rune_index:
                        prefix = line[: len(line) - len(line.lstrip())]
                        kept.append(f"{prefix}Rune: None")
                        removed = True
                    else:
                        kept.append(line)
                    seen_rune_idx += 1
                else:
                    kept.append(line)
                continue
            im = _IMPLICITS_HEADER.match(s)
            if im:
                implicits_header_idx = len(kept)
                implicits_cnt = int(im.group(1))
                implicits_remaining = implicits_cnt
                kept.append(line)
                continue
            in_imp = implicits_header_idx is not None and implicits_remaining > 0 and bool(s)
            if "{rune}" in low or low.endswith("(rune)"):
                if in_imp:
                    removed_in_implicits += 1
                    implicits_remaining -= 1
                continue
            if in_imp:
                implicits_remaining -= 1
            kept.append(line)
    else:
        seen_line_idx = 0
        for line in lines:
            s = line.strip()
            low = s.lower()
            im = _IMPLICITS_HEADER.match(s)
            if im:
                implicits_header_idx = len(kept)
                implicits_cnt = int(im.group(1))
                implicits_remaining = implicits_cnt
                kept.append(line)
                continue
            in_imp = implicits_header_idx is not None and implicits_remaining > 0 and bool(s)
            if "{rune}" in low or low.endswith("(rune)"):
                if seen_line_idx == rune_index:
                    removed = True
                    if in_imp:
                        removed_in_implicits += 1
                        implicits_remaining -= 1
                    seen_line_idx += 1
                    continue
                seen_line_idx += 1
            if in_imp:
                implicits_remaining -= 1
            kept.append(line)

    if not removed:
        raise ValueError(f"itemId={item_id} 未找到第 {rune_index} 颗插槽物品")
    if removed_in_implicits > 0 and implicits_header_idx is not None:
        hdr = kept[implicits_header_idx]
        prefix = hdr[: len(hdr) - len(hdr.lstrip())]
        kept[implicits_header_idx] = f"{prefix}Implicits: {max(0, implicits_cnt - removed_in_implicits)}"
    it.text = "\n".join(kept) + "\n"
    return ET.tostring(root, encoding="unicode")


def ablate_passive_nodes(
    xml: str,
    node_ids: list[str],
    *,
    granted_skill_names: list[str] | None = None,
) -> str:
    """从当前激活天赋树中移除指定 nodeId 集合（用于升华天赋与关键大点消融），并同步禁用对应天赋赋予的自带技能组。"""
    remove_set = {str(n).strip() for n in (node_ids or []) if str(n).strip()}
    if not remove_set:
        raise ValueError("node_ids 不能为空")
    root = ET.fromstring(xml)
    spec = _active_spec(root)
    if spec is None:
        raise ValueError("XML 中缺少 <Spec> 节点")
    cur_nodes = [n.strip() for n in (spec.attrib.get("nodes") or "").split(",") if n.strip()]
    new_nodes = [n for n in cur_nodes if n not in remove_set]
    if len(new_nodes) == len(cur_nodes):
        raise ValueError(f"当前天赋树未分配目标节点：{sorted(remove_set)}")
    spec.set("nodes", ",".join(new_nodes))

    for ws_tag in ("WeaponSet1", "WeaponSet2"):
        ws_el = spec.find(ws_tag)
        if ws_el is not None and ws_el.attrib.get("nodes"):
            ws_nodes = [n.strip() for n in ws_el.attrib["nodes"].split(",") if n.strip() and n.strip() not in remove_set]
            ws_el.set("nodes", ",".join(ws_nodes))

    if granted_skill_names:
        _disable_granted_skill_groups_in_root(root, skill_names=granted_skill_names)

    return ET.tostring(root, encoding="unicode")


def compensate_ablation(xml: str, slot_name: str) -> Optional[str]:
    """若被脱装备带三属性/精魂，把这些门槛词条临时挂到另一件安全装备上，构造门槛补偿对照 XML。"""
    root = ET.fromstring(xml)
    slot_el = _find_slot_el(root, slot_name)
    if slot_el is None:
        return None
    removed_id = slot_el.attrib.get("itemId") or "0"
    if removed_id == "0":
        return None
    removed_item = _item_el_by_id(root, removed_id)
    deps = dependency_lines(removed_item.text or "") if removed_item is not None else []
    if not deps:
        return None

    # 脱掉该槽位
    slot_el.set("itemId", "0")

    # 找安全宿主槽位（非自身、非药剂/护符、非腐化、非卡兰德）
    iset = _active_item_set(root)
    container = iset if iset is not None else root.find("Items")
    if container is None:
        return None
    host_item: Optional[ET.Element] = None
    for s in container.findall("Slot"):
        sname = s.attrib.get("name") or ""
        iid = s.attrib.get("itemId") or "0"
        if sname == slot_name or iid == "0":
            continue
        if sname.lower().startswith(("flask", "charm")):
            continue
        cand = _item_el_by_id(root, iid)
        if cand is None:
            continue
        txt = cand.text or ""
        if "Corrupted" in txt or "Kalandra" in txt:
            continue
        host_item = cand
        break

    if host_item is None:
        return None
    host_item.text = f"\n{(host_item.text or '').strip()}\n" + "\n".join(deps) + "\n"
    return ET.tostring(root, encoding="unicode")


def swap_slot_item(cur_xml: str, slot_name: str, new_item_text: str) -> str:
    """把指定槽位替换成新装备（兼容原来为空槽位或已穿装备两种情况）。"""
    root = ET.fromstring(cur_xml)
    items_el = root.find("Items")
    if items_el is None:
        raise ValueError("XML 中缺少 <Items> 节点")
    new_id = _next_item_id(items_el)
    new_el = ET.SubElement(items_el, "Item", {"id": new_id})
    new_el.text = f"\n{new_item_text.strip()}\n"

    slot_el = _find_slot_el(root, slot_name)
    if slot_el is not None:
        slot_el.set("itemId", new_id)
    else:
        iset = _active_item_set(root)
        container = iset if iset is not None else items_el
        ET.SubElement(container, "Slot", {"name": slot_name, "itemId": new_id})
    return ET.tostring(root, encoding="unicode")


def swap_tree_jewel(cur_xml: str, node_id: str, new_item_text: str) -> tuple[str, bool]:
    """将指定天赋树珠宝孔替换成新珠宝；若当前天赋尚未点到该孔，尝试借用已点出的空闲/可用珠宝孔。"""
    root = ET.fromstring(cur_xml)
    items_el = root.find("Items")
    spec = _active_spec(root)
    if items_el is None or spec is None:
        return cur_xml, False

    allocated = {n.strip() for n in (spec.attrib.get("nodes") or "").split(",") if n.strip()}
    sockets_el = spec.find("Sockets")
    if sockets_el is None:
        sockets_el = ET.SubElement(spec, "Sockets")

    existing_sockets = sockets_el.findall("Socket")
    target_sock: Optional[ET.Element] = None

    # 1. 优先命中同 nodeId（且已点出）
    if node_id and node_id in allocated:
        target_sock = next((s for s in existing_sockets if s.attrib.get("nodeId") == node_id), None)
        if target_sock is None:
            target_sock = ET.SubElement(sockets_el, "Socket", {"nodeId": node_id, "itemId": "0"})

    # 2. 若我方未点到该孔，对于非范围限定珠宝尝试借用我方已点出的空孔（或首个可用珠宝孔）测算收益
    is_radius_jewel = "Radius:" in (new_item_text or "") or "Time-Lost" in (new_item_text or "")
    if target_sock is None and not is_radius_jewel:
        alloc_socks = [s for s in existing_sockets if (s.attrib.get("nodeId") or "") in allocated]
        empty_alloc = next((s for s in alloc_socks if (s.attrib.get("itemId") or "0") == "0"), None)
        target_sock = empty_alloc or (alloc_socks[0] if alloc_socks else None)

    if target_sock is None:
        return cur_xml, False

    new_id = _next_item_id(items_el)
    new_el = ET.SubElement(items_el, "Item", {"id": new_id})
    new_el.text = f"\n{new_item_text.strip()}\n"
    target_sock.set("itemId", new_id)
    return ET.tostring(root, encoding="unicode"), True


def swap_tree(my_xml: str, target_xml: str) -> str:
    """把目标 BD 的天赋树（含双武器组天赋点、升华）套到当前角色上，同时保留玩家自己的珠宝配置。"""
    my_root = ET.fromstring(my_xml)
    tgt_root = ET.fromstring(target_xml)
    my_spec = _active_spec(my_root)
    tgt_spec = _active_spec(tgt_root)
    if my_spec is None or tgt_spec is None:
        raise ValueError("缺少天赋树 <Spec> 节点")

    for attr in ("nodes", "masteryEffects", "treeVersion", "classId", "ascendClassId", "secondaryAscendClassId", "ascendancyInternalId", "classInternalId"):
        if attr in tgt_spec.attrib:
            my_spec.set(attr, tgt_spec.attrib[attr])

    # 同步 URL 与 PoE2 双武器组天赋点（WeaponSet1 / WeaponSet2），不碰 Sockets（保留玩家珠宝）
    for tag in ("URL", "WeaponSet1", "WeaponSet2"):
        old = my_spec.find(tag)
        if old is not None:
            my_spec.remove(old)
        tgt_child = tgt_spec.find(tag)
        if tgt_child is not None:
            my_spec.append(copy.deepcopy(tgt_child))

    my_build = my_root.find("Build")
    tgt_build = tgt_root.find("Build")
    if (
        my_build is not None
        and tgt_build is not None
        and my_build.attrib.get("className") == tgt_build.attrib.get("className")
        and tgt_build.attrib.get("ascendClassName")
    ):
        my_build.set("ascendClassName", tgt_build.attrib["ascendClassName"])

    return ET.tostring(my_root, encoding="unicode")


def swap_skills(my_xml: str, target_xml: str) -> str:
    """把目标 BD 的技能连法与宝石等级/品质套到当前角色上（用于 0 成本/低成本改动测算）。"""
    my_root = ET.fromstring(my_xml)
    tgt_root = ET.fromstring(target_xml)
    tgt_skills = tgt_root.find("Skills")
    if tgt_skills is None:
        raise ValueError("目标构筑缺少 <Skills> 节点")

    old_skills = my_root.find("Skills")
    if old_skills is not None:
        my_root.remove(old_skills)
    my_root.append(copy.deepcopy(tgt_skills))

    my_build = my_root.find("Build")
    tgt_build = tgt_root.find("Build")
    if my_build is not None and tgt_build is not None and tgt_build.attrib.get("mainSocketGroup"):
        my_build.set("mainSocketGroup", tgt_build.attrib["mainSocketGroup"])

    return ET.tostring(my_root, encoding="unicode")


def _active_skill_container(root: ET.Element) -> Optional[ET.Element]:
    skills_el = root.find("Skills")
    if skills_el is None:
        return None
    asid = skills_el.attrib.get("activeSkillSet") or "1"
    return skills_el.find(f"SkillSet[@id='{asid}']") or skills_el


def _find_skill_el(root: ET.Element, skill_index: int) -> Optional[ET.Element]:
    container = _active_skill_container(root)
    if container is None:
        return None
    skills = container.findall("Skill")
    if 1 <= skill_index <= len(skills):
        return skills[skill_index - 1]
    return None


def ablate_skill_group(xml: str, skill_index: int) -> str:
    """禁用指定 1-based 索引的技能组（用于测量光环/辅助技能组的真实引擎贡献）。"""
    root = ET.fromstring(xml)
    sk = _find_skill_el(root, skill_index)
    if sk is None:
        raise ValueError(f"找不到技能组：index={skill_index}")
    sk.set("enabled", "false")
    return ET.tostring(root, encoding="unicode")


def ablate_skill_gem(xml: str, skill_index: int, gem_index: int) -> str:
    """禁用指定技能组内的第 gem_index (0-based，仅计已启用宝石) 颗宝石。"""
    root = ET.fromstring(xml)
    sk = _find_skill_el(root, skill_index)
    if sk is None:
        raise ValueError(f"找不到技能组：index={skill_index}")
    enabled_gems = [g for g in sk.findall("Gem") if g.attrib.get("enabled") != "false"]
    if not (0 <= gem_index < len(enabled_gems)):
        raise ValueError(f"技能组 index={skill_index} 中找不到宝石 gem_index={gem_index}")
    enabled_gems[gem_index].set("enabled", "false")
    return ET.tostring(root, encoding="unicode")


def downgrade_skill_gem(
    xml: str,
    skill_index: int,
    gem_index: int,
    *,
    max_level: int = 20,
    max_quality: int = 20,
) -> str:
    """将 21+ 级或 21%+ 品质的腐化宝石降配到 20/20，用于实算抄作业前期省钱损耗。"""
    root = ET.fromstring(xml)
    sk = _find_skill_el(root, skill_index)
    if sk is None:
        raise ValueError(f"找不到技能组：index={skill_index}")
    enabled_gems = [g for g in sk.findall("Gem") if g.attrib.get("enabled") != "false"]
    if not (0 <= gem_index < len(enabled_gems)):
        raise ValueError(f"技能组 index={skill_index} 中找不到宝石 gem_index={gem_index}")
    gem = enabled_gems[gem_index]
    try:
        cur_lv = int(gem.attrib.get("level") or "1")
    except ValueError:
        cur_lv = 1
    try:
        cur_q = int(gem.attrib.get("quality") or "0")
    except ValueError:
        cur_q = 0
    changed = False
    if cur_lv > max_level:
        gem.set("level", str(max_level))
        changed = True
    if cur_q > max_quality:
        gem.set("quality", str(max_quality))
        changed = True
    if not changed:
        raise ValueError(f"宝石无需降配（当前 Lv.{cur_lv}/{cur_q}%Q）")
    return ET.tostring(root, encoding="unicode")


def inject_custom_mods(xml: str, mod_lines: list[str], *, title: str = "WorkbenchProbe") -> str:
    """往 PoB2 激活的 <ConfigSet>（或 <Config>）注入全局探针词缀。

    同时兼容两代 PoB2 ConfigTab XML 格式（且各自只生效一次、不重复叠加）：
      - v0.23.1 等版本读取 `<Input name="customMods" string="..." />`（忽略 `<CustomModifierBlock>`）；
      - 新版 PoB2 在存在非空 `<CustomModifierBlock>` 时仅读取 `<CustomModifierBlock>`（忽略旧 `<Input name="customMods">`）。
    不触碰任何已穿戴装备，彻底避免卡兰德戒指、精巧腰带、本地护甲/武器倍率放大对灵敏度测算的干扰。
    """
    cleaned = [str(m).strip() for m in (mod_lines or []) if str(m).strip()]
    if not cleaned:
        return xml
    root = ET.fromstring(xml)
    cfg = root.find("Config")
    if cfg is None:
        cfg = ET.SubElement(root, "Config", {"activeConfigSet": "1"})

    csets = cfg.findall("ConfigSet")
    target_parent: ET.Element = cfg
    if csets:
        active_id = str(cfg.attrib.get("activeConfigSet") or "1")
        target_parent = next((cs for cs in csets if str(cs.attrib.get("id") or "") == active_id), csets[0])

    existing_blocks = target_parent.findall("CustomModifierBlock")
    existing_input: ET.Element | None = next(
        (el for el in target_parent.findall("Input") if str(el.attrib.get("name") or "") == "customMods"),
        None,
    )
    legacy_text = str(existing_input.attrib.get("string") or "").strip() if existing_input is not None else ""

    # 若原 XML 只有旧版 <Input name="customMods"> 而无 <CustomModifierBlock>，先保留原词缀进 block，防止新版 PoB2 丢失
    if legacy_text and not existing_blocks:
        legacy_block = ET.SubElement(target_parent, "CustomModifierBlock", {"title": "Default", "enabled": "true"})
        legacy_block.text = "\n" + legacy_text + "\n"
        existing_blocks = [legacy_block]

    # 1. 新版 PoB2：追加 <CustomModifierBlock>
    new_text = "\n".join(cleaned)
    block = ET.SubElement(target_parent, "CustomModifierBlock", {"title": title, "enabled": "true"})
    block.text = "\n" + new_text + "\n"

    # 2. v0.23.1 兼容：同步汇总所有已启用词缀到 <Input name="customMods" string="..." />
    merged_parts: list[str] = []
    if existing_blocks:
        for blk in existing_blocks:
            enabled_attr = str(blk.attrib.get("enabled") or "true").strip().lower()
            if enabled_attr == "false":
                continue
            bt = (blk.text or "").strip()
            if bt:
                merged_parts.append(bt)
    elif legacy_text:
        merged_parts.append(legacy_text)
    merged_parts.append(new_text)
    merged_str = "\n".join(merged_parts)

    if existing_input is None:
        existing_input = ET.SubElement(target_parent, "Input", {"name": "customMods", "string": merged_str})
    else:
        existing_input.set("string", merged_str)

    return ET.tostring(root, encoding="unicode")


def build_synthetic_item_text(
    *,
    base_type: str,
    rarity: str = "RARE",
    name: str = "Workbench Synthetic",
    mods: list[str] | None = None,
    implicits: list[str] | None = None,
    quality: int = 20,
    sockets: int = 0,
    item_level: int = 82,
    runes: list[str] | None = None,
) -> str:
    """构造标准 PoB2 装备文本块，供武器/防具基准探针与 AI 沙盒换装验证使用。"""
    r_up = str(rarity or "RARE").strip().upper()
    if r_up not in ("NORMAL", "MAGIC", "RARE", "UNIQUE"):
        r_up = "RARE"
    lines: list[str] = [f"Rarity: {r_up}"]
    if r_up in ("RARE", "UNIQUE"):
        lines.append((name or "Workbench Synthetic").strip())
    lines.append((base_type or "").strip())
    if quality and quality > 0:
        lines.append(f"Quality: {int(quality)}")
    if sockets and sockets > 0:
        lines.append("Sockets: " + " ".join(["S"] * int(sockets)))
    if item_level and item_level > 0:
        lines.append(f"Item Level: {int(item_level)}")
    for r in runes or []:
        rn = str(r).strip()
        if rn:
            lines.append(f"Rune: {rn}")
    imp_list = [str(x).strip() for x in (implicits or []) if str(x).strip()]
    lines.append(f"Implicits: {len(imp_list)}")
    lines.extend(imp_list)
    for m in mods or []:
        ms = str(m).strip()
        if ms:
            lines.append(ms)
    return "\n".join(lines) + "\n"


def replace_slots_batch(
    xml: str,
    replacements: list[dict[str, object]],
) -> str:
    """一次性替换多个装备槽位（用于 AI 整套抄作业方案 PoB2 沙盒验收）。

    每条 replacement 支持：
      - slot: 槽位名（如 Weapon 1 / Helmet / Ring 1）
      - rawText: 可选，完整 PoB2 装备文本（若提供则直接替换）
      - 或 baseType / rarity / name / mods / implicits / quality / sockets / runes 自动合成
      - 若 action == "remove" 则脱掉该槽位
    """
    cur = xml
    for rep in replacements or []:
        if not isinstance(rep, dict):
            continue
        slot = str(rep.get("slot") or "").strip()
        if not slot:
            continue
        if str(rep.get("action") or "").lower() == "remove":
            try:
                cur = ablate_slot(cur, slot)
            except Exception:
                pass
            continue
        raw_text = str(rep.get("rawText") or "").strip()
        if not raw_text:
            base_type = str(rep.get("baseType") or "").strip()
            if not base_type:
                continue
            raw_text = build_synthetic_item_text(
                base_type=base_type,
                rarity=str(rep.get("rarity") or "RARE"),
                name=str(rep.get("name") or rep.get("itemName") or f"Test {base_type}"),
                mods=[str(x) for x in (rep.get("mods") or [])] if isinstance(rep.get("mods"), list) else [],
                implicits=[str(x) for x in (rep.get("implicits") or [])] if isinstance(rep.get("implicits"), list) else [],
                quality=int(rep.get("quality") or 20),
                sockets=int(rep.get("sockets") or 0),
                item_level=int(rep.get("itemLevel") or 82),
                runes=[str(x) for x in (rep.get("runes") or [])] if isinstance(rep.get("runes"), list) else [],
            )
        cur = swap_slot_item(cur, slot, raw_text)
    return cur
