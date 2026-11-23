"""纯函数 PoB2 XML 变异器：为脱装消融、双码试穿、换天赋、换技能、单词缀剥离提供可单测的 XML 生成。"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from typing import Optional

_ATTR_LINE = re.compile(r"^\+\d+ to (Strength|Dexterity|Intelligence)$")
_SPIRIT_LINE = re.compile(r"^\+\d+ to Spirit$")


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


def dependency_lines(raw_item_text: str) -> list[str]:
    """提取可能触发“穿戴门槛断裂”的三属性与精魂词缀。"""
    out = []
    for line in (raw_item_text or "").splitlines():
        s = line.strip()
        if _ATTR_LINE.match(s) or _SPIRIT_LINE.match(s):
            out.append(s)
    return out


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
    """从指定 itemId 中精确移除一条词缀（用于单槽位核心词缀权重剥离测试）。"""
    root = ET.fromstring(xml)
    it = _item_el_by_id(root, item_id)
    if it is None:
        raise ValueError(f"找不到 itemId={item_id}")
    lines = (it.text or "").splitlines()
    removed = False
    kept: list[str] = []
    target = mod_line.strip()
    for line in lines:
        if not removed and target in line:
            removed = True
            continue
        kept.append(line)
    if not removed:
        raise ValueError(f"itemId={item_id} 中未找到词缀：{mod_line}")
    it.text = "\n".join(kept) + "\n"
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
