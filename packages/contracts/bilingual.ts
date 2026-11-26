/**
 * PoE2 双语词典与一键热切换翻译引擎（Desktop & Extension 共享）。
 *
 * 支持三种显示模式（0 延迟热切换，无需重跑 PoB2 引擎）：
 * - "bi": 中英对照（中文 + 英文原文，方便国服玩家对照外服词条/市集/PoB2）
 * - "zh": 纯中文（词典未收录时自动回退英文）
 * - "en": 纯英文原文
 */

export type LangMode = "bi" | "zh" | "en";

export type TermCategory =
  | "any"
  | "classes"
  | "ascendancies"
  | "slots"
  | "itemClasses"
  | "uniques"
  | "bases"
  | "gems"
  | "passives"
  | "static";

export interface GameTermsDict {
  generatedAt?: string;
  classes: Record<string, string>;
  ascendancies: Record<string, string>;
  slots: Record<string, string>;
  itemClasses: Record<string, string>;
  uniques: Record<string, string>;
  bases: Record<string, string>;
  gems: Record<string, string>;
  passives: Record<string, string>;
  static: Record<string, string>;
}

export interface BilingualDictionary {
  terms: GameTermsDict;
  mods: Record<string, string>;
}

export const LANG_STORAGE_KEY = "poe2wb_lang_mode_v1";

export const LANG_MODE_OPTIONS: Array<{
  mode: LangMode;
  shortLabel: string;
  label: string;
  hint: string;
}> = [
  { mode: "bi", shortLabel: "中英", label: "中英对照", hint: "同时显示中文与英文原文 (快捷键 Alt+L)" },
  { mode: "zh", shortLabel: "中文", label: "纯中文", hint: "优先显示国服/汉化中文 (快捷键 Alt+L)" },
  { mode: "en", shortLabel: "EN", label: "English", hint: "显示 PoB2 英文原文 (快捷键 Alt+L)" },
];

export function nextLangMode(current: LangMode): LangMode {
  if (current === "bi") return "zh";
  if (current === "zh") return "en";
  return "bi";
}

export const DEFAULT_SLOTS_ZH: Record<string, string> = {
  "Weapon 1": "主手武器 1",
  "Weapon 2": "副手武器 2",
  "Weapon 1 Swap": "副套主手 1",
  "Weapon 2 Swap": "副套副手 2",
  Helmet: "头盔",
  "Body Armour": "胸甲",
  Gloves: "手套",
  Boots: "鞋子",
  Amulet: "项链",
  "Ring 1": "戒指 1",
  "Ring 2": "戒指 2",
  Belt: "腰带",
  "Flask 1": "药剂 1",
  "Flask 2": "药剂 2",
  "Charm 1": "护符 1",
  "Charm 2": "护符 2",
  "Charm 3": "护符 3",
  Jewel: "珠宝",
  "Passive Tree": "天赋树",
  "Skills & Gems": "技能与宝石",
};

export const DEFAULT_ITEM_CLASSES_ZH: Record<string, string> = {
  Amulets: "项链",
  Belts: "腰带",
  "Body Armours": "胸甲",
  Boots: "鞋子",
  Bows: "弓",
  Bucklers: "轻盾",
  Claws: "爪",
  Crossbows: "战弩",
  Daggers: "匕首",
  Flails: "链锤",
  Foci: "法器",
  Gloves: "手套",
  Helmets: "头盔",
  "One Hand Axes": "单手斧",
  "One Hand Maces": "单手锤",
  "One Hand Swords": "单手剑",
  Quarterstaves: "长杖",
  Quivers: "箭袋",
  Rings: "戒指",
  Sceptres: "权杖",
  Shields: "盾牌",
  Spears: "长矛",
  Staves: "双手法杖",
  Talismans: "护身符",
  "Two Hand Axes": "双手斧",
  "Two Hand Maces": "双手锤",
  "Two Hand Swords": "双手剑",
  Wands: "法杖",
  Jewels: "珠宝",
};

