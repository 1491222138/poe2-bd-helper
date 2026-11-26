"""BD 倒推引擎：基于 PoB2 边际灵敏度探针 + 槽位全词缀池交集 + 装备面板底线 + 沙盒验证。

核心思想：
  1. 不再死板复制原 BD 装备上的固定词条，而是把目标 BD 当作 PoB2 黑盒函数，通过 ~20-30 次并行微扰探针
     一次性实算出所有常见输出/防御/资源词缀的真实单位边际贡献（每 1 单位带来多少 %DPS / %EHP）；
  2. 将灵敏度向量与 RePoE 全槽位合法词缀池求交集，自动倒推出每个部位【全部有效等价词缀】（含原件没带但同样有效的词条），
     并输出四段式搜索规格：
       - equipmentFloors（攻击武器 dps/pdps/aps/crit、防具 es/ev/ar/ward，保证大类相同不令技能失效）
       - mandatoryHard（AND 硬绑定：基底自带核心技能 / 关键机制）
       - mandatoryAnyOf（COUNT >= 1 等价核心门槛，如 +全法术等级 OR +闪电法术等级）
       - offensePool / defensePool（攻防分离的双 Weighted Sum OR 加权池，彻底防抗性刷分）
  3. 提供全身抗性/精魂/三属性红线账本与 3 个 PoB2 沙盒实测工具函数，供后续 AI 抄作业调用验证。
"""
from __future__ import annotations

import math
import re
import time
from typing import Any

from backend.domain import affix_analyzer, trade_url, weapon_bases
from backend.domain.common import safe_num as _num
from backend.domain.crafting import affix_pool
from backend.engine import pob_parser, runefix, xml_mutator
from backend.engine.worker_pool import WorkerPool

REVERSE_SPEC_VERSION = "v2-pob2-weapon-bases"

