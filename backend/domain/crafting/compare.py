"""买成品 vs 买半成品加工 vs 白底自做三路决策引擎（集成 HTC 风格 MDP 策略求解 + 帕累托步骤规划）。"""
from __future__ import annotations

import json
import urllib.parse
from typing import Any

from backend.domain.crafting import affix_pool
from backend.domain.crafting.base import CraftingContext, CraftingStrategy
from backend.domain.crafting.htc_solver import (
    HtcRawPolicyStrategy,
    HtcSemiPolicyStrategy,
    OmenTargetedStrategy,
    build_pareto_plans,
)
from backend.domain.crafting.simple_finish import (
    EssenceLockStrategy,
    RawRollStrategy,
    SemiExaltStrategy,
)
from backend.market import ninja_rates

DEFAULT_STRATEGIES: tuple[CraftingStrategy, ...] = (
    HtcSemiPolicyStrategy(),
    EssenceLockStrategy(),
    OmenTargetedStrategy(),
    SemiExaltStrategy(),
    HtcRawPolicyStrategy(),
    RawRollStrategy(),
)

_CLASS_TO_TRADE_CAT: dict[str, str] = {
    "Amulets": "accessory.amulet",
    "Rings": "accessory.ring",
    "Belts": "accessory.belt",
    "Body Armours": "armour.chest",
    "Helmets": "armour.helmet",
    "Gloves": "armour.gloves",
    "Boots": "armour.boots",
    "Quivers": "armour.quiver",
    "Shields": "armour.shield",
    "Bucklers": "armour.buckler",
    "Foci": "armour.focus",
    "Spears": "weapon.spear",
    "Bows": "weapon.bow",
    "Crossbows": "weapon.crossbow",
    "Wands": "weapon.wand",
    "Sceptres": "weapon.sceptre",
    "Staves": "weapon.staff",
    "Quarterstaves": "weapon.warstaff",
}


def _build_trade_url(
    league: str,
    item_class: str,
    base_type: str,
    *,
    mode: str = "finished",  # "finished" | "semi" | "white_base"
    ilvl: int = 82,
    stat_ids: list[str] | None = None,
) -> str:
    enc_league = urllib.parse.quote(league or "Standard")
    filters: dict[str, Any] = {}
    query: dict[str, Any] = {"status": {"option": "available"}}
    if base_type:
        query["type"] = base_type
    else:
        cat = _CLASS_TO_TRADE_CAT.get(item_class)
        if cat:
            filters["type_filters"] = {"filters": {"category": {"option": cat}}}

    if mode == "white_base":
        type_f = filters.setdefault("type_filters", {"filters": {}})["filters"]
        type_f["rarity"] = {"option": "normal"}
        type_f["ilvl"] = {"min": max(1, ilvl)}
        filters["misc_filters"] = {"filters": {"corrupted": {"option": "false"}}}
    elif mode == "semi":
        filters["misc_filters"] = {"filters": {"corrupted": {"option": "false"}}}
        if ilvl >= 75:
            type_f = filters.setdefault("type_filters", {"filters": {}})["filters"]
            type_f["ilvl"] = {"min": ilvl}

    if filters:
        query["filters"] = filters

    if mode != "white_base":
        clean_ids: list[str] = []
        for sid in stat_ids or []:
            if sid and sid not in clean_ids:
                clean_ids.append(sid)
        if clean_ids:
            query["stats"] = [{"type": "and", "filters": [{"id": sid} for sid in clean_ids[:6]]}]

    payload = {"query": query, "sort": {"price": "asc"}}
    return f"https://www.pathofexile.com/trade2/search/poe2/{enc_league}?q={urllib.parse.quote(json.dumps(payload))}"


def _clean_estimate(est: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in est.items() if not k.startswith("_")}


