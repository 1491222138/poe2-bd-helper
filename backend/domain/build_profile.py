"""目标 BD 定位提取器（规则 + 天赋树大点/升华 + 可选常驻 PoB2 引擎实算）。

输出严格对齐 packages/contracts/build-profile.ts 中的 BuildPositioning 与 BuildProfileResponse。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Callable

from backend.data import game_terms
from backend.domain import affix_analyzer, engine_facts, llm_advisor
from backend.engine import pob_parser, runefix

_TRAVEL_RE = re.compile(
    r"Travel|Righteous Descent|Shield Charge|Leap Slam|Blink|Dash|Roll|Whirling Slash|Tempest Flurry|Vaulting Impact",
    re.I,
)
_SPEED_MOD_RE = re.compile(r"(\d+)%\s+increased\s+Attack\s+Speed", re.I)
_PHYS_PCT_RE = re.compile(r"(\d+)%\s+increased\s+Physical\s+Damage", re.I)
_ADDED_DMG_RE = re.compile(r"Adds\s+(\d+)\s+to\s+(\d+)\s+[A-Za-z ]*Damage", re.I)
_SPELL_MOD_RE = re.compile(
    r"increased\s+Spell\s+Damage|to\s+Level\s+of\s+all\s+(Spell|Lightning|Fire|Cold|Chaos|Minion)\s+Skills|"
    r"increased\s+Cast\s+Speed",
    re.I,
)
_MARTIAL_BASE_RE = re.compile(
    r"\b(spear|bow|crossbow|quarterstaff|mace|axe|sword|claw|dagger|flail|halberd)\b",
    re.I,
)

# PoE2 常见高价追梦/核心暗金识别（用于预算档位与避坑提示）
_MIRROR_OR_CHASE_UNIQUES: dict[str, tuple[str, float, str]] = {
    "astramentis": ("镜级/天价核心", 40.0, "Mirror-Tier Chase Unique"),
    "temporalis": ("镜级/天价核心", 80.0, "Mirror-Tier Chase Unique"),
    "ingenuity": ("高价核心腰带", 15.0, "High-Value Core Belt"),
    "headhunter": ("高价刷图核心", 35.0, "High-Value Mapping Core"),
    "dream fragments": ("高价核心戒指", 15.0, "High-Value Core Ring"),
    "hand of wisdom and action": ("高价属性堆叠核心", 8.0, "Attribute-Stacking Core"),
    "morior invictus": ("高价孔位胸甲", 12.0, "High-Value Socket Chest"),
    "heart of the well": ("高溢价极品暗金珠宝", 15.0, "High-Value Unique Jewel"),
    "against the darkness": ("高价时空珠宝", 15.0, "High-Value Time-Lost Jewel"),
    "prism of belief": ("高价+技能等级珠宝", 18.0, "High-Value +Skill Jewel"),
    "megalomaniac": ("多变体极品珠宝", 12.0, "Multi-Notable Unique Jewel"),
    "controlled metamorphosis": ("高价跳点暗金珠宝", 10.0, "Radius-Jump Unique Jewel"),
    "from nothing": ("高价跳点暗金珠宝", 10.0, "Radius-Jump Unique Jewel"),
    "kalandra's touch": ("镜子级复制戒指", 35.0, "Mirror-Tier Ring"),
    "mageblood": ("镜子级腰带", 45.0, "Mirror-Tier Belt"),
    "the adorned": ("镜级腐化珠宝放大核心", 35.0, "Mirror-Tier Corrupted Jewel Multiplier"),
    "voices": ("镜级多珠宝孔核心", 30.0, "Mirror-Tier Multi-Socket Jewel"),
    "runeseeker's call": ("高价全符文放大核心武器", 25.0, "High-Value Socketed-Rune Core Weapon"),
    "svalinn": ("高价幸运格挡盾", 15.0, "High-Value Lucky-Block Shield"),
    "rite of passage": ("高价护符", 12.0, "High-Value Charm"),
    "melting maelstrom": ("高价魔力药剂", 6.0, "High-Value Mana Flask"),
    "uhtred's chalice": ("高价溢流魔力药剂", 6.0, "High-Value Overflow Mana Flask"),
    "blood of the warrior": ("功能怒气生命药剂", 4.0, "Utility Rage Life Flask"),
}

# 词缀归类与重要度（既服务核心词缀聚类，也服务单槽高亮）
_AFFIX_ARCHETYPES: list[tuple[str, re.Pattern[str], int, str]] = [
    ("+技能等级", re.compile(r"to\s+level\s+of\s+all\s+.*skills", re.I), 95, "+Skill Levels"),
    ("额外投射物/箭矢", re.compile(r"additional\s+(arrow|projectile)", re.I), 90, "Additional Projectiles"),
    ("精魂 (Spirit)", re.compile(r"to\s+spirit\b|increased\s+spirit\b", re.I), 86, "Spirit"),
    ("额外元素/混沌增伤 (Gain as Extra)", re.compile(r"gain\s+.*as\s+extra\s+.*damage", re.I), 85, "Gain as Extra Damage"),
    ("投射物速度 (Projectile Speed)", re.compile(r"increased\s+projectile\s+speed", re.I), 83, "Projectile Speed"),
    ("元素异常增伤 (Ailment Scaling)", re.compile(r"for\s+each\s+(type\s+of\s+)?elemental\s+ailment|elemental\s+ailment", re.I), 83, "Elemental Ailment Scaling"),
    ("移动速度", re.compile(r"increased\s+movement\s+speed", re.I), 82, "Movement Speed"),
    ("攻速 / 施法 / 技能速度", re.compile(r"increased\s+(attack|cast|skill)\s+speed", re.I), 78, "Attack / Cast / Skill Speed"),
    ("武器/法术高点伤", re.compile(r"adds\s+\d+\s+to\s+\d+\s+(physical|lightning|cold|fire|chaos)\s+damage", re.I), 76, "Flat Added Damage"),
    ("暴击率 / 暴击伤害", re.compile(r"critical\s+(hit\s+|strike\s+)?chance|critical\s+(spell\s+)?damage\s+bonus", re.I), 75, "Crit Chance / Crit Bonus"),
    ("最大魔力 (Mana)", re.compile(r"to\s+maximum\s+mana\b|increased\s+maximum\s+mana\b", re.I), 74, "Maximum Mana"),
    ("物理 / 法术 / 元素伤害%", re.compile(r"increased\s+(physical|spell|elemental|lightning|cold|fire|chaos|projectile|minion)\s+damage", re.I), 72, "Increased Damage %"),
    ("高能量护盾 (ES)", re.compile(r"to\s+maximum\s+energy\s+shield|increased\s+energy\s+shield", re.I), 68, "Energy Shield (ES)"),
    ("魔力回复 / 魔耗效率", re.compile(r"increased\s+mana\s+regeneration\s+rate|mana\s+cost\s+efficiency", re.I), 66, "Mana Regen / Cost Efficiency"),
    ("高闪避 / 偏转 / 护甲", re.compile(r"increased\s+(evasion|armour)|to\s+evasion\s+rating|deflect", re.I), 64, "Evasion / Deflection / Armour"),
    ("镶嵌物效果增幅", re.compile(r"increased\s+effect\s+of\s+socketed\s+augment\s+items", re.I), 63, "Socketed Augment Effect"),
    ("最大生命", re.compile(r"to\s+maximum\s+life\b", re.I), 62, "Maximum Life"),
    ("全抗 / 混沌抗性", re.compile(r"to\s+chaos\s+resistance|to\s+all\s+elemental\s+resistances", re.I), 50, "All Ele / Chaos Resistance"),
    ("属性堆叠 (力/敏/智)", re.compile(r"to\s+all\s+attributes|to\s+(strength|dexterity|intelligence)\b", re.I), 44, "Attributes (Str/Dex/Int)"),
    ("物品稀有度 (MF)", re.compile(r"increased\s+rarity\s+of\s+items\s+found", re.I), 42, "Item Rarity (MF)"),
    ("元素单抗", re.compile(r"to\s+(fire|cold|lightning)\s+resistance", re.I), 24, "Single Elemental Resistance"),
]

# 构筑核心基石优先级（用于一句话总结与排序，避免把先知 Entwined Realities 跳板锚点基石排在最前）
_PRIMARY_KEYSTONE_PRIORITY: dict[str, int] = {
    "chaos inoculation": 100,
    "eldritch battery": 96,
    "mind over matter": 94,
    "giant's blood": 92,
    "dance with death": 90,
    "acrobatics": 88,
    "blood magic": 86,
    "resolute technique": 84,
    "avatar of fire": 82,
    "pain attunement": 80,
    "ancestral bond": 78,
    "zealot's oath": 76,
    "eternal youth": 74,
    "ghost dance": 72,
    "iron reflexes": 70,
}

# 常见精魂保留技能识别（用于在无模拟数据时也识别 PoB 多开精魂光环）
_SPIRIT_SKILL_RE = re.compile(
    r"^(Archmage|Cast on |Blink|Trinity|Elemental Conflux|Mana Remnants|Siphon Elements|"
    r"Grim Feast|Ghost Dance|Wind Dancer|Herald of |Blasphemy|Lingering Illusion|"
    r"Combat Frenzy|Barrier Invocation|Raging Spirits|Alchemist's Boon|Time of Need|"
    r"Berserk|Scavenged Plating|Overwhelming Presence|Arctic Armour|Clarity|Vitality|"
    r"Precision|Cannibalism|Herbalism|Skeletal )",
    re.I,
)

_KEYSTONE_CAVEATS: dict[str, tuple[str, str]] = {
    "chaos inoculation": (
        "点了「异灵之体 (CI)」血量锁 1 点，必须保证高护盾池与晕眩/异常阈值补足。",
        "Chaos Inoculation locks Life to 1; maintain a high ES pool and stun/ailment thresholds.",
    ),
    "mind over matter": (
        "点了「心灵升华 (MoM)」魔力承伤，需盯紧魔力回复与留蓝余量，防空蓝断档。",
        "Mind over Matter takes damage from Mana; keep mana recovery and unreserved headroom sufficient.",
    ),
    "eldritch battery": (
        "点了「异能魔力 (EB)」护盾转魔力，注意实际生存池切换与回蓝机制配合。",
        "Eldritch Battery converts ES to Mana; ensure recovery mechanics match your new EHP pool.",
    ),
    "acrobatics": (
        "点了「杂耍 (Acrobatics)」会大幅削减面板闪避值，需高基数闪避支撑。",
        "Acrobatics heavily reduces Evasion Rating; high base Evasion is required to compensate.",
    ),
    "dance with death": (
        "点了「与死共舞」需要空副手才能吃满技能速度，换盾牌/副手前务必同步调天赋。",
        "Dance with Death requires an empty off-hand for Skill Speed; respec before equipping a shield/focus.",
    ),
    "giant's blood": (
        "点了「巨人之血」武器属性需求翻倍（3x），换装时极易触发力量不足红线。",
        "Giant's Blood triples weapon attribute requirements; watch Strength closely when swapping gear.",
    ),
    "avatar of fire": (
        "点了「火之化身」仅能造成火焰伤害，避免误买纯冰/电额外伤害词缀浪费预算。",
        "Avatar of Fire prevents non-Fire damage; avoid paying extra for pure Cold/Lightning mods.",
    ),
    "resolute technique": (
        "点了「必中 (RT)」无法暴击，切勿为暴击率/暴伤词缀付溢价。",
        "Resolute Technique disables crits; never pay extra for Crit Chance or Crit Damage Bonus.",
    ),
}

_SEVERITY_ORDER = {"fail": 0, "warn": 1, "info": 2}


def _num(v: Any, default: float = 0.0) -> float:
    try:
        n = float(v)
        return n if n == n else default
    except (TypeError, ValueError):
        return default


def score_mod_importance(mod_line: str, slot: str = "") -> int:
    t = (mod_line or "").lower()
    s = (slot or "").lower()
    score = 10
    if re.search(r"to level of all .*skills", t):
        score += 95
    if re.search(r"to spirit\b|increased spirit\b", t):
        score += 85
    if re.search(r"additional arrow|additional projectile", t):
        score += 90
    if re.search(r"gain .* as extra .* damage", t):
        score += 85
    if re.search(r"critical (hit |strike )?chance", t):
        score += 72
    if re.search(r"critical (spell )?damage bonus", t):
        score += 70
    if re.search(r"increased attack speed|increased cast speed|increased skill speed", t):
        score += 75
    if re.search(r"adds .* to .* (physical|lightning|cold|fire|chaos) damage", t):
        score += 74
    if re.search(r"increased physical damage|increased spell damage|increased elemental damage", t):
        score += 72
    if re.search(r"increased movement speed", t):
        score += 98 if "boot" in s else 75
    if re.search(r"to maximum mana\b|increased maximum mana\b", t):
        score += 68
    if re.search(r"increased mana regeneration rate|mana cost efficiency", t):
        score += 60
    if re.search(r"increased effect of socketed (augment items|runes|soul cores|items)|explicit speed modifier magnitudes|increased effect of suffixes|increased effect of jewel socket", t):
        score += 76
    if re.search(r"increased projectile speed|increased projectile damage", t):
        score += 72
    if re.search(r"deflection rating equal to|deflect", t):
        score += 72
    if re.search(r"aura skills have .* increased magnitudes|gain arcane surge|remnants can be collected", t):
        score += 56
    if re.search(r"to maximum life\b", t):
        score += 62
    if re.search(r"increased (evasion|energy shield(?!\s+recharge)|armour)", t):
        score += 68 if any(k in s for k in ("body", "helmet", "shield", "focus")) else 35
    if re.search(r"to maximum energy shield", t):
        score += 64
    if re.search(r"to chaos resistance", t):
        score += 55
    if re.search(r"to all elemental resistances", t):
        score += 58
    if re.search(r"to (fire|cold|lightning) resistance", t):
        score += 42
    if re.search(r"increased rarity of items found", t):
        score += 45
    if re.search(r"charm slot", t):
        score += 65
    if re.search(r"light radius|stun threshold|thorns|life regeneration|reduced attribute requirements|accuracy rating", t):
        score -= 30
    if re.search(r"to (strength|dexterity|intelligence)\b", t) and "all attributes" not in t:
        score -= 5
    return score


def _item_all_mods(it: dict[str, Any]) -> list[str]:
    """合并装备词缀与符文效果行（跳过纯符文名），避免全符文武器（如 Runeseeker's Call）漏词条。"""
    mods = list(it.get("mods") or [])
    for r in it.get("socketedRunes") or []:
        if re.search(r"\d|gain |increased |reduced |to level |to spirit|to maximum |to all |allocates |grants ", r, re.I):
            mods.append(r)
    return mods


