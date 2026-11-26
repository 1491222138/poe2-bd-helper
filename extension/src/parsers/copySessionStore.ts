/**
 * 浏览器扩展侧边栏「抄作业 & 拆解」本地持久化存储（chrome.storage.local，回退 localStorage）。
 * 切换标签页 / 打开市集挑装备 / 关闭重开侧边栏时自动恢复上下文、AI 抄作业清单与「已入手」勾选进度。
 */
import type { CopyBuildContext, CopyPlanStructured } from "@contracts";

const EXT_COPY_SESSION_KEY = "poe2wb_ext_copy_session_v1";
const MAX_SAVED_BUILDS = 12;

export interface ExtSavedBuildAnalysis {
  pobKey: string;
  aiText: string;
  aiReasoning: string;
  aiModel: string;
  copyText: string;
  copyReasoning: string;
  copyPlan: CopyPlanStructured | null;
  purchasedKeys: Record<string, boolean>;
  updatedAt: number;
}

export interface ExtCopySession {
  copyContext: CopyBuildContext;
  lastInput: string;
  lastSourceUrl?: string;
  lastPobCode: string;
  savedByBuild: Record<string, ExtSavedBuildAnalysis>;
}

export const DEFAULT_EXT_COPY_CONTEXT: CopyBuildContext = {
  characterLevel: 90,
  budgetDivine: 15,
  ownedItems: [],
  goalNote: "",
  freeformQuery: { ascendancy: "", skillIdea: "" },
};

export function makeExtPobKey(pobCode: string): string {
  const s = (pobCode || "").trim();
  if (!s) return "__consult__";
  // 快速同步 FNV-1a 32-bit x2 指纹（无需异步 WebCrypto）
  let h1 = 0x811c9dc5;
  let h2 = 0x9e3779b9;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    h1 ^= c;
    h1 = Math.imul(h1, 0x01000193) >>> 0;
    h2 ^= c + i;
    h2 = Math.imul(h2, 0x85ebca6b) >>> 0;
  }
  return `${h1.toString(16).padStart(8, "0")}${h2.toString(16).padStart(8, "0")}_${s.length}`;
}

export async function loadExtCopySession(): Promise<ExtCopySession | null> {
  try {
    let raw: unknown = null;
    if (typeof chrome !== "undefined" && chrome.storage?.local) {
      const res = await chrome.storage.local.get(EXT_COPY_SESSION_KEY);
      raw = res?.[EXT_COPY_SESSION_KEY];
    } else if (typeof localStorage !== "undefined") {
      const str = localStorage.getItem(EXT_COPY_SESSION_KEY);
      raw = str ? JSON.parse(str) : null;
    }
    if (!raw || typeof raw !== "object") return null;
    const obj = raw as Partial<ExtCopySession>;
    return {
      copyContext: { ...DEFAULT_EXT_COPY_CONTEXT, ...(obj.copyContext || {}) },
      lastInput: typeof obj.lastInput === "string" ? obj.lastInput : "",
      lastSourceUrl: typeof obj.lastSourceUrl === "string" ? obj.lastSourceUrl : undefined,
      lastPobCode: typeof obj.lastPobCode === "string" ? obj.lastPobCode : "",
      savedByBuild:
        obj.savedByBuild && typeof obj.savedByBuild === "object"
          ? (obj.savedByBuild as Record<string, ExtSavedBuildAnalysis>)
          : {},
    };
  } catch {
    return null;
  }
}

export async function saveExtCopySession(session: ExtCopySession): Promise<void> {
  try {
    // 按 updatedAt 保留最近 MAX_SAVED_BUILDS 条，防止超出配额
    const entries = Object.entries(session.savedByBuild || {})
      .sort((a, b) => (b[1]?.updatedAt || 0) - (a[1]?.updatedAt || 0))
      .slice(0, MAX_SAVED_BUILDS);
    const trimmed: ExtCopySession = {
      ...session,
      savedByBuild: Object.fromEntries(entries),
    };
    if (typeof chrome !== "undefined" && chrome.storage?.local) {
      await chrome.storage.local.set({ [EXT_COPY_SESSION_KEY]: trimmed });
    } else if (typeof localStorage !== "undefined") {
      localStorage.setItem(EXT_COPY_SESSION_KEY, JSON.stringify(trimmed));
    }
  } catch {
    // ignore storage quota / permission issues
  }
}
