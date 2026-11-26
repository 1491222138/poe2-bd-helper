"""基于 PoB2 常驻引擎的单 BD 事实采集与单件消融贡献计算（零静态正则猜测）。

职责：
  1. 直接读取 PoB2 LuaJIT 引擎的实算全貌（主技能真实名称/等级、全技能组 DPS、图腾上限、
     未保留血量、双武器组面板对比、精魂审计、PoB2 未解析词条清单）；
  2. 利用常驻 WorkerPool 并发对每件装备与每颗珠宝跑“脱装消融”，计算真实的
     ΔDPS% / ΔEHP% / ΔMana% / ΔSpirit / 抗性损失，产出版本无关的客观重要度排序。
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from typing import Any

from backend.domain.common import (
    TRAVEL_SKILL_RE as _TRAVEL_SKILL_NAME_RE,
    is_inactive_weapon_slot as _is_inactive_weapon_slot,
    safe_num as _num,
)
from backend.domain.common import pct_loss as _common_pct_loss
from backend.engine import xml_mutator

_BENCHMARKS_CACHE: dict[str, Any] | None = None
_BENCHMARKS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "endgame_benchmarks.json",
)

_MANA_ON_KILL_PCT_RE = re.compile(
    r"(?:recover|gain)\s+(\d+(?:\.\d+)?)%\s+of\s+(?:maximum\s+)?mana\s+on\s+kill",
    re.I,
)
_LIFE_ON_KILL_PCT_RE = re.compile(
    r"(?:recover|gain)\s+(\d+(?:\.\d+)?)%\s+of\s+(?:maximum\s+)?life\s+on\s+kill",
    re.I,
)
_ARMOUR_TO_ELE_RE = re.compile(r"armour\s+also\s+applies\s+to\s+elemental\s+damage", re.I)


def load_endgame_benchmarks() -> dict[str, Any]:
    """加载外置终局靶标配置（backend/data/endgame_benchmarks.json），方便跨版本零代码校准。"""
    global _BENCHMARKS_CACHE
    if _BENCHMARKS_CACHE is not None:
        return _BENCHMARKS_CACHE
    fallback: dict[str, Any] = {
        "patch": "0.5.5",
        "targets": {
            "t15_rare_solo": {"label": "单人 T15/T16 稀有金怪", "hp": 350_000, "comfortTtkSec": 1.5, "capTtkSec": 0.5},
            "divinity_arbiter_solo": {"label": "单人神性仲裁者", "hp": 15_000_000, "comfortTtkSec": 8.0, "capTtkSec": 2.0},
            "uber_arbiter_solo": {"label": "单人 Uber 灰烬仲裁者", "hp": 22_600_000, "comfortTtkSec": 10.0, "capTtkSec": 2.0},
            "t4_xesht_solo": {"label": "单人 T4 满难泽什特", "hp": 32_000_000, "comfortTtkSec": 12.0, "capTtkSec": 2.5},
            "party6_200deli_rare": {"label": "6人 200% 迷雾稀有金怪", "hp": 5_250_000, "comfortTtkSec": 2.5, "capTtkSec": 1.0},
            "party6_uber_boss": {"label": "6人满队 Uber 巅峰 Boss", "hp": 135_600_000, "comfortTtkSec": 6.0, "capTtkSec": 3.0},
        },
        "defenseCaps": {
            "deflectionRatingCap": 28800,
            "deflectionRatingCapBlinded": 23050,
            "deflectionMaxChancePct": 95,
            "deflectionDamageReductionPct": 40,
            "ciRecommendedMinEs": 5000,
            "tankPhysMaxHitCap": 22000,
            "tankEleMaxHitCap": 45000,
        },
        "manaSustain": {
            "denseMapKillsPerSec": 6.0,
            "defaultCadenceSetupUses": 3,
            "defaultCadenceCycleSec": 1.5,
            "defaultManaFlaskTotalRecovery": 2400,
            "bossSafetyMarginMult": 1.5,
        },
    }
    try:
        if os.path.isfile(_BENCHMARKS_PATH):
            with open(_BENCHMARKS_PATH, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                _BENCHMARKS_CACHE = loaded
                return _BENCHMARKS_CACHE
    except Exception:
        pass
    _BENCHMARKS_CACHE = fallback
    return _BENCHMARKS_CACHE


def _pct_loss(base: float, after: float) -> float:
    return _common_pct_loss(base, after, ndigits=1)


def ablate_build_items(
    norm_xml: str,
    parsed: dict[str, Any],
    base_sim: dict[str, Any],
    pool: Any,
) -> tuple[list[dict[str, Any]], int]:
    """并发脱掉每件装备与每颗珠宝，测量它们在 PoB2 引擎中的真实贡献度。"""
    t0 = time.time()
    unparsed_slots = {
        str(u.get("slot") or "")
        for u in (base_sim.get("unparsedMods") or [])
        if isinstance(u, dict)
    }
    has_dance_with_death = any(
        (k.get("name") or "").lower() == "dance with death"
        for k in ((parsed.get("passiveTree") or {}).get("keystones") or [])
    )
    active_ws = int(_num(base_sim.get("activeWeaponSet"), 1))
    lock_key = str(base_sim.get("dpsSource") or "") or None

    targets: list[dict[str, Any]] = []
    xml_batch: list[str] = []

    for it in parsed.get("equippedItems") or []:
        slot = it.get("slot") or ""
        if not slot:
            continue
        try:
            ablated_xml = xml_mutator.ablate_slot(norm_xml, slot)
        except Exception:
            continue
        targets.append({"kind": "item", "item": it})
        xml_batch.append(ablated_xml)

    for j in parsed.get("jewels") or []:
        slot = j.get("slot") or ""
        nid = str(j.get("nodeId") or "").strip()
        try:
            if nid:
                ablated_xml = xml_mutator.ablate_tree_jewel(norm_xml, nid)
            else:
                ablated_xml = xml_mutator.ablate_slot(norm_xml, slot)
        except Exception:
            continue
        targets.append({"kind": "jewel", "item": j})
        xml_batch.append(ablated_xml)

    if not xml_batch:
        return [], 0

    sim_results = pool.simulate_batch(xml_batch, lock_dps_key=lock_key, include_audit=False)

    base_dps = _num(base_sim.get("dps"))
    base_ehp = _num(base_sim.get("ehp"))
    base_mana = _num(base_sim.get("mana"))
    base_spirit = _num(base_sim.get("spirit"))
    base_res = {
        "fire": _num(base_sim.get("fireRes")),
        "cold": _num(base_sim.get("coldRes")),
        "lightning": _num(base_sim.get("lightningRes")),
        "chaos": _num(base_sim.get("chaosRes")),
    }
    base_attr_ok = (
        _num(base_sim.get("str")) >= _num(base_sim.get("reqStr"))
        and _num(base_sim.get("dex")) >= _num(base_sim.get("reqDex"))
        and _num(base_sim.get("int")) >= _num(base_sim.get("reqInt"))
    )

    impacts: list[dict[str, Any]] = []
    for meta, res in zip(targets, sim_results):
        it = meta["item"]
        slot = it.get("slot") or ""
        name = it.get("name") or it.get("baseType") or ""
        dps_loss = _pct_loss(base_dps, _num(res.get("dps")))
        ehp_loss = _pct_loss(base_ehp, _num(res.get("ehp")))
        mana_loss = _pct_loss(base_mana, _num(res.get("mana")))
        spirit_loss = round(base_spirit - _num(res.get("spirit")), 1)
        res_drops = [
            base_res["fire"] - _num(res.get("fireRes")),
            base_res["cold"] - _num(res.get("coldRes")),
            base_res["lightning"] - _num(res.get("lightningRes")),
            base_res["chaos"] - _num(res.get("chaosRes")),
        ]
        max_res_drop = round(max([0.0] + res_drops), 1)
        attr_broken = base_attr_ok and (
            _num(res.get("str")) < _num(res.get("reqStr"))
            or _num(res.get("dex")) < _num(res.get("reqDex"))
            or _num(res.get("int")) < _num(res.get("reqInt"))
        )

        inactive_ws = _is_inactive_weapon_slot(slot, active_ws)
        has_unparsed = slot in unparsed_slots

        # 客观贡献分：全由 PoB2 引擎脱装实算差值驱动，不依赖静态词缀/暗金词典
        score = (
            max(0.0, dps_loss) * 1.6
            + max(0.0, ehp_loss) * 1.1
            + max(0.0, mana_loss) * 0.55
            + max(0.0, spirit_loss) * 0.35
            + max(0.0, max_res_drop) * 0.35
            + (12.0 if attr_broken else 0.0)
        )

        notes: list[str] = []
        if inactive_ws:
            notes.append("位于副武器组（切换武器组时生效）")
        if has_dance_with_death and slot.lower() in ("weapon 2", "weapon 2 swap") and dps_loss < 0:
            notes.append("脱掉副手会意外触发「与死共舞」(+25% more 攻速)，面板 ΔDPS 为负不代表副手是负提升")
        if has_unparsed:
            notes.append("含 PoB2 未实算词条（实战价值高于纯面板差值）")
        if attr_broken:
            notes.append("承担关键属性门槛")

        impacts.append({
            "slot": slot,
            "kind": meta["kind"],
            "name": name,
            "baseType": it.get("baseType") or "",
            "rarity": (it.get("rarity") or "").upper(),
            "dpsLossPct": dps_loss,
            "ehpLossPct": ehp_loss,
            "manaLossPct": mana_loss,
            "spiritLoss": spirit_loss,
            "maxResDrop": max_res_drop,
            "attrBroken": attr_broken,
            "inactiveWeaponSet": inactive_ws,
            "hasUnparsedMods": has_unparsed,
            "impactScore": round(score, 1),
            "note": "；".join(notes),
        })

    impacts.sort(key=lambda x: x["impactScore"], reverse=True)
    return impacts, int((time.time() - t0) * 1000)


def _compact_skill_mechanics_row(sk: dict[str, Any], walk_ms: float) -> dict[str, Any]:
    """把 PoB2 单技能实算行整理为紧凑且完整的机制画像（阶段/持续/投速/投射物/范围/印记/冷却/攻速/DPS）。"""
    row: dict[str, Any] = {
        "name": str(sk.get("name") or ""),
        "level": int(_num(sk.get("level"), 0)),
        "isMain": bool(sk.get("isMain")),
        "isTravel": bool(sk.get("isTravel")),
        "weaponSet": int(_num(sk.get("weaponSet"), 1)),
    }
    if sk.get("includeInFullDPS"):
        row["includeInFullDPS"] = True
    g_cnt = int(_num(sk.get("gemCount"), 1))
    if g_cnt > 1:
        row["gemCount"] = g_cnt
    if sk.get("skillPartName"):
        row["skillPartName"] = str(sk["skillPartName"])
    avail_parts = sk.get("availableParts")
    if isinstance(avail_parts, list) and len(avail_parts) > 1:
        row["availableParts"] = [str(p) for p in avail_parts[:6]]
    st_cnt = int(_num(sk.get("skillStageCount"), 0))
    st_max = int(_num(sk.get("skillStageMax"), 0))
    if st_cnt > 0 or st_max > 0:
        row["skillStageCount"] = st_cnt
        if st_max > 0:
            row["skillStageMax"] = st_max

    dps = round(_num(sk.get("dps")), 1)
    if dps > 0:
        row["dps"] = dps
        row["dpsSource"] = str(sk.get("dpsSource") or "")
    avg_hit = round(_num(sk.get("avgHit")), 1)
    if avg_hit > 0:
        row["avgHit"] = avg_hit
    avg_burst = round(_num(sk.get("avgBurstDamage")), 1)
    if avg_burst > 0:
        row["avgBurstDamage"] = avg_burst

    spd = round(_num(sk.get("speed")), 2)
    spd1 = round(_num(sk.get("speedWs1")), 2)
    spd2 = round(_num(sk.get("speedWs2")), 2)
    if spd > 0:
        row["usesPerSec"] = spd
    if spd1 > 0 or spd2 > 0:
        row["speedWs1"] = spd1
        row["speedWs2"] = spd2
    cast_t = round(_num(sk.get("castTime")), 3)
    if cast_t > 0:
        row["castTimeSec"] = cast_t

    cd = round(_num(sk.get("cooldown")), 2)
    cd1 = round(_num(sk.get("cooldownWs1")), 2)
    cd2 = round(_num(sk.get("cooldownWs2")), 2)
    if cd > 0 or cd1 > 0 or cd2 > 0:
        row["cooldownSec"] = cd
        if cd1 > 0 or cd2 > 0:
            row["cooldownWs1"] = cd1
            row["cooldownWs2"] = cd2
    trig = round(_num(sk.get("triggerRate")), 2)
    if trig > 0:
        row["triggerRate"] = trig

    dur = round(_num(sk.get("duration")), 2)
    dur_mod = round(_num(sk.get("durationMod")), 2)
    dur_sec = round(_num(sk.get("durationSecondary")), 2)
    if dur > 0:
        row["durationSec"] = dur
    if dur_mod > 0 and abs(dur_mod - 1.0) >= 0.02:
        row["durationMod"] = dur_mod
    if dur_sec > 0:
        row["durationSecondarySec"] = dur_sec

    ps_mod = round(_num(sk.get("projSpeedMod")), 2)
    p_cnt = int(_num(sk.get("projCount"), 0))
    if ps_mod > 0:
        row["projSpeedMod"] = ps_mod
    if p_cnt > 0:
        row["projCount"] = p_cnt
    for src_k, dst_k in (
        ("pierceCount", "pierceCount"),
        ("chainCount", "chainCount"),
        ("terrainChainChance", "terrainChainChance"),
        ("bounceCount", "bounceCount"),
        ("forkCount", "forkCount"),
        ("splitCount", "splitCount"),
        ("repeatCount", "repeatCount"),
        ("totemLimit", "totemLimit"),
        ("minionLimit", "minionLimit"),
    ):
        v = int(_num(sk.get(src_k), 0))
        if v > 0:
            row[dst_k] = v
    if sk.get("pierceAll"):
        row["pierceAll"] = True
    if sk.get("bouncesOffTerrain"):
        row["bouncesOffTerrain"] = True
    hit_iv = round(_num(sk.get("hitIntervalSec")), 2)
    if hit_iv > 0:
        row["hitIntervalSec"] = hit_iv

    aoe_r = round(_num(sk.get("aoeRadius")), 2)
    aoe_m = round(_num(sk.get("aoeMod")), 2)
    if aoe_r > 0:
        row["aoeRadiusMetres"] = aoe_r
    if aoe_m > 0 and abs(aoe_m - 1.0) >= 0.02:
        row["aoeMod"] = aoe_m

    seal_max = int(_num(sk.get("sealMax"), 0))
    if seal_max > 0:
        row["sealMax"] = seal_max
        row["sealCooldownSec"] = round(_num(sk.get("sealCooldown")), 2)
        row["timeMaxSealsSec"] = round(_num(sk.get("timeMaxSeals")), 2)

    sk_use_ms = round(_num(sk.get("skillMoveSpeed"), walk_ms), 1)
    if row["isTravel"] or abs(sk_use_ms - walk_ms) >= 5.0:
        row["skillMoveSpeedPct"] = sk_use_ms

    mc = round(_num(sk.get("manaCost")), 1)
    if mc > 0:
        row["manaCost"] = mc
    mps = round(_num(sk.get("manaPerSecCost")), 1)
    if mps > 0:
        row["manaPerSecCost"] = mps

    if sk.get("archetype"):
        row["archetype"] = str(sk["archetype"])
    if sk.get("isShapeshift"):
        row["isShapeshift"] = True
    st_ramp = round(_num(sk.get("stageRampSec")), 2)
    if st_ramp > 0:
        row["stageRampSec"] = st_ramp

    for src_k, dst_k, dec in (
        ("magazineSize", "magazineSize", 0),
        ("reloadTime", "reloadTimeSec", 2),
        ("reloadSpeedMod", "reloadSpeedMod", 2),
        ("fuseTime", "fuseTimeSec", 2),
        ("aftershockChance", "aftershockChance", 1),
        ("exertedAttacks", "exertedAttacks", 0),
        ("totemPlacementTime", "totemPlacementTimeSec", 2),
        ("totemLife", "totemLife", 0),
        ("minionLife", "minionLife", 0),
        ("minionDps", "minionDps", 1),
        ("minionMoveSpeed", "minionMoveSpeedPct", 1),
        ("spiritCost", "spiritCost", 0),
        ("metaEnergyGainMod", "metaEnergyGainMod", 2),
        ("maxEnergy", "maxEnergy", 0),
        ("dotSharePct", "dotSharePct", 1),
        ("poisonStacks", "poisonStacks", 1),
        ("poisonStackCap", "poisonStackCap", 0),
    ):
        val = _num(sk.get(src_k), 0.0)
        if val > 0:
            row[dst_k] = int(round(val)) if dec == 0 else round(val, dec)
    return row


def _collect_build_context(
    parsed: dict[str, Any] | None,
    key_skills: list[dict[str, Any]],
) -> dict[str, Any]:
    """从 PoB2 已解析技能组、装备、珠宝、药剂、天赋节点中提取通用机制信号（零写死单一 BD）。"""
    all_gems_low: set[str] = set()
    main_supports_low: set[str] = set()
    main_gem_quality = 0
    gem_quality_map: dict[str, int] = {}
    all_items_low: set[str] = set()
    all_nodes_low: set[str] = set()
    all_mod_lines: list[str] = []

    for sk in key_skills:
        nm = str(sk.get("name") or "").strip().lower()
        if nm:
            all_gems_low.add(nm)

    if isinstance(parsed, dict):
        for sg in parsed.get("skillGroups") or []:
            gems = sg.get("gems") or []
            is_main = bool(sg.get("isMain"))
            for idx, g in enumerate(gems):
                nm = str(g.get("name") or "").strip().lower()
                if not nm:
                    continue
                all_gems_low.add(nm)
                q = int(_num(g.get("quality"), 0))
                if q > gem_quality_map.get(nm, 0):
                    gem_quality_map[nm] = q
                if is_main:
                    if idx == 0:
                        main_gem_quality = max(main_gem_quality, q)
                    else:
                        main_supports_low.add(nm)

        for bucket in ("equippedItems", "jewels", "flasksAndCharms"):
            for it in parsed.get(bucket) or []:
                for k in ("name", "baseType"):
                    v = str(it.get(k) or "").strip().lower()
                    if v:
                        all_items_low.add(v)
                for m in it.get("mods") or []:
                    if isinstance(m, str) and m.strip():
                        all_mod_lines.append(m.strip())

        tree = parsed.get("passiveTree") or {}
        for bucket in ("keystones", "ascendancyNodes", "notables", "weaponSet1Notables", "weaponSet2Notables"):
            for n in tree.get(bucket) or []:
                nm = str(n.get("name") or "").strip().lower() if isinstance(n, dict) else str(n or "").strip().lower()
                if nm:
                    all_nodes_low.add(nm)
                if isinstance(n, dict):
                    for st in n.get("stats") or []:
                        if isinstance(st, str) and st.strip():
                            all_mod_lines.append(st.strip())
        for an in tree.get("anointedNotables") or []:
            nm = str(an or "").strip().lower()
            if nm:
                all_nodes_low.add(nm)

    mana_on_kill_pct = 0.0
    life_on_kill_pct = 0.0
    armour_to_ele = False
    for line in all_mod_lines:
        m_mok = _MANA_ON_KILL_PCT_RE.search(line)
        if m_mok:
            mana_on_kill_pct += _num(m_mok.group(1), 0.0)
        m_lok = _LIFE_ON_KILL_PCT_RE.search(line)
        if m_lok:
            life_on_kill_pct += _num(m_lok.group(1), 0.0)
        if _ARMOUR_TO_ELE_RE.search(line):
            armour_to_ele = True

    has_mana_flask = any(
        ("mana flask" in nm or "lavianga" in nm or "melting maelstrom" in nm or "uhtred's chalice" in nm)
        for nm in all_items_low
    )
    has_special_mana_flask = any(
        ("lavianga" in nm or "melting maelstrom" in nm or "uhtred's chalice" in nm)
        for nm in all_items_low
    )
    return {
        "allGemsLow": all_gems_low,
        "mainSupportsLow": main_supports_low,
        "mainGemQuality": main_gem_quality,
        "gemQualityMap": gem_quality_map,
        "allItemsLow": all_items_low,
        "allNodesLow": all_nodes_low,
        "manaOnKillPct": round(mana_on_kill_pct, 2),
        "lifeOnKillPct": round(life_on_kill_pct, 2),
        "armourAppliesToElemental": armour_to_ele,
        "hasManaFlask": has_mana_flask,
        "hasSpecialManaFlask": has_special_mana_flask,
    }


def _compute_multi_hit_and_ttk(
    raw_sim: dict[str, Any],
    main_mech: dict[str, Any],
    key_skills: list[dict[str, Any]],
    ctx: dict[str, Any],
    benchmarks: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """基于 PoB2 实算数值推导：原型分类、单发 vs 复数击中/连招有效 DPS、以及全终局/6人靶标 TTK。"""
    main_name = str(main_mech.get("name") or "")
    main_low = main_name.lower()
    arch = str(main_mech.get("archetype") or "")
    all_gems = ctx["allGemsLow"]
    main_sups = ctx["mainSupportsLow"]
    all_items = ctx["allItemsLow"]

    eff_dps = _num(raw_sim.get("dps"))
    comb_dps = _num(raw_sim.get("combinedDps")) or eff_dps
    full_dps = _num(raw_sim.get("fullDps"))
    avg_hit = _num(raw_sim.get("avgHit"))
    speed = _num(raw_sim.get("speed"))
    gem_count = int(_num(main_mech.get("gemCount"), 1))
    duration = _num(main_mech.get("durationSec"))
    hit_interval = _num(main_mech.get("hitIntervalSec"))
    proj_speed = _num(main_mech.get("projSpeedMod"))
    proj_count = int(_num(main_mech.get("projCount"), 0))
    seal_max = int(_num(main_mech.get("sealMax"), 0))
    bounces = bool(main_mech.get("bouncesOffTerrain"))
    totems = int(_num(main_mech.get("activeTotemLimit"), 0))
    minions = int(_num(main_mech.get("activeMinionLimit"), 0))
    frenzy_max = int(_num(raw_sim.get("frenzyChargesMax"), 0))
    stage_cnt = int(_num(main_mech.get("skillStageCount"), 0))
    stage_max = int(_num(main_mech.get("skillStageMax"), 0))
    enemy_boss = str(raw_sim.get("enemyIsBoss") or "None")

    has_barrage = "barrage" in all_gems
    has_whirling = "whirling slash" in all_gems
    has_salvo = ("salvo" in main_sups) or (seal_max > 0)
    has_rakiata = "rakiata's flow" in main_sups or "rakiata's flow" in all_gems
    has_taming = any("the taming" in x for x in all_items)
    has_temporalis = any("temporalis" in x for x in all_items)
    has_archmage = "archmage" in all_gems
    has_return = any("return" in s for s in main_sups)
    has_tempest_bell = "tempest bell" in all_gems
    has_lightning_rod = "lightning rod" in all_gems

    # 1. 五大通用原型分类（不写死特定 BD 名称，从技能/机制组合推导）
    if arch == "trigger_meta" or any(g.startswith("cast on ") for g in all_gems) and main_mech.get("triggerRate", 0) > 0:
        cadence_archetype = "TRIGGER_ENERGY"
    elif arch in ("totem", "minion") or totems > 1 or minions > 1:
        cadence_archetype = "AUTONOMOUS_MULTI"
    elif "flicker strike" in main_low or (has_temporalis and "blink" in all_gems):
        cadence_archetype = "MOBILITY_CHAIN"
    elif arch == "dot_ailment" or "gas grenade" in main_low or "gas arrow" in main_low:
        cadence_archetype = "DOT_BYPASS"
    elif (
        has_barrage
        or has_salvo
        or (has_whirling and "twister" in main_low)
        or stage_max >= 3
        or _num(main_mech.get("cooldownSec")) >= 2.0
    ):
        cadence_archetype = "CADENCE_BURST"
    else:
        cadence_archetype = "SUSTAINED_DIRECT"

    # 2. 数学判定 PoB2 面板是否仅算了「单发单次命中」
    pob_single_hit_only = bool(int(_num(raw_sim.get("pobSingleHitOnly"), 0)) > 0)
    if not pob_single_hit_only and gem_count <= 1 and avg_hit > 0 and speed > 0 and comb_dps > 0:
        if abs(comb_dps - avg_hit * speed) / comb_dps < 0.18:
            pob_single_hit_only = True
    already_scaled_in_pob = (gem_count > 1) or (full_dps > comb_dps * 1.5 and full_dps > 0)

    # 3. 基于 PoB2 实算参数透明推导复数击中/连招有效 DPS 区间（开阔态 vs 靠墙/满连招态）
    amplifiers: list[str] = []
    mult_open = 1.0
    mult_wall_or_full = 1.0
    base_for_mult = eff_dps if eff_dps > 0 else comb_dps

    if arch == "roaming_bouncing_proj" or (bounces and duration > 0 and hit_interval > 0):
        # 0.66s 同组再击中冷却模型（Twister / Spark / Ball Lightning 等）
        eff_dur = duration if duration > 0 else 3.0
        eff_iv = hit_interval if hit_interval > 0 else 0.66
        hits_per_wave = max(1, min(15, int(math.floor(eff_dur / eff_iv)) + 1))

        # 独立波次（Barrage 每次重复拥有独立 0.66s 计时器，重复伤 50%）
        wave_dmg_mult = 1.0
        if has_barrage and ("twister" in main_low or arch in ("roaming_bouncing_proj", "direct_proj")):
            extra_repeats = 2 + max(0, frenzy_max)
            wave_dmg_mult = 1.0 + 0.5 * extra_repeats
            amplifiers.append(
                f"Barrage 独立重复波次: 基础2+{frenzy_max}狂怒球={extra_repeats}次独立0.66s计时器(波次倍率×{wave_dmg_mult:.1f})"
            )

        # Salvo 印记增加同波投射物密度（提升命中覆盖率）
        eff_proj_density = max(1, proj_count) + (seal_max if has_salvo else 0)
        if has_salvo and seal_max > 0:
            amplifiers.append(f"Salvo 满印记 +{seal_max} 发投射物，显著拉满开阔与靠墙碰撞覆盖率")

        # 旋风消耗 Whirling Slash 阶段（每阶 +80% More 伤并额外生成旋风）
        whirl_dmg_mult = 1.0
        if "twister" in main_low and has_whirling:
            if stage_cnt <= 0:
                whirl_dmg_mult = 3.4  # 3阶旋风消耗：+240% More 伤（PoB未勾选阶段时的实战增幅）
                amplifiers.append("Whirling Slash 3阶旋风消耗: 额外多发龙卷风且每阶+80% More伤(×3.4，PoB未勾阶段)")
            else:
                amplifiers.append(f"Whirling Slash 阶段已计入(stage={stage_cnt})并额外生成多发龙卷风")

        # 开阔态 vs 靠墙弹射态命中覆盖率
        open_cov = 0.45
        if eff_proj_density >= 6:
            open_cov = 0.65
        if proj_speed >= 1.5:
            open_cov = min(0.78, open_cov + 0.12)
        wall_cov = 0.92 if bounces else 0.75

        if pob_single_hit_only and not already_scaled_in_pob:
            # 节拍爆发下每轮耗时约 1.5s，若 PoB2 用的是单发 avgHit × speed，则按复数击中折算真实有效输出
            mult_open = max(1.0, hits_per_wave * wave_dmg_mult * whirl_dmg_mult * open_cov)
            mult_wall_or_full = max(mult_open, hits_per_wave * wave_dmg_mult * whirl_dmg_mult * wall_cov)
            amplifiers.append(
                f"PoB2 仅算单发1次命中({base_for_mult/1e6:.2f}M)，按持续{eff_dur:.1f}s/{eff_iv:.2f}s冷却={hits_per_wave}段/波"
                + ("+墙体弹射" if bounces else "")
                + f"，开阔态约×{mult_open:.1f} / 靠墙态约×{mult_wall_or_full:.1f}"
            )
        else:
            # PoB2 已通过 FullDPS 或 gemCount 计入多段，仅做温和开阔/靠墙提示不重复暴涨
            amplifiers.append(
                f"PoB2 已含多段计入(gemCount={gem_count}, dpsSource={raw_sim.get('dpsSource')})；"
                f"墙体弹射靠墙态覆盖~{int(wall_cov*100)}%，开阔态~{int(open_cov*100)}%"
            )
            mult_open = round(open_cov / 0.85, 2) if open_cov < 0.85 else 1.0
            mult_wall_or_full = 1.0

    elif cadence_archetype == "AUTONOMOUS_MULTI":
        if totems > 1 and str(raw_sim.get("dpsSource") or "") != "FullDPS":
            mult_open = float(totems)
            mult_wall_or_full = float(totems)
            amplifiers.append(f"图腾上限 {totems} 根（单根 DPS × {totems}）")
        elif minions > 1 and str(raw_sim.get("dpsSource") or "") != "FullDPS":
            mult_open = float(minions)
            mult_wall_or_full = float(minions)
            amplifiers.append(f"召唤物上限 {minions} 只（单体 × {minions}）")

    elif arch == "crossbow_grenade":
        mag = int(_num(main_mech.get("magazineSize"), 0))
        rel = _num(main_mech.get("reloadTimeSec"))
        if mag > 0 and rel > 0 and speed > 0 and avg_hit > 0:
            cycle_t = max(0.05, (mag - 1) / speed + rel)
            sustained_xbow = (avg_hit * mag) / cycle_t
            if base_for_mult > 0:
                ratio = max(0.2, min(2.0, sustained_xbow / base_for_mult))
                mult_open = ratio
                mult_wall_or_full = ratio
            amplifiers.append(f"弩箭装填周期实算: 弹匣{mag}发 + 装填{rel:.2f}s → 持续周期 {cycle_t:.2f}s")

    # 额外通用联动提示（不强行暴涨已经很高的面板，只作为透明机制说明）
    if has_rakiata:
        amplifiers.append("Rakiata's Flow 反转敌人元素抗性（对45%~75%抗性顶峰Boss实战额外增伤显著；严禁同时叠曝晒/降抗诅咒）")
    if has_taming and "twister" in main_low:
        amplifiers.append("The Taming 常驻三元素地面赋能（配合元素旋风稳定吃满额外伤）")
    if has_return:
        amplifiers.append("投射物返还（优先级最低，单体上限 2.0x）")
    if has_tempest_bell:
        amplifiers.append("Tempest Bell 钟铃共鸣（每0.3s震荡波+异常额外元素伤，大幅放大单体爆发）")
    if has_lightning_rod:
        amplifiers.append("Lightning Rod 避雷针连锁二次引爆（单体爆发远高于箭矢直击面板）")
    if has_archmage and totems > 0:
        amplifiers.append("Spell Totem + Archmage：图腾自动施法免除 Archmage 蓝耗惩罚")

    eff_dps_open = round(base_for_mult * mult_open, 1)
    eff_dps_wall = round(base_for_mult * mult_wall_or_full, 1)
    # 综合实战代表 DPS（取开阔与靠墙/满连招的几何折中，单发面板若未膨胀则等于原 DPS）
    eff_dps_rep = round(math.sqrt(max(1.0, eff_dps_open) * max(1.0, eff_dps_wall)), 1) if eff_dps_wall > eff_dps_open * 1.1 else eff_dps_wall

    # 4. 计算各终局靶标 TTK（单人 + 6人200%迷雾 + 6人Uber巅峰）
    targets_cfg = benchmarks.get("targets") or {}
    ttk_map: dict[str, Any] = {}
    for tid, tcfg in targets_cfg.items():
        hp = _num(tcfg.get("hp"))
        if hp <= 0:
            continue
        ttk_raw = round(hp / base_for_mult, 2) if base_for_mult > 0 else 999.0
        ttk_eff = round(hp / eff_dps_rep, 2) if eff_dps_rep > 0 else 999.0
        ttk_wall = round(hp / eff_dps_wall, 2) if eff_dps_wall > 0 else 999.0
        cap_ttk = _num(tcfg.get("capTtkSec"), 2.0)
        comfort_ttk = _num(tcfg.get("comfortTtkSec"), 8.0)
        verdict = "T0/S+ 瞬秒" if ttk_eff <= cap_ttk else ("A 稳健击杀" if ttk_eff <= comfort_ttk else "偏慢/刮痧")
        ttk_map[tid] = {
            "label": str(tcfg.get("label") or tid),
            "targetHp": int(hp),
            "pobRawTtkSec": ttk_raw,
            "effectiveTtkSec": ttk_eff,
            "bestCaseTtkSec": ttk_wall,
            "verdict": verdict,
        }

    uber_solo_ttk = (ttk_map.get("uber_arbiter_solo") or {}).get("effectiveTtkSec", 999.0)
    party6_uber_ttk = (ttk_map.get("party6_uber_boss") or {}).get("effectiveTtkSec", 999.0)
    party6_deli_ttk = (ttk_map.get("party6_200deli_rare") or {}).get("effectiveTtkSec", 999.0)

    multi_hit_audit: dict[str, Any] = {
        "cadenceArchetype": cadence_archetype,
        "pobDisplaysSingleHitOnly": pob_single_hit_only,
        "alreadyScaledInPob": already_scaled_in_pob,
        "pobRawDps": round(base_for_mult, 1),
        "singleHitAvg": round(avg_hit, 1),
        "multiHitFactorOpen": round(mult_open, 2),
        "multiHitFactorFull": round(mult_wall_or_full, 2),
        "effectiveDpsOpen": eff_dps_open,
        "effectiveDpsFull": eff_dps_wall,
        "effectiveDpsRepresentative": eff_dps_rep,
        "enemyIsBossPreset": enemy_boss,
        "amplifiers": amplifiers,
    }
    ttk_anchors: dict[str, Any] = {
        "benchmarkPatch": str(benchmarks.get("patch") or "0.5.5"),
        "clearsSoloEndgameCap": bool(uber_solo_ttk <= 2.5),
        "clearsParty6UberEasily": bool(party6_uber_ttk <= 3.5),
        "clearsParty6Delirium200Easily": bool(party6_deli_ttk <= 1.5),
        "targets": ttk_map,
    }
    return multi_hit_audit, ttk_anchors


def _compute_mana_sustain_audit(
    raw_sim: dict[str, Any],
    main_mech: dict[str, Any],
    key_skills: list[dict[str, Any]],
    ctx: dict[str, Any],
    cadence_archetype: str,
    ttk_anchors: dict[str, Any],
    benchmarks: dict[str, Any],
) -> dict[str, Any]:
    """双态魔力净值求解器：按技能节拍（非盲目 APS）+ 全回复来源 + Boss TTK 兜底窗口判定真实续航。"""
    ms_cfg = benchmarks.get("manaSustain") or {}
    kills_per_sec = _num(ms_cfg.get("denseMapKillsPerSec"), 6.0)
    setup_uses = int(_num(ms_cfg.get("defaultCadenceSetupUses"), 3))
    cycle_sec = max(0.5, _num(ms_cfg.get("defaultCadenceCycleSec"), 1.5))
    flask_recovery = _num(ms_cfg.get("defaultManaFlaskTotalRecovery"), 2400.0)
    safety_margin = _num(ms_cfg.get("bossSafetyMarginMult"), 1.5)

    mana_cost = _num(raw_sim.get("manaCost"))
    raw_mps_cost = _num(raw_sim.get("manaPerSecCost"))
    life_cost = _num(raw_sim.get("lifeCost"))
    mana_max = _num(raw_sim.get("mana"))
    mana_unres = _num(raw_sim.get("manaUnreserved")) or mana_max
    mana_regen = _num(raw_sim.get("manaRegen"))
    mana_leech = _num(raw_sim.get("manaLeech"))
    mana_on_kill_pct = _num(ctx.get("manaOnKillPct"))
    all_gems = ctx["allGemsLow"]

    has_mana_remnants = "mana remnants" in all_gems
    has_mark_siphoning = any("mark of siphoning" in g for g in all_gems)
    is_blood_cost = (mana_cost <= 0 and life_cost > 0) or ("blood magic" in ctx["allNodesLow"])

    # 1. 按原型计算真实每秒耗蓝（消除盲区 1）
    cost_model = "sustained_aps"
    if is_blood_cost:
        actual_mps_cost = 0.0
        cost_model = "life_or_blood_magic"
    elif cadence_archetype == "AUTONOMOUS_MULTI" and int(_num(main_mech.get("activeTotemLimit"), 0)) > 0:
        # 图腾仅算放置动作耗蓝，严禁套用 Archmage 每次自动施法蓝耗
        actual_mps_cost = round(mana_cost / 4.0, 1)
        cost_model = "totem_placement_only"
    elif cadence_archetype == "CADENCE_BURST":
        # 节拍爆发：例如 3次前置位移/蓄力 + Barrage + 1次主力爆发 / 1.5s 周期
        setup_cost = 0.0
        barrage_cost = 0.0
        for sk in key_skills:
            nm = str(sk.get("name") or "").lower()
            if "whirling slash" in nm:
                setup_cost = _num(sk.get("manaCost"))
            elif "barrage" in nm:
                barrage_cost = _num(sk.get("manaCost"))
        actual_mps_cost = round((setup_uses * setup_cost + barrage_cost + mana_cost) / cycle_sec, 1)
        cost_model = f"cadence_cycle({setup_uses}xSetup+Burst/{cycle_sec:.1f}s)"
    else:
        actual_mps_cost = round(raw_mps_cost if raw_mps_cost > 0 else mana_cost * _num(raw_sim.get("speed")), 1)

    # 2. 刷图态净回蓝（消除盲区 2：计入偷蓝 + 击杀回蓝 + Mana Remnants）
    mok_per_sec = round(mana_max * (mana_on_kill_pct / 100.0) * kills_per_sec, 1) if mana_on_kill_pct > 0 else 0.0
    map_gain = round(mana_regen + mana_leech + mok_per_sec, 1)
    map_net = round(map_gain - actual_mps_cost, 1)
    map_pass = bool(is_blood_cost or map_net >= 0 or mana_on_kill_pct >= 1.5 or has_mana_remnants or ctx["hasSpecialManaFlask"])

    # 3. Boss 态续航与 TTK 联动判定（消除盲区 5：Boss 几秒秒杀则药剂完全兜底，不报缺蓝！）
    boss_gain = round(mana_regen + mana_leech, 1)
    boss_deficit = round(max(0.0, actual_mps_cost - boss_gain), 1)
    uber_ttk = _num((ttk_anchors.get("targets") or {}).get("uber_arbiter_solo", {}).get("effectiveTtkSec"), 10.0)
    est_flask = flask_recovery * (1.8 if ctx["hasSpecialManaFlask"] else (1.0 if ctx["hasManaFlask"] else 0.35))
    if boss_deficit <= 0.1 or is_blood_cost:
        sustainable_sec = 999.0
        boss_pass = True
        boss_note = "纯回复/偷取已覆盖Boss战蓝耗（永动）"
    else:
        sustainable_sec = round((mana_unres + est_flask) / boss_deficit, 1)
        boss_pass = bool(sustainable_sec >= max(3.0, uber_ttk * safety_margin) or ctx["hasSpecialManaFlask"])
        boss_note = (
            f"Boss战净赤字 {boss_deficit:.1f}/s，但蓝池+药剂可支撑 {sustainable_sec:.1f}s ≥ Uber TTK({uber_ttk:.1f}s)×1.5，斩杀窗口完全兜底"
            if boss_pass
            else f"Boss战净赤字 {boss_deficit:.1f}/s，蓝池+药剂仅撑 {sustainable_sec:.1f}s（Uber TTK {uber_ttk:.1f}s），持久战有断蓝压力"
        )

    sources: list[str] = []
    if mana_regen > 0:
        sources.append(f"秒回 {mana_regen:.1f}/s")
    if mana_leech > 0:
        sources.append(f"偷蓝 {mana_leech:.1f}/s")
    if mana_on_kill_pct > 0:
        sources.append(f"击杀回 {mana_on_kill_pct:.1f}% 蓝(刷图+{mok_per_sec:.0f}/s)")
    if has_mana_remnants:
        sources.append("Mana Remnants 蓝球溢流")
    if has_mark_siphoning:
        sources.append("Mark of Siphoning 印记吸蓝")
    if ctx["hasSpecialManaFlask"]:
        sources.append("特化魔力药剂(Lavianga/Melting Maelstrom等)")
    elif ctx["hasManaFlask"]:
        sources.append("常规魔力药剂")

    return {
        "costModel": cost_model,
        "pobRawManaPerSecCost": round(raw_mps_cost, 1),
        "actualManaCostPerSec": actual_mps_cost,
        "mapNetManaPerSec": map_net,
        "mapManaVerdict": "PASS" if map_pass else "WARN",
        "bossNetDeficitPerSec": boss_deficit,
        "bossSustainableSec": sustainable_sec,
        "bossManaVerdict": "PASS" if boss_pass else "WARN",
        "bossManaNote": boss_note,
        "recoverySources": sources,
    }


def _compute_layered_defense_audit(
    raw_sim: dict[str, Any],
    ctx: dict[str, Any],
    benchmarks: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """分层防御与技能端恢复审计（消除只看裸物理一击线误伤闪避/偏转/鬼舞/结界流的盲区）+ 断链体检。"""
    dcaps = benchmarks.get("defenseCaps") or {}
    all_gems = ctx["allGemsLow"]
    all_items = ctx["allItemsLow"]
    all_nodes = ctx["allNodesLow"]

    life = _num(raw_sim.get("life"))
    es = _num(raw_sim.get("es"))
    ward = _num(raw_sim.get("ward"))
    mana = _num(raw_sim.get("mana"))
    is_ci = int(_num(raw_sim.get("chaosInoculation"), 0)) > 0
    has_mom = "mind over matter" in all_nodes
    has_eb = "eldritch battery" in all_nodes
    evade = _num(raw_sim.get("evadeChance"))
    deflect = _num(raw_sim.get("deflectChance"))
    deflect_rating = _num(raw_sim.get("deflectionRating"))
    block = _num(raw_sim.get("blockChance"))
    armour = _num(raw_sim.get("armour"))
    phys_hit = _num(raw_sim.get("physMaxHit"))
    ele_hit = _num(raw_sim.get("eleMaxHit"))
    chaos_hit = _num(raw_sim.get("chaosMaxHit"))

    has_blind = any("blind" in g for g in all_gems) or "twister" in all_gems or "whirling slash" in all_gems
    deflect_cap = _num(dcaps.get("deflectionRatingCapBlinded" if has_blind else "deflectionRatingCap"), 28800.0)
    deflect_cap_pct = round(min(100.0, (deflect_rating / deflect_cap) * 100.0), 1) if deflect_rating > 0 else 0.0
    # 0.5 偏转触发直接减伤 40%（有效承伤倍率 1 / 0.6 ≈ 1.667x），叠加 Runic Ward 兜底
    deflect_dr = _num(dcaps.get("deflectionDamageReductionPct"), 40.0) / 100.0
    deflected_phys_hit = round(phys_hit / (1.0 - deflect_dr) + ward, 1) if (deflect >= 50.0 and phys_hit > 0) else round(phys_hit + ward, 1)
    deflected_ele_hit = round(ele_hit / (1.0 - deflect_dr) + ward, 1) if (deflect >= 50.0 and ele_hit > 0) else round(ele_hit + ward, 1)

    skill_def_layers: list[str] = []
    for key, label in (
        ("virtuous barrier", "Virtuous Barrier (古灵专属防御屏障/护盾)"),
        ("ghost dance", "Ghost Dance (按闪避瞬回ES)"),
        ("wind dancer", "Wind Dancer (受击闪避层数与击退)"),
        ("charge regulation", "Charge Regulation (耐力球减伤 / 防御与暴击常驻增益)"),
        ("purity of fire", "Purity of Fire (火抗光环 + 免费挂载回复/功能辅助)"),
        ("purity of ice", "Purity of Ice (冰抗光环 + 免费挂载回复/功能辅助)"),
        ("purity of lightning", "Purity of Lightning (电抗光环 + 免费挂载回复/功能辅助)"),
        ("purity of elements", "Purity of Elements (全抗与异常免疫光环)"),
        ("discipline", "Discipline (能量护盾光环)"),
        ("arctic armour", "Arctic Armour (极地装甲减伤与受击冰缓)"),
        ("scavenged plating", "Scavenged Plating (破甲叠高额护甲)"),
        ("barkskin", "Barkskin (树皮减伤与闪避/护甲动态补强)"),
        ("convalescence", "Convalescence (一键强制启动ES充能)"),
        ("temporal rift", "Temporal Rift (时空裂隙一键回溯血/盾/蓝)"),
        ("frost wall", "Frost Wall (冰墙格挡弹道/控场阻隔与冰冻)"),
        ("time of need", "Time of Need (周期回血解异常)"),
        ("grim feast", "Grim Feast (刷图溢出护盾翻倍)"),
        ("life remnants", "Life Remnants (拾取生命溢出)"),
        ("meditate", "Meditate (引导护盾溢流)"),
        ("vitality", "Vitality (持续回血光环/辅助)"),
        ("cannibalism", "Cannibalism (击杀回血续航)"),
        ("herbalism", "Herbalism (生命药剂回复增强)"),
        ("enfeeble", "Enfeeble (衰弱诅咒直降敌人伤害)"),
        ("temporal chains", "Temporal Chains (时空锁链减速敌人行动)"),
        ("herald of ice", "Herald of Ice (冰爆全屏碎尸防亡语)"),
        ("mantra of destruction", "Mantra of Destruction (消连击触发充能/爆发)"),
        ("atalui's bloodletting", "Atalui's Bloodletting (耗血转巨额秒回)"),
        ("olroth's conviction", "Olroth's Conviction (消耗结界联动减伤/防御触发)"),
        ("knockback", "Knockback 击退控场 (推开贴脸近战怪降低承伤)"),
        ("pin", "Pin 定身控场 (限制怪物近身输出)"),
        ("electrocute", "Electrocute 感电硬控 (打断Boss与稀有怪施法)"),
    ):
        if any(key in g for g in all_gems):
            skill_def_layers.append(label)
    for key, label in (
        ("headhunter", "Headhunter 猎首 (高密图偷词缀滚雪球)"),
        ("mageblood", "Mageblood 法血 (4瓶常驻顶级功能药剂)"),
        ("shavronne's satchel", "Shavronne's Satchel (血瓶直接回ES)"),
        ("palm of the dreamer", "Palm of the Dreamer (25%最大生命混沌爆破清屏)"),
        ("undying hate", "Undying Hate (配合耗血构建巨额秒回)"),
        ("svalinn", "Svalinn (幸运格挡)"),
    ):
        if any(key in it for it in all_items):
            skill_def_layers.append(label)
    if has_blind:
        skill_def_layers.append("100% 致盲覆盖 (降敌20%命中，同步抬高闪避与偏转效率)")
    if ctx["armourAppliesToElemental"]:
        skill_def_layers.append("护甲套用元素击中伤 (0.5 新词缀)")

    eff_pool = round((es if is_ci else (life + es)) + (mana if has_mom else 0.0) + ward, 1)
    if has_mom and has_eb:
        paradigm = "mom_eb_mana_pool"
    elif is_ci:
        paradigm = "es_ci_recharge"
    elif evade >= 65.0 and (deflect >= 50.0 or any("ghost dance" in g for g in all_gems)):
        paradigm = "evasion_deflect_recovery"
    elif armour >= 10000.0 or block >= 50.0:
        paradigm = "armour_block_tank"
    else:
        paradigm = "hybrid"

    # 客观可验证的断链预警（绝不误报，仅在 PoB2 数据明确命中时触发）
    broken_hints: list[str] = []
    has_rakiata = "rakiata's flow" in ctx["mainSupportsLow"] or "rakiata's flow" in all_gems
    if has_rakiata and any(g in all_gems for g in ("elemental weakness", "flammability", "frostbite", "conductivity")):
        broken_hints.append("携带 Rakiata's Flow（反转元素抗性）同时带了降抗诅咒，两者冲突会反向降伤")
    if is_ci and "heavy buffer" in all_nodes:
        broken_hints.append("已点 CI（血量锁1）但仍分配了 Heavy Buffer，建议洗掉防自杀/浪费")
    if "advanced thaumaturgy" in all_nodes and 0 < ctx["mainGemQuality"] < 20:
        broken_hints.append(f"古灵升华 Advanced Thaumaturgy 下主技能品质仅 {ctx['mainGemQuality']}%（未满 20% 丢核心乘区）")
    sp1 = _num(raw_sim.get("spiritUnreservedSet1"))
    sp2 = _num(raw_sim.get("spiritUnreservedSet2"))
    sp_cur = _num(raw_sim.get("spiritUnreserved"))
    if sp_cur < 0 and (sp1 >= 0 or sp2 >= 0):
        broken_hints.append(f"当前面板精魂 {sp_cur:.0f} 为双武器套切换假红线（WS1余量 {sp1:.0f} / WS2余量 {sp2:.0f}，非真超载）")

    defense_audit = {
        "paradigm": paradigm,
        "effectiveHitPool": eff_pool,
        "rawPhysMaxHit": round(phys_hit, 1),
        "deflectedPhysMaxHit": deflected_phys_hit,
        "rawEleMaxHit": round(ele_hit, 1),
        "deflectedEleMaxHit": deflected_ele_hit,
        "chaosMaxHit": round(chaos_hit, 1),
        "deflectChancePct": round(deflect, 1),
        "deflectionCapProgressPct": deflect_cap_pct,
        "wardBuffer": round(ward, 1),
        "skillAndGearDefenseLayers": skill_def_layers,
    }
    return defense_audit, broken_hints


def extract_combat_metrics(
    raw_sim: dict[str, Any],
    parsed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """从 PoB2 引擎实算结果提取 6 大维度高信噪比事实：技能机制、位移速度、输出、防御、续航、PoB 假设审计。"""
    eff_dps = round(_num(raw_sim.get("dps")), 1)
    totems = int(_num(raw_sim.get("activeTotemLimit"), 0))
    total_totem_dps = round(eff_dps * totems, 1) if totems > 1 else eff_dps

    walk_ms = round(_num(raw_sim.get("moveSpeed")), 1)
    skill_ms = round(_num(raw_sim.get("skillMoveSpeed"), walk_ms), 1)
    action_speed = round(_num(raw_sim.get("actionSpeed")), 1)
    ws_stats = raw_sim.get("weaponSetStats") or {}
    ws1 = ws_stats.get("ws1") if isinstance(ws_stats, dict) else None
    ws2 = ws_stats.get("ws2") if isinstance(ws_stats, dict) else None

    travel_skills: list[dict[str, Any]] = []
    key_skills: list[dict[str, Any]] = []
    full_dps_skills: list[dict[str, Any]] = []
    seen_travel: set[str] = set()

    for sk in raw_sim.get("allSkillsDps") or []:
        if not isinstance(sk, dict):
            continue
        name = str(sk.get("name") or "").strip()
        if not name:
            continue
        compact_sk = _compact_skill_mechanics_row(sk, walk_ms)
        key_skills.append(compact_sk)
        if sk.get("includeInFullDPS"):
            full_dps_skills.append({
                "name": name,
                "dps": round(_num(sk.get("dps")), 1),
                "gemCount": int(_num(sk.get("gemCount"), 1)),
                "skillPartName": str(sk.get("skillPartName") or ""),
                "skillStageCount": int(_num(sk.get("skillStageCount"), 0)),
            })

        is_travel = bool(sk.get("isTravel")) or bool(_TRAVEL_SKILL_NAME_RE.search(name))
        if not is_travel:
            continue
        key = name.lower()
        if key in seen_travel:
            continue
        seen_travel.add(key)

        spd = round(_num(sk.get("speed")), 2)
        spd1 = round(_num(sk.get("speedWs1")), 2)
        spd2 = round(_num(sk.get("speedWs2")), 2)
        best_spd = max(spd, spd1, spd2)
        cd = round(_num(sk.get("cooldown")), 2)
        cd1 = round(_num(sk.get("cooldownWs1")), 2)
        cd2 = round(_num(sk.get("cooldownWs2")), 2)
        pos_cds = [x for x in (cd, cd1, cd2) if x > 0]
        best_cd = min(pos_cds) if pos_cds else 0.0
        cast_time = round(_num(sk.get("castTime")), 3)
        sk_use_ms = round(_num(sk.get("skillMoveSpeed"), walk_ms), 1)

        best_ws = int(_num(sk.get("weaponSet"), 1))
        if spd1 > 0 and spd1 >= spd2 and spd1 >= spd:
            best_ws = 1
        elif spd2 > 0 and spd2 > spd1 and spd2 >= spd:
            best_ws = 2

        t_entry: dict[str, Any] = {
            "name": name,
            "level": int(_num(sk.get("level"), 0)),
            "weaponSet": best_ws,
            "usesPerSec": best_spd,
            "speedWs1": spd1,
            "speedWs2": spd2,
            "cooldownSec": best_cd,
            "castTimeSec": cast_time,
            "skillMoveSpeedPct": sk_use_ms,
            "dps": round(_num(sk.get("dps")), 1),
        }
        if _num(sk.get("aoeRadius")) > 0:
            t_entry["aoeRadiusMetres"] = round(_num(sk.get("aoeRadius")), 2)
        if _num(sk.get("aoeMod")) > 0:
            t_entry["aoeMod"] = round(_num(sk.get("aoeMod")), 2)
        if _num(sk.get("duration")) > 0:
            t_entry["durationSec"] = round(_num(sk.get("duration")), 2)
        travel_skills.append(t_entry)

    travel_skills.sort(
        key=lambda x: (
            1 if x["usesPerSec"] > 0 and x["cooldownSec"] <= 0.5 else 0,
            x["usesPerSec"],
            -x["cooldownSec"] if x["cooldownSec"] > 0 else 0.0,
        ),
        reverse=True,
    )
    key_skills.sort(
        key=lambda x: (
            2 if x.get("isMain") else (1 if x.get("isTravel") else 0),
            float(x.get("dps") or 0),
            float(x.get("usesPerSec") or 0),
        ),
        reverse=True,
    )

    summary_parts: list[str] = []
    if travel_skills:
        top_t = travel_skills[0]
        if top_t["cooldownSec"] >= 0.15 and top_t["usesPerSec"] <= 2.5:
            summary_parts.append(f"{top_t['name']} CD {top_t['cooldownSec']:.2f}s")
        elif top_t["usesPerSec"] > 0:
            ws_tag = f" (WS{top_t['weaponSet']})" if (ws1 and ws2) else ""
            summary_parts.append(f"{top_t['name']} {top_t['usesPerSec']:.2f}/s{ws_tag}")
        else:
            summary_parts.append(top_t["name"])
    summary_parts.append(f"步行移速 {walk_ms:+.0f}%")
    if abs(skill_ms - walk_ms) >= 5.0:
        summary_parts.append(f"施放中移速 {skill_ms:+.0f}%")
    if abs(action_speed) >= 1.0:
        summary_parts.append(f"行动速度 {action_speed:+.0f}%")

    # 主技能形态/阶段/持续/投射物/范围/印记/冷却/弩箭/图腾/召唤/猛击/触发实算全貌
    main_mech: dict[str, Any] = {
        "name": str(raw_sim.get("mainSkillName") or ""),
        "level": int(_num(raw_sim.get("mainSkillLevel"), 0)),
        "archetype": str(raw_sim.get("skillArchetype") or ""),
        "isShapeshift": bool(int(_num(raw_sim.get("isShapeshift"), 0)) > 0),
        "gemCount": int(_num(raw_sim.get("gemCount"), 1)),
        "includeInFullDPS": bool(int(_num(raw_sim.get("includeInFullDPS"), 0)) > 0),
        "skillPart": int(_num(raw_sim.get("skillPart"), 0)),
        "skillPartName": str(raw_sim.get("skillPartName") or ""),
        "availableParts": list(raw_sim.get("mainSkillParts") or []),
        "skillStageCount": int(_num(raw_sim.get("skillStageCount"), 0)),
        "skillStageMax": int(_num(raw_sim.get("skillStageMax"), 0)),
        "stageRampSec": round(_num(raw_sim.get("stageRampSec")), 2),
        "durationSec": round(_num(raw_sim.get("duration")), 2),
        "durationMod": round(_num(raw_sim.get("durationMod")), 2),
        "durationSecondarySec": round(_num(raw_sim.get("durationSecondary")), 2),
        "projSpeedMod": round(_num(raw_sim.get("projSpeedMod")), 2),
        "projCount": int(_num(raw_sim.get("projectileCount"), 0)),
        "pierceCount": int(_num(raw_sim.get("pierceCount"), 0)),
        "pierceChance": round(_num(raw_sim.get("pierceChance")), 1),
        "pierceAll": bool(int(_num(raw_sim.get("pierceAll"), 0)) > 0),
        "chainCount": int(_num(raw_sim.get("chainCount"), 0)),
        "terrainChainChance": round(_num(raw_sim.get("terrainChainChance")), 1),
        "bouncesOffTerrain": bool(int(_num(raw_sim.get("bouncesOffTerrain"), 0)) > 0),
        "bounceCount": int(_num(raw_sim.get("bounceCount"), 0)),
        "forkCount": int(_num(raw_sim.get("forkCount"), 0)),
        "splitCount": int(_num(raw_sim.get("splitCount"), 0)),
        "hitIntervalSec": round(_num(raw_sim.get("hitIntervalSec")), 2),
        "aoeRadiusMetres": round(_num(raw_sim.get("aoeRadius")), 2),
        "aoeMod": round(_num(raw_sim.get("aoeMod")), 2),
        "presenceRadiusMetres": round(_num(raw_sim.get("presenceRadius")), 2),
        "sealMax": int(_num(raw_sim.get("sealMax"), 0)),
        "sealCooldownSec": round(_num(raw_sim.get("sealCooldown")), 2),
        "timeMaxSealsSec": round(_num(raw_sim.get("timeMaxSeals")), 2),
        "cooldownSec": round(_num(raw_sim.get("cooldown")), 2),
        "cooldownRecoveryMod": round(_num(raw_sim.get("cooldownRecovery")), 2),
        "triggerRate": round(_num(raw_sim.get("triggerRate")), 2),
        "repeatCount": int(_num(raw_sim.get("repeatCount"), 0)),
        "activeTotemLimit": totems,
        "activeMinionLimit": int(_num(raw_sim.get("activeMinionLimit"), 0)),
    }
    # 仅在有值时附加各形态专属字段，保持 JSON 紧凑高信噪比
    for src_k, dst_k, dec in (
        ("magazineSize", "magazineSize", 0),
        ("reloadTime", "reloadTimeSec", 2),
        ("reloadSpeedMod", "reloadSpeedMod", 2),
        ("fuseTime", "fuseTimeSec", 2),
        ("aftershockChance", "aftershockChance", 1),
        ("exertedAttacks", "exertedAttacks", 0),
        ("totemPlacementTime", "totemPlacementTimeSec", 2),
        ("totemLife", "totemLife", 0),
        ("minionLife", "minionLife", 0),
        ("minionDps", "minionDps", 1),
        ("minionMoveSpeed", "minionMoveSpeedPct", 1),
        ("spiritCost", "spiritCost", 0),
        ("metaEnergyGainMod", "metaEnergyGainMod", 2),
        ("maxEnergy", "maxEnergy", 0),
        ("dotSharePct", "dotSharePct", 1),
        ("poisonStacks", "poisonStacks", 1),
        ("poisonStackCap", "poisonStackCap", 0),
    ):
        val = _num(raw_sim.get(src_k), 0.0)
        if val > 0:
            main_mech[dst_k] = int(round(val)) if dec == 0 else round(val, dec)
    skill_mechanics = {
        "mainSkill": main_mech,
        "keySkills": key_skills[:14],
    }

    mobility: dict[str, Any] = {
        "walkMoveSpeedPct": walk_ms,
        "walkMoveSpeedMod": round(1.0 + walk_ms / 100.0, 2),
        "skillMoveSpeedPct": skill_ms,
        "actionSpeedPct": action_speed,
        "travelSkills": travel_skills,
        "primaryTravelSummary": " · ".join(summary_parts),
    }
    if isinstance(ws1, dict) and ws1.get("moveSpeed") is not None:
        mobility["ws1MoveSpeedPct"] = round(_num(ws1.get("moveSpeed")), 1)
    if isinstance(ws2, dict) and ws2.get("moveSpeed") is not None:
        mobility["ws2MoveSpeedPct"] = round(_num(ws2.get("moveSpeed")), 1)

    offense: dict[str, Any] = {
        "mainSkillName": str(raw_sim.get("mainSkillName") or ""),
        "mainSkillLevel": int(_num(raw_sim.get("mainSkillLevel"), 0)),
        "effectiveDps": eff_dps,
        "totalTotemDps": total_totem_dps,
        "fullDps": round(_num(raw_sim.get("fullDps")), 1),
        "combinedDps": round(_num(raw_sim.get("combinedDps")), 1),
        "totalDps": round(_num(raw_sim.get("totalDps")), 1),
        "dotDps": round(_num(raw_sim.get("dotDps")), 1),
        "igniteDps": round(_num(raw_sim.get("igniteDps")), 1),
        "bleedDps": round(_num(raw_sim.get("bleedDps")), 1),
        "poisonDps": round(_num(raw_sim.get("poisonDps")), 1),
        "cullingDps": round(_num(raw_sim.get("cullingDps")), 1),
        "dpsSource": str(raw_sim.get("dpsSource") or "CombinedDPS"),
        "avgHit": round(_num(raw_sim.get("avgHit")), 1),
        "avgBurstDamage": round(_num(raw_sim.get("avgBurstDamage")), 1),
        "usesPerSec": round(_num(raw_sim.get("speed")), 2),
        "castTimeSec": round(_num(raw_sim.get("castTime")), 3),
        "critChance": round(_num(raw_sim.get("critChance")), 2),
        "preEffectiveCritChance": round(_num(raw_sim.get("preEffectiveCritChance")), 2),
        "critMult": round(_num(raw_sim.get("critMult")), 1),
        "critBifurcates": bool(_num(raw_sim.get("critBifurcates"), 0) > 0),
        "hitChance": round(_num(raw_sim.get("hitChance")), 1),
        "shockEffect": round(_num(raw_sim.get("shockEffect")), 1),
        "chillEffect": round(_num(raw_sim.get("chillEffect")), 1),
        "freezeChance": round(_num(raw_sim.get("freezeChance")), 1),
        "activeTotemLimit": totems,
        "projectileCount": int(_num(raw_sim.get("projectileCount"), 0)),
        "rage": int(_num(raw_sim.get("rage"), 0)),
        "powerCharges": f"{int(_num(raw_sim.get('powerCharges'), 0))}/{int(_num(raw_sim.get('powerChargesMax'), 0))}",
        "frenzyCharges": f"{int(_num(raw_sim.get('frenzyCharges'), 0))}/{int(_num(raw_sim.get('frenzyChargesMax'), 0))}",
        "enduranceCharges": f"{int(_num(raw_sim.get('enduranceCharges'), 0))}/{int(_num(raw_sim.get('enduranceChargesMax'), 0))}",
    }
    if isinstance(ws1, dict):
        offense["ws1Dps"] = round(_num(ws1.get("dps")), 1)
        offense["ws1Speed"] = round(_num(ws1.get("speed")), 2)
    if isinstance(ws2, dict):
        offense["ws2Dps"] = round(_num(ws2.get("dps")), 1)
        offense["ws2Speed"] = round(_num(ws2.get("speed")), 2)

    defense: dict[str, Any] = {
        "ehp": round(_num(raw_sim.get("ehp")), 1),
        "ehpInfinite": bool(int(_num(raw_sim.get("ehpInfinite"), 0)) > 0),
        "life": round(_num(raw_sim.get("life")), 1),
        "lifeUnreserved": round(_num(raw_sim.get("lifeUnreserved")), 1),
        "lifeUnreservedPct": round(_num(raw_sim.get("lifeUnreservedPct")), 1),
        "es": round(_num(raw_sim.get("es")), 1),
        "ward": round(_num(raw_sim.get("ward")), 1),
        "mana": round(_num(raw_sim.get("mana")), 1),
        "manaUnreserved": round(_num(raw_sim.get("manaUnreserved")), 1),
        "armour": round(_num(raw_sim.get("armour")), 1),
        "physDR": round(_num(raw_sim.get("physDR")), 1),
        "evasion": round(_num(raw_sim.get("evasion")), 1),
        "evadeChance": round(_num(raw_sim.get("evadeChance")), 1),
        "deflectChance": round(_num(raw_sim.get("deflectChance")), 1),
        "blockChance": round(_num(raw_sim.get("blockChance")), 1),
        "spellBlockChance": round(_num(raw_sim.get("spellBlockChance")), 1),
        "physMaxHit": round(_num(raw_sim.get("physMaxHit")), 1),
        "fireMaxHit": round(_num(raw_sim.get("fireMaxHit")), 1),
        "coldMaxHit": round(_num(raw_sim.get("coldMaxHit")), 1),
        "lightningMaxHit": round(_num(raw_sim.get("lightningMaxHit")), 1),
        "eleMaxHit": round(_num(raw_sim.get("eleMaxHit")), 1),
        "chaosMaxHit": round(_num(raw_sim.get("chaosMaxHit")), 1),
        "chaosMaxHitInfinite": bool(
            int(_num(raw_sim.get("chaosMaxHitInfinite"), 0)) > 0
            or int(_num(raw_sim.get("chaosInoculation"), 0)) > 0
        ),
        "fireRes": round(_num(raw_sim.get("fireRes")), 1),
        "coldRes": round(_num(raw_sim.get("coldRes")), 1),
        "lightningRes": round(_num(raw_sim.get("lightningRes")), 1),
        "chaosRes": round(_num(raw_sim.get("chaosRes")), 1),
        "fireResMax": round(_num(raw_sim.get("fireResMax"), 75.0), 1),
        "coldResMax": round(_num(raw_sim.get("coldResMax"), 75.0), 1),
        "lightningResMax": round(_num(raw_sim.get("lightningResMax"), 75.0), 1),
        "chaosResMax": round(_num(raw_sim.get("chaosResMax"), 75.0), 1),
        "lifeRegen": round(_num(raw_sim.get("lifeRegen")), 1),
        "lifeLeech": round(_num(raw_sim.get("lifeLeech")), 1),
        "lifeOnHit": round(_num(raw_sim.get("lifeOnHit")), 1),
        "lifeRecovery": round(_num(raw_sim.get("lifeRecovery")), 1),
        "esRegen": round(_num(raw_sim.get("esRegen")), 1),
        "esLeech": round(_num(raw_sim.get("esLeech")), 1),
        "esRecharge": round(_num(raw_sim.get("esRecharge")), 1),
        "esRechargeDelay": round(_num(raw_sim.get("esRechargeDelay")), 2),
        "esRecovery": round(_num(raw_sim.get("esRecovery")), 1),
        "stunThreshold": round(_num(raw_sim.get("stunThreshold")), 1),
        "ailmentThreshold": round(_num(raw_sim.get("ailmentThreshold")), 1),
    }

    sustain: dict[str, Any] = {
        "spirit": round(_num(raw_sim.get("spirit")), 1),
        "spiritUnreserved": round(_num(raw_sim.get("spiritUnreserved")), 1),
        "spiritUnreservedSet1": round(_num(raw_sim.get("spiritUnreservedSet1")), 1),
        "spiritUnreservedSet2": round(_num(raw_sim.get("spiritUnreservedSet2")), 1),
        "manaCost": round(_num(raw_sim.get("manaCost")), 1),
        "manaPerSecCost": round(_num(raw_sim.get("manaPerSecCost")), 1),
        "manaRegen": round(_num(raw_sim.get("manaRegen")), 1),
        "manaLeech": round(_num(raw_sim.get("manaLeech")), 1),
        "lifeCost": round(_num(raw_sim.get("lifeCost")), 1),
        "lifePerSecCost": round(_num(raw_sim.get("lifePerSecCost")), 1),
        "esCost": round(_num(raw_sim.get("esCost")), 1),
        "esPerSecCost": round(_num(raw_sim.get("esPerSecCost")), 1),
        "str": int(_num(raw_sim.get("str"), 0)),
        "reqStr": int(_num(raw_sim.get("reqStr"), 0)),
        "dex": int(_num(raw_sim.get("dex"), 0)),
        "reqDex": int(_num(raw_sim.get("reqDex"), 0)),
        "int": int(_num(raw_sim.get("int"), 0)),
        "reqInt": int(_num(raw_sim.get("reqInt"), 0)),
    }

    parsed_cfg = (parsed or {}).get("config") if isinstance(parsed, dict) else {}
    enemy_boss = str(raw_sim.get("enemyIsBoss") or (parsed_cfg or {}).get("enemyIsBoss") or "None").strip() or "None"
    pob_assumptions: dict[str, Any] = {
        "enemyIsBoss": enemy_boss,
        "enemyLevel": int(_num(raw_sim.get("enemyLevel"), 0)),
        "mainSkillPartName": main_mech["skillPartName"],
        "mainSkillStageCount": main_mech["skillStageCount"],
        "mainSkillStageMax": main_mech["skillStageMax"],
        "mainSkillGemCount": main_mech["gemCount"],
        "fullDpsSkills": full_dps_skills,
        "activeConfigFlags": parsed_cfg or {},
        "unparsedModsCount": len(raw_sim.get("unparsedMods") or []),
    }

    benchmarks = load_endgame_benchmarks()
    build_ctx = _collect_build_context(parsed, key_skills)
    multi_hit_audit, ttk_anchors = _compute_multi_hit_and_ttk(raw_sim, main_mech, key_skills, build_ctx, benchmarks)
    mana_sustain_audit = _compute_mana_sustain_audit(
        raw_sim, main_mech, key_skills, build_ctx, multi_hit_audit["cadenceArchetype"], ttk_anchors, benchmarks
    )
    defense_audit, broken_synergy_hints = _compute_layered_defense_audit(raw_sim, build_ctx, benchmarks)

    skill_mechanics["multiHitAudit"] = multi_hit_audit
    offense["ttkAnchors"] = ttk_anchors
    defense["defenseAudit"] = defense_audit
    sustain["manaSustainAudit"] = mana_sustain_audit
    if broken_synergy_hints:
        pob_assumptions["brokenSynergyHints"] = broken_synergy_hints

    return {
        "metricsVersion": int(_num(raw_sim.get("metricsVersion"), 2)) or 2,
        "skillMechanics": skill_mechanics,
        "mobility": mobility,
        "offense": offense,
        "defense": defense,
        "sustain": sustain,
        "pobAssumptions": pob_assumptions,
    }


def collect_build_facts(
    norm_xml: str,
    parsed: dict[str, Any],
    pool: Any = None,
    *,
    run_ablation: bool = True,
    precomputed_sim: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """运行完整 PoB2 实算 + 可选并发单件消融，返回 (raw_sim, engine_facts)。"""
    raw_sim = dict(precomputed_sim) if isinstance(precomputed_sim, dict) else pool.simulate(norm_xml, include_audit=True)
    impacts: list[dict[str, Any]] = []
    ablation_ms = 0
    if run_ablation and pool is not None:
        impacts, ablation_ms = ablate_build_items(norm_xml, parsed, raw_sim, pool)

    eff_dps = round(_num(raw_sim.get("dps")), 1)
    totems = int(_num(raw_sim.get("activeTotemLimit"), 0))
    total_totem_dps = round(eff_dps * totems, 1) if totems > 1 else eff_dps
    metrics = extract_combat_metrics(raw_sim, parsed)

    facts = {
        "metricsVersion": metrics["metricsVersion"],
        "mainSkillName": str(raw_sim.get("mainSkillName") or ""),
        "mainSkillLevel": int(_num(raw_sim.get("mainSkillLevel"), 0)),
        "activeTotemLimit": totems,
        "effectiveDps": eff_dps,
        "totalTotemDps": total_totem_dps,
        "dpsSource": str(raw_sim.get("dpsSource") or "CombinedDPS"),
        "life": round(_num(raw_sim.get("life")), 1),
        "lifeUnreserved": round(_num(raw_sim.get("lifeUnreserved")), 1),
        "lifeUnreservedPct": round(_num(raw_sim.get("lifeUnreservedPct")), 1),
        "es": round(_num(raw_sim.get("es")), 1),
        "mana": round(_num(raw_sim.get("mana")), 1),
        "manaCost": round(_num(raw_sim.get("manaCost")), 1),
        "manaPerSecCost": round(_num(raw_sim.get("manaPerSecCost")), 1),
        "manaRegen": round(_num(raw_sim.get("manaRegen")), 1),
        "activeWeaponSet": int(_num(raw_sim.get("activeWeaponSet"), 1)),
        "weaponSetStats": raw_sim.get("weaponSetStats") or {},
        "allSkillsDps": raw_sim.get("allSkillsDps") or [],
        "spiritAudit": raw_sim.get("spiritAudit") or [],
        "unparsedMods": raw_sim.get("unparsedMods") or [],
        "skillMechanics": metrics["skillMechanics"],
        "mobility": metrics["mobility"],
        "offense": metrics["offense"],
        "defense": metrics["defense"],
        "sustain": metrics["sustain"],
        "pobAssumptions": metrics["pobAssumptions"],
        "itemImpacts": impacts,
        "ablationElapsedMs": ablation_ms,
    }
    return raw_sim, facts
