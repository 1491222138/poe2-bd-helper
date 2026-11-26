"""PoB2 天赋树节点索引：直接读取 PathOfBuilding-PoE2 自带的 TreeData/<ver>/tree.json。

把 XML 里的纯数字 nodeId（如 52, 9085）映射为可读的：
  - Keystone（核心基石天赋）
  - Ascendancy（升华大点）
  - Notable（核心大点，并区分 WeaponSet 1 / WeaponSet 2 武器组天赋）
  - JewelSocket（珠宝孔）
"""
from __future__ import annotations

import json
import math
import os
import xml.etree.ElementTree as ET
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
        is_mc_opt = bool(node.get("isMultipleChoiceOption"))
        is_jewel = bool(node.get("isJewelSocket"))
        if is_ks:
            kind = "keystone"
        elif asc and (is_not or is_mc_opt):
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
            "isAscendancyStart": bool(node.get("isAscendancyStart")),
            "isMultipleChoice": bool(node.get("isMultipleChoice")),
            "isMultipleChoiceOption": bool(node.get("isMultipleChoiceOption")),
            "connections": [
                str(c["id"]) if isinstance(c, dict) else str(c)
                for c in (node.get("connections") or [])
                if (isinstance(c, dict) and c.get("id") is not None) or isinstance(c, (int, str))
            ],
            "stats": list(node.get("stats") or []),
        }
    return ver, compact


