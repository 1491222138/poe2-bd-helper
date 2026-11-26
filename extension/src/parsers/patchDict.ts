/**
 * 浏览器扩展离线轻量版本词典（天赋大点/基石/升华含分支子节点 + 技能/血统辅助官方原文与关键数值）。
 *
 * 3 层热更新防过时机制：
 *   1. 启动/解析时优先读 chrome.storage.local 中已热更的最新词典，兜底读内置 data/patch_dict.json；
 *   2. 后端在线时，一键同步 GET /meta/patch-dict（本机 POB2_ROOT 最新版本）；
 *   3. 纯插件离线时，直接从 PoB2 官方 GitHub 仓库按 treeVersion 拉取 tree.json + Data/Skills/*.lua，
 *      在浏览器侧实时压缩并持久化到 chrome.storage.local（免重装插件即可跟进新赛季）。
 */
import type { AnointedNotableInfo, PassiveTreeSummary, TreeNodeInfo } from "@contracts";
import { DEFAULT_WORKBENCH_URL } from "../bridge/workbenchClient";

export interface CompactTreeNode {
  n: string;
  k: "k" | "a" | "n" | "j";
  a?: string;
  s?: string[];
}

export interface CompactGemInfo {
  d: string;
  s: 0 | 1;
  c?: string[];
}

export interface PatchDictData {
  schemaVersion: number;
  treeVersion: string;
  availableTreeVersions?: string[];
  generatedAt: string;
  nodeCount: number;
  gemCount: number;
  nodes: Record<string, CompactTreeNode>;
  gems: Record<string, CompactGemInfo>;
  source?: "bundled" | "local-backend" | "github-upstream";
}

const STORAGE_KEY = "poe2wb_patch_dict_v1";
const POB2_GITHUB_RAW_BASE =
  "https://raw.githubusercontent.com/PathOfBuildingCommunity/PathOfBuilding-PoE2/dev/src";

const SKILL_LUA_FILES = [
  "act_dex.lua",
  "act_int.lua",
  "act_str.lua",
  "sup_dex.lua",
  "sup_int.lua",
  "sup_str.lua",
  "other.lua",
  "minion.lua",
];

const HIGH_SIGNAL_STAT_RE =
  /ward_cost|empowers|maximum_critical|bifurcat|negated|weapon_set|whirling_slash|elemental_damage|additional_projectile|seal|barrage|cooldown|armour_break|intimidate|damage_\+%|damage_-%|attack_speed|cast_speed|skill_speed|projectile_speed|spirit|reservation|charge|rage|duration|totem|minion|leech|regen|recover|life_\+%|mana_\+%|evasion|energy_shield|physical_damage|fire_damage|cold_damage|lightning_damage|chaos_damage|crit|ailment|ignite|shock|chill|freeze|bleed|poison|daze|blind/i;

const NOISY_STAT_SUBSTRS = [
  "stat_description_mode",
  "animation_distance",
  "locked_duration",
  "acceleration_+%_per_second",
  "spread_angle",
  "bounce_check_angle",
  "action_distance",
  "step_distance",
  "desired_amount_override",
  "is_area_damage",
  "base_is_projectile",
  "skill_can_fire",
  "console_skill",
  "visual_",
  "display_",
  "hide_",
  "cannot_cancel",
];

let cachedDict: PatchDictData | null = null;
let loadingPromise: Promise<PatchDictData | null> | null = null;

export function compareTreeVersions(a: string, b: string): number {
  const pa = (a || "").split(/[._-]/).map((x) => parseInt(x, 10) || 0);
  const pb = (b || "").split(/[._-]/).map((x) => parseInt(x, 10) || 0);
  const len = Math.max(pa.length, pb.length);
  for (let i = 0; i < len; i++) {
    const da = pa[i] || 0;
    const db = pb[i] || 0;
    if (da !== db) return da - db;
  }
  return 0;
}

