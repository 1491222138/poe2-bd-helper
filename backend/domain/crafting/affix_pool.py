"""RePoE 全槽位词缀池 + PoB2 精华表 + 官方 Trade2 中英双语词缀映射。"""
from __future__ import annotations

import functools
import json
import os
import re
from typing import Any

from backend.config import pob2_src
from backend.data.affix_updater import (
    BUNDLED_AFFIX_LIBRARY_PATH,
    BUNDLED_TRADE_STATS_PATH,
    RUNTIME_AFFIX_LIBRARY_PATH,
    RUNTIME_TRADE_STATS_PATH,
    normalize_stat_template,
    resolve_active_file,
)

_ESSENCE_ENTRY_RE = re.compile(
    r'\["([^"]+)"\]\s*=\s*\{\s*name\s*=\s*"([^"]+)",\s*type\s*=\s*"([^"]+)",\s*'
    r'tierLevel\s*=\s*(\d+),\s*mods\s*=\s*\{(.*?)\}\s*,?\s*\}',
    re.S,
)
_ESSENCE_MOD_RE = re.compile(r'\["([^"]+)"\]\s*=\s*"([^"]+)"')
_NUM_RE = re.compile(r"\(\s*[+-]?\d+(?:\.\d+)?\s*-\s*[+-]?\d+(?:\.\d+)?\s*\)|[+-]?\d+(?:\.\d+)?")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 稀有/高价值词缀组的校准权重（当 RePoE 精简库为均匀权重 1 时提供贴近 PoE2 实战的概率权重）
_GROUP_RELATIVE_WEIGHTS: dict[str, float] = {
    "IncreaseSocketedGemLevel": 220.0,
    "AllSkillGemLevel": 180.0,
    "Spirit": 280.0,
    "MovementVelocity": 420.0,
    "CriticalStrikeChanceIncrease": 550.0,
    "CriticalStrikeMultiplier": 550.0,
    "IncreasedAttackSpeed": 620.0,
    "IncreasedCastSpeed": 620.0,
    "LocalPhysicalDamagePercent": 650.0,
    "IncreasedWeaponElementalDamagePercent": 650.0,
    "AllResistances": 520.0,
    "ChaosResistance": 460.0,
    "MaximumResistances": 250.0,
    "ItemFoundRarityIncrease": 550.0,
    "ItemFoundRarityIncreasePrefix": 550.0,
    "IncreasedLife": 1000.0,
    "IncreasedMana": 1000.0,
    "IncreasedEnergyShield": 950.0,
    "DefencesPercent": 900.0,
    "FireResistance": 1000.0,
    "ColdResistance": 1000.0,
    "LightningResistance": 1000.0,
    "Strength": 1000.0,
    "Dexterity": 1000.0,
    "Intelligence": 1000.0,
}

# 槽位 -> PoB2 Essence.lua 键名映射
_CLASS_TO_ESSENCE_SLOT: dict[str, str] = {
    "Amulets": "Amulet",
    "Rings": "Ring",
    "Belts": "Belt",
    "Body Armours": "Body Armour",
    "Helmets": "Helmet",
    "Gloves": "Gloves",
    "Boots": "Boots",
    "Shields": "Shield",
    "Bucklers": "Buckler",
    "Foci": "Focus",
    "Quivers": "Quiver",
    "Spears": "Spear",
    "Bows": "Bow",
    "Crossbows": "Crossbow",
    "Wands": "Wand",
    "Staves": "Staff",
    "Quarterstaves": "Warstaff",
    "Sceptres": "Sceptre",
    "One Hand Swords": "One Hand Sword",
    "Two Hand Swords": "Two Hand Sword",
    "One Hand Axes": "One Hand Axe",
    "Two Hand Axes": "Two Hand Axe",
    "One Hand Maces": "One Hand Mace",
    "Two Hand Maces": "Two Hand Mace",
    "Daggers": "Dagger",
    "Claws": "Claw",
    "Flails": "Flail",
    "Talismans": "Talisman",
}

