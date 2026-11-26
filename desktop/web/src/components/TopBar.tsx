import React from "react";
import { LANG_MODE_OPTIONS, type LangMode } from "@contracts";
import type { BackendHealth, MarketLeagueInfo } from "../api/workbenchApi";

interface TopBarProps {
  health: BackendHealth;
  sseConnected: boolean;
  pageTitle: string;
  leagues: MarketLeagueInfo[];
  league: string;
  onLeagueChange: (league: string) => void;
  canRunGap: boolean;
  analyzing: boolean;
  aiEnabled: boolean;
  langMode: LangMode;
  onLangModeChange: (mode: LangMode) => void;
  onRunGap: () => void;
  onRefreshState: () => void;
  onOpenAiSettings: () => void;
  onOpenEngineSetup: () => void;
}

export const TopBar: React.FC<TopBarProps> = ({
  health,
  sseConnected,
  pageTitle,
  leagues,
  league,
  onLeagueChange,
  canRunGap,
  analyzing,
  aiEnabled,
  langMode,
  onLangModeChange,
  onRunGap,
  onRefreshState,
  onOpenAiSettings,
  onOpenEngineSetup,
}) => {
  const leagueOptions = leagues.some((l) => l.name === league)
    ? leagues
    : [{ name: league, displayName: league, url: "" }, ...leagues];

  return (
    <header className="topbar">
      <div className="row">
        <strong style={{ fontSize: 15 }}>{pageTitle}</strong>
        <span className={`badge ${health.online ? "green" : "red"}`}>
          <span className={`dot ${health.online ? "online" : ""}`} />
          {health.online
            ? `后端在线 · ${health.workersRunning} Worker`
            : "后端离线 (请运行 python3 -m backend.api.server)"}
        </span>
        {health.online && (
          <span className={`badge ${sseConnected ? "blue" : "amber"}`}>
            {sseConnected ? "SSE 实时联动中" : "SSE 重连中..."}
          </span>
        )}
      </div>

      <div className="row">
        <select
          className="input"
          aria-label="当前查价赛季"
          title="切换查价与 poe.ninja 实时物价赛季"
          style={{ padding: "4px 8px", fontSize: 12, maxWidth: 160 }}
          value={league}
          onChange={(e) => onLeagueChange(e.target.value)}
        >
          {leagueOptions.map((l) => (
            <option key={l.name} value={l.name}>
              赛季：{l.displayName || l.name}
            </option>
          ))}
        </select>
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
        {health.online && (
          <button
            type="button"
            className={`btn sm ${health.pob2Ready === false ? "primary" : ""}`}
            onClick={onOpenEngineSetup}
            title={health.pob2Root ? `当前 PoB2 内核：${health.pob2Root}` : "自检或一键从 GitHub 拉取 PoB2 计算内核"}
          >
            {health.pob2Ready === false ? "⚠️ 配置 PoB2 内核" : "⚙️ PoB2 内核"}
          </button>
        )}
        <button type="button" className="btn sm" onClick={onOpenAiSettings}>
          🤖 {aiEnabled ? "AI 已启用" : "AI 配置"}
        </button>
        <button type="button" className="btn sm" onClick={onRefreshState}>
          ↻ 同步扩展状态
        </button>
        <button
          type="button"
          className="btn primary"
          disabled={!health.online || !canRunGap || analyzing}
          onClick={onRunGap}
        >
          {analyzing ? "常驻引擎批量实算中..." : "⚡ 运行自身 vs 目标全景诊断"}
        </button>
      </div>
    </header>
  );
};