export function isDictOutdatedForTree(dict: PatchDictData | null, buildTreeVersion: string): boolean {
  if (!buildTreeVersion) return false;
  if (!dict || !dict.treeVersion) return true;
  return compareTreeVersions(buildTreeVersion, dict.treeVersion) > 0;
}

async function readStorageDict(): Promise<PatchDictData | null> {
  try {
    if (typeof chrome !== "undefined" && chrome.storage?.local) {
      const res = await chrome.storage.local.get(STORAGE_KEY);
      const val = res?.[STORAGE_KEY] as PatchDictData | undefined;
      if (val && val.nodes && val.gems) return val;
    } else if (typeof localStorage !== "undefined") {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const val = JSON.parse(raw) as PatchDictData;
        if (val && val.nodes && val.gems) return val;
      }
    }
  } catch {
    // ignore storage errors
  }
  return null;
}

async function writeStorageDict(dict: PatchDictData): Promise<void> {
  try {
    if (typeof chrome !== "undefined" && chrome.storage?.local) {
      await chrome.storage.local.set({ [STORAGE_KEY]: dict });
    } else if (typeof localStorage !== "undefined") {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(dict));
    }
  } catch {
    // ignore quota errors
  }
}

async function fetchBundledDict(): Promise<PatchDictData | null> {
  try {
    const url =
      typeof chrome !== "undefined" && chrome.runtime?.getURL
        ? chrome.runtime.getURL("data/patch_dict.json")
        : "./data/patch_dict.json";
    const res = await fetch(url);
    if (!res.ok) return null;
    const data = (await res.json()) as PatchDictData;
    if (data && data.nodes && data.gems) {
      return { ...data, source: "bundled" };
    }
  } catch {
    // non-browser / test env without fetchable public file
  }
  return null;
}

export async function loadPatchDict(): Promise<PatchDictData | null> {
  if (cachedDict) return cachedDict;
  if (!loadingPromise) {
    loadingPromise = (async () => {
      const [stored, bundled] = await Promise.all([readStorageDict(), fetchBundledDict()]);
      if (stored && bundled) {
        const pickStored =
          compareTreeVersions(stored.treeVersion, bundled.treeVersion) > 0 ||
          (stored.treeVersion === bundled.treeVersion && (stored.generatedAt || "") >= (bundled.generatedAt || ""));
        cachedDict = pickStored ? stored : bundled;
      } else {
        cachedDict = stored || bundled || null;
      }
      return cachedDict;
    })();
  }
  return loadingPromise;
}

export function setPatchDictForTest(dict: PatchDictData | null): void {
  cachedDict = dict;
  loadingPromise = null;
}

export function getCachedPatchDict(): PatchDictData | null {
  return cachedDict;
}

export function lookupGemInfo(name: string, dict?: PatchDictData | null): CompactGemInfo | null {
  const active = dict ?? cachedDict;
  if (!active?.gems || !name) return null;
  if (active.gems[name]) return active.gems[name];
  const lower = name.trim().toLowerCase();
  for (const [k, v] of Object.entries(active.gems)) {
    if (k.toLowerCase() === lower) return v;
  }
  return null;
}

export function lookupNotableByName(name: string, dict?: PatchDictData | null): CompactTreeNode | null {
  const active = dict ?? cachedDict;
  if (!active?.nodes || !name) return null;
  const lower = name.trim().toLowerCase();
  for (const node of Object.values(active.nodes)) {
    if ((node.k === "n" || node.k === "k" || node.k === "a") && node.n.toLowerCase() === lower) {
      return node;
    }
  }
  return null;
}