def classify_weapon_set(items: list[dict[str, Any]]) -> dict[str, Any]:
    text = " ".join(" ".join(_item_all_mods(it)) for it in (items or []))
    bases = " ".join((it.get("baseType") or "") for it in (items or [])).lower()
    names = " ".join((it.get("name") or "") for it in (items or [])).lower()

    attack_speed = sum(int(m) for m in _SPEED_MOD_RE.findall(text))
    phys_pct = max([int(m) for m in _PHYS_PCT_RE.findall(text)] or [0])
    added_avg = 0.0
    for low, high in _ADDED_DMG_RE.findall(text):
        added_avg += (float(low) + float(high)) / 2.0
    has_crit = bool(re.search(r"Critical Hit Chance|Critical Damage Bonus", text, re.I))
    has_spell = bool(_SPELL_MOD_RE.search(text))
    has_martial = bool(_MARTIAL_BASE_RE.search(bases))
    has_sceptre = "sceptre" in bases
    grants_skill = bool(re.search(r"Grants Skill", text, re.I))
    has_shield_def = ("shield" in bases or "svalinn" in names) and bool(re.search(r"Block|Ward|Armour|Cast on Block", text, re.I))

    if attack_speed >= 20 and phys_pct == 0 and added_avg < 50 and not has_spell:
        return {
            "role": "speed",
            "label": "攻速/位移工具组",
            "detail": f"攻速合计 +{attack_speed}%，无主力物理%/高点伤 —— 专职跑图/叠层/触发",
            "attackSpeed": attack_speed,
            "en": {
                "label": "Speed / Utility Set",
                "detail": f"+{attack_speed}% total Attack Speed with low base hit — dedicated travel/stacking set",
            },
        }
    if has_martial and (phys_pct > 0 or added_avg >= 50 or has_crit):
        parts = []
        parts_en = []
        if phys_pct > 0:
            parts.append(f"物理 {phys_pct}%")
            parts_en.append(f"{phys_pct}% Phys")
        if added_avg >= 50:
            parts.append(f"点伤均值 {round(added_avg)}")
            parts_en.append(f"Avg Flat {round(added_avg)}")
        if has_crit:
            parts.append("暴击")
            parts_en.append("Crit")
        if has_sceptre:
            parts.append("副手精魂权杖")
            parts_en.append("Off-hand Spirit Sceptre")
        if attack_speed > 0:
            parts.append(f"攻速 +{attack_speed}%")
            parts_en.append(f"+{attack_speed}% AS")
        return {
            "role": "damage",
            "label": "主输出攻击组",
            "detail": " + ".join(parts),
            "attackSpeed": attack_speed,
            "en": {
                "label": "Main Attack DPS Set",
                "detail": " + ".join(parts_en),
            },
        }
    if has_spell and (any(k in bases for k in ("wand", "staff", "sceptre", "focus", "fork")) or (phys_pct == 0 and added_avg < 40)):
        parts = []
        parts_en = []
        if re.search(r"to Level of all .*Skills", text, re.I):
            parts.append("+技能等级")
            parts_en.append("+Skill Levels")
        if re.search(r"Spell Damage", text, re.I):
            parts.append("法伤")
            parts_en.append("Spell Dmg")
        if re.search(r"Gain .* as Extra", text, re.I):
            parts.append("额外元素伤")
            parts_en.append("Gain as Extra")
        if re.search(r"Cast Speed", text, re.I):
            parts.append("施速")
            parts_en.append("Cast Speed")
        if re.search(r"Spirit", text, re.I):
            parts.append("精魂")
            parts_en.append("Spirit")
        return {
            "role": "caster",
            "label": "法术/召唤输出组",
            "detail": " + ".join(parts) if parts else "法术/增益词缀驱动",
            "attackSpeed": attack_speed,
            "en": {
                "label": "Caster / Minion Set",
                "detail": " + ".join(parts_en) if parts_en else "Spell / utility mod scaling",
            },
        }
    if has_shield_def and not has_spell and phys_pct == 0:
        return {
            "role": "utility",
            "label": "防御/格挡副手组",
            "detail": "盾牌格挡 / 光环减伤 / 触发承伤工具组",
            "attackSpeed": attack_speed,
            "en": {
                "label": "Defense / Block Swap Set",
                "detail": "Shield block / defensive aura / Cast-on-Block swap set",
            },
        }
    if phys_pct > 0 or added_avg >= 50 or has_crit:
        parts = []
        parts_en = []
        if phys_pct > 0:
            parts.append(f"物理 {phys_pct}%")
            parts_en.append(f"{phys_pct}% Phys")
        if added_avg >= 50:
            parts.append(f"点伤均值 {round(added_avg)}")
            parts_en.append(f"Avg Flat {round(added_avg)}")
        if has_crit:
            parts.append("暴击")
            parts_en.append("Crit")
        if attack_speed > 0:
            parts.append(f"攻速 +{attack_speed}%")
            parts_en.append(f"+{attack_speed}% AS")
        return {
            "role": "damage",
            "label": "主输出武器组",
            "detail": " + ".join(parts),
            "attackSpeed": attack_speed,
            "en": {
                "label": "Main DPS Set",
                "detail": " + ".join(parts_en),
            },
        }
    if grants_skill:
        return {
            "role": "utility",
            "label": "功能/光环组",
            "detail": "提供触发技能或精魂光环",
            "attackSpeed": attack_speed,
            "en": {
                "label": "Utility / Aura Set",
                "detail": "Provides granted skills or Spirit auras",
            },
        }
    return {
        "role": "unknown",
        "label": "常规武器组",
        "detail": "",
        "attackSpeed": attack_speed,
        "en": {
            "label": "Standard Weapon Set",
            "detail": "",
        },
    }


