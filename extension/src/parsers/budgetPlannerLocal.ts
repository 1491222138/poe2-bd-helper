/**
 * 纯前端零延迟抄作业分层器：结合「装备三档分级 + LevelReq 等级门槛 + 市场价快照 + 用户等级/预算滑块/已有件」，
 * 拖动滑块或修改等级时 0ms 实时标出每件核心装备的购买优先级与平替提示。
 */
import type {
  BuildProfileResponse,
  CopyBuildContext,
  CraftDecisionResponse,
  ParsedItem,
} from "@contracts";
import type { AttachedUniquePrice } from "../market/ninjaPrice";
import type { ItemTradeQuote, ThreeWayMarketResult } from "../market/tradeBuilder";
import { analyzeItemDeeply, type ItemDeepAnalysis } from "./affixAnalyzer";

export type GearCopyStatus = "owned" | "level-blocked" | "mandatory-buy" | "within-budget" | "over-budget-sub";

export interface EvaluatedGearCopyItem {
  slot: string;
  name: string;
  baseType: string;
  rarity: string;
  isRareCraftable: boolean;
  rawItem: ParsedItem;
  tier: "mandatory" | "amplifier" | "luxury" | "normal";
  status: GearCopyStatus;
  statusLabel: string;
  levelReq: number;
  estimatedDivine: number | null;
  priceSource: "trade" | "ninja" | "estimate" | "unknown";
  reason: string;
  deepAnalysis?: ItemDeepAnalysis;
}

export interface LocalCopyPlanSummary {
  characterLevel: number;
  targetLevel: number;
  missingPassivePoints: number;
  budgetDivine: number;
  coveredCostDivine: number;
  remainingDivine: number;
  levelBlockedCount: number;
  overBudgetCount: number;
  items: EvaluatedGearCopyItem[];
}

// 仅在无实时市场价快照时作为兜底参考估价 (Divine)
const FALLBACK_CHASE_ESTIMATES: Record<string, number> = {
  temporalis: 180,
  astramentis: 90,
  mageblood: 120,
  headhunter: 55,
  "kalandra's touch": 80,
  "the adorned": 60,
  voices: 50,
  "heart of the well": 25,
  "against the darkness": 20,
  "prism of belief": 25,
  "rite of passage": 18,
  ingenuity: 18,
  "dream fragments": 18,
  "morior invictus": 14,
  svalinn: 15,
  "controlled metamorphosis": 12,
  "from nothing": 12,
  "the ordained": 10,
  "the taming": 4,
  "yoke of suffering": 3,
  "guiding palm of the eye": 2,
  "lavianga's spirits": 0.5,
  "the fall of the axe": 1,
  "beira's anguish": 1,
};

function matchesName(needle: string, item: ParsedItem): boolean {
  const n = needle.toLowerCase();
  const iname = (item.name || "").toLowerCase();
  const bname = (item.baseType || "").toLowerCase();
  const slot = (item.slot || "").toLowerCase();
  return (
    (iname.length > 0 && (n.includes(iname) || iname.includes(n))) ||
    (bname.length > 0 && n.includes(bname)) ||
    (slot.length > 0 && n.includes(slot))
  );
}

