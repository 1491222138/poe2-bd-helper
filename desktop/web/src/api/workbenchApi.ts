/**
 * 本地桌面端 -> 统一后端 (127.0.0.1:8788) API 与 SSE 实时推送客户端。
 */
import type {
  BudgetRoadmapReport,
  BuildAblationDossier,
  BuildPositioning,
  BuildProfileResponse,
  CopyBuildContext,
  CopyPlanAffix,
  CopyPlanItemUserState,
  CopyPlanStructured,
  CraftEssenceOption,
  CraftLibraryStatus,
  GapAnalysisReport,
  LlmAnalyzeMode,
  LlmStreamEvent,
  NinjaRatesSnapshot,
  PriceSnapshot,
  SavedCopyPlanRecord,
  SlotAffixReport,
  SlotUpgradeStep,
  TargetBuildPayload,
  TradeOpenRequest,
} from "@contracts";

function resolveDefaultBackendUrl(): string {
  if (typeof window !== "undefined") {
    const { protocol, hostname, port, origin } = window.location;
    if (
      (protocol === "http:" || protocol === "https:") &&
      (hostname === "127.0.0.1" || hostname === "localhost") &&
      port &&
      port !== "5173" &&
      port !== "4173"
    ) {
      return origin;
    }
  }
  return "http://127.0.0.1:8788";
}

export const BACKEND_URL = resolveDefaultBackendUrl();

export interface BackendHealth {
  online: boolean;
  engineStarted: boolean;
  pob2Ready?: boolean;
  pob2Found?: boolean;
  luajitFound?: boolean;
  pob2Root?: string;
  pob2Source?: string;
  pob2Layout?: string;
  workersRunning: number;
  treeVersions: string[];
  targetBuildTitle: string;
  pricesCount: number;
  pendingTradeRequests: number;
}

export interface EngineCandidate {
  source: string;
  label: string;
  valid: boolean;
  path: string;
  srcDir: string;
  layout: string;
  treeVersions: string[];
  hasData: boolean;
  hasRuntimeLua?: boolean;
}

export interface EngineInstallState {
  status: "idle" | "downloading" | "extracting" | "patching" | "verifying" | "done" | "error";
  progressPct: number;
  message: string;
  ref: string;
  downloadedBytes: number;
  totalBytes: number;
  extractedFiles: number;
  patchedFiles: number;
  error: string;
  startedAt: number;
  finishedAt: number;
}

export interface EngineStatus {
  ready: boolean;
  pob2Found: boolean;
  luajitFound: boolean;
  luajitPath: string;
  luajitHint: string;
  activeRoot: string;
  activeSrc: string;
  layout: string;
  source: string;
  treeVersions: string[];
  hasData: boolean;
  installedRef: string;
  managedDir: string;
  candidates: EngineCandidate[];
  installState: EngineInstallState;
  pinnedVersion: string;
  repoUrl: string;
}

export interface EngineTestResult {
  ok: boolean;
  bootMs: number;
  rssMb: number;
  error: string;
}

export interface MyBuildRecord {
  pobCode: string;
  title: string;
  league: string;
  updatedAt: number;
}

export interface DesktopSseHandlers {
  onConnected?: () => void;
  onTargetUpdated?: (info: { title?: string; pushedAt?: number; archetype?: string }) => void;
  onPricesUpdated?: (info: { count?: number; updatedAt?: number }) => void;
  onEngineUpdated?: (info: { pob2Root?: string; ref?: string; ready?: boolean }) => void;
  onError?: () => void;
}

export interface CraftMetaResponse {
  slot: string;
  itemClass: string;
  supportedClasses: string[];
  affixOptions: Array<{
    group: string;
    side: "prefix" | "suffix";
    sampleText: string;
    tierCount: number;
    zhText?: string;
    tradeStatId?: string;
  }>;
  essences: CraftEssenceOption[];
  rates: NinjaRatesSnapshot;
  libraryStatus?: CraftLibraryStatus;
}

export interface LlmPreset {
  label: string;
  baseUrl: string;
  model: string;
}