# ---------------------------------------------------------------------------
# 1. 全局标准探针词缀目录（全部为 PoB2 原生可解析英文词缀）
# ---------------------------------------------------------------------------
GLOBAL_PROBES: tuple[dict[str, Any], ...] = (
    # --- 技能等级（高乘区，优先探测） ---
    {
        "id": "lvl_all_skills",
        "bucket": "offense",
        "zh": "所有技能等级",
        "probeMod": "+1 to Level of all Skills",
        "delta": 1.0,
        "tradeEn": "+# to Level of all Skills",
        "tags": ("always",),
        "isSkillLevel": True,
        "patterns": (r"level of all skills$",),
    },
    {
        "id": "lvl_spell",
        "bucket": "offense",
        "zh": "所有法术技能等级",
        "probeMod": "+1 to Level of all Spell Skills",
        "delta": 1.0,
        "tradeEn": "+# to Level of all Spell Skills",
        "tags": ("spell",),
        "isSkillLevel": True,
        "patterns": (r"level of all spell skills",),
    },
    {
        "id": "lvl_lightning_spell",
        "bucket": "offense",
        "zh": "闪电法术技能等级",
        "probeMod": "+1 to Level of all Lightning Spell Skills",
        "delta": 1.0,
        "tradeEn": "+# to Level of all Lightning Spell Skills",
        "tags": ("spell",),
        "isSkillLevel": True,
        "patterns": (r"level of all lightning spell skills",),
    },
    {
        "id": "lvl_cold_spell",
        "bucket": "offense",
        "zh": "冰霜法术技能等级",
        "probeMod": "+1 to Level of all Cold Spell Skills",
        "delta": 1.0,
        "tradeEn": "+# to Level of all Cold Spell Skills",
        "tags": ("spell",),
        "isSkillLevel": True,
        "patterns": (r"level of all cold spell skills",),
    },
    {
        "id": "lvl_fire_spell",
        "bucket": "offense",
        "zh": "火焰法术技能等级",
        "probeMod": "+1 to Level of all Fire Spell Skills",
        "delta": 1.0,
        "tradeEn": "+# to Level of all Fire Spell Skills",
        "tags": ("spell",),
        "isSkillLevel": True,
        "patterns": (r"level of all fire spell skills",),
    },
    {
        "id": "lvl_chaos_spell",
        "bucket": "offense",
        "zh": "混沌法术技能等级",
        "probeMod": "+1 to Level of all Chaos Spell Skills",
        "delta": 1.0,
        "tradeEn": "+# to Level of all Chaos Spell Skills",
        "tags": ("spell",),
        "isSkillLevel": True,
        "patterns": (r"level of all chaos spell skills",),
    },
    {
        "id": "lvl_phys_spell",
        "bucket": "offense",
        "zh": "物理法术技能等级",
        "probeMod": "+1 to Level of all Physical Spell Skills",
        "delta": 1.0,
        "tradeEn": "+# to Level of all Physical Spell Skills",
        "tags": ("spell",),
        "isSkillLevel": True,
        "patterns": (r"level of all physical spell skills",),
    },
    {
        "id": "lvl_projectile",
        "bucket": "offense",
        "zh": "投射物技能等级",
        "probeMod": "+1 to Level of all Projectile Skills",
        "delta": 1.0,
        "tradeEn": "+# to Level of all Projectile Skills",
        "tags": ("attack", "proj"),
        "isSkillLevel": True,
        "patterns": (r"level of all projectile skills",),
    },
    {
        "id": "lvl_melee",
        "bucket": "offense",
        "zh": "近战技能等级",
        "probeMod": "+1 to Level of all Melee Skills",
        "delta": 1.0,
        "tradeEn": "+# to Level of all Melee Skills",
        "tags": ("attack", "melee"),
        "isSkillLevel": True,
        "patterns": (r"level of all melee skills",),
    },
    {
        "id": "lvl_minion",
        "bucket": "offense",
        "zh": "召唤物技能等级",
        "probeMod": "+1 to Level of all Minion Skills",
        "delta": 1.0,
        "tradeEn": "+# to Level of all Minion Skills",
        "tags": ("minion",),
        "isSkillLevel": True,
        "patterns": (r"level of all minion skills",),
    },
    # --- 额外伤（法系与攻击系高价值通用乘区） ---
    {
        "id": "extra_lightning",
        "bucket": "offense",
        "zh": "额外闪电伤害",
        "probeMod": "Gain 20% of Damage as Extra Lightning Damage",
        "delta": 20.0,
        "tradeEn": "Gain #% of Damage as Extra Lightning Damage",
        "tags": ("spell", "attack"),
        "patterns": (r"gain .* as extra lightning damage",),
    },
    {
        "id": "extra_cold",
        "bucket": "offense",
        "zh": "额外冰霜伤害",
        "probeMod": "Gain 20% of Damage as Extra Cold Damage",
        "delta": 20.0,
        "tradeEn": "Gain #% of Damage as Extra Cold Damage",
        "tags": ("spell", "attack"),
        "patterns": (r"gain .* as extra cold damage",),
    },
    {
        "id": "extra_fire",
        "bucket": "offense",
        "zh": "额外火焰伤害",
        "probeMod": "Gain 20% of Damage as Extra Fire Damage",
        "delta": 20.0,
        "tradeEn": "Gain #% of Damage as Extra Fire Damage",
        "tags": ("spell", "attack"),
        "patterns": (r"gain .* as extra fire damage",),
    },
    {
        "id": "extra_chaos",
        "bucket": "offense",
        "zh": "额外混沌伤害",
        "probeMod": "Gain 20% of Damage as Extra Chaos Damage",
        "delta": 20.0,
        "tradeEn": "Gain #% of Damage as Extra Chaos Damage",
        "tags": ("spell", "attack"),
        "patterns": (r"gain .* as extra chaos damage",),
    },
    # --- 百分比增伤（法伤 / 元素伤 / 混沌伤 / 攻击元素伤 / 物理伤） ---
    {
        "id": "inc_spell",
        "bucket": "offense",
        "zh": "法术伤害%",
        "probeMod": "60% increased Spell Damage",
        "delta": 60.0,
        "tradeEn": "#% increased Spell Damage",
        "tags": ("spell",),
        "patterns": (r"increased spell damage",),
    },
    {
        "id": "inc_lightning",
        "bucket": "offense",
        "zh": "闪电伤害%",
        "probeMod": "60% increased Lightning Damage",
        "delta": 60.0,
        "tradeEn": "#% increased Lightning Damage",
        "tags": ("spell", "attack"),
        "patterns": (r"increased lightning damage",),
    },
    {
        "id": "inc_cold",
        "bucket": "offense",
        "zh": "冰霜伤害%",
        "probeMod": "60% increased Cold Damage",
        "delta": 60.0,
        "tradeEn": "#% increased Cold Damage",
        "tags": ("spell", "attack"),
        "patterns": (r"increased cold damage",),
    },
    {
        "id": "inc_fire",
        "bucket": "offense",
        "zh": "火焰伤害%",
        "probeMod": "60% increased Fire Damage",
        "delta": 60.0,
        "tradeEn": "#% increased Fire Damage",
        "tags": ("spell", "attack"),
        "patterns": (r"increased fire damage",),
    },
    {
        "id": "inc_chaos",
        "bucket": "offense",
        "zh": "混沌伤害%",
        "probeMod": "60% increased Chaos Damage",
        "delta": 60.0,
        "tradeEn": "#% increased Chaos Damage",
        "tags": ("spell", "attack"),
        "patterns": (r"increased chaos damage",),
    },
    {
        "id": "inc_attack_ele",
        "bucket": "offense",
        "zh": "攻击元素伤害%",
        "probeMod": "40% increased Elemental Damage with Attacks",
        "delta": 40.0,
        "tradeEn": "#% increased Elemental Damage with Attacks",
        "tags": ("attack",),
        "patterns": (r"increased elemental damage with attacks",),
    },
    # --- 速度 / 暴击 / 命中 / 投速 ---
    {
        "id": "cast_speed",
        "bucket": "offense",
        "zh": "施法速度%",
        "probeMod": "20% increased Cast Speed",
        "delta": 20.0,
        "tradeEn": "#% increased Cast Speed",
        "tags": ("spell",),
        "patterns": (r"increased cast speed",),
    },
    {
        "id": "attack_speed",
        "bucket": "offense",
        "zh": "攻击速度%",
        "probeMod": "15% increased Attack Speed",
        "delta": 15.0,
        "tradeEn": "#% increased Attack Speed",
        "tags": ("attack",),
        "patterns": (r"increased attack speed",),
    },
    {
        "id": "spell_crit_chance",
        "bucket": "offense",
        "zh": "法术暴击率%",
        "probeMod": "60% increased Critical Hit Chance for Spells",
        "delta": 60.0,
        "tradeEn": "#% increased Critical Hit Chance for Spells",
        "tags": ("spell",),
        "patterns": (r"increased critical .* chance for spells",),
    },
    {
        "id": "crit_chance",
        "bucket": "offense",
        "zh": "全局暴击率%",
        "probeMod": "40% increased Critical Hit Chance",
        "delta": 40.0,
        "tradeEn": "#% increased Critical Hit Chance",
        "tags": ("attack", "spell"),
        "patterns": (r"increased critical (?:hit|strike) chance$",),
    },
    {
        "id": "crit_bonus",
        "bucket": "offense",
        "zh": "暴击伤害加成%",
        "probeMod": "+30% to Critical Damage Bonus",
        "delta": 30.0,
        "tradeEn": "+#% to Critical Damage Bonus",
        "tags": ("always",),
        "patterns": (r"critical damage bonus", r"critical strike multiplier"),
    },
    {
        "id": "proj_speed",
        "bucket": "offense",
        "zh": "投射物速度%",
        "probeMod": "30% increased Projectile Speed",
        "delta": 30.0,
        "tradeEn": "#% increased Projectile Speed",
        "tags": ("attack", "proj", "spell"),
        "patterns": (r"increased projectile speed",),
    },
    {
        "id": "accuracy",
        "bucket": "offense",
        "zh": "命中值",
        "probeMod": "+300 to Accuracy Rating",
        "delta": 300.0,
        "tradeEn": "+# to Accuracy Rating",
        "tags": ("attack",),
        "patterns": (r"to accuracy rating",),
    },
    # --- 攻击附加点伤（手套/戒指/箭袋/项链通用） ---
    {
        "id": "flat_phys_attack",
        "bucket": "offense",
        "zh": "攻击附加物理点伤",
        "probeMod": "Adds 15 to 25 Physical Damage to Attacks",
        "delta": 20.0,
        "tradeEn": "Adds # to # Physical Damage to Attacks",
        "tags": ("attack",),
        "patterns": (r"adds .* physical damage to attacks",),
    },
    {
        "id": "flat_lightning_attack",
        "bucket": "offense",
        "zh": "攻击附加闪电点伤",
        "probeMod": "Adds 5 to 35 Lightning Damage to Attacks",
        "delta": 20.0,
        "tradeEn": "Adds # to # Lightning Damage to Attacks",
        "tags": ("attack",),
        "patterns": (r"adds .* lightning damage to attacks",),
    },
    {
        "id": "flat_cold_attack",
        "bucket": "offense",
        "zh": "攻击附加冰霜点伤",
        "probeMod": "Adds 15 to 25 Cold Damage to Attacks",
        "delta": 20.0,
        "tradeEn": "Adds # to # Cold Damage to Attacks",
        "tags": ("attack",),
        "patterns": (r"adds .* cold damage to attacks",),
    },
    {
        "id": "flat_fire_attack",
        "bucket": "offense",
        "zh": "攻击附加火焰点伤",
        "probeMod": "Adds 15 to 25 Fire Damage to Attacks",
        "delta": 20.0,
        "tradeEn": "Adds # to # Fire Damage to Attacks",
        "tags": ("attack",),
        "patterns": (r"adds .* fire damage to attacks",),
    },
    # --- 生存 / 魔力 / 精魂 / 属性 ---
    {
        "id": "max_life",
        "bucket": "defense",
        "zh": "最大生命",
        "probeMod": "+100 to maximum Life",
        "delta": 100.0,
        "tradeEn": "+# to maximum Life",
        "tags": ("always",),
        "patterns": (r"to maximum life$",),
    },
    {
        "id": "max_es",
        "bucket": "defense",
        "zh": "最大能量护盾",
        "probeMod": "+100 to maximum Energy Shield",
        "delta": 100.0,
        "tradeEn": "+# to maximum Energy Shield",
        "tags": ("always",),
        "patterns": (r"to maximum energy shield$",),
    },
    {
        "id": "max_mana",
        "bucket": "defense",
        "zh": "最大魔力",
        "probeMod": "+100 to maximum Mana",
        "delta": 100.0,
        "tradeEn": "+# to maximum Mana",
        "tags": ("always",),
        "patterns": (r"to maximum mana$",),
    },
    {
        "id": "spirit",
        "bucket": "defense",
        "zh": "精魂",
        "probeMod": "+30 to Spirit",
        "delta": 30.0,
        "tradeEn": "+# to Spirit",
        "tags": ("always",),
        "patterns": (r"to spirit$",),
    },
    {
        "id": "mana_regen",
        "bucket": "sustain",
        "zh": "魔力回复速度%",
        "probeMod": "40% increased Mana Regeneration Rate",
        "delta": 40.0,
        "tradeEn": "#% increased Mana Regeneration Rate",
        "tags": ("spell",),
        "patterns": (r"increased mana regeneration rate",),
    },
    {
        "id": "chaos_res",
        "bucket": "defense",
        "zh": "混沌抗性%",
        "probeMod": "+25% to Chaos Resistance",
        "delta": 25.0,
        "tradeEn": "+#% to Chaos Resistance",
        "tags": ("always",),
        "patterns": (r"to chaos resistance$",),
    },
    {
        "id": "str_attr",
        "bucket": "defense",
        "zh": "力量",
        "probeMod": "+30 to Strength",
        "delta": 30.0,
        "tradeEn": "+# to Strength",
        "tags": ("always",),
        "patterns": (r"to strength$",),
    },
    {
        "id": "dex_attr",
        "bucket": "defense",
        "zh": "敏捷",
        "probeMod": "+30 to Dexterity",
        "delta": 30.0,
        "tradeEn": "+# to Dexterity",
        "tags": ("always",),
        "patterns": (r"to dexterity$",),
    },
    {
        "id": "int_attr",
        "bucket": "defense",
        "zh": "智慧",
        "probeMod": "+30 to Intelligence",
        "delta": 30.0,
        "tradeEn": "+# to Intelligence",
        "tags": ("always",),
        "patterns": (r"to intelligence$",),
    },
)

