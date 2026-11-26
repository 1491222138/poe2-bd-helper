/**
 * 本地统一后端（127.0.0.1:8788）双向通信客户端：
 * - 心跳检测 (/health) + 自动鉴权 (/token)
 * - 扩展 -> 后端：目标 BD 定位解析 (/builds/profile)、推送目标 BD (/builds/target)、推送价格快照 (/prices)
 * - 后端/桌面 -> 扩展：SSE (/events) 实时接收市集搜货指令 (trade_open_requested) + 待办拉取/确认
 */
import type {
  BuildProfileResponse,
  CraftCompareRequest,
  CraftDecisionResponse,
  PriceSnapshot,
  TargetBuildPayload,
  TradeOpenRequest,
} from "@contracts";

export const DEFAULT_WORKBENCH_URL = "http://127.0.0.1:8788";

export interface WorkbenchHealth {
  online: boolean;
  service?: string;
  engineStarted: boolean;
  workersRunning: number;
  treeVersions: string[];
  targetBuildTitle: string;
  pricesCount: number;
  pendingTradeRequests: number;
  checkedAt: number;
}

export interface WorkbenchEventHandlers {
  onConnected?: () => void;
  onTradeOpenRequested?: (req: TradeOpenRequest) => void;
  onTargetUpdated?: (info: { title?: string; pushedAt?: number; archetype?: string }) => void;
  onPricesUpdated?: (info: { count?: number; updatedAt?: number }) => void;
  onError?: () => void;
}

export class WorkbenchClient {
  private baseUrl: string;
  private token: string | null = null;

  constructor(baseUrl = DEFAULT_WORKBENCH_URL) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  async checkHealth(): Promise<WorkbenchHealth> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2000);
    try {
      const res = await fetch(`${this.baseUrl}/health`, {
        method: "GET",
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      return {
        online: Boolean(data?.ok),
        service: data?.service,
        engineStarted: Boolean(data?.engineStarted),
        workersRunning: Number(data?.workersRunning || 0),
        treeVersions: Array.isArray(data?.treeVersions) ? data.treeVersions : [],
        targetBuildTitle: String(data?.targetBuildTitle || ""),
        pricesCount: Number(data?.pricesCount || 0),
        pendingTradeRequests: Number(data?.pendingTradeRequests || 0),
        checkedAt: Date.now(),
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
        checkedAt: Date.now(),
      };
    } finally {
      clearTimeout(timer);
    }
  }

  async ensureToken(forceRefresh = false): Promise<string> {
    if (this.token && !forceRefresh) return this.token;
    const res = await fetch(`${this.baseUrl}/token`, { method: "GET" });
    if (!res.ok) {
      throw new Error(`无法获取本地工作台鉴权令牌 (HTTP ${res.status})`);
    }
    const data = await res.json();
    if (!data?.ok || !data?.token) {
      throw new Error("本地工作台返回无效令牌");
    }
    this.token = String(data.token);
    return this.token;
  }

  private async postJson<T>(path: string, body: unknown, timeoutMs = 15000): Promise<T> {
    const attempt = async (retryAuth: boolean): Promise<T> => {
      const token = await this.ensureToken(retryAuth);
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const res = await fetch(`${this.baseUrl}${path}`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Auth-Token": token,
          },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        if (res.status === 401 && !retryAuth) {
          this.token = null;
          return attempt(true);
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data?.ok) {
          throw new Error(data?.error || `后端请求失败 (HTTP ${res.status})`);
        }
        return data as T;
      } finally {
        clearTimeout(timer);
      }
    };
    return attempt(false);
  }

  async fetchProfile(pobCode: string, runSimulation = false): Promise<BuildProfileResponse> {
    const resp = await this.postJson<{ ok: true; profile: BuildProfileResponse }>(
      "/builds/profile",
      { pobCode, runSimulation, includePrices: true, useAi: false },
      runSimulation ? 25000 : 10000
    );
    return resp.profile;
  }

  async pushTargetBuild(payload: TargetBuildPayload): Promise<TargetBuildPayload> {
    const resp = await this.postJson<{ ok: true; target: TargetBuildPayload }>("/builds/target", payload);
    return resp.target;
  }

  async pushPrices(snapshot: PriceSnapshot): Promise<{ count: number; updatedAt: number }> {
    const resp = await this.postJson<{ ok: true; prices: { count: number; updatedAt: number } }>("/prices", snapshot);
    return resp.prices;
  }

  async fetchPendingTradeRequests(): Promise<TradeOpenRequest[]> {
    try {
      const res = await fetch(`${this.baseUrl}/extension/trade-requests`, { method: "GET" });
      if (!res.ok) return [];
      const data = await res.json();
      return Array.isArray(data?.requests) ? (data.requests as TradeOpenRequest[]) : [];
    } catch {
      return [];
    }
  }

  async ackTradeRequest(id: string): Promise<boolean> {
    try {
      await this.postJson<{ ok: boolean }>("/extension/trade-requests/ack", { id });
      return true;
    } catch {
      return false;
    }
  }

  async analyzeWithLlm(pobCode: string): Promise<{ text: string; model: string }> {
    const resp = await this.postJson<{ ok: true; analysis: { text: string; model: string } }>(
      "/llm/analyze",
      { mode: "target", pobCode },
      60000
    );
    return resp.analysis;
  }

  async compareCraft(req: CraftCompareRequest): Promise<CraftDecisionResponse> {
    const resp = await this.postJson<{ ok: true; decision: CraftDecisionResponse }>(
      "/craft/compare",
      req,
      15000
    );
    return resp.decision;
  }

  subscribeEvents(handlers: WorkbenchEventHandlers): () => void {
    if (typeof EventSource === "undefined") {
      return () => undefined;
    }
    const es = new EventSource(`${this.baseUrl}/events`);

    es.addEventListener("connected", () => {
      handlers.onConnected?.();
    });
    es.addEventListener("trade_open_requested", (ev) => {
      try {
        const req = JSON.parse((ev as MessageEvent).data) as TradeOpenRequest;
        handlers.onTradeOpenRequested?.(req);
      } catch {
        // ignore malformed payload
      }
    });
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
    es.onerror = () => {
      handlers.onError?.();
    };

    return () => {
      es.close();
    };
  }
}

export const workbenchClient = new WorkbenchClient();
