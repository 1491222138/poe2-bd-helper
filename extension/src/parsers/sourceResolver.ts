/**
 * 智能识别并解析输入来源：
 * - 直接粘贴的 PoB2 分享码 / XML
 * - pobb.in 链接（自动转为 /raw）
 * - pastebin 链接（自动转为 /raw/）
 * - 当前浏览的 poe.ninja/poe2 或 pobb.in 标签页
 */

export interface ResolvedSource {
  pobCode: string;
  sourceUrl?: string;
  title?: string;
}

export type TextFetcher = (url: string) => Promise<{ ok: boolean; text?: string; error?: string }>;

export function normalizeRawFetchUrl(input: string): string | null {
  const s = (input || "").trim();
  const pobbin = s.match(/^https?:\/\/(?:www\.)?pobb\.in\/([A-Za-z0-9_-]+)(?:\/raw)?\/?(?:\?.*)?$/i);
  if (pobbin) {
    return `https://pobb.in/${pobbin[1]}/raw`;
  }
  const pastebin = s.match(/^https?:\/\/(?:www\.)?pastebin\.com\/(?:raw\/)?([A-Za-z0-9_-]+)\/?(?:\?.*)?$/i);
  if (pastebin) {
    return `https://pastebin.com/raw/${pastebin[1]}`;
  }
  return null;
}

export function looksLikePobInput(input: string): boolean {
  const s = (input || "").trim();
  if (!s) return false;
  if (s.startsWith("<?xml") || s.startsWith("<PathOfBuilding")) return true;
  return s.length >= 60 && /^[A-Za-z0-9+/_=\s-]+$/.test(s);
}

export async function bgFetchText(url: string): Promise<{ ok: boolean; text?: string; error?: string }> {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: "FETCH_TEXT", url }, (resp) => {
      if (chrome.runtime.lastError || !resp?.ok) {
        resolve({ ok: false, error: resp?.error || chrome.runtime.lastError?.message || "拉取失败" });
        return;
      }
      resolve({ ok: true, text: resp.text });
    });
  });
}

export async function resolveSourceInput(
  rawInput: string,
  fetcher: TextFetcher = bgFetchText
): Promise<ResolvedSource> {
  const trimmed = (rawInput || "").trim();
  if (!trimmed) {
    throw new Error("请输入 PoB2 分享码、pobb.in 链接，或点击「抓取当前页」。");
  }

  const rawUrl = normalizeRawFetchUrl(trimmed);
  if (rawUrl) {
    const res = await fetcher(rawUrl);
    if (!res.ok || !res.text?.trim()) {
      throw new Error(res.error || `无法从 ${rawUrl} 读取 PoB2 码`);
    }
    return {
      pobCode: res.text.trim(),
      sourceUrl: trimmed,
    };
  }

  if (looksLikePobInput(trimmed)) {
    return { pobCode: trimmed };
  }

  throw new Error("暂无法识别该输入：请粘贴完整的 PoB2 分享码或 pobb.in 链接。");
}

interface PageExtractResp {
  ok: boolean;
  error?: string;
  data?: {
    url: string;
    title: string;
    pobCode: string;
    pobbinUrl: string;
    isPoeNinjaPoe2: boolean;
    isPobbin: boolean;
  };
}

export async function captureFromActiveTab(fetcher: TextFetcher = bgFetchText): Promise<ResolvedSource> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id || !tab.url) {
    throw new Error("未找到当前活动标签页。");
  }

  // 如果当前页本身就是 pobb.in，直接拉取 /raw
  const directRaw = normalizeRawFetchUrl(tab.url);
  if (directRaw) {
    const res = await fetcher(directRaw);
    if (res.ok && res.text?.trim()) {
      return {
        pobCode: res.text.trim(),
        sourceUrl: tab.url,
        title: tab.title || "",
      };
    }
  }

  const resp = await new Promise<PageExtractResp>((resolve) => {
    chrome.tabs.sendMessage(tab.id!, { type: "EXTRACT_BUILD_DATA" }, (r) => {
      if (chrome.runtime.lastError || !r) {
        resolve({
          ok: false,
          error: "当前页面未注入抓取脚本（仅支持 poe.ninja/poe2/* 与 pobb.in/*；若刚安装扩展请先刷新页面）。",
        });
        return;
      }
      resolve(r as PageExtractResp);
    });
  });

  if (!resp.ok || !resp.data) {
    throw new Error(resp.error || "页面抓取失败");
  }

  if (resp.data.pobCode) {
    return {
      pobCode: resp.data.pobCode,
      sourceUrl: resp.data.url,
      title: resp.data.title,
    };
  }

  if (resp.data.pobbinUrl) {
    const resolved = await resolveSourceInput(resp.data.pobbinUrl, fetcher);
    return {
      ...resolved,
      sourceUrl: resp.data.url,
      title: resp.data.title,
    };
  }

  throw new Error(
    "当前页面未直接暴露 PoB2 码：请在 poe.ninja 角色页点击「Copy PoB code」后粘贴到下方输入框。"
  );
}
