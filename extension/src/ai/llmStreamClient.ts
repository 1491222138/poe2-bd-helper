/**
 * 浏览器扩展双轨 SSE 流式 AI 客户端：
 *   - 轨道 A（本地后端在线且开启 AI）：走 POST http://127.0.0.1:8788/llm/analyze/stream（附带 PoB2 引擎实算 + 消融数据）
 *   - 轨道 B（纯插件离线模式 / 独立使用）：用内置离线版本词典（patchDict）水合完整天赋与宝石原文，
 *     直接 fetch OpenAI 兼容接口（stream: true）实时流式输出，支持思考过程折叠与 AbortController 一键中断。
 */
import type {
  BuildPositioning,
  BuildProfileResponse,
  CopyBuildContext,
  CopyPlanItem,
  CopyPlanStructured,
  LlmAnalyzeMode,
  LlmStreamEvent,
  PriceSnapshot,
} from "@contracts";
import { DEFAULT_WORKBENCH_URL, workbenchClient } from "../bridge/workbenchClient";
import { analyzeItemDeeply } from "../parsers/affixAnalyzer";
import { loadPatchDict, lookupGemInfo } from "../parsers/patchDict";

export interface ExtensionLlmConfig {
  enabled: boolean;
  provider: "deepseek" | "gemini" | "openai" | "ollama" | "custom";
  baseUrl: string;
  apiKey: string;
  model: string;
  temperature: number;
  autoRunOnImport: boolean;
}

export const EXTENSION_LLM_PRESETS: Record<
  ExtensionLlmConfig["provider"],
  { label: string; baseUrl: string; model: string }
