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
}

export interface AffixImportance {
  line: string;
  tier: "must" | "nice" | "cut";
  deltaDpsPct: number;
  deltaEhpPct: number;
}
