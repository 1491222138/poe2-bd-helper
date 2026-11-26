/**
 * 抄作业 & AI 分析本地存储工具（localStorage 瞬时镜像 + 后端 ~/.poe2-workbench/copy_plans.json 同步合并）。
 *
 * 解决痛点：
 *  1. 刷新页面 / 切换做装比价页 / 重启工作台 / 切换本质拆解后，抄作业方案与每件装备的选词/已入手进度不再丢失；
 *  2. 同一目标 BD 可同时保存多套不同等级/预算档位的抄作业方案，一键切换或标记「已入手」，无需挑一件装备就重新分析。
 */
import type {
  CopyBuildContext,
  CopyPlanItem,
  CopyPlanItemUserState,
  SavedCopyPlanRecord,
} from "@contracts";

export const COPY_CONTEXT_STORAGE_KEY = "poe2wb.copyContext.v1";
export const COPY_PLANS_STORAGE_KEY = "poe2wb.copyPlans.v1";
export const ACTIVE_PLAN_MAP_STORAGE_KEY = "poe2wb.activePlanByBuild.v1";
export const MAX_LOCAL_COPY_PLANS = 40;

export const DEFAULT_COPY_CONTEXT: CopyBuildContext = {
  characterLevel: 90,
  budgetDivine: 15,
  ownedItems: [],
  goalNote: "",
  freeformQuery: { ascendancy: "", skillIdea: "" },
};

// 轻量纯 TS 同步 SHA-256（与 Python hashlib.sha256(s.encode("utf-8")).hexdigest()[:16] 逐字节一致）
const SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

export function sha256Hex(input: string): string {
  const bytes = new TextEncoder().encode(input);
  const bitLen = bytes.length * 8;
  const paddedLen = Math.ceil((bytes.length + 9) / 64) * 64;
  const buf = new Uint8Array(paddedLen);
  buf.set(bytes);
  buf[bytes.length] = 0x80;
  const view = new DataView(buf.buffer);
  view.setUint32(paddedLen - 8, Math.floor(bitLen / 0x100000000), false);
  view.setUint32(paddedLen - 4, bitLen >>> 0, false);

  let h0 = 0x6a09e667;
  let h1 = 0xbb67ae85;
  let h2 = 0x3c6ef372;
  let h3 = 0xa54ff53a;
  let h4 = 0x510e527f;
  let h5 = 0x9b05688c;
  let h6 = 0x1f83d9ab;
  let h7 = 0x5be0cd19;
  const w = new Uint32Array(64);

  for (let offset = 0; offset < paddedLen; offset += 64) {
    for (let i = 0; i < 16; i++) {
      w[i] = view.getUint32(offset + i * 4, false);
    }
    for (let i = 16; i < 64; i++) {
      const s0 =
        ((w[i - 15] >>> 7) | (w[i - 15] << 25)) ^
        ((w[i - 15] >>> 18) | (w[i - 15] << 14)) ^
        (w[i - 15] >>> 3);
      const s1 =
        ((w[i - 2] >>> 17) | (w[i - 2] << 15)) ^
        ((w[i - 2] >>> 19) | (w[i - 2] << 13)) ^
        (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }

    let a = h0;
    let b = h1;
    let c = h2;
    let d = h3;
    let e = h4;
    let f = h5;
    let g = h6;
    let h = h7;

    for (let i = 0; i < 64; i++) {
      const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
      const ch = (e & f) ^ (~e & g);
      const temp1 = (h + S1 + ch + SHA256_K[i] + w[i]) >>> 0;
      const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (S0 + maj) >>> 0;

      h = g;
      g = f;
      f = e;
      e = (d + temp1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) >>> 0;
    }

    h0 = (h0 + a) >>> 0;
    h1 = (h1 + b) >>> 0;
    h2 = (h2 + c) >>> 0;
    h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0;
    h5 = (h5 + f) >>> 0;
    h6 = (h6 + g) >>> 0;
    h7 = (h7 + h) >>> 0;
  }

  return [h0, h1, h2, h3, h4, h5, h6, h7].map((v) => v.toString(16).padStart(8, "0")).join("");
}

export function computePobHash(pobCode: string | null | undefined): string {
  const s = String(pobCode || "").trim();
  if (!s) return "";
  return sha256Hex(s).slice(0, 16);
}

export function buildCopyPlanItemKey(item: Pick<CopyPlanItem, "slot" | "itemName" | "baseType">, index: number): string {
  const name = (item.itemName || item.baseType || `#${index}`).trim();
  return `${(item.slot || "Slot").trim()}::${name}`;
}

export function readLocalCopyContext(): CopyBuildContext {
  try {
    if (typeof window === "undefined" || !window.localStorage) return { ...DEFAULT_COPY_CONTEXT };
    const raw = window.localStorage.getItem(COPY_CONTEXT_STORAGE_KEY);
    if (!raw) return { ...DEFAULT_COPY_CONTEXT };
    const parsed = JSON.parse(raw) as Partial<CopyBuildContext>;
    if (!parsed || typeof parsed !== "object") return { ...DEFAULT_COPY_CONTEXT };
    return {
      ...DEFAULT_COPY_CONTEXT,
      ...parsed,
      characterLevel:
        typeof parsed.characterLevel === "number" && Number.isFinite(parsed.characterLevel)
          ? Math.max(1, Math.min(100, Math.round(parsed.characterLevel)))
          : DEFAULT_COPY_CONTEXT.characterLevel,
      budgetDivine:
        typeof parsed.budgetDivine === "number" && Number.isFinite(parsed.budgetDivine)
          ? Math.max(0, parsed.budgetDivine)
          : DEFAULT_COPY_CONTEXT.budgetDivine,
      ownedItems: Array.isArray(parsed.ownedItems) ? parsed.ownedItems.map(String) : [],
      goalNote: typeof parsed.goalNote === "string" ? parsed.goalNote : "",
      freeformQuery: {
        ascendancy: parsed.freeformQuery?.ascendancy || "",
        skillIdea: parsed.freeformQuery?.skillIdea || parsed.goalNote || "",
      },
    };
  } catch {
    return { ...DEFAULT_COPY_CONTEXT };
  }
}

export function writeLocalCopyContext(ctx: CopyBuildContext): void {
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    window.localStorage.setItem(COPY_CONTEXT_STORAGE_KEY, JSON.stringify(ctx));
  } catch {
    // ignore storage errors
  }
}

