import React, { useEffect, useState } from "react";

interface ImportBarProps {
  input: string;
  onInputChange: (v: string) => void;
  loading: boolean;
  runSimulation: boolean;
  onRunSimulationChange: (v: boolean) => void;
  backendOnline: boolean;
  hasProfile?: boolean;
  activeTitle?: string;
  onCaptureTab: () => void;
  onAnalyze: () => void;
}

export const ImportBar: React.FC<ImportBarProps> = ({
  input,
  onInputChange,
  loading,
  runSimulation,
  onRunSimulationChange,
  backendOnline,
  hasProfile = false,
  activeTitle = "",
  onCaptureTab,
  onAnalyze,
}) => {
  const [expanded, setExpanded] = useState(!hasProfile);

  useEffect(() => {
    if (hasProfile) {
      setExpanded(false);
    } else {
      setExpanded(true);
    }
  }, [hasProfile, activeTitle]);

  if (hasProfile && !expanded) {
    return (
      <section className="card import-compact">
        <div className="import-compact-title" title={activeTitle}>
          🎯 {activeTitle || "已加载目标 BD"}
        </div>
        <div className="row" style={{ gap: 5, flexShrink: 0 }}>
          <button type="button" className="btn sm" onClick={onCaptureTab} disabled={loading}>
            {loading ? "抓取中..." : "⚡ 重抓"}
          </button>
          <button type="button" className="btn sm" onClick={() => setExpanded(true)}>
            ✏️ 粘贴
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="card">
      <div className="card-title">
        <span>1. 抓取 / 导入目标 BD</span>
        <div className="row" style={{ gap: 5 }}>
          <button type="button" className="btn sm" onClick={onCaptureTab} disabled={loading}>
            ⚡ 抓取当前页
          </button>
          {hasProfile && (
            <button type="button" className="btn sm" onClick={() => setExpanded(false)}>
              收起
            </button>
          )}
        </div>
      </div>

      <textarea
        className="textarea"
        placeholder="粘贴 PoB2 分享码 / pobb.in 链接 / XML，或在 poe.ninja / pobb.in 页面点右上角「抓取当前页」…"
        value={input}
        onChange={(e) => onInputChange(e.target.value)}
      />

      <div className="row between" style={{ marginTop: 8 }}>
        <label className="checkbox-label" title="调用 127.0.0.1:8788 常驻 PoB2 LuaJIT 引擎实算 45+ 项面板">
          <input
            type="checkbox"
            checked={runSimulation}
            disabled={!backendOnline}
            onChange={(e) => onRunSimulationChange(e.target.checked)}
          />
          <span>{backendOnline ? "同步 PoB2 引擎实算" : "离线降级模式"}</span>
        </label>

        <button type="button" className="btn primary" onClick={onAnalyze} disabled={loading || !input.trim()}>
          {loading ? "解析中..." : "🔍 拆解目标 BD"}
        </button>
      </div>
    </section>
  );
};