> = {
  deepseek: { label: "DeepSeek", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  gemini: {
    label: "Gemini (OpenAI 兼容)",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    model: "gemini-2.5-flash",
  },
  openai: { label: "OpenAI", baseUrl: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  ollama: { label: "本地 Ollama", baseUrl: "http://127.0.0.1:11434/v1", model: "qwen2.5:14b" },
  custom: { label: "自定义 OpenAI 兼容", baseUrl: "", model: "" },
};

export const DEFAULT_EXTENSION_LLM_CONFIG: ExtensionLlmConfig = {
  enabled: false,
  provider: "deepseek",
  baseUrl: EXTENSION_LLM_PRESETS.deepseek.baseUrl,
  apiKey: "",
  model: EXTENSION_LLM_PRESETS.deepseek.model,
  temperature: 0.3,
  autoRunOnImport: false,
};

const LLM_CFG_STORAGE_KEY = "poe2wb_ext_llm_cfg_v1";

export async function loadExtensionLlmConfig(): Promise<ExtensionLlmConfig> {
  try {
    if (typeof chrome !== "undefined" && chrome.storage?.local) {
      const res = await chrome.storage.local.get(LLM_CFG_STORAGE_KEY);
      const raw = res?.[LLM_CFG_STORAGE_KEY];
      if (raw && typeof raw === "object") {
        return { ...DEFAULT_EXTENSION_LLM_CONFIG, ...(raw as Partial<ExtensionLlmConfig>) };
      }
    } else if (typeof localStorage !== "undefined") {
      const raw = localStorage.getItem(LLM_CFG_STORAGE_KEY);
      if (raw) {
        return { ...DEFAULT_EXTENSION_LLM_CONFIG, ...(JSON.parse(raw) as Partial<ExtensionLlmConfig>) };
      }
    }
  } catch {
    // ignore
  }
  return { ...DEFAULT_EXTENSION_LLM_CONFIG };
}

export async function saveExtensionLlmConfig(cfg: ExtensionLlmConfig): Promise<void> {
  try {
    if (typeof chrome !== "undefined" && chrome.storage?.local) {
      await chrome.storage.local.set({ [LLM_CFG_STORAGE_KEY]: cfg });
    } else if (typeof localStorage !== "undefined") {
      localStorage.setItem(LLM_CFG_STORAGE_KEY, JSON.stringify(cfg));
    }
  } catch {
    // ignore
  }
}

export async function requestOriginPermissionIfNeeded(baseUrl: string): Promise<boolean> {
  try {
    if (typeof chrome === "undefined" || !chrome.permissions?.request) return true;
    const u = new URL(baseUrl);
    const originPattern = `${u.protocol}//${u.hostname}${u.port ? `:${u.port}` : ""}/*`;
    const has = await chrome.permissions.contains({ origins: [originPattern] });
    if (has) return true;
    return await chrome.permissions.request({ origins: [originPattern] });
  } catch {
    return true;
  }
}

const SYSTEM_PROMPT =
  "你是《流放之路 2》(Path of Exile 2) 资深 BD 构筑师与做装顾问。" +
  "你必须严格基于下方提供的 PoB2 解析数据、官方天赋与宝石原文说明（gemGlossary / passiveTree）、" +
  "装备原始属性（含 Ward / Spirit / LevelReq / socketCount / 符文 / synergyHints）与 Config 配置进行推理，严禁臆造不存在的装备或机制。" +
  "分析装备词条、推荐平替与半成品时必须深挖两类联动：" +
  "1) 词条↔天赋/辅助联动（如 Subterfuge Mask 头盔ES转闪避、Spectral Ward/Beastial Skin 胸甲高闪避、Lead me through Grace 胸甲防值转精魂且禁用装备精魂、Crimson Power 胸甲ES转血、Feathered Fletching/投射物加速 投速转伤害、Falcon Dive/亚马逊 命中转攻速暴击、CI/血魔法/必中/火之化身 剔除死词条、攻速工具武器砍低T点伤）；" +
  "2) 词条↔符文插槽联动（如单手/头手鞋2孔或胸甲/双手3孔超工匠石上限必须买卓越底子或瓦尔成品、镶嵌物效果增幅词条必须配多孔+高阶符文/灵核、Kolr's Hunt/Thrud's Might 等符文解锁 Marksman/Destruction 专属词池、是否点了 Wisdom of the Maji 决定 Bonded 词条是否生效、买完平替/半成品需补插哪颗符文）；" +
  "3) 基底自带技能与传奇装备边界：Absent/Lament/Portent Amulet、Shrine/Stoic/Omen/Rattling Sceptre、Chiming Staff 等特殊基底的 Grants Skill 属于核心功能，少了会有明显功能/光环损失，平替需锁基底；传奇/暗金装备（UNIQUE）不可用通货/精华/半成品重做词缀，仅能直买成品（挑 roll / 瓦尔额外孔或基底 / 插符文 / 涂油），严禁对暗金输出 craft_semi 或 buy_base。" +
  "同时严格区分「真短板」与「PoB 导出假红线」。";

export async function buildBrowserTargetDigest(profile: BuildProfileResponse): Promise<Record<string, unknown>> {
  const dict = await loadPatchDict();
  const build = profile.build;
  const tree = build.passiveTree;

  const compactItem = (it: BuildProfileResponse["build"]["equippedItems"][number]) => {
    const deep = analyzeItemDeeply(it, build);
    const hints: string[] = [];
    if (deep.isSpeedWeapon) hints.push("攻速/位移工具武器组");
    if (deep.requireBaseType && deep.baseTypeReason) hints.push(`必保基底: ${deep.baseTypeReason}`);
    if (deep.passiveSynergies.length) hints.push(`天赋联动: ${deep.passiveSynergies.slice(0, 3).join(", ")}`);
    if (deep.socketAnalysis.exceedsCraftableSockets) {
      hints.push(`超工匠石孔位上限(${deep.socketAnalysis.socketCount}/${deep.socketAnalysis.naturalMaxSockets}孔，需卓越底子或瓦尔多槽)`);
    }
    if (deep.socketAnalysis.hasSocketEffectMod) hints.push("含镶嵌物效果增幅(必须配多孔与高阶符文)");
    if (deep.socketAnalysis.runeUnlockedPools.length) {
      hints.push(`符文解锁专属词池: ${deep.socketAnalysis.runeUnlockedPools.join("/")}`);
    }
    return {
      slot: it.slot,
      rarity: it.rarity,
      name: it.name,
      baseType: it.baseType,
      ...(it.itemLevel ? { itemLevel: it.itemLevel } : {}),
      ...(it.levelReq ? { levelReq: it.levelReq } : {}),
      ...(it.socketCount ? { socketCount: it.socketCount } : {}),
      ...(it.corrupted ? { corrupted: true } : {}),
      ...(it.properties && Object.keys(it.properties).length > 0 ? { properties: it.properties } : {}),
      mods: (it.mods || []).slice(0, 12),
      ...(it.socketedRunes?.length ? { socketedRunes: it.socketedRunes } : {}),
      ...(hints.length > 0 ? { synergyHints: hints } : {}),
    };
  };

  const sortedGroups = [...build.skillGroups].sort((a, b) => (a.isMain ? -1 : b.isMain ? 1 : a.index - b.index));
  const gemGlossary: Record<string, { desc: string; stats?: string[] }> = {};
  const unknownGems: string[] = [];
  const compactGroups: Array<{ isMain: boolean; weaponSet: string; gems: string[] }> = [];

  for (const sg of sortedGroups.slice(0, 16)) {
    compactGroups.push({
      isMain: sg.isMain,
      weaponSet: sg.weaponSet || "",
      gems: sg.gems.map((g) => `${g.name}(${g.level}/${g.quality}%)`),
    });
    for (const g of sg.gems) {
      if (!g.name || gemGlossary[g.name]) continue;
      const info = lookupGemInfo(g.name, dict);
      if (info) {
        const desc = info.d.length > 200 ? `${info.d.slice(0, 197)}...` : info.d;
        gemGlossary[g.name] = info.c?.length ? { desc, stats: info.c } : { desc };
      } else if (!unknownGems.includes(g.name)) {
        unknownGems.push(g.name);
      }
    }
  }

  const ws1Names = new Set(tree.weaponSet1Notables.map((n) => n.name));
  const ws2Names = new Set(tree.weaponSet2Notables.map((n) => n.name));
  const sharedNotables = tree.notables
    .map((n) => n.name)
    .filter((n) => n && !ws1Names.has(n) && !ws2Names.has(n))
    .slice(0, 24);

  const xmlStats = build.character.stats || {};
  const keepKeys = [
    "CombinedDPS", "TotalDPS", "FullDPS", "AverageDamage", "Speed",
    "CritChance", "PreEffectiveCritChance", "CritMultiplier", "CritBifurcates",
    "TotalEHP", "Life", "LifeUnreserved", "EnergyShield", "Ward", "Mana",
    "Spirit", "SpiritUnreserved", "Evasion", "EvadeChance", "DeflectChance",
    "Armour", "EffectiveBlockChance", "FireResist", "ColdResist", "LightningResist",
    "ChaosResist", "PhysicalMaximumHitTaken", "ChaosMaximumHitTaken",
    "EffectiveMovementSpeedMod", "ManaCost", "ManaPerSecondCost", "ManaRegenRecovery",
    "Str", "Dex", "Int", "ReqStr", "ReqDex", "ReqInt",
  ];
  const compactStats: Record<string, number> = {};
  for (const k of keepKeys) {
    if (xmlStats[k] !== undefined) compactStats[k] = Number(xmlStats[k].toFixed(2));
  }

  return {
    character: {
      class: build.character.className,
      ascendancy: build.character.ascendancy,
      level: build.character.level,
      archetype: profile.positioning.archetype,
    },
    stats: profile.simulation || compactStats,
    config: build.config || {},
    passiveTree: {
      treeVersion: tree.treeVersion,
      totalAllocated: tree.totalAllocated,
      keystones: tree.keystones.map((n) => ({ name: n.name, stats: n.stats.slice(0, 4) })),
      ascendancyNodes: tree.ascendancyNodes.map((n) => ({ name: n.name, stats: n.stats.slice(0, 4) })),
      anointedNotables: tree.anointedNotables || [],
      weaponSet1Notables: tree.weaponSet1Notables.map((n) => ({ name: n.name, stats: n.stats.slice(0, 4) })),
      weaponSet2Notables: tree.weaponSet2Notables.map((n) => ({ name: n.name, stats: n.stats.slice(0, 4) })),
      sharedNotables,
    },
    equippedItems: build.equippedItems.map(compactItem),
    jewels: build.jewels.map(compactItem),
    flasksAndCharms: build.flasksAndCharms.map(compactItem),
    skillGroups: compactGroups,
    gemGlossary,
    ...(unknownGems.length > 0 ? { unknownGemsInDict: unknownGems } : {}),
    ...(profile.engineFacts ? { engineFacts: profile.engineFacts } : {}),
  };
}

function buildTargetPrompt(digest: Record<string, unknown>): string {
  return (
    "请对下方 PoE2 目标 BD 做一次【深度本质拆解】，严格按以下两段格式输出（不要在开头写多余寒暄）：\n\n" +
    "【角色能力评测硬性准则（严禁凭字面或静态阈值臆断，必须严格基于 PoB2 引擎实算数据评判）】\n" +
    "1. 技能形态、阶段、持续时间、投射物速度与覆盖率：核查主副技能的阶段/分段（skillPartName/skillStageCount）、持续时间（durationSec/durationMod）、投射物速度（projSpeedMod）、投射物数量/连锁/穿透、范围半径（aoeRadiusMetres）、蓄能印记与冷却/触发频率；\n" +
    "2. PoB 面板真实性核查与盲区：核查 enemyIsBoss、gemCount/includeInFullDPS、Config 勾选假设是否虚高，同时结合 unparsedMods 识别引擎未算的真实增伤；\n" +
    "3. 位移速度与清图机动：综合步行移速、施放中移速、行动速度、以及位移技能（如 Whirling Slash / Tempest Flurry / Shield Charge / Leap Slam / Blink）在 WS1/WS2 下的实算攻速或冷却，高攻速位移技能跑图严禁因步行移速低而误扣分；\n" +
    "4. 多维防御与一击线：严禁只看 TotalEHP，必须核查五系一击线、最大抗性、闪避/偏转/格挡、回复/偷取/护盾充能与晕眩阈值；\n" +
    "5. 分场景独立定级并引用 PoB2 实算关键数据。\n\n" +
    "第一段：输出一个紧凑合法的 ```json 代码块（不要在 JSON 里塞长正文），字段如下：\n" +
    "```json\n" +
    "{\n" +
    '  "archetype": "简短构筑名",\n' +
    '  "oneLineIdea": "一句话讲透核心循环、联动本质与真实防御体系（<=90字）",\n' +
    '  "defenseSystem": "一句话概括全部真实防御层（含血/ES/闪避/偏转/Ward/升华特殊层）",\n' +
    '  "scenarioTags": ["最多6个高信息密度标签"],\n' +
    '  "budgetTier": "league-start | mid | high | mirror 四选一",\n' +
    '  "coreAffixes": ["按真实重要性列出5~7个核心词缀/机制方向"],\n' +
    '  "gearTiers": {\n' +
    '    "mandatory": ["🔴机制锁死件"],\n' +
    '    "amplifier": ["🟡核心放大件"],\n' +
    '    "luxury": ["🟢顶配奢侈件"]\n' +
    "  },\n" +
    '  "pitfalls": ["最多6条真正有价值的抄作业暗坑，区分真红线、隐藏刚需门槛与PoB未解析/假红线"],\n' +
    '  "contentReadiness": {\n' +
    '    "overallTier": "一句话终局定位",\n' +
    '    "overallGrade": "S | A | B | C | D",\n' +
    '    "summary": "综合评价",\n' +
    '    "radar": [\n' +
    '      {"id": "clearSpeed", "label": "清图效率", "score": 90, "metric": "实算指标", "reason": "..."},\n' +
    '      {"id": "bossing", "label": "攻坚爆发", "score": 90, "metric": "实算指标", "reason": "..."},\n' +
    '      {"id": "mobility", "label": "机动位移", "score": 90, "metric": "实算指标", "reason": "..."},\n' +
    '      {"id": "survivability", "label": "生存硬度", "score": 75, "metric": "实算指标", "reason": "..."},\n' +
    '      {"id": "sustain", "label": "续航手感", "score": 80, "metric": "实算指标", "reason": "..."},\n' +
    '      {"id": "costEfficiency", "label": "造价性价比", "score": 50, "metric": "实算指标", "reason": "..."}\n' +
    "    ],\n" +
    '    "items": [\n' +
    '      {"id": "t15_mapping", "grade": "S", "verdict": "碾压速通", "reason": "..."},\n' +
    '      {"id": "juiced_t15_delirium", "grade": "S", "verdict": "碾压速通", "reason": "..."},\n' +
    '      {"id": "pinnacle_t0_t2", "grade": "S", "verdict": "碾压速通", "reason": "..."},\n' +
    '      {"id": "pinnacle_t4_uber", "grade": "S", "verdict": "碾压速通", "reason": "..."},\n' +
    '      {"id": "trials_sekhema_chaos", "grade": "A", "verdict": "稳健推荐", "reason": "..."}\n' +
    "    ]\n" +
    "  }\n" +
    "}\n" +
    "```\n\n" +
    "第二段：紧接一行 `---`，然后用清晰中文 Markdown 按以下 5 个小节流式输出深度本质拆解：\n" +
    "### 1. ⚙️ 核心输出循环与隐藏联动门槛\n" +
    "### 2. 📈 伤害乘区解剖与 PoB2 面板盲区\n" +
    "### 3. 🛡️ 防御体系与真假红线甄别\n" +
    "### 4. 🌳 天赋树、升华与珠宝骨架\n" +
    "### 5. 💎 装备三档分级、词条↔天赋/符文联动与半成品逻辑\n\n" +
    `PoB2 带原文事实包 JSON：\n${JSON.stringify(digest)}`
  );
}

function buildCopyPrompt(
  digest: Record<string, unknown>,
  ctx: CopyBuildContext,
  priceSnapshot?: PriceSnapshot | null,
  essenceSummary = ""
): string {
  const lvl = ctx.characterLevel ?? 90;
  const targetLvl = Number((digest.character as Record<string, unknown>)?.level || 100);
  const missingPts = Math.max(0, targetLvl - lvl);
  const items = [
    ...((digest.equippedItems as Array<Record<string, unknown>>) || []),
    ...((digest.flasksAndCharms as Array<Record<string, unknown>>) || []),
  ];
  const levelBlocked = items
    .filter((it) => Number(it.levelReq || 0) > lvl)
    .map((it) => `${it.slot}: ${it.name || it.baseType} (需 Lv.${it.levelReq})`);

  return (
    "玩家已看懂目标 BD 本质，请结合【玩家当前等级、预算(D)、已有装备】按两段格式输出可执行的【定制抄作业方案】（不要多余寒暄）：\n\n" +
    "第一段：输出紧凑合法的 ```json 代码块，含 budgetSummary 与 4~8 件核心采购清单 shoppingList：\n" +
    "```json\n" +
    "{\n" +
    '  "budgetSummary": "一句话概括当前预算分配与取舍重点",\n' +
    '  "shoppingList": [\n' +
    '    {"slot":"Weapon 1","action":"buy_unique|buy_finished|buy_budget|craft_semi|buy_base|skip","priority":1,"itemName":"英文名","baseType":"英文基底","rarity":"UNIQUE|RARE","budgetDivine":4,"minIlvl":81,"minSockets":2,"corrupted":false,"lockBase":false,"reason":"选词与取舍说明","selectedAffixes":[{"text":"精确PoE2英文词缀","zh":"中文名","min":80,"required":true}],"cutAffixes":["可砍词条"],"socketTip":""}\n' +
    "  ]\n" +
    "}\n" +
    "```\n\n" +
    "第二段：紧接一行 `---`，然后用中文 Markdown 分 5 块输出：\n" +
    "### 1. 💰 当前预算逐笔分配清单\n" +
    "### 2. 🔄 超预算天价件 & 奢侈件平替 / 半成品方案（写明基底、必保词条、天赋/辅助联动、孔位上限与需补插符文）\n" +
    "### 3. 🚨 绝不能砍的机制红线与低配变通（若买不起某门槛件，必须同步改哪颗辅助/天赋防失效）\n" +
    `### 4. 🌳 等级门槛拦截与少 ${missingPts} 点天赋裁剪建议\n` +
    "### 5. 🚀 下一档预算升级阶梯\n\n" +
    `玩家条件：${JSON.stringify({
      myLevel: lvl,
      targetLevel: targetLvl,
      missingPassivePoints: missingPts,
      budgetDivine: ctx.budgetDivine ?? 15,
      ownedItems: ctx.ownedItems || [],
      goalNote: ctx.goalNote || "",
      levelBlockedItems: levelBlocked,
      marketPrices: priceSnapshot?.prices || {},
    })}\n` +
    (essenceSummary ? `\n前序本质拆解要点：\n${essenceSummary.slice(0, 1500)}\n` : "") +
    `\n目标 BD 事实包 JSON：\n${JSON.stringify(digest)}`
  );
}

function buildConsultPrompt(ctx: CopyBuildContext): string {
  return (
    "玩家目前没有 PoB2 分享码，请按以下条件直接输出《流放之路 2》实战可落地的构筑起步与抄作业指南：\n" +
    `- 意向职业/升华：${ctx.freeformQuery?.ascendancy || "未指定"}\n` +
    `- 意向玩法/核心技能：${ctx.freeformQuery?.skillIdea || "未指定"}\n` +
    `- 当前等级：Lv.${ctx.characterLevel ?? 80}\n` +
    `- 当前预算：${ctx.budgetDivine ?? 5} Divine\n` +
    `- 已有核心件：${(ctx.ownedItems || []).join("、") || "无"}\n` +
    `- 额外需求：${ctx.goalNote || "无"}\n\n` +
    "请分 5 块用中文 Markdown 输出：\n" +
    "### 1. ⚙️ 核心机制与技能连法骨架\n" +
    "### 2. 💰 当前预算装备分配与平替词缀\n" +
    "### 3. 🛡️ 防御路线与抗性/精魂/属性红线\n" +
    "### 4. 🌳 核心升华顺序与当前等级天赋重心\n" +
    "### 5. 🚀 下一档预算升级阶梯\n"
  );
}

const JSON_BLOCK_RE = /```(?:json)?\s*(\{[\s\S]*?\})\s*```/i;

export function parseStructuredHeaderFromMarkdown(raw: string): {
  structured: Record<string, unknown> | null;
  markdown: string;
} {
  const s = (raw || "").trim();
  const m = JSON_BLOCK_RE.exec(s);
  if (m) {
    let parsed: Record<string, unknown> | null = null;
    try {
      const obj = JSON.parse(m[1]);
      if (obj && typeof obj === "object" && !Array.isArray(obj)) parsed = obj as Record<string, unknown>;
    } catch {
      parsed = null;
    }
    let after = s.slice(m.index + m[0].length).trimStart();
    if (after.startsWith("---")) after = after.slice(3).trimStart();
    if (parsed) {
      const fallbackMd = typeof parsed.analysisMarkdown === "string" ? parsed.analysisMarkdown : "";
      return { structured: parsed, markdown: (after || fallbackMd).trim() };
    }
  }
  return { structured: null, markdown: s };
}

const RADAR_AXIS_LABELS: Array<{
  id: "clearSpeed" | "bossing" | "mobility" | "survivability" | "sustain" | "costEfficiency";
  label: string;
}> = [
  { id: "clearSpeed", label: "清图效率" },
  { id: "bossing", label: "攻坚爆发" },
  { id: "mobility", label: "机动位移" },
  { id: "survivability", label: "生存硬度" },
  { id: "sustain", label: "续航手感" },
  { id: "costEfficiency", label: "造价性价比" },
];

const CONTENT_ITEM_LABELS: Array<{
  id:
    | "t15_mapping"
    | "juiced_t15_delirium"
    | "pinnacle_t0_t2"
    | "pinnacle_t4_uber"
    | "trials_sekhema_chaos";
  label: string;
}> = [
  { id: "t15_mapping", label: "T15 常规速刷" },
  { id: "juiced_t15_delirium", label: "T15+ 高危/迷雾/裂隙" },
  { id: "pinnacle_t0_t2", label: "终局首领 (T0~T2)" },
  { id: "pinnacle_t4_uber", label: "终局满难 Boss (T4)" },
  { id: "trials_sekhema_chaos", label: "圣所/混沌试炼" },
];

const GRADE_VERDICT_MAP: Record<string, string> = {
  S: "碾压速通",
  A: "稳健推荐",
  B: "可打需操作",
  C: "吃力易暴毙",
  D: "红线卡住/不推荐",
};

export function mergeStructuredIntoPositioning(
  base: BuildPositioning,
  structured: Record<string, unknown>
): BuildPositioning {
  const next: BuildPositioning = { ...base, analyzedBy: "ai" };
  if (typeof structured.archetype === "string" && structured.archetype.trim()) next.archetype = structured.archetype.trim();
  if (typeof structured.oneLineIdea === "string" && structured.oneLineIdea.trim()) next.oneLineIdea = structured.oneLineIdea.trim();
  if (typeof structured.defenseSystem === "string" && structured.defenseSystem.trim()) next.defenseSystem = structured.defenseSystem.trim();
  if (Array.isArray(structured.scenarioTags) && structured.scenarioTags.length) {
    next.scenarioTags = structured.scenarioTags.map(String).slice(0, 6);
  }
  if (Array.isArray(structured.coreAffixes) && structured.coreAffixes.length) {
    next.coreAffixes = structured.coreAffixes.map(String).slice(0, 7);
  }
  if (Array.isArray(structured.pitfalls) && structured.pitfalls.length) {
    next.pitfalls = structured.pitfalls.map(String).slice(0, 6);
  }
  if (
    typeof structured.budgetTier === "string" &&
    ["league-start", "mid", "high", "mirror"].includes(structured.budgetTier)
  ) {
    next.budgetTier = structured.budgetTier as BuildPositioning["budgetTier"];
  }
  if (structured.gearTiers && typeof structured.gearTiers === "object") {
    const gt = structured.gearTiers as Record<string, unknown>;
    next.gearTiers = {
      mandatory: Array.isArray(gt.mandatory) ? gt.mandatory.map(String).slice(0, 10) : [],
      amplifier: Array.isArray(gt.amplifier) ? gt.amplifier.map(String).slice(0, 10) : [],
      luxury: Array.isArray(gt.luxury) ? gt.luxury.map(String).slice(0, 10) : [],
    };
  }
  if (structured.contentReadiness && typeof structured.contentReadiness === "object") {
    const cr = structured.contentReadiness as Record<string, unknown>;
    const byId = new Map<string, Record<string, unknown>>();
    if (Array.isArray(cr.items)) {
      for (const it of cr.items) {
        if (it && typeof it === "object" && typeof (it as Record<string, unknown>).id === "string") {
          byId.set(String((it as Record<string, unknown>).id), it as Record<string, unknown>);
        }
      }
    }
    const prevById = new Map((base.contentReadiness?.items || []).map((x) => [x.id, x]));
    const items = CONTENT_ITEM_LABELS.map(({ id, label }) => {
      const ov = byId.get(id);
      const prev = prevById.get(id);
      if (ov) {
        const g =
          typeof ov.grade === "string" && ["S", "A", "B", "C", "D"].includes(ov.grade.toUpperCase())
            ? (ov.grade.toUpperCase() as "S" | "A" | "B" | "C" | "D")
            : prev?.grade || "B";
        return {
          id,
          label: typeof ov.label === "string" && ov.label.trim() ? ov.label.trim() : label,
          grade: g,
          verdict:
            typeof ov.verdict === "string" && ov.verdict.trim()
              ? ov.verdict.trim()
              : GRADE_VERDICT_MAP[g] || "可打",
          reason: typeof ov.reason === "string" ? ov.reason.trim() : prev?.reason || "",
        };
      }
      return prev || null;
    }).filter((x): x is NonNullable<typeof x> => Boolean(x));

    const radarById = new Map<string, { id: typeof RADAR_AXIS_LABELS[number]["id"]; label: string; score: number; metric?: string; reason: string }>();
    if (Array.isArray(cr.radar)) {
      for (const r of cr.radar) {
        if (!r || typeof r !== "object") continue;
        const ro = r as Record<string, unknown>;
        const rid = String(ro.id || "").trim();
        const meta = RADAR_AXIS_LABELS.find((x) => x.id === rid);
        if (!meta) continue;
        const scNum = Number(ro.score);
        if (!Number.isFinite(scNum)) continue;
        radarById.set(rid, {
          id: meta.id,
          label: typeof ro.label === "string" && ro.label.trim() ? ro.label.trim() : meta.label,
          score: Math.max(0, Math.min(100, Math.round(scNum))),
          metric: typeof ro.metric === "string" ? ro.metric.trim() : undefined,
          reason: typeof ro.reason === "string" ? ro.reason.trim() : "",
        });
      }
    }
    const radar = RADAR_AXIS_LABELS.map((x) => radarById.get(x.id)).filter(
      (x): x is NonNullable<typeof x> => Boolean(x)
    );

    const rawGrade = typeof cr.overallGrade === "string" ? cr.overallGrade.trim().toUpperCase() : "";
    const overallGrade = ["S", "A", "B", "C", "D"].includes(rawGrade)
      ? (rawGrade as "S" | "A" | "B" | "C" | "D")
      : base.contentReadiness?.overallGrade || items[0]?.grade;

    if (overallGrade || radar.length > 0) {
      const avg = radar.length > 0 ? radar.reduce((s, r) => s + r.score, 0) / radar.length : 75;
      const finalGrade =
        overallGrade || (avg >= 88 ? "S" : avg >= 75 ? "A" : avg >= 60 ? "B" : avg >= 45 ? "C" : "D");
      next.contentReadiness = {
        overallTier:
          typeof cr.overallTier === "string" && cr.overallTier.trim()
            ? cr.overallTier.trim()
            : base.contentReadiness?.overallTier || `${finalGrade} 级构筑`,
        overallGrade: finalGrade,
        confidence: "ai",
        summary:
          typeof cr.summary === "string" && cr.summary.trim()
            ? cr.summary.trim()
            : base.contentReadiness?.summary || "",
        ...(radar.length > 0 ? { radar } : base.contentReadiness?.radar ? { radar: base.contentReadiness.radar } : {}),
        items,
        evaluatedAt: Date.now(),
      };
    }
  }
  return next;
}

export interface StreamHandlers {
  onMeta?: (info: { model: string; cached?: boolean; mode?: LlmAnalyzeMode }) => void;
  onReasoningDelta?: (delta: string) => void;
  onDelta?: (delta: string) => void;
  onStructured?: (
    structured: Record<string, unknown>,
    positioning?: BuildPositioning,
    copyPlan?: CopyPlanStructured | null
  ) => void;
  onDone?: (result: {
    text: string;
    reasoning: string;
    model: string;
    cached?: boolean;
    structured?: Record<string, unknown> | null;
    positioning?: BuildPositioning | null;
    copyPlan?: CopyPlanStructured | null;
  }) => void;
}

async function consumeBackendSseStream(
  response: Response,
  handlers: StreamHandlers
): Promise<void> {
  if (!response.body) throw new Error("后端流式响应体为空");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() || "";
    for (const frame of frames) {
      const dataLines = frame
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trim());
      if (dataLines.length === 0) continue;
      let ev: LlmStreamEvent;
      try {
        ev = JSON.parse(dataLines.join("\n")) as LlmStreamEvent;
      } catch {
        continue;
      }
      if (ev.type === "meta") handlers.onMeta?.({ model: ev.model, cached: ev.cached, mode: ev.mode });
      else if (ev.type === "reasoning") handlers.onReasoningDelta?.(ev.delta);
      else if (ev.type === "delta") handlers.onDelta?.(ev.delta);
      else if (ev.type === "structured") handlers.onStructured?.(ev.structured, ev.positioning, ev.copyPlan);
      else if (ev.type === "error") throw new Error(ev.error || "流式分析失败");
      else if (ev.type === "done") {
        handlers.onDone?.({
          text: ev.text,
          reasoning: ev.reasoning || "",
          model: ev.model,
          cached: ev.cached,
          structured: ev.structured,
          positioning: ev.positioning,
          copyPlan: ev.copyPlan,
        });
      }
    }
  }
}

