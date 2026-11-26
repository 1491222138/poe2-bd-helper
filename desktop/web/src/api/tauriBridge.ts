/**
 * Tauri 2 运行时与 poe2wb:// Deep Link 协议桥接（在纯浏览器模式下零报错自动降级）。
 */

export interface ParsedDeepLinkImport {
  action: "import";
  title?: string;
  league?: string;
  sourceUrl?: string;
  pobCode?: string;
  fromClipboard: boolean;
}

interface TauriInternals {
  invoke?: <T = unknown>(cmd: string, args?: Record<string, unknown>) => Promise<T>;
}

interface TauriEventGlobal {
  event?: {
    listen?: (event: string, cb: (ev: { payload: unknown }) => void) => Promise<() => void>;
  };
}

export function isTauriRuntime(): boolean {
  if (typeof window === "undefined") return false;
  const w = window as unknown as { __TAURI_INTERNALS__?: TauriInternals };
  return Boolean(w.__TAURI_INTERNALS__?.invoke);
}

export function parseDeepLinkUrl(rawUrl: string): ParsedDeepLinkImport | null {
  const trimmed = (rawUrl || "").trim();
  if (!trimmed.toLowerCase().startsWith("poe2wb://")) return null;
  try {
    const normalized = trimmed.replace(/^poe2wb:\/\//i, "https://poe2wb.local/");
    const parsed = new URL(normalized);
    const action = parsed.pathname.replace(/^\/+/, "").toLowerCase();
    if (action !== "import") return null;

    const qs = parsed.searchParams;
    return {
      action: "import",
      title: qs.get("title") || undefined,
      league: qs.get("league") || undefined,
      sourceUrl: qs.get("source") || undefined,
      pobCode: qs.get("pob") || undefined,
      fromClipboard: qs.get("fromClipboard") === "1" || qs.get("fromClipboard") === "true",
    };
  } catch {
    return null;
  }
}

export async function consumeStartupDeepLinks(): Promise<string[]> {
  const urls: string[] = [];

  // 1. 支持浏览器测试地址：http://127.0.0.1:5173/?deepLink=poe2wb://import?...
  if (typeof window !== "undefined") {
    const dl = new URLSearchParams(window.location.search).get("deepLink");
    if (dl) urls.push(dl);
  }

  // 2. Tauri 2 运行时拉取冷启动积压的 Deep Link
  if (isTauriRuntime()) {
    try {
      const w = window as unknown as { __TAURI_INTERNALS__?: TauriInternals };
      const pending = await w.__TAURI_INTERNALS__?.invoke?.<string[]>("pending_deep_links");
      if (Array.isArray(pending)) urls.push(...pending);
    } catch {
      // ignore
    }
  }
  return urls;
}

export function subscribeDeepLinks(onUrl: (url: string) => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  const tauri = (window as unknown as { __TAURI__?: TauriEventGlobal }).__TAURI__;
  if (!tauri?.event?.listen) return () => undefined;

  let unlisten: (() => void) | null = null;
  let disposed = false;

  tauri.event
    .listen("poe2wb://deep-link", (ev) => {
      if (typeof ev.payload === "string") onUrl(ev.payload);
    })
    .then((fn) => {
      if (disposed) fn();
      else unlisten = fn;
    })
    .catch(() => undefined);

  return () => {
    disposed = true;
    unlisten?.();
  };
}

export async function restartManagedBackend(): Promise<boolean> {
  if (!isTauriRuntime()) return false;
  try {
    const w = window as unknown as { __TAURI_INTERNALS__?: TauriInternals };
    await w.__TAURI_INTERNALS__?.invoke?.("restart_backend");
    return true;
  } catch {
    return false;
  }
}