export interface LlmConfigPublic {
  enabled: boolean;
  provider: string;
  baseUrl: string;
  model: string;
  temperature: number;
  hasApiKey: boolean;
  apiKeyPreview: string;
  presets: Record<string, LlmPreset>;
}

export interface LlmAnalyzeResult {
  text: string;
  reasoning?: string;
  toolStatus?: string;
  model: string;
  mode?: LlmAnalyzeMode;
  cached?: boolean;
  usage?: Record<string, unknown>;
  copyPlan?: CopyPlanStructured | null;
}

export interface DesktopLlmStreamHandlers {
  onMeta?: (info: { model: string; cached?: boolean; mode?: LlmAnalyzeMode }) => void;
  onToolStatus?: (info: { stage: string; round?: number; tool?: string; summary: string }) => void;
  onReasoningDelta?: (delta: string) => void;
  onDelta?: (delta: string) => void;
  onStructured?: (
    structured: Record<string, unknown>,
    positioning?: BuildPositioning,
    copyPlan?: CopyPlanStructured | null
  ) => void;
  onDone?: (
    res: LlmAnalyzeResult & {
      structured?: Record<string, unknown> | null;
      positioning?: BuildPositioning | null;
      copyPlan?: CopyPlanStructured | null;
      savedPlan?: SavedCopyPlanRecord | null;
    }
  ) => void;
}

export interface WeightedAffixTerm {
  text: string;
  zh: string;
  statId: string;
  statText?: string;
  targetVal: number;
  contrib: number;
  weight: number;
  isResPool?: boolean;
}

export interface BuildTradeUrlRequest {
  league?: string;
  slot?: string;
  itemName?: string;
  baseType?: string;
  rarity?: string;
  action?: string;
  lockBase?: boolean;
  useWeighted?: boolean;
  pobCode?: string;
  affixes?: Array<string | Partial<CopyPlanAffix>>;
  minIlvl?: number | null;
  minSockets?: number | null;
  corrupted?: boolean | null;
  maxDivine?: number | null;
}

export interface BuildTradeUrlResponse {
  mode?: string;
  tradeUrl: string;
  tradeQuery: Record<string, unknown>;
  resolvedAffixes: CopyPlanAffix[];
  matchedStatCount: number;
  weightedTerms?: WeightedAffixTerm[];
  targetScore?: number;
  minScore?: number;
  equipmentFloors?: Record<string, number>;
  mandatorySkills?: string[];
}

export interface MarketLeagueInfo {
  name: string;
  displayName: string;
  url: string;
}

export interface TradeItemQuoteResult {
  ok: boolean;
  queryOk: boolean;
  rateLimited: boolean;
  quote?: {
    slot: string;
    itemName?: string;
    kind: "exact" | "budget" | "semi" | "base";
    divineValue: number;
    currencyLabel?: string;
    listings?: number;
    tradeUrl?: string;
    liveTradeUrl?: string;
    avgWeightMatchPct?: number | null;
    note?: string;
    samples?: string[];
  } | null;
  totalListings: number;
  tradeUrl: string;
  weightedTradeUrl?: string;
  weightedTerms?: WeightedAffixTerm[];
  targetScore?: number;
  minScore?: number;
  relaxed?: boolean;
  message: string;
  prices?: PriceSnapshot;
}

export interface MarketNinjaPriceResult {
  league: string;
  matchedCount: number;
  exaltPerDivine?: number;
  chaosPerDivine?: number;
  itemCount: number;
  prices: PriceSnapshot;
}

export interface MarketBatchPriceResult {
  league: string;
  kind: string;
  attempted: number;
  pricedCount: number;
  ninjaMatchedCount: number;
  rateLimited: boolean;
  errors: Array<{ slot: string; error: string }>;
  prices: PriceSnapshot;
}

export interface PassiveTreeLayoutNode {
  id: string;
  x: number;
  y: number;
  k: "start" | "asc_start" | "keystone" | "ascendancy" | "asc_small" | "notable" | "jewel" | "small";
  n: string;
  s: string[];
  a?: string;
  cs?: string[];
}

