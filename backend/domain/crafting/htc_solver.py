"""PoE2 HTC 风格精确做装双求解器（参照 Dboire9/POE2_HTC 核心思想）：
1. Policy Solver (MDP 策略迭代)：在 (稀有度, 已命中目标掩码, 垃圾前缀数, 垃圾后缀数) 状态空间上，
   综合普通/高阶/完美通货、左旋/右旋崇高与剥离预兆、精华锁词、削减预兆(Whittling)、破裂锁词与止损重开，
   求解精确无采样噪声的最优期望造价与带「歪了怎么办」的最优策略配方。
2. Step Planner (帕累托前沿)：评估常见实战固定工艺配方，保留不被支配的（更便宜或单轮概率更高）帕累托最优配方。
"""
from __future__ import annotations

import math
from typing import Any

from backend.domain.crafting import affix_pool
from backend.domain.crafting.base import CraftingContext, CraftingStrategy
from backend.market.ninja_rates import (
    lookup_currency_price,
    lookup_essence_price,
    lookup_omen_price,
)

# PoE2 通货品阶对应的词缀最低等级门槛（截断低 T 废词）
ORB_TIER_FLOOR: dict[str, int] = {
    "basic": 0,
    "greater": 35,
    "perfect": 50,
}

ORB_TIER_ZH: dict[str, str] = {
    "basic": "普通",
    "greater": "高阶",
    "perfect": "完美",
}


def _p80_attempts(prob: float) -> int:
    if prob <= 1e-9:
        return 9999
    if prob >= 0.999999:
        return 1
    return max(1, int(math.ceil(math.log(0.2) / math.log(1.0 - prob))))


def _target_label(t: dict[str, Any]) -> str:
    return str(t.get("zhText") or t.get("sampleText") or t.get("group") or t.get("input") or "")


class _SlotRollModel:
    """精确无放回词缀池转移概率模型，支持 Basic / Greater / Perfect 通货等级门槛截断与单侧预兆限制。"""

    def __init__(
        self,
        pool: dict[str, Any],
        locked_groups: set[str],
        targets: list[dict[str, Any]],
        min_tier_mode: str = "any",
    ) -> None:
        self.pool = pool
        self.locked_groups = set(locked_groups)
        self.targets = targets[:4]
        self.n_targets = len(self.targets)
        self.full_mask = (1 << self.n_targets) - 1
        self.target_groups = [str(t["group"]) for t in self.targets]
        self.target_group_set = set(self.target_groups)
        self.target_sides = [str(t["side"]) for t in self.targets]
        self.min_tier_mode = (min_tier_mode or "any").lower()

        # 按通货品阶缓存词缀权重
        self._tier_cache: dict[str, dict[str, Any]] = {}
        for tier_key, req_floor in ORB_TIER_FLOOR.items():
            self._tier_cache[tier_key] = self._precompute_tier(req_floor)

    def _precompute_tier(self, req_floor: int) -> dict[str, Any]:
        p_pool = self.pool["prefixes"]
        s_pool = self.pool["suffixes"]

        hit_w: list[float] = []
        sub_tier_w: list[float] = []
        for t in self.targets:
            side_pool = p_pool if t["side"] == "prefix" else s_pool
            g_entry = side_pool.get(t["group"])
            if not g_entry:
                hit_w.append(0.0)
                sub_tier_w.append(0.0)
                continue
            tot_w = affix_pool.compute_orb_pool_weight(g_entry, req_floor)
            q_w, _ = affix_pool.compute_qualifying_target_weight(
                g_entry,
                min_tier_mode=self.min_tier_mode,
                desired_value=t.get("desiredValue"),
                orb_min_req_lvl=req_floor,
            )
            hit_w.append(q_w)
            sub_tier_w.append(max(0.0, tot_w - q_w))

        miss_p_list: list[float] = []
        for g_name, g_entry in p_pool.items():
            if g_name in self.locked_groups or g_name in self.target_group_set:
                continue
            w = affix_pool.compute_orb_pool_weight(g_entry, req_floor)
            if w > 0:
                miss_p_list.append(w)

        miss_s_list: list[float] = []
        for g_name, g_entry in s_pool.items():
            if g_name in self.locked_groups or g_name in self.target_group_set:
                continue
            w = affix_pool.compute_orb_pool_weight(g_entry, req_floor)
            if w > 0:
                miss_s_list.append(w)

        total_miss_p = sum(miss_p_list)
        total_miss_s = sum(miss_s_list)
        avg_miss_p = (total_miss_p / len(miss_p_list)) if miss_p_list else 0.0
        avg_miss_s = (total_miss_s / len(miss_s_list)) if miss_s_list else 0.0

        return {
            "hit_w": hit_w,
            "sub_tier_w": sub_tier_w,
            "total_miss_p": total_miss_p,
            "total_miss_s": total_miss_s,
            "avg_miss_p": avg_miss_p,
            "avg_miss_s": avg_miss_s,
        }

    def roll_distribution(
        self,
        mask: int,
        junk_p: int,
        junk_s: int,
        open_p: int,
        open_s: int,
        *,
        orb_tier: str = "basic",
        side_restrict: str | None = None,  # None | "prefix" | "suffix"
    ) -> list[tuple[str, int, float]]:
        """返回单次掷词缀的离散结果列表：(kind, target_idx_or_-1, probability)。
        kind: 'hit' (命中目标 i), 'miss_p' (歪出前缀), 'miss_s' (歪出后缀)。
        """
        allow_p = open_p > 0 and side_restrict in (None, "prefix")
        allow_s = open_s > 0 and side_restrict in (None, "suffix")
        if not allow_p and not allow_s:
            return []

        td = self._tier_cache[orb_tier]
        hit_w = td["hit_w"]
        sub_tier_w = td["sub_tier_w"]

        # 未命中的目标词缀权重（及同组低 T 算歪的部分）
        items: list[tuple[str, int, float]] = []
        extra_miss_p = 0.0
        extra_miss_s = 0.0
        for i in range(self.n_targets):
            if mask & (1 << i):
                continue
            side = self.target_sides[i]
            if side == "prefix" and allow_p:
                if hit_w[i] > 0:
                    items.append(("hit", i, hit_w[i]))
                extra_miss_p += sub_tier_w[i]
            elif side == "suffix" and allow_s:
                if hit_w[i] > 0:
                    items.append(("hit", i, hit_w[i]))
                extra_miss_s += sub_tier_w[i]

        rem_miss_p = max(0.0, td["total_miss_p"] - junk_p * td["avg_miss_p"]) + extra_miss_p if allow_p else 0.0
        rem_miss_s = max(0.0, td["total_miss_s"] - junk_s * td["avg_miss_s"]) + extra_miss_s if allow_s else 0.0

        if allow_p and rem_miss_p > 0:
            items.append(("miss_p", -1, rem_miss_p))
        if allow_s and rem_miss_s > 0:
            items.append(("miss_s", -1, rem_miss_s))

        total_w = sum(w for _, _, w in items)
        if total_w <= 1e-9:
            return []
        return [(k, idx, w / total_w) for k, idx, w in items if w > 1e-12]


