"""PoB2 天赋树节点索引：直接读取 PathOfBuilding-PoE2 自带的 TreeData/<ver>/tree.json。

把 XML 里的纯数字 nodeId（如 52, 9085）映射为可读的：
  - Keystone（核心基石天赋）
  - Ascendancy（升华大点）
  - Notable（核心大点，并区分 WeaponSet 1 / WeaponSet 2 武器组天赋）
  - JewelSocket（珠宝孔）
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from backend.config import pob2_src


def available_tree_versions() -> list[str]:
    tree_dir = os.path.join(pob2_src(), "TreeData")
    if not os.path.isdir(tree_dir):
        return []
    versions = []
    for name in os.listdir(tree_dir):
        if os.path.isfile(os.path.join(tree_dir, name, "tree.json")):
            versions.append(name)
    return sorted(versions)


def _resolve_tree_file(version: str | None) -> tuple[str, str]:
    versions = available_tree_versions()
    if not versions:
        raise FileNotFoundError(f"TreeData 下没有找到任何 tree.json（POB2_SRC={pob2_src()}）")
    ver = version if (version and version in versions) else versions[-1]
    return ver, os.path.join(pob2_src(), "TreeData", ver, "tree.json")


@lru_cache(maxsize=4)
def load_tree_nodes(version: str | None = None) -> tuple[str, dict[str, dict[str, Any]]]:
    """返回 (实际加载的 treeVersion, {nodeId: compactNodeInfo})。"""
    ver, path = _resolve_tree_file(version)
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    nodes_raw = raw.get("nodes") or {}
    compact: dict[str, dict[str, Any]] = {}
    items = nodes_raw.items() if isinstance(nodes_raw, dict) else enumerate(nodes_raw)
    for nid, node in items:
        if not isinstance(node, dict):
            continue
        name = node.get("name") or ""
        asc = node.get("ascendancyName") or ""
        is_ks = bool(node.get("isKeystone"))
        is_not = bool(node.get("isNotable"))
        is_jewel = bool(node.get("isJewelSocket"))
        if is_ks:
            kind = "keystone"
        elif asc and is_not:
            kind = "ascendancy"
        elif is_not:
            kind = "notable"
        elif is_jewel:
            kind = "jewel"
        else:
            kind = "small"
        compact[str(nid)] = {
            "id": str(nid),
            "name": name,
            "kind": kind,
            "ascendancyName": asc or None,
            "stats": list(node.get("stats") or []),
        }
    return ver, compact


def summarize_allocated_nodes(
    node_ids: list[str],
    tree_version: str | None = None,
    weapon_set1_ids: set[str] | None = None,
    weapon_set2_ids: set[str] | None = None,
) -> dict[str, Any]:
    """把一串已点 nodeId 归类成结构化天赋摘要。"""
    ws1 = weapon_set1_ids or set()
    ws2 = weapon_set2_ids or set()
    try:
        ver, index = load_tree_nodes(tree_version)
    except FileNotFoundError:
        return {
            "treeVersion": tree_version or "",
            "totalAllocated": len(node_ids),
            "keystones": [],
            "ascendancyNodes": [],
            "notables": [],
            "weaponSet1Notables": [],
            "weaponSet2Notables": [],
        }

    keystones: list[dict[str, Any]] = []
    ascendancy: list[dict[str, Any]] = []
    notables: list[dict[str, Any]] = []
    ws1_notables: list[dict[str, Any]] = []
    ws2_notables: list[dict[str, Any]] = []

    seen: set[str] = set()
    for nid in node_ids:
        sid = str(nid).strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        info = index.get(sid)
        if not info:
            continue
        ws = 1 if sid in ws1 else (2 if sid in ws2 else None)
        entry = {
            "id": sid,
            "name": info["name"],
            "kind": info["kind"],
            "ascendancyName": info["ascendancyName"],
            "weaponSet": ws,
            "stats": info["stats"],
        }
        if info["kind"] == "keystone":
            keystones.append(entry)
        elif info["kind"] == "ascendancy":
            ascendancy.append(entry)
        elif info["kind"] == "notable":
            notables.append(entry)
            if ws == 1:
                ws1_notables.append(entry)
            elif ws == 2:
                ws2_notables.append(entry)

    return {
        "treeVersion": ver,
        "totalAllocated": len(seen),
        "keystones": keystones,
        "ascendancyNodes": ascendancy,
        "notables": notables,
        "weaponSet1Notables": ws1_notables,
        "weaponSet2Notables": ws2_notables,
    }
