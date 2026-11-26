import React from "react";
import type { PriceSnapshot, TargetBuildPayload, TradeOpenRequest } from "@contracts";
import type { WorkbenchHealth } from "../../bridge/workbenchClient";
import { buildDeepLinkUrl, serializeHandoffBundle } from "../../bridge/handoff";

interface DesktopBridgeBarProps {
  health: WorkbenchHealth;
  targetPayload: TargetBuildPayload | null;
  priceSnapshot: PriceSnapshot | null;
  pendingTrades: TradeOpenRequest[];
  pushStatus: { kind: "idle" | "ok" | "error"; text: string };
  onPushTarget: () => void;
  onPushPrices: () => void;
  onOpenTradeRequest: (req: TradeOpenRequest) => void;
  onAckTradeRequest: (id: string) => void;
  onOpenUrl: (url: string) => void;
  onCopySuccess: (msg: string) => void;
}

export const DesktopBridgeBar: React.FC<DesktopBridgeBarProps> = ({
  health,
  targetPayload,
  priceSnapshot,
  pendingTrades,
  pushStatus,
  onPushTarget,
  onPushPrices,
  onOpenTradeRequest,
  onAckTradeRequest,
  onOpenUrl,
  onCopySuccess,
}) => {
  const entryCount = priceSnapshot?.entries?.length || 0;

  async function handleCopyHandoff() {
    const json = serializeHandoffBundle(targetPayload || undefined, priceSnapshot || undefined);
    await navigator.clipboard.writeText(json);
    onCopySuccess("已复制 JSON 交接包到剪贴板，可在桌面端直接粘贴导入。");
  }

  function handleDeepLinkWake() {
    const url = buildDeepLinkUrl({
      title: targetPayload?.title,
      league: targetPayload?.league,
      sourceUrl: targetPayload?.sourceUrl,
      pobCode: targetPayload?.pobCode,
    });
    onOpenUrl(url);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {/* 扩展 -> 桌面端推送 */}
      <section className="card gold">
        <div className="card-title">
          <span>🚀 推送至本地桌面工作台</span>
          <span className={`badge ${health.online ? "green" : "muted"}`}>
            {health.online ? "127.0.0.1:8788 已连接" : "未检测到桌面后端"}
          </span>
        </div>

        <div className="muted" style={{ marginBottom: 8 }}>
          {health.online
            ? `后端当前目标：${health.targetBuildTitle || "暂无"} · 已存价格：${health.pricesCount} 条`
            : "请运行 python3 -m backend.api.server 或启动 PoE2 Workbench 桌面端。离线时可使用下方兜底方式。"}
        </div>

        <div className="row">
          <button
            type="button"
            className="btn primary"
            disabled={!health.online || !targetPayload}
            onClick={onPushTarget}
          >
            🎯 推送目标 BD
          </button>

          <button
            type="button"
            className="btn success"
            disabled={!health.online || entryCount === 0}
            onClick={onPushPrices}
          >
            💰 推送价格快照 ({entryCount})
          </button>
        </div>

        {pushStatus.kind !== "idle" && (
          <div className={`alert ${pushStatus.kind === "ok" ? "success" : "error"}`} style={{ marginTop: 8 }}>
            {pushStatus.text}
          </div>
        )}
      </section>

      {/* 桌面端 -> 扩展：双向市集搜货指令队列 */}
      <section className="card">
        <div className="card-title">
          <span>📥 桌面端市集搜货指令（SSE 双向通道）</span>
          <span className="badge blue">{pendingTrades.length} 待处理</span>
        </div>

        {pendingTrades.length === 0 ? (
          <div className="muted">
            暂无待处理的桌面端搜货请求。在桌面端点击「搜成品 / 搜半成品底子」时，扩展会在此实时弹出并借助浏览器环境打开官方市集。
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {pendingTrades.map((req) => (
              <div key={req.id} className="gear-card">
                <div className="row between">
                  <strong>{req.label || `${req.slot} · ${req.itemName || "市集搜货"}`}</strong>
                  <span className="badge gold">
                    {req.kind === "semi" ? "半成品" : req.kind === "budget" ? "平替" : "原件"}
                  </span>
                </div>
                <div className="muted">
                  赛季：{req.league} · 槽位：{req.slot || "-"}
                </div>
                <div className="row" style={{ marginTop: 4 }}>
                  <button type="button" className="btn sm primary" onClick={() => onOpenTradeRequest(req)}>
                    🌐 打开官方市集
                  </button>
                  <button type="button" className="btn sm" onClick={() => onAckTradeRequest(req.id)}>
                    ✓ 标记完成
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 离线唤醒 / 交接包兜底 */}
      <section className="card">
        <div className="card-title">
          <span>🔌 离线交接兜底</span>
        </div>
        <div className="muted" style={{ marginBottom: 8 }}>
          当本地桌面端尚未启动时，可尝试协议唤醒或复制完整交接包（含目标 PoB2 码、定位卡与已查市集价）。
        </div>
        <div className="row">
          <button type="button" className="btn sm" disabled={!targetPayload} onClick={handleDeepLinkWake}>
            🔗 poe2wb:// 唤醒桌面端
          </button>
          <button
            type="button"
            className="btn sm"
            disabled={!targetPayload && entryCount === 0}
            onClick={handleCopyHandoff}
          >
            📦 复制 JSON 交接包
          </button>
        </div>
      </section>
    </div>
  );
};
