import { describe, expect, it } from "vitest";
import {
  buildHtcLinkFromClipboardTexts,
  buildHtcLinkFromParsedItem,
  buildNativePoe2HtcShareParam,
  buildPoe2PasteTextFromPayload,
  decodeHtcHandoffHash,
  parsePoe2ItemText,
  resolveHtcBaseId,
  type ParsedBuildData,
  type ParsedItem,
} from "@contracts";
import { buildDeepLinkUrl, serializeHandoffBundle } from "../bridge/handoff";
import { buildPriceSnapshot } from "../market/pricePackager";
import {
  buildStatsIndex,
  buildTradeQuery,
  computeRobustDivinePrice,
  matchItemModsWithIndex,
} from "../market/tradeBuilder";
import { buildLocalProfile, classifyWeaponSet } from "../parsers/localProfile";
import { buildOfflineThreeWayDecision, evaluateLocalCopyPlan } from "../parsers/budgetPlannerLocal";
import { hydratePassiveTree, isDictOutdatedForTree, type PatchDictData } from "../parsers/patchDict";
import { mergeStructuredIntoPositioning, parseStructuredHeaderFromMarkdown } from "../ai/llmStreamClient";
import { normalizeRawFetchUrl, resolveSourceInput } from "../parsers/sourceResolver";

const SAMPLE_STATS = [
  {
    id: "explicit",
    entries: [
      { id: "explicit.stat_1", text: "+# to Level of all Projectile Skills" },
      { id: "explicit.stat_2", text: "#% increased Attack Speed (Local)" },
      { id: "explicit.stat_3", text: "Adds # to # Physical Damage (Local)" },
      { id: "explicit.stat_4", text: "+# to maximum Life" },
      { id: "explicit.stat_5", text: "Gain #% of Damage as Extra Lightning Damage per 100 maximum Life" },
    ],
  },
  {
    id: "rune",
    entries: [{ id: "rune.stat_1", text: "+#% to Fire Resistance" }],
  },
];

const SAMPLE_SPEAR: ParsedItem = {
  slot: "Weapon 1",
  itemId: "1",
  rarity: "RARE",
  name: "Storm Skewer",
  baseType: "Winged Spear",
  corrupted: false,
  socketCount: 2,
  socketedRunes: ["+12% to Fire Resistance"],
  mods: [
    "+4 to Level of all Projectile Skills",
    "24% increased Attack Speed",
    "Adds 60 to 120 Physical Damage",
    "+85 to maximum Life",
    "Gain 3% of Damage as Extra Lightning Damage per 100 maximum Life",
    "+12% to Fire Resistance (rune)",
  ],
  rawText: "",
};

describe("sourceResolver", () => {
  it("normalizes pobb.in and pastebin URLs to raw endpoints", () => {
    expect(normalizeRawFetchUrl("https://pobb.in/AbCd1234")).toBe("https://pobb.in/AbCd1234/raw");
    expect(normalizeRawFetchUrl("https://pastebin.com/XyZ987")).toBe("https://pastebin.com/raw/XyZ987");
    expect(normalizeRawFetchUrl("not-a-url")).toBeNull();
  });

  it("resolves pobb.in link via injected fetcher", async () => {
    const res = await resolveSourceInput("https://pobb.in/demo123", async (url) => {
      expect(url).toBe("https://pobb.in/demo123/raw");
      return { ok: true, text: "eNrtMockPobCode" };
    });
    expect(res.pobCode).toBe("eNrtMockPobCode");
    expect(res.sourceUrl).toBe("https://pobb.in/demo123");
  });
});

