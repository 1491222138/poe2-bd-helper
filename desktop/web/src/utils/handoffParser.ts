/**
 * 解析扩展离线交接包 JSON（poe2-workbench-handoff/v1）或原始 PoB2 分享码，
 * 以及桌面端通用的数值/差值格式化工具。
 */
import type { CopyPlanAffix, CopyPlanItem, PriceSnapshot, TargetBuildPayload } from "@contracts";

export interface ParsedPasteInput {
  kind: "handoff" | "pob";
  pobCode: string;
  target?: TargetBuildPayload;
  prices?: PriceSnapshot;
}

export function parseTargetPasteInput(raw: string): ParsedPasteInput {
  const s = (raw || "").trim();
  if (!s) {
    throw new Error("输入内容为空");
  }
  if (s.startsWith("{")) {
    try {
      const obj = JSON.parse(s) as {
        schema?: string;
        target?: TargetBuildPayload;
        prices?: PriceSnapshot;
      };
      if (obj.schema === "poe2-workbench-handoff/v1" && obj.target?.pobCode) {
        return {
          kind: "handoff",
          pobCode: obj.target.pobCode,
          target: obj.target,
          prices: obj.prices,
        };
      }
    } catch {
      // fall through as raw text
    }
  }
  return { kind: "pob", pobCode: s };
}

export function fmtCompact(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "-";
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (Math.abs(n) >= 10_000) return `${(n / 1_000).toFixed(1)}k`;
  return Math.round(n).toLocaleString();
}

export function fmtDeltaPct(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "-";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(1)}%`;
}

export function buildOfficialTradeSearchUrl(league: string, itemName: string, baseType: string): string {
  const encLeague = encodeURIComponent(league || "Standard");
  const query: Record<string, unknown> = {
    query: {
      status: { option: "available" },
      ...(baseType ? { type: baseType } : itemName ? { name: itemName } : {}),
    },
    sort: { price: "asc" },
  };
  return `https://www.pathofexile.com/trade2/search/poe2/${encLeague}?q=${encodeURIComponent(JSON.stringify(query))}`;
}

export function inferTradeCategoryClient(slot: string, baseType = ""): string | null {
  const combined = `${slot || ""} ${baseType || ""}`.toLowerCase();
  if (combined.includes("jewel") || /diamond|emerald|ruby|sapphire|time-lost/.test(baseType.toLowerCase())) {
    return "jewel";
  }
  if (combined.includes("helmet")) return "armour.helmet";
  if (combined.includes("body") || combined.includes("chest")) return "armour.chest";
  if (combined.includes("glove")) return "armour.gloves";
  if (combined.includes("boot")) return "armour.boots";
  if (combined.includes("amulet")) return "accessory.amulet";
  if (combined.includes("ring")) return "accessory.ring";
  if (combined.includes("belt")) return "accessory.belt";
  if (combined.includes("quiver")) return "armour.quiver";
  if (combined.includes("focus")) return "armour.focus";
  if (combined.includes("buckler")) return "armour.buckler";
  if (combined.includes("shield")) return "armour.shield";
  if (combined.includes("spear")) return "weapon.spear";
  if (combined.includes("crossbow")) return "weapon.crossbow";
  if (combined.includes("bow")) return "weapon.bow";
  if (combined.includes("quarterstaff") || combined.includes("warstaff")) return "weapon.warstaff";
  if (combined.includes("staff")) return "weapon.staff";
  if (combined.includes("wand")) return "weapon.wand";
  if (combined.includes("sceptre")) return "weapon.sceptre";
  return null;
}

