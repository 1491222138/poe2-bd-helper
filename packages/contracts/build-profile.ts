/**
 * 目标 BD 定位与结构化解析契约（扩展 <-> 后端 <-> 桌面端共享）
 */

export interface TreeNodeInfo {
  id: string;
  name: string;
  kind: "keystone" | "ascendancy" | "notable" | "jewel";
  ascendancyName?: string;
  weaponSet?: 1 | 2 | null;
  stats: string[];
}

export interface AnointedNotableInfo {
  slot: string;
  itemName: string;
  name: string;
  stats: string[];
}

export interface PassiveTreeSummary {
  treeVersion: string;
  totalAllocated: number;
  keystones: TreeNodeInfo[];
  ascendancyNodes: TreeNodeInfo[];
  notables: TreeNodeInfo[];
  weaponSet1Notables: TreeNodeInfo[];
  weaponSet2Notables: TreeNodeInfo[];
  anointedNotables?: AnointedNotableInfo[];
}

export type ParsedModOrigin =
  | "implicit"
  | "enchant"
  | "fractured"
  | "desecrated"
  | "crafted"
  | "explicit"
  | "rune";

export interface ParsedModDetail {
  text: string;
  origin: ParsedModOrigin;
  isAnoint?: boolean;
  isBonded?: boolean;
}

export interface ParsedItem {
  slot: string;
  itemId: string;
  rarity: string;
  name: string;
  baseType: string;
  corrupted: boolean;
  socketCount: number;
  socketedRunes: string[];
  runeEntries?: string[];
  mods: string[];
  modDetails?: ParsedModDetail[];
  itemLevel?: number;
  rawText: string;
  levelReq?: number;
  properties?: Record<string, string>;
}

export interface ParsedGem {
  name: string;
  skillId: string;
  level: number;
  quality: number;
  weaponSet: string;
}

export interface ParsedSkillGroup {
  index: number;
  isMain: boolean;
  slot: string;
  label: string;
  weaponSet: string;
  gems: ParsedGem[];
}

export type ContentGrade = "S" | "A" | "B" | "C" | "D";

export type RadarAxisId =
  | "clearSpeed"
  | "bossing"
  | "mobility"
  | "survivability"
  | "sustain"
  | "costEfficiency";

export interface RadarDimensionScore {
  id: RadarAxisId;
  label: string;
  score: number;
  metric?: string;
  reason: string;
}

export interface ContentCapabilityItem {
  id:
    | "t15_mapping"
    | "juiced_t15_delirium"
    | "pinnacle_t0_t2"
    | "pinnacle_t4_uber"
    | "trials_sekhema_chaos";
  label: string;
  grade: ContentGrade;
  verdict: string;
  reason: string;
  en?: {
    label?: string;
    verdict?: string;
    reason?: string;
  };
}

export interface AscendancyVerdictSummary {
  ascendancy?: string;
  verdict: string;
  keyReasons?: string[];
  tradeoff?: string;
}

export interface ContentReadinessSummary {
  overallTier: string;
  overallGrade: ContentGrade;
  confidence: "ai" | "simulated" | "xml-stats" | "heuristic";
  summary: string;
  radar?: RadarDimensionScore[];
  ascendancyVerdict?: AscendancyVerdictSummary | null;
  items: ContentCapabilityItem[];
  model?: string;
  evaluatedAt?: number;
  engineMetricsSummary?: string;
  en?: {
    overallTier?: string;
    summary?: string;
  };
}

export interface GearTiersSummary {
  mandatory: string[];
  amplifier: string[];
  luxury: string[];
}

export interface BuildPositioning {
  archetype: string;
  oneLineIdea: string;
  defenseSystem?: string;
  scenarioTags: string[];
  budgetTier: "league-start" | "mid" | "high" | "mirror";
  coreUniques: string[];
  coreAffixes: string[];
  coreKeystones: string[];
  coreAscendancy: string[];
  pitfalls: string[];
  gearTiers?: GearTiersSummary;
  contentReadiness?: ContentReadinessSummary;
  analyzedBy?: "ai" | "engine-facts" | "fallback";
  en?: {
    archetype?: string;
    oneLineIdea?: string;
    scenarioTags?: string[];
    coreAffixes?: string[];
    pitfalls?: string[];
  };
}

