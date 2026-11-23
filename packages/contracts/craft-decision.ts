/**
 * 买成品 vs 买半成品加工决策契约（V1 轻量模型，策略可扩展）
 */

export type CraftStrategyKind = "semi_exalt" | "essence_lock" | "raw_roll";

export interface CraftCompareRequest {
  slot: string;
  ilvl: number;
  /** 目标想要的词缀（文本匹配或词缀 ID） */
  wantAffixes: string[];
  /** 半成品已拥有的词缀（用于锁定前缀/后缀占用与同组互斥） */
  existingAffixes?: string[];
  /** 半成品底子市场价（Divine） */
  semiBasePrice?: number;
  /** 直接买成品/平替市场价（Divine） */
  finishedBuyPrice?: number;
  /** 可选精华名与单价 */
  essenceName?: string;
  essencePrice?: number;
}

export interface CraftStrategyEstimate {
  strategy: CraftStrategyKind;
  label: string;
  hitProbability: number;
  expectedAttempts: number | null;
  expectedCostDivine: number | null;
  p80CostDivine: number | null;
  brickRiskNote?: string;
}

export interface CraftDecisionResponse {
  slot: string;
  recommended: "buy_finished" | "craft_semi" | "craft_raw" | "inconclusive";
  summary: string;
  finishedBuyPrice: number | null;
  estimates: CraftStrategyEstimate[];
}