def plan_ascendancy_ablation(
    allocated_node_ids: list[str],
    ascendancy_name: str,
    tree_version: str | None = None,
) -> dict[str, Any]:
    """根据 tree.json 的升华连接图生成精确的升华天赋消融计划。

    特点：
      1. 合并 MultipleChoice（如 Implanted Gems）与其已选子项（如 Motoric Implants）为一个逻辑升华大点，避免重复计分；
      2. 通过 BFS 连通性识别每个大点的下游强依赖分支（disconnectedDescendants），使消融同时支持
         「孤立净贡献（排除子节点）」与「整支分支贡献（含解锁的后置大点）」；
      3. 收集全部已点升华节点 ID（含小过路点，不含起点），用于整体升华消融实算。
    """
    asc_target = (ascendancy_name or "").strip()
    if not asc_target or asc_target.lower() == "none":
        return {"ascendancy": asc_target, "startNodeId": None, "allAscNodeIds": [], "notables": []}
    try:
        _, index = load_tree_nodes(tree_version)
    except FileNotFoundError:
        return {"ascendancy": asc_target, "startNodeId": None, "allAscNodeIds": [], "notables": []}

    alloc_set = {str(x).strip() for x in (allocated_node_ids or []) if str(x).strip()}
    asc_lower = asc_target.lower()

    # 构建该升华的无向邻接表并定位起点
    adj: dict[str, set[str]] = {}
    start_id: str | None = None
    asc_nodes_in_tree: dict[str, dict[str, Any]] = {}
    for nid, info in index.items():
        if (info.get("ascendancyName") or "").strip().lower() != asc_lower:
            continue
        asc_nodes_in_tree[nid] = info
        if info.get("isAscendancyStart"):
            start_id = nid
        for cid in info.get("connections") or []:
            adj.setdefault(nid, set()).add(cid)
            adj.setdefault(cid, set()).add(nid)

    alloc_asc = {nid for nid in alloc_set if nid in asc_nodes_in_tree}
    if not alloc_asc:
        return {"ascendancy": asc_target, "startNodeId": start_id, "allAscNodeIds": [], "notables": []}

    all_removable_asc_ids = sorted(nid for nid in alloc_asc if nid != start_id)

    # 识别 MultipleChoice 父节点与已选子项，合并为单一逻辑节点
    mc_option_to_parent: dict[str, str] = {}
    mc_parent_to_options: dict[str, list[str]] = {}
    for nid in alloc_asc:
        info = asc_nodes_in_tree[nid]
        if info.get("isMultipleChoice"):
            opts = [
                cid for cid in adj.get(nid, set())
                if cid in alloc_asc and asc_nodes_in_tree.get(cid, {}).get("isMultipleChoiceOption")
            ]
            if opts:
                mc_parent_to_options[nid] = opts
                for oid in opts:
                    mc_option_to_parent[oid] = nid

    # 计算每个节点的拓扑深度（从升华起点 BFS）
    depth: dict[str, int] = {}
    if start_id and start_id in alloc_asc:
        q = [start_id]
        depth[start_id] = 0
        while q:
            cur = q.pop(0)
            for nb in adj.get(cur, set()):
                if nb in alloc_asc and nb not in depth:
                    depth[nb] = depth[cur] + 1
                    q.append(nb)

    def reachable_without(removed: set[str]) -> set[str]:
        if not start_id or start_id in removed:
            return set()
        vis = {start_id}
        q = [start_id]
        while q:
            cur = q.pop(0)
            for nb in adj.get(cur, set()):
                if nb in alloc_asc and nb not in removed and nb not in vis:
                    vis.add(nb)
                    q.append(nb)
        return vis

    # 收集逻辑升华大点
    logical_entries: list[dict[str, Any]] = []
    seen_logical: set[str] = set()
    for nid in sorted(alloc_asc, key=lambda x: (depth.get(x, 99), int(x) if x.isdigit() else 0)):
        info = asc_nodes_in_tree[nid]
        if info.get("kind") != "ascendancy":
            continue
        if nid in mc_option_to_parent:
            continue
        if nid in seen_logical:
            continue
        seen_logical.add(nid)

        own_ids = [nid]
        display_name = info["name"]
        stats = list(info.get("stats") or [])
        if nid in mc_parent_to_options:
            opts = mc_parent_to_options[nid]
            own_ids.extend(opts)
            opt_names = [asc_nodes_in_tree[oid]["name"] for oid in opts if asc_nodes_in_tree.get(oid, {}).get("name")]
            if opt_names:
                display_name = f"{info['name']} → {' / '.join(opt_names)}"
            for oid in opts:
                for st in asc_nodes_in_tree.get(oid, {}).get("stats") or []:
                    if st not in stats:
                        stats.append(st)

        own_set = set(own_ids)
        vis_after = reachable_without(own_set)
        # 被级联断开的其他已分配升华节点（排除自身与起点）
        disconnected_all = sorted(
            x for x in alloc_asc
            if x != start_id and x not in own_set and x not in vis_after
        )
        disconnected_notables: list[str] = []
        for x in disconnected_all:
            xinfo = asc_nodes_in_tree.get(x) or {}
            if xinfo.get("kind") == "ascendancy" and x not in mc_option_to_parent:
                xname = xinfo.get("name") or x
                if x in mc_parent_to_options:
                    onames = [asc_nodes_in_tree[o]["name"] for o in mc_parent_to_options[x] if asc_nodes_in_tree.get(o, {}).get("name")]
                    if onames:
                        xname = f"{xname} → {' / '.join(onames)}"
                if xname not in disconnected_notables:
                    disconnected_notables.append(xname)

        logical_entries.append({
            "id": nid,
            "nodeIds": own_ids,
            "name": display_name,
            "baseName": info["name"],
            "ascendancyName": info.get("ascendancyName") or asc_target,
            "stats": stats,
            "depth": depth.get(nid, 99),
            "disconnectedIds": disconnected_all,
            "unlocksNotables": disconnected_notables,
        })

    return {
        "ascendancy": asc_target,
        "startNodeId": start_id,
        "allAscNodeIds": all_removable_asc_ids,
        "notables": logical_entries,
    }


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


def lookup_notable_by_name(name: str, tree_version: str | None = None) -> dict[str, Any] | None:
    """按英文大点名称反查天赋树节点信息（用于解析涂油 `Allocates <Notable>`）。"""
    target = (name or "").strip().lower()
    if not target:
        return None
    try:
        _, index = load_tree_nodes(tree_version)
    except FileNotFoundError:
        return None
    for info in index.values():
        if (info.get("name") or "").strip().lower() == target and info.get("kind") in ("notable", "keystone"):
            return dict(info)
    return None


