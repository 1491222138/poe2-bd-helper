/**
 * 本地后端离线时的兜底交接能力：
 * 1. poe2wb://import?... Deep Link（供 Stage 5 Tauri 2 协议唤醒）
 * 2. 标准 JSON 交接包（复制到剪贴板，一键粘贴进桌面端）
 */
import type { PriceSnapshot, TargetBuildPayload } from "@contracts";

export interface HandoffBundle {
  schema: "poe2-workbench-handoff/v1";
  createdAt: number;
  target?: TargetBuildPayload;
  prices?: PriceSnapshot;
}

export function buildDeepLinkUrl(params: {
  sourceUrl?: string;
  title?: string;
  league?: string;
  pobCode?: string;
}): string {
  const qs = new URLSearchParams();
  if (params.title) qs.set("title", params.title);
  if (params.league) qs.set("league", params.league);
  if (params.sourceUrl) qs.set("source", params.sourceUrl);
  // 仅当 PoB 码较短时附带在 URI 中；超长码建议走剪贴板交接包或直接唤醒后读取剪贴板
  if (params.pobCode && params.pobCode.length <= 4000) {
    qs.set("pob", params.pobCode);
  } else if (params.pobCode) {
    qs.set("fromClipboard", "1");
  }
  return `poe2wb://import?${qs.toString()}`;
}

export function buildHandoffBundle(
  target?: TargetBuildPayload,
  prices?: PriceSnapshot
): HandoffBundle {
  return {
    schema: "poe2-workbench-handoff/v1",
    createdAt: Date.now(),
    target,
    prices,
  };
}

export function serializeHandoffBundle(
  target?: TargetBuildPayload,
  prices?: PriceSnapshot
): string {
  return JSON.stringify(buildHandoffBundle(target, prices), null, 2);
}