# RePoE 组名与 Trade2 英文标准模板的已知差异桥接（例如 RePoE 抗性写为 "+#% to Resistances"）
_GROUP_TO_EN_HINTS: dict[str, tuple[str, ...]] = {
    "FireResistance": ("+#% to fire resistance", "#% to fire resistance"),
    "ColdResistance": ("+#% to cold resistance", "#% to cold resistance"),
    "LightningResistance": ("+#% to lightning resistance", "#% to lightning resistance"),
    "ChaosResistance": ("+#% to chaos resistance", "#% to chaos resistance"),
    "AllResistances": ("+#% to all elemental resistances", "#% to all elemental resistances"),
    "MaximumFireResist": ("+#% to maximum fire resistance",),
    "MaximumColdResist": ("+#% to maximum cold resistance",),
    "MaximumLightningResistance": ("+#% to maximum lightning resistance",),
    "MaximumChaosResistance": ("+#% to maximum chaos resistance",),
    "MaximumResistances": ("+#% to all maximum elemental resistances",),
    "MovementVelocity": ("#% increased movement speed",),
    "Spirit": ("+# to spirit", "# to spirit"),
    "IncreasedLife": ("+# to maximum life", "# to maximum life"),
    "IncreasedMana": ("+# to maximum mana", "# to maximum mana"),
    "IncreasedEnergyShield": ("+# to maximum energy shield", "# to maximum energy shield"),
    "IncreasedPhysicalDamageReductionRating": ("+# to armour", "# to armour"),
    "IncreasedEvasionRating": ("+# to evasion rating", "# to evasion rating"),
    "ItemFoundRarityIncrease": ("#% increased rarity of items found",),
    "ItemFoundRarityIncreasePrefix": ("#% increased rarity of items found",),
    "CriticalStrikeMultiplier": ("+#% to critical damage bonus", "#% to critical damage bonus"),
    "CriticalStrikeChanceIncrease": ("+#% to critical hit chance", "#% to critical hit chance"),
    "IncreasedWeaponElementalDamagePercent": ("#% increased elemental damage with attacks",),
    "LocalPhysicalDamagePercent": ("#% increased physical damage",),
    "IncreasedAttackSpeed": ("#% increased attack speed",),
    "IncreasedCastSpeed": ("#% increased cast speed",),
    "ManaRegeneration": ("#% increased mana regeneration rate",),
    "LifeRegeneration": ("regenerate # life per second",),
    "Strength": ("+# to strength", "# to strength"),
    "Dexterity": ("+# to dexterity", "# to dexterity"),
    "Intelligence": ("+# to intelligence", "# to intelligence"),
    "AllAttributes": ("+# to all attributes", "# to all attributes"),
}

# 常用中文简称/口语关键词 -> 词缀组兜底
_ZH_ALIAS_TO_GROUP: tuple[tuple[tuple[str, ...], str], ...] = (
    (("火焰抗性", "火抗"), "FireResistance"),
    (("冰霜抗性", "冰抗"), "ColdResistance"),
    (("闪电抗性", "电抗", "雷抗"), "LightningResistance"),
    (("混沌抗性", "混抗"), "ChaosResistance"),
    (("所有元素抗性", "全元素抗性", "全抗"), "AllResistances"),
    (("移动速度", "移速"), "MovementVelocity"),
    (("最大生命", "生命上限", "血量"), "IncreasedLife"),
    (("最大魔力", "魔力上限", "蓝量"), "IncreasedMana"),
    (("能量护盾", "最大能量护盾", "护盾"), "IncreasedEnergyShield"),
    (("攻击速度", "攻速"), "IncreasedAttackSpeed"),
    (("施法速度", "施速"), "IncreasedCastSpeed"),
    (("暴击伤害", "暴伤"), "CriticalStrikeMultiplier"),
    (("暴击率", "暴击几率"), "CriticalStrikeChanceIncrease"),
    (("精魂",), "Spirit"),
    (("稀有度", "掉宝"), "ItemFoundRarityIncrease"),
    (("所有技能", "技能等级"), "IncreaseSocketedGemLevel"),
    (("所有法术", "法术技能"), "AllSkillGemLevel"),
    (("全属性",), "AllAttributes"),
    (("力量",), "Strength"),
    (("敏捷",), "Dexterity"),
    (("智慧",), "Intelligence"),
    (("魔力回复", "回蓝"), "ManaRegeneration"),
    (("生命回复", "秒回"), "LifeRegeneration"),
    (("闪避值", "闪避"), "IncreasedEvasionRating"),
    (("护甲值", "护甲"), "IncreasedPhysicalDamageReductionRating"),
)

