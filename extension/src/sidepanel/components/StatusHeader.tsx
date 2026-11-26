import React from "react";
import { LANG_MODE_OPTIONS, type LangMode } from "@contracts";
import type { WorkbenchHealth } from "../../bridge/workbenchClient";
import type { LeagueInfo } from "../../market/ninjaPrice";

interface StatusHeaderProps {
  health: WorkbenchHealth;
  onRefreshHealth: () => void;
  leagues: LeagueInfo[];
  league: string;
  onLeagueChange: (league: string) => void;
  pendingTradeCount: number;
  onJumpToBridge: () => void;
  onToggleSettings: () => void;
  dictOutdated?: boolean;
  langMode: LangMode;
  onLangModeChange: (mode: LangMode) => void;
}

export const StatusHeader: React.FC<StatusHeaderProps> = ({
  health,
  onRefreshHealth,
  leagues,
  league,
  onLeagueChange,
  pendingTradeCount,
  onJumpToBridge,
  onToggleSettings,
  dictOutdated = false,
  langMode,
  onLangModeChange,
}) => {
  return (
    <header className="header">
      <div className="header-top">
        <div className="brand">
          <span>◆</span>
          <span>PoE2 Workbench 侦察兵</span>
        </div>
        <div className="row" style={{ gap: 5 }}>
          <div className="lang-switch" role="group" aria-label="中英对照热切换 (Alt+L)">
            {LANG_MODE_OPTIONS.map((opt) => (
              <button
                key={opt.mode}
                type="button"
                className={`lang-switch-btn ${langMode === opt.mode ? "active" : ""}`}
                onClick={() => onLangModeChange(opt.mode)}
                title={opt.hint}
              >
                {opt.shortLabel}
              </button>
            ))}
          </div>
          <button
            type="button"
            className={`btn sm ${dictOutdated ? "primary" : ""}`}
            onClick={onToggleSettings}
            title="AI 流式接口与 PoE2 离线版本词典设置"
          >
            ⚙️ {dictOutdated ? "词典可更新" : "设置"}
          </button>
          <button
            type="button"
            className={`status-pill ${health.online ? "online" : "offline"}`}
            onClick={onRefreshHealth}
            title="点击刷新本地工作台 (127.0.0.1:8788) 连接状态"
          >
            <span className="dot" />
            <span>
              {health.online
                ? `工作台在线${health.workersRunning > 0 ? ` · ${health.workersRunning}W` : ""}`
                : "纯插件模式"}
            </span>
          </button>
        </div>
      </div>

      <div className="header-sub">
        <select
          className="select"
          value={league}
          onChange={(e) => onLeagueChange(e.target.value)}
          aria-label="选择赛季"
        >
          {leagues.map((l) => (
            <option key={l.name} value={l.name}>
              赛季：{l.displayName}
            </option>
          ))}
        </select>

        {pendingTradeCount > 0 && (
          <button type="button" className="btn sm primary" onClick={onJumpToBridge}>
            🔔 桌面搜货 ({pendingTradeCount})
          </button>
        )}
      </div>
    </header>
  );
};