export interface PassiveTreeLayout {
  treeVersion: string;
  orbitRadii: number[];
  bounds: { minX: number; maxX: number; minY: number; maxY: number };
  classStarts: Record<string, string>;
  ascendancyStarts: Record<string, string>;
  ascendancyCenters: Record<string, { x: number; y: number; w: number; h: number }>;
  nodes: Record<string, PassiveTreeLayoutNode>;
  edges: Array<[string, string, number]>;
}

export interface PassiveTreePlanNodeBrief {
  id: string;
  name: string;
  kind: string;
  x: number;
  y: number;
  ascendancyName?: string | null;
  weaponSet?: 1 | 2 | null;
  stats: string[];
}

export interface PassiveTreeJewelInfo {
  nodeId: string;
  name: string;
  baseType: string;
  rarity: string;
  mods: string[];
}

export interface PassiveTreePlan {
  treeVersion: string;
  className: string;
  ascendancy: string;
  targetLevel: number;
  characterLevel: number;
  missingPoints: number;
  classStartId?: string | null;
  ascendancyStartId?: string | null;
  allocatedIds: string[];
  mainAllocatedIds: string[];
  ascendancyAllocatedIds: string[];
  sharedMainIds: string[];
  ws1Ids: string[];
  ws2Ids: string[];
  keptMainIds: string[];
  prunedIds: string[];
  prunedNotables: PassiveTreePlanNodeBrief[];
  anointedNodes: Array<{ id: string; name: string; source: string; stats: string[] }>;
  anointedIds: string[];
  jewelsByNode: Record<string, PassiveTreeJewelInfo>;
  keystones: PassiveTreePlanNodeBrief[];
  ascendancyNotables: PassiveTreePlanNodeBrief[];
  points: {
    fullMain: number;
    keptMain: number;
    prunedMain: number;
    ascendancy: number;
    ws1: number;
    ws2: number;
  };
  targetPobCode: string;
  prunedPobCode: string;
  diff?: {
    myLevel: number;
    myClassName: string;
    myAscendancy: string;
    myTotalAllocated: number;
    toAddIds: string[];
    toRefundIds: string[];
    sharedIds: string[];
    toAddNotables: PassiveTreePlanNodeBrief[];
    toRefundNotables: PassiveTreePlanNodeBrief[];
  } | null;
}

class DesktopWorkbenchApi {
  private baseUrl: string;
  private token: string | null = null;
  private treeLayoutCache = new Map<string, PassiveTreeLayout>();