export interface CopyBuildContext {
  characterLevel?: number;
  budgetDivine?: number;
  ownedItems?: string[];
  goalNote?: string;
  freeformQuery?: {
    ascendancy?: string;
    skillIdea?: string;
  };
}

export type CopyPlanActionKind =
  | "buy_unique"
  | "buy_finished"
  | "buy_budget"
  | "craft_semi"
  | "buy_base"
  | "skip";

export interface CopyPlanAffix {
  text: string;
  zh?: string;
  min?: number | null;
  max?: number | null;
  required?: boolean;
  statId?: string | null;
  statText?: string | null;
  statType?: string | null;
  matched?: boolean;
}

export interface CopyPlanBlueprintTerm {
  statId: string;
  text?: string;
  zh?: string;
  statText?: string;
  weight?: number;
  min?: number | null;
  bucket?: string;
  isResPool?: boolean;
}

export interface CopyPlanSearchBlueprint {
  equipmentFloors?: Record<string, number>;
  mandatoryHard?: CopyPlanBlueprintTerm[];
  mandatoryAnyOf?: Array<{
    label?: string;
    min: number;
    terms: CopyPlanBlueprintTerm[];
  }>;
  weightedGroups?: Array<{
    label?: string;
    bucket?: string;
    minScore?: number | null;
    terms: CopyPlanBlueprintTerm[];
  }>;
}

export interface CopyPlanPobVerification {
  replacedSlots?: string[];
  dps: number;
  dpsPctOfTarget: number;
  deltaDpsPct: number;
  ehp: number;
  ehpPctOfTarget: number;
  deltaEhpPct: number;
  physMaxHit?: number;
  fireRes: number;
  coldRes: number;
  lightningRes: number;
  chaosRes: number;
  spiritUnreserved: number;
  speed?: number;
  redlines: string[];
  allGreen: boolean;
}

export interface CopyPlanItem {
  slot: string;
  action: CopyPlanActionKind;
  priority: number;
  itemName: string;
  baseType?: string;
  rarity: string;
  budgetDivine?: number | null;
  minIlvl?: number | null;
  minSockets?: number | null;
  corrupted?: boolean | null;
  lockBase?: boolean;
  reason: string;
  searchMode?: "weighted_blueprint" | "weighted" | "and_affixes" | "unique" | "base";
  searchBlueprint?: CopyPlanSearchBlueprint | null;
  selectedAffixes: CopyPlanAffix[];
  cutAffixes?: string[];
  socketTip?: string;
  tradeUrl?: string;
  tradeQuery?: Record<string, unknown>;
  matchedStatCount?: number;
}

export interface CopyPlanStructured {
  budgetSummary?: string;
  budgetDivine?: number | null;
  characterLevel?: number | null;
  league?: string;
  items: CopyPlanItem[];
  pobVerification?: CopyPlanPobVerification | null;
  toolTrace?: Array<{
    round: number;
    tool: string;
    summary: string;
    ok: boolean;
  }>;
}

export interface CopyPlanItemUserState {
  purchased?: boolean;
  lockBase?: boolean;
  scoreScale?: number;
  checkedAffixes?: Record<number, boolean>;
  disabledWeightedIds?: Record<string, boolean>;
}

export interface SavedCopyPlanRecord {
  id: string;
  mode: "copy" | "consult" | "target" | "gap";
  pobHash: string;
  buildTitle: string;
  league: string;
  model: string;
  copyContext?: CopyBuildContext;
  text: string;
  reasoning?: string;
  copyPlan?: CopyPlanStructured | null;
  itemStates?: Record<string, CopyPlanItemUserState>;
  createdAt: number;
  updatedAt: number;
}

export type LlmAnalyzeMode = "target" | "copy" | "consult" | "gap" | "rate";