_CATEGORY_ORDER: dict[str, int] = {
    "explicit": 0,
    "implicit": 1,
    "fractured": 2,
    "desecrated": 3,
    "enchant": 4,
    "rune": 5,
    "sanctum": 6,
    "pseudo": 9,
}


def _norm_template(text: str) -> str:
    s = re.sub(r"\[([^|\]]+)\|([^\]]+)\]", r"\2", text or "")
    s = re.sub(r"\[([^\]]+)\]", r"\1", s)
    s = s.replace("\n", " ")
    s = _NUM_RE.sub("#", s)
    s = re.sub(r"\+#", "#", s)
    s = re.sub(r"[^a-z0-9#% ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _en_text_variants(text: str) -> list[str]:
    """生成英文词缀模板的多种等价规范化形式，提升 RePoE 与 Trade2 的命中率。"""
    base = normalize_stat_template(text)
    variants: list[str] = [base]

    repaired = base
    for src, dst in (
        ("energyshield", "energy shield"),
        ("esrechargerate", "energy shield recharge rate"),
        ("itemrarity", "rarity of items"),
        ("criticaldamagebonus", "critical damage bonus"),
        ("critical for spell", "critical hit chance for spells"),
        ("flammability buffmagnitude", "flammability magnitude"),
        ("attributes requirements", "attribute requirements"),
        ("reduced attribute requirements", "increased attribute requirements"),
        ("lightradius", "light radius"),
        ("stunthreshold", "stun threshold"),
        ("elementaldamage", "elemental"),
        ("maximumresistances", "maximum elemental resistances"),
        ("recoup as", "recouped as"),
        ("lifeleech", "leeches"),
        ("manaleech", "leeches"),
    ):
        repaired = repaired.replace(src, dst)
    repaired = re.sub(r"\bto attack\b", "to attacks", repaired)
    repaired = re.sub(r"\bwith attack\b", "with attacks", repaired)
    repaired = re.sub(r"\bincreased evasion\b(?! rating)", "increased evasion rating", repaired)
    repaired = re.sub(
        r"gain deflect equal to #% of evasion$",
        "gain deflection rating equal to #% of evasion rating",
        repaired,
    )
    repaired = re.sub(r"\s+", " ", repaired).strip()
    if repaired not in variants:
        variants.append(repaired)

    # Trade2 常见写法：# to maximum Life vs # maximum Life；以及 of Armour also applies -> Armour applies
    no_to = re.sub(r"\bto\b", "", repaired)
    no_to = re.sub(r"\s+", " ", no_to).strip()
    if no_to not in variants:
        variants.append(no_to)
    return variants


def _zh_compact(text: str) -> str:
    s = normalize_stat_template(text)
    return re.sub(r"[\s+\-#%：:,，。()（）]+", "", s)


@functools.lru_cache(maxsize=1)
def load_affix_library() -> dict[str, Any]:
    path = resolve_active_file(BUNDLED_AFFIX_LIBRARY_PATH, RUNTIME_AFFIX_LIBRARY_PATH)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@functools.lru_cache(maxsize=1)
def load_trade_stats() -> dict[str, Any]:
    path = resolve_active_file(BUNDLED_TRADE_STATS_PATH, RUNTIME_TRADE_STATS_PATH)
    if not os.path.isfile(path):
        return {"entries": [], "count": 0, "zhCount": 0}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"entries": [], "count": 0, "zhCount": 0}
    except Exception:
        return {"entries": [], "count": 0, "zhCount": 0}


