import { describe, expect, it } from "vitest";
import { createTranslator, nextLangMode, type BilingualDictionary } from "@contracts";
import { parseDeepLinkUrl } from "../api/tauriBridge";
import {
  buildCopyPlanItemKey,
  computePobHash,
  countCopyPlanProgress,
  findMatchingSavedPlan,
  mergeCopyPlans,
  patchCopyPlanItemState,
  sha256Hex,
} from "../utils/copyPlanStorage";
import {
  buildOfficialTradeSearchUrl,
  buildTradeUrlFromCopyItem,
  fmtCompact,
  fmtDeltaPct,
  parseTargetPasteInput,
} from "../utils/handoffParser";

describe("desktop handoffParser & formatters", () => {
  it("parses extension handoff JSON bundle with target build and prices", () => {
    const bundle = JSON.stringify({
      schema: "poe2-workbench-handoff/v1",
      createdAt: 1000,
      target: {
        pobCode: "eNrtMockTarget",
        title: "Amazon / Twister",
        league: "Dawn of the Hunt",
        pushedAt: 1000,
      },
      prices: {
        league: "Dawn of the Hunt",
        source: "extension",
        updatedAt: 1000,
        prices: { "Body Armour": 8 },
        entries: [],
      },
    });
    const parsed = parseTargetPasteInput(bundle);
    expect(parsed.kind).toBe("handoff");
    expect(parsed.pobCode).toBe("eNrtMockTarget");
    expect(parsed.prices?.prices["Body Armour"]).toBe(8);
  });

  it("falls back to raw PoB2 code when input is not JSON handoff", () => {
    const parsed = parseTargetPasteInput("eNrtRawPobString");
    expect(parsed.kind).toBe("pob");
    expect(parsed.pobCode).toBe("eNrtRawPobString");
  });

  it("formats compact stats, signed deltas, and trade URLs cleanly", () => {
    expect(fmtCompact(1_450_000)).toBe("1.45M");
    expect(fmtCompact(335_517)).toBe("335.5k");
    expect(fmtDeltaPct(14.25)).toBe("+14.3%");
    expect(fmtDeltaPct(-3.4)).toBe("-3.4%");
    const url = buildOfficialTradeSearchUrl("Dawn of the Hunt", "Loath Ward", "Slipstrike Vest");
    expect(url).toContain("pathofexile.com/trade2/search/poe2/Dawn%20of%20the%20Hunt");
    expect(url).toContain("Slipstrike%20Vest");
  });

  it("parses poe2wb:// deep link URLs for both inline PoB and clipboard handoff", () => {
    const inline = parseDeepLinkUrl(
      "poe2wb://import?title=Amazon%20Twister&league=Standard&pob=eNrtTest123"
    );
    expect(inline).not.toBeNull();
    expect(inline?.title).toBe("Amazon Twister");
    expect(inline?.pobCode).toBe("eNrtTest123");
    expect(inline?.fromClipboard).toBe(false);

    const clip = parseDeepLinkUrl("poe2wb://import?title=BigBuild&fromClipboard=1");
    expect(clip?.fromClipboard).toBe(true);
    expect(parseDeepLinkUrl("https://example.com")).toBeNull();
  });

  it("translates terms, slots, and mod lines across bi / zh / en hot-switch modes", () => {
    const dict: BilingualDictionary = {
      terms: {
        classes: { Monk: "武僧" },
        ascendancies: { Invoker: "祈求者" },
        slots: { "Body Armour": "胸甲" },
        itemClasses: {},
        uniques: { "Ingenuity": "独创" },
        bases: { "Utility Belt": "实用腰带" },
        gems: { "Tempest Flurry": "暴风乱舞" },
        passives: { "Chaos Inoculation": "异灵之体" },
        static: {},
      },
      mods: {
        "# to maximum life": "+# 最大生命",
        "#% increased attack speed": "攻击速度提高 #%",
      },
    };

    const bi = createTranslator(dict, "bi");
    expect(bi.slot("Body Armour")).toBe("胸甲 (Body Armour)");
    expect(bi.slot("Tree Jewel (Node 1234)")).toBe("天赋珠宝孔 (节点 1234) (Tree Jewel (Node 1234))");
    expect(bi.term("Tempest Flurry", "gems")).toBe("暴风乱舞 (Tempest Flurry)");
    expect(bi.term("Chaos Inoculation", "passives")).toBe("异灵之体 (Chaos Inoculation)");
    expect(bi.mod("+120 to maximum Life")).toBe("+120 最大生命 (+120 to maximum Life)");
    expect(bi.mod("Grants Skill: Level 18 Tempest Flurry")).toBe(
      "赋予技能：18 级 暴风乱舞 (Grants Skill: Level 18 Tempest Flurry)"
    );
    expect(bi.itemName("Ingenuity", "Utility Belt")).toBe("独创 (Ingenuity) · 实用腰带 (Utility Belt)");

    const zh = createTranslator(dict, "zh");
    expect(zh.slot("Body Armour")).toBe("胸甲");
    expect(zh.mod("+120 to maximum Life")).toBe("+120 最大生命");
    expect(zh.term("Unknown Skill", "gems")).toBe("Unknown Skill");

    const en = createTranslator(dict, "en");
    expect(en.slot("Body Armour")).toBe("Body Armour");
    expect(en.mod("+120 to maximum Life")).toBe("+120 to maximum Life");

    expect(nextLangMode("bi")).toBe("zh");
    expect(nextLangMode("zh")).toBe("en");
    expect(nextLangMode("en")).toBe("bi");
  });

  it("builds dynamic Trade2 search URLs from toggled AI copy-plan affixes and lockBase flag", () => {
    const item = {
      slot: "Boots",
      action: "buy_budget" as const,
      priority: 1,
      itemName: "Rare Boots",
      baseType: "Secutor Greaves",
      rarity: "RARE",
      minSockets: 2,
      corrupted: false,
      lockBase: false,
      reason: "优先保移速与血量",
      selectedAffixes: [
        {
          text: "25% increased Movement Speed",
          zh: "移动速度",
          min: 25,
          required: true,
          statId: "explicit.stat_2250533757",
          matched: true,
        },
        {
          text: "+90 to maximum Life",
          zh: "最大生命",
          min: 80,
          required: true,
          statId: "explicit.stat_3299347043",
          matched: true,
        },
      ],
    };

    const unlockedUrl = buildTradeUrlFromCopyItem(item, "Standard", [item.selectedAffixes[0]], false);
    const unlockedPayload = JSON.parse(decodeURIComponent(unlockedUrl.split("?q=")[1]));
    expect(unlockedPayload.query.type).toBeUndefined();
    expect(unlockedPayload.query.filters.type_filters.filters.category.option).toBe("armour.boots");
    expect(unlockedPayload.query.stats[0].filters).toHaveLength(1);
    expect(unlockedPayload.query.stats[0].filters[0]).toEqual({
      id: "explicit.stat_2250533757",
      disabled: false,
      value: { min: 25 },
    });

    const lockedUrl = buildTradeUrlFromCopyItem(item, "Standard", item.selectedAffixes, true);
    const lockedPayload = JSON.parse(decodeURIComponent(lockedUrl.split("?q=")[1]));
    expect(lockedPayload.query.type).toBe("Secutor Greaves");
    expect(lockedPayload.query.stats[0].filters).toHaveLength(2);
  });

  it("computes deterministic PoB hashes and merges/tracks saved copy-plan progress", () => {
    expect(sha256Hex("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    expect(computePobHash("abc")).toBe("ba7816bf8f01cfea");
    expect(computePobHash("   ")).toBe("");

    const boots = {
      slot: "Boots",
      action: "buy_budget" as const,
      priority: 1,
      itemName: "Rare Boots",
      rarity: "RARE",
      reason: "移速",
      selectedAffixes: [],
    };
    const belt = {
      slot: "Belt",
      action: "skip" as const,
      priority: 2,
      itemName: "Headhunter",
      rarity: "UNIQUE",
      reason: "暂缓",
      selectedAffixes: [],
    };
    const itemKey = buildCopyPlanItemKey(boots, 0);
    expect(itemKey).toBe("Boots::Rare Boots");

    const localRec = {
      id: "cp_1",
      mode: "copy" as const,
      pobHash: computePobHash("eNrtDemo"),
      buildTitle: "Deadeye",
      league: "Standard",
      model: "mock",
      copyContext: { characterLevel: 90, budgetDivine: 15, goalNote: "" },
      text: "plan text",
      copyPlan: { items: [boots, belt] },
      itemStates: {},
      createdAt: 1000,
      updatedAt: 1000,
    };

    const patched = patchCopyPlanItemState([localRec], "cp_1", itemKey, { purchased: true, scoreScale: 0.8 });
    expect(countCopyPlanProgress(patched[0])).toEqual({ done: 1, total: 1 });

    const merged = mergeCopyPlans(patched, [{ ...localRec, updatedAt: 900, itemStates: {} }]);
    expect(merged).toHaveLength(1);
    expect(merged[0].itemStates?.[itemKey]?.purchased).toBe(true);

    const matched = findMatchingSavedPlan(merged, {
      mode: "copy",
      pobHash: computePobHash("eNrtDemo"),
      copyContext: { characterLevel: 90, budgetDivine: 15, goalNote: "" },
      league: "Standard",
    });
    expect(matched?.id).toBe("cp_1");
  });
});
