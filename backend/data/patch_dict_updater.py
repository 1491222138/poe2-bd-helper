"""离线轻量版本词典（天赋树大点/基石/升华含分支子节点 + 技能/辅助宝石官方描述与核心数值）。

职责：
  1. 从本机 POB2_ROOT 的 TreeData/<ver>/tree.json 与 Data/Skills/*.lua 提取紧凑 JSON 词典；
  2. 提供给后端 llm_advisor.py（构建紧凑带原文事实包）与 GET /meta/patch-dict（供扩展在线同步）；
  3. 同步导出至 backend/data/patch_dict.json 与 extension/public/data/patch_dict.json。
"""
from __future__ import annotations

import json
import os
import re
import time
from functools import lru_cache
from typing import Any

from backend.config import REPO_ROOT, pob2_src
from backend.engine import tree_index

BACKEND_DICT_PATH = os.path.join(REPO_ROOT, "backend", "data", "patch_dict.json")
EXTENSION_DICT_PATH = os.path.join(REPO_ROOT, "extension", "public", "data", "patch_dict.json")

_SKILL_FILES = (
    "act_dex.lua",
    "act_int.lua",
    "act_str.lua",
    "sup_dex.lua",
    "sup_int.lua",
    "sup_str.lua",
    "other.lua",
    "minion.lua",
)

_BLOCK_RE = re.compile(r'skills\["([^"]+)"\]\s*=\s*\{(.*?)\n\}', re.S)
_NAME_RE = re.compile(r'^\s*name\s*=\s*"([^"]+)"', re.M)
_DESC_RE = re.compile(r'^\s*description\s*=\s*"((?:[^"\\]|\\.)*)"', re.M)
_SUPPORT_RE = re.compile(r'^\s*support\s*=\s*true', re.M)
_CONST_BLOCK_RE = re.compile(r'constantStats\s*=\s*\{(.*?)\n\s*\},', re.S)
_CONST_PAIR_RE = re.compile(r'\{\s*"([^"]+)"\s*,\s*(-?\d+(?:\.\d+)?)\s*\}')

_NOISY_STAT_SUBSTRS = (
    "stat_description_mode",
    "animation_distance",
    "locked_duration",
    "acceleration_+%_per_second",
    "spread_angle",
    "bounce_check_angle",
    "action_distance",
    "step_distance",
    "desired_amount_override",
    "is_area_damage",
    "base_is_projectile",
    "skill_can_fire",
    "console_skill",
    "visual_",
    "display_",
    "hide_",
    "cannot_cancel",
)

_HIGH_SIGNAL_STAT_RE = re.compile(
    r"ward_cost|empowers|maximum_critical|bifurcat|negated|weapon_set|whirling_slash|"
    r"elemental_damage|additional_projectile|seal|barrage|cooldown|armour_break|"
    r"intimidate|damage_\+%|damage_-%|attack_speed|cast_speed|skill_speed|projectile_speed|"
    r"spirit|reservation|charge|rage|duration|totem|minion|leech|regen|recover|"
    r"life_\+%|mana_\+%|evasion|energy_shield|physical_damage|fire_damage|cold_damage|"
    r"lightning_damage|chaos_damage|crit|ailment|ignite|shock|chill|freeze|bleed|poison|daze|blind",
    re.I,
)


def _extract_constant_stats(body: str) -> list[str]:
    """从技能 Lua 定义块中抽取高信息密度机制数值（如 ward_cost、50% 暴击上限、武器组攻速等）。"""
    seen: set[str] = set()
    out: list[str] = []
    for cb in _CONST_BLOCK_RE.finditer(body):
        for m in _CONST_PAIR_RE.finditer(cb.group(1)):
            key, val = m.group(1), m.group(2)
            kl = key.lower()
            if any(noise in kl for noise in _NOISY_STAT_SUBSTRS):
                continue
            if not _HIGH_SIGNAL_STAT_RE.search(kl):
                continue
            token = f"{key}={val}"
            if token not in seen:
                seen.add(token)
                out.append(token)
                if len(out) >= 6:
                    return out
    return out