export function readLocalCopyPlans(): SavedCopyPlanRecord[] {
  try {
    if (typeof window === "undefined" || !window.localStorage) return [];
    const raw = window.localStorage.getItem(COPY_PLANS_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return (parsed as SavedCopyPlanRecord[])
      .filter((p) => p && typeof p === "object" && typeof p.id === "string")
      .sort((a, b) => (b.updatedAt || b.createdAt || 0) - (a.updatedAt || a.createdAt || 0))
      .slice(0, MAX_LOCAL_COPY_PLANS);
  } catch {
    return [];
  }
}

export function writeLocalCopyPlans(plans: SavedCopyPlanRecord[]): void {
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    let list = plans.slice(0, MAX_LOCAL_COPY_PLANS);
    while (list.length >= 0) {
      try {
        window.localStorage.setItem(COPY_PLANS_STORAGE_KEY, JSON.stringify(list));
        return;
      } catch {
        if (list.length <= 1) return;
        list = list.slice(0, Math.max(1, Math.floor(list.length * 0.7)));
      }
    }
  } catch {
    // ignore
  }
}

export function readActivePlanIdForBuild(pobHash: string): string | null {
  try {
    if (typeof window === "undefined" || !window.localStorage) return null;
    const raw = window.localStorage.getItem(ACTIVE_PLAN_MAP_STORAGE_KEY);
    if (!raw) return null;
    const map = JSON.parse(raw) as Record<string, string>;
    return typeof map?.[pobHash || "__consult__"] === "string" ? map[pobHash || "__consult__"] : null;
  } catch {
    return null;
  }
}

export function writeActivePlanIdForBuild(pobHash: string, planId: string | null): void {
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    const raw = window.localStorage.getItem(ACTIVE_PLAN_MAP_STORAGE_KEY);
    const map: Record<string, string> = raw ? JSON.parse(raw) : {};
    const key = pobHash || "__consult__";
    if (planId) map[key] = planId;
    else delete map[key];
    window.localStorage.setItem(ACTIVE_PLAN_MAP_STORAGE_KEY, JSON.stringify(map));
  } catch {
    // ignore
  }
}