@lru_cache(maxsize=2)
def load_tree_layout(version: str | None = None) -> dict[str, Any]:
    """加载可用于 Canvas/SVG 交互式渲染的完整 PoE2 天赋树几何布局（坐标、轨道弧线、节点元数据）。"""
    ver, path = _resolve_tree_file(version)
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    constants = raw.get("constants") or {}
    orbit_radii = [float(r) for r in (constants.get("orbitRadii") or [0, 82, 162, 335, 493, 662, 846, 251, 1080, 1322])]
    skills_per_orbit = [int(c) for c in (constants.get("skillsPerOrbit") or [1, 12, 24, 24, 72, 72, 72, 24, 72, 144])]
    orbit_angles_raw = constants.get("orbitAnglesByOrbit") or []

    groups_raw = raw.get("groups")
    nodes_raw = raw.get("nodes") or {}

    def _get_group(gid: Any) -> dict[str, Any] | None:
        if gid is None:
            return None
        if isinstance(groups_raw, list):
            try:
                idx = int(gid) - 1
            except (TypeError, ValueError):
                return None
            if 0 <= idx < len(groups_raw) and isinstance(groups_raw[idx], dict):
                return groups_raw[idx]
            return None
        if isinstance(groups_raw, dict):
            g = groups_raw.get(str(gid))
            return g if isinstance(g, dict) else None
        return None

    def _node_angle(orbit: int, oidx: int) -> float:
        if 0 <= orbit < len(orbit_angles_raw):
            arr = orbit_angles_raw[orbit]
            if isinstance(arr, list) and 0 <= oidx < len(arr):
                return float(arr[oidx])
        cnt = skills_per_orbit[orbit] if 0 <= orbit < len(skills_per_orbit) and skills_per_orbit[orbit] > 0 else 1
        return (2.0 * math.pi * oidx) / cnt

    nodes_out: dict[str, dict[str, Any]] = {}
    class_starts: dict[str, str] = {}
    asc_starts: dict[str, str] = {}
    asc_centers: dict[str, dict[str, float]] = {}

    for cls in raw.get("classes") or []:
        if not isinstance(cls, dict):
            continue
        for asc in cls.get("ascendancies") or []:
            if not isinstance(asc, dict):
                continue
            aname = str(asc.get("name") or asc.get("id") or "").strip()
            bg = asc.get("background")
            if aname and isinstance(bg, dict) and bg.get("x") is not None and bg.get("y") is not None:
                asc_centers[aname] = {
                    "x": round(float(bg["x"]), 1),
                    "y": round(float(bg["y"]), 1),
                    "w": round(float(bg.get("width") or 1500), 1),
                    "h": round(float(bg.get("height") or 1500), 1),
                }

    min_x = float("inf")
    max_x = float("-inf")
    min_y = float("inf")
    max_y = float("-inf")

    items = nodes_raw.items() if isinstance(nodes_raw, dict) else enumerate(nodes_raw)
    for nid_raw, node in items:
        if not isinstance(node, dict):
            continue
        if node.get("isOnlyImage") or node.get("isProxy"):
            continue
        grp = _get_group(node.get("group"))
        if not grp or grp.get("x") is None or grp.get("y") is None:
            continue

        nid = str(nid_raw)
        orbit = int(node.get("orbit") or 0)
        oidx = int(node.get("orbitIndex") or 0)
        radius = orbit_radii[orbit] if 0 <= orbit < len(orbit_radii) else 0.0
        angle = _node_angle(orbit, oidx)
        x = round(float(grp["x"]) + math.sin(angle) * radius, 1)
        y = round(float(grp["y"]) - math.cos(angle) * radius, 1)

        if x < min_x:
            min_x = x
        if x > max_x:
            max_x = x
        if y < min_y:
            min_y = y
        if y > max_y:
            max_y = y

        asc = str(node.get("ascendancyName") or "").strip()
        c_starts = node.get("classesStart") or []
        is_asc_start = bool(node.get("isAscendancyStart"))
        is_ks = bool(node.get("isKeystone"))
        is_not = bool(node.get("isNotable"))
        is_mc = bool(node.get("isMultipleChoice"))
        is_mc_opt = bool(node.get("isMultipleChoiceOption"))
        is_jewel = bool(node.get("isJewelSocket"))

        if c_starts:
            kind = "start"
            for cn in c_starts:
                if cn:
                    class_starts[str(cn)] = nid
        elif is_asc_start:
            kind = "asc_start"
            if asc:
                asc_starts[asc] = nid
        elif is_ks:
            kind = "keystone"
        elif asc and (is_not or is_mc or is_mc_opt):
            kind = "ascendancy"
        elif asc:
            kind = "asc_small"
        elif is_not:
            kind = "notable"
        elif is_jewel:
            kind = "jewel"
        else:
            kind = "small"

        entry: dict[str, Any] = {
            "id": nid,
            "x": x,
            "y": y,
            "k": kind,
            "n": str(node.get("name") or ""),
            "s": list(node.get("stats") or []),
        }
        if asc:
            entry["a"] = asc
        if c_starts:
            entry["cs"] = [str(c) for c in c_starts if c]
        nodes_out[nid] = entry

    # 去重无向边（保留 PoB2 曲线轨道参数 orbit）
    edges_out: list[list[Any]] = []
    seen_edges: set[tuple[str, str]] = set()
    for nid_raw, node in (nodes_raw.items() if isinstance(nodes_raw, dict) else enumerate(nodes_raw)):
        if not isinstance(node, dict):
            continue
        nid = str(nid_raw)
        if nid not in nodes_out:
            continue
        for conn in node.get("connections") or []:
            if isinstance(conn, dict):
                cid = str(conn.get("id")) if conn.get("id") is not None else ""
                c_orbit = int(conn.get("orbit") or 0)
            else:
                cid = str(conn)
                c_orbit = 0
            if not cid or cid not in nodes_out or cid == nid:
                continue
            ek = (nid, cid) if nid < cid else (cid, nid)
            if ek in seen_edges:
                continue
            seen_edges.add(ek)
            # 若边顺序被翻转，有向曲率符号取反以保持圆弧方向与 PoB2 一致
            if nid < cid:
                edges_out.append([nid, cid, c_orbit])
            else:
                edges_out.append([cid, nid, -c_orbit])

    return {
        "treeVersion": ver,
        "orbitRadii": orbit_radii,
        "bounds": {
            "minX": round(min_x, 1) if math.isfinite(min_x) else -24000,
            "maxX": round(max_x, 1) if math.isfinite(max_x) else 24000,
            "minY": round(min_y, 1) if math.isfinite(min_y) else -24000,
            "maxY": round(max_y, 1) if math.isfinite(max_y) else 24000,
        },
        "classStarts": class_starts,
        "ascendancyStarts": asc_starts,
        "ascendancyCenters": asc_centers,
        "nodes": nodes_out,
        "edges": edges_out,
    }