@functools.lru_cache(maxsize=1)
def _trade_index() -> dict[str, Any]:
    raw = load_trade_stats()
    entries = raw.get("entries") or []
    by_id: dict[str, dict[str, Any]] = {}
    by_en_norm: dict[str, list[dict[str, Any]]] = {}
    by_zh_norm: dict[str, list[dict[str, Any]]] = {}
    by_zh_compact: dict[str, list[dict[str, Any]]] = {}

    sorted_entries = sorted(
        (e for e in entries if isinstance(e, dict) and e.get("id")),
        key=lambda e: (_CATEGORY_ORDER.get(str(e.get("type") or ""), 8), len(str(e.get("en") or ""))),
    )

    for e in sorted_entries:
        sid = str(e["id"])
        by_id[sid] = e
        en_norm = str(e.get("enNorm") or normalize_stat_template(str(e.get("en") or "")))
        if en_norm:
            by_en_norm.setdefault(en_norm, []).append(e)
        zh = str(e.get("zh") or "").strip()
        if zh:
            zh_norm = str(e.get("zhNorm") or normalize_stat_template(zh))
            if zh_norm:
                by_zh_norm.setdefault(zh_norm, []).append(e)
            zc = _zh_compact(zh)
            if zc:
                by_zh_compact.setdefault(zc, []).append(e)

    return {
        "by_id": by_id,
        "by_en_norm": by_en_norm,
        "by_zh_norm": by_zh_norm,
        "by_zh_compact": by_zh_compact,
        "entries": sorted_entries,
    }


def clear_library_caches() -> None:
    load_affix_library.cache_clear()
    load_trade_stats.cache_clear()
    _trade_index.cache_clear()
    load_essences.cache_clear()


def lookup_trade_stat_for_group(group: str, sample_text: str) -> dict[str, Any]:
    """根据词缀组名与英文模板，在官方 Trade2 表中查找 statId（支持复合多行词缀）与中文译名。"""
    idx = _trade_index()
    by_en = idx["by_en_norm"]

    def match_single(text_line: str, use_hints: bool) -> dict[str, Any] | None:
        candidates_norm: list[str] = []
        if use_hints and group in _GROUP_TO_EN_HINTS:
            for h in _GROUP_TO_EN_HINTS[group]:
                candidates_norm.append(normalize_stat_template(h))
        candidates_norm.extend(_en_text_variants(text_line))

        for key in candidates_norm:
            hits = by_en.get(key)
            if hits:
                return next((h for h in hits if h.get("zh")), hits[0])
        return None

    direct = match_single(sample_text, use_hints=True)
    if direct:
        sid = str(direct.get("id") or "")
        return {
            "tradeStatId": sid,
            "tradeStatIds": [sid] if sid else [],
            "zhText": str(direct.get("zh") or ""),
        }

    # 处理复合多行词缀（如 护甲闪避% + 最大生命），逐行映射后合并
    lines = [ln.strip() for ln in (sample_text or "").splitlines() if ln.strip()]
    if len(lines) > 1:
        ids: list[str] = []
        zh_parts: list[str] = []
        for ln in lines:
            hit = match_single(ln, use_hints=False)
            if hit:
                sid = str(hit.get("id") or "")
                zh = str(hit.get("zh") or "")
                if sid and sid not in ids:
                    ids.append(sid)
                if zh:
                    zh_parts.append(zh)
        if ids or zh_parts:
            return {
                "tradeStatId": ids[0] if ids else "",
                "tradeStatIds": ids,
                "zhText": " / ".join(zh_parts),
            }

    return {"tradeStatId": "", "tradeStatIds": [], "zhText": ""}


def _find_trade_entries_by_zh(query: str) -> list[dict[str, Any]]:
    idx = _trade_index()
    zh_norm = normalize_stat_template(query)
    if zh_norm and zh_norm in idx["by_zh_norm"]:
        return idx["by_zh_norm"][zh_norm]
    zc = _zh_compact(query)
    if zc and zc in idx["by_zh_compact"]:
        return idx["by_zh_compact"][zc]

    if not zc or len(zc) < 2:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    q_chars = set(zc)
    for e in idx["entries"]:
        ez = str(e.get("zh") or "")
        if not ez:
            continue
        ec = _zh_compact(ez)
        if not ec:
            continue
        if zc == ec:
            scored.append((1.0, e))
            continue
        if zc in ec or ec in zc:
            ratio = min(len(zc), len(ec)) / max(len(zc), len(ec))
            if ratio >= 0.55:
                scored.append((0.7 + 0.25 * ratio, e))
                continue
        inter = len(q_chars & set(ec))
        jaccard = inter / max(1, len(q_chars | set(ec)))
        if jaccard >= 0.6:
            scored.append((jaccard * 0.8, e))

    scored.sort(key=lambda item: (-item[0], _CATEGORY_ORDER.get(str(item[1].get("type") or ""), 8)))
    return [item[1] for item in scored[:6]]


