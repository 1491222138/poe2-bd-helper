import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createTranslator,
  loadBilingualDict,
  nextLangMode,
  readStoredLangMode,
  writeStoredLangMode,
  type BilingualDictionary,
  type BuildProfileResponse,
  type CopyBuildContext,
  type CopyPlanStructured,
  type LangMode,
  type TargetBuildPayload,
  type TradeOpenRequest,
} from "@contracts";
import {
  DEFAULT_EXTENSION_LLM_CONFIG,
  loadExtensionLlmConfig,
  runDualTrackStreamAnalysis,
  saveExtensionLlmConfig,
  type ExtensionLlmConfig,
} from "../ai/llmStreamClient";
import { DEFAULT_WORKBENCH_URL, workbenchClient, type WorkbenchHealth } from "../bridge/workbenchClient";
import {
  fetchActiveLeagues,
  loadLeaguePrices,
  matchUniquePricesForBuild,
  type LeagueInfo,
  type LeaguePriceIndex,
} from "../market/ninjaPrice";
import { buildPriceSnapshot } from "../market/pricePackager";
import { buildFallbackTradeUrl, type ItemTradeQuote } from "../market/tradeBuilder";
import { buildLocalProfile } from "../parsers/localProfile";
import {
  loadExtCopySession,
  makeExtPobKey,
  saveExtCopySession,
  type ExtSavedBuildAnalysis,
} from "../parsers/copySessionStore";
import {
  isDictOutdatedForTree,
  loadPatchDict,
  updatePatchDict,
  type PatchDictData,
} from "../parsers/patchDict";
import { parsePobCodeBrowser } from "../parsers/pobDecoder";
import { captureFromActiveTab, resolveSourceInput } from "../parsers/sourceResolver";
import { AiConfigDrawer } from "./components/AiConfigDrawer";
import { CopyBuildBar } from "./components/CopyBuildBar";
import { DesktopBridgeBar } from "./components/DesktopBridgeBar";
import { ImportBar } from "./components/ImportBar";
import { MarketPricingPanel } from "./components/MarketPricingPanel";
import { PositioningCard } from "./components/PositioningCard";
import { StatusHeader } from "./components/StatusHeader";

type TabId = "positioning" | "market" | "bridge";

const INITIAL_HEALTH: WorkbenchHealth = {
  online: false,
  engineStarted: false,
  workersRunning: 0,
  treeVersions: [],
  targetBuildTitle: "",
  pricesCount: 0,
  pendingTradeRequests: 0,
  checkedAt: 0,
};

