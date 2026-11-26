import React, { useCallback, useEffect, useState } from "react";
import { desktopApi, type EngineStatus, type EngineTestResult } from "../api/workbenchApi";

interface EngineSetupModalProps {
  open: boolean;
  backendOnline: boolean;
  onClose: () => void;
  onEngineReady: () => void;
}

const MIRROR_PRESETS = [
  { id: "direct", label: "GitHub 官方直连", prefix: "" },
  { id: "ghproxy", label: "国内加速镜像 (gh-proxy.com)", prefix: "https://gh-proxy.com/" },
  { id: "custom", label: "自定义镜像前缀...", prefix: "__custom__" },
];

export const EngineSetupModal: React.FC<EngineSetupModalProps> = ({
  open,
  backendOnline,
  onClose,
  onEngineReady,
}) => {
  const [status, setStatus] = useState<EngineStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyMsg, setBusyMsg] = useState("");
  const [error, setError] = useState("");
  const [customPath, setCustomPath] = useState("");
  const [refChoice, setRefChoice] = useState<string>("pinned");
  const [mirrorChoice, setMirrorChoice] = useState<string>("direct");
  const [customMirror, setCustomMirror] = useState<string>("");
  const [testResult, setTestResult] = useState<EngineTestResult | null>(null);

  const fetchStatus = useCallback(async () => {
    if (!backendOnline) return;
    try {
      const st = await desktopApi.getEngineStatus();
      setStatus(st);
      setError("");
    } catch (err) {
      setError((err as Error).message);
    }
  }, [backendOnline]);

  useEffect(() => {
    if (!open || !backendOnline) return;
    setLoading(true);
    setTestResult(null);
    fetchStatus().finally(() => setLoading(false));
  }, [open, backendOnline, fetchStatus]);

  const installStatus = status?.installState?.status || "idle";
  const isInstalling =
    installStatus === "downloading" ||
    installStatus === "extracting" ||
    installStatus === "patching" ||
    installStatus === "verifying";

  // 安装进行中时每秒轮询进度
  useEffect(() => {
    if (!open || !backendOnline || !isInstalling) return;
    const timer = window.setInterval(() => {
      desktopApi
        .getEngineStatus()
        .then((st) => {
          setStatus(st);
          if (st.installState.status === "done") {
            onEngineReady();
          }
        })
        .catch(() => undefined);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [open, backendOnline, isInstalling, onEngineReady]);

  if (!open) return null;

  async function handleRescan() {
    setBusyMsg("正在扫描本机 PoB2 目录...");
    setError("");
    try {
      const st = await desktopApi.detectEngine();
      setStatus(st);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyMsg("");
    }
  }

  async function handleUsePath(targetPath: string) {
    const clean = targetPath.trim();
    if (!clean) {
      setError("请输入有效的本机 PoB2 目录路径");
      return;
    }
    setBusyMsg("正在校验并绑定 PoB2 目录...");
    setError("");
    setTestResult(null);
    try {
      const st = await desktopApi.useLocalPob2Path(clean);
      setStatus(st);
      setCustomPath("");
      if (st.ready) onEngineReady();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyMsg("");
    }
  }

  async function handleGithubInstall() {
    setError("");
    setTestResult(null);
    const selectedRef = refChoice === "dev" ? "dev" : status?.pinnedVersion || "v0.23.1";
    const preset = MIRROR_PRESETS.find((m) => m.id === mirrorChoice);
    const mirrorPrefix =
      mirrorChoice === "custom" ? customMirror.trim() : preset ? preset.prefix : "";
    try {
      const inst = await desktopApi.startEngineInstall({
        ref: selectedRef,
        mirrorPrefix,
      });
      setStatus((prev) => (prev ? { ...prev, installState: inst } : prev));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function handleInstallLuajit() {
    setBusyMsg("正在尝试自动安装 LuaJIT（可能需要 10~60 秒）...");
    setError("");
    setTestResult(null);
    try {
      const res = await desktopApi.installLuajit();
      if (res.engine) setStatus(res.engine);
      if (res.installed || res.alreadyPresent) {
        if (res.engine?.ready) onEngineReady();
      } else if (res.message) {
        setError(res.message);
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyMsg("");
    }
  }

  async function handleTestLive() {
    setBusyMsg("正在拉起 LuaJIT + PoB2 冒烟校验...");
    setError("");
    try {
      const res = await desktopApi.testEngineLive();
      setTestResult(res);
      if (res.ok) onEngineReady();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusyMsg("");
    }
  }

  const pct = status?.installState?.progressPct || 0;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.68)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        padding: 16,
      }}
      onClick={onClose}
    >
      <div
        className="card gold"
        style={{ width: 640, maxWidth: "100%", maxHeight: "90vh", overflowY: "auto", margin: 0, background: "#141821" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="card-header">
          <span className="card-title">⚙️ PoB2 计算内核自检与一键安装</span>
          <button type="button" className="btn sm" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="muted" style={{ marginBottom: 12 }}>
          软件启动时会自动检测本机是否已安装 Path of Building (PoE2) 与 LuaJIT；若已有可直接复用，未安装时可一键从 GitHub 拉取精简纯计算内核（自动跳过大贴图与字体，零入侵原版 PoB2）。
        </div>

        {loading && !status ? (
          <div className="muted" style={{ padding: "16px 0" }}>
            正在自检本机 PoB2 计算环境...
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {/* 1. 当前环境自检卡 */}
            <div className="grid-2">
              <div className="kpi-card">
                <div className="row between" style={{ marginBottom: 4 }}>
                  <span className="kpi-label">PoB2 计算内核</span>
                  <span className={`badge ${status?.pob2Found ? "green" : "red"}`}>
                    {status?.pob2Found ? "✓ 已就绪" : "未检测到"}
                  </span>
                </div>
                <div style={{ fontSize: 12, wordBreak: "break-all" }}>
                  {status?.pob2Found ? status.activeRoot : "未发现可用的 HeadlessWrapper.lua"}
                </div>
                {status?.pob2Found && (
                  <div className="chips" style={{ marginTop: 6 }}>
                    <span className="badge blue">
                      布局：{status.layout === "installed" ? "官方安装版" : "源码/托管版"}
                    </span>
                    <span className="badge">来源：{status.source}</span>
                    {status.treeVersions.length > 0 && (
                      <span className="badge gold">天赋树：{status.treeVersions.join(", ")}</span>
                    )}
                  </div>
                )}
              </div>

              <div className="kpi-card">
                <div className="row between" style={{ marginBottom: 4 }}>
                  <span className="kpi-label">LuaJIT 运行时</span>
                  <span className={`badge ${status?.luajitFound ? "green" : "red"}`}>
                    {status?.luajitFound ? "✓ 已就绪" : "缺失"}
                  </span>
                </div>
                <div style={{ fontSize: 12, wordBreak: "break-all" }}>
                  {status?.luajitFound ? status.luajitPath : status?.luajitHint || "未找到 luajit 可执行文件"}
                </div>
                {status && !status.luajitFound && (
                  <div style={{ marginTop: 8 }}>
                    <button
                      type="button"
                      className="btn sm primary"
                      disabled={!backendOnline || Boolean(busyMsg) || isInstalling}
                      onClick={() => void handleInstallLuajit()}
                    >
                      ⬇️ 一键安装 LuaJIT
                    </button>
                  </div>
                )}
              </div>
            </div>

            {/* 2. 自动扫描到的本机候选 PoB2 目录 */}
            {status && status.candidates.length > 0 && (
              <div className="kpi-card">
                <div className="row between" style={{ marginBottom: 6 }}>
                  <strong style={{ fontSize: 12.5 }}>📂 本机已发现的 PoB2 目录（直接复用，无需重复下载）</strong>
                  <span className="muted" style={{ fontSize: 11 }}>共 {status.candidates.length} 处</span>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {status.candidates.map((c) => {
                    const isActive = status.pob2Found && c.path === status.activeRoot;
                    return (
                      <div key={c.path} className="row between" style={{ gap: 8, padding: "6px 8px", background: "rgba(255,255,255,0.03)", borderRadius: 6 }}>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div className="row" style={{ gap: 6 }}>
                            <span className="badge">{c.label}</span>
                            {isActive && <span className="badge green">当前使用</span>}
                            {c.treeVersions.length > 0 && (
                              <span className="muted" style={{ fontSize: 11 }}>
                                Tree: {c.treeVersions.join(", ")}
                              </span>
                            )}
                          </div>
                          <div className="muted" style={{ fontSize: 11, wordBreak: "break-all", marginTop: 2 }}>
                            {c.path}
                          </div>
                        </div>
                        {!isActive && (
                          <button
                            type="button"
                            className="btn sm"
                            disabled={Boolean(busyMsg) || isInstalling}
                            onClick={() => void handleUsePath(c.path)}
                          >
                            使用此目录
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* 3. 一键从 GitHub 拉取托管内核 */}
            <div className="kpi-card" style={{ borderColor: "rgba(212,175,55,0.35)" }}>
              <div className="row between" style={{ marginBottom: 6 }}>
                <strong style={{ color: "var(--gold)", fontSize: 13 }}>
                  ⬇️ 一键从 GitHub 下载精简 PoB2 计算内核
                </strong>
                <span className="badge blue">托管目录：{status?.managedDir ? "~/.poe2-workbench/pob2-engine" : ""}</span>
              </div>
              <div className="muted" style={{ fontSize: 11.5, marginBottom: 8 }}>
                无需安装 Git；自动从官方仓库拉取 Zip 包、精简跳过大贴图与字体并应用 LuaJIT 兼容补丁。
              </div>

              <div className="grid-2" style={{ marginBottom: 8 }}>
                <div>
                  <div className="muted" style={{ marginBottom: 4, fontSize: 11.5 }}>版本选择</div>
                  <select
                    className="input"
                    style={{ width: "100%" }}
                    value={refChoice}
                    disabled={isInstalling}
                    onChange={(e) => setRefChoice(e.target.value)}
                  >
                    <option value="pinned">
                      已验证稳定版 ({status?.pinnedVersion || "v0.23.1"})（推荐）
                    </option>
                    <option value="dev">上游最新开发版 (dev)</option>
                  </select>
                </div>

                <div>
                  <div className="muted" style={{ marginBottom: 4, fontSize: 11.5 }}>下载线路</div>
                  <select
                    className="input"
                    style={{ width: "100%" }}
                    value={mirrorChoice}
                    disabled={isInstalling}
                    onChange={(e) => setMirrorChoice(e.target.value)}
                  >
                    {MIRROR_PRESETS.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {mirrorChoice === "custom" && (
                <div style={{ marginBottom: 8 }}>
                  <input
                    className="input"
                    style={{ width: "100%" }}
                    placeholder="例如 https://gh-proxy.com/（将拼接在 GitHub URL 前）"
                    value={customMirror}
                    disabled={isInstalling}
                    onChange={(e) => setCustomMirror(e.target.value)}
                  />
                </div>
              )}

              {(isInstalling || installStatus === "done" || installStatus === "error") && status?.installState?.message && (
                <div style={{ marginBottom: 8 }}>
                  <div className="row between" style={{ fontSize: 11.5, marginBottom: 4 }}>
                    <span>{status.installState.message}</span>
                    <span>{pct}%</span>
                  </div>
                  <div style={{ height: 6, background: "rgba(255,255,255,0.08)", borderRadius: 4, overflow: "hidden" }}>
                    <div
                      style={{
                        width: `${Math.max(2, Math.min(100, pct))}%`,
                        height: "100%",
                        background:
                          installStatus === "error"
                            ? "var(--red)"
                            : installStatus === "done"
                            ? "var(--green)"
                            : "var(--gold)",
                        transition: "width 0.3s ease",
                      }}
                    />
                  </div>
                </div>
              )}

              <button
                type="button"
                className="btn primary"
                disabled={!backendOnline || isInstalling || Boolean(busyMsg)}
                onClick={() => void handleGithubInstall()}
              >
                {isInstalling
                  ? `下载安装中 (${pct}%)...`
                  : status?.pob2Found
                  ? "⬇️ 从 GitHub 下载/更新托管 PoB2 内核"
                  : "🚀 一键从 GitHub 下载 PoB2 内核"}
              </button>
            </div>

            {/* 4. 手动指定本机已有目录 */}
            <div className="kpi-card">
              <div className="muted" style={{ marginBottom: 4, fontSize: 11.5 }}>
                手动指定本机已有 PoB2 目录（支持官方安装版与 GitHub 克隆版）
              </div>
              <div className="row" style={{ gap: 6 }}>
                <input
                  className="input"
                  style={{ flex: 1 }}
                  placeholder="例如 C:\Users\...\Path of Building Community (PoE2) 或 ~/PathOfBuilding-PoE2"
                  value={customPath}
                  disabled={Boolean(busyMsg) || isInstalling}
                  onChange={(e) => setCustomPath(e.target.value)}
                />
                <button
                  type="button"
                  className="btn sm"
                  disabled={!customPath.trim() || Boolean(busyMsg) || isInstalling}
                  onClick={() => void handleUsePath(customPath)}
                >
                  校验并启用
                </button>
              </div>
            </div>

            {busyMsg && (
              <div className="banner info">
                <span>{busyMsg}</span>
              </div>
            )}
            {error && (
              <div className="banner error">
                <span>{error}</span>
              </div>
            )}
            {testResult && (
              <div className={`banner ${testResult.ok ? "success" : "error"}`}>
                <span>
                  {testResult.ok
                    ? `✓ 引擎冒烟通过！LuaJIT + PoB2 启动耗时 ${testResult.bootMs}ms，内存占用 ${testResult.rssMb}MB。`
                    : `✗ 引擎测试失败：${testResult.error}`}
                </span>
              </div>
            )}

            <div className="row between" style={{ marginTop: 2 }}>
              <button
                type="button"
                className="btn sm"
                disabled={!backendOnline || Boolean(busyMsg) || isInstalling}
                onClick={() => void handleRescan()}
              >
                ↻ 重新自检本机
              </button>
              <button
                type="button"
                className="btn sm"
                disabled={!backendOnline || !status?.ready || Boolean(busyMsg) || isInstalling}
                onClick={() => void handleTestLive()}
              >
                🔌 测试引擎冒烟连通
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