@functools.lru_cache(maxsize=1)
def load_essences() -> list[dict[str, Any]]:
    ess_path = os.path.join(pob2_src(), "Data", "Essence.lua")
    if not os.path.isfile(ess_path):
        return []
    with open(ess_path, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    out: list[dict[str, Any]] = []
    for meta_id, name, etype, tier, mods_blob in _ESSENCE_ENTRY_RE.findall(text):
        mods = {slot: mod_id for slot, mod_id in _ESSENCE_MOD_RE.findall(mods_blob)}
        if mods:
            out.append({
                "id": meta_id,
                "name": name,
                "type": etype,
                "tierLevel": int(tier),
                "mods": mods,
            })
    return out


def list_supported_classes() -> list[str]:
    return sorted(load_affix_library()["classes"].keys())


def resolve_item_class(slot: str, base_type: str = "") -> str:
    """将 PoB 槽位名（Weapon 1 / Body Armour / Ring 1...）或基底名映射到 RePoE 装备类。"""
    lib = load_affix_library()
    classes = lib["classes"]
    s = (slot or "").strip()
    b = (base_type or "").strip()

    # 1. 直接命中类名
    if s in classes:
        return s

    # 2. 按基底名精确反查
    if b:
        b_low = b.lower()
        for cls_name, cls_data in classes.items():
            for base_entry in cls_data.get("bases") or []:
                if len(base_entry) >= 2 and str(base_entry[1]).lower() == b_low:
                    return cls_name

    sl = s.lower()
    bl = b.lower()
    combined = f"{sl} {bl}"

    if "amulet" in combined:
        return "Amulets"
    if "ring" in combined:
        return "Rings"
    if "belt" in combined:
        return "Belts"
    if "body" in combined or "chest" in combined or "vest" in combined or "robe" in combined or "armour" in sl:
        return "Body Armours"
    if "helm" in combined or "tiara" in combined or "crown" in combined or "hood" in combined:
        return "Helmets"
    if "glove" in combined or "wrap" in combined or "gauntlet" in combined:
        return "Gloves"
    if "boot" in combined or "shoe" in combined or "greave" in combined:
        return "Boots"
    if "quiver" in combined:
        return "Quivers"
    if "buckler" in combined:
        return "Bucklers"
    if "focus" in combined or "foci" in combined:
        return "Foci" if "Foci" in classes else "Shields"
    if "shield" in combined:
        return "Shields"
    if "spear" in combined:
        return "Spears"
    if "crossbow" in combined:
        return "Crossbows"
    if "bow" in combined:
        return "Bows"
    if "quarterstaff" in combined or "warstaff" in combined:
        return "Quarterstaves" if "Quarterstaves" in classes else "Two Hand Maces"
    if "staff" in combined or "staves" in combined:
        return "Staves" if "Staves" in classes else "Wands"
    if "wand" in combined:
        return "Wands"
    if "sceptre" in combined:
        return "Sceptres"
    if "dagger" in combined:
        return "Daggers"
    if "claw" in combined:
        return "Claws"
    if "flail" in combined:
        return "Flails"
    if "two hand" in combined and "sword" in combined:
        return "Two Hand Swords"
    if "sword" in combined:
        return "One Hand Swords"
    if "two hand" in combined and "axe" in combined:
        return "Two Hand Axes"
    if "axe" in combined:
        return "One Hand Axes"
    if "two hand" in combined and "mace" in combined:
        return "Two Hand Maces"
    if "mace" in combined:
        return "One Hand Maces"
    if "talisman" in combined:
        return "Talismans"
    # 兜底：若只是 "Weapon 1" 且未传基底，默认 Spears（PoE2 高频）
    if "weapon" in sl:
        return "Spears"
    return "Rings"


def _effective_weight(group: str, raw_weight: float) -> float:
    if raw_weight > 1.0:
        return float(raw_weight)
    return _GROUP_RELATIVE_WEIGHTS.get(group, 800.0)


_SINGLE_NUM_RE = re.compile(r"[+-]?(\d+(?:\.\d+)?)")


def _extract_numeric_range(text: str) -> tuple[float | None, float | None]:
    """从词缀文本如 '+(100-119) to maximum Life' 或 '+35% to Fire Resistance' 提取主数值区间。"""
    if not text:
        return None, None
    first_line = text.splitlines()[0]
    m_range = re.search(r"\(\s*[+-]?(\d+(?:\.\d+)?)\s*-\s*[+-]?(\d+(?:\.\d+)?)\s*\)", first_line)
    if m_range:
        lo, hi = float(m_range.group(1)), float(m_range.group(2))
        return min(lo, hi), max(lo, hi)
    nums = [float(x) for x in _SINGLE_NUM_RE.findall(first_line)]
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], nums[0]
    # 类似 Adds 10 to 20 Physical Damage 取均值
    avg = (nums[0] + nums[1]) / 2.0
    return avg, avg