export type LlmStreamEvent =
  | { type: "meta"; model: string; cached?: boolean; mode?: LlmAnalyzeMode }
  | {
      type: "tool_status";
      stage: "thinking" | "running" | "done";
      round?: number;
      tool?: string;
      summary: string;
      result?: CopyPlanPobVerification | null;
    }
  | { type: "reasoning"; delta: string }
  | { type: "delta"; delta: string }
  | {
      type: "structured";
      structured: Record<string, unknown>;
      positioning?: BuildPositioning;
      copyPlan?: CopyPlanStructured | null;
    }
  | {
      type: "done";
      text: string;
      reasoning?: string;
      model: string;
      cached?: boolean;
      structured?: Record<string, unknown> | null;
      positioning?: BuildPositioning | null;
      copyPlan?: CopyPlanStructured | null;
      savedPlan?: SavedCopyPlanRecord | null;
    }
  | { type: "error"; error: string };

export interface TargetBuildPayload {
  pobCode: string;
  sourceUrl?: string;
  title?: string;
  league?: string;
  positioning?: BuildPositioning;
  pushedAt: number;
}

export interface WeaponSetRole {
  role: "damage" | "caster" | "speed" | "utility" | "unknown";
  label: string;
  detail: string;
  attackSpeed: number;
  en?: {
    label?: string;
    detail?: string;
  };
}

export interface BuildIntentException {
  ruleId: string;
  targetId: string;
  reason: string;
  en?: {
    reason?: string;
  };
}

export interface ScalingVector {
  key: string;
  label: string;
  note: string;
  en?: {
    label?: string;
    note?: string;
  };
}

export interface BuildIntentSummary {
  mainSkill: string;
  archetype: string;
  hasDualWeaponSet: boolean;
  weaponSetRoles: {
    set1?: WeaponSetRole;
    set2?: WeaponSetRole;
  };
  keyScalingVectors: ScalingVector[];
  intentionalExceptions: BuildIntentException[];
  hasTravelSkill: boolean;
  en?: {
    archetype?: string;
  };
}

export interface BuildHealthIssue {
  ruleId: string;
  category: "defense" | "resource" | "attribute" | "skill" | "gear" | "comfort";
  severity: "fail" | "warn" | "info";
  title: string;
  detail: string;
  targetId: string;
  suppressedByIntent?: {
    reason: string;
    en?: {
      reason?: string;
    };
  };
  en?: {
    title?: string;
    detail?: string;
  };
}

export interface CoreGearHighlight {
  slot: string;
  name: string;
  baseType: string;
  rarity: string;
  roleTag: string;
  keyMods: string[];
  importance: number;
  socketCount: number;
  corrupted: boolean;
  impactDpsPct?: number;
  impactEhpPct?: number;
  impactManaPct?: number;
  impactNote?: string;
  aiNote?: string;
  en?: {
    roleTag?: string;
  };
}

export interface TravelSkillSimInfo {
  name: string;
  level: number;
  weaponSet: number;
  usesPerSec: number;
  speedWs1: number;
  speedWs2: number;
  cooldownSec: number;
  castTimeSec: number;
  skillMoveSpeedPct: number;
  dps: number;
}

