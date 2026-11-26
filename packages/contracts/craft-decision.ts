/**
 * 买成品 vs 买半成品加工 vs 白底自做决策契约 + POE2HTC 无缝衔接工具
 */
import type { ParsedItem } from "./build-profile";
import htcCatalogRaw from "./htc-catalog.json";

export type CraftStrategyKind =
  | "semi_exalt"
  | "essence_lock"
  | "omen_targeted"
  | "htc_policy"
  | "htc_raw_policy"
  | "raw_roll";

export type CraftMinTierMode = "any" | "mid" | "high";

export interface CraftRecipeStep {
  step: number;
  action: string;
  currency?: string;
  costDivine?: number;
  successChance?: number;
  detail: string;
}

export interface CraftParetoPlan {
  id: string;
  origin: "semi" | "raw";
  title: string;
  actions: string[];
  hitProbability: number;
  perTryCostDivine: number;
  expectedCostDivine: number;
  p80CostDivine: number;
  steps: CraftRecipeStep[];
}

export interface CraftBestAction {
  mode: "buy_finished" | "craft_semi" | "craft_raw" | "inconclusive";
  headline: string;
  saveDivine: number | null;
  expectedDivine: number | null;
  p80Divine: number | null;
  tradeUrl?: string;
  tradeButtonLabel: string;
  steps: CraftRecipeStep[];
}

export interface CraftPolicySummary {
  semiExpectedDivine: number | null;
  semiP80Divine: number | null;
  rawExpectedDivine: number | null;
  rawP80Divine: number | null;
  recoveryUsed: boolean;
  notes: string[];
}

export interface CraftCompareRequest {
  slot: string;
  baseType?: string;
  league?: string;
  ilvl: number;
  /** 目标想要的词缀（文本匹配或词缀组 ID） */
  wantAffixes: string[];
  /** 半成品已拥有的词缀（用于锁定前缀/后缀占用与同组互斥） */
  existingAffixes?: string[];
  /** 半成品底子市场价（Divine） */
  semiBasePrice?: number;
  /** 白装/蓝装底材市场价（Divine） */
  whiteBasePrice?: number;
  /** 直接买成品/平替市场价（Divine） */
  finishedBuyPrice?: number;
  /** 可选精华名与单价 */
  essenceName?: string;
  essencePrice?: number;
  /** 目标词缀 T 级门槛：any 任意T / mid T3+ / high T1-T2 */
  minTierMode?: CraftMinTierMode;
  /** 半成品若带有破裂锁词（不可被剥离/重铸） */
  fracturedAffix?: string;
}

export interface CraftStrategyEstimate {
  strategy: CraftStrategyKind;
  label: string;
  origin?: "semi" | "raw";
  solverKind?: "dp" | "mdp_policy" | "pareto";
  hitProbability: number;
  expectedAttempts: number | null;
  expectedCostDivine: number | null;
  p80CostDivine: number | null;
  brickRiskNote?: string;
  stepsNote?: string;
  currencyUsed?: string[];
  recipeSteps?: CraftRecipeStep[];
  en?: {
    label?: string;
    brickRiskNote?: string;
    stepsNote?: string;
  };
}

export interface CraftResolvedAffix {
  input: string;
  matched: boolean;
  group: string;
  side: "prefix" | "suffix" | "unknown";
  sampleText: string;
  tierCount: number;
  zhText?: string;
  tradeStatId?: string;
  desiredValue?: number | null;
  qualifyingTiers?: number;
}

export interface CraftLibraryStatus {
  affixLibrary: {
    generatedAt: string;
    modsCount: number;
    classesCount: number;
    source?: Record<string, string>;
    isRuntimeOverride?: boolean;
  };
  tradeStats: {
    generatedAt: string;
    count: number;
    zhCount: number;
    source?: Record<string, string>;
    isRuntimeOverride?: boolean;
  };
}

export interface CraftEssenceOption {
  name: string;
  type: string;
  tierLevel: number;
  modId: string;
  modText: string;
  group: string;
  side: "prefix" | "suffix" | "unknown";
  priceDivine: number | null;
}

export interface NinjaRatesSnapshot {
  league: string;
  source: "poe.ninja" | "fallback";
  updatedAt: number;
  exaltPerDivine: number;
  chaosPerDivine: number;
  currencyDivine: Record<string, number>;
  essencesDivine: Record<string, number>;
  omensDivine?: Record<string, number>;
}

export interface CraftDecisionResponse {
  slot: string;
  itemClass: string;
  ilvl: number;
  recommended: "buy_finished" | "craft_semi" | "craft_raw" | "inconclusive";
  summary: string;
  finishedBuyPrice: number | null;
  semiBasePrice: number | null;
  whiteBasePrice?: number | null;
  minTierMode?: CraftMinTierMode;
  openPrefixes: number;
  openSuffixes: number;
  matchedWanted: CraftResolvedAffix[];
  matchedExisting: CraftResolvedAffix[];
  estimates: CraftStrategyEstimate[];
  rates: NinjaRatesSnapshot;
  finishedTradeUrl?: string;
  semiTradeUrl?: string;
  whiteBaseTradeUrl?: string;
  htcExternalUrl?: string;
  bestAction?: CraftBestAction;
  paretoPlans?: CraftParetoPlan[];
  policySummary?: CraftPolicySummary;
  en?: {
    summary?: string;
  };
}

export type HtcMode = "scratch" | "have_item";
export type HtcRarity = "normal" | "magic" | "rare" | "unique";
export type HtcModOrigin =
  | "explicit"
  | "fractured"
  | "desecrated"
  | "crafted"
  | "implicit"
  | "enchant"
  | "rune";

export const HTC_BASE_CLASSES = [
  "Amulets",
  "Belts",
  "Body Armours",
  "Boots",
  "Bows",
  "Bucklers",
  "Crossbows",
  "Foci",
  "Gloves",
  "Helmets",
  "One Hand Maces",
  "Quarterstaves",
  "Quivers",
  "Rings",
  "Sceptres",
  "Shields",
  "Spears",
  "Staves",
  "Two Hand Maces",
  "Wands",
] as const;