export function hydratePassiveTree(
  rawNodeIds: string[],
  ws1Ids: Set<string>,
  ws2Ids: Set<string>,
  treeVersion: string,
  anointedRaw: Array<{ slot: string; itemName: string; name: string }>,
  dict?: PatchDictData | null
): PassiveTreeSummary {
  const active = dict ?? cachedDict;
  const keystones: TreeNodeInfo[] = [];
  const ascendancyNodes: TreeNodeInfo[] = [];
  const notables: TreeNodeInfo[] = [];
  const weaponSet1Notables: TreeNodeInfo[] = [];
  const weaponSet2Notables: TreeNodeInfo[] = [];

  if (active?.nodes) {
    const seen = new Set<string>();
    for (const id of rawNodeIds) {
      if (!id || seen.has(id)) continue;
      seen.add(id);
      const entry = active.nodes[id];
      if (!entry) continue;
      const ws: 1 | 2 | null = ws1Ids.has(id) ? 1 : ws2Ids.has(id) ? 2 : null;
      const info: TreeNodeInfo = {
        id,
        name: entry.n,
        kind: entry.k === "k" ? "keystone" : entry.k === "a" ? "ascendancy" : entry.k === "n" ? "notable" : "jewel",
        ascendancyName: entry.a,
        weaponSet: ws,
        stats: entry.s || [],
      };
      if (entry.k === "k") {
        keystones.push(info);
      } else if (entry.k === "a") {
        ascendancyNodes.push(info);
      } else if (entry.k === "n") {
        notables.push(info);
        if (ws === 1) weaponSet1Notables.push(info);
        else if (ws === 2) weaponSet2Notables.push(info);
      }
    }
  }

  const anointedNotables: AnointedNotableInfo[] = anointedRaw.map((a) => {
    const found = lookupNotableByName(a.name, active);
    return {
      slot: a.slot,
      itemName: a.itemName,
      name: found?.n || a.name,
      stats: found?.s || [],
    };
  });

  return {
    treeVersion: treeVersion || active?.treeVersion || "",
    totalAllocated: rawNodeIds.length,
    keystones,
    ascendancyNodes,
    notables,
    weaponSet1Notables,
    weaponSet2Notables,
    anointedNotables,
  };
}

function parseGemsFromLuaText(txt: string, gemsOut: Record<string, CompactGemInfo>): void {
  const blockRe = /skills\["([^"]+)"\]\s*=\s*\{([\s\S]*?)\n\}/g;
  let m: RegExpExecArray | null;
  while ((m = blockRe.exec(txt)) !== null) {
    const body = m[2];
    const nm = /^\s*name\s*=\s*"([^"]+)"/m.exec(body);
    if (!nm) continue;
    const name = nm[1].trim();
    if (!name) continue;
    const dm = /^\s*description\s*=\s*"((?:[^"\\]|\\.)*)"/m.exec(body);
    const desc = dm ? dm[1].replace(/\\n/g, " ").replace(/\\"/g, '"').trim() : "";
    const isSup: 0 | 1 = /^\s*support\s*=\s*true/m.test(body) ? 1 : 0;

    const cstats: string[] = [];
    const seenC = new Set<string>();
    const constRe = /constantStats\s*=\s*\{([\s\S]*?)\n\s*\},/g;
    let cb: RegExpExecArray | null;
    while ((cb = constRe.exec(body)) !== null && cstats.length < 6) {
      const pairRe = /\{\s*"([^"]+)"\s*,\s*(-?\d+(?:\.\d+)?)\s*\}/g;
      let pm: RegExpExecArray | null;
      while ((pm = pairRe.exec(cb[1])) !== null && cstats.length < 6) {
        const k = pm[1];
        const kl = k.toLowerCase();
        if (NOISY_STAT_SUBSTRS.some((n) => kl.includes(n))) continue;
        if (!HIGH_SIGNAL_STAT_RE.test(kl)) continue;
        const tok = `${k}=${pm[2]}`;
        if (!seenC.has(tok)) {
          seenC.add(tok);
          cstats.push(tok);
        }
      }
    }
    if (!desc && cstats.length === 0) continue;
    const prev = gemsOut[name];
    if (prev && prev.d.length >= desc.length && (prev.c?.length || 0) >= cstats.length) continue;
    gemsOut[name] = cstats.length > 0 ? { d: desc, s: isSup, c: cstats } : { d: desc, s: isSup };
  }
}