def _extract_spec_tree_sets(xml_text: str) -> dict[str, Any]:
    """从 PoB2 XML 的活跃 <Spec> 中提取原始已点节点、WS1/WS2 节点、珠宝孔、职业与等级。"""
    from backend.engine import pob_parser

    root = ET.fromstring(xml_text)
    build_el = root.find("Build")
    class_name = (build_el.attrib.get("className") or "Unknown") if build_el is not None else "Unknown"
    ascendancy = (build_el.attrib.get("ascendClassName") or "None") if build_el is not None else "None"
    level = int(build_el.attrib.get("level") or "0") if build_el is not None else 0

    tree_el = root.find("Tree")
    spec = None
    if tree_el is not None:
        specs = tree_el.findall("Spec")
        if specs:
            try:
                idx = int(tree_el.attrib.get("activeSpec") or "1")
            except ValueError:
                idx = 1
            spec = specs[idx - 1] if 1 <= idx <= len(specs) else specs[0]

    raw_nodes: list[str] = []
    ws1_ids: list[str] = []
    ws2_ids: list[str] = []
    tree_ver = ""
    jewels_by_node: dict[str, dict[str, Any]] = {}

    # 解析所有物品，供珠宝孔关联
    items_by_id: dict[str, dict[str, Any]] = {}
    items_el = root.find("Items")
    if items_el is not None:
        for it in items_el.findall("Item"):
            iid = it.attrib.get("id")
            if iid:
                items_by_id[iid] = pob_parser.parse_item_text(it.text or "")

    if spec is not None:
        tree_ver = spec.attrib.get("treeVersion") or ""
        raw_nodes = [n.strip() for n in (spec.attrib.get("nodes") or "").split(",") if n.strip()]
        ws1_el = spec.find("WeaponSet1")
        ws2_el = spec.find("WeaponSet2")
        if ws1_el is not None:
            ws1_ids = [n.strip() for n in (ws1_el.attrib.get("nodes") or "").split(",") if n.strip()]
        if ws2_el is not None:
            ws2_ids = [n.strip() for n in (ws2_el.attrib.get("nodes") or "").split(",") if n.strip()]
        for sock in spec.findall("./Sockets/Socket"):
            nid = (sock.attrib.get("nodeId") or "").strip()
            iid = (sock.attrib.get("itemId") or "0").strip()
            if nid and iid != "0" and iid in items_by_id:
                j = items_by_id[iid]
                jewels_by_node[nid] = {
                    "nodeId": nid,
                    "name": j.get("name") or j.get("baseType") or "Jewel",
                    "baseType": j.get("baseType") or "",
                    "rarity": j.get("rarity") or "RARE",
                    "mods": list(j.get("mods") or [])[:10],
                }

    # 从已装备项链/物品提取涂油 Allocates <Notable>
    anointed_names: list[tuple[str, str]] = []
    parsed_full = pob_parser.parse_pob_xml(xml_text)
    for an in (parsed_full.get("passiveTree") or {}).get("anointedNotables") or []:
        nname = str(an.get("name") or "").strip()
        if nname:
            anointed_names.append((nname, str(an.get("itemName") or an.get("slot") or "Amulet")))

    return {
        "className": class_name,
        "ascendancy": ascendancy,
        "level": level,
        "treeVersion": tree_ver,
        "rawNodes": raw_nodes,
        "ws1Ids": ws1_ids,
        "ws2Ids": ws2_ids,
        "jewelsByNode": jewels_by_node,
        "anointedNames": anointed_names,
    }