_SPELL_BASE_RE = re.compile(r"\b(wand|staff|sceptre|focus)\b", re.I)
_ATTACK_BASE_RE = re.compile(
    r"\b(bow|crossbow|spear|quarterstaff|warstaff|sword|axe|mace|dagger|claw|flail|talisman|quiver)\b",
    re.I,
)
_MINION_SKILL_RE = re.compile(r"skeletal|zombie|spectre|raging spirit|minion|unearthed|wolf pack|companion", re.I)
_SPELL_SKILL_RE = re.compile(
    r"spark|arc|ball lightning|fireball|flameblast|comet|frostbolt|ice nova|eye of winter|essence drain|"
    r"contagion|hexblast|bonestorm|detonate dead|cold snap|ember fusillade|frost wall|lightning conduit|"
    r"solar orb|living bomb|dark effigy|unearth|firestorm|incinerate|volatile dead|blasphemy|archmage",
    re.I,
)
_PROJ_SKILL_RE = re.compile(
    r"arrow|shot|bolt|spear|twister|spark|fireball|frostbolt|eye of winter|lance|javelin|galvanic|shockburst|"
    r"grenade|barrage|salvo|tornado|snipe|spiral volley|rain of arrows|plasma|frag|piercing",
    re.I,
)
_MELEE_SKILL_RE = re.compile(
    r"slam|strike|flurry|slash|sunder|hammer|rolling|boneshatter|earthshatter|stampede|flicker|whirling|"
    r"tempest|ice strike|charged staff|falling thunder|leap|rake|fangs|molten|vaulting",
    re.I,
)


def _detect_build_probe_tags(parsed: dict[str, Any], base_sim: dict[str, Any]) -> set[str]:
    """根据主技能、激活武器与词缀快速判断该 BD 属于法术/攻击/投射物/近战/召唤哪类，精简探针数至 ~24 个。"""
    tags: set[str] = {"always"}
    main_name = str(base_sim.get("mainSkillName") or "")
    if not main_name:
        for sg in parsed.get("skillGroups") or []:
            if sg.get("isMain") and sg.get("gems"):
                main_name = str(sg["gems"][0].get("name") or "")
                break
    all_gem_names = " ".join(
        str(g.get("name") or "")
        for sg in (parsed.get("skillGroups") or [])
        for g in (sg.get("gems") or [])
    )
    active_ws = int((parsed.get("weaponSets") or {}).get("activeWeaponSet") or 1)
    w_slot = "weapon 1" if active_ws == 1 else "weapon 1 swap"
    w_item = next(
        (it for it in (parsed.get("equippedItems") or []) if str(it.get("slot") or "").lower() == w_slot),
        None,
    )
    w_base = str((w_item or {}).get("baseType") or "")
    w_mods = " ".join((w_item or {}).get("mods") or [])

    is_spell = bool(
        _SPELL_BASE_RE.search(w_base)
        or _SPELL_SKILL_RE.search(main_name)
        or re.search(r"spell damage|cast speed|spell skills", w_mods, re.I)
    )
    is_attack = bool(
        _ATTACK_BASE_RE.search(w_base)
        or (not is_spell and not _MINION_SKILL_RE.search(main_name))
    )
    if is_spell:
        tags.add("spell")
    if is_attack:
        tags.add("attack")
    if _PROJ_SKILL_RE.search(main_name) or "bow" in w_base.lower() or "crossbow" in w_base.lower() or "spear" in w_base.lower():
        tags.add("proj")
    if _MELEE_SKILL_RE.search(main_name) or re.search(r"mace|sword|axe|quarterstaff|warstaff|dagger|claw|flail|spear", w_base, re.I):
        tags.add("melee")
    if _MINION_SKILL_RE.search(main_name) or _MINION_SKILL_RE.search(all_gem_names):
        tags.add("minion")
    # 混合/无法确定时同时测攻击与法术，交给 PoB2 自动淘汰 0 收益项
    if "spell" not in tags and "attack" not in tags:
        tags.update(("spell", "attack"))
    return tags


def _parse_weapon_props(it: dict[str, Any]) -> dict[str, float]:
    props = it.get("properties") or {}
    aps_m = re.search(r"(\d+(?:\.\d+)?)", str(props.get("Attacks per Second") or ""))
    crit_m = re.search(r"(\d+(?:\.\d+)?)", str(props.get("Critical Hit Chance") or props.get("Critical Strike Chance") or ""))
    reload_m = re.search(r"(\d+(?:\.\d+)?)", str(props.get("Reload Time") or ""))
    spirit_m = re.search(r"(\d+(?:\.\d+)?)", str(props.get("Spirit") or ""))
    aps = float(aps_m.group(1)) if aps_m else 0.0
    crit = float(crit_m.group(1)) if crit_m else 0.0
    reload_time = float(reload_m.group(1)) if reload_m else 0.0
    spirit = float(spirit_m.group(1)) if spirit_m else 0.0
    phys_avg = 0.0
    for lo, hi in re.findall(r"(\d+)\s*-\s*(\d+)", str(props.get("Physical Damage") or "")):
        phys_avg += (float(lo) + float(hi)) / 2.0
    ele_avg = 0.0
    for lo, hi in re.findall(r"(\d+)\s*-\s*(\d+)", str(props.get("Elemental Damage") or "")):
        ele_avg += (float(lo) + float(hi)) / 2.0
    chaos_avg = 0.0
    for lo, hi in re.findall(r"(\d+)\s*-\s*(\d+)", str(props.get("Chaos Damage") or "")):
        chaos_avg += (float(lo) + float(hi)) / 2.0
    avg_hit = round(phys_avg + ele_avg + chaos_avg, 1)
    pdps = round(phys_avg * aps, 1) if aps > 0 else 0.0
    edps = round(ele_avg * aps, 1) if aps > 0 else 0.0
    total_dps = round(avg_hit * aps, 1) if aps > 0 else 0.0
    return {
        "aps": round(aps, 2),
        "crit": round(crit, 2),
        "reloadTime": round(reload_time, 2),
        "spirit": round(spirit, 1),
        "physAvg": round(phys_avg, 1),
        "eleAvg": round(ele_avg, 1),
        "chaosAvg": round(chaos_avg, 1),
        "avgHit": avg_hit,
        "pdps": pdps,
        "edps": edps,
        "dps": total_dps,
    }


def _parse_armour_props(it: dict[str, Any]) -> dict[str, float]:
    props = it.get("properties") or {}
    out: dict[str, float] = {}
    for pkey, ek in (("Ward", "ward"), ("Energy Shield", "es"), ("Evasion", "ev"), ("Armour", "ar"), ("Chance to Block", "block")):
        m = re.search(r"(\d+(?:\.\d+)?)", str(props.get(pkey) or ""))
        if m and float(m.group(1)) > 0:
            out[ek] = round(float(m.group(1)), 1)
    return out


def _probe_delta_pct(base_val: float, after_val: float) -> float:
    if base_val <= 1e-6:
        return 0.0
    return ((after_val - base_val) / base_val) * 100.0


def _build_global_resource_budget(parsed: dict[str, Any], base_sim: dict[str, Any]) -> dict[str, Any]:
    fire = round(_num(base_sim.get("fireRes")), 1)
    cold = round(_num(base_sim.get("coldRes")), 1)
    light = round(_num(base_sim.get("lightningRes")), 1)
    chaos = round(_num(base_sim.get("chaosRes")), 1)
    spirit = round(_num(base_sim.get("spirit")), 1)
    spirit_free = round(_num(base_sim.get("spiritUnreserved")), 1)
    str_v, req_str = int(_num(base_sim.get("str"))), int(_num(base_sim.get("reqStr")))
    dex_v, req_dex = int(_num(base_sim.get("dex"))), int(_num(base_sim.get("reqDex")))
    int_v, req_int = int(_num(base_sim.get("int"))), int(_num(base_sim.get("reqInt")))
    crit = round(_num(base_sim.get("critChance")), 1)

    res_carriers: list[str] = []
    for it in parsed.get("equippedItems") or []:
        slot = str(it.get("slot") or "")
        if str(it.get("rarity") or "").upper() != "UNIQUE" and any(
            k in slot.lower() for k in ("ring", "belt", "boot", "glove", "helmet")
        ):
            res_carriers.append(slot)

    return {
        "resistances": {
            "fire": fire,
            "cold": cold,
            "lightning": light,
            "chaos": chaos,
            "eleDeficit": round(max(0.0, 75.0 - fire) + max(0.0, 75.0 - cold) + max(0.0, 75.0 - light), 1),
            "chaosUncapped": chaos < 50.0,
        },
        "spirit": {
            "total": spirit,
            "unreserved": spirit_free,
            "tight": spirit_free < 15.0 and spirit > 0,
        },
        "attributes": {
            "str": str_v,
            "reqStr": req_str,
            "strMargin": str_v - req_str,
            "dex": dex_v,
            "reqDex": req_dex,
            "dexMargin": dex_v - req_dex,
            "int": int_v,
            "reqInt": req_int,
            "intMargin": int_v - req_int,
        },
        "critChance": crit,
        "critSaturated": crit >= 92.0,
        "recommendedResSlots": res_carriers[:5],
    }