/**
 * 热更新离线词典：
 * - 若本地后端在线，优先从 GET /meta/patch-dict?refresh=1 拉取本机 POB2_ROOT 最新词典；
 * - 若离线，直接从 PoB2 官方 GitHub Raw 按指定 treeVersion 拉取 tree.json + Skills/*.lua 压缩入本地缓存。
 */
export async function updatePatchDict(options?: {
  targetTreeVersion?: string;
  backendOnline?: boolean;
  backendBaseUrl?: string;
}): Promise<PatchDictData> {
  const baseUrl = (options?.backendBaseUrl || DEFAULT_WORKBENCH_URL).replace(/\/+$/, "");

  if (options?.backendOnline) {
    try {
      const res = await fetch(`${baseUrl}/meta/patch-dict?refresh=1`, { method: "GET" });
      if (res.ok) {
        const data = await res.json();
        if (data?.ok && data?.patchDict?.nodes && data?.patchDict?.gems) {
          const updated: PatchDictData = { ...data.patchDict, source: "local-backend" };
          cachedDict = updated;
          await writeStorageDict(updated);
          return updated;
        }
      }
    } catch {
      // fall through to GitHub upstream
    }
  }

  const ver = options?.targetTreeVersion || cachedDict?.treeVersion || "0_5";
  const treeRes = await fetch(`${POB2_GITHUB_RAW_BASE}/TreeData/${encodeURIComponent(ver)}/tree.json`);
  if (!treeRes.ok) {
    throw new Error(`从 PoB2 官方仓库拉取天赋树 v${ver} 失败 (HTTP ${treeRes.status})`);
  }
  const rawTree = await treeRes.json();
  const nodesRaw = (rawTree?.nodes || {}) as Record<string, Record<string, unknown>>;
  const compactNodes: Record<string, CompactTreeNode> = {};

  for (const [nid, node] of Object.entries(nodesRaw)) {
    if (!node || typeof node !== "object") continue;
    const isKs = Boolean(node.isKeystone);
    const isNot = Boolean(node.isNotable);
    const isMcOpt = Boolean(node.isMultipleChoiceOption);
    const isJewel = Boolean(node.isJewelSocket);
    const asc = String(node.ascendancyName || "").trim();
    const name = String(node.name || "").trim();
    const stats = Array.isArray(node.stats)
      ? node.stats.map((s) => String(s).trim()).filter(Boolean)
      : [];

    if (!(isKs || isNot || (asc && isMcOpt) || isJewel)) continue;
    const k: CompactTreeNode["k"] = isKs ? "k" : asc && (isNot || isMcOpt) ? "a" : isNot ? "n" : "j";
    const entry: CompactTreeNode = { n: name, k };
    if (asc) entry.a = asc;
    if (stats.length > 0) entry.s = stats;
    compactNodes[nid] = entry;
  }

  const compactGems: Record<string, CompactGemInfo> = {};
  await Promise.all(
    SKILL_LUA_FILES.map(async (fn) => {
      try {
        const r = await fetch(`${POB2_GITHUB_RAW_BASE}/Data/Skills/${fn}`);
        if (r.ok) {
          parseGemsFromLuaText(await r.text(), compactGems);
        }
      } catch {
        // tolerate individual file fetch glitches
      }
    })
  );

  if (Object.keys(compactGems).length === 0 && cachedDict?.gems) {
    Object.assign(compactGems, cachedDict.gems);
  }

  const updated: PatchDictData = {
    schemaVersion: 1,
    treeVersion: ver,
    generatedAt: new Date().toISOString().slice(0, 19).replace("T", " "),
    nodeCount: Object.keys(compactNodes).length,
    gemCount: Object.keys(compactGems).length,
    nodes: compactNodes,
    gems: compactGems,
    source: "github-upstream",
  };
  cachedDict = updated;
  await writeStorageDict(updated);
  return updated;
}
