"""V1 轻量化半成品做装策略实现（精确无放回前/后缀空位状态 DP）：
1. SemiExaltStrategy   —— 已有半成品补崇高（锁定已有前后缀占用与词缀组）
2. EssenceLockStrategy —— 精华定向补位（基于 PoB2 Essence.lua 槽位映射 + poe.ninja 精华价）
3. RawRollStrategy     —— 白底/从零重做参考基线
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from backend.domain.crafting.base import CraftingContext, CraftingStrategy
from backend.market.ninja_rates import lookup_essence_price


def _p80_attempts(prob: float) -> int | None:
    if prob <= 1e-9:
        return None
    if prob >= 0.999999:
        return 1
    return max(1, int(math.ceil(math.log(0.2) / math.log(1.0 - prob))))


def _solve_exalt_dp(
    pool: dict[str, Any],
    used_groups: set[str],
    p_open: int,
    s_open: int,
    missing_targets: list[dict[str, Any]],
) -> tuple[float, float]:
    """精确无放回状态 DP：计算在剩余 p_open 前缀 / s_open 后缀空位中命中全部 missing_targets 的概率与单轮期望消耗崇高数。"""
    if not missing_targets:
        return 1.0, 0.0

    need_p = [t for t in missing_targets if t["side"] == "prefix"]
    need_s = [t for t in missing_targets if t["side"] == "suffix"]
    if len(need_p) > p_open or len(need_s) > s_open:
        return 0.0, 0.0

    # 限制最多计算 4 条缺失目标词缀（防止极端输入指数膨胀）
    targets = missing_targets[:4]
    target_groups = {t["group"] for t in targets}

    prefix_pool = pool["prefixes"]
    suffix_pool = pool["suffixes"]

    target_weights = [
        float((prefix_pool if t["side"] == "prefix" else suffix_pool)[t["group"]]["weight"])
        for t in targets
    ]
    target_sides = [t["side"] for t in targets]

    miss_p_weights = [
        float(g["weight"]) for k, g in prefix_pool.items() if k not in used_groups and k not in target_groups
    ]
    miss_s_weights = [
        float(g["weight"]) for k, g in suffix_pool.items() if k not in used_groups and k not in target_groups
    ]
    total_miss_p = sum(miss_p_weights)
    total_miss_s = sum(miss_s_weights)
    avg_miss_p = (total_miss_p / len(miss_p_weights)) if miss_p_weights else 0.0
    avg_miss_s = (total_miss_s / len(miss_s_weights)) if miss_s_weights else 0.0

    full_mask = (1 << len(targets)) - 1
    init_p_open = p_open
    init_s_open = s_open

    @lru_cache(maxsize=None)
    def dp(pr: int, sr: int, mask: int) -> tuple[float, float]:
        if mask == full_mask:
            return 1.0, 0.0

        # 检查剩余未命中目标是否仍放得下
        rem_need_p = sum(1 for i in range(len(targets)) if not (mask & (1 << i)) and target_sides[i] == "prefix")
        rem_need_s = sum(1 for i in range(len(targets)) if not (mask & (1 << i)) and target_sides[i] == "suffix")
        if rem_need_p > pr or rem_need_s > sr:
            return 0.0, 0.0

        hits_p = sum(1 for i in range(len(targets)) if (mask & (1 << i)) and target_sides[i] == "prefix")
        hits_s = sum(1 for i in range(len(targets)) if (mask & (1 << i)) and target_sides[i] == "suffix")
        miss_drawn_p = max(0, (init_p_open - pr) - hits_p)
        miss_drawn_s = max(0, (init_s_open - sr) - hits_s)

        rem_miss_p = max(0.0, total_miss_p - miss_drawn_p * avg_miss_p) if pr > 0 else 0.0
        rem_miss_s = max(0.0, total_miss_s - miss_drawn_s * avg_miss_s) if sr > 0 else 0.0

        unhit_p_w = sum(
            target_weights[i] for i in range(len(targets)) if not (mask & (1 << i)) and target_sides[i] == "prefix"
        ) if pr > 0 else 0.0
        unhit_s_w = sum(
            target_weights[i] for i in range(len(targets)) if not (mask & (1 << i)) and target_sides[i] == "suffix"
        ) if sr > 0 else 0.0

        w_total = rem_miss_p + rem_miss_s + unhit_p_w + unhit_s_w
        if w_total <= 1e-9:
            return 0.0, 0.0

        prob = 0.0
        exp_slams = 1.0  # 本次消耗 1 颗崇高

        for i in range(len(targets)):
            if mask & (1 << i):
                continue
            side = target_sides[i]
            if (side == "prefix" and pr <= 0) or (side == "suffix" and sr <= 0):
                continue
            p_i = target_weights[i] / w_total
            sub_p, sub_s = dp(pr - (1 if side == "prefix" else 0), sr - (1 if side == "suffix" else 0), mask | (1 << i))
            prob += p_i * sub_p
            exp_slams += p_i * sub_s

        if pr > 0 and rem_miss_p > 0:
            p_mp = rem_miss_p / w_total
            sub_p, sub_s = dp(pr - 1, sr, mask)
            prob += p_mp * sub_p
            exp_slams += p_mp * sub_s

        if sr > 0 and rem_miss_s > 0:
            p_ms = rem_miss_s / w_total
            sub_p, sub_s = dp(pr, sr - 1, mask)
            prob += p_ms * sub_p
            exp_slams += p_ms * sub_s

        return prob, exp_slams

    return dp(p_open, s_open, 0)


def _extract_state(ctx: CraftingContext) -> tuple[set[str], int, int, list[dict[str, Any]]]:
    used_groups = {e["group"] for e in ctx.matched_existing if e.get("matched") and e.get("group")}
    p_used = sum(1 for e in ctx.matched_existing if e.get("matched") and e.get("side") == "prefix")
    s_used = sum(1 for e in ctx.matched_existing if e.get("matched") and e.get("side") == "suffix")
    p_open = max(0, 3 - p_used)
    s_open = max(0, 3 - s_used)

    missing: list[dict[str, Any]] = []
    seen_m: set[str] = set()
    for w in ctx.matched_wanted:
        if not w.get("matched") or not w.get("group"):
            continue
        if w["group"] in used_groups or w["group"] in seen_m:
            continue
        seen_m.add(w["group"])
        missing.append(w)

    return used_groups, p_open, s_open, missing


class SemiExaltStrategy(CraftingStrategy):
    kind = "semi_exalt"
    label = "半成品补崇高"
    label_en = "Semi-Finished + Exalt"

    def estimate(self, ctx: CraftingContext) -> dict[str, Any]:
        used_groups, p_open, s_open, missing = _extract_state(ctx)
        exalt_d = float((ctx.rates.get("currencyDivine") or {}).get("exalted", 0.01))
        base_d = ctx.semi_base_price
        unmatched = [w["input"] for w in ctx.matched_wanted if not w.get("matched")]

        if not missing and unmatched:
            return {
                "strategy": self.kind,
                "label": self.label,
                "hitProbability": 0.0,
                "expectedAttempts": None,
                "expectedCostDivine": None,
                "p80CostDivine": None,
                "brickRiskNote": f"目标词缀未能映射到 {ctx.item_class} 词缀池（{'、'.join(unmatched[:2])}），请检查槽位/基底或从下拉词缀池选择。",
                "stepsNote": "存在未识别的目标词缀。",
                "en": {
                    "label": self.label_en,
                    "brickRiskNote": f"Unmatched target affixes for {ctx.item_class} ({', '.join(unmatched[:2])}); verify slot/base or pick from dropdown.",
                    "stepsNote": "Unrecognized target affix(es).",
                },
            }

        if not missing:
            return {
                "strategy": self.kind,
                "label": self.label,
                "hitProbability": 1.0,
                "expectedAttempts": 1.0,
                "expectedCostDivine": round(base_d, 2) if base_d is not None else 0.0,
                "p80CostDivine": round(base_d, 2) if base_d is not None else 0.0,
                "brickRiskNote": "半成品已包含全部目标词缀，直接可用（无需补崇高）。",
                "stepsNote": "已有词缀已覆盖目标需求。",
                "en": {
                    "label": self.label_en,
                    "brickRiskNote": "Semi-finished base already has all target affixes — ready to use.",
                    "stepsNote": "Existing affixes already cover all targets.",
                },
            }

        prob, exp_slams = _solve_exalt_dp(ctx.pool, used_groups, p_open, s_open, missing)
        if prob <= 1e-9:
            return {
                "strategy": self.kind,
                "label": self.label,
                "hitProbability": 0.0,
                "expectedAttempts": None,
                "expectedCostDivine": None,
                "p80CostDivine": None,
                "brickRiskNote": f"当前空位不足（剩 {p_open} 前 / {s_open} 后），无法通过补崇高凑齐全部 {len(missing)} 条缺失目标词缀。",
                "stepsNote": "建议改买保留更多空位的半成品底子，或放宽次要词缀。",
                "en": {
                    "label": self.label_en,
                    "brickRiskNote": f"Not enough open affixes ({p_open}P / {s_open}S left) to exalt all {len(missing)} missing affixes.",
                    "stepsNote": "Buy a semi-finished base with more open slots or relax secondary affixes.",
                },
            }

        exp_tries = round(1.0 / prob, 2)
        p80_tries = _p80_attempts(prob) or 1
        currency_per_try = exp_slams * exalt_d

        if base_d is not None and base_d >= 0:
            per_try_d = base_d + currency_per_try
            exp_cost = round(exp_tries * per_try_d, 2)
            p80_cost = round(p80_tries * per_try_d, 2)
        else:
            exp_cost = round(exp_tries * currency_per_try, 2)
            p80_cost = round(p80_tries * currency_per_try, 2)

        brick_prob_pct = round((1.0 - prob) * 100.0, 1)
        brick_note = (
            f"单颗底子炸掉（填满空位未命中）概率约 {brick_prob_pct}%；"
            f"80% 把握需准备约 {p80_tries} 件底子（若底子可半价转卖，实际净损会低于此上限）。"
            if brick_prob_pct > 25
            else f"空位充裕，单次命中率 {prob * 100:.1f}%，炸底子风险可控。"
        )
        brick_note_en = (
            f"~{brick_prob_pct}% chance to brick a base; budget ~{p80_tries} bases for 80% confidence (lower net loss if bricks resell)."
            if brick_prob_pct > 25
            else f"Open slots sufficient; {prob * 100:.1f}% hit rate per base, brick risk is low."
        )
        steps_note = (
            f"在已有 {len(used_groups)} 条词缀（空 {p_open} 前/{s_open} 后）上补崇高，"
            f"单轮均耗 {exp_slams:.1f} E，命中 {len(missing)} 条目标词缀概率 {prob * 100:.2f}%。"
            + ("" if base_d is not None else "（未填底子价，成本仅计通货）")
        )
        steps_note_en = (
            f"Exalt-slam on {len(used_groups)} locked affixes ({p_open}P/{s_open}S open): "
            f"avg {exp_slams:.1f} Ex/try, {prob * 100:.2f}% to hit {len(missing)} targets."
            + ("" if base_d is not None else " (currency only; base price unset)")
        )

        return {
            "strategy": self.kind,
            "label": self.label,
            "hitProbability": round(prob, 5),
            "expectedAttempts": exp_tries,
            "expectedCostDivine": exp_cost,
            "p80CostDivine": p80_cost,
            "brickRiskNote": brick_note,
            "stepsNote": steps_note,
            "en": {
                "label": self.label_en,
                "brickRiskNote": brick_note_en,
                "stepsNote": steps_note_en,
            },
        }


class EssenceLockStrategy(CraftingStrategy):
    kind = "essence_lock"
    label = "精华定向补位"
    label_en = "Essence Target-Lock"

    def _pick_essence(self, ctx: CraftingContext, missing_groups: set[str]) -> dict[str, Any] | None:
        if ctx.essence_name:
            hit = next(
                (e for e in ctx.available_essences if e["name"].lower() == ctx.essence_name.lower()),
                None,
            )
            if hit:
                return hit
        # 自动挑选命中缺失目标词缀的最高档精华
        matches = [e for e in ctx.available_essences if e.get("group") in missing_groups]
        if matches:
            matches.sort(key=lambda e: -int(e.get("tierLevel") or 0))
            return matches[0]
        return None

    def estimate(self, ctx: CraftingContext) -> dict[str, Any]:
        used_groups, p_open, s_open, missing = _extract_state(ctx)
        exalt_d = float((ctx.rates.get("currencyDivine") or {}).get("exalted", 0.01))
        missing_groups = {m["group"] for m in missing}

        ess = self._pick_essence(ctx, missing_groups)
        if ess is None:
            return {
                "strategy": self.kind,
                "label": self.label,
                "hitProbability": 0.0,
                "expectedAttempts": None,
                "expectedCostDivine": None,
                "p80CostDivine": None,
                "brickRiskNote": "当前缺失的目标词缀在 PoB2 精华表中无直接对应的定向精华，可手动指定精华或使用补崇高策略。",
                "stepsNote": "无匹配精华。",
                "en": {
                    "label": self.label_en,
                    "brickRiskNote": "No matching PoB2 Essence for the missing target affixes; pick an essence manually or use Exalt Slam.",
                    "stepsNote": "No matching Essence.",
                },
            }

        ess_group = ess["group"]
        ess_side = ess["side"]
        ess_price = (
            float(ctx.essence_price)
            if ctx.essence_price is not None and ctx.essence_price >= 0
            else lookup_essence_price(ess["name"], ctx.rates)
        )

        # 检查精华锁定的前/后缀是否放得下
        if (ess_side == "prefix" and p_open <= 0) or (ess_side == "suffix" and s_open <= 0):
            return {
                "strategy": self.kind,
                "label": f"{self.label}（{ess['name']}）",
                "hitProbability": 0.0,
                "expectedAttempts": None,
                "expectedCostDivine": None,
                "p80CostDivine": None,
                "brickRiskNote": f"使用「{ess['name']}」需占用 1 个{ess_side}空位，但当前该侧已满。",
                "stepsNote": "前/后缀已满，无法定向注入该精华。",
                "en": {
                    "label": f"{self.label_en} ({ess['name']})",
                    "brickRiskNote": f"{ess['name']} requires 1 open {ess_side} slot, which is currently full.",
                    "stepsNote": "Target affix side is full.",
                },
            }

        next_p_open = p_open - (1 if ess_side == "prefix" else 0)
        next_s_open = s_open - (1 if ess_side == "suffix" else 0)
        next_used = set(used_groups) | {ess_group}
        rem_missing = [m for m in missing if m["group"] != ess_group]

        prob, exp_slams = _solve_exalt_dp(ctx.pool, next_used, next_p_open, next_s_open, rem_missing)
        if prob <= 1e-9:
            return {
                "strategy": self.kind,
                "label": f"{self.label}（{ess['name']}）",
                "hitProbability": 0.0,
                "expectedAttempts": None,
                "expectedCostDivine": None,
                "p80CostDivine": None,
                "brickRiskNote": f"锁定「{ess['name']}」后剩余空位（{next_p_open}前/{next_s_open}后）放不下其余目标词缀。",
                "stepsNote": "空位不足。",
                "en": {
                    "label": f"{self.label_en} ({ess['name']})",
                    "brickRiskNote": f"Remaining slots ({next_p_open}P/{next_s_open}S) after locking {ess['name']} cannot fit remaining targets.",
                    "stepsNote": "Insufficient open slots.",
                },
            }

        exp_tries = round(1.0 / prob, 2)
        p80_tries = _p80_attempts(prob) or 1
        base_d = ctx.semi_base_price if ctx.semi_base_price is not None else 0.0
        per_try_d = base_d + ess_price + exp_slams * exalt_d
        exp_cost = round(exp_tries * per_try_d, 2)
        p80_cost = round(p80_tries * per_try_d, 2)

        brick_note = (
            f"精华 100% 锁定「{ess['modText']}」，"
            + (
                "剩余无其他缺失目标，一发必成零炸底子风险！"
                if not rem_missing
                else f"随后补崇高命中余下 {len(rem_missing)} 条概率 {prob * 100:.1f}%，80% 把握需约 {p80_tries} 轮。"
            )
        )
        brick_note_en = (
            f"Essence guarantees \"{ess['modText']}\"; "
            + (
                "no other missing affixes — 100% guaranteed finish!"
                if not rem_missing
                else f"{prob * 100:.1f}% chance to exalt remaining {len(rem_missing)} affix(es) (~{p80_tries} tries for P80)."
            )
        )
        steps_note = f"先点 {ess['name']}（约 {ess_price:.3f} D）锁定 1 条核心词缀，再补均耗 {exp_slams:.1f} E。"
        steps_note_en = f"Apply {ess['name']} (~{ess_price:.3f} D) to lock 1 core mod, then exalt avg {exp_slams:.1f} Ex."

        return {
            "strategy": self.kind,
            "label": f"{self.label}（{ess['name']}）",
            "origin": "semi",
            "solverKind": "dp",
            "hitProbability": round(prob, 5),
            "expectedAttempts": exp_tries,
            "expectedCostDivine": exp_cost,
            "p80CostDivine": p80_cost,
            "brickRiskNote": brick_note,
            "stepsNote": steps_note,
            "currencyUsed": [ess["name"], *(["Exalted Orb"] if rem_missing else [])],
            "recipeSteps": [
                {
                    "step": 1,
                    "action": f"买非腐化半成品底子（剩 {p_open}前/{s_open}后）",
                    "costDivine": round(base_d, 2),
                    "successChance": 1.0,
                    "detail": f"参考底子价 {base_d:.2f} D，确保目标侧留有空位。",
                },
                {
                    "step": 2,
                    "action": f"使用「{ess['name']}」必锁目标词缀",
                    "currency": ess["name"],
                    "costDivine": round(ess_price, 3),
                    "successChance": 1.0,
                    "detail": f"100% 锁定「{ess['modText']}」。",
                },
                *(
                    [
                        {
                            "step": 3,
                            "action": f"补崇高凑齐余下 {len(rem_missing)} 条词缀",
                            "currency": "Exalted Orb",
                            "costDivine": round(exp_slams * exalt_d, 3),
                            "successChance": round(prob, 4),
                            "detail": f"单轮均耗 {exp_slams:.1f} E，命中率 {prob * 100:.1f}%。",
                        }
                    ]
                    if rem_missing
                    else []
                ),
            ],
            "en": {
                "label": f"{self.label_en} ({ess['name']})",
                "brickRiskNote": brick_note_en,
                "stepsNote": steps_note_en,
            },
        }


class RawRollStrategy(CraftingStrategy):
    kind = "raw_roll"
    label = "白底从零重做（参考）"
    label_en = "Raw Base Re-Roll (Baseline)"

    def estimate(self, ctx: CraftingContext) -> dict[str, Any]:
        targets = []
        seen: set[str] = set()
        for w in ctx.matched_wanted:
            if w.get("matched") and w.get("group") and w["group"] not in seen:
                seen.add(w["group"])
                targets.append(w)

        if not targets:
            return {
                "strategy": self.kind,
                "label": self.label,
                "hitProbability": 0.0,
                "expectedAttempts": None,
                "expectedCostDivine": None,
                "p80CostDivine": None,
                "brickRiskNote": "请先指定至少 1 条有效目标词缀。",
                "stepsNote": "无目标词缀。",
                "en": {
                    "label": self.label_en,
                    "brickRiskNote": "Specify at least 1 valid target affix.",
                    "stepsNote": "No target affixes.",
                },
            }

        prob, exp_slams = _solve_exalt_dp(ctx.pool, set(), 3, 3, targets)
        if prob <= 1e-9:
            return {
                "strategy": self.kind,
                "label": self.label,
                "hitProbability": 0.0,
                "expectedAttempts": None,
                "expectedCostDivine": None,
                "p80CostDivine": None,
                "brickRiskNote": "目标词缀前/后缀总数超过单件 3 前 3 后上限。",
                "stepsNote": "不可行组合。",
                "en": {
                    "label": self.label_en,
                    "brickRiskNote": "Target affixes exceed the 3-Prefix / 3-Suffix item limit.",
                    "stepsNote": "Infeasible combination.",
                },
            }

        cur = ctx.rates.get("currencyDivine") or {}
        exalt_d = float(cur.get("exalted", 0.01))
        alch_d = float(cur.get("alchemy", 0.004))
        # 白底每轮假设：点金/蜕变富豪 + 补崇高（忽略白底微末底价，仅作通货锚点）
        per_try_d = alch_d + max(1.0, exp_slams - 1.5) * exalt_d
        exp_tries = round(1.0 / prob, 1)
        p80_tries = _p80_attempts(prob) or 1

        return {
            "strategy": self.kind,
            "label": self.label,
            "hitProbability": round(prob, 6),
            "expectedAttempts": exp_tries,
            "expectedCostDivine": round(exp_tries * per_try_d, 2),
            "p80CostDivine": round(p80_tries * per_try_d, 2),
            "brickRiskNote": f"按命中目标词缀组（任意 T 级）估算，单轮概率 {prob * 100:.2f}%；若追高 T1/T2 顶级数值实际造价会高数倍，仅作底线参考。",
            "stepsNote": f"每轮约耗 {per_try_d / max(exalt_d, 1e-6):.1f} E，期望约 {exp_tries:.0f} 轮。",
            "en": {
                "label": self.label_en,
                "brickRiskNote": f"Any-tier group hit rate is {prob * 100:.2f}%/try; chasing T1/T2 tiers costs several times more (baseline only).",
                "stepsNote": f"~{per_try_d / max(exalt_d, 1e-6):.1f} Ex/try, expected ~{exp_tries:.0f} tries.",
            },
        }