def _rewrite_spec_nodes_pob(
    xml_text: str,
    kept_all_ids: set[str],
    kept_ws1_ids: set[str],
    kept_ws2_ids: set[str],
    new_level: int,
) -> str:
    """生成按目标等级拓扑裁剪后的 PoB2 分享码（确保所有节点保持连通）。"""
    from backend.engine import pob_parser

    root = ET.fromstring(xml_text)
    build_el = root.find("Build")
    if build_el is not None and new_level > 0:
        build_el.set("level", str(int(new_level)))

    tree_el = root.find("Tree")
    if tree_el is not None:
        specs = tree_el.findall("Spec")
        if specs:
            try:
                idx = int(tree_el.attrib.get("activeSpec") or "1")
            except ValueError:
                idx = 1
            spec = specs[idx - 1] if 1 <= idx <= len(specs) else specs[0]
            sorted_all = sorted(kept_all_ids, key=lambda x: int(x) if x.isdigit() else 0)
            spec.set("nodes", ",".join(sorted_all))
            ws1_el = spec.find("WeaponSet1")
            if ws1_el is not None:
                ws1_el.set("nodes", ",".join(sorted(kept_ws1_ids, key=lambda x: int(x) if x.isdigit() else 0)))
            ws2_el = spec.find("WeaponSet2")
            if ws2_el is not None:
                ws2_el.set("nodes", ",".join(sorted(kept_ws2_ids, key=lambda x: int(x) if x.isdigit() else 0)))
    return pob_parser.encode_pob(ET.tostring(root, encoding="unicode"))


