/**
 * 将扩展查到的暗金实价（poe.ninja）与官方市集三档报价（exact / budget / semi）
 * 打包为 packages/contracts/price-snapshot.ts 规定的标准 PriceSnapshot。
 */
import type { PriceSnapshot, SlotPriceEntry } from "@contracts";
import type { AttachedUniquePrice } from "./ninjaPrice";
import type { ItemTradeQuote } from "./tradeBuilder";

export function buildPriceSnapshot(
  league: string,
  uniquePrices: AttachedUniquePrice[],
  tradeQuotes: ItemTradeQuote[]
): PriceSnapshot {
  const now = Date.now();
  const entries: SlotPriceEntry[] = [];
  const prices: Record<string, number> = {};

  for (const u of uniquePrices) {
    if (u.divineValue <= 0) continue;
    entries.push({
      slot: u.slot,
      kind: "exact",
      divineValue: u.divineValue,
      currencyLabel: u.formatted,
      note: `${u.group} · ${u.name} (poe.ninja)`,
      updatedAt: now,
    });
    prices[u.slot] = u.divineValue;
  }

  for (const q of tradeQuotes) {
    entries.push({
      slot: q.slot,
      kind: q.kind,
      divineValue: q.divineValue,
      currencyLabel: q.formatted,
      listings: q.totalListings,
      tradeUrl: q.webUrl,
      note: q.modeLabel,
      updatedAt: q.queriedAt,
    });
    // 向后兼容的快捷映射优先保留 exact，其次 budget
    if (q.kind === "exact" || prices[q.slot] === undefined) {
      prices[q.slot] = q.divineValue;
    }
  }

  return {
    league: league || "Standard",
    source: "extension",
    updatedAt: now,
    prices,
    entries,
  };
}