export function evaluateLocalCopyPlan(
  profile: BuildProfileResponse,
  ctx: CopyBuildContext,
  uniquePrices: AttachedUniquePrice[] = [],
  tradeQuotes: Record<string, ItemTradeQuote> = {}
): LocalCopyPlanSummary {
  const charLevel = Math.max(1, Math.min(100, Math.round(ctx.characterLevel ?? 90)));
  const targetLevel = profile.build.character.level || 100;
  const budgetDivine = Math.max(0, Number(ctx.budgetDivine ?? 15));
  const ownedSet = new Set((ctx.ownedItems || []).map((x) => x.trim().toLowerCase()).filter(Boolean));

  const ninjaByName = new Map<string, number>();
  for (const u of uniquePrices) {
    if (u.divineValue && u.divineValue > 0) {
      ninjaByName.set(u.name.toLowerCase(), u.divineValue);
    }
  }

  const tiers = profile.positioning.gearTiers;
  const allGear: ParsedItem[] = [
    ...profile.build.equippedItems,
    ...profile.build.jewels,
    ...profile.build.flasksAndCharms,
  ];

  // 去重并确定每件装备的本质分级（优先用 AI 本质拆解出的 gearTiers，兜底用规则）
  const classifyTier = (it: ParsedItem): "mandatory" | "amplifier" | "luxury" | "normal" => {
    if (tiers) {
      if (tiers.mandatory?.some((m) => matchesName(m, it))) return "mandatory";
      if (tiers.luxury?.some((m) => matchesName(m, it))) return "luxury";
      if (tiers.amplifier?.some((m) => matchesName(m, it))) return "amplifier";
    }
    const nm = (it.name || "").toLowerCase();
    if (nm in FALLBACK_CHASE_ESTIMATES && FALLBACK_CHASE_ESTIMATES[nm] >= 18) return "luxury";
    if (it.rarity.toUpperCase() === "UNIQUE") return "amplifier";
    if (it.slot.toLowerCase().includes("weapon") || it.properties?.Ward) return "mandatory";
    return "normal";
  };

  const hasLiveNinja = uniquePrices.length > 0;
  const resolvePrice = (it: ParsedItem): { price: number | null; source: EvaluatedGearCopyItem["priceSource"] } => {
    const exactQuote = tradeQuotes[`${it.slot}:exact`];
    if (exactQuote?.divineValue && exactQuote.divineValue > 0) {
      return { price: exactQuote.divineValue, source: "trade" };
    }
    const budgetQuote = tradeQuotes[`${it.slot}:budget`];
    if (budgetQuote?.divineValue && budgetQuote.divineValue > 0) {
      return { price: budgetQuote.divineValue, source: "trade" };
    }
    const ninja = ninjaByName.get((it.name || "").toLowerCase());
    if (ninja && ninja > 0) {
      return { price: ninja, source: "ninja" };
    }
    // 仅在 poe.ninja 离线未加载时才兜底，避免实时数据已就绪时再用过期硬编码误导价格
    if (!hasLiveNinja) {
      const est = FALLBACK_CHASE_ESTIMATES[(it.name || "").toLowerCase()];
      if (est !== undefined) {
        return { price: est, source: "estimate" };
      }
    }
    return { price: null, source: "unknown" };
  };

  const tierOrder: Record<EvaluatedGearCopyItem["tier"], number> = {
    mandatory: 0,
    amplifier: 1,
    normal: 2,
    luxury: 3,
  };

  const enriched = allGear.map((it) => {
    const tier = classifyTier(it);
    const { price, source } = resolvePrice(it);
    return { it, tier, price, source };
  });
  enriched.sort((a, b) => tierOrder[a.tier] - tierOrder[b.tier] || (a.price ?? 999) - (b.price ?? 999));

  let remaining = budgetDivine;
  let covered = 0;
  let levelBlockedCount = 0;
  let overBudgetCount = 0;

  const items: EvaluatedGearCopyItem[] = enriched.map(({ it, tier, price, source }) => {
    const displayName = it.name || it.baseType || it.slot;
    const isRareCraftable =
      it.rarity.toUpperCase() !== "UNIQUE" &&
      !/flask|charm|jewel/i.test(it.slot || "") &&
      !/flask|charm|jewel/i.test(it.baseType || "");
    const deepAnalysis = isRareCraftable ? analyzeItemDeeply(it, profile.build) : undefined;
    const isOwned =
      ownedSet.has(displayName.toLowerCase()) ||
      ownedSet.has((it.slot || "").toLowerCase()) ||
      (it.name ? ownedSet.has(it.name.toLowerCase()) : false);
    const req = it.levelReq || 0;

    if (isOwned) {
      return {
        slot: it.slot,
        name: displayName,
        baseType: it.baseType,
        rarity: it.rarity,
        isRareCraftable,
        rawItem: it,
        tier,
        status: "owned",
        statusLabel: "已拥有",
        levelReq: req,
        estimatedDivine: price,
        priceSource: source,
        reason: "不占用当前预算",
        deepAnalysis,
      };
    }

    if (req > charLevel) {
      levelBlockedCount += 1;
      return {
        slot: it.slot,
        name: displayName,
        baseType: it.baseType,
        rarity: it.rarity,
        isRareCraftable,
        rawItem: it,
        tier,
        status: "level-blocked",
        statusLabel: `需 Lv.${req}`,
        levelReq: req,
        estimatedDivine: price,
        priceSource: source,
        reason: `当前 Lv.${charLevel} 穿不上，先用低需求过渡件`,
        deepAnalysis,
      };
    }

    const cost = price ?? 1.0;
    if (tier === "luxury" && (cost > remaining || budgetDivine < cost * 1.4)) {
      overBudgetCount += 1;
      return {
        slot: it.slot,
        name: displayName,
        baseType: it.baseType,
        rarity: it.rarity,
        isRareCraftable,
        rawItem: it,
        tier,
        status: "over-budget-sub",
        statusLabel: "奢侈件 · 建议平替",
        levelReq: req,
        estimatedDivine: price,
        priceSource: source,
        reason: `估价约 ${cost.toFixed(1)}D，当前预算 ${budgetDivine}D 建议先用平替黄装/珠宝过渡`,
        deepAnalysis,
      };
    }

    if (cost <= remaining + 0.01) {
      remaining = Math.max(0, remaining - cost);
      covered += cost;
      const isMandatory = tier === "mandatory";
      return {
        slot: it.slot,
        name: displayName,
        baseType: it.baseType,
        rarity: it.rarity,
        isRareCraftable,
        rawItem: it,
        tier,
        status: isMandatory ? "mandatory-buy" : "within-budget",
        statusLabel: isMandatory ? "机制必买" : "预算内可入",
        levelReq: req,
        estimatedDivine: price,
        priceSource: source,
        reason: isMandatory ? "构筑运转刚需，优先分配预算" : "在当前预算内可优先购入",
        deepAnalysis,
      };
    }

    overBudgetCount += 1;
    return {
      slot: it.slot,
      name: displayName,
      baseType: it.baseType,
      rarity: it.rarity,
      isRareCraftable,
      rawItem: it,
      tier,
      status: "over-budget-sub",
      statusLabel: tier === "mandatory" ? "超预算 · 需变通" : "超预算 · 先平替",
      levelReq: req,
      estimatedDivine: price,
      priceSource: source,
      reason:
        tier === "mandatory"
          ? `机制关键件（约 ${cost.toFixed(1)}D）超出剩余预算，可搜平替或看低配变通`
          : `约 ${cost.toFixed(1)}D 超出剩余预算，先买平替词缀过渡`,
      deepAnalysis,
    };
  });

  return {
    characterLevel: charLevel,
    targetLevel,
    missingPassivePoints: Math.max(0, targetLevel - charLevel),
    budgetDivine,
    coveredCostDivine: Number(covered.toFixed(1)),
    remainingDivine: Number(remaining.toFixed(1)),
    levelBlockedCount,
    overBudgetCount,
    items,
  };
}

