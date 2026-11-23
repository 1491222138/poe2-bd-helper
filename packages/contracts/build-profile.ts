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

export interface PassiveTreeSummary {
  treeVersion: string;
  totalAllocated: number;
  keystones: TreeNodeInfo[];
  ascendancyNodes: TreeNodeInfo[];
  notables: TreeNodeInfo[];
  weaponSet1Notables: TreeNodeInfo[];
  weaponSet2Notables: TreeNodeInfo[];
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
  mods: string[];
  rawText: string;
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

export interface BuildPositioning {
  archetype: string;
  oneLineIdea: string;
  scenarioTags: string[];
  budgetTier: "league-start" | "mid" | "high" | "mirror";
  coreUniques: string[];
  coreAffixes: string[];
  coreKeystones: string[];
  coreAscendancy: string[];
  pitfalls: string[];
}

export interface TargetBuildPayload {
  pobCode: string;
  sourceUrl?: string;
  title?: string;
  league?: string;
  positioning?: BuildPositioning;
  pushedAt: number;
}