def _pick_matching_essences(
    ctx: CraftingContext,
    targets: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """为每个目标索引挑选成本最低（或用户指定）的可用定向精华。"""
    out: dict[int, dict[str, Any]] = {}
    for i, t in enumerate(targets):
        grp = t["group"]
        cands = [e for e in ctx.available_essences if e.get("group") == grp]
        if not cands:
            continue
        if ctx.essence_name:
            exact = next((e for e in cands if e["name"].lower() == ctx.essence_name.lower()), None)
            if exact:
                price = (
                    float(ctx.essence_price)
                    if ctx.essence_price is not None and ctx.essence_price >= 0
                    else lookup_essence_price(exact["name"], ctx.rates)
                )
                out[i] = {**exact, "resolvedPrice": price}
                continue
        # 根据 T 级要求挑选精华档次：high 选最高档，否则选性价比最优
        mode = (ctx.min_tier_mode or "any").lower()
        if mode == "high":
            cands.sort(key=lambda e: (-int(e.get("tierLevel") or 0), lookup_essence_price(e["name"], ctx.rates)))
        else:
            cands.sort(key=lambda e: lookup_essence_price(e["name"], ctx.rates))
        best = cands[0]
        out[i] = {**best, "resolvedPrice": lookup_essence_price(best["name"], ctx.rates)}
    return out


def solve_crafting_mdp(
    ctx: CraftingContext,
    *,
    origin: str,  # "semi" | "raw"
    targets: list[dict[str, Any]],
    locked_groups: set[str],
    fixed_p: int,
    fixed_s: int,
    base_cost: float,
    fractured_p: int = 0,
    fractured_s: int = 0,
) -> dict[str, Any]:
    """HTC 风格精确 MDP 策略求解器（Policy Iteration）。

    状态表示 s = (rarity, mask, junk_p, junk_s):
      - rarity: 0=白底, 1=蓝底(1P+1S上限), 2=黄底(3P+3S上限)
      - mask: 目标词缀命中位掩码
      - junk_p, junk_s: 非目标垃圾前/后缀数量
    返回：精确期望成本、P80 风险预算、首轮底子成功率、是否使用剥离/预兆救底子、逐步操作配方。
    """
    targets = targets[:4]
    n = len(targets)
    full_mask = (1 << n) - 1
    if n == 0:
        return {
            "feasible": True,
            "origin": origin,
            "expectedCostDivine": round(base_cost, 2),
            "p80CostDivine": round(base_cost, 2),
            "hitProbability": 1.0,
            "expectedAttempts": 1.0,
            "recoveryUsed": False,
            "currenciesUsed": [],
            "steps": [
                {
                    "step": 1,
                    "action": "已包含全部目标词缀，直接购买使用",
                    "costDivine": round(base_cost, 2),
                    "successChance": 1.0,
                    "detail": "无需额外通货加工。",
                }
            ],
        }

    need_p_total = sum(1 for t in targets if t["side"] == "prefix")
    need_s_total = sum(1 for t in targets if t["side"] == "suffix")
    if fixed_p + need_p_total > 3 or fixed_s + need_s_total > 3:
        return {"feasible": False, "origin": origin, "reason": "目标前/后缀总数超出单件 3前3后 上限。"}

    model = _SlotRollModel(ctx.pool, locked_groups, targets, min_tier_mode=ctx.min_tier_mode)
    rates = ctx.rates
    ess_by_idx = _pick_matching_essences(ctx, targets)

    # 通货与预兆实时单价 (D)
    p_exalt = {
        "basic": lookup_currency_price("exalted", rates),
        "greater": lookup_currency_price("greater_exalted", rates),
        "perfect": lookup_currency_price("perfect_exalted", rates),
    }
    p_regal = {
        "basic": lookup_currency_price("regal", rates),
        "greater": lookup_currency_price("greater_regal", rates),
        "perfect": lookup_currency_price("perfect_regal", rates),
    }
    p_trans = {
        "basic": lookup_currency_price("transmutation", rates),
        "greater": lookup_currency_price("greater_transmutation", rates),
        "perfect": lookup_currency_price("perfect_transmutation", rates),
    }
    p_aug = {
        "basic": lookup_currency_price("augmentation", rates),
        "greater": lookup_currency_price("greater_augmentation", rates),
        "perfect": lookup_currency_price("perfect_augmentation", rates),
    }
    p_annul = lookup_currency_price("annul", rates)
    p_chaos = lookup_currency_price("chaos", rates)
    p_sin_ex = lookup_omen_price("Omen of Sinistral Exaltation", rates)
    p_dex_ex = lookup_omen_price("Omen of Dextral Exaltation", rates)
    p_sin_an = lookup_omen_price("Omen of Sinistral Annulment", rates)
    p_dex_an = lookup_omen_price("Omen of Dextral Annulment", rates)
    p_whittle = lookup_omen_price("Omen of Whittling", rates)

    eff_base_cost = max(0.005, float(base_cost))
    s_init = (0, 0, 0, 0) if origin == "raw" else (2, 0, 0, 0)

    # 生成全部合法状态
    states: list[tuple[int, int, int, int]] = []
    rarities = (0, 1, 2) if origin == "raw" else (2,)
    for rarity in rarities:
        p_cap = 0 if rarity == 0 else (1 if rarity == 1 else 3)
        s_cap = 0 if rarity == 0 else (1 if rarity == 1 else 3)
        for mask in range(1 << n):
            hp = sum(1 for i in range(n) if (mask & (1 << i)) and targets[i]["side"] == "prefix")
            hs = sum(1 for i in range(n) if (mask & (1 << i)) and targets[i]["side"] == "suffix")
            base_p = fixed_p + hp
            base_s = fixed_s + hs
            if base_p > p_cap or base_s > s_cap:
                continue
            for jp in range(0, p_cap - base_p + 1):
                for js in range(0, s_cap - base_s + 1):
                    if rarity == 0 and (mask != 0 or jp != 0 or js != 0):
                        continue
                    if rarity == 1 and (hp + hs + jp + js) == 0:
                        continue
                    states.append((rarity, mask, jp, js))

    # 预计算每个非终态状态的合法动作列表：action = (action_id, label, cost, transitions, meta)
    # transitions: list[(next_state_or_"RESTART", prob)]
    actions_by_state: dict[tuple[int, int, int, int], list[dict[str, Any]]] = {}

    for st in states:
        rarity, mask, jp, js = st
        if mask == full_mask:
            actions_by_state[st] = []
            continue

        hp = sum(1 for i in range(n) if (mask & (1 << i)) and targets[i]["side"] == "prefix")
        hs = sum(1 for i in range(n) if (mask & (1 << i)) and targets[i]["side"] == "suffix")
        cur_p = fixed_p + hp + jp
        cur_s = fixed_s + hs + js

        acts: list[dict[str, Any]] = []

        # 动作 0：放弃当前底子止损重来（半成品买新底子，白底换新白底）
        if st != s_init:
            acts.append({
                "id": "restart",
                "label": "止损换新底子重来",
                "currency": "新底子" if origin == "semi" else "新白底",
                "cost": 0.0,  # 转移到 RESTART，价值为 R（含新底子价 + V(s_init)）
                "transitions": [("RESTART", 1.0)],
                "isRecovery": False,
            })

        if rarity == 0:
            # 白底 -> 蓝底（蜕变石：普通/高阶/完美）
            for tier in ("basic", "greater", "perfect"):
                dist = model.roll_distribution(0, 0, 0, 1, 1, orb_tier=tier)
                if not dist:
                    continue
                trans: list[tuple[Any, float]] = []
                for k, idx, pr in dist:
                    if k == "hit":
                        trans.append(((1, 1 << idx, 0, 0), pr))
                    elif k == "miss_p":
                        trans.append(((1, 0, 1, 0), pr))
                    else:
                        trans.append(((1, 0, 0, 1), pr))
                acts.append({
                    "id": f"transmute_{tier}",
                    "label": f"{ORB_TIER_ZH[tier]}蜕变石点蓝",
                    "currency": f"{tier.title()} Orb of Transmutation",
                    "cost": p_trans[tier],
                    "transitions": trans,
                    "isRecovery": False,
                })
            # 白底 -> 蓝底（定向精华锁 1 条目标词缀）
            for i, ess in ess_by_idx.items():
                # 较低阶精华用于白->蓝
                ess_cost = ess["resolvedPrice"]
                acts.append({
                    "id": f"essence_white_{i}",
                    "label": f"精华起手锁「{_target_label(targets[i])}」",
                    "currency": ess["name"],
                    "cost": ess_cost,
                    "transitions": [((1, 1 << i, 0, 0), 1.0)],
                    "isRecovery": False,
                })

        elif rarity == 1:
            open_p = 1 - cur_p
            open_s = 1 - cur_s
            # 蓝底增幅（若只占了 1 条词缀）
            if open_p + open_s > 0:
                for tier in ("basic", "greater", "perfect"):
                    dist = model.roll_distribution(mask, jp, js, open_p, open_s, orb_tier=tier)
                    if not dist:
                        continue
                    trans = []
                    for k, idx, pr in dist:
                        if k == "hit":
                            trans.append(((1, mask | (1 << idx), jp, js), pr))
                        elif k == "miss_p":
                            trans.append(((1, mask, jp + 1, js), pr))
                        else:
                            trans.append(((1, mask, jp, js + 1), pr))
                    acts.append({
                        "id": f"augment_{tier}",
                        "label": f"{ORB_TIER_ZH[tier]}增幅石补蓝词",
                        "currency": f"{tier.title()} Orb of Augmentation",
                        "cost": p_aug[tier],
                        "transitions": trans,
                        "isRecovery": False,
                    })

            # 蓝底 -> 黄底（富豪石：普通/高阶/完美）
            open_p_r = 3 - cur_p
            open_s_r = 3 - cur_s
            for tier in ("basic", "greater", "perfect"):
                dist = model.roll_distribution(mask, jp, js, open_p_r, open_s_r, orb_tier=tier)
                if not dist:
                    continue
                trans = []
                for k, idx, pr in dist:
                    if k == "hit":
                        trans.append(((2, mask | (1 << idx), jp, js), pr))
                    elif k == "miss_p":
                        trans.append(((2, mask, jp + 1, js), pr))
                    else:
                        trans.append(((2, mask, jp, js + 1), pr))
                acts.append({
                    "id": f"regal_{tier}",
                    "label": f"{ORB_TIER_ZH[tier]}富豪石升金",
                    "currency": f"{tier.title()} Regal Orb",
                    "cost": p_regal[tier],
                    "transitions": trans,
                    "isRecovery": False,
                })

            # 蓝底 -> 黄底（高阶精华定向升金锁定 1 条未命中目标词缀）
            for i, ess in ess_by_idx.items():
                if mask & (1 << i):
                    continue
                side = targets[i]["side"]
                if (side == "prefix" and open_p_r <= 0) or (side == "suffix" and open_s_r <= 0):
                    continue
                acts.append({
                    "id": f"essence_regal_{i}",
                    "label": f"精华升金必锁「{_target_label(targets[i])}」",
                    "currency": ess["name"],
                    "cost": ess["resolvedPrice"],
                    "transitions": [((2, mask | (1 << i), jp, js), 1.0)],
                    "isRecovery": False,
                })

        else:
            # 黄装 (rarity == 2)
            open_p = 3 - cur_p
            open_s = 3 - cur_s

            # 1. 普通 / 高阶 / 完美 崇高石
            if open_p + open_s > 0:
                for tier in ("basic", "greater", "perfect"):
                    dist = model.roll_distribution(mask, jp, js, open_p, open_s, orb_tier=tier)
                    if not dist:
                        continue
                    trans = []
                    for k, idx, pr in dist:
                        if k == "hit":
                            trans.append(((2, mask | (1 << idx), jp, js), pr))
                        elif k == "miss_p":
                            trans.append(((2, mask, jp + 1, js), pr))
                        else:
                            trans.append(((2, mask, jp, js + 1), pr))
                    acts.append({
                        "id": f"exalt_{tier}",
                        "label": f"{ORB_TIER_ZH[tier]}崇高石补词",
                        "currency": f"{tier.title()} Exalted Orb",
                        "cost": p_exalt[tier],
                        "transitions": trans,
                        "isRecovery": False,
                    })

            # 2. 左旋崇高预兆（只补前缀）+ 普通/高阶/完美崇高
            rem_need_p = sum(1 for i in range(n) if not (mask & (1 << i)) and targets[i]["side"] == "prefix")
            if open_p > 0 and open_s > 0 and rem_need_p > 0:
                for tier in ("basic", "greater", "perfect"):
                    dist = model.roll_distribution(mask, jp, js, open_p, open_s, orb_tier=tier, side_restrict="prefix")
                    if not dist:
                        continue
                    trans = []
                    for k, idx, pr in dist:
                        if k == "hit":
                            trans.append(((2, mask | (1 << idx), jp, js), pr))
                        else:
                            trans.append(((2, mask, jp + 1, js), pr))
                    acts.append({
                        "id": f"sinistral_exalt_{tier}",
                        "label": f"左旋崇高预兆 + {ORB_TIER_ZH[tier]}崇高（必出前缀）",
                        "currency": f"Omen of Sinistral Exaltation + {tier.title()} Exalted Orb",
                        "cost": p_sin_ex + p_exalt[tier],
                        "transitions": trans,
                        "isRecovery": False,
                    })

            # 3. 右旋崇高预兆（只补后缀）+ 普通/高阶/完美崇高
            rem_need_s = sum(1 for i in range(n) if not (mask & (1 << i)) and targets[i]["side"] == "suffix")
            if open_s > 0 and open_p > 0 and rem_need_s > 0:
                for tier in ("basic", "greater", "perfect"):
                    dist = model.roll_distribution(mask, jp, js, open_p, open_s, orb_tier=tier, side_restrict="suffix")
                    if not dist:
                        continue
                    trans = []
                    for k, idx, pr in dist:
                        if k == "hit":
                            trans.append(((2, mask | (1 << idx), jp, js), pr))
                        else:
                            trans.append(((2, mask, jp, js + 1), pr))
                    acts.append({
                        "id": f"dextral_exalt_{tier}",
                        "label": f"右旋崇高预兆 + {ORB_TIER_ZH[tier]}崇高（必出后缀）",
                        "currency": f"Omen of Dextral Exaltation + {tier.title()} Exalted Orb",
                        "cost": p_dex_ex + p_exalt[tier],
                        "transitions": trans,
                        "isRecovery": False,
                    })

            # 4. 剥离救底子（普通剥离 / 左旋剥离去前缀 / 右旋剥离去后缀），保护破裂锁词
            removable_fixed_p = max(0, fixed_p - fractured_p)
            removable_fixed_s = max(0, fixed_s - fractured_s)

            # 左旋剥离（仅删前缀）
            if jp > 0:
                rem_p_pool = removable_fixed_p + hp + jp
                if rem_p_pool > 0:
                    trans = []
                    trans.append(((2, mask, jp - 1, js), jp / rem_p_pool))
                    for i in range(n):
                        if (mask & (1 << i)) and targets[i]["side"] == "prefix":
                            trans.append(((2, mask ^ (1 << i), jp, js), 1.0 / rem_p_pool))
                    if removable_fixed_p > 0:
                        trans.append(("RESTART", removable_fixed_p / rem_p_pool))
                    acts.append({
                        "id": "sinistral_annul",
                        "label": "左旋剥离预兆 + 剥离石（清垃圾前缀救底子）",
                        "currency": "Omen of Sinistral Annulment + Orb of Annulment",
                        "cost": p_sin_an + p_annul,
                        "transitions": trans,
                        "isRecovery": True,
                    })

            # 右旋剥离（仅删后缀）
            if js > 0:
                rem_s_pool = removable_fixed_s + hs + js
                if rem_s_pool > 0:
                    trans = []
                    trans.append(((2, mask, jp, js - 1), js / rem_s_pool))
                    for i in range(n):
                        if (mask & (1 << i)) and targets[i]["side"] == "suffix":
                            trans.append(((2, mask ^ (1 << i), jp, js), 1.0 / rem_s_pool))
                    if removable_fixed_s > 0:
                        trans.append(("RESTART", removable_fixed_s / rem_s_pool))
                    acts.append({
                        "id": "dextral_annul",
                        "label": "右旋剥离预兆 + 剥离石（清垃圾后缀救底子）",
                        "currency": "Omen of Dextral Annulment + Orb of Annulment",
                        "cost": p_dex_an + p_annul,
                        "transitions": trans,
                        "isRecovery": True,
                    })

            # 普通剥离（随机删 1 条非破裂词缀）
            if jp + js > 0:
                rem_all = removable_fixed_p + removable_fixed_s + hp + hs + jp + js
                if rem_all > 0:
                    trans = []
                    if jp > 0:
                        trans.append(((2, mask, jp - 1, js), jp / rem_all))
                    if js > 0:
                        trans.append(((2, mask, jp, js - 1), js / rem_all))
                    for i in range(n):
                        if mask & (1 << i):
                            trans.append(((2, mask ^ (1 << i), jp, js), 1.0 / rem_all))
                    if removable_fixed_p + removable_fixed_s > 0:
                        trans.append(("RESTART", (removable_fixed_p + removable_fixed_s) / rem_all))
                    acts.append({
                        "id": "annul",
                        "label": "剥离石赌清废词",
                        "currency": "Orb of Annulment",
                        "cost": p_annul,
                        "transitions": trans,
                        "isRecovery": True,
                    })

            # 5. 削减预兆 + 混沌石（定向洗掉最低物等废词）
            if jp + js > 0:
                # 若高 T 目标，废词为最低等级的概率约 72%；否则保守取 55%
                p_clean_junk = 0.72 if ctx.min_tier_mode == "high" else 0.55
                # 拆掉一条废词后立刻掷一条新词缀
                jp_after = jp - 1 if jp >= js else jp
                js_after = js - 1 if js > jp else js
                open_p_w = 3 - (fixed_p + hp + jp_after)
                open_s_w = 3 - (fixed_s + hs + js_after)
                dist = model.roll_distribution(mask, jp_after, js_after, open_p_w, open_s_w, orb_tier="basic")
                if dist:
                    trans = []
                    for k, idx, pr in dist:
                        if k == "hit":
                            trans.append(((2, mask | (1 << idx), jp_after, js_after), p_clean_junk * pr))
                        elif k == "miss_p":
                            trans.append(((2, mask, jp_after + 1, js_after), p_clean_junk * pr))
                        else:
                            trans.append(((2, mask, jp_after, js_after + 1), p_clean_junk * pr))
                    # 若不幸削到已有核心词缀则按炸底子算
                    if 1.0 - p_clean_junk > 1e-6:
                        trans.append(("RESTART", 1.0 - p_clean_junk))
                    acts.append({
                        "id": "whittling_chaos",
                        "label": "削减预兆(Whittling) + 混沌石洗低T废词",
                        "currency": "Omen of Whittling + Chaos Orb",
                        "cost": p_whittle + p_chaos,
                        "transitions": trans,
                        "isRecovery": True,
                    })

        actions_by_state[st] = acts

    # === Howard Policy Iteration + Parametric Fixed-Point 求解精确最优期望 R* ===
    R = 5000.0
    policy: dict[tuple[int, int, int, int], dict[str, Any] | None] = {st: None for st in states}

    for _outer in range(18):
        # 1. 给定当前重开总价 R，通过单调递减 Gauss-Seidel 求当前最优值函数 V_R(s)
        V: dict[tuple[int, int, int, int], float] = {
            st: (0.0 if st[1] == full_mask else R) for st in states
        }
        for _sweep in range(90):
            max_diff = 0.0
            for st in states:
                if st[1] == full_mask:
                    continue
                best_val = R
                best_act = None
                for act in actions_by_state[st]:
                    val = act["cost"]
                    for nxt, pr in act["transitions"]:
                        val += pr * (R if nxt == "RESTART" else V[nxt])
                    if val < best_val - 1e-12:
                        best_val = val
                        best_act = act
                diff = abs(V[st] - best_val)
                if diff > max_diff:
                    max_diff = diff
                V[st] = best_val
                policy[st] = best_act
            if max_diff < 1e-6:
                break

        # 2. 对当前贪心策略精确评估单轮 (E_currency, P_goal)，做一步 Newton 精准更新 R
        e_cur: dict[tuple[int, int, int, int], float] = {st: 0.0 for st in states}
        p_goal: dict[tuple[int, int, int, int], float] = {
            st: (1.0 if st[1] == full_mask else 0.0) for st in states
        }
        for _eval in range(120):
            md = 0.0
            for st in states:
                if st[1] == full_mask:
                    continue
                act = policy.get(st)
                if act is None or act["id"] == "restart":
                    nc, np_ = 0.0, 0.0
                else:
                    nc = act["cost"]
                    np_ = 0.0
                    for nxt, pr in act["transitions"]:
                        if nxt != "RESTART":
                            nc += pr * e_cur[nxt]
                            np_ += pr * p_goal[nxt]
                md = max(md, abs(e_cur[st] - nc), abs(p_goal[st] - np_))
                e_cur[st] = nc
                p_goal[st] = np_
            if md < 1e-7:
                break

        pg_init = p_goal[s_init]
        if pg_init <= 1e-9:
            return {"feasible": False, "origin": origin, "reason": "当前词缀池与空位条件下无法凑齐全部目标词缀。"}

        new_R = (eff_base_cost + e_cur[s_init]) / pg_init
        if abs(new_R - R) < 1e-4:
            R = new_R
            break
        R = new_R

    # 最终策略指标提取
    pg_init = p_goal[s_init]
    ec_init = e_cur[s_init]
    exp_cost = round(R, 2)
    exp_tries = round(1.0 / max(pg_init, 1e-9), 2)

    if pg_init >= 0.999:
        # 不丢底子（全程救底子或必中），按通货部分方差估算 P80
        p80_cost = round(eff_base_cost + ec_init * (1.0 if ec_init < 1e-6 else 1.55), 2)
    else:
        p80_tries = _p80_attempts(pg_init)
        p80_cost = round(p80_tries * (eff_base_cost + ec_init), 2)

    # 沿着最优策略提取主路径步骤 + 歪了怎么办（分支恢复策略）
    steps: list[dict[str, Any]] = []
    currencies_used: list[str] = []
    recovery_used = False
    step_no = 1

    steps.append({
        "step": step_no,
        "action": (
            f"市集买高物等(iLvl {ctx.ilvl}+)白底底材"
            if origin == "raw"
            else f"市集买非腐化半成品底子（含已有 {fixed_p}前/{fixed_s}后）"
        ),
        "currency": "底材" if origin == "raw" else "半成品底子",
        "costDivine": round(base_cost, 2),
        "successChance": 1.0,
        "detail": f"参考底子价约 {round(base_cost, 2)} D。",
    })
    step_no += 1

    cur_st = s_init
    visited: set[tuple[int, int, int, int]] = set()
    while cur_st[1] != full_mask and cur_st not in visited and step_no <= 10:
        visited.add(cur_st)
        act = policy.get(cur_st)
        if not act or act["id"] == "restart":
            break
        if act.get("currency") and act["currency"] not in currencies_used:
            currencies_used.append(act["currency"])

        # 计算本步命中至少一条新目标词缀的概率，以及最可能歪掉的分支策略
        hit_prob_step = 0.0
        best_hit_next = None
        best_hit_p = -1.0
        miss_sample_next = None
        for nxt, pr in act["transitions"]:
            if nxt != "RESTART" and nxt[1] != cur_st[1]:
                hit_prob_step += pr
                if pr > best_hit_p:
                    best_hit_p = pr
                    best_hit_next = nxt
            elif nxt != "RESTART" and miss_sample_next is None:
                miss_sample_next = nxt

        # 检查歪了以后最优策略做什么（救底子 vs 继续点 vs 止损换底）
        miss_advice = ""
        if miss_sample_next is not None:
            m_act = policy.get(miss_sample_next)
            if m_act:
                if m_act["id"] == "restart":
                    miss_advice = "若歪了直接止损换新底子更划算。"
                elif m_act.get("isRecovery"):
                    recovery_used = True
                    if m_act.get("currency") and m_act["currency"] not in currencies_used:
                        currencies_used.append(m_act["currency"])
                    miss_advice = f"若歪了：用「{m_act['label']}」(约 {m_act['cost']:.2f}D) 救底子比重买更省！"
                else:
                    miss_advice = f"若歪了仍有空位，继续「{m_act['label']}」。"

        steps.append({
            "step": step_no,
            "action": act["label"],
            "currency": act.get("currency"),
            "costDivine": round(float(act["cost"]), 3),
            "successChance": round(hit_prob_step, 4) if hit_prob_step > 0 else None,
            "detail": (
                f"单步通货约 {act['cost']:.3f} D"
                + (f"，命中目标词缀概率 {hit_prob_step * 100:.1f}%" if hit_prob_step > 0 else "")
                + (f"；{miss_advice}" if miss_advice else "。")
            ),
        })
        step_no += 1

        if best_hit_next is not None:
            cur_st = best_hit_next
        elif miss_sample_next is not None:
            cur_st = miss_sample_next
        else:
            break

    # 检查整个策略里是否有任意可达状态用到了剥离/削减预兆救底子
    if not recovery_used:
        for st in states:
            act = policy.get(st)
            if act and act.get("isRecovery") and p_goal[st] > 1e-6:
                recovery_used = True
                break

    return {
        "feasible": True,
        "origin": origin,
        "expectedCostDivine": exp_cost,
        "p80CostDivine": p80_cost,
        "hitProbability": round(pg_init, 5),
        "expectedAttempts": exp_tries,
        "recoveryUsed": recovery_used,
        "currenciesUsed": currencies_used,
        "steps": steps,
    }


def _eval_fixed_exalt_sequence(
    model: _SlotRollModel,
    p_open: int,
    s_open: int,
    *,
    orb_tier: str,
    use_side_omen: bool,
    orb_cost: float,
    sin_omen_cost: float,
    dex_omen_cost: float,
) -> tuple[float, float]:
    """解析 DP 计算固定通货组合（可带左右旋预兆）在剩余空位内命中全部目标的概率与单轮期望通货费。"""
    from functools import lru_cache

    targets = model.targets
    n = model.n_targets
    full_mask = model.full_mask

    @lru_cache(maxsize=None)
    def dp(pr: int, sr: int, mask: int, jp: int, js: int) -> tuple[float, float]:
        if mask == full_mask:
            return 1.0, 0.0
        rem_p = sum(1 for i in range(n) if not (mask & (1 << i)) and targets[i]["side"] == "prefix")
        rem_s = sum(1 for i in range(n) if not (mask & (1 << i)) and targets[i]["side"] == "suffix")
        if rem_p > pr or rem_s > sr:
            return 0.0, 0.0

        restrict = None
        step_cost = orb_cost
        if use_side_omen and pr > 0 and sr > 0:
            if rem_p > 0 and rem_s == 0:
                restrict = "prefix"
                step_cost += sin_omen_cost
            elif rem_s > 0 and rem_p == 0:
                restrict = "suffix"
                step_cost += dex_omen_cost

        dist = model.roll_distribution(mask, jp, js, pr, sr, orb_tier=orb_tier, side_restrict=restrict)
        if not dist:
            return 0.0, 0.0

        prob = 0.0
        exp_c = step_cost
        for k, idx, p_k in dist:
            if k == "hit":
                side = targets[idx]["side"]
                sp, sc = dp(pr - (1 if side == "prefix" else 0), sr - (1 if side == "suffix" else 0), mask | (1 << idx), jp, js)
            elif k == "miss_p":
                sp, sc = dp(pr - 1, sr, mask, jp + 1, js)
            else:
                sp, sc = dp(pr, sr - 1, mask, jp, js + 1)
            prob += p_k * sp
            exp_c += p_k * sc
        return prob, exp_c

    return dp(p_open, s_open, 0, 0, 0)


def build_pareto_plans(
    ctx: CraftingContext,
    *,
    missing_targets: list[dict[str, Any]],
    all_targets: list[dict[str, Any]],
    used_groups: set[str],
    p_open: int,
    s_open: int,
    semi_base_cost: float,
    white_base_cost: float,
    semi_mdp: dict[str, Any] | None,
    raw_mdp: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """HTC Step Planner：生成候选工艺配方并筛选帕累托前沿（不被同时更便宜且概率更高的方案支配）。"""
    rates = ctx.rates
    p_ex = {
        "basic": lookup_currency_price("exalted", rates),
        "greater": lookup_currency_price("greater_exalted", rates),
        "perfect": lookup_currency_price("perfect_exalted", rates),
    }
    p_sin = lookup_omen_price("Omen of Sinistral Exaltation", rates)
    p_dex = lookup_omen_price("Omen of Dextral Exaltation", rates)

    candidates: list[dict[str, Any]] = []

    if missing_targets:
        model = _SlotRollModel(ctx.pool, used_groups, missing_targets, min_tier_mode=ctx.min_tier_mode)
        for tier in ("basic", "greater", "perfect"):
            for use_omen in (False, True):
                prob, cur_c = _eval_fixed_exalt_sequence(
                    model,
                    p_open,
                    s_open,
                    orb_tier=tier,
                    use_side_omen=use_omen,
                    orb_cost=p_ex[tier],
                    sin_omen_cost=p_sin,
                    dex_omen_cost=p_dex,
                )
                if prob <= 1e-7:
                    continue
                per_try = semi_base_cost + cur_c
                exp_c = per_try / prob
                p80_c = _p80_attempts(prob) * per_try
                title = (
                    f"半成品 + 左右预兆 + {ORB_TIER_ZH[tier]}崇高"
                    if use_omen
                    else f"半成品 + {ORB_TIER_ZH[tier]}崇高直补"
                )
                actions = [
                    f"半成品底子 ({semi_base_cost:.2f}D)",
                    *([f"左/右旋崇高预兆"] if use_omen else []),
                    f"{ORB_TIER_ZH[tier]}崇高石",
                ]
                candidates.append({
                    "id": f"semi_{tier}_{'omen' if use_omen else 'plain'}",
                    "origin": "semi",
                    "title": title,
                    "actions": actions,
                    "hitProbability": round(prob, 5),
                    "perTryCostDivine": round(per_try, 2),
                    "expectedCostDivine": round(exp_c, 2),
                    "p80CostDivine": round(p80_c, 2),
                    "steps": [
                        {
                            "step": 1,
                            "action": f"买非腐化半成品底子（剩 {p_open}前/{s_open}后）",
                            "costDivine": round(semi_base_cost, 2),
                            "detail": "锁定最难出的核心词缀，确保未腐化。",
                        },
                        {
                            "step": 2,
                            "action": title,
                            "costDivine": round(cur_c, 3),
                            "successChance": round(prob, 4),
                            "detail": f"单轮命中率 {prob * 100:.2f}%，均耗通货 {cur_c:.2f} D。",
                        },
                    ],
                })

    if semi_mdp and semi_mdp.get("feasible"):
        candidates.append({
            "id": "semi_mdp_policy",
            "origin": "semi",
            "title": "HTC 最优半成品策略（含预兆/止损）",
            "actions": semi_mdp.get("currenciesUsed") or ["MDP 最优路径"],
            "hitProbability": semi_mdp["hitProbability"],
            "perTryCostDivine": round(semi_base_cost, 2),
            "expectedCostDivine": semi_mdp["expectedCostDivine"],
            "p80CostDivine": semi_mdp["p80CostDivine"],
            "steps": semi_mdp["steps"],
        })

    if raw_mdp and raw_mdp.get("feasible"):
        candidates.append({
            "id": "raw_mdp_policy",
            "origin": "raw",
            "title": "HTC 最优白底自做策略（蜕变/精华/预兆）",
            "actions": raw_mdp.get("currenciesUsed") or ["白底 MDP 最优路径"],
            "hitProbability": raw_mdp["hitProbability"],
            "perTryCostDivine": round(white_base_cost, 2),
            "expectedCostDivine": raw_mdp["expectedCostDivine"],
            "p80CostDivine": raw_mdp["p80CostDivine"],
            "steps": raw_mdp["steps"],
        })

    # 按来源分别去重与帕累托前沿筛选（不保留既更贵又概率更低的劣解）
    pareto: list[dict[str, Any]] = []
    seen_sig: set[tuple[str, float, float]] = set()
    for c in candidates:
        sig = (c["origin"], round(c["expectedCostDivine"], 2), round(c["hitProbability"], 4))
        if sig in seen_sig:
            continue
        dominated = False
        for other in candidates:
            if other is c or other["origin"] != c["origin"]:
                continue
            if (
                other["expectedCostDivine"] <= c["expectedCostDivine"] - 1e-4
                and other["hitProbability"] >= c["hitProbability"] - 1e-6
            ):
                dominated = True
                break
        if not dominated:
            seen_sig.add(sig)
            pareto.append(c)

    pareto.sort(key=lambda x: (x["expectedCostDivine"], -x["hitProbability"]))
    return pareto[:6]


def _extract_state(ctx: CraftingContext) -> tuple[set[str], int, int, list[dict[str, Any]], list[dict[str, Any]]]:
    used_groups = {e["group"] for e in ctx.matched_existing if e.get("matched") and e.get("group")}
    p_used = sum(1 for e in ctx.matched_existing if e.get("matched") and e.get("side") == "prefix")
    s_used = sum(1 for e in ctx.matched_existing if e.get("matched") and e.get("side") == "suffix")
    p_open = max(0, 3 - p_used)
    s_open = max(0, 3 - s_used)

    all_targets: list[dict[str, Any]] = []
    seen_all: set[str] = set()
    for w in ctx.matched_wanted:
        if not w.get("matched") or not w.get("group") or w["group"] in seen_all:
            continue
        seen_all.add(w["group"])
        all_targets.append(w)

    missing = [w for w in all_targets if w["group"] not in used_groups]
    return used_groups, p_open, s_open, missing, all_targets


class OmenTargetedStrategy(CraftingStrategy):
    """预兆定向 + 高阶/完美崇高策略（对比普通崇高直补）。"""

    kind = "omen_targeted"
    label = "预兆定向 + 高阶崇高"
    label_en = "Omen Targeted + Tiered Exalt"

    def estimate(self, ctx: CraftingContext) -> dict[str, Any]:
        used_groups, p_open, s_open, missing, _ = _extract_state(ctx)
        if not missing:
            return {
                "strategy": self.kind,
                "label": self.label,
                "origin": "semi",
                "solverKind": "dp",
                "hitProbability": 1.0,
                "expectedAttempts": 1.0,
                "expectedCostDivine": round(ctx.semi_base_price or 0.0, 2),
                "p80CostDivine": round(ctx.semi_base_price or 0.0, 2),
                "brickRiskNote": "半成品已包含全部目标词缀，无需使用预兆。",
                "stepsNote": "直接可用。",
            }

        model = _SlotRollModel(ctx.pool, used_groups, missing, min_tier_mode=ctx.min_tier_mode)
        rates = ctx.rates
        p_sin = lookup_omen_price("Omen of Sinistral Exaltation", rates)
        p_dex = lookup_omen_price("Omen of Dextral Exaltation", rates)
        base_d = ctx.semi_base_price if ctx.semi_base_price is not None else 0.0

        best: dict[str, Any] | None = None
        for tier in ("basic", "greater", "perfect"):
            orb_c = lookup_currency_price("exalted" if tier == "basic" else f"{tier}_exalted", rates)
            prob, cur_c = _eval_fixed_exalt_sequence(
                model,
                p_open,
                s_open,
                orb_tier=tier,
                use_side_omen=True,
                orb_cost=orb_c,
                sin_omen_cost=p_sin,
                dex_omen_cost=p_dex,
            )
            if prob <= 1e-8:
                continue
            per_try = base_d + cur_c
            exp_c = per_try / prob
            if best is None or exp_c < best["exp_c"]:
                best = {
                    "tier": tier,
                    "prob": prob,
                    "cur_c": cur_c,
                    "per_try": per_try,
                    "exp_c": exp_c,
                    "p80_c": _p80_attempts(prob) * per_try,
                }

        if best is None:
            return {
                "strategy": self.kind,
                "label": self.label,
                "origin": "semi",
                "solverKind": "dp",
                "hitProbability": 0.0,
                "expectedAttempts": None,
                "expectedCostDivine": None,
                "p80CostDivine": None,
                "brickRiskNote": f"剩余空位（{p_open}前/{s_open}后）不足以通过预兆补齐缺失词缀。",
                "stepsNote": "空位不足。",
            }

        tier_zh = ORB_TIER_ZH[best["tier"]]
        return {
            "strategy": self.kind,
            "label": f"预兆定向 + {tier_zh}崇高",
            "origin": "semi",
            "solverKind": "dp",
            "hitProbability": round(best["prob"], 5),
            "expectedAttempts": round(1.0 / best["prob"], 2),
            "expectedCostDivine": round(best["exp_c"], 2),
            "p80CostDivine": round(best["p80_c"], 2),
            "brickRiskNote": f"结合左/右旋崇高预兆锁侧与{tier_zh}崇高截断低 T 词缀，单轮命中率 {best['prob'] * 100:.2f}%。",
            "stepsNote": f"单轮均耗通货 {best['cur_c']:.2f} D，期望 {1.0 / best['prob']:.1f} 轮成装。",
            "currencyUsed": [f"左/右旋崇高预兆", f"{tier_zh}崇高石"],
            "en": {
                "label": f"Omen Targeted + {best['tier'].title()} Exalt",
                "brickRiskNote": f"Side-locked exalt with {best['tier']} orb floor; {best['prob'] * 100:.2f}% per try.",
                "stepsNote": f"Avg currency {best['cur_c']:.2f} D/try.",
            },
        }


class HtcSemiPolicyStrategy(CraftingStrategy):
    """HTC 风格半成品 MDP 最优策略（自动权衡普通/高阶/完美崇高、左右预兆、剥离/削减救底子与止损重开）。"""

    kind = "htc_policy"
    label = "HTC 半成品最优策略"
    label_en = "HTC Optimal Semi Policy (MDP)"

    def estimate(self, ctx: CraftingContext) -> dict[str, Any]:
        used_groups, p_open, s_open, missing, _ = _extract_state(ctx)
        fixed_p = 3 - p_open
        fixed_s = 3 - s_open
        frac_p = 1 if (ctx.fractured_group and any(e.get("group") == ctx.fractured_group and e.get("side") == "prefix" for e in ctx.matched_existing)) else 0
        frac_s = 1 if (ctx.fractured_group and any(e.get("group") == ctx.fractured_group and e.get("side") == "suffix" for e in ctx.matched_existing)) else 0
        base_d = ctx.semi_base_price if ctx.semi_base_price is not None else 0.0

        res = solve_crafting_mdp(
            ctx,
            origin="semi",
            targets=missing,
            locked_groups=used_groups,
            fixed_p=fixed_p,
            fixed_s=fixed_s,
            base_cost=base_d,
            fractured_p=frac_p,
            fractured_s=frac_s,
        )
        if not res.get("feasible"):
            return {
                "strategy": self.kind,
                "label": self.label,
                "origin": "semi",
                "solverKind": "mdp_policy",
                "hitProbability": 0.0,
                "expectedAttempts": None,
                "expectedCostDivine": None,
                "p80CostDivine": None,
                "brickRiskNote": res.get("reason") or "半成品空位不足。",
                "stepsNote": "不可行。",
            }

        rec_note = (
            "已自动启用「歪了用剥离/削减预兆救底子」最优策略，大幅降低炸高价底子损耗。"
            if res.get("recoveryUsed")
            else "当前底子价相对低廉，歪了直接止损换新底子比买高价剥离预兆更省。"
        )
        return {
            "strategy": self.kind,
            "label": self.label,
            "origin": "semi",
            "solverKind": "mdp_policy",
            "hitProbability": res["hitProbability"],
            "expectedAttempts": res["expectedAttempts"],
            "expectedCostDivine": res["expectedCostDivine"],
            "p80CostDivine": res["p80CostDivine"],
            "brickRiskNote": rec_note,
            "stepsNote": f"MDP 最优期望 {res['expectedCostDivine']} D（P80 风险 {res['p80CostDivine']} D）。",
            "currencyUsed": res.get("currenciesUsed") or [],
            "recipeSteps": res.get("steps") or [],
            "_rawMdp": res,
            "en": {
                "label": self.label_en,
                "brickRiskNote": "Optimal recovery via Annul/Whittling omens active." if res.get("recoveryUsed") else "Restarting base beats expensive recovery omens.",
                "stepsNote": f"MDP optimal expected {res['expectedCostDivine']} D (P80 {res['p80CostDivine']} D).",
            },
        }


class HtcRawPolicyStrategy(CraftingStrategy):
    """HTC 风格白底从零自做 MDP 最优策略（白底 → 蜕变/精华 → 增幅/富豪/高阶精华 → 预兆崇高 + 止损）。"""

    kind = "htc_raw_policy"
    label = "HTC 白底自做最优路径"
    label_en = "HTC Optimal Raw Base Policy (MDP)"

    def estimate(self, ctx: CraftingContext) -> dict[str, Any]:
        _, _, _, _, all_targets = _extract_state(ctx)
        if not all_targets:
            return {
                "strategy": self.kind,
                "label": self.label,
                "origin": "raw",
                "solverKind": "mdp_policy",
                "hitProbability": 0.0,
                "expectedAttempts": None,
                "expectedCostDivine": None,
                "p80CostDivine": None,
                "brickRiskNote": "请先指定有效目标词缀。",
                "stepsNote": "无目标词缀。",
            }

        white_base = ctx.white_base_price if ctx.white_base_price is not None and ctx.white_base_price >= 0 else 0.05
        res = solve_crafting_mdp(
            ctx,
            origin="raw",
            targets=all_targets,
            locked_groups=set(),
            fixed_p=0,
            fixed_s=0,
            base_cost=white_base,
        )
        if not res.get("feasible"):
            return {
                "strategy": self.kind,
                "label": self.label,
                "origin": "raw",
                "solverKind": "mdp_policy",
                "hitProbability": 0.0,
                "expectedAttempts": None,
                "expectedCostDivine": None,
                "p80CostDivine": None,
                "brickRiskNote": res.get("reason") or "目标词缀组合超出 3前3后 上限。",
                "stepsNote": "不可行。",
            }

        return {
            "strategy": self.kind,
            "label": self.label,
            "origin": "raw",
            "solverKind": "mdp_policy",
            "hitProbability": res["hitProbability"],
            "expectedAttempts": res["expectedAttempts"],
            "expectedCostDivine": res["expectedCostDivine"],
            "p80CostDivine": res["p80CostDivine"],
            "brickRiskNote": (
                f"从白底(约 {white_base:.2f} D)起手，按 MDP 最优路径（精华起手/高阶蜕变+预兆崇高+自动止损）"
                f"期望总价 {res['expectedCostDivine']} D。"
            ),
            "stepsNote": f"按步骤操作，P80 防黑预算约 {res['p80CostDivine']} D。",
            "currencyUsed": res.get("currenciesUsed") or [],
            "recipeSteps": res.get("steps") or [],
            "_rawMdp": res,
            "en": {
                "label": self.label_en,
                "brickRiskNote": f"From white base (~{white_base:.2f} D), optimal MDP path expected {res['expectedCostDivine']} D.",
                "stepsNote": f"P80 budget ~{res['p80CostDivine']} D.",
            },
        }
