"""深度装备词条分析器（联动天赋树 / 升华 / 技能 / 双武器组 / 符文插槽与基底属性）。

与前端 extension/src/parsers/affixAnalyzer.ts 保持同等 PoE2 领域语义，服务于：
  1. build_profile.py：上下文感知的词缀重要度排序与核心词缀聚类；
  2. gap_analyzer.py：analyze_slot_affixes 词缀剥离报告（含符文插槽分析、天赋联动标签、平替/半成品推荐）；
  3. llm_advisor.py：向大模型事实包注入精准的孔位/符文/天赋联动提示，防止平替与半成品建议踩坑。
"""
from __future__ import annotations

import re
from typing import Any

_TWO_HAND_BASE_RE = re.compile(
    r"\b(bow|crossbow|quarterstaff|warstaff|staff|greataxe|greatsword|greatmace|two hand|talisman)\b",
    re.I,
)
_ONE_HAND_BASE_RE = re.compile(
    r"\b(spear|wand|sceptre|dagger|claw|flail|sword|axe|mace|club|rapier)\b",
    re.I,
)
_SOCKET_EFFECT_RE = re.compile(
    r"increased\s+effect\s+of\s+socketed\s+(augment\s+items|runes|soul\s+cores|items)",
    re.I,
)
_RUNE_POOL_RE = re.compile(r"^can\s+roll\s+(.+?)\s+modifiers?$", re.I)
_GRANTS_SKILL_RE = re.compile(
    r"^grants\s+skill:\s*(?:level\s+(\d+)\s+)?(.+)$",
    re.I,
)
# 装备大类默认自带的基础攻击/举盾技（所有同大类武器/盾牌都有，除非构筑将其当主技能或连了辅助宝石，否则换同大类底子无功能损失）
_GENERIC_BASE_ATTACK_SKILLS = {
    "spear throw",
    "raise shield",
    "parry",
    "firebolt",
    "freezing shards",
    "lightning bolt",
    "chaos bolt",
    "bone blast",
    "solar orb",
    "living bomb",
    "ice nova",
    "arc",
    "galvanic field",
}
# PoE2 牺牲前后缀或专属基底赋予核心精魂/光环/机制技能的特殊基底
_SPECIAL_SKILL_BASE_RE = re.compile(
    r"absent amulet|lament amulet|portent amulet|"
    r"shrine sceptre|stoic sceptre|omen sceptre|rattling sceptre|lupine sceptre|ochre sceptre|"
    r"chiming staff|dueling wand|siphoning wand|attuned wand",
    re.I,
)
# PoE2 高功能价值基底自带技能（即便 PoB2 面板未直接折算，少了也有明显机制/光环/功能损失）
_FUNCTIONAL_GRANTED_SKILL_HINTS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"eternal rage", re.I), "常驻怒气/增伤精魂技（免精魂或省技能槽）", "缺少常驻怒气循环"),
    (re.compile(r"purity of (fire|ice|lightning|elements)", re.I), "零精魂抗性光环（还能白嫖挂载辅助光环）", "丢失免费光环与挂载辅助槽"),
    (re.compile(r"\bdiscipline\b", re.I), "免费能量护盾光环（还能挂载辅助光环）", "丢失免费护盾光环"),
    (re.compile(r"\bmalice\b", re.I), "常驻暴击脆弱/减抗光环", "丢失免费暴击光环"),
    (re.compile(r"sigil of power", re.I), "法术爆发增伤法阵", "丢失爆发增伤法阵"),
    (re.compile(r"\bblink\b", re.I), "核心瞬移位移精魂技", "丢失核心瞬移机动性"),
    (re.compile(r"cast on |invocation|spellslinger", re.I), "自动触发核心精魂技", "丢失自动触发循环"),
    (re.compile(r"archmage|trinity|elemental conflux|herald of |ghost dance|wind dancer|grim feast|combat frenzy|charge regulation|siphon elements|overwhelming presence|mana remnants|berserk|convalescence|blasphemy|time of need|withering presence|arctic armour|scavenged plating|attrition|sacrifice|mirage archer|rhoa mount|wolf pack|savage fury|barkskin", re.I), "核心精魂常驻/机制技能", "丢失基底赋予的核心精魂技"),
    (re.compile(r"skeletal |raging spirits|raise zombie|spectre", re.I), "免费召唤物技能槽", "丢失基底自带召唤物"),
    (re.compile(r"power siphon|mana drain|volatile dead|unearth|soul offering|curses?|elemental weakness", re.I), "功能/充能/续航基底技能", "丢失基底功能技能"),
)


def extract_granted_skill(mod_text: str) -> tuple[str, int | None] | None:
    cleaned = re.sub(r"\{[^}]*\}", "", str(mod_text or "")).strip()
    cleaned = re.sub(r"\s*\((?:implicit|crafted|fractured|desecrated|mutated)\)\s*$", "", cleaned, flags=re.I).strip()
    m = _GRANTS_SKILL_RE.match(cleaned)
    if not m:
        return None
    lv = int(m.group(1)) if m.group(1) else None
    sname = m.group(2).strip()
    return (sname, lv) if sname else None


def _norm_skill_key(name: str) -> str:
    return re.sub(r"\s+minion$", "", re.sub(r"\s+", " ", (name or "").strip().lower()))