export const DEFAULT_CLASSES_ZH: Record<string, string> = {
  Marauder: "野蛮人",
  Witch: "女巫",
  Ranger: "游侠",
  Duelist: "决斗者",
  Shadow: "暗影刺客",
  Templar: "圣堂武僧",
  Warrior: "战士",
  Sorceress: "术士",
  Huntress: "猎手",
  Mercenary: "佣兵",
  Monk: "武僧",
  Druid: "德鲁伊",
};

export const DEFAULT_ASCENDANCIES_ZH: Record<string, string> = {
  Oracle: "神谕者",
  Shaman: "萨满",
  Amazon: "亚马逊",
  "Spirit Walker": "灵行者",
  Ritualist: "仪祭师",
  Tactician: "战术家",
  Witchhunter: "猎巫人",
  "Gemling Legionnaire": "古灵使徒",
  "Martial Artist": "武术家",
  Invoker: "祈求者",
  "Acolyte of Chayula": "夏乌拉追随者",
  Deadeye: "锐眼",
  Pathfinder: "追猎者",
  Stormweaver: "风暴编织者",
  Chronomancer: "塑时术师",
  "Disciple of Varashta": "瓦拉煞的门徒",
  Titan: "泰坦",
  Warbringer: "战争使者",
  "Smith of Kitava": "奇塔弗匠师",
  Infernalist: "驱炎使",
  "Blood Mage": "命源法师",
  Lich: "巫妖",
  "Abyssal Lich": "深渊巫妖",
};

const EXTRA_MODS_ZH: Record<string, string> = {
  "has # charm slot": "拥有 # 个护符栏位",
  "has # charm slots": "拥有 # 个护符栏位",
  "enemies take #% increased damage for each elemental ailment type among": "敌人身上每种元素异常状态使其受到的伤害提高 #%",
  "your ailments on them": "（基于你施加的异常状态）",
  "wind skills which can be boosted by elemental ground surfaces count": "可受元素地面增强的疾风技能视为同时获得点燃、感电与冰缓地面加成",
  "as being boosted by ignited, shocked, and chilled ground": "（地面元素强化联动）",
  "#% reduced duration of ignite, shock and chill on enemies": "敌人身上的点燃、感电和冰缓持续时间缩短 #%",
};

const EMPTY_TERMS: GameTermsDict = {
  classes: DEFAULT_CLASSES_ZH,
  ascendancies: DEFAULT_ASCENDANCIES_ZH,
  slots: DEFAULT_SLOTS_ZH,
  itemClasses: DEFAULT_ITEM_CLASSES_ZH,
  uniques: {},
  bases: {},
  gems: {},
  passives: {},
  static: {},
};

const NUM_RANGE_RE = /\(\s*[+-]?\d+(?:\.\d+)?\s*-\s*[+-]?\d+(?:\.\d+)?\s*\)|[+-]?\d+(?:\.\d+)?/g;
const RICH_PIPE_RE = /\[([^|\]]+)\|([^\]]+)\]/g;
const RICH_SINGLE_RE = /\[([^\]]+)\]/g;
const TREE_JEWEL_SLOT_RE = /^Tree Jewel\s*\(Node\s*([^)]+)\)$/i;
const GRANTS_SKILL_RE = /^Grants Skill:\s*(?:Level\s+(\d+)\s+)?(.+)$/i;
const ALLOCATES_RE = /^Allocates\s+(.+)$/i;

export function cleanRepoeText(text: string): string {
  return (text || "")
    .replace(RICH_PIPE_RE, "$2")
    .replace(RICH_SINGLE_RE, "$1")
    .trim();
}