function normalizeOfflineCopyPlan(
  structured: Record<string, unknown>,
  ctx?: CopyBuildContext,
  league = "Standard"
): CopyPlanStructured | null {
  const rawList = Array.isArray(structured.shoppingList)
    ? structured.shoppingList
    : Array.isArray(structured.items)
    ? structured.items
    : [];
  const items: CopyPlanItem[] = [];
  rawList.forEach((raw, idx) => {
    if (!raw || typeof raw !== "object") return;
    const obj = raw as Record<string, unknown>;
    const slot = String(obj.slot || "").trim();
    const itemName = String(obj.itemName || obj.name || "").trim();
    if (!slot && !itemName) return;
    const rarity = String(obj.rarity || "RARE").toUpperCase();
    const rawAction = String(obj.action || (rarity === "UNIQUE" ? "buy_unique" : "buy_budget")).toLowerCase();
    const validActions = ["buy_unique", "buy_finished", "buy_budget", "craft_semi", "buy_base", "skip"];
    let action = (validActions.includes(rawAction) ? rawAction : "buy_budget") as CopyPlanItem["action"];
    if (rarity === "UNIQUE" && (action === "craft_semi" || action === "buy_base")) {
      action = "buy_unique";
    }
    const rawAffixes = Array.isArray(obj.selectedAffixes) ? obj.selectedAffixes : [];
    const selectedAffixes = rawAffixes
      .map((a) => {
        if (typeof a === "string") return { text: a, required: true };
        if (a && typeof a === "object") {
          const ao = a as Record<string, unknown>;
          return {
            text: String(ao.text || ao.line || "").trim(),
            zh: typeof ao.zh === "string" ? ao.zh.trim() : undefined,
            min: typeof ao.min === "number" ? ao.min : null,
            required: ao.required !== false,
          };
        }
        return null;
      })
      .filter((x): x is NonNullable<typeof x> => Boolean(x && (x.text || x.zh)));

    items.push({
      slot: slot || "Gear",
      action,
      priority: Number(obj.priority) || idx + 1,
      itemName: itemName || String(obj.baseType || slot),
      baseType: typeof obj.baseType === "string" ? obj.baseType.trim() : "",
      rarity,
      budgetDivine: typeof obj.budgetDivine === "number" ? obj.budgetDivine : null,
      minIlvl: typeof obj.minIlvl === "number" ? obj.minIlvl : null,
      minSockets: typeof obj.minSockets === "number" ? obj.minSockets : null,
      corrupted: typeof obj.corrupted === "boolean" ? obj.corrupted : null,
      lockBase: typeof obj.lockBase === "boolean" ? obj.lockBase : action === "craft_semi" || action === "buy_base",
      reason: String(obj.reason || "").trim(),
      selectedAffixes,
      cutAffixes: Array.isArray(obj.cutAffixes) ? obj.cutAffixes.map(String).filter(Boolean) : [],
      socketTip: typeof obj.socketTip === "string" ? obj.socketTip.trim() : "",
    });
  });

  if (items.length === 0) return null;
  return {
    budgetSummary: typeof structured.budgetSummary === "string" ? structured.budgetSummary.trim() : "",
    budgetDivine: ctx?.budgetDivine ?? null,
    characterLevel: ctx?.characterLevel ?? null,
    league,
    items,
  };
}