export function buildTradeUrlFromCopyItem(
  item: CopyPlanItem,
  league: string,
  checkedAffixes: CopyPlanAffix[],
  lockBase: boolean,
  options?: {
    disabledWeightedIds?: Set<string>;
    scoreScale?: number;
  }
): string {
  const encLeague = encodeURIComponent(league || "Standard");
  const isUnique = item.rarity.toUpperCase() === "UNIQUE" || item.action === "buy_unique";
  const query: Record<string, unknown> = { status: { option: "available" } };
  const filters: Record<string, Record<string, Record<string, unknown>>> = {};

  if (isUnique && item.itemName && item.action !== "buy_base") {
    query.name = item.itemName;
    if (lockBase && item.baseType && item.baseType.toLowerCase() !== item.itemName.toLowerCase()) {
      query.type = item.baseType;
    }
  } else if (lockBase && item.baseType) {
    query.type = item.baseType;
  } else {
    const cat = inferTradeCategoryClient(item.slot, item.baseType || "");
    if (cat) {
      filters.type_filters = { filters: { category: { option: cat } } };
    } else if (item.baseType) {
      query.type = item.baseType;
    }
  }

  if (item.action === "buy_base" && !isUnique) {
    const tf = filters.type_filters?.filters || {};
    tf.rarity = { option: "normal" };
    if (item.minIlvl && item.minIlvl >= 1) tf.ilvl = { min: item.minIlvl };
    filters.type_filters = { filters: tf };
    filters.misc_filters = { filters: { corrupted: { option: "false" } } };
  } else if (item.action === "craft_semi" && !isUnique) {
    filters.misc_filters = { filters: { corrupted: { option: "false" } } };
    if (item.minIlvl && item.minIlvl >= 70) {
      const tf = filters.type_filters?.filters || {};
      tf.ilvl = { min: item.minIlvl };
      filters.type_filters = { filters: tf };
    }
  } else if (item.corrupted === false) {
    filters.misc_filters = { filters: { corrupted: { option: "false" } } };
  }

  const eqFilters: Record<string, unknown> = {};
  if (item.minSockets && item.minSockets >= 2) {
    eqFilters.total_augment_sockets = { min: item.minSockets };
  }
  const bp = item.searchBlueprint;
  const scoreScale = options?.scoreScale && options.scoreScale > 0 ? options.scoreScale : 1.0;
  if (bp?.equipmentFloors && !isUnique && item.action !== "buy_base") {
    for (const [ek, ev] of Object.entries(bp.equipmentFloors)) {
      if (typeof ev !== "number" || ev <= 0) continue;
      if (ek === "reload_time") {
        const scaledMax = Math.round(ev * (1.0 + Math.max(0, 1.0 - scoreScale) * 0.25) * 100) / 100;
        eqFilters.reload_time = { max: scaledMax > 0 ? scaledMax : ev };
      } else if (ek === "edps_max") {
        const cur = (eqFilters.edps as Record<string, number> | undefined) || {};
        eqFilters.edps = { ...cur, max: Math.ceil(ev) };
      } else if (ek === "pdps_max") {
        const cur = (eqFilters.pdps as Record<string, number> | undefined) || {};
        eqFilters.pdps = { ...cur, max: Math.ceil(ev) };
      } else {
        const scaled =
          ek === "aps" || ek === "crit"
            ? Math.round(ev * (0.95 + 0.05 * scoreScale) * 100) / 100
            : Math.floor(ev * Math.min(1.1, Math.max(0.75, scoreScale)));
        const cur = (eqFilters[ek] as Record<string, number> | undefined) || {};
        eqFilters[ek] = { ...cur, min: scaled > 0 ? scaled : ev };
      }
    }
  }
  if (Object.keys(eqFilters).length > 0) {
    filters.equipment_filters = { filters: eqFilters };
  }

  if (Object.keys(filters).length > 0) {
    query.filters = filters;
  }

  if (item.action !== "buy_base") {
    if (bp && !isUnique) {
      const statGroups: Array<Record<string, unknown>> = [];
      if (bp.mandatoryHard && bp.mandatoryHard.length > 0) {
        const mf = bp.mandatoryHard
          .filter((t) => Boolean(t.statId))
          .map((t) => ({
            id: t.statId,
            disabled: false,
            ...(t.min !== null && t.min !== undefined ? { value: { min: t.min } } : {}),
          }));
        if (mf.length > 0) statGroups.push({ type: "and", filters: mf });
      }
      if (bp.mandatoryAnyOf && bp.mandatoryAnyOf.length > 0) {
        for (const grp of bp.mandatoryAnyOf) {
          const cf = (grp.terms || [])
            .filter((t) => Boolean(t.statId))
            .map((t) => ({
              id: t.statId,
              disabled: false,
              ...(t.min !== null && t.min !== undefined ? { value: { min: t.min } } : {}),
            }));
          if (cf.length > 0) {
            statGroups.push({ type: "count", value: { min: Math.max(1, Math.min(cf.length, grp.min || 1)) }, filters: cf });
          }
        }
      }
      if (bp.weightedGroups && bp.weightedGroups.length > 0) {
        const disabledIds = options?.disabledWeightedIds;
        for (const wg of bp.weightedGroups) {
          const wf = (wg.terms || [])
            .filter((t) => Boolean(t.statId) && (!disabledIds || !disabledIds.has(t.statId)))
            .map((t) => ({
              id: t.statId,
              disabled: false,
              value: { weight: Math.round((t.weight || 1) * 100) / 100 },
            }));
          if (wf.length > 0) {
            const g: Record<string, unknown> = { type: "weight", filters: wf.slice(0, 14) };
            if (typeof wg.minScore === "number" && wg.minScore > 0) {
              g.value = { min: Math.round(wg.minScore * scoreScale * 10) / 10 };
            }
            statGroups.push(g);
          }
        }
      }
      if (statGroups.length > 0) {
        query.stats = statGroups;
      }
    } else {
      const seen = new Set<string>();
      const statFilters: Array<Record<string, unknown>> = [];
      for (const aff of checkedAffixes) {
        if (!aff.statId || seen.has(aff.statId)) continue;
        seen.add(aff.statId);
        const f: Record<string, unknown> = { id: aff.statId, disabled: false };
        const val: Record<string, number> = {};
        if (aff.min !== null && aff.min !== undefined && Number.isFinite(Number(aff.min))) {
          val.min = Number(aff.min);
        }
        if (aff.max !== null && aff.max !== undefined && Number.isFinite(Number(aff.max))) {
          val.max = Number(aff.max);
        }
        if (Object.keys(val).length > 0) f.value = val;
        statFilters.push(f);
        if (statFilters.length >= 6) break;
      }
      if (statFilters.length > 0) {
        query.stats = [{ type: "and", filters: statFilters }];
      }
    }
  }

  const payload = { query, sort: { price: "asc" } };
  return `https://www.pathofexile.com/trade2/search/poe2/${encLeague}?q=${encodeURIComponent(JSON.stringify(payload))}`;
}

