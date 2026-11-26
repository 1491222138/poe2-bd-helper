/**
 * PoE2 官方市集（Trade2）词缀映射 + 三档查询构造器（exact 原件 / budget 平替 / semi 半成品底子）
 * + 防 429 熔断串行查价引擎。
 */
import type { ParsedBuildData, ParsedItem, ParsedModOrigin, PriceKind } from "@contracts";
import type { LeaguePriceIndex } from "./ninjaPrice";
import {
  analyzeItemDeeply,
  type AffixDifficulty,
  type AffixRole,
  type AffixSide,
  type ItemDeepAnalysis,
} from "../parsers/affixAnalyzer";
import { scoreModImportance } from "../parsers/localProfile";

const STATS_CACHE_KEY = "poe2_wb_trade_stats_cache_v1";
const STATS_CACHE_TTL_MS = 24 * 60 * 60 * 1000;

interface StatCandidate {
  id: string;
  text: string;
  textNoLocal: string;
  type: string;
  prio: number;
  isLocal: boolean;
  regex: RegExp | null;
  placeholderCount: number;
}

export interface StatsIndex {
  exactMap: Map<string, StatCandidate[]>;
  looseMap: Map<string, StatCandidate[]>;
  skeletonMap: Map<string, StatCandidate[]>;
  loadedAt: number;
}

export interface MatchedModRow {
  index: number;
  raw: string;
  cleaned: string;
  matched: boolean;
  statId: string | null;
  statText: string | null;
  statType: string | null;
  originalValue: number | null;
  min: number | null;
  max: number | null;
  importance: number;
  isPrimary: boolean;
  checked: boolean;
  origin?: ParsedModOrigin;
  side?: AffixSide;
  difficulty?: AffixDifficulty;
  role?: AffixRole;
  synergyTags?: string[];
  excludeFromTradeLock?: boolean;
  mergedCount?: number;
}

export interface TradeListingSample {
  amount: number;
  currency: string;
  seller: string;
  indexed: string;
  itemName: string;
  baseType: string;
  mods: string[];
}

export interface TradeSearchResult {
  ok: boolean;
  status?: number;
  error?: string;
  fallbackUrl?: string;
  queryId?: string;
  total: number;
  webUrl?: string;
  listings: TradeListingSample[];
}

export interface ItemTradeQuote {
  slot: string;
  kind: PriceKind;
  divineValue: number;
  formatted: string;
  totalListings: number;
  modeLabel: string;
  webUrl: string;
  samples: string[];
  queriedAt: number;
}

export interface BatchPriceController {
  paused?: boolean;
  aborted?: boolean;
  kind?: PriceKind;
}

export interface BatchPriceProgress {
  index: number;
  total: number;
  slot: string;
  itemName: string;
  state: "RUNNING" | "PAUSED" | "PRICED" | "EMPTY" | "RATE_LIMITED";
  quote?: ItemTradeQuote;
}

let memoryStatsIndex: StatsIndex | null = null;

function bgTradeGet(url: string): Promise<{ ok: boolean; status?: number; data?: unknown; error?: string }> {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: "TRADE_API_GET", url }, (resp) => {
      if (chrome.runtime.lastError || !resp?.ok) {
        resolve({
          ok: false,
          status: resp?.status,
          error: resp?.error || chrome.runtime.lastError?.message || "请求失败",
        });
        return;
      }
      resolve({ ok: true, data: resp.data });
    });
  });
}

function bgTradePost(url: string, body: unknown): Promise<{ ok: boolean; status?: number; data?: unknown; error?: string }> {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: "TRADE_API_POST", url, body }, (resp) => {
      if (chrome.runtime.lastError || !resp?.ok) {
        resolve({
          ok: false,
          status: resp?.status,
          error: resp?.error || chrome.runtime.lastError?.message || "请求失败",
        });
        return;
      }
      resolve({ ok: true, data: resp.data });
    });
  });
}

export function cleanRichText(str: string): string {
  return String(str || "")
    .replace(/\[([^|\]]+)\|([^\]]+)\]/g, "$2")
    .replace(/\[([^\]]+)\]/g, "$1")
    .trim();
}