def _extract_item_mod_value(line: str) -> float | None:
    cleaned = re.sub(r"\{[^}]*\}", "", str(line or ""))
    nums = [float(x) for x in re.findall(r"[+-]?\d+(?:\.\d+)?", cleaned)]
    if not nums:
        return None
    if len(nums) == 1:
        return abs(nums[0])
    return round((abs(nums[0]) + abs(nums[1])) / 2.0, 2)


def _match_probe_for_mod_text(text: str, active_probes: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    low = re.sub(r"\{[^}]*\}", "", str(text or "")).strip().lower()
    low = re.sub(r"\s*\((?:implicit|crafted|fractured|desecrated|rune|local)\)\s*", " ", low).strip()
    low = re.sub(r"\s+", " ", low)
    for pid, pdata in active_probes.items():
        for pat in pdata.get("_patterns") or ():
            if re.search(pat, low):
                return pdata
    return None


def _build_slot_functional_specs(
    parsed: dict[str, Any],
    base_sim: dict[str, Any],
    sensitivities: dict[str, dict[str, Any]],
    weapon_panel_audit: dict[str, dict[str, Any]],
    *,
    dossier_items_by_slot: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """把 BD 灵敏度向量与每个装备槽位的全量合法词缀池求交集，生成不拘泥原件词缀的四段式槽位规格。"""
    specs: dict[str, dict[str, Any]] = {}
    dossier_map = dossier_items_by_slot or {}
    active_ws = int((parsed.get("weaponSets") or {}).get("activeWeaponSet") or 1)

    for it in list(parsed.get("equippedItems") or []):
        slot = str(it.get("slot") or "").strip()
        if not slot:
            continue
        sl = slot.lower()
        if sl.startswith(("flask", "charm")):
            continue
        rarity = str(it.get("rarity") or "RARE").upper()
        base_type = str(it.get("baseType") or "").strip()
        name = str(it.get("name") or base_type or slot).strip()
        is_unique = rarity == "UNIQUE"
        cat = trade_url.infer_trade_category(slot, base_type)
        deep = affix_analyzer.analyze_item_deeply(it, parsed)
        d_item = dossier_map.get(sl) or {}
        is_weapon = trade_url._is_weapon_slot(slot)
        is_armour = trade_url._is_armour_slot(slot)
        is_speed_weapon = bool(deep.get("isSpeedWeapon"))
        is_inactive_ws = (active_ws == 1 and "swap" in sl) or (active_ws == 2 and "weapon" in sl and "swap" not in sl)

        # 1. 硬约束：基底锁与基底自带核心技能
        must_lock_base = bool(deep.get("requireBaseType"))
        mandatory_hard: list[dict[str, Any]] = []
        for gs in deep.get("importantGrantedSkills") or []:
            sk_res = trade_url.resolve_granted_skill_to_trade_stat(f"Grants Skill: {gs['skillName']}")
            if sk_res.get("matched") and sk_res.get("statId"):
                mandatory_hard.append({
                    "id": sk_res["statId"],
                    "zh": f"基底技能：{gs['skillName']}",
                    "text": f"Grants Skill: {gs['skillName']}",
                })

        # 2. 面板底线（武器双层模型：5 维 PoB2 实算分型 + Trade2 原生 damage/dps/pdps/edps/aps/crit/reload_time/spirit；防具 es/ev/ar/ward）
        target_panels: dict[str, float] = {}
        panel_role = "affix_driven"
        weapon_archetype: str | None = None
        recommended_bases: list[dict[str, Any]] = []
        panel_equivalence: dict[str, Any] | None = None
        crit_saturated = bool(_build_global_resource_budget(parsed, base_sim).get("critSaturated"))
        base_reason_extra = ""

        if is_weapon:
            wp = _parse_weapon_props(it)
            wa = weapon_panel_audit.get(sl) or {}
            w_base_meta = weapon_bases.get_weapon_base(base_type)
            if wa.get("implicitCore"):
                must_lock_base = True
                base_reason_extra = f"PoB2 实算基底隐含词缀贡献 {wa.get('implicitLossPct', 0):.1f}% DPS，属核心机制底材"

            # 判定武器分型：优先使用 PoB2 5 维实算探针结论，无探针（离线/副武器组）时退回静态规则
            if wa.get("weaponArchetype"):
                weapon_archetype = str(wa["weaponArchetype"])
            elif is_speed_weapon:
                weapon_archetype = "speed_tool"
            elif _SPELL_BASE_RE.search(base_type):
                weapon_archetype = "caster_or_minion"
            elif wp["dps"] >= 120:
                weapon_archetype = "attack_dps"
            else:
                weapon_archetype = "caster_or_minion"

            pref = str(
                wa.get("damagePreference")
                or ("physical" if wp["dps"] > 0 and wp["pdps"] >= wp["dps"] * 0.78 else "any_dps")
            )
            crit_imp = bool(wa.get("critImportant")) if wa else (wp["crit"] >= 8.0)
            reload_imp = bool(wa.get("reloadImportant")) if wa else False
            aps_imp = bool(wa.get("apsImportant")) if wa else (wp["aps"] >= 1.55)

            if weapon_archetype == "speed_tool":
                panel_role = "speed_tool"
                if wp["aps"] >= 1.2:
                    target_panels["aps"] = wp["aps"]
            elif weapon_archetype == "attack_hit_no_aps":
                # 不吃攻速（流血/点燃/重击/冷却/一发装填弩）：严禁用 dps/pdps 绑定攻速，改用 Trade2 原生单发均伤 damage！
                panel_role = "attack_weapon_hit_phys" if pref == "physical" else "attack_weapon_hit"
                if pref == "physical" and wp["physAvg"] >= 55:
                    target_panels["damage"] = wp["physAvg"]
                    # 纯物理单发伤防三元素武器靠高总 damage 混入：用 Trade2 原生 edps 上限过滤
                    target_panels["edps_max"] = max(40.0, round(wp["eleAvg"] * max(1.0, wp["aps"]) + 35.0, 0))
                elif wp["avgHit"] >= 55:
                    target_panels["damage"] = wp["avgHit"]
                if crit_imp and wp["crit"] >= 6.5 and not crit_saturated:
                    target_panels["crit"] = wp["crit"]
                if reload_imp and wp["reloadTime"] > 0:
                    target_panels["reload_time"] = wp["reloadTime"]
            elif weapon_archetype == "attack_dps":
                if pref == "physical" and wp["pdps"] >= 100:
                    panel_role = "attack_weapon_pdps"
                    target_panels["pdps"] = wp["pdps"]
                elif pref == "elemental" and wp["edps"] >= 100:
                    panel_role = "attack_weapon_edps"
                    target_panels["edps"] = wp["edps"]
                elif wp["dps"] >= 100:
                    panel_role = "attack_weapon_dps"
                    target_panels["dps"] = wp["dps"]
                if wp["aps"] >= 1.25 and aps_imp:
                    target_panels["aps"] = wp["aps"]
                if crit_imp and wp["crit"] >= 7.0 and not crit_saturated:
                    target_panels["crit"] = wp["crit"]
                if reload_imp and wp["reloadTime"] > 0:
                    target_panels["reload_time"] = wp["reloadTime"]
            elif weapon_archetype == "stat_stick_weapon":
                # 挂件型攻击武器（如盾牌技能/图腾/法术拿锤或弓只吃 +技能等级）：严禁锁 DPS/单发伤/攻速
                panel_role = "stat_stick_weapon"
            else:
                panel_role = "caster_weapon"
                if "sceptre" in base_type.lower() and wp["spirit"] >= 100:
                    target_panels["spirit"] = wp["spirit"]

            if w_base_meta:
                recommended_bases = weapon_bases.recommend_weapon_bases(
                    base_type,
                    weapon_archetype,
                    damage_preference=pref,
                    crit_important=crit_imp and not crit_saturated,
                    reload_important=reload_imp,
                    top_k=4,
                )
            if wa.get("panelEquivalence"):
                panel_equivalence = dict(wa["panelEquivalence"])
        elif is_armour:
            ap = _parse_armour_props(it)
            df = deep.get("defenseFloor")
            if isinstance(df, dict) and df.get("stat") in ("es", "ev", "ar", "ward") and float(df.get("raw") or 0) > 0:
                target_panels[str(df["stat"])] = round(float(df["raw"]), 1)
                panel_role = f"armour_{df['stat']}"
            else:
                for ek, thresh in (("ward", 40), ("es", 160), ("ev", 320), ("ar", 320)):
                    if ap.get(ek, 0.0) >= thresh:
                        target_panels[ek] = ap[ek]
                        panel_role = f"armour_{ek}"
                        break

        if is_unique:
            specs[slot] = {
                "slot": slot,
                "rarity": "UNIQUE",
                "itemName": name,
                "baseType": base_type,
                "allowedCategory": cat,
                "mustLockBase": False,
                "panelRole": "unique_item",
                "weaponArchetype": weapon_archetype,
                "targetPanels": target_panels,
                "mandatoryHard": mandatory_hard,
                "mandatoryAnyOf": [],
                "offensePool": [],
                "defensePool": [],
                "originalOffenseScore": 0.0,
                "originalDefenseScore": 0.0,
                "inactiveWeaponSet": is_inactive_ws,
                "note": "暗金件不可重做词缀，按预算挑 roll 或决定是否用黄装平替。",
            }
            continue

        # 3. 从 RePoE 全槽位合法词缀池求交集（不拘泥原件词缀！）
        try:
            pool_data = affix_pool.build_slot_pool(slot, ilvl=82, base_type=base_type)
            all_slot_groups = list((pool_data.get("prefixes") or {}).values()) + list((pool_data.get("suffixes") or {}).values())
        except Exception:
            all_slot_groups = []

        orig_mod_lines = [str(m) for m in (it.get("mods") or [])]
        orig_mod_by_probe: dict[str, float] = {}
        orig_ele_res_total = 0.0
        for ml in orig_mod_lines:
            val = _extract_item_mod_value(ml)
            if val is None or val <= 0:
                continue
            if re.search(r"to (fire|cold|lightning) resistance\b", ml, re.I) and "all elemental" not in ml.lower():
                orig_ele_res_total += val
            elif re.search(r"to all elemental resistances", ml, re.I):
                orig_ele_res_total += val * 3.0
            mp = _match_probe_for_mod_text(ml, sensitivities)
            if mp:
                orig_mod_by_probe[mp["id"]] = orig_mod_by_probe.get(mp["id"], 0.0) + val

        offense_pool: list[dict[str, Any]] = []
        defense_pool: list[dict[str, Any]] = []
        seen_sids: set[str] = set()
        skill_lvl_candidates: list[dict[str, Any]] = []

        # 被武器面板底线覆盖的本地物伤/点伤/攻速/暴击率不重复计入显性词缀池（避免双层重复计分）
        skip_weapon_local = bool(
            any(k in target_panels for k in ("dps", "pdps", "edps", "damage"))
            or weapon_archetype in ("attack_dps", "attack_hit_no_aps")
        )
        skip_weapon_crit_local = bool("crit" in target_panels)

        def _consider_probe(pdata: dict[str, Any], sample_en: str = "") -> None:
            pid = pdata["id"]
            dps_u = float(pdata.get("dpsPctPerUnit") or 0.0)
            ehp_u = float(pdata.get("ehpPctPerUnit") or 0.0)
            mana_u = float(pdata.get("manaPctPerUnit") or 0.0)
            is_skill_lvl = bool(pdata.get("isSkillLevel"))

            # 攻击武器已用 equipment_filters 控 DPS/单发伤 时，跳过本地攻速/点伤重复加权（保留技能等级/暴伤/额外伤等非面板词缀）
            if is_weapon and skip_weapon_local and pid in ("attack_speed", "flat_phys_attack", "flat_lightning_attack", "flat_cold_attack", "flat_fire_attack"):
                return
            if is_weapon and weapon_archetype == "attack_hit_no_aps" and pid == "attack_speed":
                return
            if is_weapon and skip_weapon_crit_local and pid == "crit_chance":
                return
            # 防具已用 equipment_filters 控 ES 时，跳过最大护盾显性重复
            if is_armour and "es" in target_panels and pid == "max_es":
                return

            # 计算统一量纲权重（1 分 ≈ +1% 综合强度）
            off_w = dps_u
            # 大法师/魔力体系：魔力对 DPS 也有真实贡献
            if pid == "max_mana" and dps_u >= 0.015:
                off_w = dps_u
            def_w = ehp_u + (mana_u * 0.15 if pid == "max_mana" else 0.0)
            if pid == "spirit":
                def_w = max(def_w, 0.25 if float(base_sim.get("spirit") or 0) > 0 else 0.0)
            if pid == "chaos_res":
                def_w = max(def_w, 0.22 if float(base_sim.get("chaosRes") or 0) < 60 else 0.08)

            if off_w < 0.015 and def_w < 0.015:
                return

            en_tpl = sample_en or str(pdata.get("tradeEn") or pdata.get("probeMod") or "")
            res = trade_url.resolve_affix_to_trade_stat(en_tpl, zh=str(pdata.get("zh") or ""), slot=slot, base_type=base_type)
            if not res.get("matched") or not res.get("statId"):
                return
            sid = str(res["statId"])
            if sid in seen_sids:
                return
            seen_sids.add(sid)

            # 归入攻/防独立加权桶（按主导贡献分桶，防止互相刷分）
            is_off_term = off_w >= max(0.02, def_w * 0.85)
            unit_w = round(max(0.02, min(25.0, off_w if is_off_term else def_w)), 3)
            orig_val = round(orig_mod_by_probe.get(pid, 0.0), 2)
            term = {
                "probeId": pid,
                "statId": sid,
                "en": res.get("statText") or en_tpl,
                "zh": str(pdata.get("zh") or res.get("zh") or en_tpl),
                "weight": unit_w,
                "dpsPctPerUnit": round(dps_u, 4),
                "ehpPctPerUnit": round(ehp_u, 4),
                "onOriginalItem": orig_val > 0,
                "originalVal": orig_val,
                "isSkillLevel": is_skill_lvl,
            }
            if is_off_term:
                offense_pool.append(term)
                if is_skill_lvl and dps_u >= 4.0:
                    skill_lvl_candidates.append(term)
            else:
                defense_pool.append(term)

        # 遍历槽位 RePoE 合法词缀池，自动吸纳所有对本 BD 有效的等价词条！
        matched_probe_ids: set[str] = set()
        for grp in all_slot_groups:
            best_txt = str(grp.get("bestText") or "")
            if not best_txt:
                continue
            mp = _match_probe_for_mod_text(best_txt, sensitivities)
            if mp and mp["id"] not in matched_probe_ids:
                matched_probe_ids.add(mp["id"])
                _consider_probe(mp, sample_en=best_txt)

        # 兜底补充原件已有的高贡献词缀（防止某些特殊/渎神/专属词池不在 RePoE 基础池中）
        for ml in orig_mod_lines:
            mp = _match_probe_for_mod_text(ml, sensitivities)
            if mp and mp["id"] not in matched_probe_ids:
                matched_probe_ids.add(mp["id"])
                _consider_probe(mp, sample_en=ml)

        # 鞋子移速专项（面板 DPS 不显但刷图核心手感）
        if "boot" in sl:
            ms_orig = 0.0
            for ml in orig_mod_lines:
                if re.search(r"movement speed", ml, re.I):
                    ms_orig = _extract_item_mod_value(ml) or 0.0
            ms_res = trade_url.resolve_affix_to_trade_stat("#% increased Movement Speed", zh="移动速度", slot=slot, base_type=base_type)
            if ms_res.get("matched") and ms_res.get("statId") and ms_res["statId"] not in seen_sids:
                seen_sids.add(str(ms_res["statId"]))
                defense_pool.append({
                    "probeId": "movement_speed",
                    "statId": str(ms_res["statId"]),
                    "en": ms_res.get("statText") or "#% increased Movement Speed",
                    "zh": "移动速度%",
                    "weight": 0.45,
                    "dpsPctPerUnit": 0.0,
                    "ehpPctPerUnit": 0.0,
                    "onOriginalItem": ms_orig > 0,
                    "originalVal": ms_orig,
                    "isSkillLevel": False,
                })

        # 通用可互换三元素抗性池（只要该槽位能 roll 抗性：戒指/项链/腰带/防具，统统允许任意三抗+全抗互换）
        if not is_weapon:
            w_res = 0.22 if orig_ele_res_total >= 20 else 0.14
            for rkey, (rsid, rzh, mult) in trade_url._ELE_RES_STAT_IDS.items():
                if rsid in seen_sids:
                    continue
                seen_sids.add(rsid)
                defense_pool.append({
                    "probeId": f"res_{rkey}",
                    "statId": rsid,
                    "en": f"Interchangeable {rkey} resistance",
                    "zh": f"{rzh}（抗性互换池）",
                    "weight": round(w_res * mult, 3),
                    "dpsPctPerUnit": 0.0,
                    "ehpPctPerUnit": round(w_res * mult, 3),
                    "onOriginalItem": orig_ele_res_total >= 20,
                    "originalVal": round(orig_ele_res_total / 3.0, 1) if rkey != "all_ele" else 0.0,
                    "isResPool": True,
                    "isSkillLevel": False,
                })

        # 排序：按每件典型满 roll 预期贡献度降序，保留 Top 12 进加权池
        def _typical_max(pid: str) -> float:
            if pid.startswith("lvl_"):
                return 4.0 if is_weapon else 2.0
            if pid.startswith("extra_"):
                return 30.0
            if pid.startswith("inc_"):
                return 100.0 if is_weapon else 40.0
            if pid in ("max_life", "max_es", "max_mana"):
                return 120.0
            if pid == "accuracy":
                return 400.0
            if pid.startswith("res_"):
                return 40.0
            return 30.0

        offense_pool.sort(key=lambda x: -(x["weight"] * _typical_max(x["probeId"])))
        defense_pool.sort(key=lambda x: (bool(x.get("isResPool")), -(x["weight"] * _typical_max(x["probeId"]))))
        offense_pool = offense_pool[:12]
        defense_pool = defense_pool[:10]

        # 计算原装备在这套统一权重下的基准得分
        orig_off_score = round(sum(t["weight"] * float(t.get("originalVal") or 0.0) for t in offense_pool), 1)
        orig_def_score = round(
            sum(t["weight"] * float(t.get("originalVal") or 0.0) for t in defense_pool if not t.get("isResPool"))
            + (next((t["weight"] for t in defense_pool if t.get("isResPool") and t["probeId"] == "res_fire"), 0.0) * orig_ele_res_total),
            1,
        )

        # 若法系武器/项链原件本身带高价值 +技能等级，提供等价 OR 门槛（全法术 OR 对应单系法术）
        mandatory_any_of: list[dict[str, Any]] = []
        orig_skill_lvl = max((int(t.get("originalVal") or 0) for t in skill_lvl_candidates), default=0)
        if skill_lvl_candidates and orig_skill_lvl >= 2:
            or_min_lvl = max(1, orig_skill_lvl - 1) if orig_skill_lvl >= 3 else orig_skill_lvl
            mandatory_any_of.append({
                "label": f"至少满足 1 条核心技能等级 ≥ +{or_min_lvl}",
                "min": 1,
                "filters": [
                    {
                        "id": t["statId"],
                        "zh": t["zh"],
                        "value": {"min": or_min_lvl},
                    }
                    for t in skill_lvl_candidates[:6]
                ],
            })

        specs[slot] = {
            "slot": slot,
            "rarity": rarity,
            "itemName": name,
            "baseType": base_type,
            "allowedCategory": cat,
            "mustLockBase": must_lock_base,
            "baseTypeReason": str(deep.get("baseTypeReason") or base_reason_extra or ""),
            "panelRole": panel_role,
            "weaponArchetype": weapon_archetype,
            "targetPanels": target_panels,
            "recommendedBases": recommended_bases,
            "panelEquivalence": panel_equivalence,
            "mandatoryHard": mandatory_hard,
            "mandatoryAnyOf": mandatory_any_of,
            "offensePool": offense_pool,
            "defensePool": defense_pool,
            "originalOffenseScore": orig_off_score,
            "originalDefenseScore": orig_def_score,
            "inactiveWeaponSet": is_inactive_ws,
            "dossierRole": str(d_item.get("roleLabel") or ""),
        }

    return specs


def prepare_reverse_probe_jobs(
    norm_xml: str,
    parsed_b: dict[str, Any],
    sim_base: dict[str, Any],
    *,
    include_probes: bool = True,
) -> dict[str, Any]:
    """生成灵敏度倒推所需的全部变体 XML 队列，供独立运行或直接并入全量消融主批次一次性并发调度。"""
    base_dps = _num(sim_base.get("dps"))
    probe_tags = _detect_build_probe_tags(parsed_b, sim_base)
    selected_probes = [p for p in GLOBAL_PROBES if any(t in probe_tags for t in p["tags"])]

    batch_jobs: list[tuple[str, str]] = []  # (key, mutated_xml)
    probe_dirs: dict[str, int] = {}
    probe_deltas: dict[str, float] = {}
    hit_chance = _num(sim_base.get("hitChance"))
    crit_chance = _num(sim_base.get("critChance"))

    if include_probes and base_dps > 0:
        for p in selected_probes:
            pid = p["id"]
            probe_mod = str(p["probeMod"])
            p_dir = 1
            p_delta = float(p["delta"])

            # 满上限属性改用负向微扰（左导数），避免 100% 命中/满暴目标 BD 把刚需命中/暴击标成死词条
            if pid == "accuracy" and hit_chance >= 99.0:
                probe_mod = "-500 to Accuracy Rating"
                p_delta = 500.0
                p_dir = -1
            elif pid == "crit_chance" and crit_chance >= 95.0:
                probe_mod = "40% reduced Critical Hit Chance"
                p_delta = 40.0
                p_dir = -1
            elif pid == "spell_crit_chance" and crit_chance >= 95.0:
                probe_mod = "60% reduced Critical Hit Chance for Spells"
                p_delta = 60.0
                p_dir = -1

            probe_dirs[pid] = p_dir
            probe_deltas[pid] = p_delta
            try:
                mut = xml_mutator.inject_custom_mods(norm_xml, [probe_mod], title=f"Probe_{pid}")
                batch_jobs.append((f"probe:{pid}", mut))
            except Exception:
                pass

    active_ws = int((parsed_b.get("weaponSets") or {}).get("activeWeaponSet") or 1)
    active_w_slot = "Weapon 1" if active_ws == 1 else "Weapon 1 Swap"
    active_w_item = next(
        (it for it in (parsed_b.get("equippedItems") or []) if str(it.get("slot") or "").lower() == active_w_slot.lower()),
        None,
    )
    active_w_implicit_line: str | None = None
    if (
        include_probes
        and base_dps > 0
        and active_w_item is not None
        and str(active_w_item.get("rarity") or "").upper() != "UNIQUE"
        and active_w_item.get("itemId")
    ):
        wid = str(active_w_item["itemId"])
        w_base_str = str(active_w_item.get("baseType") or "")
        is_spell_base = bool(_SPELL_BASE_RE.search(w_base_str))
        if not is_spell_base:
            w_probes: list[tuple[str, str]] = [
                ("w_phys", "Adds 30 to 50 Physical Damage"),
                ("w_ele", "Adds 30 to 50 Lightning Damage"),
                ("w_aps", "15% increased Attack Speed"),
                ("w_crit", "+2% to Critical Hit Chance"),
            ]
            if "crossbow" in w_base_str.lower():
                w_probes.append(("w_reload", "20% increased Reload Speed"))
            for wtag, wmod in w_probes:
                try:
                    batch_jobs.append((f"weapon:{active_w_slot.lower()}:{wtag}", xml_mutator.add_item_mod(norm_xml, wid, wmod)))
                except Exception:
                    pass
        for imp_line in (active_w_item.get("implicits") or []):
            imp_s = str(imp_line or "").strip()
            if imp_s and not imp_s.lower().startswith("grants skill:"):
                active_w_implicit_line = imp_s
                try:
                    batch_jobs.append((
                        f"weapon:{active_w_slot.lower()}:w_no_implicit",
                        xml_mutator.remove_item_mod(norm_xml, wid, imp_s),
                    ))
                except Exception:
                    pass
                break

    return {
        "probeTags": probe_tags,
        "selectedProbes": selected_probes,
        "probeDirs": probe_dirs,
        "probeDeltas": probe_deltas,
        "batchJobs": batch_jobs,
        "activeWeaponSlot": active_w_slot,
        "activeWeaponItem": active_w_item,
        "activeWeaponImplicitLine": active_w_implicit_line,
    }


def assemble_reverse_build_spec(
    parsed_b: dict[str, Any],
    sim_base: dict[str, Any],
    probe_ctx: dict[str, Any],
    sim_results: dict[str, dict[str, Any]],
    *,
    dossier: dict[str, Any] | None = None,
    started_at: float | None = None,
) -> dict[str, Any]:
    """根据已完成的探针模拟结果组装完整【BD 倒推规格包】。"""
    t0 = started_at if started_at is not None else time.time()
    base_dps = _num(sim_base.get("dps"))
    base_ehp = _num(sim_base.get("ehp"))
    base_mana = _num(sim_base.get("mana"))
    probe_tags: set[str] = probe_ctx["probeTags"]
    selected_probes: list[dict[str, Any]] = probe_ctx["selectedProbes"]
    batch_jobs: list[tuple[str, str]] = probe_ctx["batchJobs"]
    active_w_slot: str = probe_ctx["activeWeaponSlot"]
    active_w_item: dict[str, Any] | None = probe_ctx["activeWeaponItem"]
    active_w_implicit_line: str | None = probe_ctx["activeWeaponImplicitLine"]

    # 汇总武器 5 维面板与分型审计
    weapon_panel_audit: dict[str, dict[str, Any]] = {}
    if active_w_item is not None:
        wsl = active_w_slot.lower()
        w_base_str = str(active_w_item.get("baseType") or "")
        wp = _parse_weapon_props(active_w_item)
        r_phys = sim_results.get(f"weapon:{wsl}:w_phys")
        r_ele = sim_results.get(f"weapon:{wsl}:w_ele")
        r_aps = sim_results.get(f"weapon:{wsl}:w_aps")
        r_crit = sim_results.get(f"weapon:{wsl}:w_crit")
        r_reload = sim_results.get(f"weapon:{wsl}:w_reload")
        r_no_imp = sim_results.get(f"weapon:{wsl}:w_no_implicit")
        imp_loss = max(0.0, -_probe_delta_pct(base_dps, _num(r_no_imp.get("dps")))) if r_no_imp else 0.0

        if r_phys and r_ele:
            dp = _probe_delta_pct(base_dps, _num(r_phys.get("dps")))
            de = _probe_delta_pct(base_dps, _num(r_ele.get("dps")))
            da = _probe_delta_pct(base_dps, _num(r_aps.get("dps"))) if r_aps else 0.0
            dc = _probe_delta_pct(base_dps, _num(r_crit.get("dps"))) if r_crit else 0.0
            dr = _probe_delta_pct(base_dps, _num(r_reload.get("dps"))) if r_reload else 0.0
            max_dmg = max(dp, de)

            if dp > 1.0 and dp >= de * 1.45:
                pref = "physical"
            elif de > 1.0 and de >= dp * 1.6:
                pref = "elemental"
            else:
                pref = "any_dps"

            if max_dmg < 1.5 and da >= 3.0:
                archetype = "speed_tool"
            elif max_dmg < 1.5 and da < 1.5 and dc < 2.0:
                archetype = "stat_stick_weapon"
            elif max_dmg >= 1.5 and (da < 2.0 or da < max_dmg * 0.25):
                archetype = "attack_hit_no_aps"
            else:
                archetype = "attack_dps"

            aps_eff = max(0.8, wp["aps"] or 1.2)
            dps_per_100_hit = round((max_dmg / 40.0) * 100.0, 2) if max_dmg > 0 else 0.0
            dps_per_100_dps = round((max_dmg / (40.0 * aps_eff)) * 100.0, 2) if max_dmg > 0 else 0.0
            dps_per_1_crit = round(dc / 2.0, 2) if dc > 0 else 0.0

            weapon_panel_audit[wsl] = {
                "weaponArchetype": archetype,
                "physProbeDpsPct": round(dp, 2),
                "eleProbeDpsPct": round(de, 2),
                "apsProbeDpsPct": round(da, 2),
                "critProbeDpsPct": round(dc, 2),
                "reloadProbeDpsPct": round(dr, 2),
                "implicitLossPct": round(imp_loss, 2),
                "implicitLine": active_w_implicit_line or "",
                "implicitCore": imp_loss >= 4.0,
                "damagePreference": pref,
                "apsImportant": archetype != "attack_hit_no_aps" and da >= 3.5,
                "critImportant": dc >= 3.5,
                "reloadImportant": dr >= 2.5,
                "panelEquivalence": {
                    "dpsPctPer100AvgHit": dps_per_100_hit,
                    "dpsPctPer100WeaponDps": dps_per_100_dps,
                    "dpsPctPer1BaseCrit": dps_per_1_crit,
                },
            }
        elif r_no_imp:
            weapon_panel_audit[wsl] = {
                "weaponArchetype": "caster_or_minion" if _SPELL_BASE_RE.search(w_base_str) else "affix_driven",
                "implicitLossPct": round(imp_loss, 2),
                "implicitLine": active_w_implicit_line or "",
                "implicitCore": imp_loss >= 4.0,
            }

    # 组装全局词缀灵敏度表
    sensitivities: dict[str, dict[str, Any]] = {}
    effective_list: list[dict[str, Any]] = []
    dead_ids: list[str] = []

    probe_dirs: dict[str, int] = probe_ctx.get("probeDirs") or {}
    probe_deltas: dict[str, float] = probe_ctx.get("probeDeltas") or {}

    for p in selected_probes:
        pid = p["id"]
        delta = float(probe_deltas.get(pid) or p["delta"])
        p_dir = int(probe_dirs.get(pid, 1))
        r = sim_results.get(f"probe:{pid}")
        if r is not None:
            ddps = _probe_delta_pct(base_dps, _num(r.get("dps"))) * p_dir
            dehp = _probe_delta_pct(base_ehp, _num(r.get("ehp"))) * p_dir
            dmana = _probe_delta_pct(base_mana, _num(r.get("mana"))) * p_dir
            dps_u = max(0.0, ddps / delta)
            ehp_u = max(0.0, dehp / delta)
            mana_u = max(0.0, dmana / delta)
            tested = True
        else:
            # 无引擎降级估算（离线/测试场景）
            dps_u = 8.0 if p.get("isSkillLevel") else (0.15 if p["bucket"] == "offense" else 0.0)
            ehp_u = 0.06 if p["bucket"] == "defense" else 0.0
            mana_u = 0.05 if pid == "max_mana" else 0.0
            tested = False

        is_dead = tested and dps_u < 0.005 and ehp_u < 0.005 and mana_u < 0.01 and pid not in ("spirit", "chaos_res")
        entry = {
            "id": pid,
            "bucket": p["bucket"],
            "zh": p["zh"],
            "tradeEn": p["tradeEn"],
            "probeMod": p["probeMod"],
            "dpsPctPerUnit": round(dps_u, 4),
            "ehpPctPerUnit": round(ehp_u, 4),
            "manaPctPerUnit": round(mana_u, 4),
            "isSkillLevel": bool(p.get("isSkillLevel")),
            "isDead": is_dead,
            "tested": tested,
            "_patterns": p["patterns"],
        }
        sensitivities[pid] = entry
        if is_dead:
            dead_ids.append(pid)
        else:
            effective_list.append({k: v for k, v in entry.items() if not k.startswith("_")})

    effective_list.sort(
        key=lambda x: -(
            x["dpsPctPerUnit"] * float(probe_deltas.get(x["id"], 20.0))
            + x["ehpPctPerUnit"] * float(probe_deltas.get(x["id"], 15.0))
        )
    )

    dossier_items_by_slot: dict[str, dict[str, Any]] = {}
    if isinstance(dossier, dict):
        for di in list(dossier.get("items") or []) + list(dossier.get("jewels") or []):
            sl = str(di.get("slot") or "").lower()
            if sl:
                dossier_items_by_slot[sl] = di

    slot_specs = _build_slot_functional_specs(
        parsed_b,
        sim_base,
        sensitivities,
        weapon_panel_audit,
        dossier_items_by_slot=dossier_items_by_slot,
    )
    resource_budget = _build_global_resource_budget(parsed_b, sim_base)

    return {
        "version": REVERSE_SPEC_VERSION,
        "elapsedMs": round((time.time() - t0) * 1000.0, 1),
        "probeRuns": len(batch_jobs),
        "probeTags": sorted(probe_tags),
        "baseDps": round(base_dps, 1),
        "baseEhp": round(base_ehp, 1),
        "statSensitivity": effective_list[:28],
        "deadProbeIds": dead_ids,
        "weaponPanelAudit": weapon_panel_audit,
        "globalResourceBudget": resource_budget,
        "slotSpecs": slot_specs,
    }


def compute_reverse_build_spec(
    code_or_xml: str,
    pool: WorkerPool | None = None,
    *,
    base_sim: dict[str, Any] | None = None,
    parsed: dict[str, Any] | None = None,
    dossier: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """运行 PoB2 并行边际灵敏度扫描，生成完整的【BD 倒推规格包】：
    - statSensitivity: 全局词缀真实边际收益（每 1 单位带来多少 %DPS / %EHP）
    - slotSpecs: 每个槽位基于全词缀池倒推的四段式加权搜索蓝图（不拘泥原件词缀）
    - globalResourceBudget: 全身抗性/精魂/三属性红线与饱和状态账本
    """
    t0 = time.time()
    xml = pob_parser.decode_pob(code_or_xml)
    norm_xml, _ = runefix.normalize_runes(xml)
    parsed_b = parsed or pob_parser.parse_pob_xml(norm_xml)

    sim_base = base_sim
    if sim_base is None and pool is not None:
        sim_base = pool.simulate(norm_xml, include_audit=False)
    sim_base = sim_base or {}
    lock_dps_key = sim_base.get("dpsSource")

    probe_ctx = prepare_reverse_probe_jobs(
        norm_xml,
        parsed_b,
        sim_base,
        include_probes=(pool is not None),
    )

    sim_results: dict[str, dict[str, Any]] = {}
    batch_jobs = probe_ctx["batchJobs"]
    if pool is not None and batch_jobs:
        xml_list = [x for _, x in batch_jobs]
        res_list = pool.simulate_batch(xml_list, lock_dps_key=lock_dps_key, include_audit=False)
        for (k, _), r in zip(batch_jobs, res_list):
            if isinstance(r, dict) and not r.get("error"):
                sim_results[k] = r

    return assemble_reverse_build_spec(
        parsed_b,
        sim_base,
        probe_ctx,
        sim_results,
        dossier=dossier,
        started_at=t0,
    )


# ---------------------------------------------------------------------------
# 2. 供 AI 抄作业调用的 3 个 PoB2 沙盒实测工具（Tool Calling 闭环）
# ---------------------------------------------------------------------------
def _summarize_sim_vs_base(base_sim: dict[str, Any], test_sim: dict[str, Any]) -> dict[str, Any]:
    b_dps = max(1e-6, _num(base_sim.get("dps")))
    b_ehp = max(1e-6, _num(base_sim.get("ehp")))
    t_dps = _num(test_sim.get("dps"))
    t_ehp = _num(test_sim.get("ehp"))

    fire = round(_num(test_sim.get("fireRes")), 1)
    cold = round(_num(test_sim.get("coldRes")), 1)
    light = round(_num(test_sim.get("lightningRes")), 1)
    chaos = round(_num(test_sim.get("chaosRes")), 1)
    sp_free = round(_num(test_sim.get("spiritUnreserved")), 1)

    redlines: list[str] = []
    if fire < 75:
        redlines.append(f"火抗不足 ({fire}%)")
    if cold < 75:
        redlines.append(f"冰抗不足 ({cold}%)")
    if light < 75:
        redlines.append(f"电抗不足 ({light}%)")
    if sp_free < 0:
        redlines.append(f"精魂超载 ({sp_free})")
    for attr_name, cur_k, req_k in (("力量", "str", "reqStr"), ("敏捷", "dex", "reqDex"), ("智慧", "int", "reqInt")):
        if _num(test_sim.get(cur_k)) < _num(test_sim.get(req_k)):
            redlines.append(f"{attr_name}不足 ({int(_num(test_sim.get(cur_k)))}/{int(_num(test_sim.get(req_k)))})")

    return {
        "dps": round(t_dps, 1),
        "dpsPctOfTarget": round((t_dps / b_dps) * 100.0, 1),
        "deltaDpsPct": round(((t_dps - b_dps) / b_dps) * 100.0, 1),
        "ehp": round(t_ehp, 1),
        "ehpPctOfTarget": round((t_ehp / b_ehp) * 100.0, 1),
        "deltaEhpPct": round(((t_ehp - b_ehp) / b_ehp) * 100.0, 1),
        "physMaxHit": round(_num(test_sim.get("physMaxHit")), 1),
        "fireRes": fire,
        "coldRes": cold,
        "lightningRes": light,
        "chaosRes": chaos,
        "spiritUnreserved": sp_free,
        "speed": round(_num(test_sim.get("speed")), 2),
        "redlines": redlines,
        "allGreen": len(redlines) == 0,
    }


def sandbox_test_replacement(
    norm_xml: str,
    base_sim: dict[str, Any],
    pool: WorkerPool,
    *,
    slot: str,
    base_type: str,
    mods: list[str],
    rarity: str = "RARE",
    name: str = "Sandbox Candidate",
    implicits: list[str] | None = None,
) -> dict[str, Any]:
    """AI 沙盒工具 1：将指定槽位替换成假设的平替装备，跑 PoB2 实算并返回对比结果与红线。"""
    item_text = xml_mutator.build_synthetic_item_text(
        base_type=base_type,
        rarity=rarity,
        name=name,
        mods=mods,
        implicits=implicits,
    )
    mut = xml_mutator.swap_slot_item(norm_xml, slot, item_text)
    res = pool.simulate(mut, lock_dps_key=base_sim.get("dpsSource"), include_audit=False)
    return {
        "slot": slot,
        "testedBaseType": base_type,
        "testedMods": mods,
        **_summarize_sim_vs_base(base_sim, res),
    }


def sandbox_probe_affixes(
    norm_xml: str,
    base_sim: dict[str, Any],
    pool: WorkerPool,
    *,
    mod_lines: list[str],
) -> dict[str, Any]:
    """AI 沙盒工具 2：现场并行测算 1~6 条自定义/冷门词缀对当前 BD 的真实 DPS/EHP 贡献。"""
    cleaned = [str(m).strip() for m in (mod_lines or []) if str(m).strip()][:6]
    if not cleaned:
        return {"results": []}
    xmls = [xml_mutator.inject_custom_mods(norm_xml, [m], title=f"AiProbe_{i}") for i, m in enumerate(cleaned)]
    sims = pool.simulate_batch(xmls, lock_dps_key=base_sim.get("dpsSource"), include_audit=False)
    out = []
    for mod, s in zip(cleaned, sims):
        if isinstance(s, dict) and not s.get("error"):
            summary = _summarize_sim_vs_base(base_sim, s)
            out.append({
                "mod": mod,
                "deltaDpsPct": summary["deltaDpsPct"],
                "deltaEhpPct": summary["deltaEhpPct"],
                "speed": summary["speed"],
            })
        else:
            out.append({"mod": mod, "error": str((s or {}).get("error") or "sim failed")})
    return {"results": out}


def sandbox_verify_copy_plan(
    norm_xml: str,
    base_sim: dict[str, Any],
    pool: WorkerPool,
    *,
    replacements: list[dict[str, Any]],
) -> dict[str, Any]:
    """AI 沙盒工具 3：将整套抄作业平替装备一次性替换进 PoB2 跑全套终稿实算验收。"""
    mut = xml_mutator.replace_slots_batch(norm_xml, replacements)
    res = pool.simulate(mut, lock_dps_key=base_sim.get("dpsSource"), include_audit=False)
    return {
        "replacedSlots": [str(r.get("slot") or "") for r in (replacements or []) if isinstance(r, dict) and r.get("slot")],
        **_summarize_sim_vs_base(base_sim, res),
    }