export function normalizeStatTemplate(text: string): string {
  let s = cleanRepoeText(text);
  s = s.replace(/\s*\(local\)\s*/gi, " ");
  s = s.replace(/\n/g, " ");
  s = s.replace(NUM_RANGE_RE, "#");
  s = s.replace(/\+#/g, "#");
  s = s.replace(/\s+/g, " ").trim().toLowerCase();
  return s;
}

export interface BilingualTranslator {
  mode: LangMode;
  term: (text: string | undefined | null, category?: TermCategory) => string;
  termZh: (text: string | undefined | null, category?: TermCategory) => string | null;
  slot: (slotName: string | undefined | null) => string;
  mod: (line: string | undefined | null) => string;
  modZh: (line: string | undefined | null) => string | null;
  itemName: (name: string | undefined | null, baseType?: string | null) => string;
  classAsc: (className?: string | null, ascendancy?: string | null) => string;
}

const TERM_CATEGORIES: Array<Exclude<TermCategory, "any">> = [
  "classes",
  "ascendancies",
  "slots",
  "itemClasses",
  "uniques",
  "gems",
  "passives",
  "bases",
  "static",
];

function formatBi(zh: string | null, en: string, mode: LangMode): string {
  if (!en) return "";
  if (mode === "en" || !zh || zh === en) return en;
  if (mode === "zh") return zh;
  return `${zh} (${en})`;
}

export function createTranslator(
  dict: BilingualDictionary | null | undefined,
  mode: LangMode
): BilingualTranslator {
  const terms: GameTermsDict = dict?.terms
    ? {
        generatedAt: dict.terms.generatedAt,
        classes: { ...DEFAULT_CLASSES_ZH, ...(dict.terms.classes || {}) },
        ascendancies: { ...DEFAULT_ASCENDANCIES_ZH, ...(dict.terms.ascendancies || {}) },
        slots: { ...DEFAULT_SLOTS_ZH, ...(dict.terms.slots || {}) },
        itemClasses: { ...DEFAULT_ITEM_CLASSES_ZH, ...(dict.terms.itemClasses || {}) },
        uniques: dict.terms.uniques || {},
        bases: dict.terms.bases || {},
        gems: dict.terms.gems || {},
        passives: dict.terms.passives || {},
        static: dict.terms.static || {},
      }
    : EMPTY_TERMS;

  const mods: Record<string, string> = dict?.mods
    ? { ...EXTRA_MODS_ZH, ...dict.mods }
    : EXTRA_MODS_ZH;

  const termZh = (text: string | undefined | null, category: TermCategory = "any"): string | null => {
    const raw = (text || "").trim();
    if (!raw) return null;
    if (category !== "any") {
      const bucket = terms[category];
      if (bucket && raw in bucket) return bucket[raw];
    }
    for (const key of TERM_CATEGORIES) {
      const bucket = terms[key];
      if (bucket && raw in bucket) return bucket[raw];
    }
    return null;
  };

  const term = (text: string | undefined | null, category: TermCategory = "any"): string => {
    const raw = (text || "").trim();
    if (!raw) return "";
    if (mode === "en") return raw;
    return formatBi(termZh(raw, category), raw, mode);
  };

  const slot = (slotName: string | undefined | null): string => {
    const raw = (slotName || "").trim();
    if (!raw) return "";
    if (mode === "en") return raw;
    const m = TREE_JEWEL_SLOT_RE.exec(raw);
    if (m) {
      const zh = `天赋珠宝孔 (节点 ${m[1]})`;
      return mode === "zh" ? zh : `${zh} (${raw})`;
    }
    const zh = terms.slots[raw] || terms.itemClasses[raw] || null;
    return formatBi(zh, raw, mode);
  };

  const modZh = (line: string | undefined | null): string | null => {
    const raw = (line || "").trim();
    if (!raw) return null;

    const mSkill = GRANTS_SKILL_RE.exec(raw);
    if (mSkill) {
      const lvl = mSkill[1];
      const skillName = mSkill[2].trim();
      const zhSkill = termZh(skillName, "gems") || skillName;
      return lvl ? `赋予技能：${lvl} 级 ${zhSkill}` : `赋予技能：${zhSkill}`;
    }

    const mAlloc = ALLOCATES_RE.exec(raw);
    if (mAlloc) {
      const notable = mAlloc[1].trim();
      const zhNotable = termZh(notable, "passives") || notable;
      return `配置 ${zhNotable}`;
    }

    const norm = normalizeStatTemplate(raw);
    const tmpl = mods[norm];
    if (!tmpl) return null;

    const nums = raw.match(NUM_RANGE_RE);
    if (!nums || nums.length === 0) return tmpl;

    let idx = 0;
    return tmpl.replace(/#/g, (_m, offset: number) => {
      const val = nums[idx++] ?? "#";
      if (offset > 0 && tmpl[offset - 1] === "+" && val.startsWith("+")) {
        return val.slice(1);
      }
      return val;
    });
  };

  const mod = (line: string | undefined | null): string => {
    const raw = (line || "").trim();
    if (!raw) return "";
    if (mode === "en") return raw;
    const zh = modZh(raw);
    if (!zh || zh === raw) return raw;
    if (mode === "zh") return zh;
    return `${zh} (${raw})`;
  };

  const itemName = (name: string | undefined | null, baseType?: string | null): string => {
    const n = (name || "").trim();
    const b = (baseType || "").trim();
    if (!n && !b) return "";
    if (!n) return term(b, "bases");
    if (mode === "en") return b && b !== n ? `${n} (${b})` : n;

    const nZh = termZh(n, "uniques") || termZh(n, "bases");
    if (!b || b === n) {
      return formatBi(nZh, n, mode);
    }
    const bZh = termZh(b, "bases");
    if (mode === "zh") {
      return `${nZh || n} · ${bZh || b}`;
    }
    const nPart = nZh ? `${nZh} (${n})` : n;
    const bPart = bZh ? `${bZh} (${b})` : b;
    return `${nPart} · ${bPart}`;
  };

  const classAsc = (className?: string | null, ascendancy?: string | null): string => {
    const c = (className || "").trim();
    const a = (ascendancy || "").trim();
    const cStr = c ? term(c, "classes") : "";
    const aStr = a ? term(a, "ascendancies") : "";
    if (cStr && aStr) return `${cStr} · ${aStr}`;
    return cStr || aStr || "";
  };

  return { mode, term, termZh, slot, mod, modZh, itemName, classAsc };
}

