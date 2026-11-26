/**
 * PoE2 poe.ninja 实时物价查价器（带 30 分钟本地缓存，覆盖暗金、通货、宝石）。
 */
import type { ParsedBuildData } from "@contracts";

const CACHE_TTL_MS = 30 * 60 * 1000;

export interface LeagueInfo {
  name: string;
  displayName: string;
  url: string;
}

export interface NinjaPriceEntry {
  name: string;
  baseType: string;
  divineValue: number;
  exaltedValue: number;
  listingCount: number;
  type: string;
  formatted: string;
  source: "poe.ninja";
}

export interface LeaguePriceIndex {
  league: string;
  updatedAt: number;
  exaltPerDivine: number | null;
  itemCount: number;
  byName: Record<string, NinjaPriceEntry>;
}

export interface AttachedUniquePrice {
  group: string;
  slot: string;
  name: string;
  divineValue: number;
  formatted: string;
}

function fetchJsonViaBg<T = unknown>(url: string): Promise<T | null> {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: "FETCH_JSON", url }, (resp) => {
      if (chrome.runtime.lastError || !resp?.ok) {
        resolve(null);
        return;
      }
      resolve(resp.data as T);
    });
  });
}

export function normPriceKey(name: string): string {
  return String(name || "")
    .toLowerCase()
    .replace(/['’`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export async function fetchActiveLeagues(): Promise<LeagueInfo[]> {
  const state = await fetchJsonViaBg<{ economyLeagues?: Array<{ name?: string; displayName?: string; url?: string }> }>(
    "https://poe.ninja/poe2/api/data/index-state"
  );
  const leagues: LeagueInfo[] = [];

  if (state && Array.isArray(state.economyLeagues)) {
    for (const l of state.economyLeagues) {
      if (l?.name) {
        leagues.push({ name: l.name, displayName: l.displayName || l.name, url: l.url || "" });
      }
    }
  }

  if (leagues.length === 0) {
    const scout = await fetchJsonViaBg<Array<{ value?: string; name?: string; id?: string }>>(
      "https://api.poe2scout.com/leagues"
    );
    if (Array.isArray(scout)) {
      for (const l of scout) {
        const name = l?.value || l?.name || l?.id;
        if (name) leagues.push({ name, displayName: name, url: "" });
      }
    }
  }

  if (leagues.length === 0) {
    leagues.push({ name: "Standard", displayName: "Standard", url: "standard" });
  }

  leagues.sort((a, b) => {
    const score = (x: LeagueInfo) => {
      const n = x.name.toLowerCase();
      if (n.includes("ssf") || n.includes("hc") || n.includes("hardcore")) return 3;
      if (n === "standard") return 2;
      return 1;
    };
    return score(a) - score(b);
  });

  return leagues;
}

interface RawNinjaLine {
  id?: string;
  name?: string;
  currencyTypeName?: string;
  baseType?: string;
  divineValue?: number;
  exaltedValue?: number;
  primaryValue?: number;
  listingCount?: number;
  count?: number;
  volumePrimaryValue?: number;
}

interface RawNinjaPayload {
  lines?: RawNinjaLine[];
  items?: Array<{ id?: string; name?: string; baseType?: string }> | Record<string, { name?: string; baseType?: string }>;
  core?: { primary?: string; rates?: Record<string, number> };
}

interface RawPriceRecord extends Omit<NinjaPriceEntry, "formatted" | "divineValue" | "exaltedValue"> {
  divineValue: number | null;
  exaltedValue: number | null;
}

function ingestNinjaPayload(
  payload: RawNinjaPayload | null,
  typeName: string,
  byName: Record<string, RawPriceRecord>,
  ratesState: { exaltPerDivine: number | null }
): number {
  if (!payload || !Array.isArray(payload.lines)) return 0;

  const itemMetaById: Record<string, { name?: string; baseType?: string }> = {};
  if (Array.isArray(payload.items)) {
    for (const it of payload.items) {
      if (it?.id) itemMetaById[it.id] = it;
    }
  } else if (payload.items && typeof payload.items === "object") {
    for (const [k, v] of Object.entries(payload.items)) {
      itemMetaById[k] = v;
    }
  }

  const primaryCurrency = String(payload.core?.primary || "").toLowerCase();
  const coreRates = payload.core?.rates || {};
  if (primaryCurrency === "exalted" && coreRates.divine > 0) {
    ratesState.exaltPerDivine = 1 / coreRates.divine;
  } else if (primaryCurrency === "divine" && coreRates.exalted > 0) {
    ratesState.exaltPerDivine = coreRates.exalted;
  } else if (primaryCurrency === "chaos" && coreRates.divine && coreRates.exalted) {
    ratesState.exaltPerDivine = coreRates.exalted / coreRates.divine;
  }

  let added = 0;
  for (const line of payload.lines) {
    const meta = line.id ? itemMetaById[line.id] : undefined;
    const name = line.name || meta?.name || line.currencyTypeName || line.id || "";
    if (!name) continue;

    let divineValue: number | null = typeof line.divineValue === "number" && line.divineValue > 0 ? line.divineValue : null;
    let exaltedValue: number | null = typeof line.exaltedValue === "number" && line.exaltedValue > 0 ? line.exaltedValue : null;

    if (divineValue === null && typeof line.primaryValue === "number" && line.primaryValue > 0) {
      if (primaryCurrency === "divine") {
        divineValue = line.primaryValue;
        if (ratesState.exaltPerDivine) exaltedValue = line.primaryValue * ratesState.exaltPerDivine;
      } else if (primaryCurrency === "exalted") {
        exaltedValue = line.primaryValue;
        if (ratesState.exaltPerDivine) divineValue = line.primaryValue / ratesState.exaltPerDivine;
      } else if (primaryCurrency === "chaos" && coreRates.divine) {
        divineValue = line.primaryValue * coreRates.divine;
        if (coreRates.exalted) exaltedValue = line.primaryValue * coreRates.exalted;
      }
    }

    const k = normPriceKey(name);
    if (k === "divine orb" && exaltedValue && exaltedValue > 1) {
      ratesState.exaltPerDivine = exaltedValue;
    }
    if (divineValue === null && exaltedValue === null) continue;

    const existing = byName[k];
    const listingCount = line.listingCount || line.count || line.volumePrimaryValue || 0;
    if (!existing || listingCount > (existing.listingCount || 0)) {
      byName[k] = {
        name,
        baseType: line.baseType || meta?.baseType || "",
        divineValue,
        exaltedValue,
        listingCount,
        type: typeName,
        source: "poe.ninja",
      };
      added++;
    }
  }
  return added;
}

function formatEntry(
  divineValue: number | null,
  exaltedValue: number | null,
  exaltPerDivine: number | null
): { divineValue: number; exaltedValue: number; label: string } {
  let d = divineValue;
  let e = exaltedValue;
  if (d === null && e !== null && exaltPerDivine) d = e / exaltPerDivine;
  if (e === null && d !== null && exaltPerDivine) e = d * exaltPerDivine;

  if (d !== null && d >= 0.8) {
    const rounded = d >= 10 ? Math.round(d) : Math.round(d * 10) / 10;
    return { divineValue: d, exaltedValue: e || 0, label: `${rounded} D` };
  }
  if (e !== null && e >= 1) {
    const roundedE = Math.round(e);
    const dStr = d !== null ? ` (~${Math.round(d * 100) / 100} D)` : "";
    return { divineValue: d || 0, exaltedValue: e, label: `${roundedE} E${dStr}` };
  }
  if (d !== null && d > 0) {
    return { divineValue: d, exaltedValue: e || 0, label: `${Math.round(d * 100) / 100} D` };
  }
  return { divineValue: 0, exaltedValue: 0, label: "< 1 E" };
}

export async function loadLeaguePrices(leagueName: string, forceRefresh = false): Promise<LeaguePriceIndex> {
  const cacheKey = `poe2_wb_price_cache_v1_${leagueName}`;
  if (!forceRefresh && typeof chrome !== "undefined" && chrome.storage?.local) {
    const cached = await chrome.storage.local.get([cacheKey]);
    const obj = cached[cacheKey] as LeaguePriceIndex | undefined;
    if (obj && obj.updatedAt && Date.now() - obj.updatedAt < CACHE_TTL_MS && obj.itemCount > 0) {
      return obj;
    }
  }

  const encLeague = encodeURIComponent(leagueName);
  const byName: Record<string, RawPriceRecord> = {};
  const ratesState = { exaltPerDivine: null as number | null };

  const currencyTypes = ["Currency", "SoulCores", "Runes", "Omens", "UncutGems", "LineageSupportGems", "Fragments", "Talismans"];
  const stashTypes = [
    "UniqueWeapons",
    "UniqueArmours",
    "UniqueAccessories",
    "UniqueJewels",
    "UniqueFlasks",
    "UniqueCharms",
    "UniqueRelics",
    "SkillGems",
    "LineageSupportGems",
  ];

  const currencyFirst = await fetchJsonViaBg<RawNinjaPayload>(
    `https://poe.ninja/poe2/api/economy/exchange/current/overview?league=${encLeague}&type=Currency`
  );
  ingestNinjaPayload(currencyFirst, "Currency", byName, ratesState);

  const tasks: Promise<unknown>[] = [];
  for (const t of currencyTypes.slice(1)) {
    tasks.push(
      fetchJsonViaBg<RawNinjaPayload>(
        `https://poe.ninja/poe2/api/economy/exchange/current/overview?league=${encLeague}&type=${t}`
      ).then((data) => ingestNinjaPayload(data, t, byName, ratesState))
    );
  }
  for (const t of stashTypes) {
    tasks.push(
      fetchJsonViaBg<RawNinjaPayload>(
        `https://poe.ninja/poe2/api/economy/stash/current/item/overview?league=${encLeague}&type=${t}`
      ).then((data) => ingestNinjaPayload(data, t, byName, ratesState))
    );
  }
  await Promise.all(tasks);

  const finalMap: Record<string, NinjaPriceEntry> = {};
  for (const [k, v] of Object.entries(byName)) {
    const fmt = formatEntry(v.divineValue, v.exaltedValue, ratesState.exaltPerDivine);
    finalMap[k] = {
      name: v.name,
      baseType: v.baseType,
      listingCount: v.listingCount,
      type: v.type,
      source: "poe.ninja",
      divineValue: Math.round(fmt.divineValue * 100) / 100,
      exaltedValue: Math.round(fmt.exaltedValue * 10) / 10,
      formatted: fmt.label,
    };
  }

  const result: LeaguePriceIndex = {
    league: leagueName,
    updatedAt: Date.now(),
    exaltPerDivine: ratesState.exaltPerDivine ? Math.round(ratesState.exaltPerDivine) : null,
    itemCount: Object.keys(finalMap).length,
    byName: finalMap,
  };

  if (result.itemCount > 0 && typeof chrome !== "undefined" && chrome.storage?.local) {
    await chrome.storage.local.set({ [cacheKey]: result }).catch(() => undefined);
  }
  return result;
}

export function lookupNinjaPrice(name: string, priceIndex: LeaguePriceIndex | null): NinjaPriceEntry | null {
  if (!priceIndex?.byName || !name) return null;
  const k = normPriceKey(name);
  if (priceIndex.byName[k]) return priceIndex.byName[k];
  const simplified = k.replace(/\s+(i|ii|iii|iv|v)$/i, "").trim();
  if (simplified && priceIndex.byName[simplified]) return priceIndex.byName[simplified];
  return null;
}

export function matchUniquePricesForBuild(
  build: ParsedBuildData,
  priceIndex: LeaguePriceIndex | null
): AttachedUniquePrice[] {
  if (!priceIndex) return [];
  const out: AttachedUniquePrice[] = [];

  const check = (items: ParsedBuildData["equippedItems"], group: string) => {
    for (const it of items) {
      if (it.rarity.toUpperCase() !== "UNIQUE") continue;
      const hit = lookupNinjaPrice(it.name, priceIndex);
      if (hit) {
        out.push({
          group,
          slot: it.slot,
          name: it.name,
          divineValue: hit.divineValue,
          formatted: hit.formatted,
        });
      }
    }
  };

  check(build.equippedItems, "装备");
  check(build.jewels, "珠宝");
  check(build.flasksAndCharms, "药剂/护符");

  const seenGems = new Set<string>();
  for (const sg of build.skillGroups) {
    for (const gem of sg.gems) {
      const hit = lookupNinjaPrice(gem.name, priceIndex);
      if (hit && hit.divineValue >= 0.05) {
        const gk = normPriceKey(gem.name);
        if (!seenGems.has(gk)) {
          seenGems.add(gk);
          out.push({
            group: "技能/血脉宝石",
            slot: `Gem:${gem.name}`,
            name: gem.name,
            divineValue: hit.divineValue,
            formatted: hit.formatted,
          });
        }
      }
    }
  }

  return out;
}