function openUrlInTab(url: string) {
  if (typeof chrome !== "undefined" && chrome.runtime?.sendMessage) {
    chrome.runtime.sendMessage({ type: "OPEN_TAB", url });
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

export const App: React.FC = () => {
  const [health, setHealth] = useState<WorkbenchHealth>(INITIAL_HEALTH);
  const [leagues, setLeagues] = useState<LeagueInfo[]>([
    { name: "Standard", displayName: "Standard", url: "standard" },
  ]);
  const [league, setLeague] = useState<string>("Standard");
  const [priceIndex, setPriceIndex] = useState<LeaguePriceIndex | null>(null);
  const [loadingNinja, setLoadingNinja] = useState(false);

  const [input, setInput] = useState("");
  const [sourceUrl, setSourceUrl] = useState<string | undefined>(undefined);
  const [activePobCode, setActivePobCode] = useState<string>("");
  const [runSimulation, setRunSimulation] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const [profile, setProfile] = useState<BuildProfileResponse | null>(null);

  // 离线版本词典 & 插件独立 AI 配置
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [extLlmConfig, setExtLlmConfig] = useState<ExtensionLlmConfig>(DEFAULT_EXTENSION_LLM_CONFIG);
  const [patchDict, setPatchDict] = useState<PatchDictData | null>(null);
  const [dictUpdating, setDictUpdating] = useState(false);
  const [dictMessage, setDictMessage] = useState("");

  // 第一段：目标 BD 本质拆解流式状态
  const [aiLoading, setAiLoading] = useState(false);
  const [aiText, setAiText] = useState("");
  const [aiReasoning, setAiReasoning] = useState("");
  const [aiModel, setAiModel] = useState("");
  const [aiError, setAiError] = useState("");
  const essenceAbortRef = useRef<AbortController | null>(null);

  // 第二段：灵活抄作业（等级 + 预算滑块 + 已有件 + 无 PoB 咨询）流式状态
  const [copyContext, setCopyContext] = useState<CopyBuildContext>({
    characterLevel: 90,
    budgetDivine: 15,
    ownedItems: [],
    goalNote: "",
    freeformQuery: { ascendancy: "", skillIdea: "" },
  });
  const [copyStreaming, setCopyStreaming] = useState(false);
  const [copyText, setCopyText] = useState("");
  const [copyReasoning, setCopyReasoning] = useState("");
  const [copyError, setCopyError] = useState("");
  const [copyPlan, setCopyPlan] = useState<CopyPlanStructured | null>(null);
  const [purchasedKeys, setPurchasedKeys] = useState<Record<string, boolean>>({});
  const [savedByBuild, setSavedByBuild] = useState<Record<string, ExtSavedBuildAnalysis>>({});
  const sessionHydratedRef = useRef(false);
  const copyAbortRef = useRef<AbortController | null>(null);

  const [tradeQuotes, setTradeQuotes] = useState<Record<string, ItemTradeQuote>>({});
  const [pendingTrades, setPendingTrades] = useState<TradeOpenRequest[]>([]);
  const [pushStatus, setPushStatus] = useState<{ kind: "idle" | "ok" | "error"; text: string }>({
    kind: "idle",
    text: "",
  });
  const [tab, setTab] = useState<TabId>("positioning");
  const [langMode, setLangMode] = useState<LangMode>(() => readStoredLangMode());
  const [bilingualDict, setBilingualDict] = useState<BilingualDictionary | null>(null);

  const handleLangModeChange = useCallback((mode: LangMode) => {
    setLangMode(mode);
    writeStoredLangMode(mode);
  }, []);

  useEffect(() => {
    let active = true;
    loadBilingualDict(health.online ? DEFAULT_WORKBENCH_URL : undefined).then((d) => {
      if (active) setBilingualDict(d);
    });
    return () => {
      active = false;
    };
  }, [health.online]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.altKey && (e.key === "l" || e.key === "L")) {
        e.preventDefault();
        setLangMode((prev) => {
          const next = nextLangMode(prev);
          writeStoredLangMode(next);
          return next;
        });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const translator = useMemo(() => createTranslator(bilingualDict, langMode), [bilingualDict, langMode]);

  const refreshHealth = useCallback(async () => {
    const h = await workbenchClient.checkHealth();
    setHealth(h);
    return h;
  }, []);

  // 启动加载离线版本词典 + 扩展本地 AI 配置 + 恢复上次抄作业与已入手状态
  useEffect(() => {
    void loadPatchDict().then((d) => setPatchDict(d));
    void loadExtensionLlmConfig().then((cfg) => setExtLlmConfig(cfg));
    void loadExtCopySession().then(async (sess) => {
      if (sess) {
        setCopyContext(sess.copyContext);
        setSavedByBuild(sess.savedByBuild || {});
        if (sess.lastInput) setInput(sess.lastInput);
        if (sess.lastSourceUrl) setSourceUrl(sess.lastSourceUrl);
        if (sess.lastPobCode) {
          const key = makeExtPobKey(sess.lastPobCode);
          const rec = sess.savedByBuild?.[key];
          setActivePobCode(sess.lastPobCode);
          if (rec) {
            setAiText(rec.aiText || "");
            setAiReasoning(rec.aiReasoning || "");
            setAiModel(rec.aiModel || "");
            setCopyText(rec.copyText || "");
            setCopyReasoning(rec.copyReasoning || "");
            setCopyPlan(rec.copyPlan ?? null);
            setPurchasedKeys(rec.purchasedKeys || {});
          }
          try {
            const parsed = await parsePobCodeBrowser(sess.lastPobCode);
            setProfile(buildLocalProfile(parsed));
          } catch {
            // ignore corrupted cached pob
          }
        } else {
          const rec = sess.savedByBuild?.["__consult__"];
          if (rec) {
            setCopyText(rec.copyText || "");
            setCopyReasoning(rec.copyReasoning || "");
            setCopyPlan(rec.copyPlan ?? null);
            setPurchasedKeys(rec.purchasedKeys || {});
          }
        }
      }
      sessionHydratedRef.current = true;
    });
  }, []);

  // 自动将抄作业条件、AI 方案与已入手勾选写入扩展本地存储
  useEffect(() => {
    if (!sessionHydratedRef.current) return;
    const key = makeExtPobKey(activePobCode);
    const hasContent = Boolean(aiText || copyText || copyPlan || Object.keys(purchasedKeys).length > 0);
    const nextMap = hasContent
      ? {
          ...savedByBuild,
          [key]: {
            pobKey: key,
            aiText,
            aiReasoning,
            aiModel,
            copyText,
            copyReasoning,
            copyPlan,
            purchasedKeys,
            updatedAt: Date.now(),
          },
        }
      : savedByBuild;
    const timer = window.setTimeout(() => {
      void saveExtCopySession({
        copyContext,
        lastInput: input,
        lastSourceUrl: sourceUrl,
        lastPobCode: activePobCode,
        savedByBuild: nextMap,
      });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [copyContext, input, sourceUrl, activePobCode, aiText, aiReasoning, aiModel, copyText, copyReasoning, copyPlan, purchasedKeys, savedByBuild]);

  // 心跳探测：启动时立即检测 + 每 12 秒静默轮询
  useEffect(() => {
    void refreshHealth();
    const timer = window.setInterval(() => {
      void refreshHealth();
    }, 12000);
    return () => window.clearInterval(timer);
  }, [refreshHealth]);

  // 后端在线时建立 SSE 双向订阅 + 拉取待处理的桌面搜货请求
  useEffect(() => {
    if (!health.online) return;
    let active = true;

    workbenchClient.fetchPendingTradeRequests().then((reqs) => {
      if (active) setPendingTrades(reqs);
    });

    const unsub = workbenchClient.subscribeEvents({
      onTradeOpenRequested: (req) => {
        setPendingTrades((prev) => [req, ...prev.filter((r) => r.id !== req.id)]);
      },
      onTargetUpdated: () => {
        void refreshHealth();
      },
      onPricesUpdated: () => {
        void refreshHealth();
      },
    });

    return () => {
      active = false;
      unsub();
    };
  }, [health.online, refreshHealth]);

  // 加载活跃赛季列表
  useEffect(() => {
    fetchActiveLeagues()
      .then((list) => {
        if (list.length > 0) {
          setLeagues(list);
          setLeague(list[0].name);
        }
      })
      .catch(() => undefined);
  }, []);

  // 赛季切换时加载 poe.ninja 30min 缓存价格表
  const refreshNinjaPrices = useCallback(
    async (force = false) => {
      if (!league) return;
      setLoadingNinja(true);
      try {
        const idx = await loadLeaguePrices(league, force);
        setPriceIndex(idx);
      } catch {
        // 离线或限流时不阻断主流程
      } finally {
        setLoadingNinja(false);
      }
    },
    [league]
  );

  useEffect(() => {
    void refreshNinjaPrices(false);
  }, [refreshNinjaPrices]);

  const uniquePrices = useMemo(() => {
    if (!profile) return [];
    return matchUniquePricesForBuild(profile.build, priceIndex);
  }, [profile, priceIndex]);

  const priceSnapshot = useMemo(() => {
    const quotes = Object.values(tradeQuotes);
    if (uniquePrices.length === 0 && quotes.length === 0) return null;
    return buildPriceSnapshot(league, uniquePrices, quotes);
  }, [league, uniquePrices, tradeQuotes]);

  const targetPayload = useMemo<TargetBuildPayload | null>(() => {
    if (!profile || !activePobCode) return null;
    return {
      pobCode: activePobCode,
      sourceUrl,
      title: profile.positioning.archetype,
      league,
      positioning: profile.positioning,
      pushedAt: Date.now(),
    };
  }, [profile, activePobCode, sourceUrl, league]);

  const buildTreeVersion = profile?.build?.passiveTree?.treeVersion || "";
  const dictOutdated = useMemo(
    () => isDictOutdatedForTree(patchDict, buildTreeVersion),
    [patchDict, buildTreeVersion]
  );

  const runEssenceStream = useCallback(
    async (targetCode: string, targetProfile: BuildProfileResponse, onlineNow: boolean) => {
      essenceAbortRef.current?.abort();
      const ctrl = new AbortController();
      essenceAbortRef.current = ctrl;
      setAiLoading(true);
      setAiText("");
      setAiReasoning("");
      setAiError("");
      try {
        await runDualTrackStreamAnalysis({
          mode: "target",
          backendOnline: onlineNow,
          pobCode: targetCode,
          profile: targetProfile,
          signal: ctrl.signal,
          handlers: {
            onMeta: (m) => setAiModel(m.model || ""),
            onReasoningDelta: (d) => setAiReasoning((prev) => prev + d),
            onDelta: (d) => setAiText((prev) => prev + d),
            onStructured: (_s, nextPos) => {
              if (nextPos) {
                setProfile((prev) => (prev ? { ...prev, positioning: nextPos } : prev));
              }
            },
            onDone: (res) => {
              if (res.text) setAiText(res.text);
              if (res.reasoning) setAiReasoning(res.reasoning);
              if (res.model) setAiModel(res.model);
              if (res.positioning) {
                setProfile((prev) => (prev ? { ...prev, positioning: res.positioning! } : prev));
              }
            },
          },
        });
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setAiError((err as Error).message || "流式本质拆解失败");
        }
      } finally {
        setAiLoading(false);
      }
    },
    []
  );

  async function analyzeResolved(pobCode: string, srcUrl?: string) {
    essenceAbortRef.current?.abort();
    copyAbortRef.current?.abort();
    setAnalyzing(true);
    setErrorMsg("");
    setPushStatus({ kind: "idle", text: "" });
    setTradeQuotes({});
    // 先把当前激活 BD 的最新抄作业进度刷回内存字典，切换回旧 BD 时不丢失
    let latestMap = savedByBuild;
    if (activePobCode && (aiText || copyText || copyPlan || Object.keys(purchasedKeys).length > 0)) {
      const prevKey = makeExtPobKey(activePobCode);
      latestMap = {
        ...savedByBuild,
        [prevKey]: {
          pobKey: prevKey,
          aiText,
          aiReasoning,
          aiModel,
          copyText,
          copyReasoning,
          copyPlan,
          purchasedKeys,
          updatedAt: Date.now(),
        },
      };
      setSavedByBuild(latestMap);
    }
    const existing = latestMap[makeExtPobKey(pobCode)];
    setAiText(existing?.aiText || "");
    setAiReasoning(existing?.aiReasoning || "");
    setAiModel(existing?.aiModel || "");
    setAiError("");
    setCopyText(existing?.copyText || "");
    setCopyReasoning(existing?.copyReasoning || "");
    setCopyError("");
    setCopyPlan(existing?.copyPlan ?? null);
    setPurchasedKeys(existing?.purchasedKeys || {});
    try {
      let prof: BuildProfileResponse;
      if (health.online) {
        try {
          prof = await workbenchClient.fetchProfile(pobCode, runSimulation);
        } catch {
          const parsed = await parsePobCodeBrowser(pobCode);
          prof = buildLocalProfile(parsed);
        }
      } else {
        const parsed = await parsePobCodeBrowser(pobCode);
        prof = buildLocalProfile(parsed);
      }
      setActivePobCode(pobCode);
      setSourceUrl(srcUrl);
      setProfile(prof);
      setTab("positioning");

      if (extLlmConfig.autoRunOnImport && (extLlmConfig.enabled || health.online)) {
        void runEssenceStream(pobCode, prof, health.online);
      }
    } catch (err) {
      setErrorMsg((err as Error).message || "解析 PoB2 失败");
    } finally {
      setAnalyzing(false);
    }
  }

  async function handleAnalyzeInput() {
    setAnalyzing(true);
    setErrorMsg("");
    try {
      const resolved = await resolveSourceInput(input);
      await analyzeResolved(resolved.pobCode, resolved.sourceUrl);
    } catch (err) {
      setErrorMsg((err as Error).message);
      setAnalyzing(false);
    }
  }

  async function handleCaptureTab() {
    setAnalyzing(true);
    setErrorMsg("");
    try {
      const captured = await captureFromActiveTab();
      setInput(captured.sourceUrl || captured.pobCode.slice(0, 120));
      await analyzeResolved(captured.pobCode, captured.sourceUrl);
    } catch (err) {
      setErrorMsg((err as Error).message);
      setAnalyzing(false);
    }
  }

  async function handleUpdatePatchDict() {
    setDictUpdating(true);
    setDictMessage("");
    try {
      const updated = await updatePatchDict({
        targetTreeVersion: buildTreeVersion || undefined,
        backendOnline: health.online,
      });
      setPatchDict(updated);
      setDictMessage(
        `已更新至 v${updated.treeVersion}（天赋 ${updated.nodeCount} · 宝石 ${updated.gemCount}）`
      );
      if (activePobCode && !health.online) {
        const parsed = await parsePobCodeBrowser(activePobCode);
        setProfile(buildLocalProfile(parsed));
      }
    } catch (err) {
      setDictMessage(`更新失败：${(err as Error).message}`);
    } finally {
      setDictUpdating(false);
    }
  }

  async function handleSaveExtLlmConfig(next: ExtensionLlmConfig) {
    await saveExtensionLlmConfig(next);
    setExtLlmConfig(next);
  }

  async function handleRunCopyStream(mode: "copy" | "consult") {
    copyAbortRef.current?.abort();
    const ctrl = new AbortController();
    copyAbortRef.current = ctrl;
    setCopyStreaming(true);
    setCopyText("");
    setCopyReasoning("");
    setCopyError("");
    setCopyPlan(null);
    try {
      await runDualTrackStreamAnalysis({
        mode,
        backendOnline: health.online,
        league,
        pobCode: activePobCode,
        profile,
        copyContext,
        priceSnapshot,
        essenceSummary: aiText,
        signal: ctrl.signal,
        handlers: {
          onReasoningDelta: (d) => setCopyReasoning((prev) => prev + d),
          onDelta: (d) => setCopyText((prev) => prev + d),
          onStructured: (_s, _pos, nextPlan) => {
            if (nextPlan) setCopyPlan(nextPlan);
          },
          onDone: (res) => {
            if (res.text) setCopyText(res.text);
            if (res.reasoning) setCopyReasoning(res.reasoning);
            if (res.copyPlan) setCopyPlan(res.copyPlan);
          },
        },
      });
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        setCopyError((err as Error).message || "生成抄作业方案失败");
      }
    } finally {
      setCopyStreaming(false);
    }
  }

  function handleQuoteUpdate(quote: ItemTradeQuote) {
    setTradeQuotes((prev) => {
      const next = {
        ...prev,
        [`${quote.slot}:${quote.kind}`]: quote,
      };
      if (health.online) {
        const snap = buildPriceSnapshot(league, uniquePrices, Object.values(next));
        void workbenchClient.pushPrices(snap).catch(() => undefined);
      }
      return next;
    });
  }

  async function handlePushTarget() {
    if (!targetPayload) return;
    setPushStatus({ kind: "idle", text: "" });
    try {
      const saved = await workbenchClient.pushTargetBuild({ ...targetPayload, pushedAt: Date.now() });
      let priceNote = "";
      if (priceSnapshot) {
        try {
          const pres = await workbenchClient.pushPrices(priceSnapshot);
          priceNote = `，并自动同步 ${pres.count} 条实时物价`;
        } catch {
          // 忽略价格同步异常，不阻断目标推送
        }
      }
      setPushStatus({ kind: "ok", text: `已推送目标 BD「${saved.title || "目标构筑"}」到桌面工作台${priceNote}！` });
      void refreshHealth();
    } catch (err) {
      setPushStatus({ kind: "error", text: (err as Error).message });
    }
  }

  async function handlePushPrices() {
    if (!priceSnapshot) return;
    setPushStatus({ kind: "idle", text: "" });
    try {
      const res = await workbenchClient.pushPrices(priceSnapshot);
      setPushStatus({ kind: "ok", text: `已推送 ${res.count} 条市集价格快照（含原件/平替/半成品）到桌面工作台！` });
      void refreshHealth();
    } catch (err) {
      setPushStatus({ kind: "error", text: (err as Error).message });
    }
  }

  async function handleOpenTradeRequest(req: TradeOpenRequest) {
    const url =
      req.tradeUrl ||
      (req.query
        ? buildFallbackTradeUrl(req.league || league, req.query)
        : `https://www.pathofexile.com/trade2/search/poe2/${encodeURIComponent(req.league || league)}`);
    openUrlInTab(url);
    await handleAckTradeRequest(req.id);
  }

  async function handleAckTradeRequest(id: string) {
    await workbenchClient.ackTradeRequest(id);
    setPendingTrades((prev) => prev.filter((r) => r.id !== id));
    void refreshHealth();
  }

  return (
    <div className="app-shell">
      <StatusHeader
        health={health}
        onRefreshHealth={() => void refreshHealth()}
        leagues={leagues}
        league={league}
        onLeagueChange={setLeague}
        pendingTradeCount={pendingTrades.length}
        onJumpToBridge={() => setTab("bridge")}
        onToggleSettings={() => setSettingsOpen((v) => !v)}
        dictOutdated={dictOutdated}
        langMode={langMode}
        onLangModeChange={handleLangModeChange}
      />

      <main className="main">
        <AiConfigDrawer
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          config={extLlmConfig}
          onSaveConfig={handleSaveExtLlmConfig}
          patchDict={patchDict}
          buildTreeVersion={buildTreeVersion}
          dictUpdating={dictUpdating}
          dictMessage={dictMessage}
          onUpdatePatchDict={handleUpdatePatchDict}
          backendOnline={health.online}
        />

        {dictOutdated && !settingsOpen && (
          <div className="alert" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
            <span>
              检测到该 BD 为 PoE2 v{buildTreeVersion}（当前词典 v{patchDict?.treeVersion || "?"}），建议热更新词典以获取最新天赋/宝石原文。
            </span>
            <button type="button" className="btn sm primary" disabled={dictUpdating} onClick={() => void handleUpdatePatchDict()}>
              {dictUpdating ? "更新中..." : "🔄 一键更新"}
            </button>
          </div>
        )}

        <ImportBar
          input={input}
          onInputChange={setInput}
          loading={analyzing}
          runSimulation={runSimulation}
          onRunSimulationChange={setRunSimulation}
          backendOnline={health.online}
          hasProfile={Boolean(profile)}
          activeTitle={profile?.positioning.archetype || ""}
          onCaptureTab={() => void handleCaptureTab()}
          onAnalyze={() => void handleAnalyzeInput()}
        />

        {errorMsg && <div className="alert error">{errorMsg}</div>}

        <nav className="tabs">
          <button
            type="button"
            className={`tab-btn ${tab === "positioning" ? "active" : ""}`}
            onClick={() => setTab("positioning")}
          >
            🔬 拆解 &amp; 抄作业
          </button>
          <button
            type="button"
            className={`tab-btn ${tab === "market" ? "active" : ""}`}
            onClick={() => setTab("market")}
          >
            💰 装备核价
          </button>
          <button
            type="button"
            className={`tab-btn ${tab === "bridge" ? "active" : ""}`}
            onClick={() => setTab("bridge")}
          >
            🔗 桌面联动{pendingTrades.length > 0 ? ` (${pendingTrades.length})` : ""}
          </button>
        </nav>

        {tab === "positioning" &&
          (profile ? (
            <>
              <CopyBuildBar
                profile={profile}
                league={league}
                backendOnline={health.online}
                priceIndex={priceIndex}
                context={copyContext}
                onChange={setCopyContext}
                uniquePrices={uniquePrices}
                tradeQuotes={tradeQuotes}
                onQuoteUpdate={handleQuoteUpdate}
                onOpenExternalUrl={openUrlInTab}
                streaming={copyStreaming}
                copyText={copyText}
                copyReasoning={copyReasoning}
                copyError={copyError}
                copyPlan={copyPlan}
                purchasedKeys={purchasedKeys}
                onTogglePurchased={(k) =>
                  setPurchasedKeys((prev) => ({ ...prev, [k]: !prev[k] }))
                }
                onGenerateCopyPlan={() => void handleRunCopyStream("copy")}
                onStopStream={() => copyAbortRef.current?.abort()}
                onConsultWithoutPob={() => void handleRunCopyStream("consult")}
              />

              <PositioningCard
                t={translator}
                profile={profile}
                backendOnline={health.online}
                aiLoading={aiLoading}
                aiText={aiText}
                aiReasoning={aiReasoning}
                aiModel={aiModel}
                aiError={aiError}
                onRunAiAnalysis={() => void runEssenceStream(activePobCode, profile, health.online)}
                onStopAiAnalysis={() => essenceAbortRef.current?.abort()}
              />

              <div className="row between card">
                <span className="muted">已有自己的 PoB 码想精确算 ΔDPS？一键推送到桌面端</span>
                <button
                  type="button"
                  className="btn sm primary"
                  disabled={!health.online}
                  onClick={() => void handlePushTarget()}
                >
                  🚀 推送目标 BD
                </button>
              </div>
              {pushStatus.kind !== "idle" && (
                <div className={`alert ${pushStatus.kind === "ok" ? "success" : "error"}`}>
                  {pushStatus.text}
                </div>
              )}
            </>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <section className="card">
                <div className="muted">
                  在上方粘贴 PoB2 分享码 / pobb.in 链接，或打开 poe.ninja PoE2 角色页点击「抓取当前页」，即可立刻生成<strong>按等级与预算排好序的一键买/做决策卡</strong>；也可在下方无 PoB 码直接咨询起步思路。
                </div>
              </section>
              <CopyBuildBar
                profile={null}
                league={league}
                backendOnline={health.online}
                priceIndex={priceIndex}
                context={copyContext}
                onChange={setCopyContext}
                uniquePrices={[]}
                tradeQuotes={{}}
                onQuoteUpdate={handleQuoteUpdate}
                onOpenExternalUrl={openUrlInTab}
                streaming={copyStreaming}
                copyText={copyText}
                copyReasoning={copyReasoning}
                copyError={copyError}
                copyPlan={copyPlan}
                purchasedKeys={purchasedKeys}
                onTogglePurchased={(k) =>
                  setPurchasedKeys((prev) => ({ ...prev, [k]: !prev[k] }))
                }
                onGenerateCopyPlan={() => void handleRunCopyStream("copy")}
                onStopStream={() => copyAbortRef.current?.abort()}
                onConsultWithoutPob={() => void handleRunCopyStream("consult")}
              />
            </div>
          ))}

        {tab === "market" &&
          (profile ? (
            <MarketPricingPanel
              t={translator}
              build={profile.build}
              league={league}
              priceIndex={priceIndex}
              loadingNinja={loadingNinja}
              onRefreshNinja={() => void refreshNinjaPrices(true)}
              uniquePrices={uniquePrices}
              tradeQuotes={tradeQuotes}
              onQuoteUpdate={handleQuoteUpdate}
              onOpenExternalUrl={openUrlInTab}
            />
          ) : (
            <section className="card">
              <div className="muted">请先在上方导入一个目标 BD，再进行暗金自动核价与官方市集三档（原件/平替/半成品）查价。</div>
            </section>
          ))}

        {tab === "bridge" && (
          <DesktopBridgeBar
            health={health}
            targetPayload={targetPayload}
            priceSnapshot={priceSnapshot}
            pendingTrades={pendingTrades}
            pushStatus={pushStatus}
            onPushTarget={() => void handlePushTarget()}
            onPushPrices={() => void handlePushPrices()}
            onOpenTradeRequest={(req) => void handleOpenTradeRequest(req)}
            onAckTradeRequest={(id) => void handleAckTradeRequest(id)}
            onOpenUrl={openUrlInTab}
            onCopySuccess={(msg) => setPushStatus({ kind: "ok", text: msg })}
          />
        )}
      </main>
    </div>
  );
};