def build_slot_pool(slot: str, ilvl: int = 82, base_type: str = "") -> dict[str, Any]:
    """按槽位与物品等级构建前缀/后缀按词缀组聚合的完整池子（含分 T 级明细与权重）。"""
    lib = load_affix_library()
    mods_meta = lib["mods"]
    item_class = resolve_item_class(slot, base_type)
    cls_data = lib["classes"][item_class]

    def aggregate(rows: list[list[Any]], side: str) -> dict[str, dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            mod_id, req_lvl, raw_w, disp = row[0], int(row[1]), float(row[2]), str(row[3])
            if req_lvl > ilvl:
                continue
            meta = mods_meta.get(mod_id)
            grp_list = meta[1] if meta and len(meta) >= 2 and meta[1] else [mod_id]
            group = str(grp_list[0])
            w = _effective_weight(group, raw_w)
            lo, hi = _extract_numeric_range(disp)
            entry = groups.get(group)
            if entry is None:
                entry = {
                    "group": group,
                    "side": side,
                    "weight": 0.0,
                    "tierCount": 0,
                    "bestText": disp,
                    "bestReqLvl": req_lvl,
                    "templates": set(),
                    "modIds": [],
                    "tiers": [],
                }
                groups[group] = entry
            entry["weight"] += w
            entry["tierCount"] += 1
            entry["modIds"].append(mod_id)
            entry["templates"].add(_norm_template(disp))
            entry["tiers"].append({
                "modId": mod_id,
                "reqLvl": req_lvl,
                "weight": w,
                "text": disp,
                "minVal": lo,
                "maxVal": hi,
            })
            if req_lvl >= entry["bestReqLvl"]:
                entry["bestReqLvl"] = req_lvl
                entry["bestText"] = disp

        # 按 reqLvl 从高到低标定 T 级（T1 = 最高档）
        for entry in groups.values():
            entry["tiers"].sort(key=lambda t: (-t["reqLvl"], -(t["maxVal"] or 0.0)))
            for idx, t in enumerate(entry["tiers"], start=1):
                t["tierRank"] = idx
        return groups

    prefixes = aggregate(cls_data.get("prefixes") or [], "prefix")
    suffixes = aggregate(cls_data.get("suffixes") or [], "suffix")

    return {
        "itemClass": item_class,
        "ilvl": ilvl,
        "prefixes": prefixes,
        "suffixes": suffixes,
    }


def compute_orb_pool_weight(group_entry: dict[str, Any], orb_min_req_lvl: int = 0) -> float:
    """PoE2 高阶/完美通货截断低等级词缀（如 Greater 剔除 <35, Perfect 剔除 <50）后的词缀组总权重。"""
    tiers = group_entry.get("tiers") or []
    if not tiers:
        return float(group_entry.get("weight") or 0.0)
    if orb_min_req_lvl <= 0:
        return sum(float(t["weight"]) for t in tiers)
    kept = [float(t["weight"]) for t in tiers if int(t["reqLvl"]) >= orb_min_req_lvl]
    return sum(kept)


def compute_qualifying_target_weight(
    group_entry: dict[str, Any],
    *,
    min_tier_mode: str = "any",
    desired_value: float | None = None,
    orb_min_req_lvl: int = 0,
) -> tuple[float, int]:
    """计算某目标词缀在给定 T 级要求 + 通货等级下限下，算作「命中」的权重与合格 T 级数。"""
    tiers = group_entry.get("tiers") or []
    if not tiers:
        return float(group_entry.get("weight") or 0.0), int(group_entry.get("tierCount") or 1)

    total_tiers = len(tiers)
    mode = (min_tier_mode or "any").lower()
    if mode == "high":
        max_rank = max(1, min(2, int(round(total_tiers * 0.35 + 0.5))))
    elif mode == "mid":
        max_rank = max(1, int(round(total_tiers * 0.6)))
    else:
        max_rank = total_tiers

    qualifying: list[dict[str, Any]] = []
    for t in tiers:
        if int(t["reqLvl"]) < orb_min_req_lvl:
            continue
        if int(t["tierRank"]) > max_rank:
            continue
        # 若玩家在词缀里写了具体数值（且非 any 模式，或数值很高），过滤掉达不到 85% 下限的低 T
        if desired_value is not None and desired_value > 0 and mode != "any":
            max_v = t.get("maxVal")
            if max_v is not None and max_v < desired_value * 0.82:
                continue
        qualifying.append(t)

    # 若数值过滤太严导致全灭，但该组存在 >= orb_min_req_lvl 的最高 T1，则保底只认 T1
    if not qualifying:
        eligible = [t for t in tiers if int(t["reqLvl"]) >= orb_min_req_lvl]
        if eligible:
            qualifying = [eligible[0]]

    return sum(float(t["weight"]) for t in qualifying), len(qualifying)


def _build_matched_result(
    raw: str,
    g: dict[str, Any],
    *,
    zh_override: str = "",
    trade_id_override: str = "",
) -> dict[str, Any]:
    trade_info = lookup_trade_stat_for_group(g["group"], g["bestText"])
    lo, hi = _extract_numeric_range(raw)
    desired_val = hi if hi is not None else lo
    return {
        "input": raw,
        "matched": True,
        "group": g["group"],
        "side": g["side"],
        "sampleText": g["bestText"],
        "tierCount": g["tierCount"],
        "zhText": zh_override or trade_info["zhText"],
        "tradeStatId": trade_id_override or trade_info["tradeStatId"],
        "desiredValue": desired_val,
    }


def _match_english_query_in_pool(raw: str, pool: dict[str, Any]) -> dict[str, Any] | None:
    all_groups = list(pool["prefixes"].values()) + list(pool["suffixes"].values())
    q_low = raw.lower()

    # 1. 精确命中 group 名
    for g in all_groups:
        if g["group"].lower() == q_low:
            return _build_matched_result(raw, g)

    # 2. 归一化模板完全命中
    norm_q = _norm_template(raw)
    for g in all_groups:
        if norm_q in g["templates"]:
            return _build_matched_result(raw, g)

    # 3. 语义关键词特判
    for elem, grp_name in (
        ("fire resistance", "FireResistance"),
        ("cold resistance", "ColdResistance"),
        ("lightning resistance", "LightningResistance"),
        ("chaos resistance", "ChaosResistance"),
        ("all elemental resistances", "AllResistances"),
        ("movement speed", "MovementVelocity"),
        ("maximum life", "IncreasedLife"),
        ("maximum mana", "IncreasedMana"),
        ("maximum energy shield", "IncreasedEnergyShield"),
        ("attack speed", "IncreasedAttackSpeed"),
        ("cast speed", "IncreasedCastSpeed"),
        ("to spirit", "Spirit"),
        ("level of all", "IncreaseSocketedGemLevel"),
    ):
        if elem in q_low:
            hit = next((g for g in all_groups if g["group"] == grp_name), None)
            if hit:
                return _build_matched_result(raw, hit)

    # 4. Token 重合度评分
    alias_tokens = (
        norm_q.replace("critical hit", "critical")
        .replace("critical strike", "critical")
        .replace("critical damage bonus", "criticaldamagebonus")
        .replace("energy shield", "energyshield")
        .replace("item rarity", "itemrarity")
        .replace("rarity of items found", "itemrarity found")
        .replace("to fire resistance", "to resistances fire")
        .replace("to cold resistance", "to resistances cold")
        .replace("to lightning resistance", "to resistances lightning")
        .replace("to chaos resistance", "to resistances chaos")
    )
    q_tokens = {t for t in alias_tokens.split() if len(t) >= 2 and t not in ("to", "of", "the", "with", "all", "#")}

    best: dict[str, Any] | None = None
    best_score = 0.0
    for g in all_groups:
        cand_text = " ".join(g["templates"]) + " " + g["group"].lower()
        cand_tokens = {t for t in re.split(r"[^a-z0-9]+", cand_text) if len(t) >= 2}
        if not q_tokens or not cand_tokens:
            continue
        inter = len(q_tokens & cand_tokens)
        score = inter / max(1, len(q_tokens))
        if score > best_score:
            best_score = score
            best = g

    if best and best_score >= 0.45:
        return _build_matched_result(raw, best)
    return None


def match_affix_to_pool(query: str, pool: dict[str, Any]) -> dict[str, Any]:
    """把一条英文/中文词缀文本或词缀组名精准匹配到槽位池中的词缀组与前/后缀归属。"""
    raw = (query or "").strip()
    if not raw:
        return {
            "input": raw,
            "matched": False,
            "group": "",
            "side": "unknown",
            "sampleText": "",
            "tierCount": 0,
            "zhText": "",
            "tradeStatId": "",
        }

    # 若含中文，先通过官方 Trade2 中英映射表与中文别名桥接回英文模板 / 词缀组
    if _CJK_RE.search(raw):
        trade_hits = _find_trade_entries_by_zh(raw)
        for th in trade_hits:
            en_tmpl = str(th.get("en") or "")
            if not en_tmpl:
                continue
            matched = _match_english_query_in_pool(en_tmpl, pool)
            if matched:
                matched["input"] = raw
                if not matched.get("zhText"):
                    matched["zhText"] = str(th.get("zh") or "")
                if not matched.get("tradeStatId"):
                    matched["tradeStatId"] = str(th.get("id") or "")
                return matched

        all_groups = list(pool["prefixes"].values()) + list(pool["suffixes"].values())
        for keywords, grp_name in _ZH_ALIAS_TO_GROUP:
            if any(kw in raw for kw in keywords):
                hit = next((g for g in all_groups if g["group"] == grp_name), None)
                if hit:
                    return _build_matched_result(raw, hit)

    direct = _match_english_query_in_pool(raw, pool)
    if direct:
        return direct

    return {
        "input": raw,
        "matched": False,
        "group": "",
        "side": "unknown",
        "sampleText": "",
        "tierCount": 0,
        "zhText": "",
        "tradeStatId": "",
    }


def list_essences_for_slot(
    slot: str,
    base_type: str = "",
    rates: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """列出该槽位可用的全部 PoB2 精华（含锁定词缀文本、前/后缀归属与实时单价）。"""
    from backend.market.ninja_rates import lookup_essence_price

    item_class = resolve_item_class(slot, base_type)
    ess_slot = _CLASS_TO_ESSENCE_SLOT.get(item_class, item_class.rstrip("s"))
    mods_meta = load_affix_library()["mods"]

    out: list[dict[str, Any]] = []
    for e in load_essences():
        mod_id = e["mods"].get(ess_slot)
        if not mod_id:
            continue
        meta = mods_meta.get(mod_id)
        mod_text = str(meta[0]) if meta else mod_id
        group = str(meta[1][0]) if meta and len(meta) >= 2 and meta[1] else mod_id
        side = str(meta[2]) if meta and len(meta) >= 3 else "unknown"
        price = lookup_essence_price(e["name"], rates) if rates else None
        out.append({
            "name": e["name"],
            "type": e["type"],
            "tierLevel": e["tierLevel"],
            "modId": mod_id,
            "modText": mod_text,
            "group": group,
            "side": side,
            "priceDivine": round(price, 4) if price is not None else None,
        })

    out.sort(key=lambda x: (x["type"], -x["tierLevel"]))
    return out


def list_affix_options_for_slot(slot: str, ilvl: int = 82, base_type: str = "") -> list[dict[str, Any]]:
    """供前端下拉选择：返回某槽位在给定 ilvl 下的全部可用词缀组（含中文译名与市集 Stat ID）。"""
    pool = build_slot_pool(slot, ilvl=ilvl, base_type=base_type)
    out = []
    for side_key in ("prefixes", "suffixes"):
        for g in pool[side_key].values():
            trade_info = lookup_trade_stat_for_group(g["group"], g["bestText"])
            out.append({
                "group": g["group"],
                "side": g["side"],
                "sampleText": g["bestText"],
                "tierCount": g["tierCount"],
                "zhText": trade_info["zhText"],
                "tradeStatId": trade_info["tradeStatId"],
            })
    out.sort(key=lambda x: (0 if x["side"] == "prefix" else 1, x["zhText"] or x["sampleText"]))
    return out
