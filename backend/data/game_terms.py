"""PoE2 双语游戏术语与词缀翻译缓存（中英瞬切底层字典）。

数据源：
- RePoE PoE2 (`characters.min.json`, `ascendancies.min.json`, `base_items.min.json`,
  `uniques.min.json`, `skill_gems.min.json`, `passive_skill_trees/Default.min.json`)
- PoeCharm2 `zh-rCN` 官方客户端汉化词典 (`tree_dn.csv`, `Uniques.txt.csv`,
  `Items_*.txt.csv`, `Items_Gems.txt.csv`, `GUI.csv`)
- PoE2 官方 Trade2 Static API (`pathofexile.com` + `poe.game.qq.com`)
- 本项目 `trade_stats_cache.json`
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import urllib.request
from functools import lru_cache
from typing import Any

from backend.config import REPO_ROOT
from backend.data import affix_updater
from backend.market.price_store import STATE_DIR

DATA_DIR = os.path.join(REPO_ROOT, "backend", "data")
BUNDLED_GAME_TERMS_PATH = os.path.join(DATA_DIR, "game_terms_zh.json")
BUNDLED_MODS_ZH_MAP_PATH = os.path.join(DATA_DIR, "mods_zh_map.json")

RUNTIME_GAME_TERMS_PATH = os.path.join(STATE_DIR, "game_terms_zh.json")
RUNTIME_MODS_ZH_MAP_PATH = os.path.join(STATE_DIR, "mods_zh_map.json")

POECHARM_ZH_CN_BASE = "https://raw.githubusercontent.com/Chuanhsing/PoeCharm2/main/Data/Translate/zh-rCN"
TRADE_STATIC_EN_URL = "https://www.pathofexile.com/api/trade2/data/static"
TRADE_STATIC_ZH_URL = "https://poe.game.qq.com/api/trade2/data/static"

SLOT_ZH_MAP: dict[str, str] = {
    "Weapon 1": "主手武器 1",
    "Weapon 2": "副手武器 2",
    "Weapon 1 Swap": "副套主手 1",
    "Weapon 2 Swap": "副套副手 2",
    "Helmet": "头盔",
    "Body Armour": "胸甲",
    "Gloves": "手套",
    "Boots": "鞋子",
    "Amulet": "项链",
    "Ring 1": "戒指 1",
    "Ring 2": "戒指 2",
    "Belt": "腰带",
    "Flask 1": "药剂 1",
    "Flask 2": "药剂 2",
    "Charm 1": "护符 1",
    "Charm 2": "护符 2",
    "Charm 3": "护符 3",
    "Jewel": "珠宝",
    "Passive Tree": "天赋树",
    "Skills & Gems": "技能与宝石",
}

ITEM_CLASS_ZH_MAP: dict[str, str] = {
    "Amulets": "项链",
    "Belts": "腰带",
    "Body Armours": "胸甲",
    "Boots": "鞋子",
    "Bows": "弓",
    "Bucklers": "轻盾",
    "Claws": "爪",
    "Crossbows": "战弩",
    "Daggers": "匕首",
    "Flails": "链锤",
    "Foci": "法器",
    "Gloves": "手套",
    "Helmets": "头盔",
    "One Hand Axes": "单手斧",
    "One Hand Maces": "单手锤",
    "One Hand Swords": "单手剑",
    "Quarterstaves": "长杖",
    "Quivers": "箭袋",
    "Rings": "戒指",
    "Sceptres": "权杖",
    "Shields": "盾牌",
    "Spears": "长矛",
    "Staves": "双手法杖",
    "Talismans": "护身符",
    "Two Hand Axes": "双手斧",
    "Two Hand Maces": "双手锤",
    "Two Hand Swords": "双手剑",
    "Wands": "法杖",
    "Jewels": "珠宝",
}

DEFAULT_CLASSES_ZH: dict[str, str] = {
    "Marauder": "野蛮人",
    "Witch": "女巫",
    "Ranger": "游侠",
    "Duelist": "决斗者",
    "Shadow": "暗影刺客",
    "Templar": "圣堂武僧",
    "Warrior": "战士",
    "Sorceress": "术士",
    "Huntress": "猎手",
    "Mercenary": "佣兵",
    "Monk": "武僧",
    "Druid": "德鲁伊",
}

DEFAULT_ASCENDANCIES_ZH: dict[str, str] = {
    "Oracle": "神谕者",
    "Shaman": "萨满",
    "Amazon": "亚马逊",
    "Spirit Walker": "灵行者",
    "Ritualist": "仪祭师",
    "Tactician": "战术家",
    "Witchhunter": "猎巫人",
    "Gemling Legionnaire": "古灵使徒",
    "Martial Artist": "武术家",
    "Invoker": "祈求者",
    "Acolyte of Chayula": "夏乌拉追随者",
    "Deadeye": "锐眼",
    "Pathfinder": "追猎者",
    "Stormweaver": "风暴编织者",
    "Chronomancer": "塑时术师",
    "Disciple of Varashta": "瓦拉煞的门徒",
    "Titan": "泰坦",
    "Warbringer": "战争使者",
    "Smith of Kitava": "奇塔弗匠师",
    "Infernalist": "驱炎使",
    "Blood Mage": "命源法师",
    "Lich": "巫妖",
    "Abyssal Lich": "深渊巫妖",
}

EXTRA_MODS_ZH: dict[str, str] = {
    "has # charm slot": "拥有 # 个护符栏位",
    "has # charm slots": "拥有 # 个护符栏位",
    "enemies take #% increased damage for each elemental ailment type among": "敌人身上每种元素异常状态使其受到的伤害提高 #%",
    "your ailments on them": "（基于你施加的异常状态）",
    "wind skills which can be boosted by elemental ground surfaces count": "可受元素地面增强的疾风技能视为同时获得点燃、感电与冰缓地面加成",
    "as being boosted by ignited, shocked, and chilled ground": "（地面元素强化联动）",
    "#% reduced duration of ignite, shock and chill on enemies": "敌人身上的点燃、感电和冰缓持续时间缩短 #%",
}

_NUM_EXTRACT_RE = re.compile(r"\(\s*[+-]?\d+(?:\.\d+)?\s*-\s*[+-]?\d+(?:\.\d+)?\s*\)|[+-]?\d+(?:\.\d+)?")
_TREE_JEWEL_SLOT_RE = re.compile(r"^Tree Jewel\s*\(Node\s*([^)]+)\)$", re.I)
_GRANTS_SKILL_RE = re.compile(r"^Grants Skill:\s*(?:Level\s+(\d+)\s+)?(.+)$", re.I)
_ALLOCATES_RE = re.compile(r"^Allocates\s+(.+)$", re.I)


def _fetch_text(url: str, timeout: float = 25.0) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PoE2Workbench/1.0 (+https://github.com/poe2-workbench)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        return resp.read().decode("utf-8", errors="ignore")


def _parse_csv_dict(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2:
            k = row[0].strip()
            v = row[1].strip()
            if k and v:
                out[k] = v
    return out


def _read_json_safe(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@lru_cache(maxsize=1)
def load_game_terms() -> dict[str, Any]:
    path = affix_updater.resolve_active_file(BUNDLED_GAME_TERMS_PATH, RUNTIME_GAME_TERMS_PATH)
    data = _read_json_safe(path)
    return {
        "generatedAt": data.get("generatedAt") or "",
        "classes": {**DEFAULT_CLASSES_ZH, **(data.get("classes") or {})},
        "ascendancies": {**DEFAULT_ASCENDANCIES_ZH, **(data.get("ascendancies") or {})},
        "slots": {**SLOT_ZH_MAP, **(data.get("slots") or {})},
        "itemClasses": {**ITEM_CLASS_ZH_MAP, **(data.get("itemClasses") or {})},
        "uniques": data.get("uniques") or {},
        "bases": data.get("bases") or {},
        "gems": data.get("gems") or {},
        "passives": data.get("passives") or {},
        "static": data.get("static") or {},
    }


@lru_cache(maxsize=1)
def load_mods_zh_map() -> dict[str, str]:
    path = affix_updater.resolve_active_file(BUNDLED_MODS_ZH_MAP_PATH, RUNTIME_MODS_ZH_MAP_PATH)
    raw = _read_json_safe(path)
    out = dict(EXTRA_MODS_ZH)
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str) and k and v:
            out[k] = v
    return out


def clear_terms_cache() -> None:
    load_game_terms.cache_clear()
    load_mods_zh_map.cache_clear()


def translate_term(text: str, category: str = "any", lang: str = "zh") -> str:
    raw = (text or "").strip()
    if not raw or lang == "en":
        return raw
    terms = load_game_terms()
    if category != "any":
        bucket = terms.get(category) or {}
        if raw in bucket:
            return str(bucket[raw])
    for key in ("classes", "ascendancies", "slots", "itemClasses", "uniques", "gems", "passives", "bases", "static"):
        bucket = terms.get(key) or {}
        if raw in bucket:
            return str(bucket[raw])
    return raw


def translate_slot(slot: str, lang: str = "zh") -> str:
    raw = (slot or "").strip()
    if not raw or lang == "en":
        return raw
    m = _TREE_JEWEL_SLOT_RE.match(raw)
    if m:
        return f"天赋珠宝孔 (节点 {m.group(1)})"
    terms = load_game_terms()
    return str((terms.get("slots") or {}).get(raw) or (terms.get("itemClasses") or {}).get(raw) or raw)


def translate_mod_line(line: str, lang: str = "zh") -> str:
    raw = (line or "").strip()
    if not raw or lang == "en":
        return raw

    m_skill = _GRANTS_SKILL_RE.match(raw)
    if m_skill:
        lvl, skill_name = m_skill.group(1), m_skill.group(2).strip()
        zh_skill = translate_term(skill_name, "gems", "zh")
        return f"赋予技能：{lvl} 级 {zh_skill}" if lvl else f"赋予技能：{zh_skill}"

    m_alloc = _ALLOCATES_RE.match(raw)
    if m_alloc:
        notable = m_alloc.group(1).strip()
        return f"配置 {translate_term(notable, 'passives', 'zh')}"

    mods_map = load_mods_zh_map()
    norm = affix_updater.normalize_stat_template(raw)
    tmpl = mods_map.get(norm)
    if not tmpl:
        return raw

    nums = _NUM_EXTRACT_RE.findall(raw)
    if not nums:
        return tmpl

    it = iter(nums)

    def _repl(match: re.Match[str]) -> str:
        val = next(it, "#")
        pos = match.start()
        if pos > 0 and tmpl[pos - 1] == "+" and val.startswith("+"):
            return val[1:]
        return val

    return re.sub(r"#", _repl, tmpl)


def build_mods_zh_map_from_trade_cache(trade_cache: dict[str, Any] | None = None) -> dict[str, str]:
    if trade_cache is None:
        trade_path = affix_updater.resolve_active_file(
            affix_updater.BUNDLED_TRADE_STATS_PATH,
            affix_updater.RUNTIME_TRADE_STATS_PATH,
        )
        trade_cache = _read_json_safe(trade_path)

    out: dict[str, str] = {}
    # 优先保留 explicit/implicit/rune 等高优先级类别
    entries = sorted(
        (e for e in (trade_cache.get("entries") or []) if isinstance(e, dict)),
        key=lambda e: affix_updater._TRADE_CATEGORY_PRIORITY.get(str(e.get("type") or ""), 8),
    )
    for e in entries:
        en_norm = str(e.get("enNorm") or "").strip()
        zh = str(e.get("zh") or "").strip()
        if en_norm and zh and en_norm not in out:
            out[en_norm] = zh
    for k, v in EXTRA_MODS_ZH.items():
        out.setdefault(k, v)
    return out


def build_game_terms_from_sources(*, timeout: float = 25.0) -> dict[str, Any]:
    """从 RePoE PoE2 + PoeCharm2 zh-rCN + Trade2 Static 汇总轻量 PoE2 双语词典。"""
    def fetch_json(url: str) -> Any:
        return json.loads(_fetch_text(url, timeout=timeout))

    # 1. PoeCharm2 zh-rCN 词典
    tree_csv = _parse_csv_dict(_fetch_text(f"{POECHARM_ZH_CN_BASE}/tree_dn.csv", timeout=timeout))
    gems_csv = _parse_csv_dict(_fetch_text(f"{POECHARM_ZH_CN_BASE}/Items_Gems.txt.csv", timeout=timeout))
    uniques_csv = _parse_csv_dict(_fetch_text(f"{POECHARM_ZH_CN_BASE}/Uniques.txt.csv", timeout=timeout))
    gui_csv = _parse_csv_dict(_fetch_text(f"{POECHARM_ZH_CN_BASE}/GUI.csv", timeout=timeout))
    bases_csv: dict[str, str] = {}
    for fn in (
        "Items_Weapons.txt.csv",
        "Items_Armour.txt.csv",
        "Items_Accessories.txt.csv",
        "Items_Jewels.txt.csv",
        "Items_Flasks.txt.csv",
    ):
        bases_csv.update(_parse_csv_dict(_fetch_text(f"{POECHARM_ZH_CN_BASE}/{fn}", timeout=timeout)))

    # 2. RePoE PoE2 实体白名单过滤（剔除 PoE1 历史无关词条，保持字典轻量精确）
    repoe_chars = fetch_json(f"{affix_updater.REPOE_BASE_URL}/characters.min.json")
    repoe_ascs = fetch_json(f"{affix_updater.REPOE_BASE_URL}/ascendancies.min.json")
    repoe_bases = fetch_json(f"{affix_updater.REPOE_BASE_URL}/base_items.min.json")
    repoe_uniques = fetch_json(f"{affix_updater.REPOE_BASE_URL}/uniques.min.json")
    repoe_gems = fetch_json(f"{affix_updater.REPOE_BASE_URL}/skill_gems.min.json")
    repoe_tree = fetch_json(f"{affix_updater.REPOE_BASE_URL}/passive_skill_trees/Default.min.json")

    classes = dict(DEFAULT_CLASSES_ZH)
    for c in repoe_chars if isinstance(repoe_chars, list) else []:
        name = str(c.get("name") or "").strip()
        if name and not name.startswith("[DNT"):
            zh = tree_csv.get(name) or gui_csv.get(name) or classes.get(name)
            if zh:
                classes[name] = zh

    ascendancies = dict(DEFAULT_ASCENDANCIES_ZH)
    for v in (repoe_ascs.values() if isinstance(repoe_ascs, dict) else []):
        name = str(v.get("name") or "").strip() if isinstance(v, dict) else ""
        if name and not name.startswith("[DNT"):
            zh = tree_csv.get(name) or gui_csv.get(name) or ascendancies.get(name)
            if zh:
                ascendancies[name] = zh

    uniques: dict[str, str] = {}
    for v in (repoe_uniques.values() if isinstance(repoe_uniques, dict) else []):
        name = str(v.get("name") or "").strip() if isinstance(v, dict) else ""
        if name and not name.startswith("[DNT"):
            zh = uniques_csv.get(name)
            if zh:
                uniques[name] = zh

    bases: dict[str, str] = {}
    for v in (repoe_bases.values() if isinstance(repoe_bases, dict) else []):
        name = str(v.get("name") or "").strip() if isinstance(v, dict) else ""
        if name and not name.startswith("[DNT"):
            zh = bases_csv.get(name)
            if zh:
                bases[name] = zh

    gems: dict[str, str] = {}
    for v in (repoe_gems.values() if isinstance(repoe_gems, dict) else []):
        if not isinstance(v, dict):
            continue
        name = str((v.get("base_item") or {}).get("display_name") or v.get("display_name") or "").strip()
        if name and not name.startswith("[DNT"):
            zh = gems_csv.get(name)
            if zh:
                gems[name] = zh

    passives: dict[str, str] = {}
    raw_passives = repoe_tree.get("passives") or {}
    p_iter = raw_passives.values() if isinstance(raw_passives, dict) else raw_passives
    for node in p_iter:
        if not isinstance(node, dict):
            continue
        name = str(node.get("name") or "").strip()
        if name and not name.startswith("[DNT"):
            zh = tree_csv.get(name)
            if zh:
                passives[name] = zh

    # 3. 官方 Trade2 Static API（符文 / 精华 / 通货 / 裂隙等，按 id 1:1 精确对齐）
    static_map: dict[str, str] = {}
    try:
        en_static = fetch_json(TRADE_STATIC_EN_URL).get("result") or []
        zh_static = fetch_json(TRADE_STATIC_ZH_URL).get("result") or []
        zh_by_id: dict[str, str] = {}
        for sec in zh_static:
            for e in sec.get("entries") or []:
                eid = str(e.get("id") or "").strip()
                txt = str(e.get("text") or "").strip()
                if eid and txt:
                    zh_by_id[eid] = txt
        for sec in en_static:
            for e in sec.get("entries") or []:
                eid = str(e.get("id") or "").strip()
                txt = str(e.get("text") or "").strip()
                if eid and txt and eid in zh_by_id:
                    static_map[txt] = zh_by_id[eid]
    except Exception:
        pass

    return {
        "generatedAt": affix_updater._now_iso() if hasattr(affix_updater, "_now_iso") else __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "source": {
            "repoe": affix_updater.REPOE_BASE_URL,
            "poecharm2": POECHARM_ZH_CN_BASE,
            "tradeStatic": TRADE_STATIC_ZH_URL,
        },
        "classes": dict(sorted(classes.items())),
        "ascendancies": dict(sorted(ascendancies.items())),
        "slots": dict(SLOT_ZH_MAP),
        "itemClasses": dict(ITEM_CLASS_ZH_MAP),
        "uniques": dict(sorted(uniques.items())),
        "bases": dict(sorted(bases.items())),
        "gems": dict(sorted(gems.items())),
        "passives": dict(sorted(passives.items())),
        "static": dict(sorted(static_map.items())),
    }


def update_game_terms_files(*, write_repo: bool = False, timeout: float = 25.0) -> dict[str, Any]:
    terms_target = BUNDLED_GAME_TERMS_PATH if write_repo else RUNTIME_GAME_TERMS_PATH
    mods_target = BUNDLED_MODS_ZH_MAP_PATH if write_repo else RUNTIME_MODS_ZH_MAP_PATH

    terms = build_game_terms_from_sources(timeout=timeout)
    mods_map = build_mods_zh_map_from_trade_cache()

    affix_updater._atomic_write_json(terms_target, terms, compact=True)
    affix_updater._atomic_write_json(mods_target, mods_map, compact=True)
    clear_terms_cache()

    return {
        "ok": True,
        "termsPath": terms_target,
        "modsPath": mods_target,
        "counts": {
            "classes": len(terms["classes"]),
            "ascendancies": len(terms["ascendancies"]),
            "uniques": len(terms["uniques"]),
            "bases": len(terms["bases"]),
            "gems": len(terms["gems"]),
            "passives": len(terms["passives"]),
            "static": len(terms["static"]),
            "mods": len(mods_map),
        },
    }
