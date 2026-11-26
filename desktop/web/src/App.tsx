import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createTranslator,
  loadBilingualDict,
  nextLangMode,
  readStoredLangMode,
  writeStoredLangMode,
  type BilingualDictionary,
  type BudgetRoadmapReport,
  type BuildAblationDossier,
  type BuildProfileResponse,
  type CopyBuildContext,
  type CopyPlanItemUserState,
  type GapAnalysisReport,
  type LangMode,
  type LlmAnalyzeMode,
  type PriceSnapshot,
  type SavedCopyPlanRecord,
  type SlotAffixReport,
  type TargetBuildPayload,
} from "@contracts";
import {
  BACKEND_URL,
  desktopApi,
  type BackendHealth,
  type LlmAnalyzeResult,
  type LlmConfigPublic,
  type MarketLeagueInfo,
  type MyBuildRecord,
} from "./api/workbenchApi";
import { consumeStartupDeepLinks, parseDeepLinkUrl, subscribeDeepLinks } from "./api/tauriBridge";
import { AiSettingsModal } from "./components/AiSettingsModal";
import { EngineSetupModal } from "./components/EngineSetupModal";
import { TopBar } from "./components/TopBar";
import { Craft } from "./pages/Craft";
import { Overview } from "./pages/Overview";
import { Upgrade } from "./pages/Upgrade";
import {
  computePobHash,
  findMatchingSavedPlan,
  mergeCopyPlans,
  patchCopyPlanItemState,
  readActivePlanIdForBuild,
  readLocalCopyContext,
  readLocalCopyPlans,
  upsertCopyPlan,
  writeActivePlanIdForBuild,
  writeLocalCopyContext,
  writeLocalCopyPlans,
} from "./utils/copyPlanStorage";
import { parseTargetPasteInput } from "./utils/handoffParser";

type PageId = "overview" | "upgrade" | "craft";

const INIT_HEALTH: BackendHealth = {
  online: false,
  engineStarted: false,
  workersRunning: 0,
  treeVersions: [],
  targetBuildTitle: "",
  pricesCount: 0,
  pendingTradeRequests: 0,
};

