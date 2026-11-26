/**
 * 完整引擎面板 & 自身 BD vs 目标 BD 全景差距契约
 */

export interface SpiritAuditEntry {
  name: string;
  reserved: number;
  reservedPct: number;
  set1: boolean;
  set2: boolean;
}

export interface EngineStats {
  dps: number;
  dpsSource: string;
  ehp: number;
  ehpInfinite: number;
  life: number;
  es: number;
  mana: number;
  spirit: number;
  spiritReserved: number;
  spiritUnreserved: number;
  spiritSet1: number;
  spiritUnreservedSet1: number;
  spiritSet2: number;
  spiritUnreservedSet2: number;
  activeWeaponSet: number;
  hasDualWeaponSet: number;
  chaosInoculation: number;
  armour: number;
  evasion: number;
  evadeChance: number;
  blockChance: number;
  speed: number;
  critChance: number;
  critMult: number;
  hitChance: number;
  moveSpeed: number;
  skillMoveSpeed: number;
  fireRes: number;
  coldRes: number;
  lightningRes: number;
  chaosRes: number;
  fireOverCap: number;
  coldOverCap: number;
  lightningOverCap: number;
  chaosOverCap: number;
  str: number;
  dex: number;
  int: number;
  reqStr: number;
  reqDex: number;
  reqInt: number;
  physMaxHit: number;
  eleMaxHit: number;
  chaosMaxHit: number;
  elapsedMs?: number;
  spiritAudit?: SpiritAuditEntry[];
}

export interface RedlineAlert {
  kind: "resist" | "spirit" | "attribute" | "mana";
  severity: "fail" | "warn";
  message: string;
  en?: {
    message?: string;
  };
}

export interface SlotUpgradeStep {
  slot: string;
  kind: "tree" | "skills" | "slot_swap";
  label: string;
  deltaDpsPct: number;
  deltaEhpPct: number;
  deltaMoveSpeedPct: number;
  redlines: RedlineAlert[];
  priceDivine?: number | null;
  dpsPerDivine?: number | null;
  myItemName?: string;
  targetItemName?: string;
  targetBaseType?: string;
  targetRarity?: string;
  targetMods?: string[];
  exactPriceDivine?: number | null;
  budgetPriceDivine?: number | null;
  semiPriceDivine?: number | null;
  priceBasis?: "override" | "exact" | "budget" | null;
  compensatedDeltaDpsPct?: number | null;
  compensatedDeltaEhpPct?: number | null;
  inactiveWeaponSet?: boolean;
  offSetSpeedDeltaPct?: number | null;
  tradeUrl?: string;
  isIdentical?: boolean;
  en?: {
    label?: string;
    myItemName?: string;
  };
}

export interface AffixImportance {
  line: string;
  tier: "must" | "nice" | "cut";
  deltaDpsPct: number;
  deltaEhpPct: number;
  triggeredRedlines?: RedlineAlert[];
  origin?: string;
  side?: "prefix" | "suffix" | "implicit" | "enchant" | "unknown";
  craftDifficulty?: "hard" | "medium" | "easy" | "unrollable";
  synergyTags?: string[];
  budgetAction?: "lock" | "downgrade" | "cut" | "skip";
  semiAction?: "lock" | "craft_later" | "skip";
}

export interface TreeDiffSummary {
  myAllocated: number;
  targetAllocated: number;
  missingKeystones: string[];
  extraKeystones: string[];
  missingAscendancy: string[];
  extraAscendancy: string[];
  missingNotables: string[];
  extraNotables: string[];
}

export interface SkillGapItem {
  kind: "missing_support" | "level_gap" | "quality_gap" | "missing_skill";
  gemName: string;
  detail: string;
  severity: "high" | "medium" | "low";
  en?: {
    detail?: string;
  };
}

export interface SkillsDiffSummary {
  myMainSkill: string;
  targetMainSkill: string;
  gaps: SkillGapItem[];
}

export interface BuildBriefSummary {
  className: string;
  ascendancy: string;
  level: number;
  mainSkill: string;
  archetype: string;
}

export interface GapAnalysisReport {
  mySummary: BuildBriefSummary;
  targetSummary: BuildBriefSummary;
  myStats: EngineStats;
  targetStats: EngineStats;
  myRedlines: RedlineAlert[];
  treeDiff: TreeDiffSummary;
  skillsDiff: SkillsDiffSummary;
  zeroCostSteps: SlotUpgradeStep[];
  slotUpgrades: SlotUpgradeStep[];
  elapsedMs: number;
}

export interface SlotSocketAnalysis {
  socketCount: number;
  naturalMaxSockets: number;
  exceedsCraftableSockets: boolean;
  hasSocketEffectMod: boolean;
  bondedActive: boolean;
  socketedRunes: string[];
  runeUnlockedPools: string[];
  runeDeltaDpsPct?: number;
  runeDeltaEhpPct?: number;
  runeSpiritLoss?: number;
  runeMaxResDrop?: number;
  advice: string;
}

export interface SlotRecommendationPlan {
  lockAffixes: string[];
  requireBaseType: boolean;
  requireSockets: number | null;
  defenseFloor?: { stat: "es" | "ev" | "ar" | "ward"; min: number } | null;
  summary: string;
  notes: string[];
}

export interface SlotAffixReport {
  slot: string;
  itemName: string;
  baseType: string;
  rarity: string;
  baseDps: number;
  baseEhp: number;
  swappedDps: number;
  swappedEhp: number;
  deltaDpsPct: number;
  deltaEhpPct: number;
  redlines: RedlineAlert[];
  affixes: AffixImportance[];
  mustAffixes: string[];
  cuttableAffixes: string[];
  passiveSynergies?: string[];
  socketAnalysis?: SlotSocketAnalysis;
  budgetPlan?: SlotRecommendationPlan;
  semiPlan?: SlotRecommendationPlan;
  elapsedMs: number;
}

export interface RoadmapComboVerification {
  cumulative?: boolean;
  slotsApplied: string[];
  dps: number;
  ehp: number;
  deltaDpsPct: number;
  deltaEhpPct: number;
  linearDeltaDpsPct: number;
  linearDeltaEhpPct: number;
  dpsSynergyDiffPct: number;
  redlines: RedlineAlert[];
  allGreen: boolean;
}

export interface RoadmapPhase {
  phase: number;
  title: string;
  subtitle: string;
  costDivine: number;
  expectedDeltaDpsPct: number;
  expectedDeltaEhpPct: number;
  verifiedCombo?: RoadmapComboVerification;
  steps: SlotUpgradeStep[];
  en?: {
    title?: string;
    subtitle?: string;
  };
}

export interface BudgetRoadmapReport {
  budgetDivine: number;
  spentDivine: number;
  remainingDivine: number;
  expectedDeltaDpsPct: number;
  expectedDeltaEhpPct: number;
  verifiedCombo?: RoadmapComboVerification;
  phases: RoadmapPhase[];
  deferred: SlotUpgradeStep[];
  unpriced: SlotUpgradeStep[];
}
