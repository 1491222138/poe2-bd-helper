"""多预算档位最优升级路线规划器（0 成本阶段 + 0-1 背包优化 + 分阶段排序）。"""
from __future__ import annotations

import math
from typing import Any


def _step_utility(step: dict[str, Any]) -> float:
    dps = max(0.0, float(step.get("deltaDpsPct") or 0.0))
    ehp = max(0.0, float(step.get("deltaEhpPct") or 0.0))
    ms = max(0.0, float(step.get("deltaMoveSpeedPct") or 0.0))
    off_spd = max(0.0, float(step.get("offSetSpeedDeltaPct") or 0.0)) if step.get("inactiveWeaponSet") else 0.0
    redlines = step.get("redlines") or []
    fail_cnt = sum(1 for r in redlines if r.get("severity") == "fail")
    # 对触发严重红线的单件适度降权，但不完全屏蔽（可通过补抗/补属性化解）
    penalty = fail_cnt * 2.5
    return max(0.0, dps + 0.6 * ehp + 0.25 * ms + 0.35 * off_spd - penalty)


def _knapsack_select(items: list[dict[str, Any]], budget_divine: float) -> list[dict[str, Any]]:
    """0-1 背包求解预算内综合提升最大的装备组合。"""
    if budget_divine <= 0 or not items:
        return []

    # 动态量化步长：常规预算用 0.5D 精度，大预算保持状态空间 <= 2000 确保毫秒级返回
    unit = 0.5 if budget_divine <= 500 else max(1.0, budget_divine / 1500.0)
    cap = int(math.floor(budget_divine / unit + 1e-9))
    if cap <= 0:
        return []

    weights = [max(1, int(math.ceil(float(it["priceDivine"]) / unit))) for it in items]
    values = [_step_utility(it) for it in items]

    n = len(items)
    dp = [0.0] * (cap + 1)
    keep = [[False] * (cap + 1) for _ in range(n)]

    for i in range(n):
        w = weights[i]
        v = values[i]
        if w > cap or v <= 0:
            continue
        for c in range(cap, w - 1, -1):
            cand = dp[c - w] + v
            if cand > dp[c] + 1e-9:
                dp[c] = cand
                keep[i][c] = True

    chosen: list[dict[str, Any]] = []
    c = cap
    for i in range(n - 1, -1, -1):
        if keep[i][c]:
            chosen.append(items[i])
            c -= weights[i]

    chosen.reverse()
    return chosen


def _pct_delta(base: float, after: float) -> float:
    if base <= 1e-6:
        return 0.0
    return round(((after - base) / base) * 100.0, 1)


def _apply_target_steps_to_xml(
    my_xml: str,
    steps: list[dict[str, Any]],
    target_items_by_slot: dict[str, dict[str, Any]],
    target_jewels_by_slot: dict[str, dict[str, Any]],
) -> tuple[str, list[str]]:
    """把指定阶段选中的目标装备/珠宝一次性合穿到 my_xml 上，返回 (mutated_xml, applied_slots)。"""
    from backend.engine import xml_mutator

    cur = my_xml
    applied: list[str] = []
    for st in steps:
        slot = str(st.get("slot") or "").strip()
        if not slot:
            continue
        sl = slot.lower()
        t_item = target_items_by_slot.get(sl)
        if t_item and t_item.get("rawText"):
            try:
                cur = xml_mutator.swap_slot_item(cur, t_item.get("slot") or slot, str(t_item["rawText"]))
                applied.append(slot)
                continue
            except Exception:
                pass
        t_jewel = target_jewels_by_slot.get(sl)
        if t_jewel and t_jewel.get("rawText") and t_jewel.get("nodeId"):
            try:
                cur, ok = xml_mutator.swap_tree_jewel(cur, str(t_jewel["nodeId"]), str(t_jewel["rawText"]))
                if ok:
                    applied.append(slot)
            except Exception:
                pass
    return cur, applied