def build_passive_tree_plan(
    target_pob_code: str,
    *,
    my_pob_code: str | None = None,
    character_level: int | None = None,
    cut_notables: list[str] | None = None,
) -> dict[str, Any]:
    """构建抄作业交互式天赋树方案：主树/双武器组/升华/涂油/珠宝孔 + 按等级拓扑连通裁剪 + 自身差异对比 + 裁剪 PoB2 码。"""
    from backend.engine import pob_parser

    target_xml = pob_parser.decode_pob(target_pob_code)
    tgt = _extract_spec_tree_sets(target_xml)
    layout = load_tree_layout(tgt["treeVersion"] or None)
    nodes_map = layout["nodes"]
    ver = layout["treeVersion"]

    # 构建完整无向邻接表
    adj: dict[str, set[str]] = {}
    for a, b, _orb in layout["edges"]:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    raw_set = {nid for nid in tgt["rawNodes"] if nid in nodes_map}
    ws1_set = {nid for nid in tgt["ws1Ids"] if nid in nodes_map and nid in raw_set}
    ws2_set = {nid for nid in tgt["ws2Ids"] if nid in nodes_map and nid in raw_set}

    # 定位职业起点与升华起点
    class_start_id = layout["classStarts"].get(tgt["className"])
    if not class_start_id or (class_start_id not in raw_set and not (adj.get(class_start_id, set()) & raw_set)):
        # 兜底：从已点节点或其邻居中查找职业起点
        for cs_id in set(layout["classStarts"].values()):
            if cs_id in raw_set or (adj.get(cs_id, set()) & raw_set):
                class_start_id = cs_id
                break

    asc_name = tgt["ascendancy"] if tgt["ascendancy"] and tgt["ascendancy"].lower() != "none" else ""
    asc_start_id = layout["ascendancyStarts"].get(asc_name) if asc_name else None

    asc_alloc = {
        nid for nid in raw_set
        if nodes_map[nid].get("a") and nid != asc_start_id
    }
    main_alloc = {
        nid for nid in raw_set
        if not nodes_map[nid].get("a") and nid != class_start_id and nodes_map[nid].get("k") != "start"
    }
    shared_main = main_alloc - ws1_set - ws2_set

    # 涂油节点反查
    anointed_nodes: list[dict[str, Any]] = []
    anointed_ids: set[str] = set()
    for an_name, src_item in tgt["anointedNames"]:
        info = lookup_notable_by_name(an_name, ver)
        if info and info["id"] in nodes_map:
            anointed_ids.add(info["id"])
            anointed_nodes.append({
                "id": info["id"],
                "name": info["name"],
                "source": src_item,
                "stats": info.get("stats") or [],
            })

    # 计算有效点数（PoE2 双武器组共用武器组点数池）
    def _effective_main_points(m_set: set[str], w1: set[str], w2: set[str]) -> int:
        sh = m_set - w1 - w2
        return len(sh) + max(len(m_set & w1), len(m_set & w2))

    full_main_points = _effective_main_points(main_alloc, ws1_set, ws2_set)
    target_level = int(tgt["level"] or 100)
    req_level = int(character_level) if character_level is not None and int(character_level) > 0 else target_level
    missing_points = max(0, target_level - req_level)

    # 拓扑连通按等级裁剪：从外围叶节点剥离，保护基石与插珠宝的孔，确保余下节点 100% 与职业起点连通
    kept_main = set(main_alloc)
    kept_ws1 = set(ws1_set)
    kept_ws2 = set(ws2_set)
    pruned_ids: list[str] = []

    if missing_points > 0 and class_start_id and kept_main:
        root_graph = kept_main | {class_start_id}
        dist: dict[str, int] = {class_start_id: 0}
        q = [class_start_id]
        while q:
            cur = q.pop(0)
            for nb in adj.get(cur, set()):
                if nb in root_graph and nb not in dist:
                    dist[nb] = dist[cur] + 1
                    q.append(nb)

        preferred_cut_set = {str(x).strip().lower() for x in (cut_notables or []) if str(x).strip()}
        equipped_jewel_nodes = set(tgt["jewelsByNode"].keys())

        def _is_connected_without(rem_main: set[str], cand: str) -> bool:
            rem = (rem_main - {cand}) | {class_start_id}
            vis = {class_start_id}
            qq = [class_start_id]
            while qq:
                c = qq.pop(0)
                for nb in adj.get(c, set()):
                    if nb in rem and nb not in vis:
                        vis.add(nb)
                        qq.append(nb)
            return len(vis) == len(rem)

        def _prune_score(nid: str, deg: int) -> float:
            info = nodes_map[nid]
            kind = info.get("k") or "small"
            name_l = (info.get("n") or "").strip().lower()
            d = dist.get(nid, 20)
            # 优先裁死胡同过路小点 -> AI 指定裁剪大点 -> 外围普通大点 -> 环上小点 -> 空珠宝孔 -> 珠宝孔 -> 基石
            if kind == "small" and deg <= 1:
                base = 1000.0
            elif name_l and name_l in preferred_cut_set:
                base = 820.0
            elif kind == "notable":
                base = 440.0
            elif kind == "small":
                base = 360.0
            elif kind == "jewel":
                base = 130.0 if nid in equipped_jewel_nodes else 310.0
            elif kind == "keystone":
                base = 20.0
            else:
                base = 300.0
            # 若节点属于较小或相等的武器组，单剪它无法立即省出有效点数，稍微降权优先剪主树共享外围点
            in_w1 = nid in kept_ws1
            in_w2 = nid in kept_ws2
            if in_w1 and len(kept_ws1) <= len(kept_ws2):
                base -= 45.0
            elif in_w2 and len(kept_ws2) <= len(kept_ws1):
                base -= 45.0
            return base + d * 2.5

        max_iters = len(kept_main) + 5
        iters = 0
        while iters < max_iters and kept_main:
            iters += 1
            reclaimed = full_main_points - _effective_main_points(kept_main, kept_ws1, kept_ws2)
            if reclaimed >= missing_points:
                break
            active_graph = kept_main | {class_start_id}
            # 先找度 <= 1 的安全叶节点（删掉绝对不会断连）
            candidates: list[tuple[float, str]] = []
            for nid in kept_main:
                deg = sum(1 for nb in adj.get(nid, set()) if nb in active_graph)
                if deg <= 1:
                    candidates.append((_prune_score(nid, deg), nid))
            if not candidates:
                # 全在环上时，找非割点（删掉仍保持与起点连通）
                for nid in kept_main:
                    if _is_connected_without(kept_main, nid):
                        deg = sum(1 for nb in adj.get(nid, set()) if nb in active_graph)
                        candidates.append((_prune_score(nid, deg), nid))
            if not candidates:
                break
            candidates.sort(key=lambda x: (-x[0], x[1]))
            pick = candidates[0][1]
            kept_main.remove(pick)
            kept_ws1.discard(pick)
            kept_ws2.discard(pick)
            pruned_ids.append(pick)

    pruned_set = set(pruned_ids)
    kept_all_ids = set(kept_main) | set(asc_alloc)
    if class_start_id:
        kept_all_ids.add(class_start_id)
    if asc_start_id and asc_alloc:
        kept_all_ids.add(asc_start_id)

    pruned_pob_code = (
        _rewrite_spec_nodes_pob(target_xml, kept_all_ids, kept_ws1, kept_ws2, req_level)
        if pruned_set
        else target_pob_code.strip()
    )

    def _node_brief(nid: str) -> dict[str, Any]:
        info = nodes_map[nid]
        return {
            "id": nid,
            "name": info["n"],
            "kind": info["k"],
            "x": info["x"],
            "y": info["y"],
            "ascendancyName": info.get("a"),
            "weaponSet": 1 if nid in ws1_set else (2 if nid in ws2_set else None),
            "stats": info.get("s") or [],
        }

    keystones_list = [_node_brief(nid) for nid in sorted(main_alloc) if nodes_map[nid]["k"] == "keystone"]
    asc_notables_list = [_node_brief(nid) for nid in sorted(asc_alloc) if nodes_map[nid]["k"] == "ascendancy"]
    pruned_notables = [
        _node_brief(nid)
        for nid in pruned_ids
        if nodes_map[nid]["k"] in ("notable", "keystone", "jewel")
    ]

    # 自身 vs 目标差异对比（可选）
    diff_info: dict[str, Any] | None = None
    if my_pob_code and my_pob_code.strip():
        try:
            my_xml = pob_parser.decode_pob(my_pob_code)
            my = _extract_spec_tree_sets(my_xml)
            my_alloc = {
                nid for nid in my["rawNodes"]
                if nid in nodes_map and nodes_map[nid]["k"] not in ("start", "asc_start")
            }
            target_compare_set = main_alloc | asc_alloc
            to_add = sorted(target_compare_set - my_alloc)
            to_refund = sorted(my_alloc - target_compare_set)
            shared_both = sorted(target_compare_set & my_alloc)
            diff_info = {
                "myLevel": my["level"],
                "myClassName": my["className"],
                "myAscendancy": my["ascendancy"],
                "myTotalAllocated": len(my_alloc),
                "toAddIds": to_add,
                "toRefundIds": to_refund,
                "sharedIds": shared_both,
                "toAddNotables": [
                    _node_brief(nid)
                    for nid in to_add
                    if nodes_map[nid]["k"] in ("keystone", "notable", "ascendancy", "jewel")
                ],
                "toRefundNotables": [
                    _node_brief(nid)
                    for nid in to_refund
                    if nodes_map[nid]["k"] in ("keystone", "notable", "ascendancy", "jewel")
                ],
            }
        except Exception:
            diff_info = None

    return {
        "treeVersion": ver,
        "className": tgt["className"],
        "ascendancy": asc_name or "None",
        "targetLevel": target_level,
        "characterLevel": req_level,
        "missingPoints": missing_points,
        "classStartId": class_start_id,
        "ascendancyStartId": asc_start_id,
        "allocatedIds": sorted(main_alloc | asc_alloc),
        "mainAllocatedIds": sorted(main_alloc),
        "ascendancyAllocatedIds": sorted(asc_alloc),
        "sharedMainIds": sorted(shared_main),
        "ws1Ids": sorted(ws1_set),
        "ws2Ids": sorted(ws2_set),
        "keptMainIds": sorted(kept_main),
        "prunedIds": pruned_ids,
        "prunedNotables": pruned_notables,
        "anointedNodes": anointed_nodes,
        "anointedIds": sorted(anointed_ids),
        "jewelsByNode": tgt["jewelsByNode"],
        "keystones": keystones_list,
        "ascendancyNotables": asc_notables_list,
        "points": {
            "fullMain": full_main_points,
            "keptMain": _effective_main_points(kept_main, kept_ws1, kept_ws2),
            "prunedMain": full_main_points - _effective_main_points(kept_main, kept_ws1, kept_ws2),
            "ascendancy": len(asc_alloc),
            "ws1": len(ws1_set),
            "ws2": len(ws2_set),
        },
        "targetPobCode": target_pob_code.strip(),
        "prunedPobCode": pruned_pob_code,
        "diff": diff_info,
    }