export interface SimStatsSummary {
  metricsVersion?: number;
  dps: number;
  dpsSource: string;
  mainSkillName?: string;
  mainSkillLevel?: number;
  skillArchetype?: string;
  activeTotemLimit?: number;
  skillPartName?: string;
  skillStageCount?: number;
  skillStageMax?: number;
  duration?: number;
  durationMod?: number;
  projSpeedMod?: number;
  projectileCount?: number;
  aoeRadius?: number;
  aoeMod?: number;
  sealMax?: number;
  cooldown?: number;
  triggerRate?: number;
  enemyIsBoss?: string;
  ehp: number;
  life: number;
  lifeUnreserved?: number;
  lifeUnreservedPct?: number;
  es: number;
  ward?: number;
  deflectChance?: number;
  deflectionRating?: number;
  mana: number;
  manaCost?: number;
  manaPerSecCost?: number;
  manaRegen?: number;
  spirit: number;
  spiritUnreserved: number;
  fireRes: number;
  coldRes: number;
  lightningRes: number;
  chaosRes: number;
  critChance: number;
  preEffectiveCritChance?: number;
  critMult: number;
  speed: number;
  moveSpeedMod: number;
  moveSpeed?: number;
  skillMoveSpeed?: number;
  actionSpeed?: number;
  mobilitySummary?: string;
  travelSkills?: TravelSkillSimInfo[];
  physDR?: number;
  evadeChance?: number;
  blockChance?: number;
  lifeRegen?: number;
  lifeLeech?: number;
  lifeRecovery?: number;
  esRecharge?: number;
  esRecovery?: number;
  physMaxHit: number;
  fireMaxHit?: number;
  coldMaxHit?: number;
  lightningMaxHit?: number;
  eleMaxHit?: number;
  chaosMaxHit: number;
  chaosMaxHitInfinite?: boolean;
  reqStr: number;
  reqDex: number;
  reqInt: number;
  str: number;
  dex: number;
  int: number;
}

export interface ParsedBuildData {
  character: {
    className: string;
    ascendancy: string;
    level: number;
    mainSocketGroup: number;
    stats: Record<string, number>;
  };
  passiveTree: PassiveTreeSummary;
  weaponSets: {
    hasDualWeaponSet: boolean;
    set1: ParsedItem[];
    set2: ParsedItem[];
  };
  equippedItems: ParsedItem[];
  jewels: ParsedItem[];
  flasksAndCharms: ParsedItem[];
  skillGroups: ParsedSkillGroup[];
  config?: Record<string, string | number | boolean>;
  notes: string;
}

export interface BuildProfileResponse {
  source: "backend" | "local-fallback";
  build: ParsedBuildData;
  positioning: BuildPositioning;
  intent: BuildIntentSummary;
  coreGearHighlights: CoreGearHighlight[];
  healthChecks: BuildHealthIssue[];
  simulation?: SimStatsSummary | null;
  engineFacts?: Record<string, unknown> | null;
  aiAnalysis?: {
    text: string;
    structured?: Record<string, unknown> | null;
    model: string;
    cached?: boolean;
  } | null;
  aiError?: string;
}

export interface TradeOpenRequest {
  id: string;
  league: string;
  slot: string;
  kind: "exact" | "budget" | "semi" | "base";
  itemName?: string;
  label?: string;
  tradeUrl?: string;
  query?: Record<string, unknown>;
  createdAt: number;
  acked?: boolean;
}

export type AffixPurposeKind =
  | "core_dps"
  | "core_defense"
  | "synergy"
  | "threshold"
  | "sustain"
  | "utility"
  | "cuttable"
  | "dead";

export interface AblatedAffixEntry {
  line: string;
  origin: string;
  side: string;
  tier: "must" | "nice" | "cut";
  purposeKind: AffixPurposeKind;
  purposeLabel: string;
  purposeDetail: string;
  functionalLoss?: string;
  dpsLossPct: number;
  ehpLossPct: number;
  manaLossPct: number;
  spiritLoss: number;
  maxResDrop: number;
  attrBroken: boolean;
  triggeredRedlines: string[];
  synergyTags: string[];
  craftDifficulty: string;
  copyAction: "lock" | "downgrade" | "craft_later" | "cut" | "skip";
}

export interface ItemCostSummary {
  estimatedDivine: number | null;
  exactDivine?: number | null;
  budgetDivine?: number | null;
  semiDivine?: number | null;
  priceSource: "trade" | "ninja" | "manual" | "estimate" | "unknown";
  listings?: number | null;
  tradeUrl?: string | null;
  costSharePct?: number | null;
  dpsPerDivine?: number | null;
  ehpPerDivine?: number | null;
}