let cachedDict: BilingualDictionary | null = null;
let loadingDictPromise: Promise<BilingualDictionary> | null = null;

interface ChromeLike {
  runtime?: { getURL?: (path: string) => string };
  storage?: { local?: { set?: (items: Record<string, unknown>) => Promise<void> | void } };
}

function getChromeLike(): ChromeLike | undefined {
  return (globalThis as { chrome?: ChromeLike }).chrome;
}

function resolveAssetUrl(relPath: string): string {
  const ch = getChromeLike();
  if (ch?.runtime?.getURL) {
    return ch.runtime.getURL(relPath);
  }
  return `./${relPath.replace(/^\/+/, "")}`;
}

async function fetchJsonWithTimeout<T>(url: string, timeoutMs: number): Promise<T | null> {
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  try {
    const res = await fetch(url, { method: "GET", signal: ctrl?.signal });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export async function loadBilingualDict(backendUrl?: string): Promise<BilingualDictionary> {
  if (cachedDict) return cachedDict;
  if (loadingDictPromise) return loadingDictPromise;

  loadingDictPromise = (async () => {
    if (backendUrl) {
      const remote = await fetchJsonWithTimeout<{
        ok?: boolean;
        terms?: GameTermsDict;
        mods?: Record<string, string>;
      }>(`${backendUrl.replace(/\/+$/, "")}/meta/game-terms`, 1800);
      if (remote?.ok && remote.terms && remote.mods) {
        cachedDict = { terms: remote.terms, mods: remote.mods };
        return cachedDict;
      }
    }

    const [termsJson, modsJson] = await Promise.all([
      fetchJsonWithTimeout<GameTermsDict>(resolveAssetUrl("data/game_terms_zh.json"), 2500),
      fetchJsonWithTimeout<Record<string, string>>(resolveAssetUrl("data/mods_zh_map.json"), 2500),
    ]);

    cachedDict = {
      terms: termsJson || EMPTY_TERMS,
      mods: modsJson || {},
    };
    return cachedDict;
  })();

  return loadingDictPromise;
}

export function readStoredLangMode(): LangMode {
  try {
    if (typeof localStorage !== "undefined") {
      const v = localStorage.getItem(LANG_STORAGE_KEY);
      if (v === "bi" || v === "zh" || v === "en") return v;
    }
  } catch {
    // ignore storage errors
  }
  return "bi";
}

export function writeStoredLangMode(mode: LangMode): void {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(LANG_STORAGE_KEY, mode);
    }
    const ch = getChromeLike();
    if (ch?.storage?.local?.set) {
      void ch.storage.local.set({ [LANG_STORAGE_KEY]: mode });
    }
  } catch {
    // ignore storage errors
  }
}
