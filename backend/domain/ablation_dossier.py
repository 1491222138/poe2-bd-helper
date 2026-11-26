"""单目标 BD 全量引擎消融剖析账本（装备 + 珠宝 + 技能/辅助宝石 + 全量词缀与符文选择目的 + 造价）。

核心目标：
  1. 在分析目标 BD 时并发跑完全部物件消融（装备 / 珠宝 / 门槛补偿对照 / 技能组 / 辅助宝石 / 21/23 降配）
     与全部装备/珠宝的逐条词缀 + 符文剥离消融；
  2. 将引擎实算差值（ΔDPS% / ΔEHP% / ΔMana% / ΔSpirit / 四抗 / 属性红线）与静态天赋/符文联动画像、
     市集价格快照合并，输出每个物件的核心作用、造价性价比、以及每条词缀明确的选择目的；
  3. 持久化缓存（~/.poe2-workbench/ablation_dossier_cache.json），直接反哺后期抄作业、平替锁词、做装与 AI 顾问。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from typing import Any, Callable

from backend.data import patch_dict_updater
from backend.domain import affix_analyzer, build_profile, gap_analyzer
from backend.domain.common import (
    CURSE_MARK_RE as _CURSE_MARK_RE,
    DEFENSE_SKILL_RE as _DEFENSE_SKILL_RE,
    DEFENSE_SUPPORT_RE as _DEFENSE_SUPPORT_RE,
    DESC_DEFENSE_KW_RE as _DESC_DEFENSE_KW_RE,
    FALLBACK_CHASE_ESTIMATES as _FALLBACK_CHASE_ESTIMATES,
    SKILL_DEFENSE_KNOWLEDGE as _SKILL_DEFENSE_KNOWLEDGE,
    SPIRIT_AURA_RE as _SPIRIT_AURA_RE,
    SUPPORT_CRIT_RE as _SUPPORT_CRIT_RE,
    SUPPORT_UTILITY_RE as _SUPPORT_UTILITY_RE,
    TRAVEL_SKILL_RE as _TRAVEL_RE,
    TRIGGER_RE as _TRIGGER_RE,
    is_inactive_weapon_slot as _is_inactive_weapon_slot,
    pct_loss as _pct_loss,
    safe_num as _num,
)
from backend.domain.dossier_cache import (
    DOSSIER_CACHE_FILENAME,
    DOSSIER_SHARD_DIRNAME,
    DOSSIER_VERSION,
    _CACHE_MAX_ENTRIES,
    _base_cache_key,
    _cache_path,
    _dossier_cache_key,
    _is_compatible_cached_dossier,
    _load_base_cache,
    _load_cache,
    _price_signature,
    _prune_shard_cache,
    _save_cache,
    _shard_dir,
    _shard_path,
    compute_build_hash,
    get_cached_base_sim,
    get_cached_dossier_by_build,
    update_cached_base_sim,
)
from backend.engine import pob_parser, rune_index, runefix, tree_index, xml_mutator
from backend.market import market_pricer
from backend.market.price_store import STATE_DIR

# 严格仅排除 100% 确定不参与 PoB2 攻防/精魂/抗性/属性计算的独立词条行（不含复合词条）
_ZERO_COMBAT_IMPACT_MOD_RE = re.compile(
    r"^\+?\d+(?:\.\d+)?%\s+increased\s+(light\s+radius|rarity\s+of\s+items\s+found)$",
    re.I,
)


def _is_wrapped_continuation_line(line: str) -> bool:
    """识别 PoB 暗金多行描述折行产生的续行尾巴（如 'your Ailments on them'），避免重复剥离同一句。"""
    s = (line or "").strip()
    return bool(s) and bool(re.match(r"^[a-z]", s))


def _resolve_item_cost(
    it: dict[str, Any],
    price_snapshot: dict[str, Any] | None,
    price_overrides: dict[str, float] | None,
    ninja_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    slot = str(it.get("slot") or "")
    name = str(it.get("name") or "")
    rarity = str(it.get("rarity") or "").upper()

    exact = budget = semi = None
    source = "unknown"
    trade_url_str = ""
    listings = None

    if price_overrides:
        for k, v in price_overrides.items():
            if k.lower() == slot.lower() and isinstance(v, (int, float)) and v > 0:
                exact = round(float(v), 2)
                source = "manual"
                break

    if price_snapshot:
        for e in price_snapshot.get("entries") or []:
            e_slot = str(e.get("slot") or "").lower()
            e_name = str(e.get("itemName") or e.get("name") or "").lower()
            if e_slot != slot.lower() and (not name or e_name != name.lower()):
                continue
            val = _num(e.get("divineValue"), -1.0)
            if val <= 0:
                continue
            kind = str(e.get("kind") or "exact").lower()
            e_src = str(e.get("source") or "trade").lower()
            if e.get("tradeUrl") and not trade_url_str:
                trade_url_str = str(e["tradeUrl"])
            if e.get("listings") and listings is None:
                listings = int(e["listings"])
            if kind == "exact":
                if source != "manual":
                    exact = round(val, 2)
                    source = "manual" if "manual" in e_src else ("ninja" if "ninja" in e_src else "trade")
            elif kind == "budget":
                budget = round(val, 2)
                if source == "unknown":
                    source = "trade"
            elif kind == "semi":
                semi = round(val, 2)
                if source == "unknown":
                    source = "trade"

        prices_map = price_snapshot.get("prices") or {}
        if exact is None:
            for k, v in prices_map.items():
                if k.lower() in (slot.lower(), name.lower()) and isinstance(v, (int, float)) and v > 0:
                    exact = round(float(v), 2)
                    source = "ninja" if rarity == "UNIQUE" else "trade"
                    break

    # 若快照无该暗金实时价，直接查 poe.ninja 当前赛季实价（严禁用过期硬编码估价误导造价）
    if exact is None and rarity == "UNIQUE" and ninja_index:
        hit = market_pricer.lookup_ninja_item(name, ninja_index)
        if hit and float(hit.get("divineValue") or 0) > 0:
            exact = round(float(hit["divineValue"]), 2)
            source = "ninja"
            listings = int(hit.get("listingCount") or 0) or listings

    estimated = exact if exact is not None else (budget if budget is not None else semi)

    return {
        "estimatedDivine": round(estimated, 2) if estimated is not None else None,
        "exactDivine": exact,
        "budgetDivine": budget,
        "semiDivine": semi,
        "priceSource": source,
        "listings": listings,
        "tradeUrl": trade_url_str or None,
        "costSharePct": None,
        "dpsPerDivine": None,
        "ehpPerDivine": None,
    }


def _classify_affix_purpose(
    mod_line: str,
    *,
    dps_loss: float,
    ehp_loss: float,
    mana_loss: float,
    spirit_loss: float,
    max_res_drop: float,
    attr_broken: bool,
    triggered_redlines: list[str],
    dmeta: dict[str, Any],
    is_speed_weapon: bool,
    slot: str,
    is_unique: bool = False,
) -> dict[str, Any]:
    origin = str(dmeta.get("origin") or "explicit")
    side = str(dmeta.get("side") or "unknown")
    is_anoint = bool(dmeta.get("isAnoint"))
    is_dead = dmeta.get("role") == "dead"
    ctx_score = int(dmeta.get("contextualScore") or 0)
    craft_diff = str(dmeta.get("craftDifficulty") or "easy")
    syn_tags = list(dmeta.get("synergyTags") or [])
    granted_info = dmeta.get("grantedSkill") if isinstance(dmeta.get("grantedSkill"), dict) else None
    t = mod_line.lower()

    has_passive_syn = any("🌳" in tag or "💎" in tag for tag in syn_tags)
    breaks_redline = attr_broken or spirit_loss >= 15 or bool(triggered_redlines)

    # 0. 基底自带技能专项评估（联动引擎实算 + 辅助挂载 + 功能损失检测，绝不能笼统标成“面板无直显”）
    if granted_info or re.match(r"^grants\s+skill:", t):
        g_name = str((granted_info or {}).get("skillName") or mod_line)
        is_imp = bool((granted_info or {}).get("isImportant"))
        sup_list = list((granted_info or {}).get("linkedSupports") or [])
        func_desc = str((granted_info or {}).get("functionalDesc") or "基底赋予技能")
        func_loss_raw = str((granted_info or {}).get("functionalLoss") or "")
        sup_note = f"（已连 {len(sup_list)} 颗辅助：{' / '.join(sup_list[:3])}）" if sup_list else ""

        metrics = []
        if dps_loss >= 0.5:
            metrics.append(f"-{dps_loss:.1f}% DPS")
        if ehp_loss >= 0.5:
            metrics.append(f"-{ehp_loss:.1f}% EHP")
        if max_res_drop >= 5.0:
            metrics.append(f"-{max_res_drop:.0f}% 抗性")
        if spirit_loss >= 5.0:
            metrics.append(f"-{spirit_loss:.0f} 精魂")

        has_stat_loss = bool(metrics)
        if has_stat_loss or is_imp:
            m_txt = f"移除该技能实算损失 {' / '.join(metrics)}；" if has_stat_loss else ""
            loss_tag = (
                f"功能损失：{func_loss_raw or f'缺失「{g_name}」'}"
                if not has_stat_loss
                else (f"连 {len(sup_list)} 辅助 · 不可换底" if sup_list else "换底即失该技能")
            )
            action_note = (
                "暗金固有技能，无需做装，挑货注意技能等级与关键 roll。"
                if is_unique
                else "换用其他普通底子会直接造成功能/循环损失，买平替或半成品必须锁对基底！"
            )
            return {
                "tier": "must",
                "purposeKind": "synergy" if not has_stat_loss else ("core_dps" if dps_loss >= ehp_loss else "core_defense"),
                "purposeLabel": "基底自带核心技",
                "purposeDetail": f"{m_txt}基底自带「{g_name}」{sup_note}（{func_desc}）。{action_note}",
                "copyAction": "skip" if is_unique else "lock",
                "functionalLoss": loss_tag,
            }

        return {
            "tier": "cut",
            "purposeKind": "utility",
            "purposeLabel": "默认普攻基底技",
            "purposeDetail": f"同类别装备默认自带「{g_name}」，当前构筑未将其作为主力且未连辅助，换同大类基底无功能损失。",
            "copyAction": "skip",
            "functionalLoss": "未启用(无功能损失)",
        }

    # 0b. 基底额外技能槽（如 Unset Ring）与特殊技能基底代价（-1 Prefix/Suffix allowed）
    if re.match(r"^grants\s+1\s+additional\s+skill\s+slot", t):
        return {
            "tier": "must",
            "purposeKind": "synergy",
            "purposeLabel": "基底额外技能槽",
            "purposeDetail": "潜能戒（Unset Ring）核心基底功能，换其他戒指底子会直接少 1 个技能槽位。",
            "copyAction": "skip" if is_unique else "lock",
            "functionalLoss": "功能损失：少 1 个技能槽",
        }
    if origin == "implicit" and re.match(r"^-1\s+(prefix|suffix)\s+modifier\s+allowed$", t):
        return {
            "tier": "nice",
            "purposeKind": "utility",
            "purposeLabel": "技能基底代价",
            "purposeDetail": "Absent / Lament / Portent 等技能项链的固有基底代价，用以换取基底自带精魂技能。",
            "copyAction": "skip",
            "functionalLoss": "基底换技能代价",
        }

    # 1. 死词条 / 构筑冲突
    if is_dead and dps_loss < 0.5 and ehp_loss < 0.5 and not breaks_redline:
        reason = syn_tags[0] if syn_tags else "与当前升华/基石体系冲突或对主技能无效"
        return {
            "tier": "cut",
            "purposeKind": "dead",
            "purposeLabel": "无效/冲突词条",
            "purposeDetail": f"{reason}，{'暗金附带词条无需关注 roll' if is_unique else '抄作业与搜平替时直接跳过'}。",
            "copyAction": "skip",
        }

    # 2. 涂油附魔（可自涂，搜货不锁）
    if is_anoint:
        parts = []
        if dps_loss >= 0.8:
            parts.append(f"+{dps_loss:.1f}% DPS")
        if ehp_loss >= 0.8:
            parts.append(f"+{ehp_loss:.1f}% EHP")
        stat_txt = " / ".join(parts) if parts else "机制/功能大点"
        return {
            "tier": "must" if (dps_loss >= 4.0 or ehp_loss >= 4.0) else "nice",
            "purposeKind": "synergy",
            "purposeLabel": "项链涂油大点",
            "purposeDetail": f"实算贡献 {stat_txt}；买项链时无需锁死，买完自己涂油即可。",
            "copyAction": "skip",
        }

    # 3. 强天赋 / 符文插槽联动
    if has_passive_syn and (dps_loss >= 1.5 or ehp_loss >= 1.5 or mana_loss >= 3.0 or ctx_score >= 88):
        syn_name = next((tag for tag in syn_tags if "🌳" in tag or "💎" in tag), syn_tags[0] if syn_tags else "天赋联动")
        metrics = []
        if dps_loss >= 0.8:
            metrics.append(f"+{dps_loss:.1f}% DPS")
        if ehp_loss >= 0.8:
            metrics.append(f"+{ehp_loss:.1f}% EHP")
        if mana_loss >= 2.0:
            metrics.append(f"+{mana_loss:.1f}% 蓝池")
        if spirit_loss >= 5:
            metrics.append(f"+{spirit_loss:.0f} 精魂")
        m_txt = f"（实算 {' / '.join(metrics)}）" if metrics else "（机制联动放大）"
        is_must = dps_loss >= 3.5 or ehp_loss >= 3.5 or spirit_loss >= 15 or ctx_score >= 100
        return {
            "tier": "must" if is_must else "nice",
            "purposeKind": "synergy",
            "purposeLabel": "天赋/插槽联动",
            "purposeDetail": f"{syn_name}{m_txt}，构筑定向放大的关键配合词条。",
            "copyAction": "skip" if is_unique else ("lock" if is_must else "downgrade"),
        }

    # 4. 穿戴 / 精魂 / 抗性硬门槛
    if attr_broken or spirit_loss >= 15 or any("精魂" in r or "力量" in r or "敏捷" in r or "智力" in r for r in triggered_redlines):
        reasons = []
        if spirit_loss >= 15:
            reasons.append(f"提供 {spirit_loss:.0f} 点精魂")
        if attr_broken:
            reasons.append("承担装备/宝石属性门槛")
        if dps_loss >= 2.0:
            reasons.append(f"+{dps_loss:.1f}% DPS")
        return {
            "tier": "must",
            "purposeKind": "threshold",
            "purposeLabel": "硬门槛锚点",
            "purposeDetail": f"{'，'.join(reasons) or '触发面板红线'}；去掉会卡技能/装备穿戴，{'挑暗金时优先保该项数值 roll' if is_unique else '平替需同步在别处补齐'}。",
            "copyAction": "skip" if is_unique else "lock",
        }

    # 5. 核心输出词缀
    if dps_loss >= 4.0:
        extra = f"、+{ehp_loss:.1f}% EHP" if ehp_loss >= 2.0 else ""
        tail = "暗金核心伤害词条，买暗金时重点看该项数值 roll（无需做装）。" if is_unique else "该部位最核心的伤害词条，买平替/做半成品优先保。"
        return {
            "tier": "must",
            "purposeKind": "core_dps",
            "purposeLabel": "核心输出乘区",
            "purposeDetail": f"剥离后掉 {dps_loss:.1f}% DPS{extra}，{tail}",
            "copyAction": "skip" if is_unique else "lock",
        }

    # 6. 核心防御 / 生存池词缀
    if ehp_loss >= 4.0 or max_res_drop >= 18.0:
        if max_res_drop >= 18.0 and ehp_loss < 4.0:
            return {
                "tier": "must" if triggered_redlines else "nice",
                "purposeKind": "core_defense",
                "purposeLabel": "抗性达标位",
                "purposeDetail": f"提供 {max_res_drop:.0f}% 抗性缺口补足；{'暗金附带抗性，预算紧可买低 roll' if is_unique else '可按全身配装灵活转移到其他部位'}。",
                "copyAction": "skip" if is_unique else "downgrade",
            }
        res_note = f"（含 {max_res_drop:.0f}% 抗性）" if max_res_drop >= 10 else ""
        return {
            "tier": "must",
            "purposeKind": "core_defense",
            "purposeLabel": "核心防御承伤",
            "purposeDetail": f"剥离后掉 {ehp_loss:.1f}% EHP{res_note}，支撑当前生存底线的关键防词。",
            "copyAction": "skip" if is_unique else ("lock" if craft_diff != "easy" else "downgrade"),
        }

    # 7. 魔力 / 续航 / 移速 / 攻速工具位
    if mana_loss >= 4.0 or re.search(r"mana regeneration|mana cost efficiency|mana on kill|leech", t):
        return {
            "tier": "must" if mana_loss >= 8.0 else "nice",
            "purposeKind": "sustain",
            "purposeLabel": "魔力/续航支撑",
            "purposeDetail": (
                f"支撑 {mana_loss:.1f}% 魔力池，保障大法师增伤或持续施法不断蓝。"
                if mana_loss >= 2.0
                else "保障技能蓝耗与刷图回蓝手感，缺蓝时优先保留。"
            ),
            "copyAction": "skip" if is_unique else ("lock" if mana_loss >= 8.0 else "downgrade"),
        }

    if is_speed_weapon and re.search(r"attack speed|skill speed|explicit speed modifier", t):
        return {
            "tier": "must",
            "purposeKind": "utility",
            "purposeLabel": "攻速工具核心",
            "purposeDetail": "攻速/位移副武器组核心手感词缀（主输出面板不直接显示，但决定跑图/叠层速度）。",
            "copyAction": "skip" if is_unique else "lock",
            "functionalLoss": "位移/叠层手感核心",
        }

    if re.search(r"movement speed", t) and "boot" in slot.lower():
        return {
            "tier": "must" if ctx_score >= 85 else "nice",
            "purposeKind": "utility",
            "purposeLabel": "刷图移速手感",
            "purposeDetail": "鞋子移速直接决定清图效率与走位容错（面板 DPS 不计入移速）。",
            "copyAction": "skip" if is_unique else ("lock" if ctx_score >= 85 else "downgrade"),
            "functionalLoss": "刷图跑速手感",
        }

    # 8. 中等增益 / 抗性补位 / PoB 未实算高分机制词缀
    if dps_loss >= 1.2 or ehp_loss >= 1.2 or max_res_drop >= 8.0:
        parts = []
        if dps_loss >= 1.2:
            parts.append(f"+{dps_loss:.1f}% DPS")
        if ehp_loss >= 1.2:
            parts.append(f"+{ehp_loss:.1f}% EHP")
        if max_res_drop >= 8.0:
            parts.append(f"+{max_res_drop:.0f}% 抗性")
        is_res_only = max_res_drop >= 8.0 and dps_loss < 1.0 and ehp_loss < 2.5
        return {
            "tier": "nice",
            "purposeKind": "core_defense" if is_res_only else ("core_dps" if dps_loss >= ehp_loss else "core_defense"),
            "purposeLabel": "抗性补位(可转移)" if is_res_only else "中等增益词条",
            "purposeDetail": f"实算贡献 {' / '.join(parts)}；{'暗金挑货时可按预算适当放宽该项 roll' if is_unique else '预算吃紧时可降 1~2 档 T 级或后补'}。",
            "copyAction": "skip" if is_unique else ("downgrade" if not is_res_only else "craft_later"),
        }

    if ctx_score >= 95 and origin not in ("implicit", "enchant"):
        return {
            "tier": "must" if ctx_score >= 105 else "nice",
            "purposeKind": "utility",
            "purposeLabel": "机制/未实算词条",
            "purposeDetail": "PoB2 面板未直接折算数值，但属于构筑高价值机制/功能词缀，建议核对实战作用。",
            "copyAction": "skip" if is_unique else ("lock" if ctx_score >= 105 else "downgrade"),
            "functionalLoss": "高价值机制(未折算面板)",
        }

    if origin == "implicit":
        return {
            "tier": "nice",
            "purposeKind": "utility",
            "purposeLabel": "基底自带词条",
            "purposeDetail": "由装备基底类型自带，选对底子即拥有。",
            "copyAction": "skip",
        }

    # 9. 低收益 / 溢价 / 可砍词条
    if re.search(r"rarity of items found", t):
        return {
            "tier": "cut",
            "purposeKind": "cuttable",
            "purposeLabel": "打宝溢价(可砍)",
            "purposeDetail": "物品稀有度不提供攻防面板，" + ("暗金买低稀有度 roll 更省钱。" if is_unique else "抄作业起步直接砍掉可大幅降价。"),
            "copyAction": "skip" if is_unique else "cut",
        }

    if is_unique:
        return {
            "tier": "nice" if re.search(r"enemy|presence|charge|ailment|flask|charm|trigger|curse|mark|skill|every|cannot|instead|become", t) else "cut",
            "purposeKind": "utility" if re.search(r"enemy|presence|charge|ailment|flask|charm|trigger|curse|mark|skill|every|cannot|instead|become", t) else "cuttable",
            "purposeLabel": "暗金特殊机制" if re.search(r"enemy|presence|charge|ailment|flask|charm|trigger|curse|mark|skill|every|cannot|instead|become", t) else "暗金附带词条",
            "purposeDetail": (
                "暗金固有特殊机制词条（PoB2 可能未直接折算进静态面板，实战不可忽略）。"
                if re.search(r"enemy|presence|charge|ailment|flask|charm|trigger|curse|mark|skill|every|cannot|instead|become", t)
                else f"引擎实算贡献 DPS {dps_loss:+.1f}% / EHP {ehp_loss:+.1f}%，买暗金时无需为此项高 roll 付溢价。"
            ),
            "copyAction": "skip",
            "functionalLoss": "暗金机制(实战生效)" if re.search(r"enemy|presence|charge|ailment|flask|charm|trigger|curse|mark|skill|every|cannot|instead|become", t) else "",
        }

    return {
        "tier": "cut",
        "purposeKind": "cuttable",
        "purposeLabel": "低收益/可砍词条",
        "purposeDetail": (
            f"引擎实算仅贡献 DPS {dps_loss:+.1f}% / EHP {ehp_loss:+.1f}%，找平替或半成品时优先舍弃以压低造价。"
        ),
        "copyAction": "cut",
    }


def _classify_item_role_and_copy(
    it: dict[str, Any],
    *,
    dps_loss: float,
    ehp_loss: float,
    mana_loss: float,
    spirit_loss: float,
    max_res_drop: float,
    attr_broken: bool,
    comp_dps: float | None,
    comp_ehp: float | None,
    inactive_ws: bool,
    has_unparsed: bool,
    deep: dict[str, Any],
    cost: dict[str, Any],
    affix_stats: dict[str, int],
) -> dict[str, Any]:
    rarity = str(it.get("rarity") or "").upper()
    slot = str(it.get("slot") or "")
    name = str(it.get("name") or it.get("baseType") or "")
    is_unique = rarity == "UNIQUE"
    est_d = cost.get("estimatedDivine")

    # 若脱装触发属性/精魂断裂导致原 dps_loss 虚高，用门槛补偿后的净值判断真实输出角色
    net_dps = comp_dps if comp_dps is not None else dps_loss
    net_ehp = comp_ehp if comp_ehp is not None else ehp_loss
    threshold_amplified = (
        comp_dps is not None and (dps_loss - comp_dps) >= 15.0
    ) or (attr_broken and net_dps < 8.0)

    if inactive_ws:
        role_kind = "mechanic"
        role_label = "副武器组功能位"
        summary = "位于当前未激活武器组（切换武器时生效，用于跑图/叠层/切防御）。"
    elif deep.get("isSpeedWeapon"):
        role_kind = "mechanic"
        role_label = "攻速/位移工具"
        summary = "专职高攻速位移或触发工具组，重点锁攻速与速度增幅，无需为低 T 点伤溢价。"
    elif threshold_amplified:
        role_kind = "threshold"
        role_label = "门槛核心挂件"
        summary = f"直脱会断属性/精魂门槛（补门槛后净贡献 DPS -{net_dps:.1f}% / EHP -{net_ehp:.1f}%）。"
    elif net_dps >= 14.0 and net_ehp >= 10.0:
        role_kind = "core_dps"
        role_label = "攻防双核心"
        summary = f"同时扛住主力输出（-{net_dps:.1f}% DPS）与生存底线（-{net_ehp:.1f}% EHP）。"
    elif net_dps >= 10.0:
        role_kind = "core_dps"
        role_label = "核心输出件"
        summary = f"主力伤害支柱，脱掉损失 {net_dps:.1f}% DPS。"
    elif net_ehp >= 12.0 or mana_loss >= 14.0:
        role_kind = "core_defense"
        role_label = "核心生存件"
        pool_txt = f"EHP -{net_ehp:.1f}%" + (f" / 魔力 -{mana_loss:.1f}%" if mana_loss >= 8.0 else "")
        summary = f"防御体系支柱（{pool_txt}）" + (f"，联动 {' / '.join((deep.get('passiveSynergies') or [])[:1])}" if deep.get("passiveSynergies") else "。")
    elif spirit_loss >= 25.0:
        role_kind = "threshold"
        role_label = "精魂支柱"
        summary = f"提供 {spirit_loss:.0f} 点精魂上限，支撑多开光环与辅助技能运转。"
    elif has_unparsed and is_unique:
        role_kind = "mechanic"
        role_label = "机制暗金(未实算)"
        summary = "含 PoB2 引擎未完全实算的机制词条，实战不可仅看面板差值判断。"
    elif est_d is not None and est_d >= 15.0 and net_dps < 8.0 and net_ehp < 8.0 and not has_unparsed:
        role_kind = "luxury"
        role_label = "后期溢价件"
        summary = f"造价偏高（~{est_d}D）但实算仅贡献 DPS -{net_dps:.1f}% / EHP -{net_ehp:.1f}%，开荒可延后。"
    elif max_res_drop >= 25.0:
        role_kind = "core_defense"
        role_label = "抗性/功能补位"
        summary = f"主要承担 {max_res_drop:.0f}% 抗性补齐，属于全身配装可灵活调整的槽位。"
    elif net_dps >= 3.0 or net_ehp >= 3.0:
        role_kind = "utility"
        role_label = "稳健增益件"
        summary = f"平稳补强 DPS -{net_dps:.1f}% / EHP -{net_ehp:.1f}%。"
    else:
        role_kind = "utility"
        role_label = "功能/过渡位"
        summary = "纯面板占比不高，多为手感、打宝或可灵活平替的过渡槽位。"

    # 抄作业分层（mandatory / core / flexible / luxury）
    if (is_unique and (net_dps >= 16.0 or net_ehp >= 18.0 or has_unparsed or spirit_loss >= 35)) or (
        "weapon" in slot.lower() and net_dps >= 18.0
    ):
        copy_tier, copy_tier_label = "mandatory", "抄作业必保"
    elif est_d is not None and est_d >= 18.0 and net_dps < 9.0 and net_ehp < 9.0 and not has_unparsed:
        copy_tier, copy_tier_label = "luxury", "后期奢侈(先平替)"
    elif net_dps >= 7.0 or net_ehp >= 9.0 or spirit_loss >= 25.0 or attr_broken:
        copy_tier, copy_tier_label = "core", "优先核心"
    else:
        copy_tier, copy_tier_label = "flexible", "可平替/后补"

    # 生成简洁可落地的抄作业建议
    if is_unique:
        sock_a = deep.get("socketAnalysis") or {}
        extra_tip = (
            f"（注意该暗金带瓦尔 {sock_a['socketCount']} 孔，按预算决定买普通款自打孔还是直买瓦尔多孔）"
            if sock_a.get("exceedsCraftableSockets")
            else "（暗金直接买成品即可，无需复杂做装，买后按需打孔插符文/涂油/挑瓦尔基底）"
        )
        if copy_tier == "mandatory":
            advice = f"核心暗金「{name}」优先直买{extra_tip}。"
        elif copy_tier == "luxury":
            advice = f"属于后期追梦/高溢价暗金，前期可用高抗性/主属性黄装平替，攒够预算再直买。"
        else:
            advice = f"性价比不错的暗金件，按预算直买低 roll 平价款即可{extra_tip}。"
    else:
        must_cnt = affix_stats.get("must", 0)
        cut_cnt = affix_stats.get("cut", 0) + affix_stats.get("dead", 0)
        base_lock_prefix = f"必须锁基底「{it.get('baseType') or ''}」（{deep.get('baseTypeReason')}）；" if deep.get("requireBaseType") else ""
        if must_cnt > 0:
            advice = f"{base_lock_prefix}搜平替保住 {must_cnt} 条必保词缀即可" + (f"，其余 {cut_cnt} 条低收益/抗性词缀直接放开砍价。" if cut_cnt > 0 else "。")
        else:
            advice = f"{base_lock_prefix}该部位无硬锁死词缀，按自身抗性/属性缺口买便宜高性价比黄装过渡即可。"

    return {
        "roleKind": role_kind,
        "roleLabel": role_label,
        "coreRoleSummary": summary,
        "copyTier": copy_tier,
        "copyTierLabel": copy_tier_label,
        "copyAdvice": advice,
    }


def _compute_sim_delta(
    ref_sim: dict[str, Any],
    after_sim: dict[str, Any],
    *,
    ref_redline_msgs: set[str] | None = None,
) -> dict[str, Any]:
    """统一计算两个 PoB2 模拟结果之间的全量攻防/防御层/回复/机动/资源消融差值。"""
    ref_dps = _num(ref_sim.get("dps"))
    ref_ehp = _num(ref_sim.get("ehp"))
    ref_phys_hit = _num(ref_sim.get("physMaxHit"))
    ref_ele_hit = _num(ref_sim.get("eleMaxHit"))
    ref_chaos_hit = _num(ref_sim.get("chaosMaxHit"))
    ref_es = _num(ref_sim.get("es"))
    ref_life = _num(ref_sim.get("life"))
    ref_ward = _num(ref_sim.get("ward"))
    ref_armour = _num(ref_sim.get("armour"))
    ref_mana = _num(ref_sim.get("mana"))
    ref_mana_cost = _num(ref_sim.get("manaCost"))
    ref_spirit = _num(ref_sim.get("spirit"))
    ref_spirit_unres = _num(ref_sim.get("spiritUnreserved"))
    ref_speed = _num(ref_sim.get("speed"))
    ref_life_rec = max(_num(ref_sim.get("lifeRecovery")), _num(ref_sim.get("lifeRegen")) + _num(ref_sim.get("lifeLeech")))
    after_life_rec = max(_num(after_sim.get("lifeRecovery")), _num(after_sim.get("lifeRegen")) + _num(after_sim.get("lifeLeech")))
    ref_es_rec = max(_num(ref_sim.get("esRecovery")), _num(ref_sim.get("esRegen")) + _num(ref_sim.get("esLeech")))
    after_es_rec = max(_num(after_sim.get("esRecovery")), _num(after_sim.get("esRegen")) + _num(after_sim.get("esLeech")))

    ref_attr_ok = (
        _num(ref_sim.get("str")) >= _num(ref_sim.get("reqStr"))
        and _num(ref_sim.get("dex")) >= _num(ref_sim.get("reqDex"))
        and _num(ref_sim.get("int")) >= _num(ref_sim.get("reqInt"))
    )
    res_drops = [
        _num(ref_sim.get("fireRes")) - _num(after_sim.get("fireRes")),
        _num(ref_sim.get("coldRes")) - _num(after_sim.get("coldRes")),
        _num(ref_sim.get("lightningRes")) - _num(after_sim.get("lightningRes")),
        _num(ref_sim.get("chaosRes")) - _num(after_sim.get("chaosRes")),
    ]
    max_res_drop = round(max([0.0] + res_drops), 1)
    attr_broken = ref_attr_ok and (
        _num(after_sim.get("str")) < _num(after_sim.get("reqStr"))
        or _num(after_sim.get("dex")) < _num(after_sim.get("reqDex"))
        or _num(after_sim.get("int")) < _num(after_sim.get("reqInt"))
    )
    if ref_redline_msgs is None:
        ref_redline_msgs = {r["message"] for r in gap_analyzer.evaluate_redlines(ref_sim)}
    after_redlines = gap_analyzer.evaluate_redlines(after_sim, baseline=ref_sim)
    new_redlines = [r["message"] for r in after_redlines if r["message"] not in ref_redline_msgs]

    mana_cost_delta = 0.0
    if ref_mana_cost > 1e-6:
        mana_cost_delta = round(((_num(after_sim.get("manaCost")) - ref_mana_cost) / ref_mana_cost) * 100.0, 1)

    return {
        "dpsLossPct": _pct_loss(ref_dps, _num(after_sim.get("dps"))),
        "ehpLossPct": _pct_loss(ref_ehp, _num(after_sim.get("ehp"))),
        "physMaxHitLossPct": _pct_loss(ref_phys_hit, _num(after_sim.get("physMaxHit"))),
        "eleMaxHitLossPct": _pct_loss(ref_ele_hit, _num(after_sim.get("eleMaxHit"))),
        "chaosMaxHitLossPct": _pct_loss(ref_chaos_hit, _num(after_sim.get("chaosMaxHit"))),
        "esLossPct": _pct_loss(ref_es, _num(after_sim.get("es"))),
        "lifeLossPct": _pct_loss(ref_life, _num(after_sim.get("life"))),
        "wardLossPct": _pct_loss(ref_ward, _num(after_sim.get("ward"))),
        "armourLossPct": _pct_loss(ref_armour, _num(after_sim.get("armour"))),
        "manaLossPct": _pct_loss(ref_mana, _num(after_sim.get("mana"))),
        "spiritLoss": round(ref_spirit - _num(after_sim.get("spirit")), 1),
        "spiritSaved": round(max(0.0, _num(after_sim.get("spiritUnreserved")) - ref_spirit_unres), 1),
        "maxResDrop": max_res_drop,
        "evadeDrop": round(max(0.0, _num(ref_sim.get("evadeChance")) - _num(after_sim.get("evadeChance"))), 1),
        "deflectDrop": round(max(0.0, _num(ref_sim.get("deflectChance")) - _num(after_sim.get("deflectChance"))), 1),
        "blockDrop": round(max(0.0, _num(ref_sim.get("blockChance")) - _num(after_sim.get("blockChance"))), 1),
        "lifeRecoveryDrop": round(max(0.0, ref_life_rec - after_life_rec), 1),
        "esRecoveryDrop": round(max(0.0, ref_es_rec - after_es_rec), 1),
        "moveSpeedDrop": round(_num(ref_sim.get("moveSpeed")) - _num(after_sim.get("moveSpeed")), 1),
        "speedLossPct": _pct_loss(ref_speed, _num(after_sim.get("speed"))),
        "critDrop": round(_num(ref_sim.get("critChance")) - _num(after_sim.get("critChance")), 1),
        "attrBroken": attr_broken,
        "triggeredRedlines": new_redlines,
        "manaCostDeltaPct": mana_cost_delta,
    }


def _zero_delta() -> dict[str, Any]:
    return {
        "dpsLossPct": 0.0,
        "ehpLossPct": 0.0,
        "physMaxHitLossPct": 0.0,
        "eleMaxHitLossPct": 0.0,
        "chaosMaxHitLossPct": 0.0,
        "esLossPct": 0.0,
        "lifeLossPct": 0.0,
        "wardLossPct": 0.0,
        "armourLossPct": 0.0,
        "manaLossPct": 0.0,
        "spiritLoss": 0.0,
        "spiritSaved": 0.0,
        "maxResDrop": 0.0,
        "evadeDrop": 0.0,
        "deflectDrop": 0.0,
        "blockDrop": 0.0,
        "lifeRecoveryDrop": 0.0,
        "esRecoveryDrop": 0.0,
        "moveSpeedDrop": 0.0,
        "speedLossPct": 0.0,
        "critDrop": 0.0,
        "attrBroken": False,
        "triggeredRedlines": [],
        "manaCostDeltaPct": 0.0,
    }


def _detect_gem_defense_info(
    gem_name: str,
    *,
    pdict: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """识别单颗宝石的防御层归属（引擎实算防御差值 + PoE2 防御技能/辅助语义 + 官方词典原文扫描）。"""
    gname = (gem_name or "").strip()
    m = metrics or {}
    layers: list[str] = []
    descs: list[str] = []

    for pat, tag, desc in _SKILL_DEFENSE_KNOWLEDGE:
        if pat.search(gname):
            if tag not in layers:
                layers.append(tag)
            if desc not in descs:
                descs.append(desc)

    ehp_l = _num(m.get("ehpLossPct"))
    phys_l = _num(m.get("physMaxHitLossPct"))
    ele_l = _num(m.get("eleMaxHitLossPct"))
    res_d = _num(m.get("maxResDrop"))
    ev_d = _num(m.get("evadeDrop"))
    def_d = _num(m.get("deflectDrop"))
    blk_d = _num(m.get("blockDrop"))
    es_l = _num(m.get("esLossPct"))
    life_l = _num(m.get("lifeLossPct"))
    arm_l = _num(m.get("armourLossPct"))
    lrec_d = _num(m.get("lifeRecoveryDrop"))
    esrec_d = _num(m.get("esRecoveryDrop"))

    if res_d >= 5.0 and not any("抗" in x for x in layers):
        layers.append(f"抗性 -{res_d:.0f}%")
    if ev_d >= 2.0 and not any("闪避" in x for x in layers):
        layers.append(f"闪避 -{ev_d:.1f}%")
    if def_d >= 2.0 and not any("偏转" in x for x in layers):
        layers.append(f"偏转 -{def_d:.1f}%")
    if blk_d >= 2.0 and not any("格挡" in x for x in layers):
        layers.append(f"格挡 -{blk_d:.1f}%")
    if arm_l >= 5.0 and not any("护甲" in x for x in layers):
        layers.append(f"护甲 -{arm_l:.1f}%")
    if es_l >= 3.0 and not any("护盾" in x or "ES" in x for x in layers):
        layers.append(f"护盾 -{es_l:.1f}%")
    if life_l >= 3.0 and not any("生命" in x for x in layers):
        layers.append(f"生命 -{life_l:.1f}%")
    if lrec_d >= 10.0 and not any("回血" in x or "生命秒回" in x for x in layers):
        layers.append(f"生命回复 -{lrec_d:.0f}/s")
    if esrec_d >= 15.0 and not any("护盾" in x for x in layers):
        layers.append(f"护盾回复 -{esrec_d:.0f}/s")

    # 离线词典兜底扫描（捕获冷门或新赛季防御/控场宝石）
    if not layers and pdict and gname:
        info = patch_dict_updater.lookup_gem_info(gname, pdict)
        if info:
            blob = f"{info.get('d') or ''} {' '.join(info.get('c') or [])}"
            if _DESC_DEFENSE_KW_RE.search(blob):
                layers.append("词典防御/控场机制")
                short_d = str(info.get("d") or "").strip()[:90]
                if short_d:
                    descs.append(short_d)

    numeric_def_score = (
        max(0.0, ehp_l) * 1.2
        + max(0.0, phys_l) * 1.0
        + max(0.0, ele_l) * 0.7
        + max(0.0, res_d) * 0.35
        + max(0.0, ev_d) * 1.0
        + max(0.0, def_d) * 1.0
        + max(0.0, blk_d) * 1.2
        + min(20.0, max(0.0, lrec_d) * 0.25)
        + min(20.0, max(0.0, esrec_d) * 0.15)
    )
    has_numeric_def = (
        ehp_l >= 1.5
        or phys_l >= 1.5
        or ele_l >= 1.5
        or res_d >= 5.0
        or ev_d >= 2.0
        or def_d >= 2.0
        or blk_d >= 2.0
        or lrec_d >= 10.0
        or esrec_d >= 15.0
    )
    is_def = bool(layers or has_numeric_def)
    return {
        "isDefensive": is_def,
        "defenseLayers": layers[:4],
        "defenseSummary": "；".join(descs[:2]),
        "defenseScore": round(numeric_def_score + (6.0 if layers else 0.0), 1),
        "hasNumericDefense": has_numeric_def,
    }


def _classify_socketed_item_purpose(
    rune_name: str,
    *,
    meta: dict[str, Any] | None,
    metrics: dict[str, Any],
    bonded_active: bool,
    enables_idol_bonded: bool,
    grants_keystone: str | None,
    is_unparsed: bool,
    inactive_ws: bool,
    slot: str,
    item_name: str,
) -> dict[str, Any]:
    """为单颗装备插槽物品（符文 / 灵核 / 神像 / 碎片 / 遗物）判定真实地位、选择目的与抄作业建议。"""
    dps_l = _num(metrics.get("dpsLossPct"))
    ehp_l = _num(metrics.get("ehpLossPct"))
    phys_l = _num(metrics.get("physMaxHitLossPct"))
    ele_l = _num(metrics.get("eleMaxHitLossPct"))
    mana_l = _num(metrics.get("manaLossPct"))
    sp_l = _num(metrics.get("spiritLoss"))
    res_d = _num(metrics.get("maxResDrop"))
    es_l = _num(metrics.get("esLossPct"))
    attr_b = bool(metrics.get("attrBroken"))
    unlock_pools = list((meta or {}).get("unlockPools") or [])
    mods = list((meta or {}).get("mods") or [])
    bonded_mods = list((meta or {}).get("bondedMods") or [])
    is_bound = bool((meta or {}).get("isSocketBound"))

    stat_bits: list[str] = []
    if dps_l >= 0.4:
        stat_bits.append(f"DPS -{dps_l:.1f}%")
    if ehp_l >= 0.4:
        stat_bits.append(f"EHP -{ehp_l:.1f}%")
    if phys_l >= 1.0:
        stat_bits.append(f"物一击 -{phys_l:.1f}%")
    if ele_l >= 1.5:
        stat_bits.append(f"元一击 -{ele_l:.1f}%")
    if res_d >= 5.0:
        stat_bits.append(f"抗性 -{res_d:.0f}%")
    if sp_l >= 5.0:
        stat_bits.append(f"精魂 -{sp_l:.0f}")
    if es_l >= 1.5:
        stat_bits.append(f"ES -{es_l:.1f}%")
    if mana_l >= 2.0:
        stat_bits.append(f"魔力 -{mana_l:.1f}%")
    stat_txt = " / ".join(stat_bits)

    if grants_keystone:
        return {
            "tier": "must",
            "purposeKind": "synergy" if dps_l < 5.0 else "core_dps",
            "purposeLabel": f"插槽基石({grants_keystone})",
            "purposeDetail": (
                f"直接在插槽赋予核心基石「{grants_keystone}」"
                + (f"，拔掉实算损失 {stat_txt}。" if stat_txt else "（省去跨区跑天赋点，机制极核心）。")
            ),
            "copyAdvice": "机制锁死级镶嵌物，买完该装备后务必优先补插！",
        }

    if enables_idol_bonded:
        return {
            "tier": "must",
            "purposeKind": "synergy",
            "purposeLabel": "神像 Bonded 激活核",
            "purposeDetail": (
                f"激活同件装备内所有神像的 Bonded 词条（{'; '.join(bonded_mods[:2]) or '品质/全抗/属性等'}）"
                + (f"，拔掉实算损失 {stat_txt}。" if stat_txt else "。")
            ),
            "copyAdvice": "与同装备其他神像形成联动核心，抄作业需成对插齐才能吃满 Bonded 加成。",
        }

    if unlock_pools:
        pool_str = " / ".join(unlock_pools)
        return {
            "tier": "must" if (is_bound or dps_l >= 2.0 or ehp_l >= 2.0) else "nice",
            "purposeKind": "synergy",
            "purposeLabel": f"解锁词池({pool_str})",
            "purposeDetail": (
                f"用于解锁「{pool_str}」专属做装词池"
                + (f"（拔掉实算 {stat_txt}）" if stat_txt else "")
                + (f"；Bonded 生效：{bonded_mods[0]}" if bonded_active and bonded_mods else "；做装完成后锁定在孔位内。")
            ),
            "copyAdvice": "自做半成品时必须先插该符文解锁词池；直买成品已自带该插槽词条则无需重插。",
        }

    if attr_b or sp_l >= 10.0:
        return {
            "tier": "must",
            "purposeKind": "threshold",
            "purposeLabel": "精魂/门槛镶嵌",
            "purposeDetail": f"承担关键资源/属性门槛（{stat_txt or '属性/精魂支撑'}），拔掉会导致光环或装备失效。",
            "copyAdvice": "硬门槛镶嵌物，缺精魂或属性时优先补插。",
        }

    if dps_l >= 3.0 and ehp_l >= 4.0:
        return {
            "tier": "must",
            "purposeKind": "core_dps",
            "purposeLabel": "攻防双核镶嵌",
            "purposeDetail": f"单颗拔除即损失 {stat_txt}，同时撑起输出与生存/底子孔位放大收益。",
            "copyAdvice": "高性价比核心镶嵌物，买完该件装备后第一时间补插。",
        }

    if dps_l >= 3.0:
        return {
            "tier": "must",
            "purposeKind": "core_dps",
            "purposeLabel": "核心输出镶嵌",
            "purposeDetail": f"单颗提供 {stat_txt}，是「{item_name}」插槽里的核心伤害放大器。",
            "copyAdvice": "优先按目标型号补插，提升幅度接近半条顶级词缀。",
        }

    if ehp_l >= 4.0 or res_d >= 10.0 or phys_l >= 3.0 or ele_l >= 3.0:
        return {
            "tier": "must" if (ehp_l >= 8.0 or res_d >= 14.0) else "nice",
            "purposeKind": "core_defense",
            "purposeLabel": "核心防御/抗性镶嵌",
            "purposeDetail": f"单颗提供 {stat_txt}，直接抬高生存池、一击线或补齐抗性缺口。",
            "copyAdvice": "若预算紧或抗性已满可按自身缺口微调同阶防御符文/灵核，否则建议照抄。",
        }

    if is_unparsed:
        eff_preview = " / ".join(mods[:2]) if mods else rune_name
        return {
            "tier": "must",
            "purposeKind": "synergy",
            "purposeLabel": "机制镶嵌(未实算)",
            "purposeDetail": f"赋予特殊机制「{eff_preview}」（PoB2 静态面板未完全折算，实战作用不可忽视）。",
            "copyAdvice": "属于特殊机制镶嵌物（碎片/限定符文/神像），抄作业前建议核实当前赛季市价与实战效果。",
        }

    if inactive_ws:
        return {
            "tier": "nice",
            "purposeKind": "utility",
            "purposeLabel": "副武器组镶嵌",
            "purposeDetail": f"位于未激活武器组（{mods[0] if mods else '切武器组时生效'}），用于跑图攻速、切精魂或叠层。",
            "copyAdvice": "按副武器组职责配平价同功能符文即可。",
        }

    if dps_l >= 0.8 or ehp_l >= 0.8 or (bonded_active and bonded_mods):
        extra = f"（Bonded 生效：{bonded_mods[0]}）" if (bonded_active and bonded_mods) else ""
        return {
            "tier": "nice",
            "purposeKind": "core_dps" if dps_l >= ehp_l else "core_defense",
            "purposeLabel": "稳健增益镶嵌",
            "purposeDetail": f"实算贡献 {stat_txt or '平稳属性增益'}{extra}。",
            "copyAdvice": "按预算直接补插同款或低一阶平价符文过渡。",
        }

    return {
        "tier": "cut",
        "purposeKind": "utility",
        "purposeLabel": "功能/过渡镶嵌",
        "purposeDetail": f"面板直接贡献较小（{mods[0] if mods else '经验/抗性/功能补位'}），可按自身抗性或预算自由替换。",
        "copyAdvice": "前期可用便宜元素抗性/铁符文替代，无需花高价买同款。",
    }


def _classify_ascendancy_node(
    node_plan: dict[str, Any],
    *,
    isolated: dict[str, Any],
    branch: dict[str, Any],
    unparsed_text_blob: str = "",
) -> dict[str, Any]:
    """根据孤立消融 + 分支消融 + 节点官方词条，评判单颗升华大点在 BD 中的选择原因。"""
    name = str(node_plan.get("name") or "")
    stats = list(node_plan.get("stats") or [])
    unlocks = list(node_plan.get("unlocksNotables") or [])
    stats_blob = " | ".join(stats).lower()

    iso_dps = _num(isolated.get("dpsLossPct"))
    iso_ehp = _num(isolated.get("ehpLossPct"))
    iso_phys = _num(isolated.get("physMaxHitLossPct"))
    iso_ele = _num(isolated.get("eleMaxHitLossPct"))
    iso_sp = _num(isolated.get("spiritLoss"))
    iso_ms = _num(isolated.get("moveSpeedDrop"))
    iso_res = _num(isolated.get("maxResDrop"))
    iso_spd = _num(isolated.get("speedLossPct"))
    iso_attr = bool(isolated.get("attrBroken"))

    br_dps = _num(branch.get("dpsLossPct"))
    br_ehp = _num(branch.get("ehpLossPct"))

    granted = None
    for st in stats:
        gs = xml_mutator.extract_granted_skill(st)
        if gs:
            granted = gs[0]
            break

    # 识别 PoB2 静态面板无法完全量化但实战极核心的升华机制
    mech_hints: list[str] = []
    if granted:
        mech_hints.append(f"赋予专属升华技能「{granted}」")
    if "no critical damage bonus" in stats_blob or "extra damage from critical" in stats_blob:
        mech_hints.append("受击免疫暴击额外伤害（极高实战防猝死层，静态 EHP 未完全折算）")
    if "movement speed penalty from using skills" in stats_blob:
        mech_hints.append("大幅降低施放技能时的移速惩罚（决定走A/刷图手感）")
    if "gem quality grants" in stats_blob:
        mech_hints.append("让全部技能品质获得额外乘区/机制特效（核心放大全技能组）")
    if "bonded modifiers on runes" in stats_blob:
        mech_hints.append("激活全身符文/神像 Bonded 隐藏词缀")
    if "cannot gain spirit from equipment" in stats_blob or ("spirit for every" in stats_blob and "body armour" in stats_blob):
        mech_hints.append("胸甲防御值直接转化为高额精魂上限")
    if "ignoring" in stats_blob or "surrounded" in stats_blob or "lucky" in stats_blob or "consume" in stats_blob:
        mech_hints.append("提供独立实战机制增益")
    if "allocate" in stats_blob or "unallocated" in stats_blob or "radius" in stats_blob:
        mech_hints.append("改变天赋树连点规则/跨区跳点")
    if "30% less cost" in stats_blob or "less cost" in stats_blob or "mana" in stats_blob and "cost" in stats_blob:
        mech_hints.append("显著压低技能消耗，缓解高频输出续航压力")
    if "additional skill slot" in stats_blob or "maximum number of" in stats_blob:
        mech_hints.append("扩展技能/光环/图腾/召唤上限")

    iso_bits: list[str] = []
    if iso_dps >= 0.5:
        iso_bits.append(f"DPS -{iso_dps:.1f}%")
    if iso_ehp >= 0.5:
        iso_bits.append(f"EHP -{iso_ehp:.1f}%")
    if iso_phys >= 1.5:
        iso_bits.append(f"物一击 -{iso_phys:.1f}%")
    if iso_ele >= 1.5:
        iso_bits.append(f"元一击 -{iso_ele:.1f}%")
    if iso_sp >= 5.0:
        iso_bits.append(f"精魂 -{iso_sp:.0f}")
    if iso_res >= 5.0:
        iso_bits.append(f"抗性 -{iso_res:.0f}%")
    if iso_ms >= 3.0:
        iso_bits.append(f"移速 -{iso_ms:.0f}%")
    if iso_spd >= 2.0:
        iso_bits.append(f"频率 -{iso_spd:.1f}%")
    if iso_attr:
        iso_bits.append("触发属性红线")

    iso_txt = " / ".join(iso_bits)
    unlock_txt = ""
    if unlocks:
        br_bits = []
        if br_dps >= 1.0:
            br_bits.append(f"整支 DPS -{br_dps:.1f}%")
        if br_ehp >= 1.0:
            br_bits.append(f"整支 EHP -{br_ehp:.1f}%")
        br_suffix = f"（{' / '.join(br_bits)}）" if br_bits else ""
        unlock_txt = f"同时作为前置解锁「{' / '.join(unlocks)}」{br_suffix}。"

    is_mech = bool(mech_hints)
    if iso_dps >= 8.0 and (iso_ehp >= 8.0 or iso_phys >= 8.0):
        role_kind, role_label, tier = "core_both", "攻防双核升华", "core"
        reason = f"孤立消融即损失 {iso_txt}，是该升华同时撑起输出与防御的核心支柱。"
    elif iso_dps >= 6.0:
        role_kind, role_label, tier = "core_dps", "核心输出升华", "core"
        reason = f"孤立消融实算损失 {iso_txt}，直接提供核心伤害乘区。"
    elif iso_ehp >= 6.0 or iso_phys >= 6.0 or iso_ele >= 6.0 or iso_res >= 15.0:
        role_kind, role_label, tier = "core_defense", "核心防御升华", "core"
        reason = f"孤立消融实算损失 {iso_txt}，是防御层与承伤上限的关键来源。"
    elif iso_sp >= 15.0 or iso_attr:
        role_kind, role_label, tier = "threshold", "资源/门槛升华", "core"
        reason = f"孤立消融影响 {iso_txt}，支撑构筑精魂或装备/宝石穿戴体系。"
    elif iso_ms >= 5.0 or iso_spd >= 4.0:
        role_kind, role_label, tier = "mobility", "机动/频率升华", "key"
        reason = f"孤立消融损失 {iso_txt}，显著拉升跑图与操作手感。"
    elif is_mech:
        role_kind = "core_defense" if (granted and _DEFENSE_SKILL_RE.search(granted)) or "暴击" in " ".join(mech_hints) else "mechanic"
        role_label = "防御机制升华" if role_kind == "core_defense" else "核心机制升华"
        tier = "core" if (not unlocks or len(mech_hints) >= 1) else "key"
        base_m = "；".join(mech_hints[:2])
        reason = f"{base_m}" + (f"（孤立实算 {iso_txt}）。" if iso_txt else "（PoB2 静态面板未完全量化其防御/手感/机制收益）。")
    elif unlocks:
        role_kind, role_label, tier = "prerequisite", "前置过渡大点", "prerequisite"
        reason = f"本体面板直接贡献较小（{iso_txt or '数值无直显'}），核心价值在于打通后置升华分支。"
    elif iso_dps >= 1.5 or iso_ehp >= 1.5:
        role_kind, role_label, tier = "utility", "稳健增益升华", "key"
        reason = f"孤立消融平稳提供 {iso_txt}。"
    else:
        role_kind, role_label, tier = "utility", "机制/功能升华", "utility"
        reason = f"PoB2 静态面板差值较小（{stats[0] if stats else '特殊机制'}），多用于实战容错或流派配合。"

    if unlock_txt:
        reason = f"{reason}{unlock_txt}"

    return {
        "roleKind": role_kind,
        "roleLabel": role_label,
        "importanceTier": tier,
        "isMechanical": is_mech,
        "grantedSkill": granted or "",
        "selectionReason": reason,
    }


def _assemble_ascendancy_section(
    plan: dict[str, Any],
    *,
    base_sim: dict[str, Any],
    total_sim: dict[str, Any] | None,
    branch_sims: dict[str, dict[str, Any]],
    children_sims: dict[str, dict[str, Any]],
    base_redline_msgs: set[str],
) -> dict[str, Any]:
    """将升华整体消融 + 各大点孤立/分支消融组装为结构化升华剖析对象。"""
    asc_name = str(plan.get("ascendancy") or "None")
    notables_plan = list(plan.get("notables") or [])
    total_delta = _compute_sim_delta(base_sim, total_sim, ref_redline_msgs=base_redline_msgs) if total_sim else _zero_delta()

    nodes_out: list[dict[str, Any]] = []
    for np in notables_plan:
        nid = str(np["id"])
        br_sim = branch_sims.get(nid)
        ch_sim = children_sims.get(nid)
        branch_delta = _compute_sim_delta(base_sim, br_sim, ref_redline_msgs=base_redline_msgs) if br_sim else _zero_delta()
        if np.get("disconnectedIds") and ch_sim is not None and br_sim is not None:
            isolated_delta = _compute_sim_delta(ch_sim, br_sim)
        else:
            isolated_delta = dict(branch_delta)

        cls = _classify_ascendancy_node(np, isolated=isolated_delta, branch=branch_delta)
        impact_score = (
            max(0.0, isolated_delta["dpsLossPct"]) * 1.6
            + max(0.0, isolated_delta["ehpLossPct"]) * 1.2
            + max(0.0, isolated_delta["physMaxHitLossPct"]) * 0.9
            + max(0.0, isolated_delta["spiritLoss"]) * 0.4
            + max(0.0, isolated_delta["moveSpeedDrop"]) * 0.6
            + (12.0 if cls["isMechanical"] else 0.0)
            + (max(0.0, branch_delta["dpsLossPct"]) + max(0.0, branch_delta["ehpLossPct"])) * 0.25
        )
        nodes_out.append({
            "id": nid,
            "nodeIds": list(np.get("nodeIds") or [nid]),
            "name": np["name"],
            "stats": list(np.get("stats") or []),
            "unlocksNotables": list(np.get("unlocksNotables") or []),
            "hasBranchDescendants": bool(np.get("disconnectedIds")),
            "isolated": {
                "dpsLossPct": isolated_delta["dpsLossPct"],
                "ehpLossPct": isolated_delta["ehpLossPct"],
                "physMaxHitLossPct": isolated_delta["physMaxHitLossPct"],
                "eleMaxHitLossPct": isolated_delta["eleMaxHitLossPct"],
                "manaLossPct": isolated_delta["manaLossPct"],
                "spiritLoss": isolated_delta["spiritLoss"],
                "maxResDrop": isolated_delta["maxResDrop"],
                "evadeDrop": isolated_delta["evadeDrop"],
                "deflectDrop": isolated_delta["deflectDrop"],
                "moveSpeedDrop": isolated_delta["moveSpeedDrop"],
                "speedLossPct": isolated_delta["speedLossPct"],
                "attrBroken": isolated_delta["attrBroken"],
                "triggeredRedlines": isolated_delta["triggeredRedlines"],
            },
            "branch": {
                "dpsLossPct": branch_delta["dpsLossPct"],
                "ehpLossPct": branch_delta["ehpLossPct"],
                "physMaxHitLossPct": branch_delta["physMaxHitLossPct"],
                "eleMaxHitLossPct": branch_delta["eleMaxHitLossPct"],
                "spiritLoss": branch_delta["spiritLoss"],
                "maxResDrop": branch_delta["maxResDrop"],
            },
            "impactScore": round(impact_score, 1),
            **cls,
        })

    nodes_out.sort(key=lambda x: x["impactScore"], reverse=True)

    # 合成客观的「为什么选该升华」一句话总结
    tot_bits: list[str] = []
    if total_delta["dpsLossPct"] >= 1.0:
        tot_bits.append(f"DPS -{total_delta['dpsLossPct']:.1f}%")
    if total_delta["ehpLossPct"] >= 1.0:
        tot_bits.append(f"EHP -{total_delta['ehpLossPct']:.1f}%")
    if total_delta["physMaxHitLossPct"] >= 2.0:
        tot_bits.append(f"物一击 -{total_delta['physMaxHitLossPct']:.1f}%")
    if total_delta["spiritLoss"] >= 10.0:
        tot_bits.append(f"精魂 -{total_delta['spiritLoss']:.0f}")
    if total_delta["maxResDrop"] >= 5.0:
        tot_bits.append(f"抗性 -{total_delta['maxResDrop']:.0f}%")
    if total_delta["moveSpeedDrop"] >= 3.0:
        tot_bits.append(f"移速 -{total_delta['moveSpeedDrop']:.0f}%")
    if total_delta["attrBroken"]:
        tot_bits.append("触发属性红线")

    top_pillars = [f"{n['name']}({n['roleLabel']})" for n in nodes_out[:3]]
    if not nodes_out:
        why = f"当前未检测到已分配的「{asc_name}」升华大点。"
    else:
        tot_str = " / ".join(tot_bits) if tot_bits else "核心价值以非面板机制/手感/技能扩展为主"
        why = f"整套「{asc_name}」升华消融总贡献：{tot_str}；核心支柱为 {' + '.join(top_pillars)}。"

    return {
        "ascendancy": asc_name,
        "allocatedNodeCount": len(plan.get("allAscNodeIds") or []),
        "notableCount": len(nodes_out),
        "totalImpact": {
            "dpsLossPct": total_delta["dpsLossPct"],
            "ehpLossPct": total_delta["ehpLossPct"],
            "physMaxHitLossPct": total_delta["physMaxHitLossPct"],
            "eleMaxHitLossPct": total_delta["eleMaxHitLossPct"],
            "manaLossPct": total_delta["manaLossPct"],
            "spiritLoss": total_delta["spiritLoss"],
            "maxResDrop": total_delta["maxResDrop"],
            "evadeDrop": total_delta["evadeDrop"],
            "deflectDrop": total_delta["deflectDrop"],
            "moveSpeedDrop": total_delta["moveSpeedDrop"],
            "speedLossPct": total_delta["speedLossPct"],
            "attrBroken": total_delta["attrBroken"],
            "triggeredRedlines": total_delta["triggeredRedlines"],
        },
        "nodes": nodes_out,
        "whyChosenSummary": why,
    }


def ablate_ascendancy_only(
    code_or_xml: str,
    pool: Any,
    *,
    base_sim: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """轻量级升华天赋专项消融（仅跑 ~5~9 次并发模拟，<0.4s），供 /llm/rate 在全量消融未就绪时即时评判升华选择原因。"""
    if pool is None:
        return None
    xml = pob_parser.decode_pob(code_or_xml)
    norm_xml, _ = runefix.normalize_runes(xml)
    parsed = pob_parser.parse_pob_xml(norm_xml)
    asc_name = str(((parsed.get("character") or {}).get("ascendancy")) or "").strip()
    if not asc_name or asc_name.lower() == "none":
        return None
    alloc_ids = pob_parser.extract_allocated_node_ids(norm_xml)
    plan = tree_index.plan_ascendancy_ablation(
        alloc_ids,
        asc_name,
        tree_version=(parsed.get("passiveTree") or {}).get("treeVersion") or None,
    )
    if not plan.get("allAscNodeIds"):
        return None

    ref_sim = dict(base_sim) if isinstance(base_sim, dict) and base_sim.get("dps") is not None else pool.simulate(norm_xml, include_audit=False)
    lock_key = str(ref_sim.get("dpsSource") or "") or None
    base_redline_msgs = {r["message"] for r in gap_analyzer.evaluate_redlines(ref_sim)}

    xml_batch: list[str] = []
    xml_to_idx: dict[str, int] = {}
    jobs: list[tuple[str, str, int]] = []

    def enqueue(kind: str, nid: str, mut_xml: str) -> None:
        h = hashlib.sha1(mut_xml.encode("utf-8")).hexdigest()
        bidx = xml_to_idx.get(h)
        if bidx is None:
            bidx = len(xml_batch)
            xml_to_idx[h] = bidx
            xml_batch.append(mut_xml)
        jobs.append((kind, nid, bidx))

    all_granted: list[str] = []
    notable_granted: dict[str, list[str]] = {}
    for np in plan["notables"]:
        gs_list: list[str] = []
        for st in np.get("stats") or []:
            g = xml_mutator.extract_granted_skill(st)
            if g:
                gs_list.append(g[0])
                all_granted.append(g[0])
        notable_granted[np["id"]] = gs_list

    try:
        enqueue("total", "*", xml_mutator.ablate_passive_nodes(norm_xml, plan["allAscNodeIds"], granted_skill_names=all_granted))
    except Exception:
        pass

    notables_by_id = {np["id"]: np for np in plan["notables"]}
    for np in plan["notables"]:
        nid = np["id"]
        disc = list(np.get("disconnectedIds") or [])
        child_granted: list[str] = []
        for d_id in disc:
            if d_id in notable_granted:
                child_granted.extend(notable_granted[d_id])
        try:
            enqueue(
                "branch",
                nid,
                xml_mutator.ablate_passive_nodes(
                    norm_xml,
                    list(np["nodeIds"]) + disc,
                    granted_skill_names=notable_granted[nid] + child_granted,
                ),
            )
        except Exception:
            pass
        if disc:
            try:
                enqueue(
                    "children",
                    nid,
                    xml_mutator.ablate_passive_nodes(norm_xml, disc, granted_skill_names=child_granted),
                )
            except Exception:
                pass

    if not xml_batch:
        return None
    results = pool.simulate_batch(xml_batch, lock_dps_key=lock_key, include_audit=False)
    total_sim = None
    branch_sims: dict[str, dict[str, Any]] = {}
    children_sims: dict[str, dict[str, Any]] = {}
    for kind, nid, bidx in jobs:
        if kind == "total":
            total_sim = results[bidx]
        elif kind == "branch":
            branch_sims[nid] = results[bidx]
        elif kind == "children":
            children_sims[nid] = results[bidx]

    return _assemble_ascendancy_section(
        plan,
        base_sim=ref_sim,
        total_sim=total_sim,
        branch_sims=branch_sims,
        children_sims=children_sims,
        base_redline_msgs=base_redline_msgs,
    )


def _classify_skill_group(
    sg: dict[str, Any],
    *,
    dps_loss: float,
    ehp_loss: float,
    mana_loss: float,
    spirit_saved: float,
    max_res_drop: float,
    own_dps: float,
    gem_entries: list[dict[str, Any]],
    defense_info: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gems = sg.get("gems") or []
    main_name = str(gems[0].get("name") or "技能组") if gems else "技能组"
    is_main = bool(sg.get("isMain"))
    dinfo = defense_info or {}
    m = metrics or {}
    phys_l = _num(m.get("physMaxHitLossPct"))
    ele_l = _num(m.get("eleMaxHitLossPct"))
    ev_d = _num(m.get("evadeDrop"))
    def_d = _num(m.get("deflectDrop"))
    lrec_d = _num(m.get("lifeRecoveryDrop"))
    esrec_d = _num(m.get("esRecoveryDrop"))
    def_layers = list(dinfo.get("defenseLayers") or [])
    is_def_group = bool(dinfo.get("isDefensive"))

    def _def_metric_str() -> str:
        bits = []
        if ehp_loss >= 1.0:
            bits.append(f"EHP -{ehp_loss:.1f}%")
        if phys_l >= 1.5:
            bits.append(f"物一击 -{phys_l:.1f}%")
        if ele_l >= 1.5:
            bits.append(f"元一击 -{ele_l:.1f}%")
        if max_res_drop >= 5.0:
            bits.append(f"抗性 -{max_res_drop:.0f}%")
        if ev_d >= 2.0:
            bits.append(f"闪避 -{ev_d:.1f}%")
        if def_d >= 2.0:
            bits.append(f"偏转 -{def_d:.1f}%")
        if lrec_d >= 10.0:
            bits.append(f"生命秒回 -{lrec_d:.0f}/s")
        if esrec_d >= 15.0:
            bits.append(f"护盾回复 -{esrec_d:.0f}/s")
        return " / ".join(bits)

    if is_main:
        top_sups = sorted(
            [g for g in gem_entries if g.get("isSupport")],
            key=lambda x: x.get("dpsLossPct", 0.0),
            reverse=True,
        )
        top_txt = f"，最大增伤辅助为 {top_sups[0]['name']} (-{top_sups[0]['dpsLossPct']:.1f}% DPS)" if top_sups and top_sups[0].get("dpsLossPct", 0) > 1 else ""
        dg_notes = [g for g in gem_entries if g.get("downgrade2020DpsLossPct") is not None]
        advice = "抄作业第一时间对齐主技能与高贡献辅助连法"
        if dg_notes:
            max_dg = max(float(g.get("downgrade2020DpsLossPct") or 0.0) for g in dg_notes)
            if max_dg < 5.0:
                advice += f"；21/23 腐化宝石降到 20/20 单颗最多仅掉 {max_dg:.1f}% DPS，开荒用 20/20 即可。"
            else:
                advice += f"；主技能高等级收益明显（降 20/20 掉 {max_dg:.1f}% DPS），成型后优先追 21 级。"
        else:
            advice += "。"
        return {
            "roleKind": "main_dps",
            "roleLabel": "主输出核心",
            "coreRoleSummary": f"构筑核心输出主轴（{len(gems)} 连{top_txt}）。",
            "copyTier": "mandatory",
            "copyTierLabel": "必保主技能",
            "copyAdvice": advice,
        }

    if _TRAVEL_RE.search(main_name):
        def_tail = f"；同时挂载防御/控场辅助（{' / '.join(def_layers[:2])}）降低近身风险" if def_layers else ""
        return {
            "roleKind": "travel",
            "roleLabel": "位移/跑图" + ("+控场" if def_layers else ""),
            "coreRoleSummary": f"提供跑图机动性与躲避 Boss 技能容错（PoB DPS 不计入位移）{def_tail}。",
            "copyTier": "core",
            "copyTierLabel": "手感必备",
            "copyAdvice": "直接按连法配 1 套即可，通常不吃高等级腐化，省钱省事。",
        }

    if _TRIGGER_RE.search(main_name):
        sp_txt = f"占用 {spirit_saved:.0f} 精魂，" if spirit_saved > 0 else ""
        dps_txt = f"关掉掉 {dps_loss:.1f}% DPS" if dps_loss >= 1.0 else "面板未完全折算自动触发循环"
        def_tail = f"；内置防御/控场层：{' / '.join(def_layers[:2])}" if def_layers else ""
        return {
            "roleKind": "trigger",
            "roleLabel": "自动触发" + ("+防御控场" if def_layers else "核心"),
            "coreRoleSummary": f"{sp_txt}{dps_txt}，承担自动化施法/清图或叠层触发{def_tail}。",
            "copyTier": "mandatory" if (dps_loss >= 8.0 or ehp_loss >= 8.0 or spirit_saved >= 50) else "core",
            "copyTierLabel": "机制必保" if (dps_loss >= 8.0 or ehp_loss >= 8.0 or spirit_saved >= 50) else "核心机制",
            "copyAdvice": "注意核对精魂上限与触发能量效率，确保实战能稳定触发。",
        }

    if _CURSE_MARK_RE.search(main_name):
        is_def_curse = bool(re.search(r"enfeeble|temporal chains", main_name, re.I))
        if is_def_curse:
            return {
                "roleKind": "defense",
                "roleLabel": "防御减伤诅咒",
                "coreRoleSummary": f"通过诅咒削弱敌人伤害或行动速度（{' / '.join(def_layers[:2]) or '核心防御诅咒'}）。",
                "copyTier": "core",
                "copyTierLabel": "生存必备",
                "copyAdvice": "低成本高收益防御诅咒，高层图/攻坚必带。",
            }
        dps_txt = f"提供约 {dps_loss:.1f}% 增伤" if dps_loss >= 1.0 else "提供 Boss 减抗/易伤或印记机制"
        return {
            "roleKind": "buff_debuff",
            "roleLabel": "诅咒/印记增伤",
            "coreRoleSummary": f"{dps_txt}，攻坚 Boss 爆发期关键辅助技能。",
            "copyTier": "core",
            "copyTierLabel": "攻坚必带",
            "copyAdvice": "0 成本高收益攻坚技能，抄作业优先补齐对应品质与等级。",
        }

    # 明确以防御/回复/抗性/屏障为主导的技能组（哪怕零精魂如 Purity of Fire/Ice 或 Virtuous Barrier，或高 EHP/一击线/回血收益）
    def_metric_txt = _def_metric_str()
    def_dominant = (
        is_def_group
        and (
            ehp_loss >= max(4.0, dps_loss * 1.1)
            or max_res_drop >= 10.0
            or lrec_d >= 15.0
            or esrec_d >= 20.0
            or ev_d >= 3.0
            or def_d >= 3.0
            or (dps_loss < 2.0 and own_dps < 20000)
        )
    )
    if def_dominant:
        layer_txt = " / ".join(def_layers[:3])
        sp_prefix = f"占 {spirit_saved:.0f} 精魂 · " if spirit_saved > 0 else ("0精魂基底/升华技 · " if re.search(r"purity of|virtuous barrier|discipline", main_name, re.I) else "")
        eff = def_metric_txt or (dinfo.get("defenseSummary") or f"提供 {layer_txt} 防御层（静态面板未完全折算）")
        if def_metric_txt and layer_txt:
            eff = f"{def_metric_txt}（{layer_txt}）"
        is_key_def = ehp_loss >= 8.0 or phys_l >= 6.0 or max_res_drop >= 15.0 or lrec_d >= 30.0 or bool(re.search(r"ghost dance|wind dancer|virtuous barrier|convalescence|grim feast|purity of", main_name, re.I))
        return {
            "roleKind": "defense",
            "roleLabel": "防御光环/回复" if (spirit_saved > 0 or re.search(r"purity of|discipline|vitality", main_name, re.I)) else "核心防御/保命技",
            "coreRoleSummary": f"{sp_prefix}{eff}。",
            "copyTier": "mandatory" if is_key_def else "core",
            "copyTierLabel": "核心生存层" if is_key_def else "生存必备",
            "copyAdvice": (
                "核心防御支柱，抄作业不可漏带；若连有回复/控场辅助（如 Vitality/Cannibalism/Blind/CDR）需同步补齐。"
                if is_key_def
                else "低造价高保命收益，优先按连法配齐。"
            ),
        }

    if spirit_saved > 0 or _SPIRIT_AURA_RE.search(main_name):
        parts = []
        if dps_loss >= 1.0:
            parts.append(f"DPS -{dps_loss:.1f}%")
        if def_metric_txt:
            parts.append(def_metric_txt)
        if mana_loss >= 2.0:
            parts.append(f"魔力 -{mana_loss:.1f}%")
        eff = " / ".join(parts) if parts else "提供光环机制/续航/清图增益（面板未直接计入）"
        if def_layers:
            eff += f" · 防御层：{def_layers[0]}"
        sp_txt = f"占 {spirit_saved:.0f} 精魂" if spirit_saved > 0 else "精魂持续技"
        is_key = dps_loss >= 8.0 or ehp_loss >= 8.0
        return {
            "roleKind": "spirit_aura",
            "roleLabel": "攻防精魂光环" if (dps_loss >= 5.0 and (ehp_loss >= 5.0 or bool(def_layers))) else "精魂光环/常驻",
            "coreRoleSummary": f"{sp_txt} · {eff}。",
            "copyTier": "mandatory" if is_key else "core",
            "copyTierLabel": "必开光环" if is_key else "常驻增益",
            "copyAdvice": (
                "核心常驻乘区，精魂不够时优先保住它，砍掉低收益辅光环。"
                if is_key
                else "按自身精魂余量优先开启，精魂紧张时可延后或降辅宝石。"
            ),
        }

    if dps_loss >= 5.0 or own_dps >= 30000:
        def_tail = f"（兼顾防御：{' / '.join(def_layers[:2])}）" if def_layers else ""
        return {
            "roleKind": "buff_debuff",
            "roleLabel": "副输出/增伤技",
            "coreRoleSummary": f"关掉损失 {dps_loss:.1f}% DPS" + (f"（自身面板 {own_dps/1000:.1f}k）" if own_dps >= 1000 else "") + f"{def_tail}。",
            "copyTier": "core",
            "copyTierLabel": "核心配合",
            "copyAdvice": "配合主技能打爆发或补清图，前期 19~20 级即可稳定发挥。",
        }

    if ehp_loss >= 4.0 or def_metric_txt:
        return {
            "roleKind": "defense",
            "roleLabel": "防御/保命技",
            "coreRoleSummary": f"关掉损失 {def_metric_txt or f'EHP -{ehp_loss:.1f}%'}，承担关键减伤或恢复。",
            "copyTier": "core",
            "copyTierLabel": "生存必备",
            "copyAdvice": "低造价高保命收益，优先带上。",
        }

    return {
        "roleKind": "utility",
        "roleLabel": "功能/辅助技",
        "coreRoleSummary": "用于清图手感、充能、召唤工具或特定场景配合（面板主 DPS 未直接计入）。",
        "copyTier": "flexible",
        "copyTierLabel": "按需选配",
        "copyAdvice": "成本几乎为 0，照抄连法即可，无需高价买腐化宝石。",
    }


def _classify_gem_role(
    gem: dict[str, Any],
    *,
    is_main_gem: bool,
    dps_loss: float,
    ehp_loss: float,
    mana_cost_delta: float,
    downgrade_loss: float | None,
    downgrade_ehp_loss: float | None = None,
    defense_info: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    gname = str(gem.get("name") or "")
    lv = int(_num(gem.get("level"), 1))
    q = int(_num(gem.get("quality"), 0))
    dinfo = defense_info or {}
    m = metrics or {}
    def_layers = list(dinfo.get("defenseLayers") or [])
    lrec_d = _num(m.get("lifeRecoveryDrop"))
    esrec_d = _num(m.get("esRecoveryDrop"))
    phys_l = _num(m.get("physMaxHitLossPct"))
    res_d = _num(m.get("maxResDrop"))
    ev_d = _num(m.get("evadeDrop"))
    def_d = _num(m.get("deflectDrop"))

    def _def_bits() -> str:
        bits = []
        if ehp_loss >= 0.8:
            bits.append(f"EHP -{ehp_loss:.1f}%")
        if phys_l >= 1.5:
            bits.append(f"物一击 -{phys_l:.1f}%")
        if res_d >= 5.0:
            bits.append(f"抗性 -{res_d:.0f}%")
        if ev_d >= 2.0:
            bits.append(f"闪避 -{ev_d:.1f}%")
        if def_d >= 2.0:
            bits.append(f"偏转 -{def_d:.1f}%")
        if lrec_d >= 8.0:
            bits.append(f"生命回复 -{lrec_d:.0f}/s")
        if esrec_d >= 12.0:
            bits.append(f"护盾回复 -{esrec_d:.0f}/s")
        return " / ".join(bits)

    if is_main_gem:
        is_def_main = bool(dinfo.get("isDefensive")) and dps_loss < 2.0
        role = "防御主动技" if is_def_main else "主动核心技"
        if is_def_main:
            detail = f"当前 Lv.{lv} / {q}% 品质，{dinfo.get('defenseSummary') or ('防御核心：' + ' / '.join(def_layers[:2]))}。"
        else:
            detail = f"当前 Lv.{lv} / {q}% 品质，技能组主动本体。"
        if downgrade_loss is not None or (downgrade_ehp_loss is not None and downgrade_ehp_loss >= 0.5):
            if downgrade_ehp_loss is not None and downgrade_ehp_loss >= (downgrade_loss or 0.0) and downgrade_ehp_loss >= 1.0:
                advice = f"21/23 降到 20/20 损失 {downgrade_ehp_loss:.1f}% EHP，{'成型后可追高等级' if downgrade_ehp_loss >= 4.0 else '前期 20/20 性价比最高'}。"
            elif (downgrade_loss or 0.0) < 4.5:
                advice = f"21/23 降到 20/20 仅掉 {(downgrade_loss or 0.0):.1f}% DPS，开荒用 20/20 省大钱。"
            else:
                advice = f"21 级提升显著（降 20/20 掉 {(downgrade_loss or 0.0):.1f}% DPS），成型后值得优先买 21 级。"
        else:
            advice = "优先升满 20 级并补满 20% 品质。"
        return role, detail, advice

    def_txt = _def_bits()
    if dps_loss >= 15.0:
        role = "顶级核心乘区"
        detail = f"拆掉后直接掉 {dps_loss:.1f}% DPS，是主技能最不可替代的增伤辅助。"
    elif dps_loss >= 6.0:
        role = "主力输出辅助"
        detail = f"实算提供 +{dps_loss:.1f}% DPS" + (f"（魔耗变化 {mana_cost_delta:+.0f}%）" if abs(mana_cost_delta) >= 8 else "") + "。"
    elif ehp_loss >= 3.0 or lrec_d >= 10.0 or esrec_d >= 15.0 or res_d >= 5.0 or ev_d >= 2.0 or def_d >= 2.0:
        role = "核心防御/回复辅助"
        layer_note = f"（{def_layers[0]}）" if def_layers else ""
        detail = f"拆掉后实算损失 {def_txt}{layer_note}，直接补强生存与回复底线。"
    elif dinfo.get("isDefensive") and def_layers:
        role = "防御/控场辅助"
        detail = f"{def_layers[0]}：{dinfo.get('defenseSummary') or '强化实战生存/控场/恢复能力（静态 EHP 面板不完全体现）'}。"
    elif _SUPPORT_CRIT_RE.search(gname):
        role = "暴击/爆发辅助"
        detail = f"强化暴击率/暴伤体系（实算 DPS -{dps_loss:.1f}%）。"
    elif _SUPPORT_UTILITY_RE.search(gname):
        role = "手感/形态辅助"
        detail = (
            f"改善投射物形态、范围、施速或蓝耗手感（实算 DPS -{dps_loss:.1f}%）。"
            if dps_loss >= 0.5
            else "优化清图范围、投射物覆盖、回蓝或冷却手感（纯单体 DPS 面板不完全体现）。"
        )
    elif dps_loss >= 1.0:
        role = "次级输出辅助"
        detail = f"提供 +{dps_loss:.1f}% DPS 增益，缺孔时可排在最后补。"
    else:
        role = "机制/功能辅助"
        detail = "承担触发条件、异常施加或手感配合（面板单体 DPS 差值不明显）。"

    if downgrade_loss is not None:
        advice = (
            f"21/23→20/20 仅差 {downgrade_loss:.1f}% DPS，前期买 20/20 即可。"
            if downgrade_loss < 3.5
            else f"高等级/品质收益较高（降配掉 {downgrade_loss:.1f}% DPS），后期优先补 21/23。"
        )
    elif lv <= 5 and q >= 20:
        advice = f"刻意压到 Lv.{lv} / {q}%Q 的低级高品配法，抄作业切勿盲目升级！"
    elif dps_loss >= 10.0:
        advice = "必连核心孔位，4连/5连阶段最先上。"
    elif dinfo.get("isDefensive"):
        advice = "低成本高容错防御/回复辅助，抄作业优先补齐。"
    else:
        advice = "按孔位顺位补齐，20/20 性价比最高。"

    return role, detail, advice


def apply_pricing_overlay(
    dossier: dict[str, Any],
    *,
    parsed: dict[str, Any] | None = None,
    price_snapshot: dict[str, Any] | None = None,
    price_overrides: dict[str, float] | None = None,
    ninja_index: dict[str, Any] | None = None,
    league: str = "",
) -> dict[str, Any]:
    """在本地已缓存的纯引擎消融底座上，纯内存覆写最新物价、总造价、性价比与抄作业底座（<5ms，不重跑引擎）。"""
    out = copy.deepcopy(dossier)
    eff_league = (league or out.get("league") or (price_snapshot or {}).get("league") or "").strip()
    if eff_league:
        out["league"] = eff_league
    out["version"] = DOSSIER_VERSION

    items_out: list[dict[str, Any]] = list(out.get("items") or [])
    jewels_out: list[dict[str, Any]] = list(out.get("jewels") or [])
    skills_out: list[dict[str, Any]] = list(out.get("skills") or [])
    all_entries = items_out + jewels_out

    for x in all_entries:
        it_stub = {
            "slot": x.get("slot") or "",
            "name": x.get("name") or "",
            "baseType": x.get("baseType") or "",
            "rarity": x.get("rarity") or "",
        }
        cost = _resolve_item_cost(it_stub, price_snapshot, price_overrides, ninja_index=ninja_index)
        x["cost"] = cost

        rune_imp = x.get("runeImpact") if isinstance(x.get("runeImpact"), dict) else {}
        if ninja_index and isinstance(rune_imp.get("socketedItems"), list):
            for si in rune_imp["socketedItems"]:
                if isinstance(si, dict) and si.get("name"):
                    rhit = market_pricer.lookup_ninja_item(str(si["name"]), ninja_index)
                    if rhit and float(rhit.get("divineValue") or 0) > 0:
                        si["estimatedDivine"] = round(float(rhit["divineValue"]), 2)
                        si["priceSource"] = "ninja"
        deep_stub = {
            "isSpeedWeapon": bool(x.get("isSpeedWeapon")) or (
                x.get("roleKind") == "mechanic" and "攻速" in str(x.get("roleLabel") or "")
            ),
            "passiveSynergies": list(x.get("passiveSynergies") or []),
            "socketAnalysis": {
                "socketCount": int(rune_imp.get("socketCount") or x.get("socketCount") or 0),
                "exceedsCraftableSockets": bool(rune_imp.get("exceedsCraftableSockets")),
            },
            "requireBaseType": bool(x.get("requireBaseType")),
            "baseTypeReason": str(x.get("baseTypeReason") or ""),
        }
        role_info = _classify_item_role_and_copy(
            it_stub,
            dps_loss=_num(x.get("dpsLossPct")),
            ehp_loss=_num(x.get("ehpLossPct")),
            mana_loss=_num(x.get("manaLossPct")),
            spirit_loss=_num(x.get("spiritLoss")),
            max_res_drop=_num(x.get("maxResDrop")),
            attr_broken=bool(x.get("attrBroken")),
            comp_dps=x.get("compensatedDpsLossPct"),
            comp_ehp=x.get("compensatedEhpLossPct"),
            inactive_ws=bool(x.get("inactiveWeaponSet")),
            has_unparsed=bool(x.get("hasUnparsedMods")),
            deep=deep_stub,
            cost=cost,
            affix_stats=x.get("affixStats") or {},
        )
        x.update(role_info)

    # 回填总造价（装备+珠宝+血脉宝石实时价）、占比与单件性价比
    priced_vals = [
        float(x["cost"]["estimatedDivine"])
        for x in all_entries
        if x["cost"]["estimatedDivine"] is not None and x["cost"]["estimatedDivine"] > 0
    ]
    gems_cost_d = 0.0
    priced_gems: list[dict[str, Any]] = []
    if ninja_index:
        seen_gem_keys: set[str] = set()
        gem_names: list[str] = []
        if isinstance(parsed, dict):
            for sg in parsed.get("skillGroups") or []:
                for g in sg.get("gems") or []:
                    if g.get("name"):
                        gem_names.append(str(g["name"]).strip())
        else:
            for sg in skills_out:
                for g in sg.get("gems") or []:
                    if g.get("name"):
                        gem_names.append(str(g["name"]).strip())
        for gname in gem_names:
            gk = market_pricer.norm_price_key(gname)
            if not gk or gk in seen_gem_keys:
                continue
            hit = market_pricer.lookup_ninja_item(gname, ninja_index)
            if hit and float(hit.get("divineValue") or 0) >= 0.05:
                seen_gem_keys.add(gk)
                dv = round(float(hit["divineValue"]), 2)
                gems_cost_d += dv
                priced_gems.append({"name": gname, "divineValue": dv, "formatted": hit.get("formatted") or f"{dv} D"})
    gems_cost_d = round(gems_cost_d, 2)
    total_est_d = round(sum(priced_vals) + gems_cost_d, 2)
    unpriced_rare_count = sum(
        1 for x in all_entries
        if (x["cost"]["estimatedDivine"] is None or x["cost"]["estimatedDivine"] <= 0)
        and str(x.get("rarity") or "").upper() != "NORMAL"
    )
    for x in all_entries:
        est = x["cost"]["estimatedDivine"]
        if est is not None and est > 0:
            if total_est_d > 0:
                x["cost"]["costSharePct"] = round((est / total_est_d) * 100.0, 1)
            net_dps = x["compensatedDpsLossPct"] if x["compensatedDpsLossPct"] is not None else x["dpsLossPct"]
            net_ehp = x["compensatedEhpLossPct"] if x["compensatedEhpLossPct"] is not None else x["ehpLossPct"]
            if net_dps > 0:
                x["cost"]["dpsPerDivine"] = round(net_dps / est, 2)
            if net_ehp > 0:
                x["cost"]["ehpPerDivine"] = round(net_ehp / est, 2)

    top_priorities: list[str] = []
    for x in sorted(all_entries, key=lambda e: (e["copyTier"] != "mandatory", -_num(e.get("impactScore"))))[:5]:
        net_d = x["compensatedDpsLossPct"] if x["compensatedDpsLossPct"] is not None else x["dpsLossPct"]
        net_e = x["compensatedEhpLossPct"] if x["compensatedEhpLossPct"] is not None else x["ehpLossPct"]
        cost_txt = f" (~{x['cost']['estimatedDivine']}D)" if x["cost"]["estimatedDivine"] else ""
        top_priorities.append(
            f"{x['slot']} · {x['name']}{cost_txt}：[{x['roleLabel']}] DPS -{net_d:.1f}% / EHP -{net_e:.1f}%"
        )

    budget_cuts: list[str] = []
    for x in all_entries:
        if x["copyTier"] == "luxury":
            budget_cuts.append(f"{x['slot']} · {x['name']}（~{x['cost']['estimatedDivine'] or '?'}D，面板仅 DPS -{x['dpsLossPct']:.1f}%，前期先用平替）")
        elif str(x.get("rarity") or "").upper() != "UNIQUE" and ((x.get("affixStats") or {}).get("cut", 0) + (x.get("affixStats") or {}).get("dead", 0)) >= 2:
            cut_n = (x.get("affixStats") or {}).get("cut", 0) + (x.get("affixStats") or {}).get("dead", 0)
            budget_cuts.append(f"{x['slot']} · 含 {cut_n} 条低收益/可砍词条，搜平替仅锁 {(x.get('affixStats') or {}).get('must', 0)} 条必保词缀")
    for s in skills_out:
        if s.get("maxDowngrade2020LossPct") is not None and s["maxDowngrade2020LossPct"] < 4.5:
            budget_cuts.append(f"技能「{s['mainGemName']}」组：21/23 降配 20/20 最多仅掉 {s['maxDowngrade2020LossPct']:.1f}% DPS，开荒省钱")

    redline_anchors: list[str] = []
    for x in all_entries:
        if x.get("attrBroken") or _num(x.get("spiritLoss")) >= 20 or _num(x.get("maxResDrop")) >= 35:
            tags = []
            if x.get("attrBroken"):
                tags.append("属性门槛")
            if _num(x.get("spiritLoss")) >= 20:
                tags.append(f"精魂 -{_num(x.get('spiritLoss')):.0f}")
            if _num(x.get("maxResDrop")) >= 35:
                tags.append(f"抗性 -{_num(x.get('maxResDrop')):.0f}%")
            redline_anchors.append(f"{x['slot']} · {x['name']}（{' / '.join(tags)}）")

    summary = dict(out.get("summary") or {})
    summary["mandatoryCount"] = sum(1 for x in all_entries if x["copyTier"] == "mandatory")
    summary["coreCount"] = sum(1 for x in all_entries if x["copyTier"] == "core")
    summary["luxuryCount"] = sum(1 for x in all_entries if x["copyTier"] == "luxury")

    out["items"] = items_out
    out["jewels"] = jewels_out
    out["skills"] = skills_out
    out["totalEstimatedDivine"] = total_est_d
    out["gemsCostDivine"] = gems_cost_d
    out["pricedGems"] = priced_gems
    out["pricedItemCount"] = len(priced_vals)
    out["unpricedItemCount"] = unpriced_rare_count
    out["totalItemCount"] = len(all_entries)
    out["summary"] = summary
    out["copyFoundation"] = {
        "topPriorities": top_priorities[:6],
        "budgetCuts": budget_cuts[:6],
        "redlineAnchors": redline_anchors[:6],
    }
    out["pricesUpdatedAt"] = int(time.time() * 1000)
    return out


def build_ablation_dossier(
    code_or_xml: str,
    pool: Any = None,
    *,
    pool_factory: Callable[[], Any] | None = None,
    price_snapshot: dict[str, Any] | None = None,
    price_overrides: dict[str, float] | None = None,
    league: str = "",
    allow_ninja_network: bool = True,
    state_dir: str = STATE_DIR,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """运行目标 BD 全量引擎消融（装备 + 珠宝 + 技能/辅助 + 全量词缀与基底自带技能 + 实时造价）。
    本地缓存按纯 PoB 消融底座持久化；后续切赛季、更新 Ninja 价或手填价仅在本地底座上瞬时覆写，不再重跑消融。
    """
    t0 = time.time()
    xml = pob_parser.decode_pob(code_or_xml)
    norm_xml, _ = runefix.normalize_runes(xml)
    bhash = compute_build_hash(norm_xml)
    ckey = _base_cache_key(norm_xml)

    eff_league = (league or (price_snapshot or {}).get("league") or "").strip()
    if not eff_league:
        eff_league = market_pricer.default_trade_league(allow_network=allow_ninja_network, state_dir=state_dir)

    # 拉取当前赛季 poe.ninja 实时物价（30 分钟缓存；离线或禁网自动跳过，不阻塞）
    ninja_index: dict[str, Any] | None = None
    try:
        ninja_index = market_pricer.fetch_ninja_item_index(
            eff_league,
            force_refresh=False,
            allow_network=allow_ninja_network,
            state_dir=state_dir,
        )
    except Exception:
        ninja_index = None

    pricing_sig = _price_signature(
        price_snapshot,
        price_overrides,
        league=eff_league,
        ninja_index=ninja_index,
    )

    if not force_refresh:
        cached_base, migrated_legacy = _load_base_cache(norm_xml, state_dir=state_dir)
        if cached_base is not None:
            same_pricing = (
                cached_base.get("pricingSignature") == pricing_sig
                and cached_base.get("league") == eff_league
            )
            if same_pricing:
                if migrated_legacy:
                    try:
                        _save_cache(ckey, cached_base, state_dir=state_dir)
                    except Exception:
                        pass
                return {**cached_base, "cached": True}
            # apply_pricing_overlay 在 parsed=None 时直接复用缓存中的 skills 列表，免去重复 XML 解析
            updated = apply_pricing_overlay(
                cached_base,
                parsed=None,
                price_snapshot=price_snapshot,
                price_overrides=price_overrides,
                ninja_index=ninja_index,
                league=eff_league,
            )
            updated["pricingSignature"] = pricing_sig
            try:
                _save_cache(ckey, updated, state_dir=state_dir)
            except Exception:
                pass
            return {**updated, "cached": True}

    active_pool = pool if pool is not None else (pool_factory() if pool_factory is not None else None)
    if active_pool is None:
        raise ValueError("缺少 PoB2 引擎 WorkerPool，无法执行全量消融")

    parsed = pob_parser.parse_pob_xml(norm_xml)
    base_sim = active_pool.simulate(norm_xml, include_audit=True)
    lock_key = str(base_sim.get("dpsSource") or "") or None
    active_ws = int(_num(base_sim.get("activeWeaponSet"), 1))
    unparsed_mods_list = [u for u in (base_sim.get("unparsedMods") or []) if isinstance(u, dict)]
    unparsed_slots = {str(u.get("slot") or "") for u in unparsed_mods_list}
    unparsed_lines_by_slot: dict[str, set[str]] = {}
    for u in unparsed_mods_list:
        sl = str(u.get("slot") or "")
        ln = str(u.get("line") or "").strip().lower()
        if sl and ln:
            unparsed_lines_by_slot.setdefault(sl, set()).add(ln)

    base_redlines = gap_analyzer.evaluate_redlines(base_sim)
    base_redline_msgs = {r["message"] for r in base_redlines}

    def calc_metrics(sim: dict[str, Any]) -> dict[str, Any]:
        return _compute_sim_delta(base_sim, sim, ref_redline_msgs=base_redline_msgs)

    # ---------- 组装统一批量模拟队列（含精确 XML 去重 + 零损失剪枝） ----------
    jobs: list[tuple[dict[str, Any], int]] = []
    xml_batch: list[str] = []
    xml_to_idx: dict[str, int] = {}
    dedup_saved = 0
    pruned_zero_impact = 0

    def enqueue(meta: dict[str, Any], mutated_xml: str) -> None:
        nonlocal dedup_saved
        h = hashlib.sha1(mutated_xml.encode("utf-8")).hexdigest()
        bidx = xml_to_idx.get(h)
        if bidx is None:
            bidx = len(xml_batch)
            xml_to_idx[h] = bidx
            xml_batch.append(mutated_xml)
        else:
            dedup_saved += 1
        jobs.append((meta, bidx))

    all_objects: list[tuple[str, dict[str, Any]]] = [
        ("item", it) for it in (parsed.get("equippedItems") or []) if it.get("slot")
    ] + [
        ("jewel", j) for j in (parsed.get("jewels") or []) if j.get("slot")
    ]

    deep_by_key: dict[str, dict[str, Any]] = {}
    rune_entries_by_key: dict[str, list[str]] = {}
    bp_synergy = affix_analyzer.extract_build_synergy_profile(parsed)

    for kind, it in all_objects:
        slot = str(it.get("slot") or "")
        item_id = str(it.get("itemId") or "")
        obj_key = f"{kind}:{slot}:{item_id}"
        deep_by_key[obj_key] = affix_analyzer.analyze_item_deeply(it, parsed)

        # 1. 脱装 / 拔珠宝本体消融（若装备基底自带重要活跃技能，同步禁用其自带技能组，防止无 source 技能组残留低估装备贡献）
        imp_granted_names = [
            str(gs.get("skillName") or "")
            for gs in (deep_by_key[obj_key].get("importantGrantedSkills") or [])
            if gs.get("skillName")
        ]
        try:
            if kind == "jewel" and str(it.get("nodeId") or "").strip():
                mut = xml_mutator.ablate_tree_jewel(norm_xml, str(it["nodeId"]))
            elif imp_granted_names:
                mut = xml_mutator.ablate_slot_with_granted_skills(norm_xml, slot, imp_granted_names, item_id=item_id)
            else:
                mut = xml_mutator.ablate_slot(norm_xml, slot)
            enqueue({"type": "object", "objKey": obj_key}, mut)
        except Exception:
            pass

        # 2. 若装备带三属性/精魂，直接并行跑门槛补偿对照
        if kind == "item":
            try:
                comp_xml = xml_mutator.compensate_ablation(norm_xml, slot)
                if comp_xml:
                    enqueue({"type": "comp", "objKey": obj_key}, comp_xml)
            except Exception:
                pass

        # 3. 该件装备/珠宝的逐条原生词缀消融（基底自带技能同步禁用对应技能组；严格仅剪枝 100% 确定无面板影响的独立行与折行续行）
        native_mods = [m for m in (it.get("mods") or []) if m and not re.search(r"\(rune\)|\{rune\}", m, re.I)]
        for mod_idx, mod_line in enumerate(native_mods):
            if _ZERO_COMBAT_IMPACT_MOD_RE.match(mod_line.strip()) or _is_wrapped_continuation_line(mod_line):
                pruned_zero_impact += 1
                continue
            try:
                if xml_mutator.extract_granted_skill(mod_line):
                    mut = xml_mutator.ablate_item_granted_skill(norm_xml, item_id, slot, mod_line)
                else:
                    mut = xml_mutator.remove_item_mod(norm_xml, item_id, mod_line)
                enqueue({"type": "affix", "objKey": obj_key, "modIdx": mod_idx, "modLine": mod_line}, mut)
            except Exception:
                continue

        # 4. 符文整体剥离消融 + 装备插槽物品逐颗独立消融（符文 / 灵核 / 神像 / 碎片 / 遗物）
        ordered_runes = [str(r).strip() for r in (it.get("runeEntries") or []) if str(r).strip()]
        if not ordered_runes and it.get("socketedRunes"):
            # 旧版无 Rune: 行格式兜底：用 modDetails 里 origin=='rune' 的词条作为逐条插槽项
            ordered_runes = [
                str(d.get("text") or "").strip()
                for d in (it.get("modDetails") or [])
                if d.get("origin") == "rune" and str(d.get("text") or "").strip()
            ]
        rune_entries_by_key[obj_key] = ordered_runes

        if it.get("socketedRunes") or ordered_runes:
            try:
                mut = xml_mutator.strip_item_runes(norm_xml, item_id)
                enqueue({"type": "rune", "objKey": obj_key}, mut)
            except Exception:
                pass
            for r_idx, r_name in enumerate(ordered_runes):
                try:
                    mut_r = xml_mutator.ablate_item_rune_at(norm_xml, item_id, r_idx)
                    enqueue({"type": "socketed_item", "objKey": obj_key, "runeIdx": r_idx, "runeName": r_name}, mut_r)
                except Exception:
                    continue

    # 5. 技能组 + 辅助宝石（含防御/回复/光环/位移控场技能组的全部辅助） + 21/23 降配消融
    skill_groups = parsed.get("skillGroups") or []
    for sg in skill_groups:
        sidx = int(sg.get("index") or 0)
        gems = sg.get("gems") or []
        if sidx <= 0 or not gems:
            continue
        is_main = bool(sg.get("isMain"))
        main_active_idx = sg.get("mainActiveSkill")
        main_gem_pos = (main_active_idx - 1) if isinstance(main_active_idx, int) and 1 <= main_active_idx <= len(gems) else 0
        first_gem_name = str(gems[0].get("name") or "")

        if not is_main:
            try:
                enqueue({"type": "skill_group", "skillIndex": sidx}, xml_mutator.ablate_skill_group(norm_xml, sidx))
            except Exception:
                pass

        for gidx, gem in enumerate(gems):
            is_main_gem = is_main and gidx == main_gem_pos
            gname = str(gem.get("name") or "")
            if is_main and not is_main_gem:
                try:
                    enqueue(
                        {"type": "skill_gem", "skillIndex": sidx, "gemIndex": gidx},
                        xml_mutator.ablate_skill_gem(norm_xml, sidx, gidx),
                    )
                except Exception:
                    pass
            elif not is_main and gidx > 0 and len(gems) <= 6 and (
                _SPIRIT_AURA_RE.search(first_gem_name)
                or _TRIGGER_RE.search(first_gem_name)
                or _CURSE_MARK_RE.search(first_gem_name)
                or _DEFENSE_SKILL_RE.search(first_gem_name)
                or _TRAVEL_RE.search(first_gem_name)
                or _DEFENSE_SUPPORT_RE.search(gname)
            ):
                try:
                    enqueue(
                        {"type": "skill_gem", "skillIndex": sidx, "gemIndex": gidx},
                        xml_mutator.ablate_skill_gem(norm_xml, sidx, gidx),
                    )
                except Exception:
                    pass

            # 21+ 级或 21%+ 品质宝石，实算降配回 20/20 的损耗
            if int(_num(gem.get("level"), 1)) > 20 or int(_num(gem.get("quality"), 0)) > 20:
                try:
                    enqueue(
                        {"type": "gem_downgrade", "skillIndex": sidx, "gemIndex": gidx},
                        xml_mutator.downgrade_skill_gem(norm_xml, sidx, gidx, max_level=20, max_quality=20),
                    )
                except Exception:
                    pass

    # 6. 升华天赋整套消融 + 逐个大点孤立/分支消融
    asc_name = str(((parsed.get("character") or {}).get("ascendancy")) or "").strip()
    alloc_node_ids = pob_parser.extract_allocated_node_ids(norm_xml)
    asc_plan = tree_index.plan_ascendancy_ablation(
        alloc_node_ids,
        asc_name,
        tree_version=(parsed.get("passiveTree") or {}).get("treeVersion") or None,
    )
    if asc_plan.get("allAscNodeIds"):
        all_asc_granted: list[str] = []
        notable_granted: dict[str, list[str]] = {}
        for np in asc_plan["notables"]:
            gs_list: list[str] = []
            for st in np.get("stats") or []:
                g = xml_mutator.extract_granted_skill(st)
                if g:
                    gs_list.append(g[0])
                    all_asc_granted.append(g[0])
            notable_granted[np["id"]] = gs_list
        try:
            enqueue(
                {"type": "asc_total"},
                xml_mutator.ablate_passive_nodes(norm_xml, asc_plan["allAscNodeIds"], granted_skill_names=all_asc_granted),
            )
        except Exception:
            pass
        for np in asc_plan["notables"]:
            nid = np["id"]
            disc = list(np.get("disconnectedIds") or [])
            child_granted: list[str] = []
            for d_id in disc:
                if d_id in notable_granted:
                    child_granted.extend(notable_granted[d_id])
            try:
                enqueue(
                    {"type": "asc_branch", "nodeId": nid},
                    xml_mutator.ablate_passive_nodes(
                        norm_xml,
                        list(np["nodeIds"]) + disc,
                        granted_skill_names=notable_granted[nid] + child_granted,
                    ),
                )
            except Exception:
                pass
            if disc:
                try:
                    enqueue(
                        {"type": "asc_children", "nodeId": nid},
                        xml_mutator.ablate_passive_nodes(norm_xml, disc, granted_skill_names=child_granted),
                    )
                except Exception:
                    pass

    # 7. 将倒推灵敏度与武器 5 维探针直接并入主批次，消除二次 simulate_batch 的尾延迟
    rev_probe_ctx: dict[str, Any] | None = None
    rev_t0 = time.time()
    try:
        from backend.domain import stat_sensitivity
        rev_probe_ctx = stat_sensitivity.prepare_reverse_probe_jobs(norm_xml, parsed, base_sim, include_probes=True)
        for probe_key, probe_xml in rev_probe_ctx.get("batchJobs") or []:
            enqueue({"type": "rev_probe", "probeKey": probe_key}, probe_xml)
    except Exception:
        rev_probe_ctx = None

    sim_results = active_pool.simulate_batch(xml_batch, lock_dps_key=lock_key, include_audit=False) if xml_batch else []

    # ---------- 汇总模拟结果 ----------
    obj_sim_map: dict[str, dict[str, Any]] = {}
    comp_sim_map: dict[str, dict[str, Any]] = {}
    affix_sim_map: dict[tuple[str, int], dict[str, Any]] = {}
    rune_sim_map: dict[str, dict[str, Any]] = {}
    socketed_sim_map: dict[tuple[str, int], dict[str, Any]] = {}
    sg_sim_map: dict[int, dict[str, Any]] = {}
    gem_sim_map: dict[tuple[int, int], dict[str, Any]] = {}
    gem_dg_map: dict[tuple[int, int], dict[str, Any]] = {}
    asc_total_sim: dict[str, Any] | None = None
    asc_branch_sims: dict[str, dict[str, Any]] = {}
    asc_children_sims: dict[str, dict[str, Any]] = {}
    rev_probe_sims: dict[str, dict[str, Any]] = {}

    for meta, bidx in jobs:
        sim = sim_results[bidx]
        jtype = meta["type"]
        if jtype == "object":
            obj_sim_map[meta["objKey"]] = calc_metrics(sim)
        elif jtype == "comp":
            comp_sim_map[meta["objKey"]] = calc_metrics(sim)
        elif jtype == "affix":
            affix_sim_map[(meta["objKey"], meta["modIdx"])] = calc_metrics(sim)
        elif jtype == "rune":
            rune_sim_map[meta["objKey"]] = calc_metrics(sim)
        elif jtype == "socketed_item":
            socketed_sim_map[(meta["objKey"], meta["runeIdx"])] = calc_metrics(sim)
        elif jtype == "skill_group":
            sg_sim_map[meta["skillIndex"]] = calc_metrics(sim)
        elif jtype == "skill_gem":
            gem_sim_map[(meta["skillIndex"], meta["gemIndex"])] = calc_metrics(sim)
        elif jtype == "gem_downgrade":
            gem_dg_map[(meta["skillIndex"], meta["gemIndex"])] = calc_metrics(sim)
        elif jtype == "asc_total":
            asc_total_sim = sim
        elif jtype == "asc_branch":
            asc_branch_sims[meta["nodeId"]] = sim
        elif jtype == "asc_children":
            asc_children_sims[meta["nodeId"]] = sim
        elif jtype == "rev_probe":
            if isinstance(sim, dict) and not sim.get("error"):
                rev_probe_sims[meta["probeKey"]] = sim

    # ---------- 组装装备 & 珠宝详情 ----------
    has_dwd = any(
        (k.get("name") or "").lower() == "dance with death"
        for k in ((parsed.get("passiveTree") or {}).get("keystones") or [])
    )

    items_out: list[dict[str, Any]] = []
    jewels_out: list[dict[str, Any]] = []
    total_affixes_tested = 0
    total_must_affixes = 0
    total_cut_affixes = 0

    for kind, it in all_objects:
        slot = str(it.get("slot") or "")
        item_id = str(it.get("itemId") or "")
        name = str(it.get("name") or it.get("baseType") or "")
        base_type = str(it.get("baseType") or "")
        rarity = str(it.get("rarity") or "").upper()
        obj_key = f"{kind}:{slot}:{item_id}"
        deep = deep_by_key.get(obj_key) or {}
        cost = _resolve_item_cost(it, price_snapshot, price_overrides, ninja_index=ninja_index)

        m = obj_sim_map.get(obj_key) or {
            "dpsLossPct": 0.0,
            "ehpLossPct": 0.0,
            "manaLossPct": 0.0,
            "spiritLoss": 0.0,
            "maxResDrop": 0.0,
            "attrBroken": False,
            "triggeredRedlines": [],
        }
        cm = comp_sim_map.get(obj_key)
        comp_dps = cm["dpsLossPct"] if cm is not None else None
        comp_ehp = cm["ehpLossPct"] if cm is not None else None

        inactive_ws = _is_inactive_weapon_slot(slot, active_ws)
        has_unparsed = slot in unparsed_slots

        # 逐条词缀目的整理
        native_mods = [mod for mod in (it.get("mods") or []) if mod and not re.search(r"\(rune\)|\{rune\}", mod, re.I)]
        deep_by_line: dict[str, list[dict[str, Any]]] = {}
        for da in deep.get("affixes") or []:
            deep_by_line.setdefault(str(da.get("line") or "").lower(), []).append(da)
        deep_cursor: dict[str, int] = {}

        affix_entries: list[dict[str, Any]] = []
        for mod_idx, mod_line in enumerate(native_mods):
            if _is_wrapped_continuation_line(mod_line):
                if affix_entries:
                    affix_entries[-1]["line"] = f"{affix_entries[-1]['line']} {mod_line.strip()}"
                continue
            k = mod_line.strip().lower()
            cands = deep_by_line.get(k) or []
            cur = deep_cursor.get(k, 0)
            dmeta = cands[min(cur, len(cands) - 1)] if cands else {}
            deep_cursor[k] = cur + 1

            am = affix_sim_map.get((obj_key, mod_idx)) or {
                "dpsLossPct": 0.0,
                "ehpLossPct": 0.0,
                "manaLossPct": 0.0,
                "spiritLoss": 0.0,
                "maxResDrop": 0.0,
                "attrBroken": False,
                "triggeredRedlines": [],
            }
            if (obj_key, mod_idx) in affix_sim_map:
                total_affixes_tested += 1

            cls = _classify_affix_purpose(
                mod_line,
                dps_loss=am["dpsLossPct"],
                ehp_loss=am["ehpLossPct"],
                mana_loss=am["manaLossPct"],
                spirit_loss=am["spiritLoss"],
                max_res_drop=am["maxResDrop"],
                attr_broken=am["attrBroken"],
                triggered_redlines=am["triggeredRedlines"],
                dmeta=dmeta,
                is_speed_weapon=bool(deep.get("isSpeedWeapon")),
                slot=slot,
                is_unique=(rarity == "UNIQUE"),
            )
            copy_action = "skip" if (rarity == "UNIQUE" and not dmeta.get("isAnoint")) else cls["copyAction"]
            affix_entries.append({
                "line": mod_line,
                "origin": str(dmeta.get("origin") or "explicit"),
                "side": str(dmeta.get("side") or "unknown"),
                "tier": cls["tier"],
                "purposeKind": cls["purposeKind"],
                "purposeLabel": cls["purposeLabel"],
                "purposeDetail": cls["purposeDetail"],
                "functionalLoss": cls.get("functionalLoss") or "",
                "dpsLossPct": am["dpsLossPct"],
                "ehpLossPct": am["ehpLossPct"],
                "manaLossPct": am["manaLossPct"],
                "spiritLoss": am["spiritLoss"],
                "maxResDrop": am["maxResDrop"],
                "attrBroken": am["attrBroken"],
                "triggeredRedlines": am["triggeredRedlines"],
                "synergyTags": list(dmeta.get("synergyTags") or []),
                "craftDifficulty": str(dmeta.get("craftDifficulty") or "easy"),
                "copyAction": copy_action,
            })

        tier_order = {"must": 0, "nice": 1, "cut": 2}
        affix_entries.sort(
            key=lambda a: (
                tier_order.get(a["tier"], 9),
                a["purposeKind"] == "dead",
                -(abs(a["dpsLossPct"]) + abs(a["ehpLossPct"]) + a["maxResDrop"] * 0.2),
            )
        )

        affix_stats = {
            "total": len(affix_entries),
            "must": sum(1 for a in affix_entries if a["tier"] == "must"),
            "nice": sum(1 for a in affix_entries if a["tier"] == "nice"),
            "cut": sum(1 for a in affix_entries if a["tier"] == "cut" and a["purposeKind"] != "dead"),
            "dead": sum(1 for a in affix_entries if a["purposeKind"] == "dead"),
        }
        total_must_affixes += affix_stats["must"]
        total_cut_affixes += affix_stats["cut"] + affix_stats["dead"]

        # 符文插槽贡献（整体 + 逐颗装备插槽物品：符文 / 灵核 / 神像 / 碎片 / 遗物）
        sa = deep.get("socketAnalysis") or {}
        ordered_runes = rune_entries_by_key.get(obj_key) or []
        rune_metas = [
            rune_index.lookup_socketed_item(rn, slot=slot, base_type=base_type)
            for rn in ordered_runes
        ]
        # 检查同件装备内是否有类似 Fox Idol 赋予同件神像 Bonded 生效的词条
        item_enables_idol_bonded = any(
            any("idols socketed in this item gain the benefits of their bonded" in ml.lower() for ml in (rm.get("mods") or []))
            for rm in rune_metas
            if isinstance(rm, dict)
        )
        slot_unparsed_lines = unparsed_lines_by_slot.get(slot) or set()

        socketed_items_out: list[dict[str, Any]] = []
        for r_idx, r_name in enumerate(ordered_runes):
            rmeta = rune_metas[r_idx]
            sm = socketed_sim_map.get((obj_key, r_idx)) or _zero_delta()
            rtype = str((rmeta or {}).get("runeType") or "Rune")
            rtype_label = str((rmeta or {}).get("runeTypeLabel") or rune_index.rune_type_label(rtype, r_name))
            rmods = list((rmeta or {}).get("mods") or [])
            rbonded = list((rmeta or {}).get("bondedMods") or [])
            runlocks = list((rmeta or {}).get("unlockPools") or [])
            if not rmods and r_name and any(ch.isdigit() for ch in r_name):
                rmods = [r_name]

            enables_bonded = any(
                "idols socketed in this item gain the benefits of their bonded" in ml.lower()
                for ml in rmods
            )
            bonded_active_here = bool(
                rbonded and (
                    bp_synergy.get("bondedRunesActive")
                    or (item_enables_idol_bonded and rtype.lower() == "idol")
                )
            )
            grants_ks: str | None = None
            for ml in rmods:
                ml_clean = ml.strip()
                if ml_clean and len(ml_clean) <= 42 and not any(ch.isdigit() for ch in ml_clean):
                    ninfo = tree_index.lookup_notable_by_name(
                        ml_clean,
                        tree_version=(parsed.get("passiveTree") or {}).get("treeVersion") or None,
                    )
                    if ninfo and ninfo.get("kind") == "keystone":
                        grants_ks = str(ninfo["name"])
                        break
            is_unparsed_rune = any(ml.strip().lower() in slot_unparsed_lines for ml in rmods)

            s_cls = _classify_socketed_item_purpose(
                r_name,
                meta=rmeta,
                metrics=sm,
                bonded_active=bonded_active_here,
                enables_idol_bonded=enables_bonded,
                grants_keystone=grants_ks,
                is_unparsed=is_unparsed_rune,
                inactive_ws=inactive_ws,
                slot=slot,
                item_name=name,
            )
            r_est_d = None
            r_price_src = "unknown"
            if ninja_index:
                rhit = market_pricer.lookup_ninja_item(r_name, ninja_index)
                if rhit and float(rhit.get("divineValue") or 0) > 0:
                    r_est_d = round(float(rhit["divineValue"]), 2)
                    r_price_src = "ninja"

            socketed_items_out.append({
                "index": r_idx,
                "name": str((rmeta or {}).get("name") or r_name),
                "runeType": rtype,
                "runeTypeLabel": rtype_label,
                "effects": rmods,
                "bondedEffects": rbonded,
                "bondedActive": bonded_active_here,
                "unlockPools": runlocks,
                "grantsKeystone": grants_ks or "",
                "isSocketBound": bool((rmeta or {}).get("isSocketBound")),
                "isUnparsed": is_unparsed_rune,
                "estimatedDivine": r_est_d,
                "priceSource": r_price_src,
                "dpsLossPct": sm["dpsLossPct"],
                "ehpLossPct": sm["ehpLossPct"],
                "physMaxHitLossPct": sm["physMaxHitLossPct"],
                "eleMaxHitLossPct": sm["eleMaxHitLossPct"],
                "esLossPct": sm["esLossPct"],
                "lifeLossPct": sm["lifeLossPct"],
                "manaLossPct": sm["manaLossPct"],
                "spiritLoss": sm["spiritLoss"],
                "maxResDrop": sm["maxResDrop"],
                "attrBroken": sm["attrBroken"],
                "triggeredRedlines": sm["triggeredRedlines"],
                **s_cls,
            })

        rune_impact = None
        if obj_key in rune_sim_map or sa.get("socketCount", 0) > 0 or socketed_items_out:
            rm = rune_sim_map.get(obj_key) or _zero_delta()
            rune_impact = {
                "dpsLossPct": rm["dpsLossPct"],
                "ehpLossPct": rm["ehpLossPct"],
                "physMaxHitLossPct": rm["physMaxHitLossPct"],
                "eleMaxHitLossPct": rm["eleMaxHitLossPct"],
                "spiritLoss": rm["spiritLoss"],
                "maxResDrop": rm["maxResDrop"],
                "advice": str(sa.get("advice") or ""),
                "socketCount": int(sa.get("socketCount") or len(socketed_items_out)),
                "exceedsCraftableSockets": bool(sa.get("exceedsCraftableSockets")),
                "socketedRunes": list(sa.get("socketedRunes") or []),
                "socketedItems": socketed_items_out,
            }

        # 综合贡献分（用门槛补偿后的净值防虚高，但保留门槛加分）
        eff_dps_loss = comp_dps if comp_dps is not None else m["dpsLossPct"]
        eff_ehp_loss = comp_ehp if comp_ehp is not None else m["ehpLossPct"]
        score = (
            max(0.0, eff_dps_loss) * 1.6
            + max(0.0, eff_ehp_loss) * 1.1
            + max(0.0, m["manaLossPct"]) * 0.55
            + max(0.0, m["spiritLoss"]) * 0.35
            + max(0.0, m["maxResDrop"]) * 0.35
            + (12.0 if m["attrBroken"] else 0.0)
        )

        notes: list[str] = []
        if inactive_ws:
            notes.append("位于副武器组（切武器生效）")
        if has_dwd and slot.lower() in ("weapon 2", "weapon 2 swap") and m["dpsLossPct"] < 0:
            notes.append("脱副手触发「与死共舞」(+25% more 攻速)，ΔDPS 为负不代表副手是负提升")
        if has_unparsed:
            notes.append("含 PoB2 未完全实算词条（实战价值高于纯面板差值）")
        if m["attrBroken"]:
            notes.append("承担关键属性门槛")
        if comp_dps is not None and abs(m["dpsLossPct"] - comp_dps) >= 8.0:
            notes.append(f"补齐属性/精魂门槛后净贡献 DPS -{comp_dps:.1f}% / EHP -{comp_ehp:.1f}%")

        if deep.get("requireBaseType") and deep.get("baseTypeReason"):
            notes.append(f"锁基底：{deep['baseTypeReason']}")

        role_info = _classify_item_role_and_copy(
            it,
            dps_loss=m["dpsLossPct"],
            ehp_loss=m["ehpLossPct"],
            mana_loss=m["manaLossPct"],
            spirit_loss=m["spiritLoss"],
            max_res_drop=m["maxResDrop"],
            attr_broken=m["attrBroken"],
            comp_dps=comp_dps,
            comp_ehp=comp_ehp,
            inactive_ws=inactive_ws,
            has_unparsed=has_unparsed,
            deep=deep,
            cost=cost,
            affix_stats=affix_stats,
        )

        # 用实算必保词缀同步更新平替/半成品推荐锁词（传奇/暗金不生成做装锁词）
        budget_plan = dict(deep.get("budgetPlan") or {})
        semi_plan = dict(deep.get("semiPlan") or {})
        if rarity != "UNIQUE":
            must_lock = [
                a["line"] for a in affix_entries
                if a["tier"] == "must" and a["copyAction"] == "lock" and a["origin"] != "implicit"
            ]
            semi_lock = [
                a["line"] for a in affix_entries
                if a["tier"] == "must" and a["copyAction"] == "lock" and a["origin"] != "implicit"
                and (a["craftDifficulty"] == "hard" or a["origin"] == "fractured")
            ] or must_lock[:2]
            if must_lock:
                budget_plan["lockAffixes"] = list(dict.fromkeys(must_lock))[:3]
            if semi_lock:
                semi_plan["lockAffixes"] = list(dict.fromkeys(semi_lock))[:2]

        entry = {
            "slot": slot,
            "kind": kind,
            "itemId": item_id,
            "name": name,
            "baseType": base_type,
            "rarity": rarity,
            "corrupted": bool(it.get("corrupted")),
            "socketCount": int(it.get("socketCount") or 0),
            "levelReq": int(it.get("levelReq") or 0),
            "requireBaseType": bool(deep.get("requireBaseType")),
            "baseTypeReason": str(deep.get("baseTypeReason") or ""),
            "importantGrantedSkills": list(deep.get("importantGrantedSkills") or []),
            "dpsLossPct": m["dpsLossPct"],
            "ehpLossPct": m["ehpLossPct"],
            "manaLossPct": m["manaLossPct"],
            "spiritLoss": m["spiritLoss"],
            "maxResDrop": m["maxResDrop"],
            "attrBroken": m["attrBroken"],
            "triggeredRedlines": m["triggeredRedlines"],
            "compensatedDpsLossPct": comp_dps,
            "compensatedEhpLossPct": comp_ehp,
            "inactiveWeaponSet": inactive_ws,
            "hasUnparsedMods": has_unparsed,
            "isSpeedWeapon": bool(deep.get("isSpeedWeapon")),
            "impactScore": round(score, 1),
            "note": "；".join(notes),
            "cost": cost,
            "affixStats": affix_stats,
            "affixes": affix_entries,
            "runeImpact": rune_impact,
            "passiveSynergies": list(deep.get("passiveSynergies") or []),
            "budgetPlan": budget_plan,
            "semiPlan": semi_plan,
            **role_info,
        }
        if kind == "jewel":
            jewels_out.append(entry)
        else:
            items_out.append(entry)

    # ---------- 回填总造价（装备+珠宝+血脉宝石实时价）、占比与单件性价比 ----------
    all_entries = items_out + jewels_out
    priced_vals = [float(x["cost"]["estimatedDivine"]) for x in all_entries if x["cost"]["estimatedDivine"] is not None and x["cost"]["estimatedDivine"] > 0]
    gems_cost_d = 0.0
    priced_gems: list[dict[str, Any]] = []
    if ninja_index:
        seen_gem_keys: set[str] = set()
        for sg in parsed.get("skillGroups") or []:
            for g in sg.get("gems") or []:
                gname = str(g.get("name") or "").strip()
                gk = market_pricer.norm_price_key(gname)
                if not gk or gk in seen_gem_keys:
                    continue
                hit = market_pricer.lookup_ninja_item(gname, ninja_index)
                if hit and float(hit.get("divineValue") or 0) >= 0.05:
                    seen_gem_keys.add(gk)
                    dv = round(float(hit["divineValue"]), 2)
                    gems_cost_d += dv
                    priced_gems.append({"name": gname, "divineValue": dv, "formatted": hit.get("formatted") or f"{dv} D"})
    gems_cost_d = round(gems_cost_d, 2)
    total_est_d = round(sum(priced_vals) + gems_cost_d, 2)
    unpriced_rare_count = sum(
        1 for x in all_entries
        if (x["cost"]["estimatedDivine"] is None or x["cost"]["estimatedDivine"] <= 0)
        and str(x.get("rarity") or "").upper() != "NORMAL"
    )
    for x in all_entries:
        est = x["cost"]["estimatedDivine"]
        if est is not None and est > 0:
            if total_est_d > 0:
                x["cost"]["costSharePct"] = round((est / total_est_d) * 100.0, 1)
            net_dps = x["compensatedDpsLossPct"] if x["compensatedDpsLossPct"] is not None else x["dpsLossPct"]
            net_ehp = x["compensatedEhpLossPct"] if x["compensatedEhpLossPct"] is not None else x["ehpLossPct"]
            if net_dps > 0:
                x["cost"]["dpsPerDivine"] = round(net_dps / est, 2)
            if net_ehp > 0:
                x["cost"]["ehpPerDivine"] = round(net_ehp / est, 2)

    items_out.sort(key=lambda x: x["impactScore"], reverse=True)
    jewels_out.sort(key=lambda x: x["impactScore"], reverse=True)

    # ---------- 组装技能组与辅助宝石消融详情（含完整技能防御层分析） ----------
    try:
        pdict_local = patch_dict_updater.load_patch_dict(auto_build=False)
    except Exception:
        pdict_local = None

    spirit_by_name: dict[str, float] = {}
    for sa_row in base_sim.get("spiritAudit") or []:
        if isinstance(sa_row, dict) and sa_row.get("name"):
            spirit_by_name[str(sa_row["name"]).lower()] = _num(sa_row.get("spiritReserved"))

    own_dps_by_name: dict[str, float] = {}
    for sk_row in base_sim.get("allSkillsDps") or []:
        if isinstance(sk_row, dict) and sk_row.get("name"):
            own_dps_by_name[str(sk_row["name"]).lower()] = _num(sk_row.get("dps"))

    skills_out: list[dict[str, Any]] = []
    skill_defense_highlights: list[dict[str, Any]] = []
    for sg in skill_groups:
        sidx = int(sg.get("index") or 0)
        gems = sg.get("gems") or []
        if not gems:
            continue
        is_main = bool(sg.get("isMain"))
        main_active_idx = sg.get("mainActiveSkill")
        main_gem_pos = (main_active_idx - 1) if isinstance(main_active_idx, int) and 1 <= main_active_idx <= len(gems) else 0
        main_gem_name = str(gems[main_gem_pos].get("name") or gems[0].get("name") or "技能组")

        sgm = sg_sim_map.get(sidx) or _zero_delta()
        audit_sp = spirit_by_name.get(main_gem_name.lower(), 0.0)
        spirit_saved = max(sgm["spiritSaved"], audit_sp)
        own_dps = own_dps_by_name.get(main_gem_name.lower(), 0.0)

        gem_entries: list[dict[str, Any]] = []
        group_def_layers: list[str] = []
        group_def_descs: list[str] = []
        for gidx, gem in enumerate(gems):
            is_mg = (is_main and gidx == main_gem_pos) or (not is_main and gidx == 0)
            gm = gem_sim_map.get((sidx, gidx))
            dgm = gem_dg_map.get((sidx, gidx))
            eff_gm = gm if gm is not None else (sgm if is_mg and not is_main else _zero_delta())
            gdps = gm["dpsLossPct"] if gm else 0.0
            gehp = gm["ehpLossPct"] if gm else 0.0
            gmcost = gm["manaCostDeltaPct"] if gm else 0.0
            gsp = gm["spiritSaved"] if gm else 0.0
            dg_loss = dgm["dpsLossPct"] if dgm else None
            dg_ehp_loss = dgm["ehpLossPct"] if dgm else None

            g_def = _detect_gem_defense_info(
                str(gem.get("name") or ""),
                pdict=pdict_local,
                metrics=eff_gm,
            )
            for lyr in g_def["defenseLayers"]:
                if lyr not in group_def_layers:
                    group_def_layers.append(lyr)
            if g_def["defenseSummary"] and g_def["defenseSummary"] not in group_def_descs:
                group_def_descs.append(g_def["defenseSummary"])

            g_role, g_detail, g_advice = _classify_gem_role(
                gem,
                is_main_gem=is_mg,
                dps_loss=gdps,
                ehp_loss=gehp,
                mana_cost_delta=gmcost,
                downgrade_loss=dg_loss,
                downgrade_ehp_loss=dg_ehp_loss,
                defense_info=g_def,
                metrics=eff_gm,
            )
            gem_entries.append({
                "name": str(gem.get("name") or ""),
                "skillId": str(gem.get("skillId") or ""),
                "level": int(_num(gem.get("level"), 1)),
                "quality": int(_num(gem.get("quality"), 0)),
                "isSupport": not is_mg,
                "isMainGem": is_mg,
                "tested": gm is not None,
                "dpsLossPct": gdps,
                "ehpLossPct": gehp,
                "physMaxHitLossPct": eff_gm["physMaxHitLossPct"] if gm else 0.0,
                "eleMaxHitLossPct": eff_gm["eleMaxHitLossPct"] if gm else 0.0,
                "maxResDrop": eff_gm["maxResDrop"] if gm else 0.0,
                "evadeDrop": eff_gm["evadeDrop"] if gm else 0.0,
                "deflectDrop": eff_gm["deflectDrop"] if gm else 0.0,
                "lifeRecoveryDrop": eff_gm["lifeRecoveryDrop"] if gm else 0.0,
                "esRecoveryDrop": eff_gm["esRecoveryDrop"] if gm else 0.0,
                "manaCostDeltaPct": gmcost,
                "spiritSaved": gsp,
                "downgrade2020DpsLossPct": dg_loss,
                "downgrade2020EhpLossPct": dg_ehp_loss,
                "isDefensive": g_def["isDefensive"],
                "defenseLayers": g_def["defenseLayers"],
                "roleLabel": g_role,
                "purposeDetail": g_detail,
                "copyAdvice": g_advice,
            })

        # 主技能组按主宝石置顶 + 辅助按 DPS+防御 贡献降序
        if is_main and len(gem_entries) > 1:
            primary_g = [g for g in gem_entries if g["isMainGem"]]
            sup_g = sorted(
                [g for g in gem_entries if not g["isMainGem"]],
                key=lambda x: (x["dpsLossPct"] + x["ehpLossPct"] * 0.8),
                reverse=True,
            )
            gem_entries = primary_g + sup_g

        sg_def_base = _detect_gem_defense_info(main_gem_name, pdict=pdict_local, metrics=sgm)
        for lyr in sg_def_base["defenseLayers"]:
            if lyr not in group_def_layers:
                group_def_layers.insert(0, lyr)
        group_def_info = {
            "isDefensive": bool(sg_def_base["isDefensive"] or group_def_layers),
            "defenseLayers": group_def_layers[:5],
            "defenseSummary": "；".join(group_def_descs[:2]) or sg_def_base["defenseSummary"],
            "defenseScore": round(
                sg_def_base["defenseScore"] + sum(3.0 for g in gem_entries if g["isSupport"] and g["isDefensive"]),
                1,
            ),
            "physMaxHitLossPct": sgm["physMaxHitLossPct"],
            "eleMaxHitLossPct": sgm["eleMaxHitLossPct"],
            "esLossPct": sgm["esLossPct"],
            "lifeLossPct": sgm["lifeLossPct"],
            "armourLossPct": sgm["armourLossPct"],
            "evadeDrop": sgm["evadeDrop"],
            "deflectDrop": sgm["deflectDrop"],
            "blockDrop": sgm["blockDrop"],
            "lifeRecoveryDrop": sgm["lifeRecoveryDrop"],
            "esRecoveryDrop": sgm["esRecoveryDrop"],
        }

        s_cls = _classify_skill_group(
            sg,
            dps_loss=sgm["dpsLossPct"],
            ehp_loss=sgm["ehpLossPct"],
            mana_loss=sgm["manaLossPct"],
            spirit_saved=spirit_saved,
            max_res_drop=sgm["maxResDrop"],
            own_dps=own_dps,
            gem_entries=gem_entries,
            defense_info=group_def_info,
            metrics=sgm,
        )

        # 汇总 21/23 降配最大损耗提示
        dg_vals = [g["downgrade2020DpsLossPct"] for g in gem_entries if g["downgrade2020DpsLossPct"] is not None]
        max_dg = round(max(dg_vals), 1) if dg_vals else None

        skill_row = {
            "index": sidx,
            "isMain": is_main,
            "mainGemName": main_gem_name,
            "slot": str(sg.get("slot") or ""),
            "weaponSet": str(sg.get("weaponSet") or ""),
            "dpsLossPct": sgm["dpsLossPct"],
            "ehpLossPct": sgm["ehpLossPct"],
            "physMaxHitLossPct": sgm["physMaxHitLossPct"],
            "eleMaxHitLossPct": sgm["eleMaxHitLossPct"],
            "evadeDrop": sgm["evadeDrop"],
            "deflectDrop": sgm["deflectDrop"],
            "blockDrop": sgm["blockDrop"],
            "lifeRecoveryDrop": sgm["lifeRecoveryDrop"],
            "esRecoveryDrop": sgm["esRecoveryDrop"],
            "manaLossPct": sgm["manaLossPct"],
            "spiritSaved": round(spirit_saved, 1),
            "maxResDrop": sgm["maxResDrop"],
            "skillOwnDps": round(own_dps, 1),
            "maxDowngrade2020LossPct": max_dg,
            "defenseAnalysis": group_def_info,
            "gems": gem_entries,
            "note": "",
            **s_cls,
        }
        skills_out.append(skill_row)

        if group_def_info["isDefensive"] and not is_main:
            skill_defense_highlights.append({
                "index": sidx,
                "mainGemName": main_gem_name,
                "roleLabel": s_cls["roleLabel"],
                "ehpLossPct": sgm["ehpLossPct"],
                "physMaxHitLossPct": sgm["physMaxHitLossPct"],
                "eleMaxHitLossPct": sgm["eleMaxHitLossPct"],
                "maxResDrop": sgm["maxResDrop"],
                "evadeDrop": sgm["evadeDrop"],
                "deflectDrop": sgm["deflectDrop"],
                "lifeRecoveryDrop": sgm["lifeRecoveryDrop"],
                "esRecoveryDrop": sgm["esRecoveryDrop"],
                "spiritSaved": round(spirit_saved, 1),
                "defenseLayers": group_def_info["defenseLayers"],
                "defenseSummary": group_def_info["defenseSummary"],
                "defenseScore": group_def_info["defenseScore"],
            })

    # 技能排序：主技能置顶，其次按 DPS + 防御贡献 + 精魂占用降序
    skills_out.sort(
        key=lambda s: (
            not s["isMain"],
            -(
                max(0.0, s["dpsLossPct"]) * 1.5
                + max(0.0, s["ehpLossPct"]) * 1.2
                + (s.get("defenseAnalysis") or {}).get("defenseScore", 0.0) * 0.45
                + s["spiritSaved"] * 0.2
            ),
        )
    )
    skill_defense_highlights.sort(key=lambda x: x["defenseScore"], reverse=True)

    # ---------- 组装升华天赋消融剖析 ----------
    ascendancy_out: dict[str, Any] | None = None
    if asc_plan.get("allAscNodeIds"):
        ascendancy_out = _assemble_ascendancy_section(
            asc_plan,
            base_sim=base_sim,
            total_sim=asc_total_sim,
            branch_sims=asc_branch_sims,
            children_sims=asc_children_sims,
            base_redline_msgs=base_redline_msgs,
        )

    total_socketed_tested = sum(
        len(((x.get("runeImpact") or {}).get("socketedItems") or []))
        for x in items_out
    )

    # ---------- 生成后期抄作业基础信息底座 ----------
    top_priorities: list[str] = []
    for x in sorted(all_entries, key=lambda e: (e["copyTier"] != "mandatory", -e["impactScore"]))[:5]:
        net_d = x["compensatedDpsLossPct"] if x["compensatedDpsLossPct"] is not None else x["dpsLossPct"]
        net_e = x["compensatedEhpLossPct"] if x["compensatedEhpLossPct"] is not None else x["ehpLossPct"]
        cost_txt = f" (~{x['cost']['estimatedDivine']}D)" if x["cost"]["estimatedDivine"] else ""
        top_priorities.append(
            f"{x['slot']} · {x['name']}{cost_txt}：[{x['roleLabel']}] DPS -{net_d:.1f}% / EHP -{net_e:.1f}%"
        )

    budget_cuts: list[str] = []
    for x in all_entries:
        if x["copyTier"] == "luxury":
            budget_cuts.append(f"{x['slot']} · {x['name']}（~{x['cost']['estimatedDivine'] or '?'}D，面板仅 DPS -{x['dpsLossPct']:.1f}%，前期先用平替）")
        elif x["rarity"] != "UNIQUE" and (x["affixStats"]["cut"] + x["affixStats"]["dead"]) >= 2:
            cut_n = x["affixStats"]["cut"] + x["affixStats"]["dead"]
            budget_cuts.append(f"{x['slot']} · 含 {cut_n} 条低收益/可砍词条，搜平替仅锁 {x['affixStats']['must']} 条必保词缀")
    for s in skills_out:
        if s["maxDowngrade2020LossPct"] is not None and s["maxDowngrade2020LossPct"] < 4.5:
            budget_cuts.append(f"技能「{s['mainGemName']}」组：21/23 降配 20/20 最多仅掉 {s['maxDowngrade2020LossPct']:.1f}% DPS，开荒省钱")

    redline_anchors: list[str] = []
    for x in all_entries:
        if x["attrBroken"] or x["spiritLoss"] >= 20 or x["maxResDrop"] >= 35:
            tags = []
            if x["attrBroken"]:
                tags.append("属性门槛")
            if x["spiritLoss"] >= 20:
                tags.append(f"精魂 -{x['spiritLoss']:.0f}")
            if x["maxResDrop"] >= 35:
                tags.append(f"抗性 -{x['maxResDrop']:.0f}%")
            redline_anchors.append(f"{x['slot']} · {x['name']}（{' / '.join(tags)}）")

    try:
        from backend.domain import stat_sensitivity
        if rev_probe_ctx is not None:
            reverse_spec = stat_sensitivity.assemble_reverse_build_spec(
                parsed,
                base_sim,
                rev_probe_ctx,
                rev_probe_sims,
                dossier={"items": items_out, "jewels": jewels_out},
                started_at=rev_t0,
            )
        else:
            reverse_spec = stat_sensitivity.compute_reverse_build_spec(
                norm_xml,
                active_pool,
                base_sim=base_sim,
                parsed=parsed,
                dossier={"items": items_out, "jewels": jewels_out},
            )
    except Exception:
        reverse_spec = None

    dossier = {
        "version": DOSSIER_VERSION,
        "buildHash": bhash,
        "league": eff_league,
        "pricingSignature": pricing_sig,
        "generatedAt": int(time.time() * 1000),
        "pricesUpdatedAt": int(time.time() * 1000),
        "elapsedMs": round((time.time() - t0) * 1000.0, 1),
        "cached": False,
        "baseSim": base_sim,
        "reverseSpec": reverse_spec,
        "totalEstimatedDivine": total_est_d,
        "gemsCostDivine": gems_cost_d,
        "pricedGems": priced_gems,
        "pricedItemCount": len(priced_vals),
        "unpricedItemCount": unpriced_rare_count,
        "totalItemCount": len(all_entries),
        "summary": {
            "mandatoryCount": sum(1 for x in all_entries if x["copyTier"] == "mandatory"),
            "coreCount": sum(1 for x in all_entries if x["copyTier"] == "core"),
            "luxuryCount": sum(1 for x in all_entries if x["copyTier"] == "luxury"),
            "totalAffixesTested": total_affixes_tested,
            "totalSocketedItemsTested": total_socketed_tested,
            "ascendancyNodeCount": len((ascendancy_out or {}).get("nodes") or []),
            "defensiveSkillCount": len(skill_defense_highlights),
            "mustAffixCount": total_must_affixes,
            "cuttableAffixCount": total_cut_affixes,
            "totalSimRuns": len(xml_batch) + 1,
            "dedupSavedRuns": dedup_saved,
            "prunedZeroImpactRuns": pruned_zero_impact,
        },
        "items": items_out,
        "jewels": jewels_out,
        "skills": skills_out,
        "ascendancy": ascendancy_out,
        "skillDefenseSummary": skill_defense_highlights,
        "copyFoundation": {
            "topPriorities": top_priorities[:6],
            "budgetCuts": budget_cuts[:6],
            "redlineAnchors": redline_anchors[:6],
        },
    }

    try:
        _save_cache(ckey, dossier, state_dir=state_dir)
    except Exception:
        pass

    return dossier


def _is_valid_reverse_spec(spec: Any, *, require_tested: bool = False) -> bool:
    if not isinstance(spec, dict) or not spec.get("slotSpecs"):
        return False
    from backend.domain import stat_sensitivity
    if require_tested:
        if spec.get("version") != stat_sensitivity.REVERSE_SPEC_VERSION:
            return False
        sens = spec.get("statSensitivity") or []
        if not any(isinstance(s, dict) and s.get("tested") is True for s in sens):
            return False
    return True


def ensure_reverse_spec(
    code_or_xml: str,
    pool: Any | None = None,
    *,
    dossier: dict[str, Any] | None = None,
    base_sim: dict[str, Any] | None = None,
    state_dir: str = STATE_DIR,
) -> dict[str, Any] | None:
    """获取或按需惰性补算目标 BD 的倒推灵敏度规格包（reverseSpec），并自动刷新历史上未实测的离线假缓存。"""
    need_live = pool is not None
    if isinstance(dossier, dict) and _is_valid_reverse_spec(dossier.get("reverseSpec"), require_tested=need_live):
        return dossier["reverseSpec"]
    try:
        xml = pob_parser.decode_pob(code_or_xml)
        norm_xml, _ = runefix.normalize_runes(xml)
    except Exception:
        return None
    cached, _ = _load_base_cache(norm_xml, state_dir=state_dir)
    if isinstance(cached, dict) and _is_valid_reverse_spec(cached.get("reverseSpec"), require_tested=need_live):
        return cached["reverseSpec"]

    from backend.domain import stat_sensitivity
    eff_base_sim = base_sim or (cached.get("baseSim") if isinstance(cached, dict) else None)
    eff_dossier = dossier or cached
    spec = stat_sensitivity.compute_reverse_build_spec(
        norm_xml,
        pool,
        base_sim=eff_base_sim if isinstance(eff_base_sim, dict) else None,
        dossier=eff_dossier if isinstance(eff_dossier, dict) else None,
    )
    if isinstance(cached, dict) and spec:
        cached["reverseSpec"] = spec
        try:
            _save_cache(_base_cache_key(norm_xml), cached, state_dir=state_dir)
        except Exception:
            pass
    return spec