interface TauriGlobalLike {
  core?: { invoke?: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
  invoke?: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
}

function getTauriInvoke(): ((cmd: string, args?: Record<string, unknown>) => Promise<unknown>) | null {
  if (typeof window === "undefined") return null;
  const g = window as unknown as { __TAURI__?: TauriGlobalLike; __TAURI_INTERNALS__?: TauriGlobalLike };
  if (typeof g.__TAURI__?.core?.invoke === "function") return g.__TAURI__.core.invoke;
  if (typeof g.__TAURI__?.invoke === "function") return g.__TAURI__.invoke;
  if (typeof g.__TAURI_INTERNALS__?.invoke === "function") return g.__TAURI_INTERNALS__.invoke;
  return null;
}

async function openViaBackendSystemBrowser(url: string): Promise<boolean> {
  try {
    const base = "http://127.0.0.1:8788";
    const tokRes = await fetch(`${base}/token`);
    if (!tokRes.ok) return false;
    const tokData = (await tokRes.json()) as { token?: string };
    if (!tokData?.token) return false;
    const res = await fetch(`${base}/system/open-url`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Auth-Token": tokData.token,
      },
      body: JSON.stringify({ url }),
    });
    if (!res.ok) return false;
    const d = (await res.json().catch(() => ({}))) as { ok?: boolean; opened?: boolean };
    return Boolean(d?.ok && d?.opened !== false);
  } catch {
    return false;
  }
}

export function openExternalUrl(url: string): void {
  if (!url || typeof window === "undefined") return;

  // 1. Tauri 桌面容器内 WKWebView 会静默吞掉 window.open：优先走原生命令，失败再走本地后端拉起系统浏览器
  const tauriInvoke = getTauriInvoke();
  if (tauriInvoke) {
    void tauriInvoke("open_external_url", { url }).catch(() => {
      void openViaBackendSystemBrowser(url);
    });
    return;
  }

  // 2. 普通浏览器：不传 noopener 以便准确判断是否被弹窗拦截，成功后手动清空 opener 保证安全
  const popup = window.open(url, "_blank");
  if (popup) {
    try {
      popup.opener = null;
    } catch {
      // cross-origin ignore
    }
    return;
  }

  // 3. 若被浏览器弹窗拦截（popup === null），立刻交由本地后端 webbrowser.open 直接在系统默认浏览器打开
  void openViaBackendSystemBrowser(url);
}

/**
 * 异步构建市集链接后再跳转的安全打开器：
 * - 普通浏览器下在点击同步栈内先占位打开新标签，避免 await 之后用户手势失效被弹窗拦截；
 * - Tauri 桌面容器或弹窗被拦截时，自动回退到本地原生/后端系统浏览器打开。
 */
export async function openExternalUrlAsync(resolveUrl: () => Promise<string>): Promise<string> {
  if (typeof window === "undefined") {
    return resolveUrl();
  }
  const tauriInvoke = getTauriInvoke();
  let placeholder: Window | null = null;
  if (!tauriInvoke) {
    try {
      placeholder = window.open("about:blank", "_blank");
      if (placeholder && placeholder.document) {
        placeholder.document.title = "正在打开官方 PoE2 市集...";
        placeholder.document.body.innerHTML =
          '<div style="font-family:system-ui,sans-serif;padding:32px;background:#0f1419;color:#e5e7eb;">正在生成加权平替市集查询并跳转，请稍候...</div>';
      }
    } catch {
      placeholder = null;
    }
  }

  let url = "";
  try {
    url = await resolveUrl();
  } catch (err) {
    if (placeholder && !placeholder.closed) {
      try {
        placeholder.close();
      } catch {
        // ignore
      }
    }
    throw err;
  }

  if (!url) {
    if (placeholder && !placeholder.closed) {
      try {
        placeholder.close();
      } catch {
        // ignore
      }
    }
    return "";
  }

  if (placeholder && !placeholder.closed) {
    try {
      placeholder.opener = null;
      placeholder.location.replace(url);
      return url;
    } catch {
      // fall through to openExternalUrl
    }
  }

  openExternalUrl(url);
  return url;
}