export function mergeCopyPlans(
  localPlans: SavedCopyPlanRecord[],
  remotePlans: SavedCopyPlanRecord[]
): SavedCopyPlanRecord[] {
  const byId = new Map<string, SavedCopyPlanRecord>();
  for (const p of localPlans) {
    if (p?.id) byId.set(p.id, p);
  }
  for (const r of remotePlans) {
    if (!r?.id) continue;
    const prev = byId.get(r.id);
    if (!prev) {
      byId.set(r.id, r);
    } else {
      const rTs = r.updatedAt || r.createdAt || 0;
      const pTs = prev.updatedAt || prev.createdAt || 0;
      if (rTs >= pTs) {
        byId.set(r.id, {
          ...prev,
          ...r,
          itemStates: { ...(prev.itemStates || {}), ...(r.itemStates || {}) },
        });
      } else {
        byId.set(r.id, {
          ...r,
          ...prev,
          itemStates: { ...(r.itemStates || {}), ...(prev.itemStates || {}) },
        });
      }
    }
  }
  return Array.from(byId.values())
    .sort((a, b) => (b.updatedAt || b.createdAt || 0) - (a.updatedAt || a.createdAt || 0))
    .slice(0, MAX_LOCAL_COPY_PLANS);
}

export function upsertCopyPlan(
  plans: SavedCopyPlanRecord[],
  record: SavedCopyPlanRecord
): SavedCopyPlanRecord[] {
  const filtered = plans.filter((p) => p.id !== record.id);
  return [record, ...filtered]
    .sort((a, b) => (b.updatedAt || b.createdAt || 0) - (a.updatedAt || a.createdAt || 0))
    .slice(0, MAX_LOCAL_COPY_PLANS);
}

export function patchCopyPlanItemState(
  plans: SavedCopyPlanRecord[],
  planId: string,
  itemKey: string,
  patch: Partial<CopyPlanItemUserState>
): SavedCopyPlanRecord[] {
  const now = Date.now();
  return plans.map((p) => {
    if (p.id !== planId) return p;
    const prevStates = p.itemStates || {};
    const prevItem = prevStates[itemKey] || {};
    return {
      ...p,
      updatedAt: now,
      itemStates: {
        ...prevStates,
        [itemKey]: {
          ...prevItem,
          ...patch,
        },
      },
    };
  });
}

export function findMatchingSavedPlan(
  plans: SavedCopyPlanRecord[],
  query: {
    mode: SavedCopyPlanRecord["mode"];
    pobHash: string;
    copyContext?: CopyBuildContext;
    league?: string;
  }
): SavedCopyPlanRecord | null {
  const normGoal = (query.copyContext?.goalNote || "").trim();
  const lvl = Math.round(query.copyContext?.characterLevel ?? 90);
  const bud = Number(query.copyContext?.budgetDivine ?? 15);
  const lg = (query.league || "Standard").trim();

  for (const p of plans) {
    if (p.mode !== query.mode) continue;
    if ((p.pobHash || "") !== (query.pobHash || "")) continue;
    if (query.mode === "target" || query.mode === "gap") {
      return p;
    }
    const pLvl = Math.round(p.copyContext?.characterLevel ?? p.copyPlan?.characterLevel ?? 90);
    const pBud = Number(p.copyContext?.budgetDivine ?? p.copyPlan?.budgetDivine ?? 15);
    const pGoal = (p.copyContext?.goalNote || "").trim();
    const pLg = (p.league || "Standard").trim();
    if (pLvl === lvl && Math.abs(pBud - bud) < 0.001 && pGoal === normGoal && pLg === lg) {
      return p;
    }
  }
  return null;
}

export function countCopyPlanProgress(record: SavedCopyPlanRecord | null | undefined): {
  done: number;
  total: number;
} {
  const items = record?.copyPlan?.items || [];
  if (items.length === 0) return { done: 0, total: 0 };
  const states = record?.itemStates || {};
  let total = 0;
  let done = 0;
  items.forEach((item, idx) => {
    if (item.action === "skip") return;
    total += 1;
    const key = buildCopyPlanItemKey(item, idx);
    if (states[key]?.purchased) done += 1;
  });
  return { done, total };
}