export type HtcBaseClass = (typeof HTC_BASE_CLASSES)[number];

export interface HtcParsedMod {
  raw: string;
  cleaned: string;
  template: string;
  numericValue: number | null;
  origin: HtcModOrigin;
  fractured?: boolean;
  desecrated?: boolean;
  skippedReason?: string;
}

export interface ParsedPoe2ClipboardItem {
  itemClassRaw: string;
  htcBaseClass: HtcBaseClass | null;
  baseType: string;
  itemName: string;
  rarity: HtcRarity;
  ilvl: number;
  corrupted: boolean;
  armour: number;
  evasion: number;
  energyShield: number;
  variantHint: string;
  runes: string[];
  mods: HtcParsedMod[];
  transferableMods: HtcParsedMod[];
  skippedMods: HtcParsedMod[];
  unsupportedReason?: string;
}

export interface HtcCompactMod {
  t: string;
  v?: number | null;
  f?: 1;
  d?: 1;
}

export interface HtcHandoffPayload {
  v: 1;
  mode: HtcMode;
  baseClass: HtcBaseClass;
  baseType?: string;
  itemName?: string;
  variantHint?: string;
  ilvl: number;
  rarity?: "n" | "m" | "r";
  minTierMode?: CraftMinTierMode;
  budgetExalts?: number;
  baseCostExalts?: number;
  runes?: string[];
  /** 原始游戏内 Ctrl+C 完整装备文本（仅在 have_item 且为结构化剪贴板文本时携带，可直接喂给 POE2HTC 的 Paste your item） */
  rawExistingText?: string;
  /** 是否已通过 POE2HTC 原生 ?s= 协议编码（置 1 时扩展不再重复操作 DOM） */
  nativeS?: 1;
  /** 原生 ?s= 编码时未能自动映射或超出前后缀上限的词缀列表 */
  nativeUnmatched?: string[];
  existingMods?: HtcCompactMod[];
  targetMods: HtcCompactMod[];
  skippedNotes?: string[];
}

export interface HtcLinkResult {
  supported: boolean;
  unsupportedReason?: string;
  url: string;
  baseClass: HtcBaseClass | null;
  variantHint: string;
  ilvl: number;
  transferableTargetCount: number;
  transferableExistingCount: number;
  skippedNotes: string[];
  payload?: HtcHandoffPayload;
}

const KNOWN_HTC_RUNES = [
  "Astrid's Creativity",
  "Serle's Triumph",
  "Thrud's Might",
  "Kolr's Hunt",
];

function cleanHtcRichText(str: string): string {
  return String(str || "")
    .replace(/\[([^|\]]+)\|([^\]]+)\]/g, "$2")
    .replace(/\[([^\]]+)\]/g, "$1")
    .replace(/\{[^}]*\}/g, "")
    .trim();
}