describe("tradeBuilder", () => {
  const index = buildStatsIndex(SAMPLE_STATS);

  it("matches native mods, strips rune mods, and handles fixed-constant regex stats", () => {
    const rows = matchItemModsWithIndex(SAMPLE_SPEAR, index, "exact");
    expect(rows.some((r) => r.raw.includes("(rune)"))).toBe(false);

    const extra = rows.find((r) => r.statId === "explicit.stat_5");
    expect(extra).toBeDefined();
    expect(extra?.originalValue).toBe(3); // 精确捕获 3%，而不是把固定常量 100 平均进来

    const speed = rows.find((r) => r.statId === "explicit.stat_2");
    expect(speed?.matched).toBe(true);
    expect(speed?.min).toBe(21); // exact -10%: floor(24 * 0.9) = 21
  });

  it("builds distinct queries for exact, budget, and semi (non-corrupted) presets", () => {
    const exactRows = matchItemModsWithIndex(SAMPLE_SPEAR, index, "exact");
    const exactQ = buildTradeQuery(SAMPLE_SPEAR, exactRows, { kind: "exact" }) as {
      query: { type?: string; filters?: Record<string, unknown>; stats: Array<{ filters: unknown[] }> };
    };
    expect(exactQ.query.type).toBe("Winged Spear");
    expect(exactQ.query.filters?.equipment_filters).toBeDefined();
    expect(exactQ.query.stats[0].filters.length).toBeGreaterThanOrEqual(3);

    const budgetRows = matchItemModsWithIndex(SAMPLE_SPEAR, index, "budget");
    const budgetQ = buildTradeQuery(SAMPLE_SPEAR, budgetRows, { kind: "budget" }) as {
      query: { type?: string; filters?: { type_filters?: { filters?: { category?: { option: string } } } } };
    };
    expect(budgetQ.query.type).toBeUndefined();
    expect(budgetQ.query.filters?.type_filters?.filters?.category?.option).toBe("weapon.spear");

    const semiRows = matchItemModsWithIndex(SAMPLE_SPEAR, index, "semi");
    const semiQ = buildTradeQuery(SAMPLE_SPEAR, semiRows, { kind: "semi" }) as {
      query: {
        filters?: { misc_filters?: { filters?: { corrupted?: { option: string } } } };
        stats: Array<{ filters: unknown[] }>;
      };
    };
    expect(semiQ.query.stats[0].filters.length).toBe(2);
    expect(semiQ.query.filters?.misc_filters?.filters?.corrupted?.option).toBe("false");
  });

  it("skips lowest price-fixer listing when computing robust median price", () => {
    const price = computeRobustDivinePrice(
      [
        { amount: 1, currency: "divine" }, // 压价钓鱼单，应跳过
        { amount: 10, currency: "divine" },
        { amount: 12, currency: "divine" },
        { amount: 14, currency: "divine" },
      ],
      null
    );
    expect(price).toBe(12);
  });
});