def evaluate_granted_skill(
    mod_text: str,
    *,
    base_type: str = "",
    slot: str = "",
    parsed_build: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """评估装备基底自带技能在当前 BD 中的真实地位（是否启用、是否连了辅助、是否免精魂特殊基底、少了是否有功能损失）。"""
    parsed = extract_granted_skill(mod_text)
    if not parsed:
        return None
    skill_name, level = parsed
    skey = _norm_skill_key(skill_name)
    base_l = (base_type or "").lower()
    is_special_base = bool(_SPECIAL_SKILL_BASE_RE.search(base_l))
    is_spirit_amulet = any(k in base_l for k in ("absent amulet", "lament amulet", "portent amulet"))
    is_generic_attack = skey in _GENERIC_BASE_ATTACK_SKILLS and not is_special_base

    linked_supports: list[str] = []
    is_used_in_build = False
    is_main_skill = False
    for sg in (parsed_build or {}).get("skillGroups") or []:
        gems = sg.get("gems") or []
        if not gems:
            continue
        first_key = _norm_skill_key(gems[0].get("name") or "")
        if first_key == skey:
            is_used_in_build = True
            if sg.get("isMain"):
                is_main_skill = True
            for g in gems[1:]:
                gn = str(g.get("name") or "").strip()
                if gn and gn not in linked_supports:
                    linked_supports.append(gn)

    func_desc = ""
    func_loss = ""
    for pat, desc, loss in _FUNCTIONAL_GRANTED_SKILL_HINTS:
        if pat.search(skill_name):
            func_desc = desc
            func_loss = loss
            break

    is_important = bool(
        is_main_skill
        or len(linked_supports) > 0
        or is_special_base
        or bool(func_desc)
        or (is_used_in_build and not is_generic_attack)
    )
    if not func_desc:
        if is_spirit_amulet:
            func_desc = "项链基底赋予的内置精魂技（省精魂与技能槽）"
            func_loss = f"丢失「{skill_name}」技能槽与精魂收益"
        elif is_important:
            func_desc = "当前构筑实际启用的基底自带技能"
            func_loss = f"丢失基底自带技能「{skill_name}」"
        else:
            func_desc = "同类别武器/盾牌默认基础技能（当前未作主力且未连辅助）"
            func_loss = ""

    return {
        "skillName": skill_name,
        "level": level,
        "isSpecialBase": is_special_base,
        "isSpiritAmulet": is_spirit_amulet,
        "isGenericAttack": is_generic_attack,
        "isUsedInBuild": is_used_in_build,
        "isMainSkill": is_main_skill,
        "linkedSupports": linked_supports,
        "functionalDesc": func_desc,
        "functionalLoss": func_loss,
        "isImportant": is_important,
    }


def _first_num(val: Any) -> float:
    if val is None:
        return 0.0
    m = re.search(r"(\d+(?:\.\d+)?)", str(val))
    return float(m.group(1)) if m else 0.0


def get_natural_max_sockets(slot: str, base_type: str = "") -> int:
    """PoE2 工匠石（Artificer's Orb）可打出的自然孔位上限。"""
    s = (slot or "").lower()
    b = (base_type or "").lower()
    if any(k in s for k in ("ring", "amulet", "belt", "quiver", "jewel", "flask", "charm")):
        return 0
    if "body" in s or "chest" in s:
        return 2
    if any(k in s for k in ("helmet", "gloves", "boots", "shield", "focus")):
        return 1
    if _TWO_HAND_BASE_RE.search(b) and not _ONE_HAND_BASE_RE.search(re.sub(r"quarterstaff|warstaff", "", b, flags=re.I)):
        return 2
    if "weapon" in s:
        return 2 if _TWO_HAND_BASE_RE.search(b) else 1
    return 0


def _slot_key(slot: str) -> str | None:
    s = (slot or "").lower()
    for key, pats in (
        ("helmet", ("helmet",)),
        ("body", ("body", "chest")),
        ("gloves", ("gloves",)),
        ("boots", ("boots",)),
        ("shield", ("shield", "buckler")),
        ("focus", ("focus",)),
        ("ring", ("ring",)),
        ("amulet", ("amulet",)),
        ("quiver", ("quiver",)),
        ("weapon", ("weapon",)),
    ):
        if any(p in s for p in pats):
            return key
    return None


def _is_armour_slot(slot: str) -> bool:
    return _slot_key(slot) in ("helmet", "body", "gloves", "boots", "shield", "focus")


def _is_speed_utility_weapon(it: dict[str, Any]) -> bool:
    s = (it.get("slot") or "").lower()
    if "weapon" not in s:
        return False
    text = " ".join(list(it.get("mods") or []) + list(it.get("socketedRunes") or []))
    total_speed = sum(int(m) for m in re.findall(r"(\d+)%\s+increased\s+Attack\s+Speed", text, re.I))
    has_speed_mag = bool(re.search(r"explicit\s+speed\s+modifier\s+magnitudes", text, re.I))
    phys_pct = max([int(m) for m in re.findall(r"(\d+)%\s+increased\s+Physical\s+Damage", text, re.I)] or [0])
    added_avg = sum(
        (float(lo) + float(hi)) / 2.0
        for lo, hi in re.findall(r"Adds\s+(\d+)\s+to\s+(\d+)\s+[A-Za-z ]*Damage", text, re.I)
    )
    has_spell = bool(
        re.search(
            r"increased\s+Spell\s+Damage|to\s+Level\s+of\s+all\s+(Spell|Lightning|Fire|Cold|Chaos|Minion)\s+Skills|increased\s+Cast\s+Speed",
            text,
            re.I,
        )
    )
    return (total_speed >= 20 or has_speed_mag) and phys_pct == 0 and added_avg < 50 and not has_spell


def extract_build_synergy_profile(parsed: dict[str, Any] | None) -> dict[str, Any]:
    """从完整解析的 PoB2 数据提取天赋/升华/技能/双武器组/暗金联动画像。"""
    prof: dict[str, Any] = {
        "lifeDead": False,
        "chaosResDead": False,
        "manaDead": False,
        "critDead": False,
        "nonFireDead": False,
        "gearSpiritDead": False,
        "esCore": False,
        "esToMana": False,
        "manaCore": False,
        "evasionToArmour": False,
        "bondedRunesActive": False,
        "projSpeedIsDamage": False,
        "accuracyIsDamage": False,
        "deflectionScaling": False,
        "elementalAilmentScaling": False,
        "needsWardBase": False,
        "hasTravelAttack": False,
        "strScaling": False,
        "dexScaling": False,
        "intScaling": False,
        "giantBlood": False,
        "hollowPalm": False,
        "isSpellBuild": False,
        "isAttackBuild": False,
        "isProjectileBuild": False,
        "isMinionBuild": False,
        "isCritBuild": False,
        "speedWeaponSlots": set(),
        "slotSynergies": [],
        "activeNodeNames": set(),
    }
    if not parsed:
        return prof

    tree = parsed.get("passiveTree") or {}
    all_nodes: list[dict[str, Any]] = (
        list(tree.get("keystones") or [])
        + list(tree.get("ascendancyNodes") or [])
        + list(tree.get("notables") or [])
        + list(tree.get("anointedNotables") or [])
    )

    seen_syn: set[str] = set()

    def add_syn(slot_key: str, stat_kind: str, node_name: str, label: str, boost: float, penalize: bool = False) -> None:
        k = f"{slot_key}:{stat_kind}:{node_name}"
        if k in seen_syn:
            return
        seen_syn.add(k)
        prof["slotSynergies"].append({
            "slotKey": slot_key,
            "statKind": stat_kind,
            "nodeName": node_name,
            "label": label,
            "boost": boost,
            "penalize": penalize,
        })

    for node in all_nodes:
        name = (node.get("name") or "").strip()
        nl = name.lower()
        st = " | ".join(str(x) for x in (node.get("stats") or [])).lower()
        if nl:
            prof["activeNodeNames"].add(nl)

        if nl == "chaos inoculation" or "maximum life becomes 1" in st:
            prof["lifeDead"] = True
            prof["chaosResDead"] = True
            prof["esCore"] = True
        if nl == "blood magic" or "you have no mana" in st:
            prof["manaDead"] = True
        if nl == "resolute technique" or "never deal critical" in st:
            prof["critDead"] = True
        if nl == "avatar of fire" or "deal no non-fire damage" in st:
            prof["nonFireDead"] = True
        if nl == "eldritch battery" or "converts all energy shield to mana" in st:
            prof["esToMana"] = True
            prof["manaCore"] = True
        if nl == "mind over matter" or "taken from mana before life" in st:
            prof["manaCore"] = True
        if nl == "iron reflexes" or "converts all evasion rating to armour" in st:
            prof["evasionToArmour"] = True
        if nl == "giant's blood" or "attribute requirements of" in st:
            prof["giantBlood"] = True
            prof["strScaling"] = True
        if nl == "hollow palm technique" or "per 75 item evasion on equipped armour items" in st:
            prof["hollowPalm"] = True
            add_syn("armour", "ev", name or "Hollow Palm", "空手道(防具闪避→攻速)", 36)
            add_syn("armour", "es", name or "Hollow Palm", "空手道(防具ES→基础暴击)", 36)

        if "cannot gain spirit from equipment" in st or "you have no spirit" in st or nl == "embrace the darkness":
            prof["gearSpiritDead"] = True
        if nl == "lead me through grace..." or ("spirit for every" in st and "equipped body armour" in st):
            prof["gearSpiritDead"] = True
            add_syn("body", "es", name or "Lead me through Grace...", "Invoker(胸甲ES→精魂)", 44)
            add_syn("body", "ev", name or "Lead me through Grace...", "Invoker(胸甲闪避→精魂)", 40)
        if nl == "wisdom of the maji" or "bonded modifiers on runes" in st:
            prof["bondedRunesActive"] = True

        if nl == "feathered fletching" or "projectile speed also apply to damage" in st:
            prof["projSpeedIsDamage"] = True
        if (
            nl in ("falcon dive", "penetrate")
            or (nl == "critical strike" and "accuracy" in st)
            or "attack speed per 400 accuracy" in st
            or "excess chance to hit" in st
            or "weapon's accuracy rating" in st
        ):
            prof["accuracyIsDamage"] = True
        if "deflection rating equal to" in st or nl == "enhanced reflexes":
            prof["deflectionScaling"] = True
        if nl == "harness the elements" or "for each type of elemental ailment" in st:
            prof["elementalAilmentScaling"] = True

        if nl == "falcon technique" or "per 25 dexterity" in st:
            prof["dexScaling"] = True
        if nl in ("the winter owl", "pure power") or "per 10 intelligence" in st:
            prof["intScaling"] = True
        if nl in ("powerful casting", "brute strength") or "per 10 strength" in st or "per 15 strength" in st:
            prof["strScaling"] = True
        if nl in ("arcane intensity", "blackened heart") or "per 100 maximum mana" in st:
            prof["manaCore"] = True

        if nl == "subterfuge mask" or ("evasion rating per" in st and "energy shield on equipped helmet" in st):
            add_syn("helmet", "es", name or "Subterfuge Mask", "头盔ES→闪避(Subterfuge Mask)", 45)
        if nl == "illuminated crown" or "increased energy shield from equipped helmet" in st:
            add_syn("helmet", "es", name or "Illuminated Crown", "头盔ES放大(Illuminated Crown)", 34)
        if nl == "ruinic helm" or ("energy shield per" in st and "armour on equipped helmet" in st):
            add_syn("helmet", "ar", name or "Ruinic Helm", "头盔护甲→ES(Ruinic Helm)", 42)
        if nl == "spectral ward" or ("energy shield per" in st and "evasion on equipped body armour" in st):
            add_syn("body", "ev", name or "Spectral Ward", "胸甲闪避→ES(Spectral Ward)", 44)
        if nl == "beastial skin" or "increased evasion rating from equipped body armour" in st:
            add_syn("body", "ev", name or "Beastial Skin", "胸甲闪避翻倍(Beastial Skin)", 40)
        if nl == "crimson power" or ("maximum life equal to" in st and "energy shield on equipped body armour" in st):
            add_syn("body", "es", name or "Crimson Power", "胸甲ES→生命(Crimson Power)", 44)
        if nl in ("stone skin", "sturdy metal") or "armour from equipped body armour" in st:
            add_syn("body", "ar", name or "Stone Skin", f"胸甲护甲放大({name})", 36)
        if nl == "ancient aegis":
            add_syn("body", "es", name, "胸甲护盾放大(Ancient Aegis)", 32)
            add_syn("body", "ar", name, "胸甲护甲放大(Ancient Aegis)", 32)
        if nl == "leather bound gauntlets" or ("evasion rating per" in st and "armour on equipped gloves" in st):
            add_syn("gloves", "ar", name or "Leather Bound Gauntlets", "手套护甲→闪避", 38)
        if nl == "iron slippers" or ("armour per" in st and "energy shield on equipped boots" in st):
            add_syn("boots", "es", name or "Iron Slippers", "鞋子ES→护甲(Iron Slippers)", 38)
        if nl == "stalking panther" or "evasion rating from equipped helmet, gloves and boots is doubled" in st:
            add_syn("helmet", "ev", name or "Stalking Panther", "头/手/鞋闪避翻倍(Stalking Panther)", 42)
            add_syn("gloves", "ev", name or "Stalking Panther", "头/手/鞋闪避翻倍(Stalking Panther)", 42)
            add_syn("boots", "ev", name or "Stalking Panther", "头/手/鞋闪避翻倍(Stalking Panther)", 42)
            add_syn("body", "ev", name or "Stalking Panther", "胸甲闪避减半(Stalking Panther)", -25, penalize=True)
        if nl in ("dreamcatcher", "warding fetish") or "energy shield from equipped focus" in st:
            add_syn("focus", "es", name or "Dreamcatcher", f"法器ES放大({name})", 36)
        if nl in ("greatest defence", "spiked shield") or ("attack damage per" in st and "equipped shield" in st):
            add_syn("shield", "all_def", name, f"盾牌防值→攻击增伤({name})", 42)
        if nl in ("fortified aegis", "towering shield"):
            add_syn("shield", "all_def", name, f"盾牌防值放大({name})", 30)
        if nl == "mystic attunement" or "bonuses gained from equipped rings and amulets" in st:
            add_syn("ring", "all_mods", name or "Mystic Attunement", "戒指词条+25%(Mystic Attunement)", 12)
            add_syn("amulet", "all_mods", name or "Mystic Attunement", "项链词条+25%(Mystic Attunement)", 12)
        if nl in ("wrapped quiver", "master fletching") or "bonuses gained from equipped quiver" in st:
            add_syn("quiver", "all_mods", name, f"箭袋词条放大({name})", 16)

    gem_blob = " | ".join(
        (g.get("name") or "").strip().lower()
        for sg in (parsed.get("skillGroups") or [])
        for g in (sg.get("gems") or [])
        if g.get("name")
    )
    if "projectile acceleration" in gem_blob:
        prof["projSpeedIsDamage"] = True
    if "olroth's conviction" in gem_blob:
        prof["needsWardBase"] = True
    if "archmage" in gem_blob:
        prof["manaCore"] = True
    if re.search(r"whirling slash|shield charge|leap slam|tempest flurry", gem_blob):
        prof["hasTravelAttack"] = True
    if re.search(r"skeletal |raging spirits|raise zombie|spectre|minion", gem_blob):
        prof["isMinionBuild"] = True

    for it in list(parsed.get("equippedItems") or []) + list(parsed.get("jewels") or []):
        nm = (it.get("name") or "").lower()
        if "hand of wisdom and action" in nm:
            prof["intScaling"] = True
            prof["dexScaling"] = True
        if "pillar of the caged god" in nm or "astramentis" in nm:
            prof["strScaling"] = True
            prof["dexScaling"] = True
            prof["intScaling"] = True
        if "yoke of suffering" in nm or "the taming" in nm:
            prof["elementalAilmentScaling"] = True
        if _first_num((it.get("properties") or {}).get("Ward")) >= 50:
            prof["needsWardBase"] = True
        if _is_speed_utility_weapon(it):
            prof["speedWeaponSlots"].add((it.get("slot") or "").lower())

    stats = (parsed.get("character") or {}).get("stats") or {}
    if not prof["critDead"] and (
        _first_num(stats.get("CritChance")) >= 28
        or any(re.search(r"critical (hit|damage)", " ".join(n.get("stats") or []), re.I) for n in all_nodes)
    ):
        prof["isCritBuild"] = True
    if _first_num(stats.get("EnergyShield")) >= 1200:
        prof["esCore"] = True
    if _first_num(stats.get("Mana")) >= 2800:
        prof["manaCore"] = True

    gear_text = " ".join(
        f"{it.get('baseType') or ''} {' '.join(it.get('mods') or [])}"
        for it in (parsed.get("equippedItems") or [])
    ).lower()
    spell_sig = bool(
        re.search(r"increased spell damage|to level of all spell skills|cast speed", gear_text)
        or re.search(r"spark|arc|fireball|comet|frostbolt|hexblast|essence drain|contagion|bonestorm|eye of winter", gem_blob)
    )
    attack_sig = bool(
        re.search(r"\b(spear|bow|crossbow|quarterstaff|mace|axe|sword|claw|dagger|flail)\b", gear_text)
        or re.search(r"twister|lightning arrow|lightning spear|whirling slash|ice strike|tempest flurry|explosive shot|galvanic", gem_blob)
    )
    if spell_sig and not attack_sig:
        prof["isSpellBuild"] = True
    elif attack_sig and not spell_sig:
        prof["isAttackBuild"] = True
    elif spell_sig and attack_sig:
        prof["isSpellBuild"] = True
        prof["isAttackBuild"] = True
    if re.search(r"projectile|arrow|spear|twister|bow|crossbow|quiver|spark|frostbolt", f"{gem_blob} {gear_text}"):
        prof["isProjectileBuild"] = True

    return prof


def _resolve_mod_origins(it: dict[str, Any]) -> list[dict[str, Any]]:
    details = it.get("modDetails")
    if isinstance(details, list) and details:
        return [
            {
                "text": str(d.get("text") or ""),
                "origin": str(d.get("origin") or "explicit"),
                "isAnoint": bool(d.get("isAnoint") or re.match(r"^allocates\s+", str(d.get("text") or ""), re.I)),
            }
            for d in details
            if d.get("origin") != "rune" and d.get("text")
        ]
    out: list[dict[str, Any]] = []
    for raw in it.get("mods") or []:
        if re.search(r"\(rune\)|\{rune\}", raw, re.I):
            continue
        low = raw.lower()
        cleaned = re.sub(r"\s*\((implicit|crafted|fractured|rune|desecrated|mutated)\)\s*$", "", re.sub(r"\{[^}]*\}", "", raw), flags=re.I).strip()
        is_anoint = bool(re.match(r"^allocates\s+", cleaned, re.I))
        if "(fractured)" in low or "{fractured}" in low:
            origin = "fractured"
        elif "(desecrated)" in low or "{desecrated}" in low:
            origin = "desecrated"
        elif "(crafted)" in low or "{crafted}" in low:
            origin = "crafted"
        elif "(implicit)" in low or "{implicit}" in low or re.match(r"^grants\s+(skill:|1 additional skill slot)", cleaned, re.I):
            origin = "implicit"
        elif is_anoint or "{enchant}" in low:
            origin = "enchant"
        else:
            origin = "explicit"
        out.append({"text": cleaned, "origin": origin, "isAnoint": is_anoint})
    return out


def _mod_skeleton(text: str) -> str:
    s = re.sub(r"[+-]?\d+(?:\.\d+)?", "#", text or "")
    s = re.sub(r"\+#", "#", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _is_local_defense_stat_of(mod_text: str, stat: str) -> bool:
    t = (mod_text or "").lower()
    if re.search(r"recharge|regeneration|delay|deflect", t):
        return False
    if stat == "es":
        return bool(re.search(r"maximum energy shield|increased (?:armour and |evasion and )?energy shield\b", t))
    if stat == "ev":
        return bool(re.search(r"to evasion rating|increased (?:armour and )?evasion", t))
    if stat == "ar":
        return bool(re.search(r"to armour|increased armour", t))
    if stat == "ward":
        return bool(re.search(r"\bward\b", t))
    return False


def infer_affix_side(mod_text: str, origin: str) -> str:
    if origin == "implicit":
        return "implicit"
    if origin == "enchant":
        return "enchant"
    t = (mod_text or "").lower()
    if re.search(
        r"resistance|strength|dexterity|intelligence|all attributes|attack speed|cast speed|skill speed|use speed|"
        r"critical (hit |strike )?chance|critical .*damage bonus|accuracy rating|mana regeneration|life regeneration|"
        r"energy shield recharge|stun threshold|light radius|deflection rating|effect of socketed|explicit speed modifier|"
        r"reduced attribute|projectile speed|mana on kill|life on kill|leech|to level of all .*skills",
        t,
    ):
        return "suffix"
    if re.search(
        r"maximum life|maximum mana|maximum energy shield|evasion rating|to armour|increased armour|"
        r"increased energy shield(?!\s+recharge)|increased evasion|adds \d+ to \d+|increased physical damage|"
        r"increased spell damage|increased elemental damage|increased projectile damage|"
        r"increased movement speed|to spirit\b|increased spirit|gain .* as extra|additional arrow|additional projectile|"
        r"increased rarity of items found",
        t,
    ):
        return "prefix"
    return "unknown"


def infer_craft_difficulty(mod_text: str, origin: str, is_anoint: bool) -> str:
    t = (mod_text or "").lower()
    if is_anoint or origin in ("implicit", "enchant") or t.startswith("grants skill:"):
        return "unrollable"
    if origin == "fractured":
        return "hard"
    if re.search(
        r"to level of all .*skills|additional (arrow|projectile)|to spirit\b|increased spirit\b|"
        r"increased movement speed|gain .* as extra|effect of socketed|explicit .* modifier magnitudes|"
        r"deflection rating equal to|increased critical (hit |strike )?chance|critical .*damage bonus|"
        r"\d{2,3}% increased physical damage|increased spell damage|increased attack speed|increased cast speed|"
        r"increased projectile speed",
        t,
    ):
        return "hard"
    if re.search(
        r"to chaos resistance|to all elemental resistances|adds \d+ to \d+ .* damage|"
        r"increased (energy shield|evasion|armour)|to maximum (energy shield|mana|life)|"
        r"mana cost efficiency|increased projectile damage|increased elemental damage",
        t,
    ):
        return "medium"
    return "easy"


def analyze_item_sockets(it: dict[str, Any], bp: dict[str, Any]) -> dict[str, Any]:
    slot = it.get("slot") or ""
    base_type = it.get("baseType") or ""
    natural_max = get_natural_max_sockets(slot, base_type)
    socket_count = int(it.get("socketCount") or 0)
    exceeds = natural_max > 0 and socket_count > natural_max
    has_socket_eff = bool(_SOCKET_EFFECT_RE.search("\n".join(it.get("mods") or [])))

    rune_names: list[str] = []
    rune_effects: list[str] = []
    bonded_effects: list[str] = []
    rune_pools: list[str] = []

    for r in it.get("socketedRunes") or []:
        clean = str(r or "").strip()
        if not clean:
            continue
        pm = _RUNE_POOL_RE.match(clean)
        if pm:
            if pm.group(1) not in rune_pools:
                rune_pools.append(pm.group(1))
            continue
        if re.match(r"^bonded\s*:", clean, re.I):
            bonded_effects.append(re.sub(r"^bonded\s*:\s*", "", clean, flags=re.I).strip())
            continue
        if re.search(r"\d|gain |increased |reduced |to level |to spirit|to maximum |to all |grants ", clean, re.I):
            rune_effects.append(clean)
        else:
            rune_names.append(clean)

    is_unique = (it.get("rarity") or "").upper() == "UNIQUE"
    notes: list[str] = []
    if exceeds:
        if is_unique:
            notes.append(f"当前 {socket_count} 孔超过工匠石上限({natural_max}孔)，属于瓦尔额外孔位，需直买已瓦尔 {socket_count} 孔暗金成品（暗金无需复杂做装）")
        else:
            notes.append(f"当前 {socket_count} 孔超过工匠石上限({natural_max}孔)，半成品/白底需买卓越多孔底子，成品需锁孔位")
    elif socket_count > 0 and has_socket_eff:
        notes.append(f"含「镶嵌物效果增幅」词条，必须配合 {socket_count} 个符文插槽与高阶符文/灵核才吃满收益")
    elif socket_count > 0 and rune_effects:
        if is_unique:
            notes.append(f"{socket_count} 孔在工匠石上限({natural_max}孔)内，买普通暗金后自行打孔补插：{' + '.join(rune_names[:2]) or rune_effects[0]}")
        else:
            notes.append(f"{socket_count} 孔在工匠石上限({natural_max}孔)内，半成品可不锁孔，买后打孔补插：{' + '.join(rune_names[:2]) or rune_effects[0]}")
    if rune_pools and not is_unique:
        notes.append(f"符文解锁专属词池「{' / '.join(rune_pools)}」({rune_names[0] if rune_names else '符文'})，自做半成品需先插对应符文")
    if bonded_effects:
        if bp.get("bondedRunesActive"):
            notes.append(f"已点「Wisdom of the Maji」，符文 Bonded 词条生效（{bonded_effects[0]}）")
        else:
            notes.append("未点萨满 Bonded 升华，符文上的 Bonded 词条不生效，平替无需为此付溢价")

    return {
        "socketCount": socket_count,
        "naturalMaxSockets": natural_max,
        "exceedsCraftableSockets": exceeds,
        "hasSocketEffectMod": has_socket_eff,
        "bondedActive": bool(bp.get("bondedRunesActive") and bonded_effects),
        "socketedRunes": list(dict.fromkeys(rune_names + rune_effects)),
        "runeNames": rune_names,
        "runeEffects": rune_effects,
        "bondedEffects": bonded_effects,
        "runeUnlockedPools": rune_pools,
        "requireSocketsInBudget": bool((exceeds or has_socket_eff) and socket_count >= 2),
        "requireSocketsInSemi": bool((exceeds or has_socket_eff) and socket_count >= 2),
        "advice": "；".join(notes) or (f"{socket_count} 符文插槽（工匠石可打满）" if socket_count > 0 else "无符文插槽"),
    }


def analyze_item_deeply(it: dict[str, Any], parsed_build: dict[str, Any] | None = None) -> dict[str, Any]:
    """深度分析单件装备词条、天赋联动、符文插槽依赖、平替与半成品锁词策略。"""
    bp = extract_build_synergy_profile(parsed_build)
    slot = it.get("slot") or ""
    slot_l = slot.lower()
    base_type = it.get("baseType") or ""
    base_l = base_type.lower()
    skey = _slot_key(slot)
    is_speed_weapon = slot_l in bp["speedWeaponSlots"] or _is_speed_utility_weapon(it)
    sock = analyze_item_sockets(it, bp)

    rel_syns = [
        s for s in bp["slotSynergies"]
        if s["slotKey"] == skey or (s["slotKey"] == "armour" and _is_armour_slot(slot))
    ]
    passive_synergies = [s["label"] for s in rel_syns]

    def find_syn(kind: str) -> dict[str, Any] | None:
        return next((s for s in rel_syns if not s.get("penalize") and s["statKind"] in (kind, "all_def")), None)

    props = it.get("properties") or {}
    ward_val = _first_num(props.get("Ward"))
    es_val = _first_num(props.get("Energy Shield"))
    ev_val = _first_num(props.get("Evasion"))
    ar_val = _first_num(props.get("Armour"))
    origins = _resolve_mod_origins(it)

    is_unique = (it.get("rarity") or "").upper() == "UNIQUE"
    require_base = False
    base_reason = ""
    important_granted_skills: list[dict[str, Any]] = []
    for o in origins:
        gs = evaluate_granted_skill(o["text"], base_type=base_type, slot=slot, parsed_build=parsed_build)
        if gs and gs["isImportant"]:
            important_granted_skills.append(gs)

    if ward_val >= 40 or (bp["needsWardBase"] and "runeforged" in base_l):
        require_base = True
        base_reason = f"含结界 Ward ({int(ward_val) or '底子'})，支撑技能 Ward 消耗机制，平替不可换普通底子"
    elif important_granted_skills:
        require_base = True
        gs0 = important_granted_skills[0]
        sup_hint = f"（挂载 {len(gs0['linkedSupports'])} 颗辅助）" if gs0["linkedSupports"] else ""
        base_reason = f"基底自带重要技能「{gs0['skillName']}」{sup_hint}（{gs0['functionalDesc']}），换普通基底会直接产生功能损失"
    elif "unset ring" in base_l or any(re.search(r"grants 1 additional skill slot", o["text"], re.I) for o in origins):
        require_base = True
        base_reason = "潜能戒指提供额外技能槽位，平替需保留 Unset Ring 底子"
    elif "solar amulet" in base_l and not bp["gearSpiritDead"]:
        require_base = True
        base_reason = "基底自带精魂 (Spirit)，平替建议保留 Solar Amulet"
    elif "breach ring" in base_l:
        require_base = True
        base_reason = "裂隙戒指支持 50% 品质放大词缀，平替建议保留 Breach Ring"

    defense_floor: dict[str, Any] | None = None
    if ward_val >= 50:
        defense_floor = {"stat": "ward", "min": int(ward_val * 0.75), "raw": int(ward_val), "reason": f"结界底子 Ward {int(ward_val)}"}
    elif _is_armour_slot(slot):
        es_syn, ev_syn, ar_syn = find_syn("es"), find_syn("ev"), find_syn("ar")
        if es_syn and es_val >= 120:
            defense_floor = {"stat": "es", "min": int(es_val * 0.78), "raw": int(es_val), "reason": f"{es_syn['label']} → 优先锁总护盾 ≥ {int(es_val * 0.78)}"}
        elif ev_syn and ev_val >= 400:
            defense_floor = {"stat": "ev", "min": int(ev_val * 0.78), "raw": int(ev_val), "reason": f"{ev_syn['label']} → 优先锁总闪避 ≥ {int(ev_val * 0.78)}"}
        elif ar_syn and ar_val >= 400:
            defense_floor = {"stat": "ar", "min": int(ar_val * 0.78), "raw": int(ar_val), "reason": f"{ar_syn['label']} → 优先锁总护甲 ≥ {int(ar_val * 0.78)}"}
        elif bp["esCore"] and es_val >= 220 and es_val >= ev_val * 0.3:
            defense_floor = {"stat": "es", "min": int(es_val * 0.78), "raw": int(es_val), "reason": f"高护盾体系 → 优先锁总护盾 ≥ {int(es_val * 0.78)}"}

    has_build = parsed_build is not None
    analyzed_affixes: list[dict[str, Any]] = []
    for idx, entry in enumerate(origins):
        cleaned = entry["text"]
        t = cleaned.lower()
        origin = entry["origin"]
        is_anoint = entry["isAnoint"]
        side = infer_affix_side(cleaned, origin)
        difficulty = infer_craft_difficulty(cleaned, origin, is_anoint)
        tags: list[str] = []
        score = 15.0
        role = "utility"
        exclude_lock = False

        if is_anoint:
            analyzed_affixes.append({
                "index": idx,
                "line": cleaned,
                "origin": origin,
                "isAnoint": True,
                "side": "enchant",
                "craftDifficulty": "unrollable",
                "role": "flexible",
                "contextualScore": 5,
                "synergyTags": ["🫧 涂油可自涂(搜货不锁)"],
                "excludeFromTradeLock": True,
            })
            continue
        if re.match(r"^grants\s+skill:", t):
            gs = evaluate_granted_skill(cleaned, base_type=base_type, slot=slot, parsed_build=parsed_build)
            is_imp = bool(gs and gs["isImportant"])
            g_tags: list[str] = []
            if gs and is_imp:
                g_tags.append(f"⚡ 基底核心自带技能({gs['skillName']})")
                if gs["linkedSupports"]:
                    g_tags.append(f"🔗 挂载 {len(gs['linkedSupports'])} 颗辅助")
                g_tags.append("🔒 换底子即功能损失")
            else:
                g_tags.append("📌 类别默认基底技(未启用)")
            analyzed_affixes.append({
                "index": idx,
                "line": cleaned,
                "origin": "implicit",
                "isAnoint": False,
                "side": "implicit",
                "craftDifficulty": "unrollable",
                "role": "synergy" if is_imp else "utility",
                "contextualScore": 108 if is_imp else 8,
                "synergyTags": g_tags,
                "excludeFromTradeLock": True,  # 基底自带技能通过锁基底/暗金名体现，不作为显性词缀筛
                "grantedSkill": gs,
            })
            continue
        if re.match(r"^grants\s+1\s+additional\s+skill\s+slot", t):
            analyzed_affixes.append({
                "index": idx,
                "line": cleaned,
                "origin": "implicit",
                "isAnoint": False,
                "side": "implicit",
                "craftDifficulty": "unrollable",
                "role": "synergy",
                "contextualScore": 96,
                "synergyTags": ["⚡ 基底额外技能槽(Unset Ring 刚需)"],
                "excludeFromTradeLock": True,
            })
            continue
        if origin == "implicit" and re.match(r"^-1\s+(prefix|suffix)\s+modifier\s+allowed$", t):
            analyzed_affixes.append({
                "index": idx,
                "line": cleaned,
                "origin": "implicit",
                "isAnoint": False,
                "side": "implicit",
                "craftDifficulty": "unrollable",
                "role": "utility",
                "contextualScore": 10,
                "synergyTags": ["📌 特殊技能基底代价(换取自带精魂技)"],
                "excludeFromTradeLock": True,
            })
            continue

        if origin == "fractured":
            tags.append("🔒 破裂词条")
            score += 22

        if re.search(r"to level of all .*skills", t):
            is_spell_sk = bool(re.search(r"spell|fire|cold|lightning|chaos", t)) and not re.search(r"attack|projectile|melee", t)
            is_minion_sk = "minion" in t
            if is_minion_sk and has_build and not bp["isMinionBuild"]:
                score, role = 0, "dead"
                tags.append("⚠️ 非召唤无效")
            elif is_spell_sk and has_build and bp["isAttackBuild"] and not bp["isSpellBuild"]:
                score, role = 5, "dead"
                tags.append("⚠️ 攻击BD无效")
            else:
                score += 98
                role = "core"
                tags.append("🔥 核心+技能等级")
        elif re.search(r"additional (arrow|projectile)", t):
            score += 94
            role = "core"
            tags.append("🔥 核心额外投射物")
        elif re.search(r"explicit\s+speed\s+modifier\s+magnitudes", t):
            score += 96 if is_speed_weapon else 68
            role = "core"
            tags.append("⚡ 攻速工具组核心增幅" if is_speed_weapon else "🔥 速度词缀幅度增幅")
        elif _SOCKET_EFFECT_RE.search(t):
            score += 92 if sock["socketCount"] >= 2 else 58
            role = "synergy" if sock["socketCount"] >= 2 else "utility"
            tags.append(f"💎 符文插槽联动(配{sock['socketCount'] or 2}孔)")
        elif re.search(r"to spirit\b|increased spirit\b", t):
            if bp["gearSpiritDead"]:
                score, role = 0, "dead"
                tags.append("🌳 天赋冲突(装备精魂无效)")
            else:
                score += 88
                role = "core"
                tags.append("🔥 核心精魂")
        elif re.search(r"gain .* as extra .* damage", t):
            if bp["nonFireDead"] and not re.search(r"fire|all elements", t):
                score, role = 2, "dead"
                tags.append("🌳 火之化身冲突(非火伤无效)")
            else:
                score += 88
                role = "core"
                tags.append("🔥 额外伤害乘区")
        elif re.search(r"increased projectile speed", t):
            if bp["projSpeedIsDamage"]:
                score += 90
                role = "synergy"
                tags.append("🌳 投速转增伤(天赋/加速辅助)")
            elif bp["isProjectileBuild"]:
                score += 62
                role = "core"
                tags.append("🎯 投射物手感/射程")
            else:
                score += 25
        elif re.search(r"increased projectile damage", t):
            score += 78 if bp["isProjectileBuild"] else 35
            role = "core" if bp["isProjectileBuild"] else "utility"
            if bp["isProjectileBuild"]:
                tags.append("🔥 投射物核心增伤")
        elif re.search(r"for each (type of )?elemental ailment|elemental ailment", t):
            score += 86 if bp["elementalAilmentScaling"] else 64
            role = "synergy"
            if bp["elementalAilmentScaling"]:
                tags.append("🌳 异常增伤联动")
        elif re.search(r"increased movement speed", t):
            if "boot" in slot_l:
                score += 56 if bp["hasTravelAttack"] else 96
                role = "utility" if bp["hasTravelAttack"] else "core"
                if bp["hasTravelAttack"]:
                    tags.append("💡 位移技能跑图(鞋移速可降配)")
            else:
                score += 76
                role = "core"
        elif re.search(r"increased attack speed|increased skill speed", t):
            if has_build and bp["isSpellBuild"] and not bp["isAttackBuild"] and "attack speed" in t:
                score, role = 5, "dead"
                tags.append("⚠️ 法术BD攻速无效")
            else:
                score += 98 if is_speed_weapon else 80
                role = "core"
                if is_speed_weapon:
                    tags.append("⚡ 攻速工具组必保")
        elif re.search(r"increased cast speed", t):
            if has_build and bp["isAttackBuild"] and not bp["isSpellBuild"]:
                score, role = 5, "dead"
                tags.append("⚠️ 攻击BD施速无效")
            else:
                score += 80
                role = "core"
        elif re.search(r"critical (hit |strike )?chance|critical (spell )?damage bonus", t):
            if bp["critDead"]:
                score, role = 0, "dead"
                tags.append("🌳 必中(RT)冲突(暴击无效)")
            else:
                score += 82 if bp["isCritBuild"] else 68
                role = "core" if bp["isCritBuild"] else "utility"
                if bp["isCritBuild"]:
                    tags.append("🔥 暴击体系核心")
        elif re.search(r"adds \d+ to \d+ (physical|lightning|cold|fire|chaos) damage", t):
            m = re.search(r"adds (\d+) to (\d+)", t)
            avg = (float(m.group(1)) + float(m.group(2))) / 2.0 if m else 0.0
            if is_speed_weapon and avg < 45:
                score, role = 6, "dead"
                tags.append("⚡ 攻速工具武器低T点伤(可砍)")
            elif bp["nonFireDead"] and re.search(r"cold|lightning|chaos", t):
                score, role = 2, "dead"
                tags.append("🌳 火之化身冲突")
            elif has_build and bp["isSpellBuild"] and not bp["isAttackBuild"] and "to attacks" in t:
                score, role = 4, "dead"
                tags.append("⚠️ 法术BD攻击点伤无效")
            elif "weapon" in slot_l and 0 < avg < 25:
                score += 22
                role = "flexible"
                tags.append("💡 低档点伤(可降配)")
            else:
                score += 76
                role = "core"
        elif re.search(r"increased physical damage", t):
            if is_speed_weapon:
                score, role = 8, "flexible"
                tags.append("⚡ 攻速工具武器可砍物理%")
            else:
                score += 80
                role = "core"
        elif re.search(r"increased (spell|elemental|lightning|cold|fire|chaos|attack) damage", t):
            if "spell damage" in t and has_build and bp["isAttackBuild"] and not bp["isSpellBuild"]:
                score, role = 5, "dead"
                tags.append("⚠️ 攻击BD法伤无效")
            else:
                score += 74
                role = "core"
        elif re.search(r"deflection rating equal to|deflect", t):
            score += 86 if bp["deflectionScaling"] else 72
            role = "defense"
            tags.append("🌳 偏转防御联动核心" if bp["deflectionScaling"] else "🛡️ 核心偏转减伤")
        elif re.search(r"energy shield recharge", t):
            score += 38 if bp["esCore"] else 22
            role = "utility"
        elif re.search(r"to maximum energy shield|increased energy shield(?!\s+recharge)|increased evasion and energy shield|increased armour and energy shield", t):
            es_syn, ev_syn = find_syn("es"), find_syn("ev")
            if es_syn:
                score += 76 + es_syn["boost"] * 0.45
                role = "synergy"
                tags.append(f"🌳 {es_syn['label']}")
            elif "evasion" in t and ev_syn:
                score += 74 + ev_syn["boost"] * 0.4
                role = "synergy"
                tags.append(f"🌳 {ev_syn['label']}")
            elif bp["esToMana"]:
                score += 80
                role = "synergy"
                tags.append("🌳 异能魔力(ES→魔力)")
            elif bp["esCore"] or any(k in slot_l for k in ("helmet", "body", "shield", "focus")):
                score += 68
                role = "defense"
            else:
                score += 42
                role = "defense"
        elif re.search(r"to evasion rating|increased evasion", t):
            ev_syn = find_syn("ev")
            ev_pen = next((s for s in rel_syns if s.get("penalize") and s["statKind"] == "ev"), None)
            if ev_pen:
                score += 18
                role = "flexible"
                tags.append(f"🌳 {ev_pen['label']}")
            elif ev_syn:
                score += 76 + ev_syn["boost"] * 0.45
                role = "synergy"
                tags.append(f"🌳 {ev_syn['label']}")
            elif bp["evasionToArmour"]:
                score += 68
                role = "synergy"
                tags.append("🌳 钢铁反射(闪避→护甲)")
            else:
                score += 66 if _is_armour_slot(slot) else 36
                role = "defense"
        elif re.search(r"to armour|increased armour", t):
            ar_syn = find_syn("ar")
            if ar_syn:
                score += 76 + ar_syn["boost"] * 0.45
                role = "synergy"
                tags.append(f"🌳 {ar_syn['label']}")
            else:
                score += 62 if _is_armour_slot(slot) else 34
                role = "defense"
        elif re.search(r"to maximum life\b", t):
            if bp["lifeDead"]:
                score, role = 0, "dead"
                tags.append("🌳 异灵之体(CI)生命无效")
            else:
                score += 64
                role = "defense"
        elif re.search(r"to maximum mana\b|increased maximum mana\b|mana regeneration|mana cost efficiency", t):
            if bp["manaDead"]:
                score, role = 0, "dead"
                tags.append("🌳 血魔法(魔力无效)")
            elif bp["manaCore"]:
                score += 84
                role = "synergy"
                tags.append("🌳 蓝池/大法师核心")
            else:
                score += 48
                role = "utility"
        elif re.search(r"to chaos resistance", t):
            if bp["chaosResDead"]:
                score, role = 0, "dead"
                tags.append("🌳 异灵之体(CI)混抗无效")
            else:
                score += 56
                role = "defense"
        elif re.search(r"to all elemental resistances", t):
            score += 60
            role = "defense"
        elif re.search(r"to (fire|cold|lightning) resistance", t):
            score += 40
            role = "flexible"
            tags.append("🔄 单抗可跨部位/符文调配")
        elif re.search(r"accuracy rating", t):
            if bp["accuracyIsDamage"]:
                score += 84
                role = "synergy"
                tags.append("🌳 天赋联动(命中→攻速/暴击/伤害)")
            else:
                score, role = 6, "flexible"
        elif re.search(r"to all attributes", t):
            if bp["strScaling"] or bp["dexScaling"] or bp["intScaling"]:
                score += 78
                role = "synergy"
                tags.append("🌳 属性堆叠联动")
            else:
                score += 48
        elif re.search(r"to strength\b", t):
            if bp["strScaling"] or bp["giantBlood"]:
                score += 74
                role = "synergy"
                tags.append("🌳 巨人之血(力量刚需)" if bp["giantBlood"] else "🌳 力量增伤联动")
            else:
                score += 26
                role = "flexible"
        elif re.search(r"to dexterity\b", t):
            if bp["dexScaling"]:
                score += 74
                role = "synergy"
                tags.append("🌳 敏捷增伤/攻速联动")
            else:
                score += 26
                role = "flexible"
        elif re.search(r"to intelligence\b", t):
            if bp["intScaling"] or bp["manaCore"]:
                score += 74
                role = "synergy"
                tags.append("🌳 智慧增伤联动")
            else:
                score += 26
                role = "flexible"
        elif re.search(r"increased rarity of items found", t):
            score += 42
            role = "flexible"
            tags.append("💰 打宝稀有度(平替可先降配)")
        elif re.search(r"charm slot", t):
            score += 66
            role = "core"
        elif re.search(r"light radius|stun threshold|thorns|life regeneration|reduced attribute requirements", t):
            score, role = 4, "flexible"

        if any("marksman" in p.lower() for p in sock["runeUnlockedPools"]) and re.search(
            r"projectile speed|projectile damage|mark skills|level of all projectile", t
        ):
            tags.append("💎 符文池(Marksman)")
        if any("destruction" in p.lower() for p in sock["runeUnlockedPools"]) and re.search(
            r"explicit speed modifier|attack speed|critical", t
        ):
            tags.append("💎 符文池(Destruction)")

        if role == "dead":
            exclude_lock = True

        analyzed_affixes.append({
            "index": idx,
            "line": cleaned,
            "origin": origin,
            "isAnoint": False,
            "side": side,
            "craftDifficulty": difficulty,
            "role": role,
            "contextualScore": int(round(score)),
            "synergyTags": list(dict.fromkeys(tags)),
            "excludeFromTradeLock": exclude_lock,
        })

    # 生成平替 & 半成品推荐方案（按词条骨架去重；若已锁定总防值门槛，平替优先保非冗余词条如偏转/抗性）
    def _dedup(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for r in rows:
            sk = _mod_skeleton(r["line"])
            if sk in seen:
                continue
            seen.add(sk)
            out.append(r)
        return out

    active = _dedup(
        sorted(
            [a for a in analyzed_affixes if not a["excludeFromTradeLock"] and a["role"] != "dead"],
            key=lambda x: x["contextualScore"],
            reverse=True,
        )
    )
    non_redundant = (
        [a for a in active if not _is_local_defense_stat_of(a["line"], defense_floor["stat"])]
        if defense_floor
        else active
    )
    budget_cands = [a["line"] for a in non_redundant if a["contextualScore"] >= 55]
    budget_locks = budget_cands[:3] if budget_cands else [a["line"] for a in active if a["contextualScore"] >= 55][:3]
    if not budget_locks:
        budget_locks = [a["line"] for a in active[:2]]

    diff_rank = {"hard": 2, "medium": 1, "easy": 0, "unrollable": -1}
    semi_sorted = sorted(
        [a for a in active if a["craftDifficulty"] in ("hard", "medium") or a["origin"] == "fractured" or a["contextualScore"] >= 70],
        key=lambda a: (1 if a["origin"] == "fractured" else 0, diff_rank.get(a["craftDifficulty"], 0), a["contextualScore"]),
        reverse=True,
    )
    semi_locks = [a["line"] for a in semi_sorted[:2]] or budget_locks[:2]

    budget_notes: list[str] = []
    if require_base:
        budget_notes.append(base_reason)
    if defense_floor:
        budget_notes.append(defense_floor["reason"])
    if sock["requireSocketsInBudget"]:
        budget_notes.append(f"平替需锁 ≥{sock['socketCount']} 符文孔（{sock['advice']}）")
    elif sock["runeNames"]:
        budget_notes.append(f"买完平替记得补插符文：{' + '.join(sock['runeNames'][:2])}")
    dead_cnt = sum(1 for a in analyzed_affixes if a["role"] == "dead")
    if dead_cnt:
        budget_notes.append(f"已自动剔除 {dead_cnt} 条与当前天赋/定位冲突的无效词条")

    semi_notes: list[str] = []
    if sock["requireSocketsInSemi"]:
        semi_notes.append(f"必须买非腐化 ≥{sock['socketCount']} 孔卓越底子（超过工匠石 {sock['naturalMaxSockets']} 孔上限）")
    elif sock["naturalMaxSockets"] > 0:
        semi_notes.append(f"半成品不强求满孔（工匠石最多可打 {sock['naturalMaxSockets']} 孔），优先看非腐化与核心词缀空位")
    if sock["runeUnlockedPools"]:
        semi_notes.append(f"加工前需先插「{sock['runeNames'][0] if sock['runeNames'] else sock['runeUnlockedPools'][0]}」解锁专属词池（{'/'.join(sock['runeUnlockedPools'])}）")
    if require_base:
        semi_notes.append(f"底子锁定：{base_type}（{base_reason}）")

    if is_unique:
        u_notes: list[str] = []
        if sock["exceedsCraftableSockets"]:
            u_notes.append(f"瓦尔额外孔位（{sock['socketCount']}孔 > 工匠石上限 {sock['naturalMaxSockets']}孔），需直买已瓦尔多孔成品")
        elif sock["runeNames"]:
            u_notes.append(f"买后打孔补插符文：{' + '.join(sock['runeNames'][:2])}")
        if important_granted_skills:
            u_notes.append(f"自带技能「{important_granted_skills[0]['skillName']}」随暗金固定附带，挑货时注意技能等级与关键 roll")
        if any(a["isAnoint"] for a in analyzed_affixes):
            u_notes.append("项链涂油可买完自行补涂，搜货不必锁涂油")
        unique_summary = " · ".join(u_notes[:2]) or "传奇/暗金直接购买成品（仅挑关键数值 roll、瓦尔额外孔/瓦尔基底、打孔插符文或涂油，无需做装）"
        return {
            "slot": slot,
            "isUnique": True,
            "isSpeedWeapon": is_speed_weapon,
            "requireBaseType": require_base,
            "baseTypeReason": base_reason,
            "importantGrantedSkills": important_granted_skills,
            "defenseFloor": defense_floor,
            "passiveSynergies": passive_synergies,
            "socketAnalysis": sock,
            "affixes": analyzed_affixes,
            "budgetPlan": {
                "lockAffixes": [],
                "requireBaseType": False,
                "requireSockets": sock["socketCount"] if sock["exceedsCraftableSockets"] else None,
                "defenseFloor": None,
                "summary": unique_summary,
                "notes": u_notes or [unique_summary],
            },
            "semiPlan": {
                "lockAffixes": [],
                "requireBaseType": False,
                "requireSockets": None,
                "defenseFloor": None,
                "summary": "传奇/暗金装备不可用半成品或通货重做词缀，仅需直买成品（或按需挑瓦尔多孔/瓦尔基底）",
                "notes": ["传奇/暗金装备无需复杂做装，仅挑 roll / 打满工匠孔插符文 / 瓦尔基底 / 涂油"],
            },
        }

    return {
        "slot": slot,
        "isUnique": False,
        "isSpeedWeapon": is_speed_weapon,
        "requireBaseType": require_base,
        "baseTypeReason": base_reason,
        "importantGrantedSkills": important_granted_skills,
        "defenseFloor": defense_floor,
        "passiveSynergies": passive_synergies,
        "socketAnalysis": sock,
        "affixes": analyzed_affixes,
        "budgetPlan": {
            "lockAffixes": budget_locks,
            "requireBaseType": require_base,
            "requireSockets": sock["socketCount"] if sock["requireSocketsInBudget"] else None,
            "defenseFloor": (
                {"stat": defense_floor["stat"], "min": defense_floor["min"]} if defense_floor else None
            ),
            "summary": " · ".join(budget_notes[:2]) or (f"平替优先保：{' + '.join(budget_locks[:2])}" if budget_locks else "按核心词缀降配"),
            "notes": budget_notes,
        },
        "semiPlan": {
            "lockAffixes": semi_locks,
            "requireBaseType": require_base,
            "requireSockets": sock["socketCount"] if sock["requireSocketsInSemi"] else None,
            "defenseFloor": (
                {"stat": defense_floor["stat"], "min": defense_floor["min"]} if defense_floor else None
            ),
            "summary": " · ".join(semi_notes[:2]) or (f"半成品锁最难出词条：{' + '.join(semi_locks)}" if semi_locks else "锁 1~2 条难出核心词缀 + 非腐化"),
            "notes": semi_notes,
        },
    }