def build_patch_dict(tree_version: str | None = None) -> dict[str, Any]:
    """从本机 POB2_ROOT 构建紧凑离线词典（含天赋节点与技能/辅助宝石原文）。"""
    versions = tree_index.available_tree_versions()
    if not versions:
        raise FileNotFoundError(f"未找到 PoB2 TreeData 目录：{pob2_src()}")
    ver = tree_version if (tree_version and tree_version in versions) else versions[-1]
    tree_path = os.path.join(pob2_src(), "TreeData", ver, "tree.json")

    with open(tree_path, encoding="utf-8") as f:
        raw_tree = json.load(f)
    nodes_raw = raw_tree.get("nodes") or {}
    items = nodes_raw.items() if isinstance(nodes_raw, dict) else enumerate(nodes_raw)

    compact_nodes: dict[str, dict[str, Any]] = {}
    notable_by_name: dict[str, list[str]] = {}
    for nid, node in items:
        if not isinstance(node, dict):
            continue
        name = (node.get("name") or "").strip()
        asc = (node.get("ascendancyName") or "").strip()
        is_ks = bool(node.get("isKeystone"))
        is_not = bool(node.get("isNotable"))
        is_mc_opt = bool(node.get("isMultipleChoiceOption"))
        is_jewel = bool(node.get("isJewelSocket"))
        stats = [str(s).strip() for s in (node.get("stats") or []) if str(s).strip()]

        if not (is_ks or is_not or (asc and is_mc_opt) or is_jewel):
            continue

        if is_ks:
            kind = "k"
        elif asc and (is_not or is_mc_opt):
            kind = "a"
        elif is_not:
            kind = "n"
        else:
            kind = "j"

        entry: dict[str, Any] = {"n": name, "k": kind}
        if asc:
            entry["a"] = asc
        if stats:
            entry["s"] = stats
        compact_nodes[str(nid)] = entry
        if (is_not or is_ks or is_mc_opt) and name and stats and name.lower() not in notable_by_name:
            notable_by_name[name.lower()] = stats

    skills_dir = os.path.join(pob2_src(), "Data", "Skills")
    compact_gems: dict[str, dict[str, Any]] = {}
    if os.path.isdir(skills_dir):
        for fn in _SKILL_FILES:
            full = os.path.join(skills_dir, fn)
            if not os.path.isfile(full):
                continue
            with open(full, encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            for m in _BLOCK_RE.finditer(txt):
                body = m.group(2)
                nm = _NAME_RE.search(body)
                if not nm:
                    continue
                name = nm.group(1).strip()
                if not name:
                    continue
                dm = _DESC_RE.search(body)
                desc = dm.group(1).replace("\\n", " ").replace('\\"', '"').strip() if dm else ""
                cstats = _extract_constant_stats(body)
                if not desc and not cstats:
                    continue
                is_sup = bool(_SUPPORT_RE.search(body))
                prev = compact_gems.get(name)
                # 优先保留描述更完整且有数值的词条
                if prev is not None and len(prev.get("d", "")) >= len(desc) and len(prev.get("c", [])) >= len(cstats):
                    continue
                g_entry: dict[str, Any] = {"d": desc, "s": 1 if is_sup else 0}
                if cstats:
                    g_entry["c"] = cstats
                compact_gems[name] = g_entry

    return {
        "schemaVersion": 1,
        "treeVersion": ver,
        "availableTreeVersions": versions,
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "nodeCount": len(compact_nodes),
        "gemCount": len(compact_gems),
        "nodes": compact_nodes,
        "gems": compact_gems,
    }


def write_patch_dict_files(
    *,
    tree_version: str | None = None,
    write_backend: bool = True,
    write_extension: bool = True,
) -> dict[str, Any]:
    """构建词典并原子写入 backend/data 与 extension/public/data。"""
    data = build_patch_dict(tree_version=tree_version)
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    targets: list[str] = []
    if write_backend:
        targets.append(BACKEND_DICT_PATH)
    if write_extension:
        targets.append(EXTENSION_DICT_PATH)

    for path in targets:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(serialized)
        os.replace(tmp, path)

    load_patch_dict.cache_clear()
    return data


@lru_cache(maxsize=2)
def load_patch_dict(auto_build: bool = True) -> dict[str, Any]:
    """加载已生成的词典；若缺失或版本落后于本机 POB2_ROOT 则按需自动重建。"""
    latest_versions = tree_index.available_tree_versions()
    latest_ver = latest_versions[-1] if latest_versions else ""

    if os.path.isfile(BACKEND_DICT_PATH):
        try:
            with open(BACKEND_DICT_PATH, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and loaded.get("nodes") and loaded.get("gems"):
                if (not latest_ver) or (loaded.get("treeVersion") == latest_ver) or (not auto_build):
                    return loaded
        except Exception:
            pass

    if auto_build and latest_ver:
        try:
            return write_patch_dict_files(write_backend=True, write_extension=False)
        except Exception:
            pass

    return {
        "schemaVersion": 1,
        "treeVersion": latest_ver,
        "availableTreeVersions": latest_versions,
        "generatedAt": "",
        "nodeCount": 0,
        "gemCount": 0,
        "nodes": {},
        "gems": {},
    }


def lookup_gem_info(name: str, patch_dict: dict[str, Any] | None = None) -> dict[str, Any] | None:
    d = patch_dict if patch_dict is not None else load_patch_dict()
    gems = d.get("gems") or {}
    if not name or not isinstance(gems, dict):
        return None
    direct = gems.get(name)
    if isinstance(direct, dict):
        return direct
    nl = name.strip().lower()
    for k, v in gems.items():
        if k.lower() == nl and isinstance(v, dict):
            return v
    return None