describe("pricePackager & localProfile & handoff", () => {
  it("packages exact/budget/semi quotes into contract PriceSnapshot", () => {
    const snap = buildPriceSnapshot(
      "Dawn of the Hunt",
      [{ group: "珠宝", slot: "Tree Jewel", name: "Heart of the Well", divineValue: 20, formatted: "20 D" }],
      [
        {
          slot: "Weapon 1",
          kind: "budget",
          divineValue: 4.5,
          formatted: "4.5 D",
          totalListings: 18,
          modeLabel: "平替 (-25%)",
          webUrl: "https://www.pathofexile.com/trade2/search/poe2/test",
          samples: ["4 divine"],
          queriedAt: 1000,
        },
        {
          slot: "Weapon 1",
          kind: "semi",
          divineValue: 1.5,
          formatted: "1.5 D",
          totalListings: 32,
          modeLabel: "半成品底子 (非腐化)",
          webUrl: "https://www.pathofexile.com/trade2/search/poe2/semi",
          samples: ["1.5 divine"],
          queriedAt: 1001,
        },
      ]
    );
    expect(snap.league).toBe("Dawn of the Hunt");
    expect(snap.prices["Tree Jewel"]).toBe(20);
    expect(snap.prices["Weapon 1"]).toBe(4.5);
    expect(snap.entries).toHaveLength(3);
    expect(snap.entries?.map((e) => e.kind)).toEqual(["exact", "budget", "semi"]);
  });

  it("extracts dual weapon set roles and offline fallback profile", () => {
    const speedSet = classifyWeaponSet([
      { ...SAMPLE_SPEAR, mods: ["26% increased Attack Speed", "+14 to Dexterity"] },
    ]);
    expect(speedSet.role).toBe("speed");

    const mockBuild: ParsedBuildData = {
      character: {
        className: "Mercenary",
        ascendancy: "Gemling Legionnaire",
        level: 96,
        mainSocketGroup: 1,
        stats: { FireResist: 75, ColdResist: 75, LightningResist: 75, Spirit: 100, SpiritUnreserved: 10 },
      },
      passiveTree: {
        treeVersion: "0_5",
        totalAllocated: 98,
        keystones: [],
        ascendancyNodes: [],
        notables: [],
        weaponSet1Notables: [],
        weaponSet2Notables: [],
      },
      weaponSets: { hasDualWeaponSet: false, set1: [SAMPLE_SPEAR], set2: [] },
      equippedItems: [SAMPLE_SPEAR],
      jewels: [],
      flasksAndCharms: [],
      skillGroups: [
        {
          index: 1,
          isMain: true,
          slot: "Weapon 1",
          label: "",
          weaponSet: "1",
          gems: [{ name: "Twister", skillId: "Twister", level: 20, quality: 20, weaponSet: "1" }],
        },
      ],
      notes: "",
    };

    const profile = buildLocalProfile(mockBuild);
    expect(profile.source).toBe("local-fallback");
    expect(profile.positioning.archetype).toContain("Twister");
    expect(profile.positioning.coreAffixes.length).toBeGreaterThan(0);
    // 停用静态正则评级：未运行 AI 时 contentReadiness 必须为空
    expect(profile.positioning.contentReadiness).toBeUndefined();

    // 验证 AI 结构化评级可以在 base.contentReadiness 为空时完整融合六维雷达并标注 confidence="ai"
    const aiMerged = mergeStructuredIntoPositioning(profile.positioning, {
      contentReadiness: {
        overallGrade: "S",
        overallTier: "终局 T4 全通极速秒杀档",
        summary: "109M DPS + 9.4/s 旋风斩位移",
        radar: [
          { id: "clearSpeed", score: 98, metric: "投速×2.3", reason: "全屏覆盖" },
          { id: "bossing", score: 120, metric: "109M", reason: "应自动钳位到100" },
          { id: "mobility", score: 95, metric: "9.4/s", reason: "极速" },
          { id: "survivability", score: 78, metric: "物一击14.5k", reason: "稳健" },
          { id: "sustain", score: 82, metric: "循环顺畅", reason: "流畅" },
          { id: "costEfficiency", score: 30, metric: "镜级造价", reason: "昂贵" },
        ],
        items: [{ id: "t15_mapping", grade: "S", verdict: "碾压速通", reason: "高机动清图" }],
      },
    });
    expect(aiMerged.contentReadiness?.confidence).toBe("ai");
    expect(aiMerged.contentReadiness?.overallGrade).toBe("S");
    expect(aiMerged.contentReadiness?.radar).toHaveLength(6);
    expect(aiMerged.contentReadiness?.radar?.[1]?.score).toBe(100);
    expect(aiMerged.contentReadiness?.items[0]?.id).toBe("t15_mapping");

    const deepLink = buildDeepLinkUrl({ title: profile.positioning.archetype, league: "Standard", pobCode: "abc" });
    expect(deepLink.startsWith("poe2wb://import?")).toBe(true);
    const json = serializeHandoffBundle({ pobCode: "abc", title: "Test", pushedAt: 1 }, undefined);
    expect(json).toContain("poe2-workbench-handoff/v1");
  });

  it("handles spear+sceptre set, offline patchDict hydration, budget/level copy planner, and streaming header split", () => {
    const dualRole = classifyWeaponSet([
      {
        ...SAMPLE_SPEAR,
        name: "The Ordained",
        baseType: "Grand Spear",
        mods: ["169% increased Physical Damage", "Adds 1 to 250 Lightning Damage", "+7.51% to Critical Hit Chance"],
      },
      {
        ...SAMPLE_SPEAR,
        slot: "Weapon 2 Swap",
        name: "Guiding Palm of the Eye",
        baseType: "Shrine Sceptre",
        mods: ["Gain 25% of Damage as Extra Cold Damage", "15% increased Spirit"],
      },
    ]);
    expect(dualRole.role).toBe("damage");

    const mockDict: PatchDictData = {
      schemaVersion: 1,
      treeVersion: "0_5",
      generatedAt: "2026-11-24",
      nodeCount: 2,
      gemCount: 1,
      nodes: {
        "14226": { n: "Dance with Death", k: "k", s: ["25% more Skill Speed while Off Hand is empty"] },
        "63259": { n: "Motoric Implants", k: "a", a: "Gemling Legionnaire", s: ["+2 to Level of all Skills with a Dexterity requirement"] },
      },
      gems: {
        "Olroth's Conviction": { d: "Empowers additional uses", s: 1, c: ["base_skill_ward_cost_as_%_of_life_and_mana_cost=100"] },
      },
    };
    expect(isDictOutdatedForTree(mockDict, "0_6")).toBe(true);
    expect(isDictOutdatedForTree(mockDict, "0_5")).toBe(false);

    const hydrated = hydratePassiveTree(["14226", "63259"], new Set(), new Set(), "0_5", [], mockDict);
    expect(hydrated.keystones[0]?.name).toBe("Dance with Death");
    expect(hydrated.ascendancyNodes[0]?.name).toBe("Motoric Implants");

    const rawStream =
      '```json\n{"archetype":"古灵 / 旋龙","gearTiers":{"mandatory":["The Taming"],"amplifier":["The Ordained"],"luxury":["Headhunter"]}}\n```\n---\n### 1. 本质循环\nWS1跑图+WS2旋龙。';
    const { structured, markdown } = parseStructuredHeaderFromMarkdown(rawStream);
    expect(structured?.archetype).toBe("古灵 / 旋龙");
    expect(markdown).toContain("WS1跑图+WS2旋龙");

    const mockBuild: ParsedBuildData = {
      character: { className: "Mercenary", ascendancy: "Gemling Legionnaire", level: 100, mainSocketGroup: 1, stats: {} },
      passiveTree: hydrated,
      weaponSets: { hasDualWeaponSet: false, set1: [], set2: [] },
      equippedItems: [
        { ...SAMPLE_SPEAR, name: "The Ordained", baseType: "Grand Spear", rarity: "UNIQUE", levelReq: 79 },
        { ...SAMPLE_SPEAR, slot: "Ring 2", name: "The Taming", baseType: "Prismatic Ring", rarity: "UNIQUE", levelReq: 42 },
        { ...SAMPLE_SPEAR, slot: "Belt", name: "Headhunter", baseType: "Heavy Belt", rarity: "UNIQUE", levelReq: 50 },
      ],
      jewels: [],
      flasksAndCharms: [],
      skillGroups: [],
      notes: "",
    };
    const baseProf = buildLocalProfile(mockBuild);
    const mergedProf = {
      ...baseProf,
      positioning: mergeStructuredIntoPositioning(baseProf.positioning, structured!),
    };
    const copyPlan = evaluateLocalCopyPlan(mergedProf, {
      characterLevel: 75,
      budgetDivine: 10,
      ownedItems: ["The Taming"],
    });
    expect(copyPlan.missingPassivePoints).toBe(25);
    expect(copyPlan.items.find((x) => x.name === "The Ordained")?.status).toBe("level-blocked");
    expect(copyPlan.items.find((x) => x.name === "The Taming")?.status).toBe("owned");
    expect(copyPlan.items.find((x) => x.name === "Headhunter")?.status).toBe("over-budget-sub");

    // 验证白底底材查询与离线三路决策器
    const baseQuery = buildTradeQuery(SAMPLE_SPEAR, [], { kind: "base", minIlvl: 82 }) as {
      query: { type?: string; filters?: { type_filters?: { filters?: { rarity?: { option?: string }; ilvl?: { min?: number } } }; misc_filters?: { filters?: { corrupted?: { option?: string } } } } };
    };
    expect(baseQuery.query.type).toBe("Winged Spear");
    expect(baseQuery.query.filters?.type_filters?.filters?.rarity?.option).toBe("normal");
    expect(baseQuery.query.filters?.type_filters?.filters?.ilvl?.min).toBe(82);
    expect(baseQuery.query.filters?.misc_filters?.filters?.corrupted?.option).toBe("false");

    const offlineDec = buildOfflineThreeWayDecision(
      SAMPLE_SPEAR,
      {
        finishedKind: "budget",
        finishedQuote: {
          slot: "Weapon 1",
          kind: "budget",
          divineValue: 12,
          formatted: "12 D",
          totalListings: 10,
          modeLabel: "平替 (-25%)",
          webUrl: "https://trade/fin",
          samples: ["12 divine"],
          queriedAt: 1,
        },
        semiQuote: {
          slot: "Weapon 1",
          kind: "semi",
          divineValue: 2,
          formatted: "2 D",
          totalListings: 20,
          modeLabel: "半成品底子 (非腐化)",
          webUrl: "https://trade/semi",
          samples: ["2 divine"],
          queriedAt: 1,
        },
        baseQuote: {
          slot: "Weapon 1",
          kind: "base",
          divineValue: 0.2,
          formatted: "0.2 D",
          totalListings: 50,
          modeLabel: "白底底材 (非腐化)",
          webUrl: "https://trade/base",
          samples: ["20 exalted"],
          queriedAt: 1,
        },
        finishedUrl: "https://trade/fin",
        semiUrl: "https://trade/semi",
        baseUrl: "https://trade/base",
        wantAffixes: ["+4 to Level of all Projectile Skills"],
        existingAffixes: ["+4 to Level of all Projectile Skills"],
        rateLimited: false,
      },
      "Standard"
    );
    expect(offlineDec.recommended).toBe("craft_semi");
    expect(offlineDec.bestAction?.tradeUrl).toBe("https://trade/semi");
  });

  it("applies deep passive-tree and rune-socket synergy analysis for budget and semi-finished queries", () => {
    const extStats = buildStatsIndex([
      ...SAMPLE_STATS,
      {
        id: "explicit",
        entries: [
          { id: "explicit.stat_es_pct", text: "#% increased Energy Shield (Local)" },
          { id: "explicit.stat_es_flat", text: "+# to maximum Energy Shield (Local)" },
          { id: "explicit.stat_fire_res", text: "+#% to Fire Resistance" },
          { id: "explicit.stat_chaos_res", text: "+#% to Chaos Resistance" },
          { id: "explicit.stat_acc", text: "+# to Accuracy Rating" },
          { id: "explicit.stat_proj_spd", text: "#% increased Projectile Speed" },
          { id: "explicit.stat_speed_mag", text: "#% increased Explicit Speed Modifier magnitudes" },
          { id: "explicit.stat_sock_eff", text: "#% increased effect of Socketed Augment Items" },
        ],
      },
      {
        id: "augment",
        entries: [{ id: "rune.stat_aug_1", text: "+#% to Cold Resistance" }],
      },
      {
        id: "enchant",
        entries: [{ id: "enchant.stat_anoint", text: "Allocates Subterfuge Mask" }],
      },
    ]);

    // 1. 攻速工具长矛（单手 2 孔超过工匠石上限 + 镶嵌物效果增幅 + 重复攻速行 + 低 T 垃圾点伤）
    const speedSpear: ParsedItem = {
      slot: "Weapon 1",
      itemId: "w1",
      rarity: "RARE",
      name: "Armageddon Edge",
      baseType: "Soaring Spear",
      corrupted: false,
      socketCount: 2,
      socketedRunes: ["Thrud's Might", "Soul Core of Quipolatl", "6% increased Attack Speed", "Can roll Destruction modifiers"],
      mods: [
        "30% increased Explicit Speed Modifier magnitudes",
        "Adds 10 to 22 Physical Damage",
        "36% increased Attack Speed",
        "29% increased effect of Socketed Augment Items",
        "10% increased Attack Speed",
      ],
      rawText: "",
    };

    const speedBudgetRows = matchItemModsWithIndex(speedSpear, extStats, "budget");
    // 合并重复攻速词条（36 + 10 = 46），且剔除低 T 垃圾点伤
    const mergedAs = speedBudgetRows.find((r) => r.statId === "explicit.stat_2");
    expect(mergedAs?.originalValue).toBe(46);
    expect(mergedAs?.mergedCount).toBe(2);
    const trashFlat = speedBudgetRows.find((r) => r.statId === "explicit.stat_3");
    expect(trashFlat?.role).toBe("dead");
    expect(trashFlat?.checked).toBe(false);

    // 单手 2 孔超工匠石上限 + 镶嵌物增幅：即使在 semi / budget 模式也必须锁 total_augment_sockets >= 2
    const speedSemiRows = matchItemModsWithIndex(speedSpear, extStats, "semi");
    const speedSemiQ = buildTradeQuery(speedSpear, speedSemiRows, { kind: "semi" }) as {
      query: {
        filters?: {
          equipment_filters?: { filters?: { total_augment_sockets?: { min?: number } } };
          misc_filters?: { filters?: { corrupted?: { option?: string } } };
        };
      };
    };
    expect(speedSemiQ.query.filters?.equipment_filters?.filters?.total_augment_sockets?.min).toBe(2);
    expect(speedSemiQ.query.filters?.misc_filters?.filters?.corrupted?.option).toBe("false");

    // 2. CI + Subterfuge Mask + Falcon Dive 天赋联动：生命与混抗必须判为死词条，命中值必须视为高价值词条，头盔平替锁总 ES 下限
    const ciBuild: ParsedBuildData = {
      character: { className: "Ranger", ascendancy: "Amazon", level: 95, mainSocketGroup: 1, stats: {} },
      passiveTree: {
        treeVersion: "0_5",
        totalAllocated: 100,
        keystones: [{ id: "1", name: "Chaos Inoculation", kind: "keystone", stats: ["Maximum Life becomes 1", "Immune to Chaos Damage"] }],
        ascendancyNodes: [],
        notables: [
          { id: "2", name: "Subterfuge Mask", kind: "notable", stats: ["+1 to Evasion Rating per 1 Item Energy Shield on Equipped Helmet"] },
          { id: "3", name: "Falcon Dive", kind: "notable", stats: ["1% increased Attack Speed per 400 Accuracy Rating, up to 20%"] },
        ],
        weaponSet1Notables: [],
        weaponSet2Notables: [],
      },
      weaponSets: { hasDualWeaponSet: false, set1: [], set2: [] },
      equippedItems: [],
      jewels: [],
      flasksAndCharms: [],
      skillGroups: [
        {
          index: 1,
          isMain: true,
          slot: "Weapon 1",
          label: "",
          weaponSet: "1",
          gems: [
            { name: "Twister", skillId: "Twister", level: 20, quality: 20, weaponSet: "1" },
            { name: "Projectile Acceleration III", skillId: "PA3", level: 20, quality: 20, weaponSet: "1" },
            { name: "Olroth's Conviction", skillId: "OC", level: 20, quality: 20, weaponSet: "1" },
          ],
        },
      ],
      notes: "",
    };

    const ciHelmet: ParsedItem = {
      slot: "Helmet",
      itemId: "h1",
      rarity: "RARE",
      name: "Hypnotic Star",
      baseType: "Ancestral Tiara",
      corrupted: false,
      socketCount: 1,
      socketedRunes: [],
      properties: { "Energy Shield": "400" },
      mods: [
        "86% increased Energy Shield",
        "+73 to maximum Energy Shield",
        "+95 to maximum Life",
        "+25% to Chaos Resistance",
        "+400 to Accuracy Rating",
        "+35% to Fire Resistance",
      ],
      rawText: "",
    };

    const ciHelmRows = matchItemModsWithIndex(ciHelmet, extStats, "budget", ciBuild);
    expect(ciHelmRows.find((r) => r.statId === "explicit.stat_4")?.role).toBe("dead");
    expect(ciHelmRows.find((r) => r.statId === "explicit.stat_4")?.checked).toBe(false);
    expect(ciHelmRows.find((r) => r.statId === "explicit.stat_chaos_res")?.role).toBe("dead");
    expect(ciHelmRows.find((r) => r.statId === "explicit.stat_acc")?.role).toBe("synergy");
    expect(ciHelmRows.find((r) => r.statId === "explicit.stat_acc")?.checked).toBe(true);

    const ciHelmBudgetQ = buildTradeQuery(ciHelmet, ciHelmRows, { kind: "budget", build: ciBuild }) as {
      query: { filters?: { equipment_filters?: { filters?: { es?: { min?: number } } } } };
    };
    expect(ciHelmBudgetQ.query.filters?.equipment_filters?.filters?.es?.min).toBe(312);

    // 3. Ward 底子 + 投射物加速辅助 + 涂油项链排除
    const wardGloves: ParsedItem = {
      slot: "Gloves",
      itemId: "g1",
      rarity: "RARE",
      name: "Phoenix Claw",
      baseType: "Runeforged Secured Wraps",
      corrupted: false,
      socketCount: 1,
      socketedRunes: ["Kolr's Hunt", "Can roll Marksman modifiers"],
      properties: { Ward: "166" },
      mods: ["+2 to Level of all Projectile Skills", "36% increased Projectile Speed", "+32% to Fire Resistance"],
      rawText: "",
    };
    const gloveRows = matchItemModsWithIndex(wardGloves, extStats, "budget", ciBuild);
    expect(gloveRows.find((r) => r.statId === "explicit.stat_proj_spd")?.checked).toBe(true);
    const gloveBudgetQ = buildTradeQuery(wardGloves, gloveRows, { kind: "budget", build: ciBuild }) as {
      query: { type?: string };
    };
    // Ward 底子在平替模式也必须锁定基底
    expect(gloveBudgetQ.query.type).toBe("Runeforged Secured Wraps");

    const anointedAmulet: ParsedItem = {
      slot: "Amulet",
      itemId: "a1",
      rarity: "RARE",
      name: "Sol Torc",
      baseType: "Solar Amulet",
      corrupted: false,
      socketCount: 0,
      socketedRunes: [],
      mods: ["Allocates Subterfuge Mask", "+3 to Level of all Projectile Skills", "+35% to Fire Resistance"],
      rawText: "",
    };
    const amuRows = matchItemModsWithIndex(anointedAmulet, extStats, "exact", ciBuild);
    expect(amuRows.find((r) => r.statId === "enchant.stat_anoint")?.checked).toBe(false);
  });

  it("parses PoE2 clipboard item text and round-trips HTC handoff payload", () => {
    const rawClip = [
      "Item Class: Body Armours",
      "Rarity: Rare",
      "Viper Shroud",
      "Slipstrike Vest",
      "--------",
      "Evasion Rating: 912",
      "Energy Shield: 320",
      "--------",
      "Item Level: 83",
      "--------",
      "+12% to Cold Resistance (rune)",
      "--------",
      "+115 to maximum Life (fractured)",
      "+38% to Fire Resistance",
      "+34% to Chaos Resistance",
    ].join("\n");

    const parsed = parsePoe2ItemText(rawClip);
    expect(parsed.htcBaseClass).toBe("Body Armours");
    expect(parsed.baseType).toBe("Slipstrike Vest");
    expect(parsed.ilvl).toBe(83);
    expect(parsed.variantHint).toBe("Evasion / Energy Shield");
    expect(parsed.transferableMods).toHaveLength(3);
    expect(parsed.transferableMods[0].fractured).toBe(true);
    expect(parsed.skippedMods).toHaveLength(1);

    const link = buildHtcLinkFromClipboardTexts({
      mode: "have_item",
      existingText: rawClip,
      targetText: "+115 to maximum Life\n+38% to Fire Resistance\n+34% to Chaos Resistance\n+42% to Cold Resistance",
      minTierMode: "mid",
    });
    expect(link.supported).toBe(true);
    expect(link.url).toContain("poe2htc.com/?s=");
    expect(resolveHtcBaseId("Body Armours", "Evasion / Energy Shield", "Slipstrike Vest")).toBe("Body_Armours_dex_int");
    expect(resolveHtcBaseId("Spears", "", "Akoyan Spear")).toBe("Spears");

    const decoded = decodeHtcHandoffHash(link.url.slice(link.url.indexOf("#")));
    expect(decoded?.mode).toBe("have_item");
    expect(decoded?.baseClass).toBe("Body Armours");
    expect(decoded?.nativeS).toBe(1);
    expect(decoded?.existingMods).toHaveLength(3);
    expect(decoded?.targetMods).toHaveLength(4);
    expect(decoded?.rawExistingText).toContain("Item Class: Body Armours");
    expect(decoded ? buildPoe2PasteTextFromPayload(decoded) : "").toContain("Slipstrike Vest");

    const nativeEnc = link.payload ? buildNativePoe2HtcShareParam(link.payload) : null;
    expect(nativeEnc?.baseId).toBe("Body_Armours_dex_int");
    expect(nativeEnc?.matchedExistingCount).toBe(3);
    expect(nativeEnc?.matchedTargetCount).toBe(4);

    const spearLink = buildHtcLinkFromParsedItem(SAMPLE_SPEAR, { mode: "scratch", minTierMode: "high" });
    expect(spearLink.supported).toBe(true);
    expect(spearLink.baseClass).toBe("Spears");
    expect(spearLink.url).toContain("poe2htc.com/?s=");
    expect(spearLink.transferableTargetCount).toBe(5);
    expect(spearLink.payload ? buildPoe2PasteTextFromPayload(spearLink.payload) : "").toContain("Item Class: Spears");
    const spearNative = spearLink.payload ? buildNativePoe2HtcShareParam(spearLink.payload) : null;
    expect(spearNative?.baseId).toBe("Spears");
    // SAMPLE_SPEAR has 3 real Spear mods (+4 proj, 24% AS, flat phys) + 2 fictional test lines
    expect(spearNative?.matchedTargetCount).toBe(3);

    const realSpearLink = buildHtcLinkFromClipboardTexts({
      mode: "scratch",
      fallbackSlot: "Spears",
      fallbackBaseType: "Akoyan Spear",
      ilvlOverride: 82,
      targetText: [
        "179% increased Physical Damage",
        "Adds 33 to 66 Physical Damage",
        "27% increased Attack Speed",
        "+24% to Critical Damage Bonus",
        "+4 to Level of all Projectile Skills",
      ].join("\n"),
    });
    expect(realSpearLink.supported).toBe(true);
    expect(realSpearLink.url).toContain("poe2htc.com/?s=");
    const realSpearNative = realSpearLink.payload ? buildNativePoe2HtcShareParam(realSpearLink.payload) : null;
    expect(realSpearNative?.baseId).toBe("Spears");
    expect(realSpearNative?.matchedTargetCount).toBe(5);
    expect(realSpearNative?.unmatchedMods).toHaveLength(0);
    expect(buildHtcLinkFromParsedItem({ ...SAMPLE_SPEAR, rarity: "UNIQUE" }).supported).toBe(false);
  });
});
