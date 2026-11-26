/**
 * 市集价格数据包契约（扩展查价 -> 本地后端 -> 桌面端）
 */

export type PriceKind = "exact" | "budget" | "semi" | "base";

export interface SlotPriceEntry {
  slot: string;
  itemName?: string;
  kind: PriceKind;
  divineValue: number;
  currencyLabel?: string;
  listings?: number;
  tradeUrl?: string;
  source?: string;
  note?: string;
  updatedAt: number;
}

export interface PriceSnapshot {
  league: string;
  source: "extension" | "manual" | "poe.ninja" | "backend-trade" | "backend-market";
  updatedAt: number;
  /** 向后兼容的快捷映射：slot -> 原件神圣石价格 */
  prices: Record<string, number>;
  /** 精细化多档报价（原件 / 平替 / 半成品底子） */
  entries?: SlotPriceEntry[];
}