export function normalizeHtcModTemplate(text: string): string {
  return cleanHtcRichText(text)
    .replace(/\s*\((implicit|crafted|fractured|rune|desecrated|mutated|enchant|local|unscalable)\)\s*/gi, " ")
    .replace(/[+-]?\d+(?:\.\d+)?(?:\s*[–-]\s*[+-]?\d+(?:\.\d+)?)?/g, "#")
    .replace(/\+#/g, "#")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

export function extractModNumericValue(text: string): number | null {
  const cleaned = cleanHtcRichText(text).replace(
    /\s*\((implicit|crafted|fractured|rune|desecrated|mutated|enchant|local|unscalable)\)\s*$/gi,
    ""
  );
  const matches = cleaned.match(/[+-]?\d+(?:\.\d+)?/g);
  if (!matches || matches.length === 0) return null;
  const nums = matches.map((m) => Math.abs(parseFloat(m))).filter((n) => Number.isFinite(n));
  if (nums.length === 0) return null;
  if (nums.length === 1) return nums[0];
  return Math.round(((nums[0] + nums[1]) / 2) * 10) / 10;
}

export function resolveHtcBaseClass(
  slotOrClass: string,
  baseType = ""
): { baseClass: HtcBaseClass | null; unsupportedReason?: string } {
  const s = (slotOrClass || "").trim();
  const b = (baseType || "").trim();
  const combined = `${s} ${b}`.toLowerCase();

  if (/\b(jewel|diamond|emerald|ruby|sapphire|time-lost)\b/.test(combined)) {
    return { baseClass: null, unsupportedReason: "POE2HTC 暂不支持珠宝（Jewels）做装规划。" };
  }
  if (/\b(charm|flask)\b/.test(combined)) {
    return { baseClass: null, unsupportedReason: "POE2HTC 暂不支持药剂与护符（Flasks / Charms）。" };
  }

  for (const cls of HTC_BASE_CLASSES) {
    if (cls.toLowerCase() === s.toLowerCase()) return { baseClass: cls };
  }

  if (/\bamulet\b/.test(combined)) return { baseClass: "Amulets" };
  if (/\bring\b/.test(combined)) return { baseClass: "Rings" };
  if (/\bbelt\b/.test(combined)) return { baseClass: "Belts" };
  if (
    /\b(body|chest|vest|robe|coat|plate|armour|brigandine|garb|regalia|raiment|mantle|jacket|tunic|doublet|cuirass)\b/.test(combined) &&
    !/\b(helmet|glove|boot|shield|buckler)\b/.test(combined)
  ) {
    return { baseClass: "Body Armours" };
  }
  if (/\b(helm|tiara|crown|hood|circlet|cap|mask|visage|tricorne|burgonet|greathelm)\b/.test(combined)) {
    return { baseClass: "Helmets" };
  }
  if (/\b(glove|wrap|gauntlet|mitt|bracer|cuff)\b/.test(combined)) return { baseClass: "Gloves" };
  if (/\b(boot|shoe|greave|sandal|slipper|legging|sabaton)\b/.test(combined)) return { baseClass: "Boots" };
  if (/\bquiver\b/.test(combined)) return { baseClass: "Quivers" };
  if (/\bbuckler\b/.test(combined)) return { baseClass: "Bucklers" };
  if (/\b(focus|foci)\b/.test(combined)) return { baseClass: "Foci" };
  if (/\bshield\b/.test(combined)) return { baseClass: "Shields" };
  if (/\bspear\b/.test(combined)) return { baseClass: "Spears" };
  if (/\bcrossbow\b/.test(combined)) return { baseClass: "Crossbows" };
  if (/\bbow\b/.test(combined)) return { baseClass: "Bows" };
  if (/\b(quarterstaff|quarterstaves|warstaff)\b/.test(combined)) return { baseClass: "Quarterstaves" };
  if (/\b(staff|staves)\b/.test(combined)) return { baseClass: "Staves" };
  if (/\bwand\b/.test(combined)) return { baseClass: "Wands" };
  if (/\bsceptre\b/.test(combined)) return { baseClass: "Sceptres" };
  if (/\b(two hand mace|greatmace|maul|greathammer)\b/.test(combined)) return { baseClass: "Two Hand Maces" };
  if (/\b(one hand mace|mace|hammer|club)\b/.test(combined)) return { baseClass: "One Hand Maces" };

  if (/\b(sword|axe|dagger|claw|flail|talisman)\b/.test(combined)) {
    return { baseClass: null, unsupportedReason: `POE2HTC 当前词缀库暂未开放「${s || b}」类别。` };
  }
  return { baseClass: null, unsupportedReason: `无法识别装备大类「${s || b || "未知"}」。` };
}

export function inferHtcVariantHint(
  baseClass: HtcBaseClass | null,
  baseType: string,
  defenses: { armour?: number; evasion?: number; energyShield?: number } = {}
): string {
  const b = (baseType || "").toLowerCase();
  if (!baseClass) return "";
  if (baseClass === "Wands") {
    if (b.includes("volatile")) return "Fire";
    if (b.includes("frigid")) return "Cold";
    if (b.includes("galvanic")) return "Lightning";
    if (b.includes("bone") || b.includes("offering")) return "Physical";
    if (b.includes("primordial") || b.includes("withered")) return "Chaos";
    return "";
  }
  if (
    baseClass === "Body Armours" ||
    baseClass === "Helmets" ||
    baseClass === "Gloves" ||
    baseClass === "Boots" ||
    baseClass === "Shields"
  ) {
    const parts: string[] = [];
    if (Number(defenses.armour || 0) > 0) parts.push("Armour");
    if (Number(defenses.evasion || 0) > 0) parts.push("Evasion");
    if (Number(defenses.energyShield || 0) > 0) parts.push("Energy Shield");
    if (parts.length > 0) return parts.join(" / ");
  }
  return baseType || "";
}
function classifyHtcModLine(rawLine: string): HtcParsedMod {
  const raw = rawLine.trim();
  const low = raw.toLowerCase();
  let origin: HtcModOrigin = "explicit";
  let fractured = false;
  let desecrated = false;

  if (/\(fractured\)|\{fractured\}/i.test(low)) {
    origin = "fractured";
    fractured = true;
  } else if (/\(desecrated\)|\{desecrated\}/i.test(low)) {
    origin = "desecrated";
    desecrated = true;
  } else if (/\(rune\)|\{rune\}|\(augment\)/i.test(low)) {
    origin = "rune";
  } else if (/\(implicit\)|\{implicit\}/i.test(low)) {
    origin = "implicit";
  } else if (/\(enchant\)|\{enchant\}/i.test(low)) {
    origin = "enchant";
  } else if (/\(crafted\)|\{crafted\}/i.test(low)) {
    origin = "crafted";
  }

  const cleaned = cleanHtcRichText(raw)
    .replace(/\s*\((implicit|crafted|fractured|rune|desecrated|mutated|enchant|augment)\)\s*$/gi, "")
    .trim();

  let skippedReason: string | undefined;
  if (origin === "rune") skippedReason = "符文词条（不占前后缀）";
  else if (origin === "implicit") skippedReason = "基底固有词条（非前后缀）";
  else if (origin === "enchant" || /^allocates\s+/i.test(cleaned)) skippedReason = "涂油/附魔（可后续自涂）";
  else if (/^grants\s+(skill:|1 additional skill slot)/i.test(cleaned)) skippedReason = "基底自带技能（通过基底自带）";
  else if (/^-1\s+(prefix|suffix)\s+modifier\s+allowed$/i.test(cleaned)) skippedReason = "特殊基底固有代价";

  return {
    raw,
    cleaned,
    template: normalizeHtcModTemplate(cleaned),
    numericValue: extractModNumericValue(cleaned),
    origin,
    fractured: fractured || undefined,
    desecrated: desecrated || undefined,
    skippedReason,
  };
}

const HTC_HEADER_LINE_RE =
  /^(Item Class|Rarity|Quality|Armour|Evasion Rating|Evasion|Energy Shield|Ward|Block Chance|Physical Damage|Elemental Damage|Chaos Damage|Critical Hit Chance|Attacks per Second|Weapon Range|Spirit|Requirements|Level|Str|Dex|Int|Sockets|Item Level|Note|Price|Mirrored|Unidentified|Split|Sanctified|Corrupted|Place into|Right click)/i;

export function parsePoe2ItemText(rawText: string, fallbackSlot = "", fallbackBase = ""): ParsedPoe2ClipboardItem {
  const text = String(rawText || "").replace(/\r\n/g, "\n").trim();
  const sections = text
    .split(/\n-{4,}\n/)
    .map((sec) => sec.split("\n").map((l) => l.trim()).filter(Boolean))
    .filter((sec) => sec.length > 0);

  let itemClassRaw = "";
  let rarity: HtcRarity = "rare";
  let itemName = "";
  let baseType = fallbackBase;
  let ilvl = 82;
  let corrupted = false;
  let armour = 0;
  let evasion = 0;
  let energyShield = 0;
  const runes: string[] = [];
  const mods: HtcParsedMod[] = [];

  const isStructuredClipboard = sections.length >= 2 || /^Item Class:|^Rarity:/im.test(text);
  if (!isStructuredClipboard) {
    for (const ln of text.split("\n").map((l) => l.trim()).filter(Boolean)) {
      mods.push(classifyHtcModLine(ln));
    }
  } else {
    for (let sIdx = 0; sIdx < sections.length; sIdx++) {
      const lines = sections[sIdx];
      if (sIdx === 0 && lines.some((l) => /^Item Class:|^Rarity:/i.test(l))) {
        const nonMeta: string[] = [];
        for (const l of lines) {
          const mCls = l.match(/^Item Class:\s*(.+)$/i);
          if (mCls) {
            itemClassRaw = mCls[1].trim();
            continue;
          }
          const mRar = l.match(/^Rarity:\s*(.+)$/i);
          if (mRar) {
            const rv = mRar[1].trim().toLowerCase();
            if (rv.includes("unique")) rarity = "unique";
            else if (rv.includes("normal")) rarity = "normal";
            else if (rv.includes("magic")) rarity = "magic";
            else rarity = "rare";
            continue;
          }
          nonMeta.push(l);
        }
        if (nonMeta.length >= 2) {
          itemName = nonMeta[0];
          baseType = nonMeta[1];
        } else if (nonMeta.length === 1) {
          baseType = nonMeta[0];
          itemName = nonMeta[0];
        }
        continue;
      }

      let isPropertyOrReqSection = false;
      for (const l of lines) {
        const mIlvl = l.match(/^Item Level:\s*(\d+)/i);
        if (mIlvl) {
          ilvl = Math.max(1, Math.min(100, parseInt(mIlvl[1], 10) || 82));
          isPropertyOrReqSection = true;
          continue;
        }
        const mAr = l.match(/^Armour:\s*(\d+)/i);
        if (mAr) {
          armour = parseInt(mAr[1], 10) || 0;
          isPropertyOrReqSection = true;
          continue;
        }
        const mEv = l.match(/^Evasion(?: Rating)?:\s*(\d+)/i);
        if (mEv) {
          evasion = parseInt(mEv[1], 10) || 0;
          isPropertyOrReqSection = true;
          continue;
        }
        const mEs = l.match(/^Energy Shield:\s*(\d+)/i);
        if (mEs) {
          energyShield = parseInt(mEs[1], 10) || 0;
          isPropertyOrReqSection = true;
          continue;
        }
        if (/^Corrupted$/i.test(l)) {
          corrupted = true;
          isPropertyOrReqSection = true;
          continue;
        }
        if (/^Requirements:|^Sockets:|^Quality:|^Physical Damage:|^Elemental Damage:|^Attacks per Second:|^Critical Hit Chance:|^Block Chance:|^Spirit:|^Note:|^~price/i.test(l)) {
          isPropertyOrReqSection = true;
          continue;
        }
      }

      if (isPropertyOrReqSection) {
        for (const l of lines) {
          for (const kr of KNOWN_HTC_RUNES) {
            if (l.toLowerCase().includes(kr.toLowerCase()) && !runes.includes(kr)) runes.push(kr);
          }
        }
        continue;
      }

      const looksLikeFlavor =
        rarity === "unique" &&
        sIdx === sections.length - 1 &&
        lines.every((l) => !/\d/.test(l) && !/\((implicit|fractured|desecrated|rune|enchant|crafted)\)/i.test(l));
      if (looksLikeFlavor) continue;

      for (const l of lines) {
        if (HTC_HEADER_LINE_RE.test(l)) continue;
        for (const kr of KNOWN_HTC_RUNES) {
          if (l.toLowerCase().includes(kr.toLowerCase()) && !runes.includes(kr)) runes.push(kr);
        }
        mods.push(classifyHtcModLine(l));
      }
    }
  }

  const { baseClass, unsupportedReason: clsReason } = resolveHtcBaseClass(itemClassRaw || fallbackSlot, baseType);
  let unsupportedReason = clsReason;
  if (rarity === "unique") unsupportedReason = "传奇/暗金装备不支持做装规划，请直接去官方市集购买成品。";
  else if (corrupted) unsupportedReason = "该装备已腐化（Corrupted），无法再使用通货加工。";

  const variantHint = inferHtcVariantHint(baseClass, baseType, { armour, evasion, energyShield });
  return {
    itemClassRaw,
    htcBaseClass: baseClass,
    baseType,
    itemName,
    rarity,
    ilvl,
    corrupted,
    armour,
    evasion,
    energyShield,
    variantHint,
    runes,
    mods,
    transferableMods: mods.filter((m) => !m.skippedReason),
    skippedMods: mods.filter((m) => Boolean(m.skippedReason)),
    unsupportedReason,
  };
}
type HtcCatalogTuple = [
  slug: string,
  side: 0 | 1,
  src: number,
  text: string,
  tiers: Array<[number, number]>,
];

interface HtcCatalogData {
  patch: string;
  p: HtcCatalogTuple[];
  b: Record<string, number[]>;
}

const HTC_CATALOG = htcCatalogRaw as unknown as HtcCatalogData;

const HTC_RUNE_ID_MAP: Record<string, string> = {
  "astrid's creativity": "astrids-creativity",
  "serle's triumph": "serles-triumph",
  "thrud's might": "thruds-might",
  "kolr's hunt": "kolrs-hunt",
  "uhtred's sidereus": "uhtreds-sidereus",
};

function encodeBase64UrlUtf8(str: string): string {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) {
    bin += String.fromCharCode(bytes[i]);
  }
  const b64 =
    typeof btoa === "function"
      ? btoa(bin)
      : typeof Buffer !== "undefined"
      ? Buffer.from(bytes).toString("base64")
      : "";
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function tokenizeCatalogTemplate(norm: string): Set<string> {
  const stop = new Set(["to", "of", "the", "a", "an", "with", "for", "and", "in", "on", "#"]);
  return new Set(norm.split(/[^a-z0-9%]+/).filter((t) => t.length >= 2 && !stop.has(t)));
}

export function resolveHtcBaseId(baseClass: HtcBaseClass, variantHint = "", baseType = ""): string {
  const bases = HTC_CATALOG.b;
  const vh = `${variantHint || ""} ${baseType || ""}`.toLowerCase();

  if (baseClass === "One Hand Maces") return "OneHand_Maces";
  if (baseClass === "Two Hand Maces") return "TwoHand_Maces";

  if (baseClass === "Wands" || baseClass === "Staves") {
    if (/\bfire\b|volatile|pyre|ashen/.test(vh)) return `${baseClass}_fire`;
    if (/\bcold\b|frigid|gelid/.test(vh)) return `${baseClass}_cold`;
    if (/\blightning\b|galvanic|voltaic/.test(vh)) return `${baseClass}_lightning`;
    if (/\bphysical\b|bone|offering/.test(vh)) return `${baseClass}_physical`;
    if (/\bchaos\b|primordial|withered/.test(vh)) return `${baseClass}_chaos`;
    return baseClass;
  }

  if (baseClass === "Shields") {
    const hasEv = /evasion/.test(vh);
    const hasEs = /energy shield/.test(vh);
    if (hasEv) return "Shields_str_dex";
    if (hasEs) return "Shields_str_int";
    return "Shields_str";
  }

  if (
    baseClass === "Body Armours" ||
    baseClass === "Helmets" ||
    baseClass === "Gloves" ||
    baseClass === "Boots"
  ) {
    const prefix = baseClass.replace(/\s+/g, "_");
    const hasAr = /\barmour\b|\bplate\b|\bcuirass\b|\bgreathelm\b|\bgauntlet\b|\bgreave\b/.test(vh);
    const hasEv = /\bevasion\b|\bvest\b|\bbrigandine\b|\bhood\b|\btricorne\b|\bwrap\b|\bslipper\b/.test(vh);
    const hasEs = /\benergy shield\b|\brobe\b|\bregalia\b|\braiment\b|\btiara\b|\bcirclet\b|\bmask\b|\bsandal\b/.test(vh);

    let suffix = "str";
    if (hasAr && hasEv) suffix = "str_dex";
    else if (hasAr && hasEs) suffix = "str_int";
    else if (hasEv && hasEs) suffix = "dex_int";
    else if (hasEv) suffix = "dex";
    else if (hasEs) suffix = "int";
    else if (hasAr) suffix = "str";

    const candidate = `${prefix}_${suffix}`;
    if (bases[candidate]) return candidate;
  }

  const direct = baseClass.replace(/\s+/g, "_");
  if (bases[direct]) return direct;
  const firstMatch = Object.keys(bases).find((k) => k.toLowerCase().startsWith(direct.toLowerCase()));
  return firstMatch || baseClass;
}

function pickCatalogTierDisplay(
  tiers: Array<[number, number]>,
  targetVal: number | null | undefined,
  minTierMode: CraftMinTierMode = "mid"
): number {
  if (!tiers || tiers.length === 0) return 1;
  if (targetVal != null && Number.isFinite(targetVal)) {
    const exactIdx = tiers.findIndex(([lo, hi]) => targetVal >= lo && targetVal <= hi);
    if (exactIdx >= 0) return exactIdx + 1;

    const maxHi = Math.max(...tiers.map(([, hi]) => hi));
    if (targetVal > maxHi) return 1;

    const floorIdx = tiers.findIndex(([lo]) => targetVal >= lo * 0.92);
    if (floorIdx >= 0) return floorIdx + 1;

    let bestIdx = 0;
    let bestDist = Infinity;
    for (let i = 0; i < tiers.length; i++) {
      const mid = (tiers[i][0] + tiers[i][1]) / 2;
      const dist = Math.abs(mid - targetVal);
      if (dist < bestDist) {
        bestDist = dist;
        bestIdx = i;
      }
    }
    return bestIdx + 1;
  }

  if (minTierMode === "high") return 1;
  if (minTierMode === "any") return tiers.length;
  return Math.min(tiers.length, Math.floor(tiers.length * 0.35) + 1);
}

interface MatchedCatalogMod {
  slug: string;
  side: 0 | 1;
  src: number;
  tierDisplay: number;
}

function matchCatalogMod(
  baseId: string,
  mod: HtcCompactMod,
  minTierMode: CraftMinTierMode,
  allowCraftedSources: boolean
): MatchedCatalogMod | null {
  const indices = HTC_CATALOG.b[baseId];
  if (!indices || indices.length === 0) return null;

  const qNorm = normalizeHtcModTemplate(mod.t);
  if (!qNorm) return null;
  const qTokens = tokenizeCatalogTemplate(qNorm);

  let best: { tuple: HtcCatalogTuple; score: number } | null = null;
  for (const idx of indices) {
    const tuple = HTC_CATALOG.p[idx];
    if (!tuple) continue;
    const [, , src, text] = tuple;
    // POE2HTC's "I have an item" existing prefixes/suffixes rejects essence / perfect_essence
    if (!allowCraftedSources && (src === 2 || src === 3)) continue;

    const candNorm = normalizeHtcModTemplate(text);
    if (!candNorm) continue;

    let score = 0;
    if (candNorm === qNorm) {
      score = 1.0;
    } else {
      const rTokens = tokenizeCatalogTemplate(candNorm);
      if (qTokens.size > 0 && rTokens.size > 0) {
        let inter = 0;
        for (const tok of qTokens) if (rTokens.has(tok)) inter++;
        const jacc = inter / Math.max(1, qTokens.size + rTokens.size - inter);
        const recall = inter / qTokens.size;
        score = jacc * 0.65 + recall * 0.35;
      }
    }
    if (score < 0.72) continue;

    if (mod.d) {
      score += src === 1 ? 0.08 : -0.05;
    } else {
      if (src === 0) score += 0.06;
      else if (src === 1) score -= 0.06;
      else score -= 0.12;
    }

    if (!best || score > best.score) best = { tuple, score };
  }

  if (!best) return null;
  const [slug, side, src, , tiers] = best.tuple;
  return {
    slug,
    side,
    src,
    tierDisplay: pickCatalogTierDisplay(tiers, mod.v, minTierMode),
  };
}

export interface NativeHtcShareEncoded {
  s: string;
  baseId: string;
  matchedExistingCount: number;
  matchedTargetCount: number;
  unmatchedMods: string[];
}

export function buildNativePoe2HtcShareParam(payload: HtcHandoffPayload): NativeHtcShareEncoded | null {
  const baseId = resolveHtcBaseId(payload.baseClass, payload.variantHint, payload.baseType);
  if (!HTC_CATALOG.b[baseId]) return null;

  const minTier = payload.minTierMode || "mid";
  const runeIds = (payload.runes || [])
    .map((r) => HTC_RUNE_ID_MAP[r.trim().toLowerCase().replace(/[’']/g, "'")])
    .filter((id): id is string => Boolean(id));
  const hasSerle = runeIds.includes("serles-triumph");

  const maxRareP = 3;
  const maxRareS = hasSerle ? 4 : 3;
  const isMagic = payload.rarity === "m";
  const maxItemP = isMagic ? 1 : maxRareP;
  const maxItemS = isMagic ? 1 : maxRareS;

  const unmatchedMods: string[] = [];
  const ilvl = Math.max(1, Math.min(100, Math.round(payload.ilvl || 82)));

  // 1. Existing item prefixes / suffixes (I have an item)
  const itemPx: Array<[string, number] | [string, number, 1]> = [];
  const itemSx: Array<[string, number] | [string, number, 1]> = [];
  const seenExSlugs = new Set<string>();
  let usedItemFractured = false;

  for (const mod of payload.existingMods || []) {
    const hit = matchCatalogMod(baseId, mod, minTier, false);
    if (!hit) {
      unmatchedMods.push(mod.t);
      continue;
    }
    if (seenExSlugs.has(hit.slug)) continue;
    const bucket = hit.side === 0 ? itemPx : itemSx;
    const cap = hit.side === 0 ? maxItemP : maxItemS;
    if (bucket.length >= cap) {
      unmatchedMods.push(`${mod.t}（超出${hit.side === 0 ? "前缀" : "后缀"}上限 ${cap} 条）`);
      continue;
    }
    seenExSlugs.add(hit.slug);
    if (mod.f && !usedItemFractured) {
      usedItemFractured = true;
      bucket.push([hit.slug, hit.tierDisplay, 1]);
    } else {
      bucket.push([hit.slug, hit.tierDisplay]);
    }
  }

  // 2. Target mods (for both Plan from scratch & I have an item -> Full plan to a target)
  const targets: Array<[string, number]> = [];
  const fracturedTargets: string[] = [];
  const seenTargetSlugs = new Set<string>();
  let targetPCount = 0;
  let targetSCount = 0;

  for (const mod of payload.targetMods || []) {
    const hit = matchCatalogMod(baseId, mod, minTier, true);
    if (!hit) {
      unmatchedMods.push(mod.t);
      continue;
    }
    if (seenTargetSlugs.has(hit.slug)) continue;
    if (hit.side === 0 && targetPCount >= maxRareP) {
      unmatchedMods.push(`${mod.t}（超出前缀上限 ${maxRareP} 条）`);
      continue;
    }
    if (hit.side === 1 && targetSCount >= maxRareS) {
      unmatchedMods.push(`${mod.t}（超出后缀上限 ${maxRareS} 条）`);
      continue;
    }
    seenTargetSlugs.add(hit.slug);
    if (hit.side === 0) targetPCount++;
    else targetSCount++;
    targets.push([hit.slug, hit.tierDisplay]);
    if (mod.f && fracturedTargets.length === 0) {
      fracturedTargets.push(hit.slug);
    }
  }

  const workspace = {
    v: 4,
    m: payload.mode === "have_item" ? "i" : "p",
    l: {
      b: baseId,
      lv: ilvl,
      t: payload.mode === "scratch" ? targets : [],
      f: payload.mode === "scratch" ? fracturedTargets : [],
      p: [] as string[],
      bg: payload.budgetExalts && payload.budgetExalts > 0 ? String(payload.budgetExalts) : "",
      bc: payload.baseCostExalts && payload.baseCostExalts > 0 ? String(payload.baseCostExalts) : "",
      ...(runeIds.length > 0 ? { ru: runeIds } : {}),
    },
    i: {
      b: baseId,
      lv: ilvl,
      r: isMagic ? "m" : "r",
      px: itemPx,
      sx: itemSx,
      sm: targets.length > 0 ? "p" : "c",
      t: payload.mode === "have_item" ? targets : [],
      ...(runeIds.length > 0 ? { ru: runeIds } : {}),
    },
  };

  return {
    s: encodeBase64UrlUtf8(JSON.stringify(workspace)),
    baseId,
    matchedExistingCount: itemPx.length + itemSx.length,
    matchedTargetCount: targets.length,
    unmatchedMods,
  };
}

export function decodeHtcHandoffHash(hash: string): HtcHandoffPayload | null {
  try {
    const sp = new URLSearchParams(String(hash || "").replace(/^#/, ""));
    const raw = sp.get("poe2wb2") || sp.get("poe2wb");
    if (!raw) return null;
    const obj = JSON.parse(raw) as HtcHandoffPayload;
    if (obj && obj.v === 1 && obj.baseClass) return obj;
    return null;
  } catch {
    return null;
  }
}

export function buildPoe2HtcUrl(payload: HtcHandoffPayload): string {
  const base = "https://poe2htc.com/";
  const encoded = buildNativePoe2HtcShareParam(payload);
  if (encoded) {
    const hashPayload: HtcHandoffPayload = {
      ...payload,
      nativeS: 1,
      ...(encoded.unmatchedMods.length > 0 ? { nativeUnmatched: encoded.unmatchedMods } : {}),
    };
    const hashParams = new URLSearchParams();
    hashParams.set("poe2wb2", JSON.stringify(hashPayload));
    return `${base}?s=${encoded.s}#${hashParams.toString()}`;
  }
  const params = new URLSearchParams();
  params.set("poe2wb2", JSON.stringify(payload));
  return `${base}#${params.toString()}`;
}

/**
 * 生成可直接粘贴到 POE2HTC「Paste your item」输入框的标准 PoE2 游戏内装备文本。
 * 优先返回原始 Ctrl+C 剪贴板文本；若没有原始文本，则按基底/物等/词缀合成标准格式。
 */
export function buildPoe2PasteTextFromPayload(payload: HtcHandoffPayload): string {
  if (payload.rawExistingText && payload.rawExistingText.trim()) {
    return payload.rawExistingText.trim();
  }
  const rarityLabel = payload.rarity === "n" ? "Normal" : payload.rarity === "m" ? "Magic" : "Rare";
  const baseLine = (payload.baseType || payload.itemName || payload.baseClass).trim();
  const nameLine = (payload.itemName || "").trim();
  const headerLines = [`Item Class: ${payload.baseClass}`, `Rarity: ${rarityLabel}`];
  if (rarityLabel === "Rare") {
    headerLines.push(nameLine && nameLine.toLowerCase() !== baseLine.toLowerCase() ? nameLine : "Workbench Item");
  }
  headerLines.push(baseLine);

  const modsSource =
    payload.existingMods && payload.existingMods.length > 0 ? payload.existingMods : payload.targetMods || [];
  const modLines = modsSource
    .map((m) => {
      const suffix = m.f ? " (fractured)" : m.d ? " (desecrated)" : "";
      return `${m.t}${suffix}`.trim();
    })
    .filter(Boolean);

  const sections = [headerLines.join("\n"), `Item Level: ${payload.ilvl || 82}`];
  if (modLines.length > 0) {
    sections.push(modLines.join("\n"));
  }
  return sections.join("\n--------\n");
}

function extractCompactRawClipboardText(rawText?: string): string | undefined {
  const cleaned = String(rawText || "").replace(/\r\n/g, "\n").trim();
  if (!cleaned || cleaned.length > 2400) return undefined;
  if (/^Item Class:|^Rarity:/im.test(cleaned) || /\n-{4,}\n/.test(cleaned)) {
    return cleaned;
  }
  return undefined;
}

export function buildHtcLinkFromParsedItem(
  item: ParsedItem,
  options: {
    mode?: HtcMode;
    minTierMode?: CraftMinTierMode;
    existingItemText?: string;
    budgetExalts?: number;
    baseCostExalts?: number;
  } = {}
): HtcLinkResult {
  if ((item.rarity || "RARE").toLowerCase() === "unique") {
    return {
      supported: false,
      unsupportedReason: "传奇/暗金装备不支持做装，请直接去市集购买成品。",
      url: "https://poe2htc.com/",
      baseClass: null,
      variantHint: "",
      ilvl: item.itemLevel || 82,
      transferableTargetCount: 0,
      transferableExistingCount: 0,
      skippedNotes: [],
    };
  }

  const { baseClass, unsupportedReason } = resolveHtcBaseClass(item.slot, item.baseType);
  const ilvl = Math.max(1, Math.min(100, item.itemLevel || Math.max(82, item.levelReq || 82)));
  if (!baseClass) {
    return {
      supported: false,
      unsupportedReason: unsupportedReason || "当前部位暂不支持 POE2HTC。",
      url: "https://poe2htc.com/",
      baseClass: null,
      variantHint: "",
      ilvl,
      transferableTargetCount: 0,
      transferableExistingCount: 0,
      skippedNotes: [],
    };
  }

  const props = item.properties || {};
  const parseProp = (v?: string) => {
    const m = String(v || "").match(/(\d+)/);
    return m ? parseInt(m[1], 10) : 0;
  };
  const variantHint = inferHtcVariantHint(baseClass, item.baseType, {
    armour: parseProp(props.Armour),
    evasion: parseProp(props.Evasion || props["Evasion Rating"]),
    energyShield: parseProp(props["Energy Shield"]),
  });

  const parsedTargetMods: HtcParsedMod[] = [];
  if (item.modDetails && item.modDetails.length > 0) {
    for (const d of item.modDetails) {
      const suffix = d.origin !== "explicit" ? ` (${d.origin})` : "";
      parsedTargetMods.push(classifyHtcModLine(`${d.text}${suffix}`));
    }
  } else {
    for (const m of item.mods || []) parsedTargetMods.push(classifyHtcModLine(m));
  }

  const runes: string[] = [];
  for (const r of item.socketedRunes || []) {
    for (const kr of KNOWN_HTC_RUNES) {
      if (r.toLowerCase().includes(kr.toLowerCase()) && !runes.includes(kr)) runes.push(kr);
    }
  }

  const targetMods: HtcCompactMod[] = parsedTargetMods
    .filter((m) => !m.skippedReason)
    .slice(0, 6)
    .map((m) => ({
      t: m.cleaned,
      ...(m.numericValue !== null ? { v: m.numericValue } : {}),
      ...(m.fractured ? { f: 1 as const } : {}),
      ...(m.desecrated ? { d: 1 as const } : {}),
    }));

  const skippedNotes = parsedTargetMods
    .filter((m) => Boolean(m.skippedReason))
    .map((m) => `${m.cleaned}（${m.skippedReason}）`);

  let existingMods: HtcCompactMod[] | undefined;
  let rarityCode: "n" | "m" | "r" = "r";
  const mode: HtcMode = options.mode || (options.existingItemText?.trim() ? "have_item" : "scratch");

  if (options.existingItemText?.trim()) {
    const parsedExisting = parsePoe2ItemText(options.existingItemText, item.slot, item.baseType);
    existingMods = parsedExisting.transferableMods.slice(0, 6).map((m) => ({
      t: m.cleaned,
      ...(m.numericValue !== null ? { v: m.numericValue } : {}),
      ...(m.fractured ? { f: 1 as const } : {}),
      ...(m.desecrated ? { d: 1 as const } : {}),
    }));
    rarityCode = parsedExisting.rarity === "normal" ? "n" : parsedExisting.rarity === "magic" ? "m" : "r";
  }

  const rawExistingText = mode === "have_item" ? extractCompactRawClipboardText(options.existingItemText) : undefined;

  const payload: HtcHandoffPayload = {
    v: 1,
    mode,
    baseClass,
    baseType: item.baseType || undefined,
    itemName: item.name || item.baseType || item.slot,
    variantHint: variantHint || undefined,
    ilvl,
    rarity: rarityCode,
    minTierMode: options.minTierMode || "mid",
    ...(options.budgetExalts ? { budgetExalts: options.budgetExalts } : {}),
    ...(options.baseCostExalts ? { baseCostExalts: options.baseCostExalts } : {}),
    ...(runes.length > 0 ? { runes } : {}),
    ...(rawExistingText ? { rawExistingText } : {}),
    ...(existingMods && existingMods.length > 0 ? { existingMods } : {}),
    targetMods,
    ...(skippedNotes.length > 0 ? { skippedNotes: skippedNotes.slice(0, 8) } : {}),
  };

  return {
    supported: true,
    url: buildPoe2HtcUrl(payload),
    baseClass,
    variantHint,
    ilvl,
    transferableTargetCount: targetMods.length,
    transferableExistingCount: existingMods?.length || 0,
    skippedNotes,
    payload,
  };
}

export function buildHtcLinkFromClipboardTexts(params: {
  mode: HtcMode;
  targetText?: string;
  existingText?: string;
  fallbackSlot?: string;
  fallbackBaseType?: string;
  ilvlOverride?: number;
  minTierMode?: CraftMinTierMode;
  budgetExalts?: number;
  baseCostExalts?: number;
}): HtcLinkResult {
  const parsedTarget = params.targetText?.trim()
    ? parsePoe2ItemText(params.targetText, params.fallbackSlot, params.fallbackBaseType)
    : null;
  const parsedExisting = params.existingText?.trim()
    ? parsePoe2ItemText(params.existingText, params.fallbackSlot, params.fallbackBaseType)
    : null;

  const primary = params.mode === "have_item" ? parsedExisting || parsedTarget : parsedTarget || parsedExisting;
  const baseClass =
    primary?.htcBaseClass ||
    parsedTarget?.htcBaseClass ||
    parsedExisting?.htcBaseClass ||
    resolveHtcBaseClass(params.fallbackSlot || "", params.fallbackBaseType || "").baseClass;

  const unsupportedReason =
    primary?.unsupportedReason ||
    (!baseClass ? "未能识别装备类别，请先选择装备槽位或粘贴完整游戏内 Ctrl+C 文本。" : undefined);

  const ilvl = Math.max(1, Math.min(100, params.ilvlOverride || primary?.ilvl || 82));

  if (!baseClass || unsupportedReason) {
    return {
      supported: false,
      unsupportedReason: unsupportedReason || "当前装备暂不支持 POE2HTC。",
      url: "https://poe2htc.com/",
      baseClass,
      variantHint: primary?.variantHint || "",
      ilvl,
      transferableTargetCount: 0,
      transferableExistingCount: 0,
      skippedNotes: [],
    };
  }

  const targetMods: HtcCompactMod[] = (parsedTarget?.transferableMods || [])
    .slice(0, 6)
    .map((m) => ({
      t: m.cleaned,
      ...(m.numericValue !== null ? { v: m.numericValue } : {}),
      ...(m.fractured ? { f: 1 as const } : {}),
      ...(m.desecrated ? { d: 1 as const } : {}),
    }));

  const existingMods: HtcCompactMod[] = (parsedExisting?.transferableMods || [])
    .slice(0, 6)
    .map((m) => ({
      t: m.cleaned,
      ...(m.numericValue !== null ? { v: m.numericValue } : {}),
      ...(m.fractured ? { f: 1 as const } : {}),
      ...(m.desecrated ? { d: 1 as const } : {}),
    }));

  const skippedNotes = [
    ...(parsedTarget?.skippedMods || []).map((m) => `目标跳过：${m.cleaned}（${m.skippedReason}）`),
    ...(parsedExisting?.skippedMods || []).map((m) => `已有跳过：${m.cleaned}（${m.skippedReason}）`),
  ];

  const runes = Array.from(new Set([...(parsedTarget?.runes || []), ...(parsedExisting?.runes || [])]));
  const rarityCode: "n" | "m" | "r" =
    parsedExisting?.rarity === "normal" ? "n" : parsedExisting?.rarity === "magic" ? "m" : "r";

  const variantHint =
    primary?.variantHint ||
    inferHtcVariantHint(baseClass, params.fallbackBaseType || primary?.baseType || "");

  const rawExistingText = params.mode === "have_item" ? extractCompactRawClipboardText(params.existingText) : undefined;

  const payload: HtcHandoffPayload = {
    v: 1,
    mode: params.mode,
    baseClass,
    baseType: primary?.baseType || params.fallbackBaseType || undefined,
    itemName: primary?.itemName || primary?.baseType || baseClass,
    variantHint: variantHint || undefined,
    ilvl,
    rarity: rarityCode,
    minTierMode: params.minTierMode || "mid",
    ...(params.budgetExalts ? { budgetExalts: params.budgetExalts } : {}),
    ...(params.baseCostExalts ? { baseCostExalts: params.baseCostExalts } : {}),
    ...(runes.length > 0 ? { runes } : {}),
    ...(rawExistingText ? { rawExistingText } : {}),
    ...(existingMods.length > 0 ? { existingMods } : {}),
    targetMods,
    ...(skippedNotes.length > 0 ? { skippedNotes: skippedNotes.slice(0, 8) } : {}),
  };

  return {
    supported: true,
    url: buildPoe2HtcUrl(payload),
    baseClass,
    variantHint,
    ilvl,
    transferableTargetCount: targetMods.length,
    transferableExistingCount: existingMods.length,
    skippedNotes,
    payload,
  };
}