/**
 * 纯插件离线降级三路决策器（本地后端未启动时，基于官方市集三档实价快速给出推荐与直达按钮；
 * 启动本地后端后自动无缝升级为 HTC 风格 MDP 精确概率求解）。
 */
export function buildOfflineThreeWayDecision(
  item: ParsedItem,
  market: ThreeWayMarketResult,
  league: string
): CraftDecisionResponse {
  const fin = market.finishedQuote?.divineValue ?? null;
  const semi = market.semiQuote?.divineValue ?? null;
  const base = market.baseQuote?.divineValue ?? null;

  // 保守估算离线加工通货成本（提示用户启动本地后端获取精确 HTC 概率）
  const semiTotalEst = semi !== null ? Math.round((semi * 2.2 + 0.6) * 100) / 100 : null;
  const rawTotalEst = base !== null ? Math.round((base * 12 + 2.8) * 100) / 100 : null;

  let recommended: CraftDecisionResponse["recommended"] = "inconclusive";
  let headline = "已获取实时市集价（启动本地后端可解锁 HTC 精确做装概率）";
  let tradeUrl = market.finishedUrl;
  let tradeButtonLabel = "直达市集买成品";
  let saveDivine: number | null = null;
  let expectedDivine: number | null = fin;

  if (fin !== null) {
    if (semiTotalEst !== null && semiTotalEst < fin * 0.78) {
      recommended = "craft_semi";
      saveDivine = Math.round((fin - semiTotalEst) * 100) / 100;
      expectedDivine = semiTotalEst;
      headline = `建议买半成品加工 · 约 ${semiTotalEst} D（比成品省 ~${saveDivine} D，离线估算）`;
      tradeUrl = market.semiUrl;
      tradeButtonLabel = "直达市集买半成品底子";
    } else if (rawTotalEst !== null && rawTotalEst < fin * 0.72) {
      recommended = "craft_raw";
      saveDivine = Math.round((fin - rawTotalEst) * 100) / 100;
      expectedDivine = rawTotalEst;
      headline = `建议买白底自做 · 约 ${rawTotalEst} D（比成品省 ~${saveDivine} D，离线估算）`;
      tradeUrl = market.baseUrl;
      tradeButtonLabel = "直达市集买白底";
    } else {
      recommended = "buy_finished";
      expectedDivine = fin;
      headline = `建议直接买成品 · ${fin} D（零炸底子风险）`;
      tradeUrl = market.finishedUrl;
      tradeButtonLabel = "直达市集买成品";
    }
  } else if (semi !== null) {
    headline = `市集暂无同款成品，建议买半成品底子 (${semi} D) 自行加工`;
    tradeUrl = market.semiUrl;
    tradeButtonLabel = "直达市集买半成品底子";
    expectedDivine = semiTotalEst;
  }

  return {
    slot: item.slot,
    itemClass: item.slot,
    ilvl: Math.max(82, item.levelReq || 82),
    recommended,
    summary: `${headline}。提示：启动本地后端 (127.0.0.1:8788) 即可启用完整 HTC 精确概率与预兆/精华步骤配方。`,
    finishedBuyPrice: fin,
    semiBasePrice: semi,
    whiteBasePrice: base,
    openPrefixes: 2,
    openSuffixes: 2,
    matchedWanted: [],
    matchedExisting: [],
    estimates: [],
    rates: {
      league,
      source: "fallback",
      updatedAt: Date.now(),
      exaltPerDivine: 100,
      chaosPerDivine: 40,
      currencyDivine: {},
      essencesDivine: {},
    },
    finishedTradeUrl: market.finishedUrl,
    semiTradeUrl: market.semiUrl,
    whiteBaseTradeUrl: market.baseUrl,
    htcExternalUrl: "https://www.poe2htc.com/",
    bestAction: {
      mode: recommended,
      headline,
      saveDivine,
      expectedDivine,
      p80Divine: expectedDivine ? Math.round(expectedDivine * 1.5 * 100) / 100 : null,
      tradeUrl,
      tradeButtonLabel,
      steps: [
        {
          step: 1,
          action: tradeButtonLabel,
          costDivine: recommended === "craft_semi" ? semi ?? undefined : recommended === "craft_raw" ? base ?? undefined : fin ?? undefined,
          detail: "已按当前赛季与核心词缀自动生成官方 Trade2 筛选链接。",
        },
      ],
    },
  };
}