export interface SocketedItemAblationEntry {
  index: number;
  name: string;
  runeType: string;
  runeTypeLabel: string;
  effects: string[];
  bondedEffects: string[];
  bondedActive: boolean;
  unlockPools: string[];
  grantsKeystone?: string;
  isSocketBound?: boolean;
  isUnparsed?: boolean;
  estimatedDivine?: number | null;
  priceSource?: string;
  dpsLossPct: number;
  ehpLossPct: number;
  physMaxHitLossPct?: number;
  eleMaxHitLossPct?: number;
  esLossPct?: number;
  lifeLossPct?: number;
  manaLossPct?: number;
  spiritLoss: number;
  maxResDrop: number;
  attrBroken?: boolean;
  triggeredRedlines?: string[];
  tier: "must" | "nice" | "cut";
  purposeKind: AffixPurposeKind;
  purposeLabel: string;
  purposeDetail: string;
  copyAdvice: string;
}

export interface ItemRuneImpact {
  dpsLossPct: number;
  ehpLossPct: number;
  physMaxHitLossPct?: number;
  eleMaxHitLossPct?: number;
  spiritLoss: number;
  maxResDrop: number;
  advice: string;
  socketCount: number;
  exceedsCraftableSockets: boolean;
  socketedRunes: string[];
  socketedItems?: SocketedItemAblationEntry[];
}

export interface ItemAblationDetail {
  slot: string;
  kind: "item" | "jewel";
  itemId: string;
  name: string;
  baseType: string;
  rarity: string;
  corrupted: boolean;
  socketCount: number;
  levelReq: number;
  requireBaseType?: boolean;
  baseTypeReason?: string;
  importantGrantedSkills?: Array<{
    skillName: string;
    level?: number | null;
    linkedSupports?: string[];
    functionalDesc?: string;
    functionalLoss?: string;
    isImportant?: boolean;
  }>;
  roleKind: "core_dps" | "core_defense" | "threshold" | "mechanic" | "utility" | "luxury";
  roleLabel: string;
  coreRoleSummary: string;
  copyTier: "mandatory" | "core" | "flexible" | "luxury";
  copyTierLabel: string;
  copyAdvice: string;
  dpsLossPct: number;
  ehpLossPct: number;
  manaLossPct: number;
  spiritLoss: number;
  maxResDrop: number;
  attrBroken: boolean;
  triggeredRedlines: string[];
  compensatedDpsLossPct?: number | null;
  compensatedEhpLossPct?: number | null;
  inactiveWeaponSet: boolean;
  hasUnparsedMods: boolean;
  impactScore: number;
  note: string;
  cost: ItemCostSummary;
  affixStats: {
    total: number;
    must: number;
    nice: number;
    cut: number;
    dead: number;
  };
  affixes: AblatedAffixEntry[];
  runeImpact?: ItemRuneImpact | null;
  passiveSynergies: string[];
  budgetPlan?: Record<string, unknown>;
  semiPlan?: Record<string, unknown>;
}

export interface GemAblationEntry {
  name: string;
  skillId: string;
  level: number;
  quality: number;
  isSupport: boolean;
  isMainGem: boolean;
  tested: boolean;
  dpsLossPct: number;
  ehpLossPct: number;
  physMaxHitLossPct?: number;
  eleMaxHitLossPct?: number;
  maxResDrop?: number;
  evadeDrop?: number;
  deflectDrop?: number;
  lifeRecoveryDrop?: number;
  esRecoveryDrop?: number;
  manaCostDeltaPct: number;
  spiritSaved: number;
  downgrade2020DpsLossPct?: number | null;
  downgrade2020EhpLossPct?: number | null;
  isDefensive?: boolean;
  defenseLayers?: string[];
  roleLabel: string;
  purposeDetail: string;
  copyAdvice: string;
}

export interface SkillDefenseAnalysis {
  isDefensive: boolean;
  defenseLayers: string[];
  defenseSummary: string;
  defenseScore: number;
  physMaxHitLossPct: number;
  eleMaxHitLossPct: number;
  esLossPct: number;
  lifeLossPct: number;
  armourLossPct: number;
  evadeDrop: number;
  deflectDrop: number;
  blockDrop: number;
  lifeRecoveryDrop: number;
  esRecoveryDrop: number;
}