def verify_roadmap_combos(
    roadmap: dict[str, Any],
    *,
    my_code: str,
    target_code: str,
    pool: Any,
    base_sim: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对 0-1 背包选出的 Phase 1 与累计 Phase 1+2 方案补跑 PoB2 合穿实算，输出真实组合 ΔDPS/ΔEHP 与红线状态。"""
    if not pool or not my_code or not target_code:
        return roadmap
    phases = roadmap.get("phases") or []
    equip_phases = [p for p in phases if int(p.get("phase") or 0) >= 1 and p.get("steps")]
    if not equip_phases:
        return roadmap

    from backend.domain import gap_analyzer
    from backend.engine import pob_parser, runefix

    try:
        my_xml, _ = runefix.normalize_runes(pob_parser.decode_pob(my_code))
        tgt_xml, _ = runefix.normalize_runes(pob_parser.decode_pob(target_code))
        tgt_parsed = pob_parser.parse_pob_xml(tgt_xml)
    except Exception:
        return roadmap

    target_items_by_slot: dict[str, dict[str, Any]] = {}
    for it in tgt_parsed.get("equippedItems") or []:
        sl = str(it.get("slot") or "").strip().lower()
        if sl and it.get("rawText"):
            target_items_by_slot[sl] = it

    target_jewels_by_slot: dict[str, dict[str, Any]] = {}
    for idx, jw in enumerate(tgt_parsed.get("jewels") or [], start=1):
        nid = str(jw.get("nodeId") or "").strip()
        sl = str(jw.get("slot") or "").strip().lower()
        if sl:
            target_jewels_by_slot[sl] = jw
        if nid:
            target_jewels_by_slot[f"jewel (node {nid})"] = jw
            target_jewels_by_slot[f"jewel #{idx}"] = jw

    # 构造累计合穿 XML（Phase 1 单独合穿；若有 Phase 2 则再跑 Phase 1+2 累计合穿）
    batch_jobs: list[tuple[str, str, list[str], float, float]] = []
    if base_sim is None:
        batch_jobs.append(("base", my_xml, [], 0.0, 0.0))

    cum_steps: list[dict[str, Any]] = []
    cum_linear_dps = 0.0
    cum_linear_ehp = 0.0
    for ph in equip_phases:
        ph_steps = list(ph.get("steps") or [])
        cum_steps.extend(ph_steps)
        cum_linear_dps += sum(float(s.get("deltaDpsPct") or 0.0) for s in ph_steps)
        cum_linear_ehp += sum(float(s.get("deltaEhpPct") or 0.0) for s in ph_steps)
        mut_xml, applied = _apply_target_steps_to_xml(
            my_xml,
            cum_steps,
            target_items_by_slot,
            target_jewels_by_slot,
        )
        if applied:
            batch_jobs.append((f"phase:{ph['phase']}", mut_xml, applied, round(cum_linear_dps, 1), round(cum_linear_ehp, 1)))

    if not any(k.startswith("phase:") for k, *_ in batch_jobs):
        return roadmap

    try:
        lock_key = str((base_sim or {}).get("dpsSource") or "") or None
        sims = pool.simulate_batch([x for _, x, *_ in batch_jobs], lock_dps_key=lock_key, include_audit=False)
    except Exception:
        return roadmap

    sim_map: dict[str, dict[str, Any]] = {}
    for (k, *_), sim in zip(batch_jobs, sims):
        if isinstance(sim, dict) and not sim.get("error"):
            sim_map[k] = sim

    eff_base = base_sim or sim_map.get("base")
    if not isinstance(eff_base, dict):
        return roadmap
    b_dps = float(eff_base.get("dps") or 0.0)
    b_ehp = float(eff_base.get("ehp") or 0.0)

    last_verified: dict[str, Any] | None = None
    for (k, _, applied, lin_dps, lin_ehp) in batch_jobs:
        if not k.startswith("phase:"):
            continue
        sim = sim_map.get(k)
        if not sim:
            continue
        pnum = int(k.split(":", 1)[1])
        real_dps = _pct_delta(b_dps, float(sim.get("dps") or 0.0))
        real_ehp = _pct_delta(b_ehp, float(sim.get("ehp") or 0.0))
        redlines = gap_analyzer.evaluate_redlines(sim, baseline=eff_base)
        all_green = len(redlines) == 0
        ver = {
            "cumulative": pnum > 1,
            "slotsApplied": applied,
            "dps": round(float(sim.get("dps") or 0.0), 1),
            "ehp": round(float(sim.get("ehp") or 0.0), 1),
            "deltaDpsPct": real_dps,
            "deltaEhpPct": real_ehp,
            "linearDeltaDpsPct": lin_dps,
            "linearDeltaEhpPct": lin_ehp,
            "dpsSynergyDiffPct": round(real_dps - lin_dps, 1),
            "redlines": redlines,
            "allGreen": all_green,
        }
        for ph in phases:
            if int(ph.get("phase") or 0) == pnum:
                ph["verifiedCombo"] = ver
                break
        last_verified = ver

    if last_verified is not None:
        roadmap["verifiedCombo"] = last_verified
    return roadmap


def plan_roadmap(
    zero_cost_steps: list[dict[str, Any]],
    slot_upgrades: list[dict[str, Any]],
    budget_divine: float,
    *,
    price_overrides: dict[str, float] | None = None,
    my_code: str | None = None,
    target_code: str | None = None,
    pool: Any = None,
    base_sim: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据当前预算档位生成分阶段升级路线（Phase 0 零成本 -> Phase 1 核心质变 -> Phase 2 稳步补强）。"""
    budget = max(0.0, float(budget_divine))
    overrides = price_overrides or {}

    # Phase 0：0 成本高收益动作（换天赋 / 换技能连法与品质）
    phase0_steps = [
        s
        for s in (zero_cost_steps or [])
        if (float(s.get("deltaDpsPct") or 0.0) > 0.5 or float(s.get("deltaEhpPct") or 0.0) > 1.0)
    ]

    # 整理有效装备升级候选（过滤相同件与负收益件）
    priced_candidates: list[dict[str, Any]] = []
    unpriced: list[dict[str, Any]] = []

    for raw in slot_upgrades or []:
        if raw.get("isIdentical"):
            continue
        step = dict(raw)
        slot = step.get("slot") or ""
        if slot in overrides and float(overrides[slot]) > 0:
            step["priceDivine"] = round(float(overrides[slot]), 2)
            step["dpsPerDivine"] = round(float(step.get("deltaDpsPct") or 0.0) / step["priceDivine"], 2)

        util = _step_utility(step)
        if util <= 0.3:
            continue

        price = step.get("priceDivine")
        if price is None or float(price) <= 0:
            unpriced.append(step)
        else:
            priced_candidates.append(step)

    chosen = _knapsack_select(priced_candidates, budget)
    chosen_slots = {s["slot"] for s in chosen}
    deferred = [s for s in priced_candidates if s["slot"] not in chosen_slots]

    # 按 ROI（单位神圣石综合收益）对选中项排序，拆分为 Phase 1 核心必买 & Phase 2 稳步补强
    chosen.sort(
        key=lambda s: _step_utility(s) / max(0.1, float(s.get("priceDivine") or 1.0)),
        reverse=True,
    )
    deferred.sort(key=lambda s: _step_utility(s), reverse=True)
    unpriced.sort(key=lambda s: _step_utility(s), reverse=True)

    phases: list[dict[str, Any]] = []
    if phase0_steps:
        phases.append({
            "phase": 0,
            "title": "Phase 0 · 零成本优先调整",
            "subtitle": "先调天赋大点/升华与主技能连法品质，不花通货立即可拿的提升",
            "costDivine": 0.0,
            "expectedDeltaDpsPct": round(sum(float(s.get("deltaDpsPct") or 0.0) for s in phase0_steps), 1),
            "expectedDeltaEhpPct": round(sum(float(s.get("deltaEhpPct") or 0.0) for s in phase0_steps), 1),
            "steps": phase0_steps,
            "en": {
                "title": "Phase 0 · Zero-Cost Quick Wins",
                "subtitle": "Adjust passive tree, ascendancy, and 20/20 gem quality first for free upgrades",
            },
        })

    if chosen:
        split_idx = max(1, min(3, math.ceil(len(chosen) / 2)))
        core_steps = chosen[:split_idx]
        follow_steps = chosen[split_idx:]

        phases.append({
            "phase": 1,
            "title": "Phase 1 · 最高性价比核心位（先买）",
            "subtitle": "单位神圣石提升最高的关键装备，优先拿下质变",
            "costDivine": round(sum(float(s["priceDivine"]) for s in core_steps), 2),
            "expectedDeltaDpsPct": round(sum(float(s.get("deltaDpsPct") or 0.0) for s in core_steps), 1),
            "expectedDeltaEhpPct": round(sum(float(s.get("deltaEhpPct") or 0.0) for s in core_steps), 1),
            "steps": core_steps,
            "en": {
                "title": "Phase 1 · Highest ROI Core Upgrades",
                "subtitle": "Best DPS/EHP per Divine spent — buy these breakpoints first",
            },
        })

        if follow_steps:
            phases.append({
                "phase": 2,
                "title": "Phase 2 · 预算内稳步补强",
                "subtitle": "在剩余预算内顺次补齐防具、抗性与次级输出槽位",
                "costDivine": round(sum(float(s["priceDivine"]) for s in follow_steps), 2),
                "expectedDeltaDpsPct": round(sum(float(s.get("deltaDpsPct") or 0.0) for s in follow_steps), 1),
                "expectedDeltaEhpPct": round(sum(float(s.get("deltaEhpPct") or 0.0) for s in follow_steps), 1),
                "steps": follow_steps,
                "en": {
                    "title": "Phase 2 · Remaining Budget Follow-Ups",
                    "subtitle": "Fill remaining defence, resistance, and secondary damage slots within budget",
                },
            })

    spent = round(sum(float(s["priceDivine"]) for s in chosen), 2)
    all_selected = phase0_steps + chosen
    total_dps = round(sum(float(s.get("deltaDpsPct") or 0.0) for s in all_selected), 1)
    total_ehp = round(sum(float(s.get("deltaEhpPct") or 0.0) for s in all_selected), 1)

    roadmap: dict[str, Any] = {
        "budgetDivine": round(budget, 2),
        "spentDivine": spent,
        "remainingDivine": round(max(0.0, budget - spent), 2),
        "expectedDeltaDpsPct": total_dps,
        "expectedDeltaEhpPct": total_ehp,
        "phases": phases,
        "deferred": deferred,
        "unpriced": unpriced,
    }
    if pool is not None and my_code and target_code and chosen:
        verify_roadmap_combos(
            roadmap,
            my_code=my_code,
            target_code=target_code,
            pool=pool,
            base_sim=base_sim,
        )
    return roadmap