export const App: React.FC = () => {
  const [page, setPage] = useState<PageId>("overview");
  const [health, setHealth] = useState<BackendHealth>(INIT_HEALTH);
  const [sseConnected, setSseConnected] = useState(false);

  const [myBuild, setMyBuild] = useState<MyBuildRecord | null>(null);
  const [targetBuild, setTargetBuild] = useState<TargetBuildPayload | null>(null);
  const [targetProfile, setTargetProfile] = useState<BuildProfileResponse | null>(null);
  const [targetDossier, setTargetDossier] = useState<BuildAblationDossier | null>(null);
  const [dossierLoading, setDossierLoading] = useState(false);
  const [dossierError, setDossierError] = useState("");
  const [priceSnapshot, setPriceSnapshot] = useState<PriceSnapshot | null>(null);
  const [leagues, setLeagues] = useState<MarketLeagueInfo[]>([
    { name: "Standard", displayName: "Standard", url: "standard" },
  ]);
  const [selectedLeague, setSelectedLeague] = useState<string>("");
  const [pricingBusy, setPricingBusy] = useState<string>("");
  const [pricingStatus, setPricingStatus] = useState<string>("");

  const [gapReport, setGapReport] = useState<GapAnalysisReport | null>(null);
  const [priceOverrides, setPriceOverrides] = useState<Record<string, number>>({});
  const [craftSeed, setCraftSeed] = useState<SlotAffixReport | null>(null);

  const [llmConfig, setLlmConfig] = useState<LlmConfigPublic | null>(null);
  const [showAiModal, setShowAiModal] = useState(false);
  const [showEngineModal, setShowEngineModal] = useState(false);
  const autoPromptedEngineRef = useRef(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiResult, setAiResult] = useState<LlmAnalyzeResult | null>(null);
  const [aiError, setAiError] = useState("");
  const [aiRatingLoading, setAiRatingLoading] = useState(false);
  const [aiRatingError, setAiRatingError] = useState("");
  const autoRatedKeyRef = useRef<string>("");
  const aiAbortRef = useRef<AbortController | null>(null);
  const [copyContext, setCopyContext] = useState<CopyBuildContext>(() => readLocalCopyContext());
  const [savedPlans, setSavedPlans] = useState<SavedCopyPlanRecord[]>(() => readLocalCopyPlans());
  const [activePlanId, setActivePlanId] = useState<string | null>(null);
  const lastRestoredBuildRef = useRef<string | null>(null);

  const handleCopyContextChange = useCallback((next: CopyBuildContext) => {
    setCopyContext(next);
    writeLocalCopyContext(next);
  }, []);

  const [analyzing, setAnalyzing] = useState(false);
  const [toast, setToast] = useState<{ kind: "info" | "success" | "error"; text: string } | null>(null);
  const [langMode, setLangMode] = useState<LangMode>(() => readStoredLangMode());
  const [bilingualDict, setBilingualDict] = useState<BilingualDictionary | null>(null);
  const loadedDossierCodeRef = useRef<string>("");

  const handleLangModeChange = useCallback((mode: LangMode) => {
    setLangMode(mode);
    writeStoredLangMode(mode);
  }, []);

  useEffect(() => {
    let active = true;
    loadBilingualDict(BACKEND_URL).then((d) => {
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

  const syncAllFromBackend = useCallback(async () => {
    const h = await desktopApi.checkHealth();
    setHealth(h);
    if (!h.online) return;
    const [mine, tgt, prices, llmCfg, lgList, remotePlans] = await Promise.all([
      desktopApi.getMyBuild(),
      desktopApi.getTargetBuild(),
      desktopApi.getPrices(),
      desktopApi.getLlmConfig(),
      desktopApi.getMarketLeagues(),
      desktopApi.listCopyPlans(),
    ]);
    setMyBuild(mine);
    setTargetBuild(tgt);
    setPriceSnapshot(prices);
    if (llmCfg) setLlmConfig(llmCfg);
    if (lgList && lgList.length > 0) {
      setLeagues(lgList);
      setSelectedLeague((prev) => prev || tgt?.league || prices?.league || lgList[0].name);
    }
    setSavedPlans((prev) => {
      const merged = mergeCopyPlans(prev, remotePlans);
      writeLocalCopyPlans(merged);
      return merged;
    });
  }, []);

  const activeLeague = selectedLeague || targetBuild?.league || priceSnapshot?.league || leagues[0]?.name || "Standard";

  const loadTargetDossier = useCallback(
    async (pobCode: string, forceRefresh = false) => {
      setDossierLoading(true);
      setDossierError("");
      try {
        const d = await desktopApi.getAblationDossier({ pobCode, league: activeLeague, forceRefresh });
        setTargetDossier(d);
      } catch (err) {
        setDossierError((err as Error).message);
      } finally {
        setDossierLoading(false);
      }
    },
    [activeLeague]
  );

  const handleRefreshNinjaPrices = useCallback(
    async (forceRefresh = true) => {
      if (!targetBuild?.pobCode) return;
      setPricingBusy("ninja");
      setPricingStatus(`正在拉取 ${activeLeague} 赛季 poe.ninja 暗金/珠宝/宝石实时物价...`);
      try {
        const res = await desktopApi.priceBuildNinja({
          pobCode: targetBuild.pobCode,
          league: activeLeague,
          forceRefresh,
        });
        setPriceSnapshot(res.prices);
        const rateTxt = res.exaltPerDivine ? `（1D ≈ ${res.exaltPerDivine}E）` : "";
        setPricingStatus(`已同步 ${res.league} 赛季 poe.ninja 实时价：命中 ${res.matchedCount} 件暗金/血脉宝石${rateTxt}，已自动更正本地造价。`);
      } catch (err) {
        setPricingStatus(`拉取 Ninja 实时价失败：${(err as Error).message}`);
      } finally {
        setPricingBusy("");
      }
    },
    [targetBuild?.pobCode, activeLeague]
  );

  const handleOverrideItemPrice = useCallback(
    async (slot: string, divineValue: number, itemName?: string) => {
      const saved = await desktopApi.overrideItemPrice({
        slot,
        divineValue,
        league: activeLeague,
        itemName,
      });
      setPriceSnapshot(saved);
      setPricingStatus(`已将 ${slot} 参考价更正为 ${divineValue} D（直接写入本地缓存，未重跑消融）。`);
    },
    [activeLeague]
  );

  // 仅当目标 BD 码本身变化或后端上线时拉取 Profile（命中本地 baseSim 秒开）
  useEffect(() => {
    if (!health.online || !targetBuild?.pobCode) {
      setTargetProfile(null);
      return;
    }
    let cancelled = false;
    desktopApi
      .getBuildProfile(targetBuild.pobCode, true, false)
      .then((prof) => {
        if (!cancelled) setTargetProfile(prof);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [health.online, targetBuild?.pobCode]);

  // 加载消融账本：同一目标 BD 仅价格/赛季变动时静默毫秒级覆写本地底座，不闪烁不折叠
  useEffect(() => {
    if (!health.online || !targetBuild?.pobCode) {
      loadedDossierCodeRef.current = "";
      setTargetDossier(null);
      setDossierLoading(false);
      setDossierError("");
      return;
    }
    let cancelled = false;
    const code = targetBuild.pobCode;
    const isSameBuild = loadedDossierCodeRef.current === code;
    if (!isSameBuild) {
      setDossierLoading(true);
    }
    setDossierError("");
    desktopApi
      .getAblationDossier({ pobCode: code, league: activeLeague })
      .then((d) => {
        if (!cancelled) {
          loadedDossierCodeRef.current = code;
          setTargetDossier(d);
        }
      })
      .catch((err) => {
        if (!cancelled) setDossierError((err as Error).message);
      })
      .finally(() => {
        if (!cancelled) setDossierLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [health.online, targetBuild?.pobCode, priceSnapshot?.updatedAt, activeLeague]);

  useEffect(() => {
    void syncAllFromBackend();
    const timer = window.setInterval(() => {
      desktopApi.checkHealth().then(setHealth).catch(() => undefined);
    }, 12000);
    return () => window.clearInterval(timer);
  }, [syncAllFromBackend]);

  // 订阅 poe2wb:// Deep Link 协议唤醒（Tauri 2 或浏览器 ?deepLink=... 参数）
  const handleDeepLinkUrl = useCallback(async (rawUrl: string) => {
    const dl = parseDeepLinkUrl(rawUrl);
    if (!dl) return;
    let code = dl.pobCode || "";
    if (!code && dl.fromClipboard && typeof navigator !== "undefined" && navigator.clipboard?.readText) {
      try {
        code = (await navigator.clipboard.readText()).trim();
      } catch {
        // 剪贴板权限拒绝时提示用户手动粘贴
      }
    }
    if (!code) {
      setToast({
        kind: "info",
        text: `已响应 poe2wb:// 唤醒（${dl.title || "目标构筑"}），请直接按 Cmd/Ctrl+V 粘贴剪贴板中的 PoB2 码。`,
      });
      return;
    }
    try {
      const parsed = parseTargetPasteInput(code);
      const saved = await desktopApi.saveTargetBuild({
        pobCode: parsed.pobCode,
        title: dl.title || parsed.target?.title,
        league: dl.league || parsed.target?.league,
        sourceUrl: dl.sourceUrl || parsed.target?.sourceUrl,
        positioning: parsed.target?.positioning,
      });
      setTargetBuild(saved);
      if (parsed.prices) {
        await desktopApi.savePrices(parsed.prices);
        setPriceSnapshot(parsed.prices);
      }
      setToast({ kind: "success", text: `通过 poe2wb:// 协议自动导入目标 BD「${saved.title || "目标构筑"}」！` });
    } catch (err) {
      setToast({ kind: "error", text: `Deep Link 导入失败：${(err as Error).message}` });
    }
  }, []);

  useEffect(() => {
    consumeStartupDeepLinks().then((urls) => {
      for (const u of urls) void handleDeepLinkUrl(u);
    });
    return subscribeDeepLinks((u) => void handleDeepLinkUrl(u));
  }, [handleDeepLinkUrl]);

  useEffect(() => {
    if (health.online && health.pob2Ready === false && !autoPromptedEngineRef.current) {
      autoPromptedEngineRef.current = true;
      setShowEngineModal(true);
    }
  }, [health.online, health.pob2Ready]);

  // 订阅 SSE /events：扩展推送新目标 BD 或新市集价格时即时刷新
  useEffect(() => {
    if (!health.online) {
      setSseConnected(false);
      return;
    }
    const unsub = desktopApi.subscribeEvents({
      onConnected: () => setSseConnected(true),
      onTargetUpdated: (info) => {
        setToast({
          kind: "success",
          text: `收到浏览器扩展推送的新目标 BD「${info.title || info.archetype || "目标构筑"}」，已自动同步！`,
        });
        void desktopApi.getTargetBuild().then(setTargetBuild);
        void desktopApi.checkHealth().then(setHealth);
      },
      onEngineUpdated: () => {
        setToast({ kind: "success", text: "PoB2 计算内核已更新就绪！" });
        void syncAllFromBackend();
      },
      onPricesUpdated: (info) => {
        setToast({
          kind: "info",
          text: `收到浏览器扩展推送的最新市集价格（${info.count || 0} 条），升级性价比与预算路线可直接使用！`,
        });
        void desktopApi.getPrices().then(setPriceSnapshot);
        void desktopApi.checkHealth().then(setHealth);
      },
      onError: () => setSseConnected(false),
    });
    return () => unsub();
  }, [health.online]);

  async function handleSaveMyBuild(code: string) {
    setToast(null);
    try {
      const saved = await desktopApi.saveMyBuild(code);
      setMyBuild(saved);
      setToast({ kind: "success", text: `已保存「我的 BD」：${saved.title || "当前角色"}` });
    } catch (err) {
      setToast({ kind: "error", text: (err as Error).message });
    }
  }

  async function handleSaveTargetInput(raw: string) {
    setToast(null);
    try {
      const parsed = parseTargetPasteInput(raw);
      const saved = await desktopApi.saveTargetBuild(
        parsed.target
          ? { ...parsed.target, pobCode: parsed.pobCode }
          : { pobCode: parsed.pobCode }
      );
      setTargetBuild(saved);
      if (parsed.prices) {
        await desktopApi.savePrices(parsed.prices);
        setPriceSnapshot(parsed.prices);
      }
      setToast({
        kind: "success",
        text:
          parsed.kind === "handoff"
            ? `已从扩展交接包导入目标 BD「${saved.title}」及价格快照！`
            : `已导入目标 BD「${saved.title || "目标构筑"}」`,
      });
    } catch (err) {
      setToast({ kind: "error", text: (err as Error).message });
    }
  }

  const handleRunGap = useCallback(async () => {
    if (!myBuild?.pobCode || !targetBuild?.pobCode) return;
    setAnalyzing(true);
    setToast(null);
    try {
      const report = await desktopApi.analyzeGap({
        myPobCode: myBuild.pobCode,
        targetPobCode: targetBuild.pobCode,
        priceOverrides,
      });
      setGapReport(report);
      setToast({
        kind: "success",
        text: `全景诊断完成（耗时 ${report.elapsedMs}ms）：已实算 0 成本提升与 ${report.slotUpgrades.length} 件装备试穿。`,
      });
    } catch (err) {
      setToast({ kind: "error", text: (err as Error).message });
    } finally {
      setAnalyzing(false);
    }
  }, [myBuild, targetBuild, priceOverrides]);

  const handlePriceOverrideChange = useCallback((slot: string, value: number | null) => {
    setPriceOverrides((prev) => {
      const next = { ...prev };
      if (value === null || value <= 0) delete next[slot];
      else next[slot] = value;
      return next;
    });
  }, []);

  const handleInspectAffixes = useCallback(
    async (slot: string): Promise<SlotAffixReport> => {
      return desktopApi.analyzeSlotAffixes({
        slot,
        myPobCode: myBuild?.pobCode,
        targetPobCode: targetBuild?.pobCode,
      });
    },
    [myBuild, targetBuild]
  );

  const handlePlanRoadmap = useCallback(
    async (budget: number, overrides: Record<string, number>): Promise<BudgetRoadmapReport> => {
      if (!gapReport) throw new Error("尚未运行全景诊断");
      return desktopApi.planRoadmap({
        budgetDivine: budget,
        zeroCostSteps: gapReport.zeroCostSteps,
        slotUpgrades: gapReport.slotUpgrades,
        priceOverrides: overrides,
      });
    },
    [gapReport]
  );

  const handleSaveAiConfig = useCallback(
    async (payload: {
      enabled: boolean;
      provider: string;
      baseUrl: string;
      apiKey?: string;
      model: string;
      temperature: number;
      clearApiKey?: boolean;
      test?: boolean;
    }) => {
      const res = await desktopApi.saveLlmConfig(payload);
      setLlmConfig(res.config);
      return { testMessage: res.testResult?.text };
    },
    []
  );

  const handleRateTarget = useCallback(
    async (forceRefresh = false) => {
      if (!targetBuild?.pobCode) return;
      if (!llmConfig?.enabled) {
        setShowAiModal(true);
        return;
      }
      setAiRatingLoading(true);
      setAiRatingError("");
      try {
        const res = await desktopApi.rateTargetWithAi({
          pobCode: targetBuild.pobCode,
          profile: targetProfile || undefined,
          ablationDossier: targetDossier,
          forceRefresh,
        });
        if (res.positioning) {
          setTargetProfile((prev) =>
            prev
              ? {
                  ...prev,
                  positioning: res.positioning,
                  simulation: res.simulation ?? prev.simulation,
                  engineFacts: res.engineFacts ?? prev.engineFacts,
                }
              : prev
          );
          setTargetBuild((prev) => (prev ? { ...prev, positioning: res.positioning } : prev));
        }
      } catch (err) {
        setAiRatingError((err as Error).message || "AI 引擎评级失败");
      } finally {
        setAiRatingLoading(false);
      }
    },
    [llmConfig?.enabled, targetBuild?.pobCode, targetProfile, targetDossier]
  );

  // PoB2 引擎实算就绪且已启用 AI 时，自动由 AI 结合 7 维实算数据生成六边形多维能力图（命中缓存毫秒级）
  useEffect(() => {
    const code = targetBuild?.pobCode || "";
    if (!health.online || !llmConfig?.enabled || !code || !targetProfile?.simulation) return;
    const pCr = targetProfile.positioning?.contentReadiness;
    const bCr = targetBuild?.positioning?.contentReadiness;
    const hasAiRadar =
      (pCr?.confidence === "ai" && (pCr.radar?.length ?? 0) >= 6) ||
      (bCr?.confidence === "ai" && (bCr.radar?.length ?? 0) >= 6);
    if (hasAiRadar) return;
    const rateKey = `${code.slice(0, 64)}:${code.length}:${llmConfig.model || ""}:v12asc`;
    if (autoRatedKeyRef.current === rateKey) return;
    autoRatedKeyRef.current = rateKey;
    void handleRateTarget(false);
  }, [
    health.online,
    llmConfig?.enabled,
    llmConfig?.model,
    targetBuild?.pobCode,
    targetBuild?.positioning?.contentReadiness?.confidence,
    targetBuild?.positioning?.contentReadiness?.radar?.length,
    targetProfile?.simulation,
    targetProfile?.positioning?.contentReadiness?.confidence,
    targetProfile?.positioning?.contentReadiness?.radar?.length,
    handleRateTarget,
  ]);

  const currentPobHash = useMemo(() => computePobHash(targetBuild?.pobCode), [targetBuild?.pobCode]);

  const activeSavedPlan = useMemo(
    () => (activePlanId ? savedPlans.find((p) => p.id === activePlanId) || null : null),
    [savedPlans, activePlanId]
  );

  const applySavedPlanToView = useCallback(
    (plan: SavedCopyPlanRecord, syncContext = false) => {
      setActivePlanId(plan.id);
      writeActivePlanIdForBuild(currentPobHash, plan.id);
      setAiError("");
      setAiResult({
        text: plan.text,
        reasoning: plan.reasoning || "",
        model: plan.model,
        mode: plan.mode,
        cached: true,
        copyPlan: plan.copyPlan ?? null,
      });
      if (syncContext && (plan.mode === "copy" || plan.mode === "consult") && plan.copyContext) {
        const nextCtx: CopyBuildContext = {
          ...copyContext,
          characterLevel: plan.copyContext.characterLevel ?? copyContext.characterLevel ?? 90,
          budgetDivine: plan.copyContext.budgetDivine ?? copyContext.budgetDivine ?? 15,
          goalNote: plan.copyContext.goalNote ?? "",
          freeformQuery: plan.copyContext.freeformQuery || copyContext.freeformQuery,
        };
        handleCopyContextChange(nextCtx);
      }
    },
    [currentPobHash, copyContext, handleCopyContextChange]
  );

  // 目标 BD 加载/切换或本地+后端方案同步后，自动恢复最近一次本地保存的抄作业/拆解方案，不再需要重算
  useEffect(() => {
    if (aiLoading) return;
    const buildChanged = lastRestoredBuildRef.current !== currentPobHash;
    const currentStillValid = Boolean(activePlanId && savedPlans.some((p) => p.id === activePlanId));
    if (!buildChanged && currentStillValid) return;

    const preferredId = readActivePlanIdForBuild(currentPobHash);
    let candidate = preferredId ? savedPlans.find((p) => p.id === preferredId) || null : null;
    if (!candidate) {
      const sameBuild = savedPlans.filter((p) => (p.pobHash || "") === currentPobHash);
      candidate =
        sameBuild.find((p) => p.mode === "copy") ||
        sameBuild.find((p) => p.mode === "target") ||
        sameBuild[0] ||
        null;
    }
    if (candidate) {
      lastRestoredBuildRef.current = currentPobHash;
      setActivePlanId(candidate.id);
      setAiResult({
        text: candidate.text,
        reasoning: candidate.reasoning || "",
        model: candidate.model,
        mode: candidate.mode,
        cached: true,
        copyPlan: candidate.copyPlan ?? null,
      });
    } else if (buildChanged) {
      lastRestoredBuildRef.current = currentPobHash;
      setActivePlanId(null);
      setAiResult(null);
    }
  }, [currentPobHash, savedPlans, activePlanId, aiLoading]);

  const handleUpdateCopyPlanItemState = useCallback(
    (itemKey: string, patch: Partial<CopyPlanItemUserState>) => {
      if (!activePlanId) return;
      setSavedPlans((prev) => {
        const next = patchCopyPlanItemState(prev, activePlanId, itemKey, patch);
        writeLocalCopyPlans(next);
        return next;
      });
      if (health.online) {
        void desktopApi.updateCopyPlanItemState(activePlanId, itemKey, patch).catch(() => undefined);
      }
    },
    [activePlanId, health.online]
  );

  const handleDeleteSavedPlan = useCallback(
    (planId: string) => {
      setSavedPlans((prev) => {
        const next = prev.filter((p) => p.id !== planId);
        writeLocalCopyPlans(next);
        if (activePlanId === planId) {
          const fallback =
            next.find((p) => (p.pobHash || "") === currentPobHash && p.mode === "copy") ||
            next.find((p) => (p.pobHash || "") === currentPobHash) ||
            null;
          if (fallback) {
            setActivePlanId(fallback.id);
            writeActivePlanIdForBuild(currentPobHash, fallback.id);
            setAiResult({
              text: fallback.text,
              reasoning: fallback.reasoning || "",
              model: fallback.model,
              mode: fallback.mode,
              cached: true,
              copyPlan: fallback.copyPlan ?? null,
            });
          } else {
            setActivePlanId(null);
            writeActivePlanIdForBuild(currentPobHash, null);
            setAiResult(null);
          }
        }
        return next;
      });
      if (health.online) {
        void desktopApi.deleteCopyPlan(planId).catch(() => undefined);
      }
    },
    [activePlanId, currentPobHash, health.online]
  );

  const handleStopAi = useCallback(() => {
    aiAbortRef.current?.abort();
  }, []);

  const handleRunAi = useCallback(
    async (mode: LlmAnalyzeMode, forceRefresh = false) => {
      const activeLeagueNow = activeLeague || targetBuild?.league || priceSnapshot?.league || "Standard";
      if (!forceRefresh && (mode === "copy" || mode === "target" || mode === "consult" || mode === "gap")) {
        const matched = findMatchingSavedPlan(savedPlans, {
          mode,
          pobHash: mode === "consult" ? "" : currentPobHash,
          copyContext,
          league: activeLeagueNow,
        });
        if (matched && (matched.text || matched.copyPlan)) {
          applySavedPlanToView(matched, false);
          const label =
            mode === "copy"
              ? `Lv.${matched.copyContext?.characterLevel ?? copyContext.characterLevel ?? 90} / ${matched.copyContext?.budgetDivine ?? copyContext.budgetDivine ?? 15}D 抄作业方案`
              : mode === "target"
              ? "目标 BD 本质拆解"
              : mode === "gap"
              ? "双码差距诊断"
              : "无码咨询方案";
          setToast({
            kind: "info",
            text: `已从本地存储秒开「${label}」（如需重新生成请点「↻ 重新分析」）。`,
          });
          return;
        }
      }

      if (!llmConfig?.enabled) {
        setShowAiModal(true);
        return;
      }
      aiAbortRef.current?.abort();
      const ctrl = new AbortController();
      aiAbortRef.current = ctrl;
      setAiLoading(true);
      setAiError("");
      const savedTargetEssence =
        savedPlans.find((p) => p.mode === "target" && (p.pobHash || "") === currentPobHash)?.text || "";
      const prevEssence =
        mode === "copy" ? (aiResult?.mode === "target" ? aiResult.text : savedTargetEssence) : "";
      const reusePlanId =
        forceRefresh &&
        activeSavedPlan &&
        activeSavedPlan.mode === mode &&
        (activeSavedPlan.pobHash || "") === (mode === "consult" ? "" : currentPobHash)
          ? activeSavedPlan.id
          : undefined;
      setAiResult({ text: "", reasoning: "", model: llmConfig.model || "", mode });

      const buildTitleNow =
        targetBuild?.title ||
        targetProfile?.positioning?.archetype ||
        copyContext.goalNote ||
        "未命名构筑";
      // 合并高频流式 token 更新（~50ms 批刷新），避免每个 token 触发顶层重渲染风暴
      let pendingDelta = "";
      let pendingReasoning = "";
      let flushTimer: ReturnType<typeof setTimeout> | null = null;
      const flushStreamDeltas = () => {
        if (flushTimer !== null) {
          clearTimeout(flushTimer);
          flushTimer = null;
        }
        if (!pendingDelta && !pendingReasoning) return;
        const dText = pendingDelta;
        const dReason = pendingReasoning;
        pendingDelta = "";
        pendingReasoning = "";
        setAiResult((prev) => ({
          text: (prev?.text || "") + dText,
          reasoning: (prev?.reasoning || "") + dReason,
          toolStatus: dText ? "" : prev?.toolStatus || "",
          model: prev?.model || "",
          mode,
          cached: prev?.cached,
          copyPlan: prev?.copyPlan ?? null,
        }));
      };
      const scheduleFlush = () => {
        if (flushTimer === null) {
          flushTimer = setTimeout(flushStreamDeltas, 50);
        }
      };

      try {
        await desktopApi.streamAnalyzeWithLlm(
          {
            mode,
            league: activeLeagueNow,
            pobCode: targetBuild?.pobCode || undefined,
            buildTitle: buildTitleNow,
            planId: reusePlanId,
            useCache: !forceRefresh,
            profile: mode === "target" || mode === "copy" ? targetProfile || undefined : undefined,
            ablationDossier: mode === "target" || mode === "copy" ? targetDossier : undefined,
            gapReport: mode === "gap" ? gapReport || undefined : undefined,
            copyContext: mode === "copy" || mode === "consult" ? copyContext : undefined,
            essenceSummary: prevEssence,
          },
          {
            onMeta: (m) => {
              flushStreamDeltas();
              setAiResult((prev) => ({
                text: prev?.text || "",
                reasoning: prev?.reasoning || "",
                toolStatus: prev?.toolStatus || "",
                model: m.model || prev?.model || "",
                mode,
                cached: m.cached,
                copyPlan: prev?.copyPlan ?? null,
              }));
            },
            onToolStatus: (info) => {
              flushStreamDeltas();
              setAiResult((prev) => ({
                text: prev?.text || "",
                reasoning: prev?.reasoning || "",
                toolStatus: info.summary,
                model: prev?.model || "",
                mode,
                copyPlan: prev?.copyPlan ?? null,
              }));
            },
            onReasoningDelta: (d) => {
              pendingReasoning += d;
              scheduleFlush();
            },
            onDelta: (d) => {
              pendingDelta += d;
              scheduleFlush();
            },
            onStructured: (_s, nextPos, nextCopyPlan) => {
              flushStreamDeltas();
              if (nextPos) {
                setTargetProfile((prev) => (prev ? { ...prev, positioning: nextPos } : prev));
                setTargetBuild((prev) => (prev ? { ...prev, positioning: nextPos } : prev));
              }
              if (nextCopyPlan) {
                setAiResult((prev) => ({
                  text: prev?.text || "",
                  reasoning: prev?.reasoning || "",
                  model: prev?.model || "",
                  mode,
                  cached: prev?.cached,
                  copyPlan: nextCopyPlan,
                }));
              }
            },
            onDone: (res) => {
              if (flushTimer !== null) {
                clearTimeout(flushTimer);
                flushTimer = null;
              }
              pendingDelta = "";
              pendingReasoning = "";
              setAiResult({
                text: res.text,
                reasoning: res.reasoning || "",
                model: res.model,
                mode,
                cached: res.cached,
                copyPlan: res.copyPlan ?? null,
              });
              if (res.positioning) {
                setTargetProfile((prev) => (prev ? { ...prev, positioning: res.positioning! } : prev));
                setTargetBuild((prev) => (prev ? { ...prev, positioning: res.positioning! } : prev));
              }
              if ((mode === "copy" || mode === "consult" || mode === "target" || mode === "gap") && (res.text || res.copyPlan)) {
                const now = Date.now();
                const savedRec: SavedCopyPlanRecord = res.savedPlan || {
                  id: reusePlanId || `cp_${now.toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
                  mode,
                  pobHash: mode === "consult" ? "" : currentPobHash,
                  buildTitle: buildTitleNow,
                  league: activeLeagueNow,
                  model: res.model || llmConfig.model || "",
                  copyContext: mode === "copy" || mode === "consult" ? { ...copyContext } : undefined,
                  text: res.text,
                  reasoning: res.reasoning || "",
                  copyPlan: res.copyPlan ?? null,
                  itemStates:
                    reusePlanId && activeSavedPlan && activeSavedPlan.id === reusePlanId
                      ? activeSavedPlan.itemStates || {}
                      : {},
                  createdAt: now,
                  updatedAt: now,
                };
                setSavedPlans((prev) => {
                  const next = upsertCopyPlan(prev, savedRec);
                  writeLocalCopyPlans(next);
                  return next;
                });
                setActivePlanId(savedRec.id);
                writeActivePlanIdForBuild(currentPobHash, savedRec.id);
                if (!res.savedPlan && health.online) {
                  void desktopApi.saveCopyPlan(savedRec).catch(() => undefined);
                }
              }
            },
          },
          ctrl.signal
        );
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          setAiError((err as Error).message);
        }
      } finally {
        flushStreamDeltas();
        setAiLoading(false);
      }
    },
    [
      activeLeague,
      activeSavedPlan,
      applySavedPlanToView,
      copyContext,
      currentPobHash,
      gapReport,
      health.online,
      llmConfig?.enabled,
      llmConfig?.model,
      priceSnapshot?.league,
      savedPlans,
      targetBuild?.league,
      targetBuild?.pobCode,
      targetBuild?.title,
      targetDossier,
      targetProfile,
      aiResult,
    ]
  );

  const handleRefreshNinjaClick = useCallback(() => {
    void handleRefreshNinjaPrices(true);
  }, [handleRefreshNinjaPrices]);

  const handleRateTargetClick = useCallback(
    (forceRefresh?: boolean) => {
      void handleRateTarget(Boolean(forceRefresh));
    },
    [handleRateTarget]
  );

  const handleSelectSavedPlan = useCallback(
    (plan: SavedCopyPlanRecord) => {
      applySavedPlanToView(plan, true);
    },
    [applySavedPlanToView]
  );

  const handleRefreshDossierClick = useCallback(() => {
    if (targetBuild?.pobCode) void loadTargetDossier(targetBuild.pobCode, true);
  }, [targetBuild?.pobCode, loadTargetDossier]);

  const handleOpenCraftWithSlot = useCallback((rep: SlotAffixReport) => {
    setCraftSeed(rep);
    setPage("craft");
  }, []);

  const handleGoUpgrade = useCallback(() => {
    setPage("upgrade");
  }, []);

  const handleOpenAiSettings = useCallback(() => {
    setShowAiModal(true);
  }, []);

  const handleNotify = useCallback((text: string) => {
    setToast({ kind: "success", text });
  }, []);

  const pageTitleMap: Record<PageId, string> = {
    overview: "总览 & 0 成本诊断",
    upgrade: "逐件试穿 · 词缀拆解 · 预算路线",
    craft: "做装引导 · 一键直达 POE2HTC",
  };

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand-box">
          <div className="brand-title">◆ PoE2 Workbench</div>
          <div className="brand-sub">自身 BD 追赶 & 造价决策台</div>
        </div>

        <nav className="nav-list">
          <button
            type="button"
            className={`nav-item ${page === "overview" ? "active" : ""}`}
            onClick={() => setPage("overview")}
          >
            <span>📊 总览 & 0 成本</span>
            {gapReport?.myRedlines.length ? (
              <span className="badge red">{gapReport.myRedlines.length}</span>
            ) : null}
          </button>

          <button
            type="button"
            className={`nav-item ${page === "upgrade" ? "active" : ""}`}
            onClick={() => setPage("upgrade")}
          >
            <span>🛡️ 试穿 & 预算路线</span>
            {gapReport ? <span className="badge gold">{gapReport.slotUpgrades.length}</span> : null}
          </button>

          <button
            type="button"
            className={`nav-item ${page === "craft" ? "active" : ""}`}
            onClick={() => setPage("craft")}
          >
            <span>⚒️ 做装引导</span>
            <span className="badge green">HTC</span>
          </button>
        </nav>

        <div style={{ marginTop: "auto", fontSize: 11, color: "var(--muted)" }}>
          <div>当前赛季：{activeLeague}</div>
          <div style={{ marginTop: 4 }}>
            价格快照：{priceSnapshot ? `${priceSnapshot.entries?.length || Object.keys(priceSnapshot.prices).length} 条` : "暂无"}
          </div>
          <div style={{ marginTop: 4 }}>统一后端：127.0.0.1:8788</div>
        </div>
      </aside>

      <div className="content-col">
        <TopBar
          health={health}
          sseConnected={sseConnected}
          pageTitle={pageTitleMap[page]}
          leagues={leagues}
          league={activeLeague}
          onLeagueChange={setSelectedLeague}
          canRunGap={Boolean(myBuild?.pobCode && targetBuild?.pobCode)}
          analyzing={analyzing}
          aiEnabled={Boolean(llmConfig?.enabled)}
          langMode={langMode}
          onLangModeChange={handleLangModeChange}
          onRunGap={() => void handleRunGap()}
          onRefreshState={() => void syncAllFromBackend()}
          onOpenAiSettings={() => setShowAiModal(true)}
          onOpenEngineSetup={() => setShowEngineModal(true)}
        />

        {health.online && health.pob2Ready === false && (
          <div style={{ padding: "12px 22px 0" }}>
            <div className="banner info">
              <span>
                ⚙️ 尚未检测到完整的 PoB2 计算内核（{!health.pob2Found ? "缺少 PoB2 引擎" : "缺少 LuaJIT"}），请绑定本机 PoB2 目录或一键从 GitHub 拉取。
              </span>
              <button type="button" className="btn sm primary" onClick={() => setShowEngineModal(true)}>
                立即配置内核
              </button>
            </div>
          </div>
        )}

        {toast && (
          <div style={{ padding: "12px 22px 0" }}>
            <div className={`banner ${toast.kind}`}>
              <span>{toast.text}</span>
              <button type="button" className="btn sm" onClick={() => setToast(null)}>
                关闭
              </button>
            </div>
          </div>
        )}

        {page === "overview" && (
          <Overview
            t={translator}
            league={activeLeague}
            backendOnline={health.online}
            myBuild={myBuild}
            targetBuild={targetBuild}
            targetProfile={targetProfile}
            targetDossier={targetDossier}
            dossierLoading={dossierLoading}
            dossierError={dossierError}
            priceSnapshot={priceSnapshot}
            pricingBusy={pricingBusy}
            pricingStatus={pricingStatus}
            onRefreshNinjaPrices={handleRefreshNinjaClick}
            onOverrideItemPrice={handleOverrideItemPrice}
            gapReport={gapReport}
            analyzing={analyzing}
            aiEnabled={Boolean(llmConfig?.enabled)}
            aiLoading={aiLoading}
            aiResult={aiResult}
            aiError={aiError}
            aiRatingLoading={aiRatingLoading}
            aiRatingError={aiRatingError}
            onRateTarget={handleRateTargetClick}
            copyContext={copyContext}
            onCopyContextChange={handleCopyContextChange}
            currentPobHash={currentPobHash}
            savedPlans={savedPlans}
            activePlan={activeSavedPlan}
            onSelectSavedPlan={handleSelectSavedPlan}
            onDeleteSavedPlan={handleDeleteSavedPlan}
            onUpdateCopyPlanItemState={handleUpdateCopyPlanItemState}
            onSaveMyBuild={handleSaveMyBuild}
            onSaveTargetInput={handleSaveTargetInput}
            onRefreshDossier={handleRefreshDossierClick}
            onOpenCraftWithSlot={handleOpenCraftWithSlot}
            onRunGap={handleRunGap}
            onGoUpgrade={handleGoUpgrade}
            onRunAi={handleRunAi}
            onStopAi={handleStopAi}
            onOpenAiSettings={handleOpenAiSettings}
            onNotify={handleNotify}
          />
        )}

        {page === "upgrade" && (
          <Upgrade
            t={translator}
            league={activeLeague}
            gapReport={gapReport}
            priceOverrides={priceOverrides}
            onPriceOverrideChange={handlePriceOverrideChange}
            onInspectAffixes={handleInspectAffixes}
            onPlanRoadmap={handlePlanRoadmap}
            onOpenCraftWithSlot={(rep) => {
              setCraftSeed(rep);
              setPage("craft");
            }}
          />
        )}

        {page === "craft" && (
          <Craft
            t={translator}
            seedSlot={craftSeed}
            league={activeLeague}
          />
        )}
      </div>

      <AiSettingsModal
        open={showAiModal}
        config={llmConfig}
        onClose={() => setShowAiModal(false)}
        onSave={handleSaveAiConfig}
      />

      <EngineSetupModal
        open={showEngineModal}
        backendOnline={health.online}
        onClose={() => setShowEngineModal(false)}
        onEngineReady={() => void syncAllFromBackend()}
      />
    </div>
  );
};
