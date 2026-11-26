import type {
  AblatedAffixEntry,
  ItemAblationDetail,
  SlotAffixReport,
} from "@contracts";
import type { WeightedAffixTerm } from "../../api/workbenchApi";

export type DossierTab = "gear" | "jewels" | "skills" | "ascendancy" | "copy";

export interface ResolvedTradeMeta {
  budget?: string;
  semi?: string;
  statCount?: number;
  mode?: string;
  weightedTerms?: WeightedAffixTerm[];
  targetScore?: number;
  minScore?: number;
  equipmentFloors?: Record<string, number>;
  mandatorySkills?: string[];
}

export const ROLE_BADGE_CLASS: Record<string, string> = {
  core_dps: "gold",
  core_both: "gold",
  core_defense: "green",
  defense: "green",
  threshold: "red",
  mechanic: "purple",
  mobility: "blue",
  prerequisite: "amber",
  utility: "blue",
  luxury: "unique",
  main_dps: "gold",
  spirit_aura: "blue",
  buff_debuff: "green",
  trigger: "purple",
  travel: "blue",
};

export const COPY_TIER_CLASS: Record<string, string> = {
  mandatory: "red",
  core: "gold",
  flexible: "blue",
  luxury: "unique",
};

export const PURPOSE_BADGE_CLASS: Record<string, string> = {
  core_dps: "gold",
  core_defense: "green",
  synergy: "purple",
  threshold: "red",
  sustain: "blue",
  utility: "blue",
  cuttable: "",
  dead: "red",
};

export const COPY_ACTION_LABEL: Record<AblatedAffixEntry["copyAction"], { label: string; cls: string }> = {
  lock: { label: "必锁", cls: "red" },
  downgrade: { label: "可降配", cls: "amber" },
  craft_later: { label: "留空后补", cls: "blue" },
  cut: { label: "可砍", cls: "green" },
  skip: { label: "无需锁", cls: "" },
};

export const ORIGIN_TAG: Record<string, string> = {
  fractured: "破裂",
  desecrated: "渎神",
  crafted: "工艺",
  enchant: "附魔/涂油",
  implicit: "基底",
};

export function fmtD(v: number | null | undefined, source?: string): string {
  if (v === null || v === undefined || v <= 0) return "未核价";
  const num = v >= 10 ? String(Math.round(v * 10) / 10) : String(Math.round(v * 100) / 100);
  if (source === "estimate") return `~${num}D(估)`;
  if (source === "ninja") return `${num}D`;
  if (source === "manual") return `${num}D(手)`;
  return `${num}D`;
}

export function priceSourceBadgeLabel(source?: string): string {
  if (source === "ninja") return "poe.ninja 实时";
  if (source === "trade") return "官方市集实价";
  if (source === "manual") return "手动填价";
  if (source === "estimate") return "参考估算";
  return "待查价";
}

export function fmtLoss(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v) || Math.abs(v) < 0.05) return "0%";
  return v > 0 ? `-${v.toFixed(1)}%` : `+${Math.abs(v).toFixed(1)}%`;
}

export function makeItemKey(item: ItemAblationDetail, idx: number): string {
  return `${item.kind || "item"}:${item.slot || "slot"}:${item.itemId || ""}:${idx}`;
}

export function pickLockedAffixesForDossier(item: ItemAblationDetail, kind: "budget" | "semi"): string[] {
  const plan = (kind === "semi" ? item.semiPlan : item.budgetPlan) as { lockAffixes?: unknown } | undefined;
  if (Array.isArray(plan?.lockAffixes) && plan.lockAffixes.length > 0) {
    return plan.lockAffixes.map(String).filter(Boolean).slice(0, kind === "semi" ? 2 : 3);
  }
  const affixes = item.affixes || [];
  const locked = affixes
    .filter((a) => a.tier === "must" && a.copyAction === "lock")
    .map((a) => a.line)
    .filter(Boolean);
  if (locked.length > 0) return locked.slice(0, kind === "semi" ? 2 : 3);
  return affixes
    .filter((a) => a.tier !== "cut" && a.purposeKind !== "dead")
    .map((a) => a.line)
    .filter(Boolean)
    .slice(0, kind === "semi" ? 2 : 3);
}

export function buildCraftSeedFromDossier(item: ItemAblationDetail, simDps = 0, simEhp = 0): SlotAffixReport {
  return {
    slot: item.slot,
    itemName: item.name,
    baseType: item.baseType,
    rarity: item.rarity,
    baseDps: simDps,
    baseEhp: simEhp,
    swappedDps: simDps,
    swappedEhp: simEhp,
    deltaDpsPct: item.compensatedDpsLossPct ?? item.dpsLossPct,
    deltaEhpPct: item.compensatedEhpLossPct ?? item.ehpLossPct,
    redlines: [],
    affixes: item.affixes.map((a) => ({
      line: a.line,
      tier: a.tier,
      deltaDpsPct: a.dpsLossPct,
      deltaEhpPct: a.ehpLossPct,
      triggeredRedlines: a.triggeredRedlines.map((msg) => ({
        kind: "resist" as const,
        severity: "warn" as const,
        message: msg,
      })),
      origin: a.origin as "explicit",
      side: a.side as "prefix",
      craftDifficulty: a.craftDifficulty as "easy",
      synergyTags: a.synergyTags,
    })),
    mustAffixes: item.affixes.filter((a) => a.tier === "must").map((a) => a.line),
    cuttableAffixes: item.affixes.filter((a) => a.tier === "cut").map((a) => a.line),
    passiveSynergies: item.passiveSynergies,
    elapsedMs: 0,
  };
}