export function normalizeTemplate(text: string, stripPlus = false): string {
  let s = cleanRichText(text).replace(/\s+/g, " ").trim().toLowerCase();
  if (stripPlus) s = s.replace(/\+#/g, "#");
  return s;
}

export function toNumberSkeleton(text: string): string {
  return cleanRichText(text)
    .replace(/\s*\(local\)\s*/gi, " ")
    .replace(/[+-]?\d+(?:\.\d+)?/g, "#")
    .replace(/\+#/g, "#")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

export function buildStatRegex(statTextNoLocal: string): { regex: RegExp | null; placeholderCount: number } {
  const clean = cleanRichText(statTextNoLocal).replace(/\s+/g, " ").trim();
  let placeholderCount = 0;
  const tokenized = clean.replace(/\+?#|\(\+?#\)/g, () => {
    placeholderCount++;
    return "__POE2_NUM_PLACEHOLDER__";
  });
  const escaped = tokenized.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = "^" + escaped.replace(/__POE2_NUM_PLACEHOLDER__/g, "\\+?([+-]?\\d+(?:\\.\\d+)?)") + "$";
  try {
    return { regex: new RegExp(pattern, "i"), placeholderCount };
  } catch {
    return { regex: null, placeholderCount: 0 };
  }
}

export function parseModLine(modLine: string): {
  raw: string;
  cleaned: string;
  template: string;
  skeleton: string;
  nums: number[];
  numericValue: number | null;
} {
  const cleaned = cleanRichText(modLine)
    .replace(/\s*\((implicit|crafted|fractured|rune|desecrated|mutated)\)\s*$/gi, "")
    .trim();

  const nums: number[] = [];
  const template = cleaned.replace(/[+-]?\d+(?:\.\d+)?/g, (match) => {
    const val = parseFloat(match);
    nums.push(val);
    return match.startsWith("+") ? "+#" : "#";
  });

  let numericValue: number | null = null;
  if (nums.length === 1) numericValue = nums[0];
  else if (nums.length >= 2) numericValue = Math.round(((nums[0] + nums[1]) / 2) * 10) / 10;

  return { raw: modLine, cleaned, template, skeleton: toNumberSkeleton(cleaned), nums, numericValue };
}

export function buildStatsIndex(
  rawResult: Array<{ id?: string; entries?: Array<{ id?: string; text?: string }> }>
): StatsIndex {
  const exactMap = new Map<string, StatCandidate[]>();
  const looseMap = new Map<string, StatCandidate[]>();
  const skeletonMap = new Map<string, StatCandidate[]>();
  const priorityOrder: Record<string, number> = {
    explicit: 1,
    desecrated: 2,
    implicit: 3,
    enchant: 4,
    fractured: 5,
    rune: 6,
    pseudo: 7,
  };

  const add = (map: Map<string, StatCandidate[]>, key: string, item: StatCandidate) => {
    const arr = map.get(key);
    if (arr) arr.push(item);
    else map.set(key, [item]);
  };

  for (const group of rawResult || []) {
    const groupId = String(group.id || "").toLowerCase();
    const prio = priorityOrder[groupId] || 8;
    for (const entry of group.entries || []) {
      if (!entry?.id || !entry?.text) continue;
      const text = entry.text;
      const isLocal = /\(local\)/i.test(text);
      const textNoLocal = text.replace(/\s*\(local\)\s*/gi, " ").replace(/\s+/g, " ").trim();
      const { regex, placeholderCount } = buildStatRegex(textNoLocal);
      const item: StatCandidate = {
        id: entry.id,
        text,
        textNoLocal,
        type: groupId,
        prio,
        isLocal,
        regex,
        placeholderCount,
      };
      add(exactMap, normalizeTemplate(text, false), item);
      add(looseMap, normalizeTemplate(text, true), item);
      if (isLocal) {
        add(exactMap, normalizeTemplate(textNoLocal, false), item);
        add(looseMap, normalizeTemplate(textNoLocal, true), item);
      }
      add(skeletonMap, toNumberSkeleton(textNoLocal), item);
    }
  }
  return { exactMap, looseMap, skeletonMap, loadedAt: Date.now() };
}

export async function ensureStatsIndex(): Promise<StatsIndex> {
  if (memoryStatsIndex) return memoryStatsIndex;

  if (typeof chrome !== "undefined" && chrome.storage?.local) {
    const cached = await chrome.storage.local.get([STATS_CACHE_KEY]);
    const obj = cached[STATS_CACHE_KEY] as { loadedAt?: number; result?: Array<{ id?: string; entries?: Array<{ id?: string; text?: string }> }> } | undefined;
    if (obj?.loadedAt && Date.now() - obj.loadedAt < STATS_CACHE_TTL_MS && Array.isArray(obj.result)) {
      memoryStatsIndex = buildStatsIndex(obj.result);
      return memoryStatsIndex;
    }
  }

  const resp = await bgTradeGet("https://www.pathofexile.com/api/trade2/data/stats");
  const raw = (resp.data as { result?: Array<{ id?: string; entries?: Array<{ id?: string; text?: string }> }> } | undefined)?.result;
  if (!resp.ok || !Array.isArray(raw)) {
    throw new Error(`无法拉取官方 PoE2 词缀库（${resp.error || "网络错误"}）。请确认能访问 pathofexile.com。`);
  }

  const compact = raw.map((g) => ({
    id: g.id,
    entries: (g.entries || []).map((e) => ({ id: e.id, text: e.text })),
  }));
  if (typeof chrome !== "undefined" && chrome.storage?.local) {
    await chrome.storage.local.set({ [STATS_CACHE_KEY]: { loadedAt: Date.now(), result: compact } }).catch(() => undefined);
  }

  memoryStatsIndex = buildStatsIndex(compact);
  return memoryStatsIndex;
}

export function calcRolledMin(val: number | null, tolerancePct: number): number | null {
  if (val === null || Number.isNaN(val)) return null;
  if (val === 0) return 0;
  if (val < 0) return null;
  const factor = 1 - tolerancePct / 100;
  const scaled = val * factor;
  if (Math.abs(val) <= 5 && Number.isInteger(val)) {
    return tolerancePct === 0 ? val : Math.max(1, Math.floor(scaled));
  }
  return val >= 10 ? Math.floor(scaled) : Math.round(scaled * 10) / 10;
}

function isLocalGearSlot(slot: string): boolean {
  const s = (slot || "").toLowerCase();
  return /weapon|helmet|body|gloves|boots|shield|focus/.test(s);
}

function pickBestStatCandidate(candidates: StatCandidate[] | undefined, preferLocal: boolean): StatCandidate | null {
  if (!candidates || candidates.length === 0) return null;
  return [...candidates].sort((a, b) => {
    if (a.isLocal !== b.isLocal) return preferLocal ? (a.isLocal ? -1 : 1) : a.isLocal ? 1 : -1;
    return a.prio - b.prio;
  })[0];
}

function isLocalDefenseRowFor(row: MatchedModRow, stat: "es" | "ev" | "ar" | "ward"): boolean {
  const t = (row.cleaned || "").toLowerCase();
  if (/recharge|regeneration|delay|deflect/.test(t)) return false;
  if (stat === "es") return /maximum energy shield|increased (?:armour and |evasion and )?energy shield\b/.test(t);
  if (stat === "ev") return /to evasion rating|increased (?:armour and )?evasion/.test(t);
  if (stat === "ar") return /to armour|increased armour/.test(t);
  if (stat === "ward") return /\bward\b/.test(t);
  return false;
}

export function applyPriceKindPreset(
  modRows: MatchedModRow[],
  kind: PriceKind,
  deepAnalysis?: ItemDeepAnalysis | null
): MatchedModRow[] {
  const toleranceByKind: Record<PriceKind, number> = {
    exact: 10,
    budget: 25,
    semi: 15,
    base: 0,
  };
  const tol = toleranceByKind[kind];
  const eligible = modRows.filter((r) => r.matched && !r.excludeFromTradeLock && r.role !== "dead");
  const matched = eligible.length > 0 ? eligible : modRows.filter((r) => r.matched && !r.excludeFromTradeLock);

  const selectedIds = new Set<number>();
  if (kind === "base") {
    // 白底底材不附加任何显性词缀筛选
  } else if (kind === "semi") {
    // 半成品底子：优先锁破裂词条与最难洗的 1~2 条核心主词条，留空前后缀供加工
    const diffRank = (d?: AffixDifficulty) => (d === "hard" ? 2 : d === "medium" ? 1 : 0);
    const semiRanked = [...matched].sort((a, b) => {
      const fA = a.origin === "fractured" ? 1 : 0;
      const fB = b.origin === "fractured" ? 1 : 0;
      if (fA !== fB) return fB - fA;
      const dDiff = diffRank(b.difficulty) - diffRank(a.difficulty);
      if (dDiff !== 0) return dDiff;
      return b.importance - a.importance;
    });
    semiRanked.slice(0, Math.min(2, semiRanked.length)).forEach((r) => selectedIds.add(r.index));
  } else if (kind === "budget") {
    // 平替：锁 2~3 条非死词条核心/联动主词条 + 宽松下限 (-25%)；
    // 若已启用总防值门槛（如 Subterfuge Mask 头盔 ES / Spectral Ward 胸甲闪避），避免重复锁细碎防具前后缀
    const defStat = deepAnalysis?.defenseFloor?.stat;
    const pool =
      defStat && defStat !== "ward"
        ? matched.filter((r) => !isLocalDefenseRowFor(r, defStat))
        : matched;
    const activePool = pool.length > 0 ? pool : matched;
    const primaries = activePool.filter((r) => r.isPrimary);
    const list = primaries.length >= 2 ? primaries.slice(0, 3) : activePool.slice(0, 3);
    list.forEach((r) => selectedIds.add(r.index));
  } else {
    // 原件：锁前 4 条高价值词缀 + 严格下限 (-10%)
    const primaries = matched.filter((r) => r.isPrimary);
    const list = primaries.length >= 3 ? primaries.slice(0, 4) : matched.slice(0, 4);
    list.forEach((r) => selectedIds.add(r.index));
  }

  return modRows.map((r) => ({
    ...r,
    checked: selectedIds.has(r.index),
    min: r.matched && r.originalValue !== null ? calcRolledMin(r.originalValue, tol) : r.min,
  }));
}

export function matchItemModsWithIndex(
  item: ParsedItem,
  index: StatsIndex,
  kind: PriceKind = "exact",
  build?: ParsedBuildData | null
): MatchedModRow[] {
  const preferLocal = isLocalGearSlot(item.slot);
  const deep = analyzeItemDeeply(item, build);
  const analyzedByClean = new Map<string, typeof deep.affixes>();
  for (const a of deep.affixes) {
    const key = a.cleaned.toLowerCase();
    const list = analyzedByClean.get(key);
    if (list) list.push(a);
    else analyzedByClean.set(key, [a]);
  }
  const usedAnalyzed = new Map<string, number>();

  const nativeMods = (item.mods || []).filter((m) => !/\(rune\)|\{rune\}/i.test(m));

  const rawRows: MatchedModRow[] = nativeMods.map((modLine, idx) => {
    const parsed = parseModLine(modLine);
    let match =
      pickBestStatCandidate(index.exactMap.get(normalizeTemplate(parsed.template, false)), preferLocal) ||
      pickBestStatCandidate(index.looseMap.get(normalizeTemplate(parsed.template, true)), preferLocal);

    let extractedVal = parsed.numericValue;
    if (!match) {
      const skel = index.skeletonMap.get(parsed.skeleton) || [];
      const regexMatched = skel.filter((c) => c.regex?.test(parsed.cleaned));
      match = pickBestStatCandidate(regexMatched.length ? regexMatched : skel, preferLocal);
    }

    if (match?.regex) {
      const m = parsed.cleaned.match(match.regex);
      if (m) {
        const capNums = m
          .slice(1)
          .map((v) => parseFloat(v))
          .filter((v) => Number.isFinite(v));
        if (capNums.length === 0) extractedVal = null;
        else if (capNums.length === 1) extractedVal = capNums[0];
        else extractedVal = Math.round(((capNums[0] + capNums[1]) / 2) * 10) / 10;
      }
    }

    // PoE2 新版 Trade2 API 把 rune 归类为 augment，两者均不应作为原生显性词缀匹配
    if (match && (match.type === "rune" || match.type === "augment")) match = null;

    const cleanKey = parsed.cleaned.toLowerCase();
    const cands = analyzedByClean.get(cleanKey);
    const cursor = usedAnalyzed.get(cleanKey) || 0;
    const analyzed = cands && cands.length > 0 ? cands[Math.min(cursor, cands.length - 1)] : undefined;
    usedAnalyzed.set(cleanKey, cursor + 1);

    const importance = analyzed ? analyzed.contextualScore : scoreModImportance(parsed.cleaned, item.slot);
    return {
      index: idx,
      raw: modLine,
      cleaned: parsed.cleaned,
      matched: Boolean(match),
      statId: match?.id || null,
      statText: match?.text || null,
      statType: match?.type || null,
      originalValue: extractedVal,
      min: calcRolledMin(extractedVal, 15),
      max: null,
      importance,
      isPrimary: importance >= 55 && !analyzed?.excludeFromTradeLock && analyzed?.role !== "dead",
      checked: false,
      origin: analyzed?.origin,
      side: analyzed?.side,
      difficulty: analyzed?.difficulty,
      role: analyzed?.role,
      synergyTags: analyzed?.synergyTags,
      excludeFromTradeLock: analyzed?.excludeFromTradeLock,
      mergedCount: 1,
    };
  });

  // 合并同 statId 的重复/复合拆行词缀（如 36% + 10% Attack Speed、+32% + +31% Fire Res），防止 Trade2 出现重复筛选失效
  const mergedRows: MatchedModRow[] = [];
  const byStatId = new Map<string, MatchedModRow>();
  for (const row of rawRows) {
    if (row.matched && row.statId && !row.excludeFromTradeLock) {
      const existing = byStatId.get(row.statId);
      if (existing) {
        if (existing.originalValue !== null && row.originalValue !== null) {
          const sumVal = Math.round((existing.originalValue + row.originalValue) * 10) / 10;
          existing.originalValue = sumVal;
          existing.mergedCount = (existing.mergedCount || 1) + 1;
          existing.cleaned = `${existing.cleaned} (+${row.originalValue} 合并=${sumVal})`;
        }
        existing.importance = Math.max(existing.importance, row.importance);
        existing.isPrimary = existing.isPrimary || row.isPrimary;
        if (row.synergyTags?.length) {
          existing.synergyTags = Array.from(new Set([...(existing.synergyTags || []), ...row.synergyTags]));
        }
        continue;
      }
      byStatId.set(row.statId, row);
    }
    mergedRows.push(row);
  }

  mergedRows.sort((a, b) => {
    if (a.matched !== b.matched) return a.matched ? -1 : 1;
    const exA = a.excludeFromTradeLock ? 1 : 0;
    const exB = b.excludeFromTradeLock ? 1 : 0;
    if (exA !== exB) return exA - exB;
    return b.importance - a.importance;
  });

  return applyPriceKindPreset(mergedRows, kind, deep);
}

export async function matchItemMods(
  item: ParsedItem,
  kind: PriceKind = "exact",
  build?: ParsedBuildData | null
): Promise<MatchedModRow[]> {
  const index = await ensureStatsIndex();
  return matchItemModsWithIndex(item, index, kind, build);
}

export function inferTradeCategory(item: ParsedItem): string | null {
  const slot = (item.slot || "").toLowerCase();
  const base = (item.baseType || "").toLowerCase();

  if (slot.includes("helmet")) return "armour.helmet";
  if (slot.includes("body")) return "armour.chest";
  if (slot.includes("gloves")) return "armour.gloves";
  if (slot.includes("boots")) return "armour.boots";
  if (slot.includes("amulet")) return "accessory.amulet";
  if (slot.includes("ring")) return "accessory.ring";
  if (slot.includes("belt")) return "accessory.belt";
  if (slot.includes("quiver")) return "armour.quiver";
  if (slot.includes("shield")) return "armour.shield";
  if (slot.includes("focus")) return "armour.focus";
  if (slot.includes("jewel") || /jewel|emerald|diamond|ruby|sapphire/.test(base)) return "jewel";
  if (base.includes("spear")) return "weapon.spear";
  if (base.includes("crossbow")) return "weapon.crossbow";
  if (base.includes("bow")) return "weapon.bow";
  if (base.includes("quarterstaff")) return "weapon.warstaff";
  if (base.includes("staff")) return "weapon.staff";
  if (base.includes("wand")) return "weapon.wand";
  if (base.includes("sceptre")) return "weapon.sceptre";
  return null;
}

export function buildTradeQuery(
  item: ParsedItem,
  modRows: MatchedModRow[],
  options: {
    kind?: PriceKind;
    matchBase?: boolean;
    onlineOnly?: boolean;
    filterSockets?: boolean;
    minSockets?: number;
    minIlvl?: number;
    build?: ParsedBuildData | null;
    deepAnalysis?: ItemDeepAnalysis | null;
  } = {}
): Record<string, unknown> {
  const isUnique = item.rarity.toUpperCase() === "UNIQUE";
  const kind = options.kind || "exact";
  const deep = options.deepAnalysis ?? analyzeItemDeeply(item, options.build);

  // 特殊刚需基底（Ward 底子、潜能戒指 Unset Ring、精魂项链 Solar Amulet、裂隙戒指等）即使平替也保留基底
  const matchBase = options.matchBase ?? (kind !== "budget" || deep.requireBaseType);
  const onlineOnly = options.onlineOnly ?? true;

  // 智能符文孔位筛选：
  // - exact 模式保持 >=2 孔筛选；
  // - budget / semi / base 模式仅当孔数超过工匠石上限（如单手2孔/头手鞋2孔/胸甲3孔）或含「镶嵌物效果增幅」时强制锁孔位，
  //   避免工匠石可轻松打满的普通孔位误过滤优质半成品/白底！
  const autoNeedSockets =
    !isUnique &&
    item.socketCount >= 2 &&
    (kind === "exact" ||
      (kind === "budget" && deep.socketAnalysis.requireSocketsInBudget) ||
      ((kind === "semi" || kind === "base") && deep.socketAnalysis.requireSocketsInSemi));
  const filterSockets = options.filterSockets ?? autoNeedSockets;
  const minSockets = options.minSockets ?? item.socketCount;

  const statFilters =
    kind === "base"
      ? []
      : modRows
          .filter((r) => r.checked && r.statId)
          .map((r) => {
            const value: Record<string, number> = {};
            if (r.min !== null && !Number.isNaN(Number(r.min))) value.min = Number(r.min);
            if (r.max !== null && !Number.isNaN(Number(r.max))) value.max = Number(r.max);
            const f: Record<string, unknown> = { id: r.statId, disabled: false };
            if (Object.keys(value).length > 0) f.value = value;
            return f;
          });

  const filters: Record<string, unknown> = {};
  const query: Record<string, unknown> = {
    status: { option: onlineOnly ? "available" : "any" },
    ...(statFilters.length > 0 || kind !== "base" ? { stats: [{ type: "and", filters: statFilters }] } : {}),
  };

  if (isUnique && item.name && kind !== "base") {
    query.name = item.name;
    if (matchBase && item.baseType && item.baseType !== item.name) {
      query.type = item.baseType;
    }
  } else if (matchBase && item.baseType) {
    query.type = item.baseType;
  } else {
    const cat = inferTradeCategory(item);
    if (cat) {
      filters.type_filters = { filters: { category: { option: cat } } };
    }
  }

  const targetIlvl = options.minIlvl ?? Math.max(78, Math.min(84, item.itemLevel || item.levelReq || 82));

  // 白底模式：锁定普通白装 + 非腐化 + 最低物等
  if (kind === "base" && !isUnique) {
    const typeFilters = ((filters.type_filters as { filters?: Record<string, unknown> } | undefined)?.filters ||
      {}) as Record<string, unknown>;
    typeFilters.rarity = { option: "normal" };
    typeFilters.ilvl = { min: targetIlvl };
    filters.type_filters = { filters: typeFilters };
    filters.misc_filters = {
      filters: {
        corrupted: { option: "false" },
      },
    };
  }

  // 半成品底子模式强制非腐化，保证可继续补崇高/精华/预兆加工
  if (kind === "semi" && !isUnique) {
    filters.misc_filters = {
      filters: {
        corrupted: { option: "false" },
      },
    };
  }

  const eqFilters: Record<string, unknown> = {};
  if (filterSockets && minSockets >= 1) {
    eqFilters.total_augment_sockets = { min: minSockets };
  }
  // 配合天赋防御转换（如 Subterfuge Mask 头盔 ES / Spectral Ward 胸甲闪避）在平替/原件中加总防值下限
  if (!isUnique && (kind === "budget" || kind === "exact") && deep.defenseFloor) {
    const minVal = kind === "exact" ? deep.defenseFloor.exactMin : deep.defenseFloor.budgetMin;
    if (minVal > 0) {
      eqFilters[deep.defenseFloor.stat] = { min: minVal };
    }
  }
  if (Object.keys(eqFilters).length > 0) {
    filters.equipment_filters = { filters: eqFilters };
  }

  if (Object.keys(filters).length > 0) {
    query.filters = filters;
  }

  return { query, sort: { price: "asc" } };
}

export function buildFallbackTradeUrl(league: string, payload: Record<string, unknown>): string {
  const encLeague = encodeURIComponent(league || "Standard");
  return `https://www.pathofexile.com/trade2/search/poe2/${encLeague}?q=${encodeURIComponent(JSON.stringify(payload))}`;
}

export async function executeTradeSearch(
  league: string,
  payload: Record<string, unknown>
): Promise<TradeSearchResult> {
  const encLeague = encodeURIComponent(league || "Standard");
  const searchUrl = `https://www.pathofexile.com/api/trade2/search/poe2/${encLeague}`;
  let resp = await bgTradePost(searchUrl, payload);

  const q = (payload.query || {}) as Record<string, unknown>;
  const statusOpt = (q.status as { option?: string } | undefined)?.option;

  // 容错 1：available / securable 若在部分旧区服报错时降级 online
  if (!resp.ok && resp.status !== 429 && (statusOpt === "securable" || statusOpt === "available")) {
    const retry = JSON.parse(JSON.stringify(payload));
    retry.query.status.option = "online";
    resp = await bgTradePost(searchUrl, retry);
  }

  // 容错 2：部分类别不支持 total_augment_sockets / equipment_filters 筛选时自动剥离重试
  const hasSockets = Boolean(
    ((q.filters as Record<string, unknown> | undefined)?.equipment_filters as Record<string, unknown> | undefined)
  );
  if (!resp.ok && resp.status !== 429 && hasSockets) {
    const retry = JSON.parse(JSON.stringify(payload));
    delete retry.query.filters.equipment_filters;
    if (Object.keys(retry.query.filters).length === 0) delete retry.query.filters;
    if (retry.query.status?.option === "securable") retry.query.status.option = "available";
    resp = await bgTradePost(searchUrl, retry);
  }

  const data = resp.data as { id?: string; total?: number; result?: string[] } | undefined;
  if (!resp.ok || !data?.id) {
    return {
      ok: false,
      status: resp.status,
      error: resp.error || "官方市集搜索失败",
      fallbackUrl: buildFallbackTradeUrl(league, payload),
      total: 0,
      listings: [],
    };
  }

  const queryId = data.id;
  const total = data.total || 0;
  const ids = (data.result || []).slice(0, 6);
  const webUrl = `https://www.pathofexile.com/trade2/search/poe2/${encLeague}/${queryId}`;
  let listings: TradeListingSample[] = [];

  if (ids.length > 0) {
    const fetchUrl = `https://www.pathofexile.com/api/trade2/fetch/${ids.join(",")}?query=${encodeURIComponent(queryId)}&realm=poe2`;
    const fetchResp = await bgTradeGet(fetchUrl);
    const fetchResult = (fetchResp.data as { result?: Array<Record<string, unknown>> } | undefined)?.result;
    if (fetchResp.ok && Array.isArray(fetchResult)) {
      listings = fetchResult
        .map((entry) => {
          const listing = (entry.listing || {}) as Record<string, unknown>;
          const p = (listing.price || {}) as { amount?: number; currency?: string };
          const account = (listing.account || {}) as { name?: string };
          const it = (entry.item || {}) as Record<string, unknown>;
          const mods = [
            ...((it.implicitMods as string[]) || []),
            ...((it.fracturedMods as string[]) || []),
            ...((it.desecratedMods as string[]) || []),
            ...((it.explicitMods as string[]) || []),
            ...((it.craftedMods as string[]) || []),
          ].map(cleanRichText);

          return {
            amount: Number(p.amount),
            currency: String(p.currency || ""),
            seller: String(account.name || ""),
            indexed: String(listing.indexed || ""),
            itemName: cleanRichText(String(it.name || it.typeLine || "")),
            baseType: cleanRichText(String(it.baseType || it.typeLine || "")),
            mods: mods.slice(0, 10),
          };
        })
        .filter((x) => Number.isFinite(x.amount) && x.amount > 0);
    }
  }

  return { ok: true, queryId, total, webUrl, listings };
}

const CURRENCY_ID_TO_NAME: Record<string, string> = {
  divine: "divine orb",
  exalted: "exalted orb",
  chaos: "chaos orb",
  mirror: "mirror of kalandra",
  annul: "orb of annulment",
  alch: "orb of alchemy",
  regal: "regal orb",
  vaal: "vaal orb",
  chance: "orb of chance",
};

export function convertListingToDivine(
  amount: number,
  currency: string,
  priceIndex: LeaguePriceIndex | null
): number | null {
  if (!Number.isFinite(amount) || amount <= 0) return null;
  const cur = String(currency || "").toLowerCase().trim();
  if (cur === "divine" || cur === "div") return amount;

  const exaltPerDiv = priceIndex?.exaltPerDivine || 100;
  if (cur === "exalted" || cur === "exalt" || cur === "ex") {
    return amount / exaltPerDiv;
  }
  const fullName = CURRENCY_ID_TO_NAME[cur];
  if (fullName && priceIndex?.byName?.[fullName]?.divineValue) {
    return amount * priceIndex.byName[fullName].divineValue;
  }
  if (cur === "mirror") return amount * 500;
  if (cur === "chaos") return amount / Math.max(10, exaltPerDiv * 0.4);
  return null;
}

export function formatDivineLabel(d: number | null | undefined): string {
  if (d === null || d === undefined || Number.isNaN(d)) return "未知";
  if (d >= 10) return `${Math.round(d)} D`;
  if (d >= 1) return `${Math.round(d * 10) / 10} D`;
  if (d >= 0.05) return `${Math.round(d * 100) / 100} D`;
  return "< 0.05 D";
}

/** 稳健参考价：>= 3 条时跳过首条常见压价钓鱼单，取第 2~5 条中位数 */
export function computeRobustDivinePrice(
  listings: Array<{ amount: number; currency: string }>,
  priceIndex: LeaguePriceIndex | null
): number | null {
  const vals = (listings || [])
    .map((l) => convertListingToDivine(l.amount, l.currency, priceIndex))
    .filter((v): v is number => v !== null && v > 0)
    .sort((a, b) => a - b);

  if (vals.length === 0) return null;
  const sample = vals.length >= 3 ? vals.slice(1, Math.min(5, vals.length)) : vals;
  const mid = Math.floor(sample.length / 2);
  const median = sample.length % 2 === 1 ? sample[mid] : (sample[mid - 1] + sample[mid]) / 2;
  return Math.round(median * 100) / 100;
}

const KIND_LABELS: Record<PriceKind, string> = {
  exact: "原件 (-10%)",
  budget: "平替 (-25%)",
  semi: "半成品底子 (非腐化)",
  base: "白底底材 (非腐化)",
};

export async function buildDirectTradeUrlForItem(
  item: ParsedItem,
  league: string,
  kind: PriceKind = "budget",
  options: { minIlvl?: number; build?: ParsedBuildData | null } = {}
): Promise<string> {
  const isUnique = item.rarity.toUpperCase() === "UNIQUE";
  const deep = analyzeItemDeeply(item, options.build);
  if (isUnique || kind === "base") {
    const payload = buildTradeQuery(item, [], {
      kind,
      minIlvl: options.minIlvl,
      build: options.build,
      deepAnalysis: deep,
    });
    return buildFallbackTradeUrl(league, payload);
  }
  try {
    const rows = await matchItemMods(item, kind, options.build);
    const payload = buildTradeQuery(item, rows, {
      kind,
      minIlvl: options.minIlvl,
      build: options.build,
      deepAnalysis: deep,
    });
    return buildFallbackTradeUrl(league, payload);
  } catch {
    // 词缀索引拉取失败时退化为基底/大类直达
    const payload = buildTradeQuery(item, [], {
      kind,
      minIlvl: options.minIlvl,
      build: options.build,
      deepAnalysis: deep,
    });
    return buildFallbackTradeUrl(league, payload);
  }
}

export interface ThreeWayMarketResult {
  finishedQuote?: ItemTradeQuote;
  finishedKind: "exact" | "budget";
  semiQuote?: ItemTradeQuote;
  baseQuote?: ItemTradeQuote;
  finishedUrl: string;
  semiUrl: string;
  baseUrl: string;
  wantAffixes: string[];
  existingAffixes: string[];
  deepAnalysis?: ItemDeepAnalysis;
  rateLimited: boolean;
  error?: string;
}

/**
 * 为单件稀有装备自动执行「成品/平替 + 半成品底子 + 白底底材」三档官方市集实价查询（含 1.3s 防 429 节流）。
 */
export async function priceItemThreeWay(
  item: ParsedItem,
  league: string,
  priceIndex: LeaguePriceIndex | null,
  onProgress?: (msg: string) => void,
  build?: ParsedBuildData | null
): Promise<ThreeWayMarketResult> {
  await ensureStatsIndex();
  const deep = analyzeItemDeeply(item, build);
  const exactRows = await matchItemMods(item, "exact", build);
  const budgetRows = applyPriceKindPreset(exactRows, "budget", deep);
  const semiRows = applyPriceKindPreset(exactRows, "semi", deep);

  const stripMergeHint = (s: string) => s.replace(/\s*\(\+[\d.]+\s*合并=[\d.]+\)\s*$/, "").trim();
  const wantAffixes = exactRows
    .filter((r) => r.checked && !r.excludeFromTradeLock && r.role !== "dead")
    .map((r) => stripMergeHint(r.cleaned))
    .slice(0, 4);
  const existingAffixes = semiRows
    .filter((r) => r.checked && !r.excludeFromTradeLock && r.role !== "dead")
    .map((r) => stripMergeHint(r.cleaned))
    .slice(0, 2);

  const targetIlvl = Math.max(78, Math.min(84, item.itemLevel || item.levelReq || 82));
  let finishedUrl = buildFallbackTradeUrl(
    league,
    buildTradeQuery(item, budgetRows, { kind: "budget", build, deepAnalysis: deep })
  );
  let semiUrl = buildFallbackTradeUrl(
    league,
    buildTradeQuery(item, semiRows, { kind: "semi", build, deepAnalysis: deep })
  );
  let baseUrl = buildFallbackTradeUrl(
    league,
    buildTradeQuery(item, [], { kind: "base", minIlvl: targetIlvl, build, deepAnalysis: deep })
  );

  let finishedQuote: ItemTradeQuote | undefined;
  let finishedKind: "exact" | "budget" = "budget";
  let semiQuote: ItemTradeQuote | undefined;
  let baseQuote: ItemTradeQuote | undefined;

  // 1. 先查成品（先查平替/核心 3 词，更接近真实成交价；若为空再放宽）
  onProgress?.("1/3 正在查询市集成品/平替挂单价...");
  const finRes = await priceSingleItem(item, league, "budget", priceIndex, budgetRows, build);
  if (finRes.rateLimited) {
    return {
      finishedKind,
      finishedUrl,
      semiUrl,
      baseUrl,
      wantAffixes,
      existingAffixes,
      deepAnalysis: deep,
      rateLimited: true,
      error: "触发官方市集 429 限流，已立即熔断保护，请稍后重试。",
    };
  }
  if (finRes.webUrl) finishedUrl = finRes.webUrl;
  if (finRes.quote) finishedQuote = finRes.quote;

  await sleep(1250);

  // 2. 查半成品底子（仅锁 1~2 条最难核心词缀 + 非腐化 + 按需锁超工匠上限孔位）
  onProgress?.("2/3 正在查询半成品底子挂单价...");
  const semiRes = await priceSingleItem(item, league, "semi", priceIndex, semiRows, build);
  if (semiRes.rateLimited) {
    return {
      finishedQuote,
      finishedKind,
      finishedUrl,
      semiUrl,
      baseUrl,
      wantAffixes,
      existingAffixes,
      deepAnalysis: deep,
      rateLimited: true,
      error: "查半成品底子时触发 429 限流，已熔断停止。",
    };
  }
  if (semiRes.webUrl) semiUrl = semiRes.webUrl;
  if (semiRes.quote) semiQuote = semiRes.quote;

  await sleep(1250);

  // 3. 查高物等白底底材价
  onProgress?.("3/3 正在查询高物等白底底材价...");
  const baseRes = await priceSingleItem(item, league, "base", priceIndex, [], build);
  if (baseRes.webUrl) baseUrl = baseRes.webUrl;
  if (baseRes.quote) baseQuote = baseRes.quote;

  return {
    finishedQuote,
    finishedKind,
    semiQuote,
    baseQuote,
    finishedUrl,
    semiUrl,
    baseUrl,
    wantAffixes,
    existingAffixes,
    deepAnalysis: deep,
    rateLimited: Boolean(baseRes.rateLimited),
  };
}

export async function priceSingleItem(
  item: ParsedItem,
  league: string,
  kind: PriceKind,
  priceIndex: LeaguePriceIndex | null,
  customRows?: MatchedModRow[],
  build?: ParsedBuildData | null
): Promise<{ ok: boolean; rateLimited?: boolean; quote?: ItemTradeQuote; webUrl?: string; total?: number; error?: string }> {
  const deep = analyzeItemDeeply(item, build);
  const rows = customRows || (await matchItemMods(item, kind, build));
  const payload = buildTradeQuery(item, rows, { kind, build, deepAnalysis: deep });
  const res = await executeTradeSearch(league, payload);

  if (!res.ok) {
    const rateLimited = res.status === 429 || String(res.error || "").includes("429");
    return { ok: false, rateLimited, webUrl: res.fallbackUrl, error: res.error };
  }

  if (res.total === 0 || res.listings.length === 0) {
    return { ok: true, total: 0, webUrl: res.webUrl };
  }

  const medianD = computeRobustDivinePrice(res.listings, priceIndex);
  if (medianD === null) {
    return { ok: true, total: res.total, webUrl: res.webUrl };
  }

  const quote: ItemTradeQuote = {
    slot: item.slot,
    kind,
    divineValue: medianD,
    formatted: formatDivineLabel(medianD),
    totalListings: res.total,
    modeLabel: KIND_LABELS[kind],
    webUrl: res.webUrl || buildFallbackTradeUrl(league, payload),
    samples: res.listings.slice(0, 4).map((l) => `${l.amount} ${l.currency}`),
    queriedAt: Date.now(),
  };
  return { ok: true, total: res.total, webUrl: quote.webUrl, quote };
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export async function batchPriceItems(
  items: ParsedItem[],
  league: string,
  priceIndex: LeaguePriceIndex | null,
  controller: BatchPriceController,
  onProgress?: (p: BatchPriceProgress) => void,
  build?: ParsedBuildData | null
): Promise<{ quotes: ItemTradeQuote[]; rateLimited: boolean }> {
  const kind = controller.kind || "budget";
  const queue = items.slice(0, 20);
  const quotes: ItemTradeQuote[] = [];
  let rateLimited = false;

  if (queue.length === 0) return { quotes, rateLimited };
  await ensureStatsIndex();

  const waitIfPaused = async (idx: number, it: ParsedItem) => {
    while (controller.paused && !controller.aborted) {
      onProgress?.({ index: idx + 1, total: queue.length, slot: it.slot, itemName: it.name, state: "PAUSED" });
      await sleep(250);
    }
  };

  for (let i = 0; i < queue.length; i++) {
    const item = queue[i];
    await waitIfPaused(i, item);
    if (controller.aborted) break;

    onProgress?.({ index: i + 1, total: queue.length, slot: item.slot, itemName: item.name, state: "RUNNING" });
    const res = await priceSingleItem(item, league, kind, priceIndex, undefined, build);

    if (res.rateLimited) {
      rateLimited = true;
      onProgress?.({ index: i + 1, total: queue.length, slot: item.slot, itemName: item.name, state: "RATE_LIMITED" });
      break;
    }

    if (res.quote) {
      quotes.push(res.quote);
      onProgress?.({
        index: i + 1,
        total: queue.length,
        slot: item.slot,
        itemName: item.name,
        state: "PRICED",
        quote: res.quote,
      });
    } else {
      onProgress?.({ index: i + 1, total: queue.length, slot: item.slot, itemName: item.name, state: "EMPTY" });
    }

    // 每次查完节流 1.6s，防触发官方市集 429
    if (i < queue.length - 1) {
      for (let tick = 0; tick < 8; tick++) {
        await waitIfPaused(i, item);
        if (controller.aborted) break;
        await sleep(200);
      }
    }
  }

  return { quotes, rateLimited };
}