def _pick_main_skill(parsed: dict[str, Any], sim_stats: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
    groups = parsed.get("skillGroups") or []
    flagged = next((g for g in groups if g.get("isMain")), None)

    def _resolve_group_name(g: dict[str, Any]) -> str:
        gems = g.get("gems") or []
        if not gems:
            return ""
        first_name = (gems[0].get("name") or "").strip()
        engine_name = str((sim_stats or {}).get("mainSkillName") or "").strip()
        if g.get("isMain") and engine_name:
            if first_name and first_name.lower() != engine_name.lower() and re.search(r"totem|cast on |spellslinger|blasphemy", first_name, re.I):
                return f"{engine_name} ({first_name})"
            return engine_name
        mas = g.get("mainActiveSkill")
        if isinstance(mas, int) and 1 <= mas <= len(gems):
            picked = (gems[mas - 1].get("name") or "").strip()
            if picked:
                if mas > 1 and first_name and first_name.lower() != picked.lower() and re.search(r"totem|cast on |spellslinger", first_name, re.I):
                    return f"{picked} ({first_name})"
                return picked
        active = next((x for x in gems if not re.match(r"^Support", x.get("name") or "", re.I)), gems[0])
        return active.get("name") or ""

    if flagged:
        return flagged, _resolve_group_name(flagged)

    best_group = None
    best_gem_name = ""
    best_score = -1
    for g in groups:
        gems = g.get("gems") or []
        if not gems:
            continue
        active = next((x for x in gems if not re.match(r"^Support", x.get("name") or "", re.I)), gems[0])
        # 多连主技能组优先于单颗功能技能
        score = len(gems) * 10 + int(_num(active.get("level"), 1))
        if score > best_score:
            best_score = score
            best_group = g
            best_gem_name = _resolve_group_name(g)
    return best_group, best_gem_name


def _has_travel_skill(parsed: dict[str, Any]) -> bool:
    for g in parsed.get("skillGroups") or []:
        for gem in g.get("gems") or []:
            if _TRAVEL_RE.search(gem.get("name") or ""):
                return True
    return False


def _merge_stats(parsed: dict[str, Any], sim_stats: dict[str, Any] | None = None) -> dict[str, float]:
    """统一 XML 自带 PlayerStat 与常驻 LuaJIT 实算面板，实算值优先。"""
    xml_stats = (parsed.get("character") or {}).get("stats") or {}
    out: dict[str, float] = {k: _num(v) for k, v in xml_stats.items()}
    if not sim_stats:
        return out

    mapping = {
        "CombinedDPS": sim_stats.get("dps"),
        "TotalEHP": sim_stats.get("ehp"),
        "Life": sim_stats.get("life"),
        "LifeUnreserved": sim_stats.get("lifeUnreserved"),
        "LifeUnreservedPercent": sim_stats.get("lifeUnreservedPct"),
        "ActiveTotemLimit": sim_stats.get("activeTotemLimit"),
        "EnergyShield": sim_stats.get("es"),
        "Ward": sim_stats.get("ward"),
        "DeflectChance": sim_stats.get("deflectChance"),
        "DeflectionRating": sim_stats.get("deflectionRating"),
        "Mana": sim_stats.get("mana"),
        "Spirit": sim_stats.get("spirit"),
        "SpiritUnreserved": sim_stats.get("spiritUnreserved"),
        "Armour": sim_stats.get("armour"),
        "Evasion": sim_stats.get("evasion"),
        "EvadeChance": sim_stats.get("evadeChance"),
        "EffectiveBlockChance": sim_stats.get("blockChance"),
        "Speed": sim_stats.get("speed"),
        "CritChance": sim_stats.get("critChance"),
        "PreEffectiveCritChance": sim_stats.get("preEffectiveCritChance"),
        "CritMultiplier": sim_stats.get("critMult"),
        "ManaCost": sim_stats.get("manaCost"),
        "ManaPerSecondCost": sim_stats.get("manaPerSecCost"),
        "ManaRegenRecovery": sim_stats.get("manaRegen"),
        "FireResist": sim_stats.get("fireRes"),
        "ColdResist": sim_stats.get("coldRes"),
        "LightningResist": sim_stats.get("lightningRes"),
        "ChaosResist": sim_stats.get("chaosRes"),
        "Str": sim_stats.get("str"),
        "Dex": sim_stats.get("dex"),
        "Int": sim_stats.get("int"),
        "ReqStr": sim_stats.get("reqStr"),
        "ReqDex": sim_stats.get("reqDex"),
        "ReqInt": sim_stats.get("reqInt"),
        "PhysicalMaximumHitTaken": sim_stats.get("physMaxHit"),
        "ChaosMaximumHitTaken": sim_stats.get("chaosMaxHit"),
    }
    for k, val in mapping.items():
        if val is not None:
            out[k] = _num(val)
    if sim_stats.get("moveSpeed") is not None:
        out["EffectiveMovementSpeedMod"] = 1.0 + _num(sim_stats["moveSpeed"]) / 100.0
    if sim_stats.get("chaosInoculation"):
        out["ChaosInoculation"] = 1.0
    return out


def extract_intent(parsed: dict[str, Any], sim_stats: dict[str, Any] | None = None) -> dict[str, Any]:
    stats = _merge_stats(parsed, sim_stats)
    ws = parsed.get("weaponSets") or {}
    ws_roles: dict[str, Any] = {}
    if ws.get("set1"):
        ws_roles["set1"] = classify_weapon_set(ws["set1"])
    if ws.get("set2"):
        ws_roles["set2"] = classify_weapon_set(ws["set2"])

    _, main_skill_name = _pick_main_skill(parsed, sim_stats)
    exceptions: list[dict[str, str]] = []

    for g in parsed.get("skillGroups") or []:
        for gem in g.get("gems") or []:
            lv = int(_num(gem.get("level"), 1))
            q = int(_num(gem.get("quality"), 0))
            if lv <= 5 and q >= 20:
                gname = gem.get("name", "")
                exceptions.append({
                    "ruleId": "skill.lowLevelHighQuality",
                    "targetId": f"gem:{gname}",
                    "reason": f"{gname} 压到 {lv} 级但 {q}% 品质 —— 高品低级的刻意触发/工具配法",
                    "en": {
                        "reason": f"{gname} kept at Lv.{lv} with {q}% quality — intentional low-level trigger/utility setup",
                    },
                })

    for key, role in ws_roles.items():
        if role["role"] in ("speed", "utility"):
            en_role = role.get("en") or {}
            exceptions.append({
                "ruleId": "weapon.lowDamage",
                "targetId": f"weaponSet:{key}",
                "reason": f"{key} 是{role['label']}：{role['detail']}",
                "en": {
                    "reason": f"{key} is {en_role.get('label', role['label'])}: {en_role.get('detail', role['detail'])}",
                },
            })

    vectors: list[dict[str, Any]] = []
    if stats.get("Speed", 0) > 0:
        vectors.append({
            "key": "speed",
            "label": f"频率 {stats['Speed']:.2f}/s",
            "note": "直接放大输出与刷图手感",
            "en": {
                "label": f"Rate {stats['Speed']:.2f}/s",
                "note": "Directly scales DPS & clear smoothness",
            },
        })
    if stats.get("CritChance", 0) >= 35:
        raw_mult = stats.get("CritMultiplier", 0)
        mult = raw_mult / 100.0 if raw_mult > 20 else raw_mult
        pre_crit = stats.get("PreEffectiveCritChance", 0)
        if stats["CritChance"] >= 99.5 and 0 < pre_crit < 95:
            crit_label = f"必然暴击 {stats['CritChance']:.0f}% × {mult:.1f}x（重骰前 {pre_crit:.0f}%）"
            crit_label_en = f"Lucky Crit {stats['CritChance']:.0f}% × {mult:.1f}x ({pre_crit:.0f}% pre-roll)"
        else:
            crit_label = f"暴击 {stats['CritChance']:.0f}% × {mult:.1f}x"
            crit_label_en = f"Crit {stats['CritChance']:.0f}% × {mult:.1f}x"
        vectors.append({
            "key": "crit",
            "label": crit_label,
            "note": "核心爆发乘区",
            "en": {
                "label": crit_label_en,
                "note": "Primary burst damage multiplier",
            },
        })
    has_dual_gear = bool(ws.get("set1")) and bool(ws.get("set2"))
    has_dual_tree = bool(ws.get("hasDualWeaponTree")) or (
        bool(((parsed.get("passiveTree") or {}).get("weaponSet1Notables")))
        and bool(((parsed.get("passiveTree") or {}).get("weaponSet2Notables")))
    )
    if has_dual_gear:
        vectors.append({
            "key": "dualWeaponSet",
            "label": "双武器组分工",
            "note": "按 Set1/Set2 分别配装与天赋",
            "en": {
                "label": "Dual Weapon Set Split",
                "note": "Distinct gear & passive branches for Set 1 / Set 2",
            },
        })
    elif has_dual_tree:
        vectors.append({
            "key": "dualWeaponSet",
            "label": "单武器锁双组天赋",
            "note": "同一套武器按技能切换 WS1 / WS2 天赋分支",
            "en": {
                "label": "Single Weapon Dual Tree",
                "note": "Same weapon switching WS1 / WS2 passive branches by skill",
            },
        })
    if stats.get("Mana", 0) >= 3000:
        vectors.append({
            "key": "mana",
            "label": f"高魔力池 {int(stats['Mana'])}",
            "note": "大法师增伤 / MoM 承伤核心池",
            "en": {
                "label": f"High Mana {int(stats['Mana'])}",
                "note": "Archmage scaling / MoM EHP core pool",
            },
        })
    if stats.get("EnergyShield", 0) >= 1500:
        vectors.append({
            "key": "es",
            "label": f"护盾池 {int(stats['EnergyShield'])}",
            "note": "高 ES 承伤核心",
            "en": {
                "label": f"ES Pool {int(stats['EnergyShield'])}",
                "note": "High Energy Shield defense core",
            },
        })
    if stats.get("Evasion", 0) >= 5000:
        vectors.append({
            "key": "evasion",
            "label": f"闪避 {int(stats['Evasion'])}",
            "note": "高闪避规避体系",
            "en": {
                "label": f"Evasion {int(stats['Evasion'])}",
                "note": "High Evasion avoidance layer",
            },
        })

    char = parsed.get("character") or {}
    asc = char.get("ascendancy") or char.get("className") or "Unknown"
    if asc == "None":
        asc = char.get("className") or "Unknown"
    archetype = " / ".join(p for p in (asc, main_skill_name) if p)
    asc_zh = game_terms.translate_term(asc, "any", "zh")
    skill_zh = game_terms.translate_term(main_skill_name, "gems", "zh")
    archetype_zh = " / ".join(p for p in (asc_zh, skill_zh) if p)

    return {
        "mainSkill": main_skill_name,
        "archetype": archetype_zh or archetype,
        "hasDualWeaponSet": bool(ws.get("hasDualWeaponSet")),
        "weaponSetRoles": ws_roles,
        "keyScalingVectors": vectors,
        "intentionalExceptions": exceptions,
        "hasTravelSkill": _has_travel_skill(parsed),
        "en": {
            "archetype": archetype,
        },
    }


def run_health_checks(
    parsed: dict[str, Any],
    intent: dict[str, Any],
    sim_stats: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    s = _merge_stats(parsed, sim_stats)
    pool = s.get("Life", 0) + s.get("EnergyShield", 0) + s.get("Ward", 0)
    has_ci = s.get("ChaosInoculation", 0) > 0 or any(
        (k.get("name") or "").lower() == "chaos inoculation"
        for k in ((parsed.get("passiveTree") or {}).get("keystones") or [])
    )

    raw: list[dict[str, Any]] = []

    def add(
        rule_id: str,
        category: str,
        severity: str,
        title: str,
        detail: str,
        target_id: str = "",
        *,
        title_en: str = "",
        detail_en: str = "",
    ) -> None:
        raw.append({
            "ruleId": rule_id,
            "category": category,
            "severity": severity,
            "title": title,
            "detail": detail,
            "targetId": target_id,
            "en": {
                "title": title_en or title,
                "detail": detail_en or detail,
            },
        })

    for key, label, en_label in (
        ("FireResist", "火焰", "Fire"),
        ("ColdResist", "冰霜", "Cold"),
        ("LightningResist", "闪电", "Lightning"),
    ):
        if key in s:
            v = s[key]
            if v < 60:
                add(
                    "res.element", "defense", "fail",
                    f"{label}抗性 {v:.0f}%", f"离 75% 差 {75 - v:.0f} 点，元素伤害会直接打穿。", key,
                    title_en=f"{en_label} Res {v:.0f}%",
                    detail_en=f"{75 - v:.0f}% below the 75% cap; elemental hits will shred defenses.",
                )
            elif v < 75:
                add(
                    "res.element", "defense", "warn",
                    f"{label}抗性 {v:.0f}%", f"差 {75 - v:.0f} 点满抗，高层地图承伤压力大。", key,
                    title_en=f"{en_label} Res {v:.0f}%",
                    detail_en=f"{75 - v:.0f}% short of 75% cap; risky in high-tier maps.",
                )

    if "ChaosResist" in s and not has_ci:
        v = s["ChaosResist"]
        if v < 0:
            add(
                "res.chaos", "defense", "fail",
                f"混沌抗 {v:.0f}%", "负混沌抗等于额外承伤，混沌地面/毒伤极易暴毙。", "ChaosResist",
                title_en=f"Chaos Res {v:.0f}%",
                detail_en="Negative Chaos Res amplifies poison/chaos ground damage and causes one-shots.",
            )
        elif v < 30:
            add(
                "res.chaos", "defense", "warn",
                f"混沌抗 {v:.0f}%", "混沌抗偏低，建议至少补到 30%~50%+。", "ChaosResist",
                title_en=f"Chaos Res {v:.0f}%",
                detail_en="Low Chaos Resistance; aim for at least 30%–50%+.",
            )

    if "Spirit" in s and "SpiritUnreserved" in s and s["SpiritUnreserved"] < 0:
        left = s["SpiritUnreserved"]
        ws = parsed.get("weaponSets") or {}
        has_s1_gear = bool(ws.get("set1"))
        has_s2_gear = bool(ws.get("set2"))
        has_dual_gear = has_s1_gear and has_s2_gear
        sp1_ok = has_s1_gear and sim_stats is not None and _num(sim_stats.get("spiritUnreservedSet1"), -1.0) >= 0
        sp2_ok = has_s2_gear and sim_stats is not None and _num(sim_stats.get("spiritUnreservedSet2"), -1.0) >= 0

        # 统计同时勾选的精魂技能数量（兼容 sim_stats.spiritAudit 与纯 XML 解析，确保双模式一致）
        if sim_stats and isinstance(sim_stats.get("spiritAudit"), list) and sim_stats["spiritAudit"]:
            spirit_skill_cnt = len(sim_stats["spiritAudit"])
        else:
            seen_sp: set[str] = set()
            for g in parsed.get("skillGroups") or []:
                for gem in g.get("gems") or []:
                    gname = (gem.get("name") or "").strip()
                    if gname and _SPIRIT_SKILL_RE.match(gname):
                        seen_sp.add(gname.lower())
            spirit_skill_cnt = len(seen_sp)

        if has_dual_gear and (sp1_ok or sp2_ok):
            add(
                "spirit.overflow",
                "resource",
                "warn",
                f"部分武器组精魂超额 {abs(left):.0f}",
                "其中一套武器组精魂正常，另一套在 PoB 中可能重复勾选了切武器光环/精魂技，实战请核对武器组绑定。",
                "Spirit",
                title_en=f"Partial Weapon-Set Spirit Overcap ({abs(left):.0f})",
                detail_en="One weapon set fits within Spirit while the other has overlapping aura toggles in PoB.",
            )
        elif s["Spirit"] >= 250 and abs(left) <= 100 and spirit_skill_cnt >= 5:
            add(
                "spirit.overflow",
                "resource",
                "warn",
                f"PoB 精魂多开超额 {abs(left):.0f}",
                f"当前同时勾选了 {spirit_skill_cnt} 个精魂技（占用 {s['Spirit'] - left:.0f} / 上限 {s['Spirit']:.0f}），多为刷图与攻坚场景光环同时勾起，实战请按场景或武器组切换开关。",
                "Spirit",
                title_en=f"PoB Spirit Multi-Toggle ({abs(left):.0f} over)",
                detail_en=f"{spirit_skill_cnt} Spirit skills enabled at once ({s['Spirit'] - left:.0f} / {s['Spirit']:.0f}); likely mapping + bossing toggles.",
            )
        else:
            add(
                "spirit.overflow",
                "resource",
                "fail",
                f"精魂超额 {abs(left):.0f}",
                f"占用 {s['Spirit'] - left:.0f} / 上限 {s['Spirit']:.0f} —— 有光环/召唤物实际无法开启。",
                "Spirit",
                title_en=f"Spirit Overcap by {abs(left):.0f}",
                detail_en=f"Reserved {s['Spirit'] - left:.0f} / Cap {s['Spirit']:.0f} — some auras or minions cannot be activated.",
            )

    mana_per_sec = s.get("ManaPerSecondCost", 0.0)
    mana_regen = s.get("ManaRegenRecovery", 0.0)
    if mana_per_sec > 0 and mana_regen > 0 and mana_per_sec > mana_regen * 2.0:
        has_remnants = any(
            "mana remnants" in (gem.get("name") or "").lower()
            for g in (parsed.get("skillGroups") or [])
            for gem in (g.get("gems") or [])
        )
        has_maelstrom = any(
            "melting maelstrom" in (it.get("name") or "").lower()
            for it in (parsed.get("flasksAndCharms") or [])
        )
        sustain_helpers = [x for x in ("Mana Remnants 残片" if has_remnants else "", "Melting Maelstrom 魔力药" if has_maelstrom else "") if x]
        sustain_helpers_en = [x for x in ("Mana Remnants" if has_remnants else "", "Melting Maelstrom" if has_maelstrom else "") if x]
        helper_txt = f"（已配 {' + '.join(sustain_helpers)} 兜底，但断档仍有风险）" if sustain_helpers else "（建议补魔耗效率、魔力残片或魔力药剂续航）"
        helper_txt_en = f" (backed by {' + '.join(sustain_helpers_en)}, but downtime risk remains)" if sustain_helpers_en else " (consider cost efficiency, Mana Remnants, or Mana flask)"
        add(
            "mana.sustain",
            "resource",
            "warn",
            f"高耗蓝 {mana_per_sec:.0f}/s vs 回蓝 {mana_regen:.0f}/s",
            f"持续施法每秒耗蓝为自然回蓝的 {mana_per_sec / mana_regen:.1f} 倍{helper_txt}。",
            "Mana",
            title_en=f"High Mana Drain {mana_per_sec:.0f}/s vs Regen {mana_regen:.0f}/s",
            detail_en=f"Sustained cast cost is {mana_per_sec / mana_regen:.1f}x natural regen{helper_txt_en}.",
        )

    if pool > 0 and "PhysicalMaximumHitTaken" in s and s["PhysicalMaximumHitTaken"] > 0:
        hit = s["PhysicalMaximumHitTaken"]
        if hit < pool * 1.5:
            add(
                "defense.physMaxHit",
                "defense",
                "warn",
                f"物理最大承伤 {round(hit)}",
                f"仅为有效血盾池（{round(pool)}）的 {hit / pool:.2f} 倍，物理重击容易穿防。",
                "PhysicalMaximumHitTaken",
                title_en=f"Phys Max Hit {round(hit)}",
                detail_en=f"Only {hit / pool:.2f}x effective pool ({round(pool)}); vulnerable to heavy physical hits.",
            )

    if pool > 0 and not has_ci and "ChaosMaximumHitTaken" in s and s["ChaosMaximumHitTaken"] > 0:
        hit = s["ChaosMaximumHitTaken"]
        if hit < pool * 2.0:
            add(
                "defense.chaosMaxHit",
                "defense",
                "warn",
                f"混沌最大承伤 {round(hit)}",
                f"仅为有效血盾池的 {hit / pool:.2f} 倍，注意混沌抗与混沌伤害。",
                "ChaosMaximumHitTaken",
                title_en=f"Chaos Max Hit {round(hit)}",
                detail_en=f"Only {hit / pool:.2f}x effective pool; watch Chaos Resistance and chaos hits.",
            )

    for have, need, label, en_label in (
        ("Str", "ReqStr", "力量", "Strength"),
        ("Dex", "ReqDex", "敏捷", "Dexterity"),
        ("Int", "ReqInt", "智力", "Intelligence"),
    ):
        if have in s and need in s and s[have] < s[need]:
            add(
                "attr.requirement", "attribute", "fail",
                f"{label}不足", f"需要 {s[need]:.0f}，当前 {s[have]:.0f}，装备或宝石会失效。", have,
                title_en=f"Insufficient {en_label}",
                detail_en=f"Requires {s[need]:.0f}, have {s[have]:.0f}; gear or gems will be disabled.",
            )

    for g in parsed.get("skillGroups") or []:
        for gem in g.get("gems") or []:
            lv = int(_num(gem.get("level"), 1))
            q = int(_num(gem.get("quality"), 0))
            if lv <= 5 and q >= 20:
                gname = gem.get("name", "")
                add(
                    "skill.lowLevelHighQuality",
                    "skill",
                    "info",
                    f"{gname} {lv} 级 / {q}% 品",
                    "低级高品组合：多为刻意压级触发/工具技能。",
                    f"gem:{gname}",
                    title_en=f"{gname} Lv.{lv} / {q}%Q",
                    detail_en="Low-level high-quality gem: likely intentional trigger/utility setup.",
                )

    for ws_key, role in (intent.get("weaponSetRoles") or {}).items():
        if role.get("role") not in ("damage", "caster"):
            en_role = role.get("en") or {}
            add(
                "weapon.lowDamage",
                "gear",
                "info",
                f"{ws_key} 非主输出武器组",
                role.get("detail") or "该武器组承担攻速/功能职责。",
                f"weaponSet:{ws_key}",
                title_en=f"{ws_key} Non-DPS Weapon Set",
                detail_en=en_role.get("detail") or "Dedicated speed / utility weapon set.",
            )

    if "EffectiveMovementSpeedMod" in s:
        mod = s["EffectiveMovementSpeedMod"]
        if 0 < mod < 1.15 and not intent.get("hasTravelSkill"):
            add(
                "movement.slow", "comfort", "warn",
                f"移速 ×{mod:.2f}", "偏慢且未检测到常用位移技能，刷图体感吃亏。", "EffectiveMovementSpeedMod",
                title_en=f"Move Speed ×{mod:.2f}",
                detail_en="Relatively slow with no travel skill detected; mapping pace may feel sluggish.",
            )

    exceptions = intent.get("intentionalExceptions") or []
    merged: list[dict[str, Any]] = []
    for item in raw:
        hit = next(
            (
                ex
                for ex in exceptions
                if ex["ruleId"] == item["ruleId"]
                and (not ex["targetId"] or not item["targetId"] or ex["targetId"] == item["targetId"] or item["targetId"].startswith(ex["targetId"]))
            ),
            None,
        )
        if hit:
            item = {
                **item,
                "suppressedByIntent": {
                    "reason": hit["reason"],
                    "en": hit.get("en") or {"reason": hit["reason"]},
                },
            }
        merged.append(item)

    merged.sort(key=lambda x: _SEVERITY_ORDER.get(x["severity"], 9))
    return merged


def extract_core_gear(
    parsed: dict[str, Any],
    item_impacts: list[dict[str, Any]] | None = None,
    ai_gear_notes: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    highlights: list[dict[str, Any]] = []
    all_items = list(parsed.get("equippedItems") or []) + list(parsed.get("jewels") or [])
    impact_by_slot = {
        str(x.get("slot") or ""): x
        for x in (item_impacts or [])
        if isinstance(x, dict) and x.get("slot")
    }
    notes_map = ai_gear_notes or {}

    for it in all_items:
        rarity = (it.get("rarity") or "").upper()
        slot = it.get("slot") or ""
        slot_l = slot.lower()
        name = it.get("name") or it.get("baseType") or ""
        mods = _item_all_mods(it)
        deep = affix_analyzer.analyze_item_deeply(it, parsed)
        deep_scored = sorted(
            (a for a in (deep.get("affixes") or []) if not a.get("excludeFromTradeLock") and a.get("role") != "dead"),
            key=lambda a: int(a.get("contextualScore") or 0),
            reverse=True,
        )
        if deep_scored:
            top_mods = [str(a["line"]) for a in deep_scored[:4]]
            top_score = int(deep_scored[0].get("contextualScore") or 10)
        else:
            scored_mods = sorted(
                ((score_mod_importance(m, slot), m) for m in mods),
                key=lambda x: x[0],
                reverse=True,
            )
            top_mods = [m for _, m in scored_mods[:4]]
            top_score = scored_mods[0][0] if scored_mods else 10

        is_unique = rarity == "UNIQUE"
        is_limb = bool(re.match(r"^(arm|leg)\s*\d+$", slot_l))
        chase_info = _MIRROR_OR_CHASE_UNIQUES.get(name.lower())
        role_tag = "核心暗金" if is_unique else ("肢体附魔" if is_limb else "关键黄装")
        role_tag_en = "Core Unique" if is_unique else ("Limb Augment" if is_limb else "Key Rare")
        if chase_info:
            role_tag = chase_info[0]
            role_tag_en = chase_info[2]
        elif "weapon" in slot_l:
            role_tag = "核心武器"
            role_tag_en = "Core Weapon"
        elif "jewel" in slot_l:
            role_tag = "暗金珠宝" if is_unique else "词缀珠宝"
            role_tag_en = "Unique Jewel" if is_unique else "Rare Jewel"
        elif any(k in slot_l for k in ("body", "helmet", "boots")):
            role_tag = "核心防具"
            role_tag_en = "Core Armour"
        elif "amulet" in slot_l:
            role_tag = "项链核心位"
            role_tag_en = "Core Amulet"

        static_importance = (
            (15 if is_limb and rarity == "NORMAL" else top_score)
            + (35 if is_unique else 0)
            + (25 if "weapon" in slot_l else 0)
            + (30 if chase_info else 0)
        )

        imp = impact_by_slot.get(slot)
        if imp is not None:
            ablation_score = float(imp.get("impactScore") or 0.0)
            floor_bonus = 45.0 if (is_unique and (imp.get("inactiveWeaponSet") or imp.get("hasUnparsedMods"))) else 0.0
            importance = round(max(ablation_score * 1.35 + (20.0 if is_unique else 0.0), floor_bonus), 1)
        else:
            importance = static_importance

        entry: dict[str, Any] = {
            "slot": slot,
            "name": name,
            "baseType": it.get("baseType") or "",
            "rarity": rarity,
            "roleTag": role_tag,
            "keyMods": top_mods,
            "importance": importance,
            "socketCount": int(it.get("socketCount") or 0),
            "corrupted": bool(it.get("corrupted")),
            "en": {
                "roleTag": role_tag_en,
            },
        }
        if imp is not None:
            entry["impactDpsPct"] = imp.get("dpsLossPct", 0.0)
            entry["impactEhpPct"] = imp.get("ehpLossPct", 0.0)
            entry["impactManaPct"] = imp.get("manaLossPct", 0.0)
            if imp.get("note"):
                entry["impactNote"] = imp["note"]
        if slot in notes_map:
            entry["aiNote"] = notes_map[slot]
        highlights.append(entry)

    highlights.sort(key=lambda x: x["importance"], reverse=True)
    return highlights


def extract_core_affixes(parsed: dict[str, Any]) -> tuple[list[str], list[str]]:
    weighted: Counter[str] = Counter()
    en_map: dict[str, str] = {}
    all_items = list(parsed.get("equippedItems") or []) + list(parsed.get("jewels") or [])

    for it in all_items:
        slot = it.get("slot") or ""
        is_rare = (it.get("rarity") or "").upper() != "UNIQUE"
        slot_mult = 1.4 if "weapon" in slot.lower() or "amulet" in slot.lower() else 1.0
        rarity_mult = 1.2 if is_rare else 0.7
        props = it.get("properties") or {}
        if props.get("Ward"):
            try:
                if float(str(props["Ward"]).split()[0]) >= 50:
                    weighted["符文结界 (Ward)"] += 95
                    en_map["符文结界 (Ward)"] = "Runic Ward"
            except Exception:
                pass
        for mod in _item_all_mods(it):
            for label, pattern, base_weight, en_label in _AFFIX_ARCHETYPES:
                if pattern.search(mod):
                    weighted[label] += int(base_weight * slot_mult * rarity_mult)
                    en_map[label] = en_label
                    break

    top = [label for label, _ in weighted.most_common(7)]
    return top, [en_map.get(lbl, lbl) for lbl in top]


def _estimate_budget_tier(
    parsed: dict[str, Any],
    price_snapshot: dict[str, Any] | None = None,
) -> tuple[str, list[str], list[str]]:
    reasons: list[str] = []
    reasons_en: list[str] = []

    # 若扩展已回传真实价格快照，优先按总造价定档
    if price_snapshot and isinstance(price_snapshot.get("prices"), dict) and price_snapshot["prices"]:
        total_d = sum(float(v) for v in price_snapshot["prices"].values() if isinstance(v, (int, float)) and v > 0)
        if total_d >= 200:
            return "mirror", [f"已核实市集总造价约 {total_d:.0f} D（镜级/毕业档）"], [f"Verified market cost ~{total_d:.0f} D (Mirror / Endgame Tier)"]
        if total_d >= 50:
            return "high", [f"已核实市集总造价约 {total_d:.0f} D（中高造价）"], [f"Verified market cost ~{total_d:.0f} D (High Budget)"]
        if total_d >= 12:
            return "mid", [f"已核实市集总造价约 {total_d:.0f} D（中预算成型）"], [f"Verified market cost ~{total_d:.0f} D (Mid Budget)"]
        if total_d > 0:
            return "league-start", [f"已核实市集总造价约 {total_d:.1f} D（低造价友好）"], [f"Verified market cost ~{total_d:.1f} D (League-Start Friendly)"]

    score = 0.0
    chase_hits: list[str] = []
    all_items = list(parsed.get("equippedItems") or []) + list(parsed.get("jewels") or []) + list(parsed.get("flasksAndCharms") or [])
    unique_count = 0
    corrupted_count = 0
    high_mod_rares = 0

    for it in all_items:
        rarity = (it.get("rarity") or "").upper()
        name = it.get("name") or ""
        if rarity == "UNIQUE":
            unique_count += 1
            hit = _MIRROR_OR_CHASE_UNIQUES.get(name.lower())
            if hit:
                score += hit[1]
                chase_hits.append(name)
            else:
                score += 1.5
        elif len(it.get("mods") or []) >= 6:
            high_mod_rares += 1
            score += 2.5
        if it.get("corrupted"):
            corrupted_count += 1
            score += 1.5

    corrupted_gems = 0
    for g in parsed.get("skillGroups") or []:
        for gem in g.get("gems") or []:
            if int(_num(gem.get("level"), 1)) >= 21 or int(_num(gem.get("quality"), 0)) >= 21:
                corrupted_gems += 1
                score += 4.0

    if chase_hits:
        reasons.append(f"含高价核心暗金：{'、'.join(chase_hits[:3])}")
        reasons_en.append(f"High-value chase uniques: {', '.join(chase_hits[:3])}")
    if corrupted_gems >= 2:
        reasons.append(f"含 {corrupted_gems} 颗 21 级 / 超 20% 品质腐化宝石（前期可降配 20/20）")
        reasons_en.append(f"Contains {corrupted_gems} corrupted Lv.21 / >20%Q gems (20/20 is fine early)")
    if high_mod_rares >= 5:
        reasons.append(f"{high_mod_rares} 件满 6 词缀黄装，后期微调空间大")
        reasons_en.append(f"{high_mod_rares} full 6-mod rares with high late-game optimization ceiling")

    if score >= 75:
        return "mirror", reasons, reasons_en
    if score >= 28:
        return "high", reasons, reasons_en
    if score >= 12:
        return "mid", reasons, reasons_en
    return "league-start", reasons, reasons_en


def _defense_label(stats: dict[str, float], keystones: list[str]) -> tuple[str, str]:
    ks_lower = {k.lower() for k in keystones}
    life = stats.get("Life", 0)
    life_unres = stats.get("LifeUnreserved", 0)
    eff_life = life_unres if 0 < life_unres < life else life
    is_low_life = (life > 0 and 0 < life_unres <= life * 0.38) or ("pain attunement" in ks_lower and eff_life <= 800)
    es = stats.get("EnergyShield", 0)
    mana = stats.get("Mana", 0)
    ev = stats.get("Evasion", 0)
    deflect = stats.get("DeflectChance", 0)
    ward = stats.get("Ward", 0)
    arm = stats.get("Armour", 0)
    blk = stats.get("EffectiveBlockChance", 0)

    has_ci = "chaos inoculation" in ks_lower
    has_eb = "eldritch battery" in ks_lower
    has_mom = "mind over matter" in ks_lower

    parts: list[str] = []
    parts_en: list[str] = []
    if has_ci and has_eb and has_mom:
        parts.append("CI + EB + MoM 纯蓝池")
        parts_en.append("CI + EB + MoM Pure Mana Pool")
    elif has_ci and has_eb:
        parts.append("CI + EB 魔力转化")
        parts_en.append("CI + EB Mana Conversion")
    elif has_ci or (es >= 2000 and life <= 500):
        parts.append("CI 纯护盾")
        parts_en.append("CI Pure ES")
    elif is_low_life and es >= 2000:
        parts.append("低血 Pain Attunement + 护盾" if "pain attunement" in ks_lower else "低血纯护盾")
        parts_en.append("Low-Life Pain Attunement + ES" if "pain attunement" in ks_lower else "Low-Life Pure ES")
    elif es >= 1200 and eff_life >= 1200:
        parts.append("血盾混合")
        parts_en.append("Hybrid Life/ES")
    elif es >= 1500:
        parts.append("能量护盾")
        parts_en.append("Energy Shield")
    elif eff_life > 0:
        parts.append("生命池")
        parts_en.append("Life Pool")

    if ev >= 5000:
        if deflect >= 30:
            parts.append(f"高闪避+偏转{deflect:.0f}%")
            parts_en.append(f"High Evasion + Deflect {deflect:.0f}%")
        else:
            parts.append("高闪避")
            parts_en.append("High Evasion")
    elif deflect >= 30:
        parts.append(f"偏转 {deflect:.0f}%")
        parts_en.append(f"Deflect {deflect:.0f}%")
    if ward >= 80:
        parts.append(f"结界 {int(ward)}")
        parts_en.append(f"Ward {int(ward)}")
    if arm >= 5000:
        parts.append("高护甲")
        parts_en.append("High Armour")
    if blk >= 30:
        parts.append(f"格挡 {blk:.0f}%")
        parts_en.append(f"Block {blk:.0f}%")
    if has_mom and not (has_ci and has_eb):
        parts.append(f"MoM 蓝池承伤({int(mana)})" if mana >= 2000 else "MoM 蓝池承伤")
        parts_en.append(f"MoM Mana({int(mana)})" if mana >= 2000 else "MoM Mana")

    return (
        " + ".join(parts) if parts else "常规混合防御",
        " + ".join(parts_en) if parts_en else "Balanced Hybrid Defense",
    )


def _score_to_grade(score: float) -> str:
    if score >= 88:
        return "S"
    if score >= 74:
        return "A"
    if score >= 58:
        return "B"
    if score >= 42:
        return "C"
    return "D"


def _grade_verdict(grade: str) -> tuple[str, str]:
    return {
        "S": ("碾压速通", "Crushing / Speedrun"),
        "A": ("稳健推荐", "Solid & Reliable"),
        "B": ("可打需操作", "Viable w/ Mechanics"),
        "C": ("吃力易暴毙", "Struggles / Rippy"),
        "D": ("红线卡住/不推荐", "Blocked / Not Recommended"),
    }.get(grade, ("可打", "Viable"))


def evaluate_content_readiness(
    parsed: dict[str, Any],
    intent: dict[str, Any],
    health_checks: list[dict[str, Any]],
    sim_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """评估这套 BD 能打哪些 PoE2 终局内容并给出 S/A/B/C/D 分级与客观依据。"""
    stats = _merge_stats(parsed, sim_stats)
    base_dps = max(
        stats.get("FullDPS", 0.0),
        stats.get("CombinedDPS", 0.0),
        stats.get("TotalDPS", 0.0),
    )
    totem_limit = int(stats.get("ActiveTotemLimit", 0.0))
    dps = base_dps * totem_limit if totem_limit > 1 else base_dps
    ehp = stats.get("TotalEHP", 0.0)
    life = stats.get("Life", 0.0)
    life_unres = stats.get("LifeUnreserved", 0.0)
    eff_life = life_unres if 0 < life_unres < life else life
    es = stats.get("EnergyShield", 0.0)
    mana = stats.get("Mana", 0.0)
    ks_lower = {(k.get("name") or "").lower() for k in ((parsed.get("passiveTree") or {}).get("keystones") or [])}
    has_mom = "mind over matter" in ks_lower
    has_ci = stats.get("ChaosInoculation", 0.0) > 0 or "chaos inoculation" in ks_lower
    # 点了心灵升华 (MoM) 时，魔力池计入真实有效血池，EHP 也按血+蓝保底估算
    pool = eff_life + es + (mana if has_mom else 0.0)
    eff_ehp = max(ehp, pool * 3.2) if has_mom and mana >= 2000 else ehp

    phys_hit = stats.get("PhysicalMaximumHitTaken", 0.0)
    chaos_res = stats.get("ChaosResist", 0.0)
    fire_res = stats.get("FireResist", 75.0)
    cold_res = stats.get("ColdResist", 75.0)
    light_res = stats.get("LightningResist", 75.0)
    min_ele_res = min(fire_res, cold_res, light_res)
    ms_mod = stats.get("EffectiveMovementSpeedMod", 1.15)
    has_travel = bool(intent.get("hasTravelSkill"))
    eff_chaos = 100.0 if has_ci else chaos_res

    char_level = int((parsed.get("character") or {}).get("level") or 0)
    has_real_stats = dps > 0 or ehp > 0 or pool > 0
    confidence = "simulated" if sim_stats else ("xml-stats" if has_real_stats else "heuristic")

    # 若 XML 中未带预计算面板且未跑引擎实算，按等级与装备完整度给出保守启发式估计
    if not has_real_stats:
        gear_cnt = len(parsed.get("equippedItems") or [])
        dps = 180000.0 if char_level >= 92 and gear_cnt >= 8 else (85000.0 if char_level >= 80 else 35000.0)
        eff_ehp = ehp = 38000.0 if char_level >= 92 else (22000.0 if char_level >= 80 else 12000.0)
        pool = 3200.0 if char_level >= 90 else 2000.0
        phys_hit = pool * 2.5

    active_fails = [c for c in health_checks if not c.get("suppressedByIntent") and c["severity"] == "fail"]
    has_redline_fail = len(active_fails) > 0
    redline_penalty = 24.0 if has_redline_fail else (10.0 if min_ele_res < 75 else 0.0)
    # 超过 1.5M DPS 属于高爆发秒杀档，可部分补偿玻璃大炮防御评分（但仍保留脆皮提示）
    melt_bonus = 14.0 if dps >= 1_500_000 else (7.0 if dps >= 800_000 else 0.0)
    pool_label = f"血+蓝 {pool:.0f}" if has_mom and mana >= 1500 else f"血盾 {pool:.0f}"
    pool_label_en = f"Life+Mana {pool:.0f}" if has_mom and mana >= 1500 else f"Life+ES {pool:.0f}"

    # 1. T15 常规速刷
    s_t15 = 0.0
    s_t15 += 46.0 if dps >= 1_000_000 else (42.0 if dps >= 250_000 else (34.0 if dps >= 120_000 else (24.0 if dps >= 60_000 else 12.0)))
    s_t15 += 34.0 if eff_ehp >= 35_000 else (28.0 if eff_ehp >= 20_000 else (18.0 if eff_ehp >= 12_000 else 8.0))
    s_t15 += 24.0 if (has_travel or ms_mod >= 1.30) else (18.0 if ms_mod >= 1.18 else 10.0)
    s_t15 = max(10.0, s_t15 + melt_bonus * 0.6 - redline_penalty)
    g_t15 = _score_to_grade(s_t15)
    r_t15 = f"DPS {dps/1000:.0f}k / {pool_label} (EHP {eff_ehp/1000:.1f}k)，{'高机动位移流畅' if (has_travel or ms_mod >= 1.28) else f'移速×{ms_mod:.2f}'}"
    r_t15_en = f"DPS {dps/1000:.0f}k / {pool_label_en} (EHP {eff_ehp/1000:.1f}k), {'high mobility' if (has_travel or ms_mod >= 1.28) else f'MS ×{ms_mod:.2f}'}"
    if min_ele_res < 75:
        r_t15 += f"，元素抗最低 {min_ele_res:.0f}% 需补满"
        r_t15_en += f", lowest ele res {min_ele_res:.0f}% (cap 75%)"

    # 2. T15+ 高危词缀 / 迷雾 / 裂隙
    s_juice = 0.0
    s_juice += 46.0 if dps >= 1_200_000 else (40.0 if dps >= 450_000 else (32.0 if dps >= 250_000 else (22.0 if dps >= 120_000 else 10.0)))
    s_juice += 30.0 if eff_ehp >= 55_000 else (24.0 if eff_ehp >= 35_000 else (18.0 if eff_ehp >= 20_000 else 8.0))
    s_juice += 16.0 if eff_chaos >= 50 else (11.0 if eff_chaos >= 20 else (5.0 if eff_chaos >= 0 else 0.0))
    s_juice += 10.0 if (phys_hit >= 14_000 or (phys_hit == 0 and eff_ehp >= 45_000)) else (6.0 if phys_hit >= 6_500 else 2.0)
    s_juice = max(10.0, s_juice + melt_bonus * 0.5 - redline_penalty)
    g_juice = _score_to_grade(s_juice)
    r_juice = f"清图 DPS {dps/1000:.0f}k，{'CI 免疫混沌' if has_ci else f'混抗 {eff_chaos:.0f}%'}"
    r_juice_en = f"Clear DPS {dps/1000:.0f}k, {'CI Chaos Immune' if has_ci else f'Chaos Res {eff_chaos:.0f}%'}"
    if phys_hit > 0:
        r_juice += f"，物理一击线 {phys_hit/1000:.1f}k"
        r_juice_en += f", Phys Max Hit {phys_hit/1000:.1f}k"

    # 3. 终局首领 T0~T2（基础裂隙/迷雾/探险/灰烬仲裁者）
    s_boss2 = 0.0
    s_boss2 += 52.0 if dps >= 1_200_000 else (46.0 if dps >= 500_000 else (38.0 if dps >= 250_000 else (26.0 if dps >= 120_000 else 12.0)))
    s_boss2 += 34.0 if eff_ehp >= 45_000 else (26.0 if eff_ehp >= 22_000 else (18.0 if eff_ehp >= 14_000 else 8.0))
    s_boss2 += 16.0 if min_ele_res >= 75 and eff_chaos >= 20 else (10.0 if min_ele_res >= 75 else 0.0)
    s_boss2 = max(10.0, s_boss2 + melt_bonus * 0.6 - redline_penalty)
    g_boss2 = _score_to_grade(s_boss2)
    r_boss2 = f"单体 {dps/1000:.0f}k DPS，{pool_label} / EHP {eff_ehp/1000:.1f}k"
    r_boss2_en = f"Single-target {dps/1000:.0f}k DPS, {pool_label_en} / EHP {eff_ehp/1000:.1f}k"

    # 4. 终局满难 Boss T4（T4 仲裁者 / T4 裂隙王 / T4 迷雾王）
    s_boss4 = 0.0
    s_boss4 += 56.0 if dps >= 1_800_000 else (50.0 if dps >= 900_000 else (40.0 if dps >= 500_000 else (28.0 if dps >= 280_000 else (16.0 if dps >= 150_000 else 6.0))))
    s_boss4 += 30.0 if eff_ehp >= 70_000 else (24.0 if eff_ehp >= 40_000 else (18.0 if eff_ehp >= 20_000 else 8.0))
    s_boss4 += 16.0 if (eff_chaos >= 45 and min_ele_res >= 75) else (10.0 if min_ele_res >= 75 else 0.0)
    s_boss4 = max(10.0, s_boss4 + melt_bonus * 0.4 - redline_penalty * 1.1)
    g_boss4 = _score_to_grade(s_boss4)
    r_boss4 = f"当前 {dps/1000:.0f}k DPS / {pool_label} (EHP {eff_ehp/1000:.1f}k)"
    r_boss4_en = f"{dps/1000:.0f}k DPS / {pool_label_en} (EHP {eff_ehp/1000:.1f}k)"
    if dps >= 1_500_000 and phys_hit > 0 and phys_hit < 10_000:
        r_boss4 += "（极高爆发秒杀型，但物理一击线偏脆需走位躲大招）"
        r_boss4_en += " (Glass-cannon burst; low Phys Max Hit requires dodging slams)"
    elif g_boss4 in ("C", "D") and dps < 400_000:
        r_boss4 += "（建议先提核心输出与承伤上限再挑战满难 T4）"
        r_boss4_en += " (Upgrade DPS and max hit before attempting T4 Ubers)"

    # 5. 圣所试炼 Sekhemas / 混沌试炼
    ev_chance = stats.get("EvadeChance", 0.0)
    blk_chance = stats.get("EffectiveBlockChance", 0.0)
    s_trial = 0.0
    s_trial += 40.0 if dps >= 1_000_000 else (34.0 if dps >= 220_000 else (26.0 if dps >= 110_000 else 15.0))
    s_trial += 28.0 if (has_travel or ms_mod >= 1.28) else (18.0 if ms_mod >= 1.15 else 10.0)
    s_trial += 22.0 if (pool >= 3500 or ev_chance >= 60 or blk_chance >= 40) else (14.0 if pool >= 2200 else 8.0)
    s_trial += 14.0 if eff_chaos >= 35 else (9.0 if eff_chaos >= 0 else 0.0)
    s_trial = max(10.0, s_trial - redline_penalty * 0.85)
    g_trial = _score_to_grade(s_trial)
    r_trial = f"试炼表现：{pool_label}、{'带位移' if has_travel else f'移速×{ms_mod:.2f}'}、{'CI' if has_ci else f'混抗{eff_chaos:.0f}%'}"
    r_trial_en = f"Trials: {pool_label_en}, {'travel skill' if has_travel else f'MS ×{ms_mod:.2f}'}, {'CI' if has_ci else f'Chaos {eff_chaos:.0f}%'}"

    def mk_item(cid: str, label: str, label_en: str, grade: str, reason: str, reason_en: str) -> dict[str, Any]:
        v_zh, v_en = _grade_verdict(grade)
        return {
            "id": cid,
            "label": label,
            "grade": grade,
            "verdict": v_zh,
            "reason": reason,
            "en": {
                "label": label_en,
                "verdict": v_en,
                "reason": reason_en,
            },
        }

    items = [
        mk_item("t15_mapping", "T15 常规速刷", "T15 Mapping", g_t15, r_t15, r_t15_en),
        mk_item("juiced_t15_delirium", "T15+ 高危/迷雾/裂隙", "Juiced T15+ / Delirium", g_juice, r_juice, r_juice_en),
        mk_item("pinnacle_t0_t2", "终局首领 (T0~T2)", "Pinnacle Bosses (T0–T2)", g_boss2, r_boss2, r_boss2_en),
        mk_item("pinnacle_t4_uber", "终局满难 Boss (T4)", "Uber / T4 Pinnacle", g_boss4, r_boss4, r_boss4_en),
        mk_item("trials_sekhema_chaos", "圣所/混沌试炼", "Sekhemas / Chaos Trials", g_trial, r_trial, r_trial_en),
    ]

    if g_boss4 in ("S", "A") and g_juice in ("S", "A"):
        overall_grade = "S" if g_boss4 == "S" else "A"
        overall_tier = "终局 T4 全通毕业档"
        overall_tier_en = "T4 All-Content Endgame Ready"
    elif g_juice in ("S", "A") and g_boss2 in ("S", "A"):
        overall_grade = "A"
        overall_tier = "T15+ 高危速刷 & T2 首领稳通档"
        overall_tier_en = "Juiced T15+ & T2 Boss Ready"
    elif g_t15 in ("S", "A"):
        overall_grade = "B"
        overall_tier = "常规 T15 稳刷 / 中期攻坚档"
        overall_tier_en = "Consistent T15 Mapping / Mid Progression"
    elif g_t15 == "B":
        overall_grade = "C"
        overall_tier = "异界开荒 / 成型过渡档"
        overall_tier_en = "Early Atlas / Transition Tier"
    else:
        overall_grade = "D"
        overall_tier = "红线未达标 / 需先修补面板"
        overall_tier_en = "Redlines Failing / Needs Fixes"

    best_labels = [x["label"] for x in items if x["grade"] in ("S", "A")]
    weak_labels = [x["label"] for x in items if x["grade"] in ("C", "D")]
    best_labels_en = [x["en"]["label"] for x in items if x["grade"] in ("S", "A")]
    weak_labels_en = [x["en"]["label"] for x in items if x["grade"] in ("C", "D")]
    summary_parts = []
    summary_parts_en = []
    if best_labels:
        summary_parts.append(f"主力胜任：{'、'.join(best_labels[:3])}")
        summary_parts_en.append(f"Strong in: {', '.join(best_labels_en[:3])}")
    if weak_labels:
        summary_parts.append(f"当前短板：{'、'.join(weak_labels[:2])}")
        summary_parts_en.append(f"Weak in: {', '.join(weak_labels_en[:2])}")
    if has_redline_fail:
        summary_parts.append("存在严重面板红线（抗性/精魂/属性），修补后评级会明显上浮")
        summary_parts_en.append("Critical stat redlines detected (Res/Spirit/Attr); fixing them boosts ratings")
    if confidence == "heuristic":
        summary_parts.append("（未含预计算面板，勾「同步 PoB2 引擎实算」可得精确分级）")
        summary_parts_en.append("(Heuristic estimate; enable PoB2 live simulation for exact grades)")

    return {
        "overallTier": overall_tier,
        "overallGrade": overall_grade,
        "confidence": confidence,
        "summary": "；".join(summary_parts) if summary_parts else "各终局场景表现均衡。",
        "items": items,
        "en": {
            "overallTier": overall_tier_en,
            "summary": "; ".join(summary_parts_en) if summary_parts_en else "Well-balanced across endgame content.",
        },
    }


def build_positioning(
    parsed: dict[str, Any],
    intent: dict[str, Any],
    health_checks: list[dict[str, Any]],
    core_gear: list[dict[str, Any]],
    sim_stats: dict[str, Any] | None = None,
    price_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stats = _merge_stats(parsed, sim_stats)
    tree = parsed.get("passiveTree") or {}
    keystones = [n["name"] for n in (tree.get("keystones") or []) if n.get("name")]
    asc_nodes = [n["name"] for n in (tree.get("ascendancyNodes") or []) if n.get("name")]

    core_uniques: list[str] = []
    seen_u: set[str] = set()
    for it in list(parsed.get("equippedItems") or []) + list(parsed.get("jewels") or []) + list(parsed.get("flasksAndCharms") or []):
        if (it.get("rarity") or "").upper() == "UNIQUE":
            name = (it.get("name") or "").strip()
            if name and name.lower() not in seen_u:
                seen_u.add(name.lower())
                core_uniques.append(name)

    core_affixes, core_affixes_en = extract_core_affixes(parsed)
    budget_tier, budget_reasons, budget_reasons_en = _estimate_budget_tier(parsed, price_snapshot)
    def_label, def_label_en = _defense_label(stats, keystones)

    # 场景标签
    tags: list[str] = []
    tags_en: list[str] = []
    char = parsed.get("character") or {}
    asc = char.get("ascendancy")
    if asc and asc != "None":
        tags.append(game_terms.translate_term(asc, "ascendancies", "zh"))
        tags_en.append(asc)
    elif char.get("className"):
        tags.append(game_terms.translate_term(char["className"], "classes", "zh"))
        tags_en.append(char["className"])

    tags.append(def_label)
    tags_en.append(def_label_en)
    ws = parsed.get("weaponSets") or {}
    has_dual_gear = bool(ws.get("set1")) and bool(ws.get("set2"))
    has_dual_tree = bool(ws.get("hasDualWeaponTree")) or (
        bool(tree.get("weaponSet1Notables")) and bool(tree.get("weaponSet2Notables"))
    )
    if has_dual_gear:
        tags.append("双武器组分工")
        tags_en.append("Dual Weapon Sets")
    elif has_dual_tree:
        tags.append("单武器锁双组天赋")
        tags_en.append("Dual Passive Trees")
    if stats.get("CritChance", 0) >= 40:
        tags.append("暴击体系")
        tags_en.append("Crit Scaling")
    if intent.get("hasTravelSkill") or stats.get("EffectiveMovementSpeedMod", 1.0) >= 1.3:
        tags.append("高机动速刷")
        tags_en.append("Fast Clear")
    if len(core_uniques) >= 4:
        tags.append("多暗金联动")
        tags_en.append("Unique-Heavy")
    elif len(core_uniques) <= 1:
        tags.append("黄装自由度高")
        tags_en.append("Rare-Flexible")
    if stats.get("CombinedDPS", 0) >= 250000 or stats.get("FullDPS", 0) >= 250000:
        tags.append("T15+ 攻坚/终局")
        tags_en.append("T15+ Endgame")

    # 按构筑核心度排序基石（优先 CI/EB/MoM 等核心基石，避免先知跳板基石占前两位）
    sorted_keystones = sorted(
        keystones,
        key=lambda k: _PRIMARY_KEYSTONE_PRIORITY.get(k.lower(), 0),
        reverse=True,
    )
    has_entwined = any(a.lower() == "entwined realities" for a in asc_nodes)

    # 一句话核心思路
    main_skill = intent.get("mainSkill") or "核心技能"
    main_skill_en = intent.get("mainSkill") or "Main Skill"
    ws_roles = intent.get("weaponSetRoles") or {}
    dual_clause = ""
    dual_clause_en = ""
    if has_dual_gear and ws_roles.get("set1") and ws_roles.get("set2"):
        s1_en = (ws_roles["set1"].get("en") or {}).get("label") or ws_roles["set1"]["label"]
        s2_en = (ws_roles["set2"].get("en") or {}).get("label") or ws_roles["set2"]["label"]
        dual_clause = f"，双武器组分工（Set1 {ws_roles['set1']['label']} / Set2 {ws_roles['set2']['label']}）"
        dual_clause_en = f", dual weapon sets (Set 1: {s1_en} / Set 2: {s2_en})"
    elif has_dual_tree:
        dual_clause = "，单武器锁双组天赋分支"
        dual_clause_en = ", single-weapon dual passive tree split"
    unique_clause = f"，绑定核心暗金「{' / '.join(core_uniques[:2])}」" if core_uniques else ""
    unique_clause_en = f", anchored by uniques ({' / '.join(core_uniques[:2])})" if core_uniques else ""
    ks_top = sorted_keystones[:3] if len(sorted_keystones) >= 3 and _PRIMARY_KEYSTONE_PRIORITY.get(sorted_keystones[2].lower(), 0) >= 80 else sorted_keystones[:2]
    ks_clause = f"，核心基石「{' / '.join(ks_top)}」" if ks_top else ""
    ks_clause_en = f", core keystones ({' / '.join(ks_top)})" if ks_top else ""
    if has_entwined and len(keystones) >= 5:
        ks_clause += f"（另含 {len(keystones) - len(ks_top)} 个先知跳点基石）"
        ks_clause_en += f" (+{len(keystones) - len(ks_top)} Oracle jump keystones)"

    one_line = f"以「{main_skill}」为主轴{dual_clause}{unique_clause}{ks_clause}；防御走「{def_label}」路线。"
    one_line_en = f"Built around {main_skill_en}{dual_clause_en}{unique_clause_en}{ks_clause_en}; defense uses {def_label_en}."

    # 避坑提示（合并：预算溢价提示 + Keystone 注意事项 + 未被豁免的红线问题）
    pitfalls: list[str] = []
    pitfalls_en: list[str] = []
    for r in budget_reasons:
        pitfalls.append(r)
    for r in budget_reasons_en:
        pitfalls_en.append(r)

    ks_lower = {k.lower() for k in keystones}
    if {"chaos inoculation", "eldritch battery", "mind over matter"}.issubset(ks_lower):
        pitfalls.append(
            "点了「CI + EB + MoM」纯蓝池体系：血锁 1 点且护盾全转魔力（ES=0），承伤与施法共用魔力池，必须盯紧最大魔力、残片/药剂回蓝与晕眩/异常免疫。"
        )
        pitfalls_en.append(
            "CI + EB + MoM pure Mana pool: Life is 1 and ES converts to Mana (ES=0); sustain Max Mana, Mana Remnants/Flasks, and stun/ailment mitigation."
        )
        skip_ks = {"chaos inoculation", "eldritch battery", "mind over matter"}
    else:
        skip_ks = set()

    for ks in sorted_keystones:
        if ks.lower() in skip_ks:
            continue
        caveat = _KEYSTONE_CAVEATS.get(ks.lower())
        if caveat:
            pitfalls.append(caveat[0])
            pitfalls_en.append(caveat[1])

    for issue in health_checks:
        if issue.get("suppressedByIntent"):
            continue
        if issue["severity"] in ("fail", "warn"):
            pitfalls.append(f"【{issue['title']}】{issue['detail']}")
            en_iss = issue.get("en") or {}
            pitfalls_en.append(f"[{en_iss.get('title', issue['title'])}] {en_iss.get('detail', issue['detail'])}")

    if not pitfalls:
        pitfalls.append("面板抗性、精魂与属性红线均正常，抄作业时优先保住武器核心词缀与主技能连法。")
        pitfalls_en.append("Resistances, Spirit, and attribute checks look clean; prioritize weapon affixes and main skill links.")

    # 评级不再使用静态正则/阈值规则，统一留空等待 AI 结合 PoB2 引擎实算数据（位移速度、输出、防御等）进行评级
    return {
        "archetype": intent.get("archetype") or main_skill,
        "oneLineIdea": one_line,
        "scenarioTags": list(dict.fromkeys(tags))[:6],
        "budgetTier": budget_tier,
        "coreUniques": core_uniques[:16],
        "coreAffixes": core_affixes[:7],
        "coreKeystones": sorted_keystones,
        "coreAscendancy": asc_nodes,
        "pitfalls": list(dict.fromkeys(pitfalls))[:6],
        "contentReadiness": None,
        "en": {
            "archetype": (intent.get("en") or {}).get("archetype") or intent.get("archetype") or main_skill_en,
            "oneLineIdea": one_line_en,
            "scenarioTags": list(dict.fromkeys(tags_en))[:6],
            "coreAffixes": core_affixes_en[:7],
            "pitfalls": list(dict.fromkeys(pitfalls_en))[:6],
        },
    }


def _format_sim_summary(sim: dict[str, Any]) -> dict[str, Any]:
    ms = _num(sim.get("moveSpeed"), 0.0)
    skill_ms = _num(sim.get("skillMoveSpeed"), ms)
    action_spd = _num(sim.get("actionSpeed"), 0.0)
    metrics = engine_facts.extract_combat_metrics(sim)
    mobility = metrics.get("mobility") or {}
    return {
        "metricsVersion": int(_num(sim.get("metricsVersion"), 2)) or 2,
        "dps": round(_num(sim.get("dps")), 1),
        "dpsSource": str(sim.get("dpsSource") or "CombinedDPS"),
        "mainSkillName": str(sim.get("mainSkillName") or ""),
        "mainSkillLevel": int(_num(sim.get("mainSkillLevel"), 0)),
        "skillArchetype": str(sim.get("skillArchetype") or ""),
        "activeTotemLimit": int(_num(sim.get("activeTotemLimit"), 0)),
        "skillPartName": str(sim.get("skillPartName") or ""),
        "skillStageCount": int(_num(sim.get("skillStageCount"), 0)),
        "skillStageMax": int(_num(sim.get("skillStageMax"), 0)),
        "duration": round(_num(sim.get("duration")), 2),
        "durationMod": round(_num(sim.get("durationMod")), 2),
        "projSpeedMod": round(_num(sim.get("projSpeedMod")), 2),
        "projectileCount": int(_num(sim.get("projectileCount"), 0)),
        "aoeRadius": round(_num(sim.get("aoeRadius")), 2),
        "aoeMod": round(_num(sim.get("aoeMod")), 2),
        "sealMax": int(_num(sim.get("sealMax"), 0)),
        "cooldown": round(_num(sim.get("cooldown")), 2),
        "triggerRate": round(_num(sim.get("triggerRate")), 2),
        "enemyIsBoss": str(sim.get("enemyIsBoss") or ""),
        "ehp": round(_num(sim.get("ehp")), 1),
        "life": round(_num(sim.get("life")), 1),
        "lifeUnreserved": round(_num(sim.get("lifeUnreserved")), 1),
        "lifeUnreservedPct": round(_num(sim.get("lifeUnreservedPct")), 1),
        "es": round(_num(sim.get("es")), 1),
        "ward": round(_num(sim.get("ward")), 1),
        "deflectChance": round(_num(sim.get("deflectChance")), 1),
        "deflectionRating": round(_num(sim.get("deflectionRating")), 1),
        "mana": round(_num(sim.get("mana")), 1),
        "manaCost": round(_num(sim.get("manaCost")), 1),
        "manaPerSecCost": round(_num(sim.get("manaPerSecCost")), 1),
        "manaRegen": round(_num(sim.get("manaRegen")), 1),
        "spirit": round(_num(sim.get("spirit")), 1),
        "spiritUnreserved": round(_num(sim.get("spiritUnreserved")), 1),
        "fireRes": round(_num(sim.get("fireRes")), 1),
        "coldRes": round(_num(sim.get("coldRes")), 1),
        "lightningRes": round(_num(sim.get("lightningRes")), 1),
        "chaosRes": round(_num(sim.get("chaosRes")), 1),
        "critChance": round(_num(sim.get("critChance")), 2),
        "preEffectiveCritChance": round(_num(sim.get("preEffectiveCritChance")), 2),
        "critMult": round(_num(sim.get("critMult")), 1),
        "speed": round(_num(sim.get("speed")), 2),
        "moveSpeedMod": round(1.0 + ms / 100.0, 2),
        "moveSpeed": round(ms, 1),
        "skillMoveSpeed": round(skill_ms, 1),
        "actionSpeed": round(action_spd, 1),
        "mobilitySummary": str(mobility.get("primaryTravelSummary") or ""),
        "travelSkills": list(mobility.get("travelSkills") or []),
        "physDR": round(_num(sim.get("physDR")), 1),
        "evadeChance": round(_num(sim.get("evadeChance")), 1),
        "blockChance": round(_num(sim.get("blockChance")), 1),
        "lifeRegen": round(_num(sim.get("lifeRegen")), 1),
        "lifeLeech": round(_num(sim.get("lifeLeech")), 1),
        "lifeRecovery": round(_num(sim.get("lifeRecovery")), 1),
        "esRecharge": round(_num(sim.get("esRecharge")), 1),
        "esRecovery": round(_num(sim.get("esRecovery")), 1),
        "physMaxHit": round(_num(sim.get("physMaxHit")), 1),
        "fireMaxHit": round(_num(sim.get("fireMaxHit")), 1),
        "coldMaxHit": round(_num(sim.get("coldMaxHit")), 1),
        "lightningMaxHit": round(_num(sim.get("lightningMaxHit")), 1),
        "eleMaxHit": round(_num(sim.get("eleMaxHit")), 1),
        "chaosMaxHit": round(_num(sim.get("chaosMaxHit")), 1),
        "chaosMaxHitInfinite": bool(int(_num(sim.get("chaosMaxHitInfinite"), 0)) > 0 or int(_num(sim.get("chaosInoculation"), 0)) > 0),
        "reqStr": round(_num(sim.get("reqStr")), 0),
        "reqDex": round(_num(sim.get("reqDex")), 0),
        "reqInt": round(_num(sim.get("reqInt")), 0),
        "str": round(_num(sim.get("str")), 0),
        "dex": round(_num(sim.get("dex")), 0),
        "int": round(_num(sim.get("int")), 0),
    }


def extract_profile(
    code_or_xml: str,
    *,
    pool: Any = None,
    run_simulation: bool = False,
    run_ablation: bool = True,
    cached_base_sim: dict[str, Any] | None = None,
    price_snapshot: dict[str, Any] | None = None,
    use_ai: bool | None = None,
    state_dir: str | None = None,
    llm_cfg: dict[str, Any] | None = None,
    llm_http_post: Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """完整解析目标 BD：PoB2 引擎实算 + 并发单件消融事实包 + 可选 AI 结构化理解融合。"""
    xml = pob_parser.decode_pob(code_or_xml)
    parsed = pob_parser.parse_pob_xml(xml)

    raw_sim: dict[str, Any] | None = None
    sim_summary: dict[str, Any] | None = None
    facts: dict[str, Any] | None = None
    if run_simulation and (pool is not None or (not run_ablation and isinstance(cached_base_sim, dict))):
        norm_xml, _ = runefix.normalize_runes(xml)
        raw_sim, facts = engine_facts.collect_build_facts(
            norm_xml,
            parsed,
            pool,
            run_ablation=run_ablation,
            precomputed_sim=cached_base_sim if not run_ablation else None,
        )
        sim_summary = _format_sim_summary(raw_sim)

    intent = extract_intent(parsed, raw_sim)
    health_checks = run_health_checks(parsed, intent, raw_sim)
    item_impacts = (facts or {}).get("itemImpacts") or None
    core_gear = extract_core_gear(parsed, item_impacts=item_impacts)
    positioning = build_positioning(
        parsed,
        intent,
        health_checks,
        core_gear,
        sim_stats=raw_sim,
        price_snapshot=price_snapshot,
    )
    positioning["analyzedBy"] = "engine-facts" if raw_sim is not None else "fallback"

    result: dict[str, Any] = {
        "source": "backend",
        "build": parsed,
        "positioning": positioning,
        "intent": intent,
        "coreGearHighlights": core_gear,
        "healthChecks": health_checks,
        "simulation": sim_summary,
        "engineFacts": facts,
    }

    # 若启用了大模型（或显式要求 use_ai=True），由 AI 基于完整 PoB2 事实包接管结构化定位
    should_run_ai = False
    active_llm_cfg = llm_cfg
    if use_ai is True:
        should_run_ai = True
    elif use_ai is None and (llm_cfg is not None or state_dir is not None):
        active_llm_cfg = llm_cfg or llm_advisor.load_llm_config(state_dir)
        should_run_ai = bool(active_llm_cfg.get("enabled"))

    if should_run_ai:
        try:
            ai_res = llm_advisor.analyze_target_structured(
                result,
                cfg=active_llm_cfg,
                state_dir=state_dir or llm_advisor.STATE_DIR,
                http_post=llm_http_post,
            )
            result["aiAnalysis"] = ai_res
            structured = ai_res.get("structured")
            if isinstance(structured, dict):
                result["positioning"] = llm_advisor.merge_ai_positioning(
                    positioning,
                    structured,
                    model=str(ai_res.get("model") or ""),
                    engine_metrics_summary=llm_advisor._format_engine_metrics_summary(result),
                )
                gear_notes = structured.get("coreGearNotes")
                if isinstance(gear_notes, dict) and gear_notes:
                    result["coreGearHighlights"] = extract_core_gear(
                        parsed,
                        item_impacts=item_impacts,
                        ai_gear_notes=gear_notes,
                    )
        except Exception as exc:  # noqa: BLE001
            result["aiError"] = str(exc)

    return result
