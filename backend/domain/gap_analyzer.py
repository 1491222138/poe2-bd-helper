"""自身 BD vs 目标 BD 全景差距诊断 + 关键槽位词缀逐条剥离（常驻 LuaJIT 批量实算）。"""
from __future__ import annotations

import re
import time
from typing import Any

from backend.domain import affix_analyzer, build_profile
from backend.domain.common import pct_delta as _pct_delta, safe_num as _num
from backend.engine import pob_parser, runefix, xml_mutator


def evaluate_redlines(stats: dict[str, Any], baseline: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """检测抗性、精魂（含双武器组）、三属性与魔力红线。"""
    alerts: list[dict[str, Any]] = []
    has_ci = int(_num(stats.get("chaosInoculation"))) > 0

    for key, label, en_label in (("fireRes", "火焰", "Fire"), ("coldRes", "冰霜", "Cold"), ("lightningRes", "闪电", "Lightning")):
        v = _num(stats.get(key))
        base_v = _num(baseline.get(key), 75.0) if baseline else 75.0
        if v < 60:
            alerts.append({
                "kind": "resist",
                "severity": "fail",
                "message": f"{label}抗性掉至 {v:.0f}%（差 {75 - v:.0f}% 满抗）",
                "en": {"message": f"{en_label} Res drops to {v:.0f}% ({75 - v:.0f}% below cap)"},
            })
        elif v < 75 and (baseline is None or v < base_v):
            alerts.append({
                "kind": "resist",
                "severity": "warn",
                "message": f"{label}抗性不足 75%（当前 {v:.0f}%）",
                "en": {"message": f"{en_label} Res below 75% ({v:.0f}%)"},
            })

    if not has_ci:
        chaos = _num(stats.get("chaosRes"))
        base_c = _num(baseline.get("chaosRes"), chaos) if baseline else chaos
        if chaos < 0:
            alerts.append({
                "kind": "resist",
                "severity": "fail",
                "message": f"混沌抗为负（{chaos:.0f}%），高风险暴毙",
                "en": {"message": f"Negative Chaos Res ({chaos:.0f}%), high rip risk"},
            })
        elif chaos < 25 and (baseline is None or chaos < base_c - 5):
            alerts.append({
                "kind": "resist",
                "severity": "warn",
                "message": f"混沌抗偏低（{chaos:.0f}%）",
                "en": {"message": f"Low Chaos Res ({chaos:.0f}%)"},
            })

    sp_unres = _num(stats.get("spiritUnreserved"))
    sp1_unres = _num(stats.get("spiritUnreservedSet1"), sp_unres)
    sp2_unres = _num(stats.get("spiritUnreservedSet2"), 0.0)
    has_dual = int(_num(stats.get("hasDualWeaponSet"))) > 0

    if sp_unres < 0:
        alerts.append({
            "kind": "spirit",
            "severity": "fail",
            "message": f"精魂超额 {abs(sp_unres):.0f} 点（部分光环/技能无法开启）",
            "en": {"message": f"Spirit overcap by {abs(sp_unres):.0f} (some auras/skills disabled)"},
        })
    elif has_dual and (sp1_unres < 0 or sp2_unres < 0):
        which = "Set 1" if sp1_unres < 0 else "Set 2"
        short = abs(sp1_unres if sp1_unres < 0 else sp2_unres)
        alerts.append({
            "kind": "spirit",
            "severity": "fail",
            "message": f"双武器组 {which} 精魂超额 {short:.0f} 点",
            "en": {"message": f"Weapon {which} Spirit overcap by {short:.0f}"},
        })

    for have_k, req_k, label, en_label in (
        ("str", "reqStr", "力量", "Strength"),
        ("dex", "reqDex", "敏捷", "Dexterity"),
        ("int", "reqInt", "智力", "Intelligence"),
    ):
        have = _num(stats.get(have_k))
        req = _num(stats.get(req_k))
        if req > 0 and have < req:
            alerts.append({
                "kind": "attribute",
                "severity": "fail",
                "message": f"{label}不足（当前 {have:.0f} / 需要 {req:.0f}）",
                "en": {"message": f"Insufficient {en_label} ({have:.0f} / {req:.0f} req)"},
            })

    mana_unres = stats.get("manaUnreserved")
    if mana_unres is not None and _num(mana_unres) <= 0 and _num(stats.get("mana")) > 0:
        alerts.append({
            "kind": "mana",
            "severity": "warn",
            "message": "可用魔力耗尽（<= 0），可能无法释放技能",
            "en": {"message": "Unreserved Mana depleted (<= 0); may fail to cast skills"},
        })

    return alerts


def diff_passive_tree(my_parsed: dict[str, Any], target_parsed: dict[str, Any]) -> dict[str, Any]:
    my_tree = my_parsed.get("passiveTree") or {}
    tgt_tree = target_parsed.get("passiveTree") or {}

    def names_of(tree: dict[str, Any], key: str) -> list[str]:
        return [n["name"] for n in (tree.get(key) or []) if n.get("name")]

    def list_diff(tgt: list[str], mine: list[str]) -> tuple[list[str], list[str]]:
        mine_set = set(mine)
        tgt_set = set(tgt)
        missing = list(dict.fromkeys(x for x in tgt if x not in mine_set))
        extra = list(dict.fromkeys(x for x in mine if x not in tgt_set))
        return missing, extra

    miss_ks, extra_ks = list_diff(names_of(tgt_tree, "keystones"), names_of(my_tree, "keystones"))
    miss_asc, extra_asc = list_diff(names_of(tgt_tree, "ascendancyNodes"), names_of(my_tree, "ascendancyNodes"))
    miss_not, extra_not = list_diff(names_of(tgt_tree, "notables"), names_of(my_tree, "notables"))

    return {
        "myAllocated": int(my_tree.get("totalAllocated") or 0),
        "targetAllocated": int(tgt_tree.get("totalAllocated") or 0),
        "missingKeystones": miss_ks,
        "extraKeystones": extra_ks,
        "missingAscendancy": miss_asc,
        "extraAscendancy": extra_asc,
        "missingNotables": miss_not,
        "extraNotables": extra_not,
    }


def _main_group(parsed: dict[str, Any]) -> dict[str, Any] | None:
    groups = parsed.get("skillGroups") or []
    flagged = next((g for g in groups if g.get("isMain")), None)
    if flagged:
        return flagged
    return max(groups, key=lambda g: len(g.get("gems") or []), default=None)


def diff_skills(my_parsed: dict[str, Any], target_parsed: dict[str, Any]) -> dict[str, Any]:
    my_main = _main_group(my_parsed)
    tgt_main = _main_group(target_parsed)
    my_main_name = (my_main["gems"][0]["name"] if my_main and my_main.get("gems") else "")
    tgt_main_name = (tgt_main["gems"][0]["name"] if tgt_main and tgt_main.get("gems") else "")

    gaps: list[dict[str, Any]] = []

    if tgt_main:
        my_main_gems = {g["name"].lower(): g for g in ((my_main or {}).get("gems") or [])}
        for gem in tgt_main.get("gems") or []:
            gname = gem.get("name") or ""
            mine = my_main_gems.get(gname.lower())
            if not mine:
                gaps.append({
                    "kind": "missing_support",
                    "gemName": gname,
                    "detail": f"主技能组缺少「{gname}」（目标 Lv.{gem.get('level', 1)} / {gem.get('quality', 0)}%Q）",
                    "severity": "high",
                    "en": {"detail": f"Main group missing {gname} (target Lv.{gem.get('level', 1)} / {gem.get('quality', 0)}%Q)"},
                })
            else:
                tgt_lv = int(_num(gem.get("level"), 1))
                my_lv = int(_num(mine.get("level"), 1))
                tgt_q = int(_num(gem.get("quality"), 0))
                my_q = int(_num(mine.get("quality"), 0))
                if tgt_lv - my_lv >= 2:
                    gaps.append({
                        "kind": "level_gap",
                        "gemName": gname,
                        "detail": f"{gname} 等级落后（我 Lv.{my_lv} vs 目标 Lv.{tgt_lv}）",
                        "severity": "medium",
                        "en": {"detail": f"{gname} level behind (Yours Lv.{my_lv} vs Target Lv.{tgt_lv})"},
                    })
                if tgt_q >= 15 and tgt_q - my_q >= 8:
                    gaps.append({
                        "kind": "quality_gap",
                        "gemName": gname,
                        "detail": f"{gname} 品质偏低（我 {my_q}% vs 目标 {tgt_q}%，低成本可先补满 20%）",
                        "severity": "medium",
                        "en": {"detail": f"{gname} quality low (Yours {my_q}% vs Target {tgt_q}% — cheap upgrade)"},
                    })

    # 对比非主技能组的独立主动/精魂技能
    my_all_actives = {
        (g["gems"][0]["name"] or "").lower()
        for g in (my_parsed.get("skillGroups") or [])
        if g.get("gems")
    }
    for sg in target_parsed.get("skillGroups") or []:
        if sg is tgt_main or not sg.get("gems"):
            continue
        act = sg["gems"][0]
        aname = act.get("name") or ""
        if aname and aname.lower() not in my_all_actives:
            gaps.append({
                "kind": "missing_skill",
                "gemName": aname,
                "detail": f"缺少功能/光环技能「{aname}」（{len(sg['gems'])} 连）",
                "severity": "low",
                "en": {"detail": f"Missing utility/aura skill {aname} ({len(sg['gems'])}-link)"},
            })

    return {
        "myMainSkill": my_main_name,
        "targetMainSkill": tgt_main_name,
        "gaps": gaps[:15],
    }


def _brief_summary(parsed: dict[str, Any]) -> dict[str, Any]:
    char = parsed.get("character") or {}
    intent = build_profile.extract_intent(parsed)
    return {
        "className": char.get("className") or "Unknown",
        "ascendancy": char.get("ascendancy") or "None",
        "level": int(char.get("level") or 0),
        "mainSkill": intent.get("mainSkill") or "",
        "archetype": intent.get("archetype") or "",
    }


def _lookup_slot_prices(
    slot: str,
    price_snapshot: dict[str, Any] | None,
    price_overrides: dict[str, float] | None,
) -> dict[str, Any]:
    exact = budget = semi = None
    trade_url = ""

    if price_snapshot:
        for e in price_snapshot.get("entries") or []:
            if (e.get("slot") or "").lower() != slot.lower():
                continue
            val = _num(e.get("divineValue"), -1.0)
            if val <= 0:
                continue
            kind = e.get("kind") or "exact"
            if kind == "exact":
                exact = val
            elif kind == "budget":
                budget = val
            elif kind == "semi":
                semi = val
            if not trade_url and e.get("tradeUrl"):
                trade_url = str(e["tradeUrl"])

        legacy = (price_snapshot.get("prices") or {}).get(slot)
        if exact is None and isinstance(legacy, (int, float)) and legacy > 0:
            exact = float(legacy)

    override = (price_overrides or {}).get(slot)
    if override is not None and _num(override, -1.0) > 0:
        primary_price = float(override)
        price_basis: str | None = "override"
    elif exact is not None:
        # 试穿模拟用的是目标原件，主参考价优先对齐 exact 避免「原件收益 / 平替低价」虚高 ROI
        primary_price = exact
        price_basis = "exact"
    elif budget is not None:
        primary_price = budget
        price_basis = "budget"
    else:
        primary_price = None
        price_basis = None

    return {
        "priceDivine": round(primary_price, 2) if primary_price is not None else None,
        "exactPriceDivine": round(exact, 2) if exact is not None else None,
        "budgetPriceDivine": round(budget, 2) if budget is not None else None,
        "semiPriceDivine": round(semi, 2) if semi is not None else None,
        "priceBasis": price_basis,
        "tradeUrl": trade_url,
    }


def _norm_item_text(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip())


def analyze_gap(
    my_code_or_xml: str,
    target_code_or_xml: str,
    pool: Any,
    *,
    price_snapshot: dict[str, Any] | None = None,
    price_overrides: dict[str, float] | None = None,
) -> dict[str, Any]:
    """执行「我的 BD vs 目标 BD」全景诊断与逐件装备试穿实算。"""
    t0 = time.time()

    my_xml, _ = runefix.normalize_runes(pob_parser.decode_pob(my_code_or_xml))
    tgt_xml, _ = runefix.normalize_runes(pob_parser.decode_pob(target_code_or_xml))

    my_parsed = pob_parser.parse_pob_xml(my_xml)
    tgt_parsed = pob_parser.parse_pob_xml(tgt_xml)

    # 1. 先实算我的基线并锁定 dpsSource 口径，确保后续每次替换可横向对比
    my_stats = pool.simulate(my_xml, include_audit=True)
    lock_key = my_stats.get("dpsSource") or None
    my_dps = _num(my_stats.get("dps"))
    my_ehp = _num(my_stats.get("ehp"))
    my_ms = _num(my_stats.get("moveSpeed"))
    my_active_ws = int(_num(my_stats.get("activeWeaponSet"), 1))
    my_ws_stats = my_stats.get("weaponSetStats") if isinstance(my_stats.get("weaponSetStats"), dict) else {}

    def _is_inactive_ws_slot(slot_name: str) -> bool:
        sl = (slot_name or "").strip().lower()
        if my_active_ws == 1:
            return sl in ("weapon 1 swap", "weapon 2 swap")
        if my_active_ws == 2:
            return sl in ("weapon 1", "weapon 2")
        return False

    # 2. 构造批量变体列表：目标本体 + 0成本换天赋 + 0成本换技能 + 逐件换装
    batch_xmls: list[str] = [tgt_xml]
    batch_tags: list[tuple[str, Any]] = [("target", None)]

    try:
        tree_xml = xml_mutator.swap_tree(my_xml, tgt_xml)
        batch_xmls.append(tree_xml)
        batch_tags.append(("tree", None))
    except Exception:
        pass

    try:
        skills_xml = xml_mutator.swap_skills(my_xml, tgt_xml)
        batch_xmls.append(skills_xml)
        batch_tags.append(("skills", None))
    except Exception:
        pass

    my_items_by_slot = {it["slot"]: it for it in (my_parsed.get("equippedItems") or [])}
    my_jewels_by_slot = {it["slot"]: it for it in (my_parsed.get("jewels") or [])}
    my_jewels_by_name = {
        (it.get("name") or it.get("baseType") or "").lower(): it
        for it in (my_parsed.get("jewels") or [])
        if (it.get("name") or it.get("baseType"))
    }
    identical_slots: list[dict[str, Any]] = []
    unmatched_jewels: list[dict[str, Any]] = []

    for tgt_item in tgt_parsed.get("equippedItems") or []:
        slot = tgt_item["slot"]
        my_item = my_items_by_slot.get(slot)
        if my_item and _norm_item_text(my_item.get("rawText", "")) == _norm_item_text(tgt_item.get("rawText", "")):
            identical_slots.append(tgt_item)
            continue
        swapped_xml = xml_mutator.swap_slot_item(my_xml, slot, tgt_item["rawText"])
        batch_xmls.append(swapped_xml)
        batch_tags.append(("slot", (tgt_item, my_item, swapped_xml)))

    for tgt_jewel in tgt_parsed.get("jewels") or []:
        slot = tgt_jewel["slot"]
        node_id = str(tgt_jewel.get("nodeId") or "")
        j_name_key = (tgt_jewel.get("name") or tgt_jewel.get("baseType") or "").lower()
        my_jewel = my_jewels_by_slot.get(slot) or (my_jewels_by_name.get(j_name_key) if j_name_key else None)
        if my_jewel and _norm_item_text(my_jewel.get("rawText", "")) == _norm_item_text(tgt_jewel.get("rawText", "")):
            identical_slots.append(tgt_jewel)
            continue

        if node_id:
            swapped_xml, ok_swap = xml_mutator.swap_tree_jewel(my_xml, node_id, tgt_jewel["rawText"])
            if not ok_swap:
                unmatched_jewels.append(tgt_jewel)
                continue
        else:
            swapped_xml = xml_mutator.swap_slot_item(my_xml, slot, tgt_jewel["rawText"])
        batch_xmls.append(swapped_xml)
        batch_tags.append(("slot", (tgt_jewel, my_jewel, swapped_xml)))

    batch_results = pool.simulate_batch(batch_xmls, lock_dps_key=lock_key, include_audit=False)

    # 2.5 对单件试穿触发属性/精魂 fail 红线的槽位，补跑一次「门槛补偿对照」避免误丢高价值升级件
    comp_jobs: list[tuple[int, str]] = []
    for idx, ((tag, meta), sim) in enumerate(zip(batch_tags, batch_results)):
        if tag != "slot" or not isinstance(sim, dict):
            continue
        redlines_raw = evaluate_redlines(sim, baseline=my_stats)
        has_threshold_fail = any(
            r.get("severity") == "fail" and r.get("kind") in ("attribute", "spirit")
            for r in redlines_raw
        )
        if not has_threshold_fail:
            continue
        str_def = max(0, int(round(_num(sim.get("reqStr")) - _num(sim.get("str")))))
        dex_def = max(0, int(round(_num(sim.get("reqDex")) - _num(sim.get("dex")))))
        int_def = max(0, int(round(_num(sim.get("reqInt")) - _num(sim.get("int")))))
        sp_unres = _num(sim.get("spiritUnreserved"))
        sp_def = max(0, int(round(-sp_unres + 0.5))) if sp_unres < 0 else 0
        if str_def or dex_def or int_def or sp_def:
            _, _, swapped_xml = meta
            comp_xml = xml_mutator.inject_threshold_compensation(
                swapped_xml,
                str_deficit=str_def,
                dex_deficit=dex_def,
                int_deficit=int_def,
                spirit_deficit=sp_def,
            )
            comp_jobs.append((idx, comp_xml))

    comp_sim_by_idx: dict[int, dict[str, Any]] = {}
    if comp_jobs:
        comp_results = pool.simulate_batch([x for _, x in comp_jobs], lock_dps_key=lock_key, include_audit=False)
        for (idx, _), csim in zip(comp_jobs, comp_results):
            if isinstance(csim, dict) and not csim.get("error"):
                comp_sim_by_idx[idx] = csim

    target_stats: dict[str, Any] = {}
    zero_cost_steps: list[dict[str, Any]] = []
    slot_upgrades: list[dict[str, Any]] = []

    for idx, ((tag, meta), sim) in enumerate(zip(batch_tags, batch_results)):
        if tag == "target":
            target_stats = sim
            continue

        d_dps = _pct_delta(_num(sim.get("dps")), my_dps)
        d_ehp = _pct_delta(_num(sim.get("ehp")), my_ehp)
        d_ms = round(_num(sim.get("moveSpeed")) - my_ms, 1)
        redlines = evaluate_redlines(sim, baseline=my_stats)

        if tag == "tree":
            zero_cost_steps.append({
                "slot": "Passive Tree",
                "kind": "tree",
                "label": "对齐目标天赋树与升华（0 成本 / 金币洗点）",
                "deltaDpsPct": d_dps,
                "deltaEhpPct": d_ehp,
                "deltaMoveSpeedPct": d_ms,
                "redlines": redlines,
                "priceDivine": 0.0,
                "dpsPerDivine": None,
                "en": {"label": "Match Target Passive Tree & Ascendancy (0 Cost / Gold Respec)"},
            })
        elif tag == "skills":
            zero_cost_steps.append({
                "slot": "Skills & Gems",
                "kind": "skills",
                "label": "对齐目标技能连法与 20/20 宝石品质",
                "deltaDpsPct": d_dps,
                "deltaEhpPct": d_ehp,
                "deltaMoveSpeedPct": d_ms,
                "redlines": redlines,
                "priceDivine": 0.0,
                "dpsPerDivine": None,
                "en": {"label": "Match Target Skill Links & 20/20 Gem Quality"},
            })
        elif tag == "slot":
            tgt_item, my_item, _ = meta
            slot = tgt_item["slot"]
            inactive_ws = _is_inactive_ws_slot(slot)
            off_speed_delta = 0.0

            # 副武器组独立效用评估：比较非激活武器组的独立面板（DPS / EHP / 攻速工具频率）
            if inactive_ws:
                off_key = "ws1" if my_active_ws == 2 else "ws2"
                base_off = my_ws_stats.get(off_key) if isinstance(my_ws_stats, dict) else None
                sim_ws = sim.get("weaponSetStats") if isinstance(sim.get("weaponSetStats"), dict) else {}
                new_off = sim_ws.get(off_key) if isinstance(sim_ws, dict) else None
                if isinstance(base_off, dict) and isinstance(new_off, dict):
                    off_d_dps = _pct_delta(_num(new_off.get("dps")), _num(base_off.get("dps")))
                    off_d_ehp = _pct_delta(_num(new_off.get("ehp")), _num(base_off.get("ehp")))
                    off_speed_delta = _pct_delta(_num(new_off.get("speed")), _num(base_off.get("speed")))
                    if abs(d_dps) < 0.05 and abs(off_d_dps) >= 0.5:
                        d_dps = off_d_dps
                    if abs(d_ehp) < 0.05 and abs(off_d_ehp) >= 0.5:
                        d_ehp = off_d_ehp

            # 若属性/精魂红线断裂导致裸试穿收益暴跌，用补足门槛后的实算潜力防漏推（同时保留红线提示）
            comp_dps: float | None = None
            comp_ehp: float | None = None
            csim = comp_sim_by_idx.get(idx)
            if csim is not None:
                comp_dps = _pct_delta(_num(csim.get("dps")), my_dps)
                comp_ehp = _pct_delta(_num(csim.get("ehp")), my_ehp)
                if comp_dps > d_dps + 3.0:
                    d_dps = comp_dps
                if comp_ehp > d_ehp + 3.0:
                    d_ehp = comp_ehp

            pinfo = _lookup_slot_prices(slot, price_snapshot, price_overrides)
            price_d = pinfo["priceDivine"]
            dps_per_d = round(d_dps / price_d, 2) if (price_d and price_d > 0) else None
            tgt_name = tgt_item.get("name") or tgt_item.get("baseType")
            step_entry: dict[str, Any] = {
                "slot": slot,
                "kind": "slot_swap",
                "label": f"试穿 {slot}：{tgt_name}",
                "deltaDpsPct": d_dps,
                "deltaEhpPct": d_ehp,
                "deltaMoveSpeedPct": d_ms,
                "redlines": redlines,
                "dpsPerDivine": dps_per_d,
                "myItemName": (my_item.get("name") or my_item.get("baseType")) if my_item else "(空槽位)",
                "targetItemName": tgt_name,
                "targetBaseType": tgt_item.get("baseType") or "",
                "targetRarity": tgt_item.get("rarity") or "",
                "targetMods": (tgt_item.get("mods") or [])[:10],
                "isIdentical": False,
                "en": {
                    "label": f"Equip {slot}: {tgt_name}",
                    "myItemName": (my_item.get("name") or my_item.get("baseType")) if my_item else "(Empty Slot)",
                },
                **pinfo,
            }
            if inactive_ws:
                step_entry["inactiveWeaponSet"] = True
                if abs(off_speed_delta) >= 0.5:
                    step_entry["offSetSpeedDeltaPct"] = off_speed_delta
            if comp_dps is not None:
                step_entry["compensatedDeltaDpsPct"] = comp_dps
                step_entry["compensatedDeltaEhpPct"] = comp_ehp
            slot_upgrades.append(step_entry)

    for tgt_item in unmatched_jewels:
        slot = tgt_item["slot"]
        pinfo = _lookup_slot_prices(slot, price_snapshot, price_overrides)
        tgt_name = tgt_item.get("name") or tgt_item.get("baseType")
        slot_upgrades.append({
            "slot": slot,
            "kind": "slot_swap",
            "label": f"补配珠宝 {slot}：{tgt_name}",
            "deltaDpsPct": 0.0,
            "deltaEhpPct": 0.0,
            "deltaMoveSpeedPct": 0.0,
            "redlines": [{
                "kind": "attribute",
                "severity": "warn",
                "message": "当前天赋尚未点出该珠宝孔（建议先对齐天赋树再测算）",
                "en": {"message": "Jewel socket not allocated in your tree yet (match passive tree first)"},
            }],
            "dpsPerDivine": None,
            "myItemName": "(未点出该孔位)",
            "targetItemName": tgt_name,
            "targetBaseType": tgt_item.get("baseType") or "",
            "targetRarity": tgt_item.get("rarity") or "",
            "targetMods": (tgt_item.get("mods") or [])[:10],
            "isIdentical": False,
            "en": {
                "label": f"Socket Jewel {slot}: {tgt_name}",
                "myItemName": "(Socket Unallocated)",
            },
            **pinfo,
        })

    for tgt_item in identical_slots:
        slot = tgt_item["slot"]
        pinfo = _lookup_slot_prices(slot, price_snapshot, price_overrides)
        slot_upgrades.append({
            "slot": slot,
            "kind": "slot_swap",
            "label": f"{slot} 与目标一致",
            "deltaDpsPct": 0.0,
            "deltaEhpPct": 0.0,
            "deltaMoveSpeedPct": 0.0,
            "redlines": [],
            "dpsPerDivine": None,
            "myItemName": tgt_item.get("name") or tgt_item.get("baseType"),
            "targetItemName": tgt_item.get("name") or tgt_item.get("baseType"),
            "targetBaseType": tgt_item.get("baseType") or "",
            "targetRarity": tgt_item.get("rarity") or "",
            "targetMods": (tgt_item.get("mods") or [])[:10],
            "isIdentical": True,
            "en": {
                "label": f"{slot} matches target",
            },
            **pinfo,
        })

    # 按综合提升降序排列（相同装备放最后）
    slot_upgrades.sort(
        key=lambda x: (
            not x.get("isIdentical", False),
            x["deltaDpsPct"] + 0.5 * x["deltaEhpPct"],
        ),
        reverse=True,
    )

    return {
        "mySummary": _brief_summary(my_parsed),
        "targetSummary": _brief_summary(tgt_parsed),
        "myStats": my_stats,
        "targetStats": target_stats,
        "myRedlines": evaluate_redlines(my_stats),
        "treeDiff": diff_passive_tree(my_parsed, tgt_parsed),
        "skillsDiff": diff_skills(my_parsed, tgt_parsed),
        "zeroCostSteps": zero_cost_steps,
        "slotUpgrades": slot_upgrades,
        "elapsedMs": round((time.time() - t0) * 1000.0, 1),
    }


def analyze_slot_affixes(
    my_code_or_xml: str,
    target_code_or_xml: str,
    slot_name: str,
    pool: Any,
) -> dict[str, Any]:
    """将目标槽位装备穿到自身后，逐条剥离原生词缀并实算每条词缀的 DPS/EHP/红线贡献。"""
    t0 = time.time()
    my_xml, _ = runefix.normalize_runes(pob_parser.decode_pob(my_code_or_xml))
    tgt_xml, _ = runefix.normalize_runes(pob_parser.decode_pob(target_code_or_xml))

    tgt_parsed = pob_parser.parse_pob_xml(tgt_xml)
    all_target_items = list(tgt_parsed.get("equippedItems") or []) + list(tgt_parsed.get("jewels") or [])
    tgt_item = next(
        (it for it in all_target_items if it["slot"].lower() == slot_name.lower()),
        None,
    )
    if tgt_item is None:
        raise ValueError(f"目标 BD 中找不到槽位：{slot_name}")

    real_slot = tgt_item["slot"]
    node_id = str(tgt_item.get("nodeId") or "")
    if node_id:
        swapped_xml, ok_swap = xml_mutator.swap_tree_jewel(my_xml, node_id, tgt_item["rawText"])
        if not ok_swap:
            raise ValueError(f"当前天赋尚未点出珠宝孔 {real_slot}，无法剥离测试词缀")
        swapped_parsed = pob_parser.parse_pob_xml(swapped_xml)
        swapped_item = next(
            (it for it in (swapped_parsed.get("jewels") or []) if it.get("nodeId") == node_id or it["slot"] == real_slot),
            None,
        ) or next((it for it in (swapped_parsed.get("jewels") or []) if (it.get("name") or "") == (tgt_item.get("name") or "")), None)
        if swapped_item is None:
            raise ValueError(f"珠宝 {real_slot} 注入后未能定位 itemId")
    else:
        swapped_xml = xml_mutator.swap_slot_item(my_xml, real_slot, tgt_item["rawText"])
        swapped_parsed = pob_parser.parse_pob_xml(swapped_xml)
        swapped_item = next(
            it for it in list(swapped_parsed["equippedItems"]) + list(swapped_parsed["jewels"])
            if it["slot"] == real_slot
        )
    new_item_id = swapped_item["itemId"]

    native_mods = [m for m in (tgt_item.get("mods") or []) if m and not re.search(r"\(rune\)|\{rune\}", m, re.I)]
    deep = affix_analyzer.analyze_item_deeply(tgt_item, tgt_parsed)
    deep_by_line: dict[str, list[dict[str, Any]]] = {}
    for da in deep.get("affixes") or []:
        deep_by_line.setdefault(str(da.get("line") or "").lower(), []).append(da)
    deep_cursor: dict[str, int] = {}

    # 1. 基线（我的原装 + 穿上目标件）
    base_sim = pool.simulate(my_xml, include_audit=False)
    lock_key = base_sim.get("dpsSource") or None

    batch_xmls: list[str] = [swapped_xml]
    tested_mods: list[str] = []

    for mod_line in native_mods:
        try:
            if xml_mutator.extract_granted_skill(mod_line):
                mutated = xml_mutator.ablate_item_granted_skill(swapped_xml, new_item_id, real_slot, mod_line)
            else:
                mutated = xml_mutator.remove_item_mod(swapped_xml, new_item_id, mod_line)
            batch_xmls.append(mutated)
            tested_mods.append(mod_line)
        except ValueError:
            continue

    # 额外并发跑 1 次「拔掉全部符文」消融，量化符文插槽的真实贡献
    has_rune_ablation = False
    if tgt_item.get("socketedRunes"):
        try:
            no_rune_xml = xml_mutator.strip_item_runes(swapped_xml, new_item_id)
            batch_xmls.append(no_rune_xml)
            has_rune_ablation = True
        except ValueError:
            has_rune_ablation = False

    sims = pool.simulate_batch(batch_xmls, lock_dps_key=lock_key, include_audit=False)
    swapped_sim = sims[0]
    mod_sims = sims[1 : 1 + len(tested_mods)]
    rune_sim = sims[-1] if has_rune_ablation else None

    base_dps = _num(base_sim.get("dps"))
    base_ehp = _num(base_sim.get("ehp"))
    swapped_dps = _num(swapped_sim.get("dps"))
    swapped_ehp = _num(swapped_sim.get("ehp"))
    swapped_redlines = evaluate_redlines(swapped_sim, baseline=base_sim)
    swapped_redline_msgs = {r["message"] for r in swapped_redlines}

    affixes: list[dict[str, Any]] = []
    for mod_line, msim in zip(tested_mods, mod_sims):
        # 剥离该词缀后的损失量（正值表示该词缀带来的正向贡献）
        loss_dps_pct = _pct_delta(swapped_dps, _num(msim.get("dps"))) if _num(msim.get("dps")) > 0 else 0.0
        # 用相对 swapped 的下降幅度表示直观贡献：-(after - swapped)/swapped * 100
        contrib_dps_pct = round(-_pct_delta(_num(msim.get("dps")), swapped_dps), 2)
        contrib_ehp_pct = round(-_pct_delta(_num(msim.get("ehp")), swapped_ehp), 2)

        after_redlines = evaluate_redlines(msim, baseline=swapped_sim)
        new_redlines = [r for r in after_redlines if r["message"] not in swapped_redline_msgs]
        breaks_redline = any(r["severity"] == "fail" for r in new_redlines) or any(
            r["kind"] in ("resist", "spirit", "attribute") for r in new_redlines
        )

        # 结合静态深度词条分析（识别天赋/符文联动、涂油、死词条与 PoB2 未解析核心词缀）
        k = mod_line.strip().lower()
        cands = deep_by_line.get(k) or []
        cur = deep_cursor.get(k, 0)
        dmeta = cands[min(cur, len(cands) - 1)] if cands else {}
        deep_cursor[k] = cur + 1

        is_anoint = bool(dmeta.get("isAnoint"))
        is_dead = dmeta.get("role") == "dead"
        ctx_score = int(dmeta.get("contextualScore") or 0)
        craft_diff = str(dmeta.get("craftDifficulty") or "easy")
        origin = str(dmeta.get("origin") or "explicit")
        side = str(dmeta.get("side") or "unknown")
        syn_tags = list(dmeta.get("synergyTags") or [])

        granted_info = dmeta.get("grantedSkill") if isinstance(dmeta.get("grantedSkill"), dict) else None
        is_important_granted = bool(granted_info and granted_info.get("isImportant"))

        if is_dead:
            tier = "cut"
        elif is_important_granted:
            tier = "must"
        elif contrib_dps_pct >= 4.0 or contrib_ehp_pct >= 4.0 or breaks_redline or loss_dps_pct >= 5.0:
            tier = "must"
        elif (ctx_score >= 95 and not is_anoint and origin != "implicit") and (contrib_dps_pct == 0.0 and contrib_ehp_pct == 0.0):
            # 防止 PoB2 未解析词条（或副武器组词缀如 Explicit Speed Modifier Magnitudes / Socketed Augment Effect）被误判成 cut
            tier = "must" if ctx_score >= 105 else "nice"
        elif contrib_dps_pct >= 1.2 or contrib_ehp_pct >= 1.2 or (ctx_score >= 82 and not is_anoint and origin != "implicit"):
            tier = "nice"
        else:
            tier = "cut"

        if is_anoint or origin == "implicit" or is_dead or (tgt_item.get("rarity") or "").upper() == "UNIQUE":
            budget_action = "skip"
            semi_action = "skip"
        elif tier == "must":
            budget_action = "lock"
            semi_action = "lock" if (craft_diff == "hard" or origin == "fractured") else "craft_later"
        elif tier == "nice":
            budget_action = "downgrade"
            semi_action = "lock" if origin == "fractured" else "craft_later"
        else:
            budget_action = "cut"
            semi_action = "skip"

        affixes.append({
            "line": mod_line,
            "tier": tier,
            "deltaDpsPct": contrib_dps_pct,
            "deltaEhpPct": contrib_ehp_pct,
            "triggeredRedlines": new_redlines,
            "origin": origin,
            "side": side,
            "craftDifficulty": craft_diff,
            "synergyTags": syn_tags,
            "budgetAction": budget_action,
            "semiAction": semi_action,
        })

    tier_order = {"must": 0, "nice": 1, "cut": 2}
    affixes.sort(key=lambda a: (tier_order[a["tier"]], -(abs(a["deltaDpsPct"]) + abs(a["deltaEhpPct"]))))

    socket_analysis = dict(deep.get("socketAnalysis") or {})
    if rune_sim is not None:
        r_dps = round(-_pct_delta(_num(rune_sim.get("dps")), swapped_dps), 2)
        r_ehp = round(-_pct_delta(_num(rune_sim.get("ehp")), swapped_ehp), 2)
        r_sp = round(_num(swapped_sim.get("spirit")) - _num(rune_sim.get("spirit")), 1)
        r_res_drop = round(
            max(
                0.0,
                _num(swapped_sim.get("fireRes")) - _num(rune_sim.get("fireRes")),
                _num(swapped_sim.get("coldRes")) - _num(rune_sim.get("coldRes")),
                _num(swapped_sim.get("lightningRes")) - _num(rune_sim.get("lightningRes")),
                _num(swapped_sim.get("chaosRes")) - _num(rune_sim.get("chaosRes")),
            ),
            1,
        )
        socket_analysis["runeDeltaDpsPct"] = r_dps
        socket_analysis["runeDeltaEhpPct"] = r_ehp
        socket_analysis["runeSpiritLoss"] = r_sp
        socket_analysis["runeMaxResDrop"] = r_res_drop

    # 用 PoB2 实算结果微调 budgetPlan / semiPlan 的锁词清单（仍保持去重与跳过涂油/死词条）
    budget_plan = dict(deep.get("budgetPlan") or {})
    semi_plan = dict(deep.get("semiPlan") or {})
    live_must_lockable = [a["line"] for a in affixes if a["tier"] == "must" and a["budgetAction"] == "lock"]
    live_semi_hard = [
        a["line"]
        for a in affixes
        if a["semiAction"] == "lock" and a["origin"] != "implicit"
    ]
    if live_must_lockable:
        budget_plan["lockAffixes"] = list(dict.fromkeys(live_must_lockable))[:3]
    if live_semi_hard:
        semi_plan["lockAffixes"] = list(dict.fromkeys(live_semi_hard))[:2]

    return {
        "slot": real_slot,
        "itemName": tgt_item.get("name") or tgt_item.get("baseType") or "",
        "baseType": tgt_item.get("baseType") or "",
        "rarity": tgt_item.get("rarity") or "",
        "baseDps": round(base_dps, 1),
        "baseEhp": round(base_ehp, 1),
        "swappedDps": round(swapped_dps, 1),
        "swappedEhp": round(swapped_ehp, 1),
        "deltaDpsPct": _pct_delta(swapped_dps, base_dps),
        "deltaEhpPct": _pct_delta(swapped_ehp, base_ehp),
        "redlines": swapped_redlines,
        "affixes": affixes,
        "mustAffixes": [a["line"] for a in affixes if a["tier"] == "must"],
        "cuttableAffixes": [a["line"] for a in affixes if a["tier"] == "cut"],
        "passiveSynergies": deep.get("passiveSynergies") or [],
        "socketAnalysis": socket_analysis,
        "budgetPlan": budget_plan,
        "semiPlan": semi_plan,
        "elapsedMs": round((time.time() - t0) * 1000.0, 1),
    }