  constructor(baseUrl = BACKEND_URL) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  async checkHealth(): Promise<BackendHealth> {
    try {
      const res = await fetch(`${this.baseUrl}/health`, { method: "GET" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      return {
        online: Boolean(d?.ok),
        engineStarted: Boolean(d?.engineStarted),
        pob2Ready: typeof d?.pob2Ready === "boolean" ? d.pob2Ready : undefined,
        pob2Found: typeof d?.pob2Found === "boolean" ? d.pob2Found : undefined,
        luajitFound: typeof d?.luajitFound === "boolean" ? d.luajitFound : undefined,
        pob2Root: typeof d?.pob2Root === "string" ? d.pob2Root : undefined,
        pob2Source: typeof d?.pob2Source === "string" ? d.pob2Source : undefined,
        pob2Layout: typeof d?.pob2Layout === "string" ? d.pob2Layout : undefined,
        workersRunning: Number(d?.workersRunning || 0),
        treeVersions: Array.isArray(d?.treeVersions) ? d.treeVersions : [],
        targetBuildTitle: String(d?.targetBuildTitle || ""),
        pricesCount: Number(d?.pricesCount || 0),
        pendingTradeRequests: Number(d?.pendingTradeRequests || 0),
      };
    } catch {
      this.token = null;
      return {
        online: false,
        engineStarted: false,
        workersRunning: 0,
        treeVersions: [],
        targetBuildTitle: "",
        pricesCount: 0,
        pendingTradeRequests: 0,
      };
    }
  }

  private async ensureToken(force = false): Promise<string> {
    if (this.token && !force) return this.token;
    const res = await fetch(`${this.baseUrl}/token`);
    if (!res.ok) throw new Error(`获取鉴权令牌失败 (HTTP ${res.status})`);
    const d = await res.json();
    if (!d?.ok || !d?.token) throw new Error("后端返回无效令牌");
    this.token = String(d.token);
    return this.token;
  }

  private async postJson<T>(path: string, body: unknown): Promise<T> {
    const attempt = async (retryAuth: boolean): Promise<T> => {
      const tok = await this.ensureToken(retryAuth);
      const res = await fetch(`${this.baseUrl}${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Auth-Token": tok,
        },
        body: JSON.stringify(body),
      });
      if (res.status === 401 && !retryAuth) {
        this.token = null;
        return attempt(true);
      }
      const d = await res.json().catch(() => ({}));
      if (!res.ok || !d?.ok) {
        throw new Error(d?.error || `请求失败 (HTTP ${res.status})`);
      }
      return d as T;
    };
    return attempt(false);
  }

  async getMyBuild(): Promise<MyBuildRecord | null> {
    const res = await fetch(`${this.baseUrl}/builds/mine`);
    if (!res.ok) return null;
    const d = await res.json();
    return d?.mine || null;
  }

  async saveMyBuild(pobCode: string, title = "", league = ""): Promise<MyBuildRecord> {
    const d = await this.postJson<{ ok: true; mine: MyBuildRecord }>("/builds/mine", {
      pobCode,
      title,
      league,
    });
    return d.mine;
  }

  async getTargetBuild(): Promise<TargetBuildPayload | null> {
    const res = await fetch(`${this.baseUrl}/builds/target`);
    if (!res.ok) return null;
    const d = await res.json();
    return d?.target || null;
  }

  async saveTargetBuild(payload: Partial<TargetBuildPayload> & { pobCode: string }): Promise<TargetBuildPayload> {
    const d = await this.postJson<{ ok: true; target: TargetBuildPayload }>("/builds/target", {
      ...payload,
      pushedAt: payload.pushedAt || Date.now(),
    });
    return d.target;
  }

  async getPrices(): Promise<PriceSnapshot | null> {
    const res = await fetch(`${this.baseUrl}/prices`);
    if (!res.ok) return null;
    const d = await res.json();
    if (d?.empty) return null;
    return {
      league: d.league || "Standard",
      source: d.source || "extension",
      updatedAt: d.updatedAt || Date.now(),
      prices: d.prices || {},
      entries: d.entries || [],
    };
  }

  async savePrices(snapshot: PriceSnapshot): Promise<{ count: number }> {
    const d = await this.postJson<{ ok: true; prices: { count: number } }>("/prices", snapshot);
    return d.prices;
  }

  async analyzeGap(params: {
    myPobCode?: string;
    targetPobCode?: string;
    priceOverrides?: Record<string, number>;
  }): Promise<GapAnalysisReport> {
    const d = await this.postJson<{ ok: true; report: GapAnalysisReport }>("/gap/analyze", params);
    return d.report;
  }

  async analyzeSlotAffixes(params: {
    slot: string;
    myPobCode?: string;
    targetPobCode?: string;
  }): Promise<SlotAffixReport> {
    const d = await this.postJson<{ ok: true; report: SlotAffixReport }>("/gap/slot-affixes", params);
    return d.report;
  }

  async planRoadmap(params: {
    budgetDivine: number;
    zeroCostSteps: SlotUpgradeStep[];
    slotUpgrades: SlotUpgradeStep[];
    priceOverrides?: Record<string, number>;
  }): Promise<BudgetRoadmapReport> {
    const d = await this.postJson<{ ok: true; roadmap: BudgetRoadmapReport }>("/roadmap", params);
    return d.roadmap;
  }

  async sendTradeOpenToExtension(req: {
    league: string;
    slot: string;
    kind: "exact" | "budget" | "semi" | "base";
    itemName?: string;
    label?: string;
    tradeUrl?: string;
  }): Promise<TradeOpenRequest> {
    const d = await this.postJson<{ ok: true; request: TradeOpenRequest }>("/extension/open-trade", req);
    return d.request;
  }

  async openSystemUrl(url: string): Promise<boolean> {
    const d = await this.postJson<{ ok: true; opened: boolean }>("/system/open-url", { url });
    return Boolean(d.opened);
  }

  async buildTradeUrl(req: BuildTradeUrlRequest): Promise<BuildTradeUrlResponse> {
    const d = await this.postJson<{ ok: true } & BuildTradeUrlResponse>("/trade/build-url", req);
    return {
      mode: d.mode,
      tradeUrl: d.tradeUrl,
      tradeQuery: d.tradeQuery,
      resolvedAffixes: d.resolvedAffixes || [],
      matchedStatCount: Number(d.matchedStatCount || 0),
      weightedTerms: d.weightedTerms || [],
      targetScore: d.targetScore,
      minScore: d.minScore,
      equipmentFloors: d.equipmentFloors,
      mandatorySkills: d.mandatorySkills || [],
    };
  }

  async getMarketRates(league: string, refresh = false): Promise<NinjaRatesSnapshot> {
    const qs = new URLSearchParams({ league, refresh: refresh ? "1" : "0" });
    const res = await fetch(`${this.baseUrl}/market/rates?${qs.toString()}`);
    if (!res.ok) throw new Error(`获取汇率失败 (HTTP ${res.status})`);
    const d = await res.json();
    return d.rates as NinjaRatesSnapshot;
  }

  async getMarketLeagues(refresh = false): Promise<MarketLeagueInfo[]> {
    const qs = new URLSearchParams({ refresh: refresh ? "1" : "0" });
    const res = await fetch(`${this.baseUrl}/market/leagues?${qs.toString()}`);
    if (!res.ok) return [{ name: "Standard", displayName: "Standard", url: "standard" }];
    const d = await res.json();
    return Array.isArray(d?.leagues) && d.leagues.length > 0
      ? (d.leagues as MarketLeagueInfo[])
      : [{ name: "Standard", displayName: "Standard", url: "standard" }];
  }

  async priceBuildNinja(params: {
    pobCode?: string;
    league?: string;
    forceRefresh?: boolean;
  }): Promise<MarketNinjaPriceResult> {
    const d = await this.postJson<{ ok: true } & MarketNinjaPriceResult>("/market/price-ninja", params);
    return {
      league: d.league,
      matchedCount: Number(d.matchedCount || 0),
      exaltPerDivine: d.exaltPerDivine,
      chaosPerDivine: d.chaosPerDivine,
      itemCount: Number(d.itemCount || 0),
      prices: d.prices,
    };
  }

  async priceSingleItem(params: {
    pobCode?: string;
    slot: string;
    league?: string;
    kind?: "exact" | "budget" | "semi" | "base";
  }): Promise<TradeItemQuoteResult> {
    const d = await this.postJson<TradeItemQuoteResult>("/market/price-item", params);
    return d;
  }

  async priceBuildBatch(params: {
    pobCode?: string;
    league?: string;
    kind?: "exact" | "budget" | "semi";
    slots?: string[];
  }): Promise<MarketBatchPriceResult> {
    const d = await this.postJson<{ ok: true } & MarketBatchPriceResult>("/market/price-batch", params);
    return {
      league: d.league,
      kind: d.kind,
      attempted: Number(d.attempted || 0),
      pricedCount: Number(d.pricedCount || 0),
      ninjaMatchedCount: Number(d.ninjaMatchedCount || 0),
      rateLimited: Boolean(d.rateLimited),
      errors: Array.isArray(d.errors) ? d.errors : [],
      prices: d.prices,
    };
  }

  async overrideItemPrice(params: {
    slot: string;
    divineValue: number;
    kind?: "exact" | "budget" | "semi" | "base";
    league?: string;
    itemName?: string;
  }): Promise<PriceSnapshot> {
    const d = await this.postJson<{ ok: true; prices: PriceSnapshot }>("/prices/override", params);
    return d.prices;
  }

  async getCraftMeta(params: {
    slot: string;
    ilvl: number;
    baseType?: string;
    league?: string;
  }): Promise<CraftMetaResponse> {
    const qs = new URLSearchParams({
      slot: params.slot,
      ilvl: String(params.ilvl),
      baseType: params.baseType || "",
      league: params.league || "Standard",
    });
    const res = await fetch(`${this.baseUrl}/craft/meta?${qs.toString()}`);
    if (!res.ok) throw new Error(`获取槽位词缀池失败 (HTTP ${res.status})`);
    const d = await res.json();
    return d as CraftMetaResponse;
  }

  async getBuildProfile(
    pobCode: string,
    runSimulation = true,
    runAblation = runSimulation
  ): Promise<BuildProfileResponse> {
    const d = await this.postJson<{ ok: true; profile: BuildProfileResponse }>("/builds/profile", {
      pobCode,
      runSimulation,
      runAblation,
      includePrices: true,
      useAi: false,
    });
    return d.profile;
  }

  async rateTargetWithAi(params: {
    pobCode?: string;
    profile?: BuildProfileResponse;
    ablationDossier?: BuildAblationDossier | null;
    forceRefresh?: boolean;
  }): Promise<{
    positioning: BuildPositioning;
    simulation?: BuildProfileResponse["simulation"];
    engineFacts?: BuildProfileResponse["engineFacts"];
    rating: {
      model: string;
      cached: boolean;
      engineMetricsSummary?: string;
    };
  }> {
    const d = await this.postJson<{
      ok: true;
      positioning: BuildPositioning;
      simulation?: BuildProfileResponse["simulation"];
      engineFacts?: BuildProfileResponse["engineFacts"];
      rating: {
        model: string;
        cached: boolean;
        engineMetricsSummary?: string;
      };
    }>("/llm/rate", params);
    return {
      positioning: d.positioning,
      simulation: d.simulation,
      engineFacts: d.engineFacts,
      rating: d.rating,
    };
  }

  async getAblationDossier(params: {
    pobCode: string;
    league?: string;
    priceOverrides?: Record<string, number>;
    forceRefresh?: boolean;
  }): Promise<BuildAblationDossier> {
    const d = await this.postJson<{ ok: true; dossier: BuildAblationDossier }>("/builds/ablation-dossier", {
      pobCode: params.pobCode,
      league: params.league,
      priceOverrides: params.priceOverrides,
      includePrices: true,
      forceRefresh: Boolean(params.forceRefresh),
    });
    return d.dossier;
  }

  async getTreeLayout(version?: string): Promise<PassiveTreeLayout> {
    const cacheKey = (version || "latest").trim() || "latest";
    const hit = this.treeLayoutCache.get(cacheKey);
    if (hit) return hit;
    const qs = version ? `?version=${encodeURIComponent(version)}` : "";
    const res = await fetch(`${this.baseUrl}/tree/layout${qs}`);
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d?.error || `加载天赋树几何布局失败 (HTTP ${res.status})`);
    }
    const d = await res.json();
    const layout = d.layout as PassiveTreeLayout;
    this.treeLayoutCache.set(cacheKey, layout);
    if (layout?.treeVersion) {
      this.treeLayoutCache.set(layout.treeVersion, layout);
    }
    return layout;
  }

  async getPassiveTreePlan(params: {
    targetPobCode?: string;
    myPobCode?: string;
    characterLevel?: number;
    cutNotables?: string[];
  }): Promise<PassiveTreePlan> {
    const d = await this.postJson<{ ok: true; plan: PassiveTreePlan }>("/builds/passive-tree", params);
    return d.plan;
  }

  async getLlmConfig(): Promise<LlmConfigPublic | null> {
    try {
      const res = await fetch(`${this.baseUrl}/llm/config`);
      if (!res.ok) return null;
      const d = await res.json();
      return (d?.config as LlmConfigPublic) || null;
    } catch {
      return null;
    }
  }

  async saveLlmConfig(payload: {
    enabled: boolean;
    provider: string;
    baseUrl: string;
    apiKey?: string;
    model: string;
    temperature?: number;
    clearApiKey?: boolean;
    test?: boolean;
  }): Promise<{ config: LlmConfigPublic; testResult?: LlmAnalyzeResult | null }> {
    const d = await this.postJson<{ ok: true; config: LlmConfigPublic; testResult?: LlmAnalyzeResult | null }>(
      "/llm/config",
      payload
    );
    return { config: d.config, testResult: d.testResult };
  }

  async analyzeWithLlm(params: {
    mode: LlmAnalyzeMode;
    profile?: BuildProfileResponse;
    gapReport?: GapAnalysisReport;
    copyContext?: CopyBuildContext;
    essenceSummary?: string;
  }): Promise<LlmAnalyzeResult> {
    const d = await this.postJson<{ ok: true; analysis: LlmAnalyzeResult }>("/llm/analyze", params);
    return d.analysis;
  }

  async listCopyPlans(params?: { pobHash?: string; mode?: string }): Promise<SavedCopyPlanRecord[]> {
    const qs = new URLSearchParams();
    if (params?.pobHash) qs.set("pobHash", params.pobHash);
    if (params?.mode) qs.set("mode", params.mode);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    const res = await fetch(`${this.baseUrl}/copy-plans${suffix}`);
    if (!res.ok) return [];
    const d = await res.json().catch(() => ({}));
    return Array.isArray(d?.plans) ? (d.plans as SavedCopyPlanRecord[]) : [];
  }

  async saveCopyPlan(payload: Partial<SavedCopyPlanRecord> & { pobCode?: string }): Promise<SavedCopyPlanRecord> {
    const d = await this.postJson<{ ok: true; plan: SavedCopyPlanRecord }>("/copy-plans", payload);
    return d.plan;
  }

  async updateCopyPlanItemState(
    planId: string,
    itemKey: string,
    patch: Partial<CopyPlanItemUserState>
  ): Promise<SavedCopyPlanRecord> {
    const d = await this.postJson<{ ok: true; plan: SavedCopyPlanRecord }>("/copy-plans/item-state", {
      planId,
      itemKey,
      patch,
    });
    return d.plan;
  }

  async deleteCopyPlan(id: string): Promise<boolean> {
    const d = await this.postJson<{ ok: true; deleted: boolean }>("/copy-plans/delete", { id });
    return Boolean(d.deleted);
  }

  async streamAnalyzeWithLlm(
    params: {
      mode: LlmAnalyzeMode;
      league?: string;
      pobCode?: string;
      buildTitle?: string;
      planId?: string;
      useCache?: boolean;
      profile?: BuildProfileResponse;
      ablationDossier?: BuildAblationDossier | null;
      gapReport?: GapAnalysisReport;
      copyContext?: CopyBuildContext;
      essenceSummary?: string;
    },
    handlers: DesktopLlmStreamHandlers,
    signal?: AbortSignal
  ): Promise<void> {
    const attempt = async (retryAuth: boolean): Promise<Response> => {
      const tok = await this.ensureToken(retryAuth);
      const res = await fetch(`${this.baseUrl}/llm/analyze/stream`, {
        method: "POST",
        signal,
        headers: {
          "Content-Type": "application/json",
          "X-Auth-Token": tok,
        },
        body: JSON.stringify(params),
      });
      if (res.status === 401 && !retryAuth) {
        this.token = null;
        return attempt(true);
      }
      return res;
    };

    let res: Response;
    try {
      res = await attempt(false);
    } catch (err) {
      if ((err as Error)?.name === "AbortError") throw err;
      throw new Error(`连接本地 AI 流式服务中断（${(err as Error)?.message || "网络超时"}），请确认后端运行中后重试。`);
    }
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d?.error || `流式请求失败 (HTTP ${res.status})`);
    }
    if (!res.body) throw new Error("后端流式响应体为空");

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buf = "";

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const frames = buf.split("\n\n");
        buf = frames.pop() || "";
        for (const frame of frames) {
          const dataLines = frame
            .split("\n")
            .filter((l) => l.startsWith("data:"))
            .map((l) => l.slice(5).trim());
          if (dataLines.length === 0) continue;
          let ev: LlmStreamEvent;
          try {
            ev = JSON.parse(dataLines.join("\n")) as LlmStreamEvent;
          } catch {
            continue;
          }
          if (ev.type === "meta") handlers.onMeta?.({ model: ev.model, cached: ev.cached, mode: ev.mode });
          else if (ev.type === "tool_status") handlers.onToolStatus?.({ stage: ev.stage, round: ev.round, tool: ev.tool, summary: ev.summary });
          else if (ev.type === "reasoning") handlers.onReasoningDelta?.(ev.delta);
          else if (ev.type === "delta") handlers.onDelta?.(ev.delta);
          else if (ev.type === "structured") handlers.onStructured?.(ev.structured, ev.positioning, ev.copyPlan);
          else if (ev.type === "error") throw new Error(ev.error || "流式分析出错");
          else if (ev.type === "done") {
            handlers.onDone?.({
              text: ev.text,
              reasoning: ev.reasoning || "",
              model: ev.model,
              mode: params.mode,
              cached: ev.cached,
              structured: ev.structured,
              positioning: ev.positioning,
              copyPlan: ev.copyPlan,
              savedPlan: ev.savedPlan ?? null,
            });
          }
        }
      }
    } catch (err) {
      if ((err as Error)?.name === "AbortError") throw err;
      const msg = (err as Error)?.message || "流式连接中断";
      if (/load failed|failed to fetch|networkerror|aborted/i.test(msg)) {
        throw new Error(`流式连接被中断（${msg}），可能是上游大模型响应较慢或网络波动，请直接重试。`);
      }
      throw err;
    }
  }

  async getEngineStatus(): Promise<EngineStatus> {
    const res = await fetch(`${this.baseUrl}/engine/status`, { method: "GET" });
    const d = await res.json().catch(() => ({}));
    if (!res.ok || !d?.ok) {
      throw new Error(d?.error || `HTTP ${res.status}`);
    }
    return d.engine as EngineStatus;
  }

  async detectEngine(): Promise<EngineStatus> {
    const d = await this.postJson<{ ok: boolean; engine: EngineStatus }>("/engine/detect", {});
    return d.engine;
  }

  async useLocalPob2Path(path: string): Promise<EngineStatus> {
    const d = await this.postJson<{ ok: boolean; engine: EngineStatus }>("/engine/use-path", { path });
    return d.engine;
  }

  async startEngineInstall(params?: { ref?: string; mirrorPrefix?: string }): Promise<EngineInstallState> {
    const d = await this.postJson<{ ok: boolean; installState: EngineInstallState }>("/engine/install", params || {});
    return d.installState;
  }

  async installLuajit(): Promise<{
    installed: boolean;
    alreadyPresent: boolean;
    luajitPath: string;
    message: string;
    engine: EngineStatus;
  }> {
    const d = await this.postJson<{
      ok: boolean;
      installed: boolean;
      alreadyPresent: boolean;
      luajitPath: string;
      message: string;
      engine: EngineStatus;
    }>("/engine/install-luajit", {});
    return {
      installed: Boolean(d.installed),
      alreadyPresent: Boolean(d.alreadyPresent),
      luajitPath: String(d.luajitPath || ""),
      message: String(d.message || ""),
      engine: d.engine,
    };
  }

  async testEngineLive(): Promise<EngineTestResult> {
    const d = await this.postJson<{ ok: boolean; test: EngineTestResult }>("/engine/test", {});
    return d.test;
  }

  subscribeEvents(handlers: DesktopSseHandlers): () => void {
    if (typeof EventSource === "undefined") return () => undefined;
    const es = new EventSource(`${this.baseUrl}/events`);
    es.addEventListener("connected", () => handlers.onConnected?.());
    es.addEventListener("target_updated", (ev) => {
      try {
        handlers.onTargetUpdated?.(JSON.parse((ev as MessageEvent).data));
      } catch {
        // ignore
      }
    });
    es.addEventListener("prices_updated", (ev) => {
      try {
        handlers.onPricesUpdated?.(JSON.parse((ev as MessageEvent).data));
      } catch {
        // ignore
      }
    });
    es.addEventListener("engine_updated", (ev) => {
      try {
        handlers.onEngineUpdated?.(JSON.parse((ev as MessageEvent).data));
      } catch {
        // ignore
      }
    });
    es.onerror = () => handlers.onError?.();
    return () => es.close();
  }
}

export const desktopApi = new DesktopWorkbenchApi();