export interface SkillDefenseHighlight {
  index: number;
  mainGemName: string;
  roleLabel: string;
  ehpLossPct: number;
  physMaxHitLossPct: number;
  eleMaxHitLossPct: number;
  maxResDrop: number;
  evadeDrop: number;
  deflectDrop: number;
  lifeRecoveryDrop: number;
  esRecoveryDrop: number;
  spiritSaved: number;
  defenseLayers: string[];
  defenseSummary: string;
  defenseScore: number;
}

export interface SkillGroupAblationDetail {
  index: number;
  isMain: boolean;
  mainGemName: string;
  slot: string;
  weaponSet: string;
  roleKind: "main_dps" | "spirit_aura" | "buff_debuff" | "defense" | "trigger" | "travel" | "utility";
  roleLabel: string;
  coreRoleSummary: string;
  dpsLossPct: number;
  ehpLossPct: number;
  physMaxHitLossPct?: number;
  eleMaxHitLossPct?: number;
  evadeDrop?: number;
  deflectDrop?: number;
  blockDrop?: number;
  lifeRecoveryDrop?: number;
  esRecoveryDrop?: number;
  manaLossPct: number;
  spiritSaved: number;
  maxResDrop: number;
  skillOwnDps: number;
  maxDowngrade2020LossPct?: number | null;
  defenseAnalysis?: SkillDefenseAnalysis | null;
  copyTier: "mandatory" | "core" | "flexible" | "luxury";
  copyTierLabel: string;
  copyAdvice: string;
  gems: GemAblationEntry[];
  note: string;
}

export interface AscendancyImpactMetrics {
  dpsLossPct: number;
  ehpLossPct: number;
  physMaxHitLossPct: number;
  eleMaxHitLossPct: number;
  manaLossPct?: number;
  spiritLoss: number;
  maxResDrop: number;
  evadeDrop?: number;
  deflectDrop?: number;
  moveSpeedDrop?: number;
  speedLossPct?: number;
  attrBroken?: boolean;
  triggeredRedlines?: string[];
}

export interface AscendancyNodeAblation {
  id: string;
  nodeIds: string[];
  name: string;
  stats: string[];
  unlocksNotables: string[];
  hasBranchDescendants: boolean;
  isolated: AscendancyImpactMetrics;
  branch: AscendancyImpactMetrics;
  impactScore: number;
  roleKind: "core_both" | "core_dps" | "core_defense" | "threshold" | "mobility" | "mechanic" | "prerequisite" | "utility";
  roleLabel: string;
  importanceTier: "core" | "key" | "prerequisite" | "utility";
  isMechanical: boolean;
  grantedSkill?: string;
  selectionReason: string;
}

export interface AscendancyAblationSummary {
  ascendancy: string;
  allocatedNodeCount: number;
  notableCount: number;
  totalImpact: AscendancyImpactMetrics;
  nodes: AscendancyNodeAblation[];
  whyChosenSummary: string;
}

export interface BuildAblationDossier {
  buildHash: string;
  league?: string;
  generatedAt: number;
  elapsedMs: number;
  cached?: boolean;
  totalEstimatedDivine: number;
  gemsCostDivine?: number;
  pricedGems?: Array<{ name: string; divineValue: number; formatted: string }>;
  pricedItemCount: number;
  unpricedItemCount?: number;
  totalItemCount: number;
  summary: {
    mandatoryCount: number;
    coreCount: number;
    luxuryCount: number;
    totalAffixesTested: number;
    totalSocketedItemsTested?: number;
    ascendancyNodeCount?: number;
    defensiveSkillCount?: number;
    mustAffixCount: number;
    cuttableAffixCount: number;
    totalSimRuns: number;
  };
  items: ItemAblationDetail[];
  jewels: ItemAblationDetail[];
  skills: SkillGroupAblationDetail[];
  ascendancy?: AscendancyAblationSummary | null;
  skillDefenseSummary?: SkillDefenseHighlight[];
  copyFoundation: {
    topPriorities: string[];
    budgetCuts: string[];
    redlineAnchors: string[];
  };
}