def compare_craft_vs_buy(
    req: dict[str, Any],
    *,
    strategies: tuple[CraftingStrategy, ...] = DEFAULT_STRATEGIES,
    allow_network_rates: bool = True,
    rates_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行「买成品 vs 买半成品加工 vs 白底自做」三路决策分析，输出严格对齐 CraftDecisionResponse。"""
    slot = str(req.get("slot") or "Rings")
    base_type = str(req.get("baseType") or "")
    league = str(req.get("league") or "Standard")
    ilvl = max(1, min(100, int(req.get("ilvl") or 82)))
    min_tier_mode = str(req.get("minTierMode") or "any").lower()
    if min_tier_mode not in ("any", "mid", "high"):
        min_tier_mode = "any"

    want_raw = [str(x).strip() for x in (req.get("wantAffixes") or []) if str(x).strip()]
    exist_raw = [str(x).strip() for x in (req.get("existingAffixes") or []) if str(x).strip()]

    semi_price = req.get("semiBasePrice")
    semi_base_price = float(semi_price) if isinstance(semi_price, (int, float)) and semi_price >= 0 else None

    white_price = req.get("whiteBasePrice")
    white_base_price = float(white_price) if isinstance(white_price, (int, float)) and white_price >= 0 else None

    fin_price = req.get("finishedBuyPrice")
    finished_buy_price = float(fin_price) if isinstance(fin_price, (int, float)) and fin_price > 0 else None

    ess_name = str(req.get("essenceName") or "").strip() or None
    ess_price_raw = req.get("essencePrice")
    essence_price = float(ess_price_raw) if isinstance(ess_price_raw, (int, float)) and ess_price_raw >= 0 else None

    rates = rates_override or ninja_rates.fetch_ninja_rates(league, allow_network=allow_network_rates)
    pool = affix_pool.build_slot_pool(slot, ilvl=ilvl, base_type=base_type)
    item_class = pool["itemClass"]

    matched_wanted = [affix_pool.match_affix_to_pool(w, pool) for w in want_raw]
    matched_existing = [affix_pool.match_affix_to_pool(e, pool) for e in exist_raw]
    available_essences = affix_pool.list_essences_for_slot(slot, base_type=base_type, rates=rates)

    # 补充每个目标词缀在当前 T 级要求下的合格 T 数
    for mw in matched_wanted:
        if mw.get("matched") and mw.get("group"):
            side_pool = pool["prefixes"] if mw["side"] == "prefix" else pool["suffixes"]
            g_entry = side_pool.get(mw["group"])
            if g_entry:
                _, q_tiers = affix_pool.compute_qualifying_target_weight(
                    g_entry,
                    min_tier_mode=min_tier_mode,
                    desired_value=mw.get("desiredValue"),
                )
                mw["qualifyingTiers"] = q_tiers

    fractured_raw = str(req.get("fracturedAffix") or "").strip()
    fractured_group: str | None = None
    if fractured_raw:
        mf = affix_pool.match_affix_to_pool(fractured_raw, pool)
        if mf.get("matched") and mf.get("group"):
            fractured_group = str(mf["group"])

    p_used = sum(1 for e in matched_existing if e.get("matched") and e.get("side") == "prefix")
    s_used = sum(1 for e in matched_existing if e.get("matched") and e.get("side") == "suffix")
    open_prefixes = max(0, 3 - p_used)
    open_suffixes = max(0, 3 - s_used)

    ctx = CraftingContext(
        slot=slot,
        item_class=item_class,
        ilvl=ilvl,
        pool=pool,
        matched_wanted=matched_wanted,
        matched_existing=matched_existing,
        semi_base_price=semi_base_price,
        finished_buy_price=finished_buy_price,
        rates=rates,
        available_essences=available_essences,
        essence_name=ess_name,
        essence_price=essence_price,
        white_base_price=white_base_price,
        min_tier_mode=min_tier_mode,
        fractured_group=fractured_group,
    )

    raw_estimates = [s.estimate(ctx) for s in strategies]

    semi_mdp = next((e.get("_rawMdp") for e in raw_estimates if e["strategy"] == "htc_policy"), None)
    raw_mdp = next((e.get("_rawMdp") for e in raw_estimates if e["strategy"] == "htc_raw_policy"), None)
    estimates = [_clean_estimate(e) for e in raw_estimates]

    # 生成三档市集直达链接
    want_stat_ids = [str(x.get("tradeStatId") or "") for x in matched_wanted if x.get("matched") and x.get("tradeStatId")]
    exist_stat_ids = [str(x.get("tradeStatId") or "") for x in matched_existing if x.get("matched") and x.get("tradeStatId")]
    finished_url = _build_trade_url(league, item_class, base_type, mode="finished", ilvl=ilvl, stat_ids=want_stat_ids)
    semi_url = _build_trade_url(league, item_class, base_type, mode="semi", ilvl=ilvl, stat_ids=exist_stat_ids)
    white_base_url = _build_trade_url(league, item_class, base_type, mode="white_base", ilvl=ilvl)
    htc_url = "https://www.poe2htc.com/"

    # 提取半成品最优方案 & 白底最优方案
    semi_candidates = [
        e for e in estimates
        if e["strategy"] in ("htc_policy", "semi_exalt", "essence_lock", "omen_targeted")
        and e.get("expectedCostDivine") is not None
        and e["hitProbability"] > 0
    ]
    best_semi = min(semi_candidates, key=lambda e: float(e["expectedCostDivine"])) if semi_candidates else None

    htc_raw = next(
        (e for e in estimates if e["strategy"] == "htc_raw_policy" and e.get("expectedCostDivine") is not None and e["hitProbability"] > 0),
        None,
    )
    raw_fallback = next(
        (e for e in estimates if e["strategy"] == "raw_roll" and e.get("expectedCostDivine") is not None and e["hitProbability"] > 0),
        None,
    )
    best_raw = htc_raw or raw_fallback

    # 帕累托前沿配方规划
    used_groups = {e["group"] for e in matched_existing if e.get("matched") and e.get("group")}
    all_targets = []
    seen_t: set[str] = set()
    for w in matched_wanted:
        if w.get("matched") and w.get("group") and w["group"] not in seen_t:
            seen_t.add(w["group"])
            all_targets.append(w)
    missing_targets = [w for w in all_targets if w["group"] not in used_groups]

    pareto_plans = build_pareto_plans(
        ctx,
        missing_targets=missing_targets,
        all_targets=all_targets,
        used_groups=used_groups,
        p_open=open_prefixes,
        s_open=open_suffixes,
        semi_base_cost=semi_base_price if semi_base_price is not None else 0.0,
        white_base_cost=white_base_price if white_base_price is not None else 0.05,
        semi_mdp=semi_mdp,
        raw_mdp=raw_mdp,
    )

    # === 综合三路决策（买成品 vs 半成品加工 vs 白底自做） ===
    # 说明：半成品/白底需要相比成品有至少 15% 的安全边际（抵消方差与操作时间）才推荐自己做；
    #       白底仅在明确比成品便宜 ≥20% 且也优于半成品（或半成品不可行/未填底子价）时推荐。
    recommended = "inconclusive"
    summary = ""
    summary_en = ""
    best_action: dict[str, Any] = {
        "mode": "inconclusive",
        "headline": "补全参考价后自动生成买/做最优方案",
        "saveDivine": None,
        "expectedDivine": None,
        "p80Divine": None,
        "tradeUrl": finished_url,
        "tradeButtonLabel": "去市集看成品价",
        "steps": [],
    }

    semi_exp = float(best_semi["expectedCostDivine"]) if (best_semi and semi_base_price is not None) else float("inf")
    semi_p80 = float(best_semi["p80CostDivine"]) if (best_semi and best_semi.get("p80CostDivine") is not None) else None
    raw_exp = float(best_raw["expectedCostDivine"]) if best_raw else float("inf")
    raw_p80 = float(best_raw["p80CostDivine"]) if (best_raw and best_raw.get("p80CostDivine") is not None) else None

    if finished_buy_price is None:
        # 尚未填成品价：对比半成品 vs 白底给出做装期望
        if best_semi and (semi_base_price is not None or not best_raw):
            recommended = "inconclusive"
            best_label_en = (best_semi.get("en") or {}).get("label") or best_semi["label"]
            summary = (
                f"当前最优加工方案为「{best_semi['label']}」，期望成本约 {best_semi['expectedCostDivine']} D"
                f"（P80 风险预算 {best_semi['p80CostDivine']} D）；填入成品直买价即可自动判定买还是做。"
            )
            summary_en = (
                f"Best craft path is \"{best_label_en}\": expected ~{best_semi['expectedCostDivine']} D "
                f"(P80 {best_semi['p80CostDivine']} D); enter finished price for final verdict."
            )
            best_action = {
                "mode": "inconclusive",
                "headline": f"半成品加工约 {best_semi['expectedCostDivine']} D（填成品价即可比价）",
                "saveDivine": None,
                "expectedDivine": best_semi["expectedCostDivine"],
                "p80Divine": semi_p80,
                "tradeUrl": semi_url,
                "tradeButtonLabel": "去市集查半成品底子",
                "steps": best_semi.get("recipeSteps") or [],
            }
        elif best_raw:
            summary = f"白底从零打造最优期望约 {best_raw['expectedCostDivine']} D（P80 {best_raw['p80CostDivine']} D）；填入成品价即可比价。"
            summary_en = f"Raw base optimal craft expected ~{best_raw['expectedCostDivine']} D; enter finished price to compare."
            best_action = {
                "mode": "inconclusive",
                "headline": f"白底自做约 {best_raw['expectedCostDivine']} D（填成品价即可比价）",
                "saveDivine": None,
                "expectedDivine": best_raw["expectedCostDivine"],
                "p80Divine": raw_p80,
                "tradeUrl": white_base_url,
                "tradeButtonLabel": "去市集查白底底材",
                "steps": best_raw.get("recipeSteps") or [],
            }
        else:
            summary = "请填入有效目标词缀与参考价以生成三路对比结论。"
            summary_en = "Enter valid target affixes and reference prices to compare."
    else:
        # 具备成品直买价：三路同台 PK
        semi_wins_buy = best_semi is not None and semi_base_price is not None and semi_exp <= finished_buy_price * 0.85
        # 白底需比成品便宜 >= 20%，且当用户提供了半成品底子价时还要比半成品更省（或半成品不可行）
        raw_allowed = white_base_price is not None or (semi_base_price is None and not semi_wins_buy)
        raw_wins_buy = (
            best_raw is not None
            and raw_allowed
            and raw_exp <= finished_buy_price * 0.80
            and (not semi_wins_buy or raw_exp < semi_exp * 0.92)
        )

        if raw_wins_buy and best_raw is not None:
            recommended = "craft_raw"
            save = round(finished_buy_price - raw_exp, 2)
            summary = (
                f"推荐「买白底从零自做（{best_raw['label']}）」：期望 {raw_exp} D 显著低于成品 {finished_buy_price} D"
                f"（预计省 {save} D），建议预留 P80 预算 {raw_p80} D。"
            )
            summary_en = (
                f"Recommended: Craft from White Base ({best_raw['label']}) — expected {raw_exp} D vs finished {finished_buy_price} D "
                f"(save ~{save} D, P80 {raw_p80} D)."
            )
            best_action = {
                "mode": "craft_raw",
                "headline": f"白底自做最省 · 期望 {raw_exp} D（比成品省 {save} D）",
                "saveDivine": save,
                "expectedDivine": raw_exp,
                "p80Divine": raw_p80,
                "tradeUrl": white_base_url,
                "tradeButtonLabel": f"去市集买 iLvl {ilvl}+ 白底",
                "steps": best_raw.get("recipeSteps") or [],
            }
        elif semi_wins_buy and best_semi is not None:
            recommended = "craft_semi"
            save = round(finished_buy_price - semi_exp, 2)
            best_label_en = (best_semi.get("en") or {}).get("label") or best_semi["label"]
            summary = (
                f"推荐「买半成品加工（{best_semi['label']}）」：期望总价 {semi_exp} D 低于成品直买 {finished_buy_price} D"
                f"（预计省 {save} D），请预留 P80 防连黑预算 {semi_p80} D。"
            )
            summary_en = (
                f"Recommended: Craft Semi-Base ({best_label_en}) — expected {semi_exp} D vs finished {finished_buy_price} D "
                f"(save ~{save} D), keep P80 budget {semi_p80} D."
            )
            best_action = {
                "mode": "craft_semi",
                "headline": f"买半成品加工最省 · 期望 {semi_exp} D（省 {save} D）",
                "saveDivine": save,
                "expectedDivine": semi_exp,
                "p80Divine": semi_p80,
                "tradeUrl": semi_url,
                "tradeButtonLabel": "去市集买半成品底子",
                "steps": best_semi.get("recipeSteps") or [
                    {
                        "step": 1,
                        "action": f"市集买非腐化半成品底子（约 {semi_base_price} D）",
                        "costDivine": semi_base_price,
                        "detail": "锁核心难出词缀并保留足够空位。",
                    },
                    {
                        "step": 2,
                        "action": best_semi["label"],
                        "detail": best_semi.get("stepsNote") or "",
                    },
                ],
            }
        else:
            recommended = "buy_finished"
            alt_parts: list[str] = []
            if best_semi and semi_base_price is not None:
                alt_parts.append(f"半成品加工期望 {semi_exp} D (P80 {semi_p80} D)")
            if best_raw:
                alt_parts.append(f"白底自做期望 {raw_exp} D")
            alt_text = "、".join(alt_parts) if alt_parts else "自做条件不可行"
            summary = f"推荐「直接买成品（{finished_buy_price} D）」：{alt_text}，省不出足够安全边际且有炸底子方差风险。"
            summary_en = f"Recommended: Buy Finished ({finished_buy_price} D) — crafting offers insufficient margin against variance."
            best_action = {
                "mode": "buy_finished",
                "headline": f"直接买成品最省心 · {finished_buy_price} D（零炸底子风险）",
                "saveDivine": (
                    round(min(semi_exp, raw_exp) - finished_buy_price, 2)
                    if min(semi_exp, raw_exp) < float("inf")
                    else None
                ),
                "expectedDivine": finished_buy_price,
                "p80Divine": finished_buy_price,
                "tradeUrl": finished_url,
                "tradeButtonLabel": "直达市集买成品",
                "steps": [
                    {
                        "step": 1,
                        "action": f"直达市集筛选目标词缀，按 {finished_buy_price} D 左右入手成品",
                        "costDivine": finished_buy_price,
                        "successChance": 1.0,
                        "detail": "成品直买零方差，买了即可直接穿戴。",
                    }
                ],
            }

    policy_notes: list[str] = []
    if semi_mdp and semi_mdp.get("feasible"):
        policy_notes.append(
            f"半成品 MDP 最优期望 {semi_mdp['expectedCostDivine']} D"
            + ("（启用了预兆剥离救底子）" if semi_mdp.get("recoveryUsed") else "（歪了止损换底更省）")
        )
    if raw_mdp and raw_mdp.get("feasible"):
        policy_notes.append(f"白底自做 MDP 最优期望 {raw_mdp['expectedCostDivine']} D（P80 {raw_mdp['p80CostDivine']} D）")

    policy_summary = {
        "semiExpectedDivine": semi_mdp["expectedCostDivine"] if (semi_mdp and semi_mdp.get("feasible")) else None,
        "semiP80Divine": semi_mdp["p80CostDivine"] if (semi_mdp and semi_mdp.get("feasible")) else None,
        "rawExpectedDivine": raw_mdp["expectedCostDivine"] if (raw_mdp and raw_mdp.get("feasible")) else None,
        "rawP80Divine": raw_mdp["p80CostDivine"] if (raw_mdp and raw_mdp.get("feasible")) else None,
        "recoveryUsed": bool(
            (semi_mdp and semi_mdp.get("recoveryUsed")) or (raw_mdp and raw_mdp.get("recoveryUsed"))
        ),
        "notes": policy_notes,
    }

    return {
        "slot": slot,
        "itemClass": item_class,
        "ilvl": ilvl,
        "minTierMode": min_tier_mode,
        "recommended": recommended,
        "summary": summary,
        "finishedBuyPrice": finished_buy_price,
        "semiBasePrice": semi_base_price,
        "whiteBasePrice": white_base_price,
        "openPrefixes": open_prefixes,
        "openSuffixes": open_suffixes,
        "matchedWanted": matched_wanted,
        "matchedExisting": matched_existing,
        "estimates": estimates,
        "rates": rates,
        "finishedTradeUrl": finished_url,
        "semiTradeUrl": semi_url,
        "whiteBaseTradeUrl": white_base_url,
        "htcExternalUrl": htc_url,
        "bestAction": best_action,
        "paretoPlans": pareto_plans,
        "policySummary": policy_summary,
        "en": {
            "summary": summary_en,
        },
    }