async function streamDirectOpenAi(
  cfg: ExtensionLlmConfig,
  mode: LlmAnalyzeMode,
  userPrompt: string,
  basePositioning: BuildPositioning | null,
  handlers: StreamHandlers,
  signal?: AbortSignal,
  copyContext?: CopyBuildContext,
  league = "Standard"
): Promise<void> {
  const baseUrl = (cfg.baseUrl || "").trim().replace(/\/+$/, "");
  if (!baseUrl) throw new Error("请先在扩展「⚙️ 设置」中填写大模型 API Base URL");
  const model = (cfg.model || "").trim();
  if (!model) throw new Error("请先在扩展「⚙️ 设置」中填写模型名称");
  const url = baseUrl.endsWith("/chat/completions") ? baseUrl : `${baseUrl}/chat/completions`;
  const isLocal = url.includes("127.0.0.1") || url.includes("localhost");
  if (!cfg.apiKey && cfg.provider !== "ollama" && !isLocal) {
    throw new Error("请先在扩展「⚙️ 设置」中填写大模型 API Key（或启动本地桌面端）");
  }

  await requestOriginPermissionIfNeeded(url);

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream, application/json",
  };
  if (cfg.apiKey) headers.Authorization = `Bearer ${cfg.apiKey}`;

  const res = await fetch(url, {
    method: "POST",
    headers,
    signal,
    body: JSON.stringify({
      model,
      temperature: cfg.temperature ?? 0.3,
      stream: true,
      messages: [
        { role: "system", content: SYSTEM_PROMPT },
        { role: "user", content: userPrompt },
      ],
    }),
  });

  if (!res.ok) {
    const errTxt = (await res.text().catch(() => "")).slice(0, 300);
    throw new Error(`大模型接口返回 HTTP ${res.status}：${errTxt}`);
  }

  handlers.onMeta?.({ model, cached: false, mode });

  let rawAll = "";
  let reasoningAll = "";
  let headerEmitted = false;
  let mdSentLen = 0;
  let structuredObj: Record<string, unknown> | null = null;
  let mergedPos: BuildPositioning | null = basePositioning;
  let copyPlanObj: CopyPlanStructured | null = null;
  const hasStructuredHeader = mode === "target" || mode === "copy";

  const pushContentPiece = (piece: string) => {
    rawAll += piece;
    if (!hasStructuredHeader) {
      handlers.onDelta?.(piece);
      return;
    }
    if (!headerEmitted) {
      const m = JSON_BLOCK_RE.exec(rawAll);
      if (m) {
        try {
          const parsed = JSON.parse(m[1]);
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
            structuredObj = parsed as Record<string, unknown>;
            if (mode === "target") {
              mergedPos = basePositioning ? mergeStructuredIntoPositioning(basePositioning, structuredObj) : null;
              handlers.onStructured?.(structuredObj, mergedPos || undefined);
            } else {
              copyPlanObj = normalizeOfflineCopyPlan(structuredObj, copyContext, league);
              if (copyPlanObj) handlers.onStructured?.(structuredObj, undefined, copyPlanObj);
            }
          }
        } catch {
          // ignore
        }
        headerEmitted = true;
        let after = rawAll.slice(m.index + m[0].length).trimStart();
        if (after.startsWith("---")) after = after.slice(3).trimStart();
        if (after) {
          mdSentLen = after.length;
          handlers.onDelta?.(after);
        }
      } else if (rawAll.length > (mode === "copy" ? 4500 : 2200) && !rawAll.slice(0, 250).includes("```")) {
        headerEmitted = true;
        mdSentLen = rawAll.length;
        handlers.onDelta?.(rawAll);
      }
    } else {
      const m = JSON_BLOCK_RE.exec(rawAll);
      let after = m ? rawAll.slice(m.index + m[0].length).trimStart() : rawAll;
      if (after.startsWith("---")) after = after.slice(3).trimStart();
      if (after.length > mdSentLen) {
        handlers.onDelta?.(after.slice(mdSentLen));
        mdSentLen = after.length;
      }
    }
  };

  const ctype = (res.headers.get("content-type") || "").toLowerCase();
  if (ctype.includes("application/json") && !ctype.includes("text/event-stream")) {
    const obj = await res.json();
    const msg = obj?.choices?.[0]?.message || {};
    if (msg.reasoning_content || msg.reasoning) {
      const r = String(msg.reasoning_content || msg.reasoning);
      reasoningAll += r;
      handlers.onReasoningDelta?.(r);
    }
    if (msg.content) pushContentPiece(String(msg.content));
  } else if (res.body) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop() || "";
      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line.startsWith("data:")) continue;
        const payload = line.slice(5).trim();
        if (payload === "[DONE]") break;
        try {
          const chunk = JSON.parse(payload);
          const delta = chunk?.choices?.[0]?.delta || chunk?.choices?.[0]?.message || {};
          const rDelta = delta.reasoning_content || delta.reasoning;
          if (typeof rDelta === "string" && rDelta) {
            reasoningAll += rDelta;
            handlers.onReasoningDelta?.(rDelta);
          }
          if (typeof delta.content === "string" && delta.content) {
            pushContentPiece(delta.content);
          }
          const fr = chunk?.choices?.[0]?.finish_reason;
          if (fr === "stop" || fr === "length") break;
        } catch {
          // ignore partial json
        }
      }
    }
  }

  if (mode === "target") {
    const { structured, markdown } = parseStructuredHeaderFromMarkdown(rawAll);
    if (structured && !structuredObj) {
      structuredObj = structured;
      mergedPos = basePositioning ? mergeStructuredIntoPositioning(basePositioning, structuredObj) : null;
      handlers.onStructured?.(structuredObj, mergedPos || undefined);
    }
    if (markdown.length > mdSentLen) {
      handlers.onDelta?.(markdown.slice(mdSentLen));
    }
    handlers.onDone?.({
      text: markdown,
      reasoning: reasoningAll,
      model,
      cached: false,
      structured: structuredObj,
      positioning: mergedPos,
    });
  } else if (mode === "copy") {
    const { structured, markdown } = parseStructuredHeaderFromMarkdown(rawAll);
    if (structured && !copyPlanObj) {
      structuredObj = structured;
      copyPlanObj = normalizeOfflineCopyPlan(structured, copyContext, league);
      if (copyPlanObj) handlers.onStructured?.(structured, undefined, copyPlanObj);
    }
    if (markdown.length > mdSentLen) {
      handlers.onDelta?.(markdown.slice(mdSentLen));
    }
    handlers.onDone?.({
      text: markdown,
      reasoning: reasoningAll,
      model,
      cached: false,
      structured: structuredObj,
      copyPlan: copyPlanObj,
    });
  } else {
    handlers.onDone?.({
      text: rawAll.trim(),
      reasoning: reasoningAll,
      model,
      cached: false,
    });
  }
}

export async function runDualTrackStreamAnalysis(params: {
  mode: LlmAnalyzeMode;
  backendOnline: boolean;
  league?: string;
  pobCode?: string;
  profile?: BuildProfileResponse | null;
  copyContext?: CopyBuildContext;
  priceSnapshot?: PriceSnapshot | null;
  essenceSummary?: string;
  handlers: StreamHandlers;
  signal?: AbortSignal;
}): Promise<void> {
  const { mode, backendOnline, league, pobCode, profile, copyContext, priceSnapshot, essenceSummary, handlers, signal } = params;

  // 轨道 A：本地后端在线且桌面端已配置 AI 时，优先走后端流式接口（含 PoB2 引擎实算 + 消融）
  if (backendOnline) {
    try {
      const cfgRes = await fetch(`${DEFAULT_WORKBENCH_URL}/llm/config`);
      const cfgJson = cfgRes.ok ? await cfgRes.json() : null;
      if (cfgJson?.ok && cfgJson?.config?.enabled) {
        const token = await workbenchClient.ensureToken();
        const resp = await fetch(`${DEFAULT_WORKBENCH_URL}/llm/analyze/stream`, {
          method: "POST",
          signal,
          headers: {
            "Content-Type": "application/json",
            "X-Auth-Token": token,
          },
          body: JSON.stringify({
            mode,
            league: league || priceSnapshot?.league || "Standard",
            pobCode: pobCode || "",
            profile: profile || undefined,
            copyContext,
            essenceSummary: essenceSummary || "",
            runSimulation: Boolean(pobCode),
          }),
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err?.error || `后端流式请求失败 (HTTP ${resp.status})`);
        }
        await consumeBackendSseStream(resp, handlers);
        return;
      }
    } catch (err) {
      // 如果用户主动中止直接抛出；如果后端未配置 AI 则平滑降级至轨道 B（扩展内置 AI）
      if ((err as Error).name === "AbortError") throw err;
      const extCfg = await loadExtensionLlmConfig();
      if (!extCfg.enabled) throw err;
    }
  }

  // 轨道 B：纯插件独立模式（浏览器直连大模型流式）
  const extCfg = await loadExtensionLlmConfig();
  if (!extCfg.enabled) {
    throw new Error("尚未启用 AI：请点击右上角「⚙️ 设置」配置大模型 API Key（或启动已配好 AI 的本地桌面端）");
  }

  if (mode === "consult") {
    const prompt = buildConsultPrompt(copyContext || {});
    await streamDirectOpenAi(extCfg, "consult", prompt, null, handlers, signal, copyContext, league || "Standard");
    return;
  }

  if (!profile) {
    throw new Error("请先导入一个目标 BD");
  }
  const digest = await buildBrowserTargetDigest(profile);
  const prompt =
    mode === "copy"
      ? buildCopyPrompt(digest, copyContext || {}, priceSnapshot, essenceSummary)
      : buildTargetPrompt(digest);
  await streamDirectOpenAi(extCfg, mode, prompt, profile.positioning, handlers, signal, copyContext, league || "Standard");
}
