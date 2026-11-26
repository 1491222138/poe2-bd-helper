import React, { useMemo, useState } from "react";
import { MarkdownView } from "./MarkdownView";
import {
  buildHtcLinkFromClipboardTexts,
  buildHtcLinkFromParsedItem,
  type BuildProfileResponse,
  type CopyBuildContext,
  type CopyPlanItem,
  type CopyPlanStructured,
  type CraftDecisionResponse,
  type CraftMinTierMode,
  type ParsedItem,
  type PriceKind,
} from "@contracts";
import { workbenchClient } from "../../bridge/workbenchClient";
import type { AttachedUniquePrice, LeaguePriceIndex } from "../../market/ninjaPrice";
import {
  buildDirectTradeUrlForItem,
  buildFallbackTradeUrl,
  buildTradeQuery,
  ensureStatsIndex,
  matchItemModsWithIndex,
  priceItemThreeWay,
  priceSingleItem,
  type ItemTradeQuote,
} from "../../market/tradeBuilder";
import {
  buildOfflineThreeWayDecision,
  evaluateLocalCopyPlan,
  type EvaluatedGearCopyItem,
} from "../../parsers/budgetPlannerLocal";

interface CopyBuildBarProps {
  profile: BuildProfileResponse | null;
  league: string;
  backendOnline: boolean;
  priceIndex: LeaguePriceIndex | null;
  context: CopyBuildContext;
  onChange: (next: CopyBuildContext) => void;
  uniquePrices: AttachedUniquePrice[];
  tradeQuotes: Record<string, ItemTradeQuote>;
  onQuoteUpdate: (quote: ItemTradeQuote) => void;
  onOpenExternalUrl: (url: string) => void;
  streaming: boolean;
  copyText: string;
  copyReasoning: string;
  copyError: string;
  copyPlan?: CopyPlanStructured | null;
  purchasedKeys?: Record<string, boolean>;
  onTogglePurchased?: (key: string) => void;
  onGenerateCopyPlan: () => void;
  onStopStream: () => void;
  onConsultWithoutPob: () => void;
}

const BUDGET_STOPS = [1, 2, 5, 10, 15, 25, 30, 50, 80, 120, 200, 300];
const BUDGET_PRESETS: Array<{ label: string; value: number }> = [
  { label: "开荒 2D", value: 2 },
  { label: "中期 10D", value: 10 },
  { label: "成型 30D", value: 30 },
  { label: "高配 80D", value: 80 },
  { label: "毕业 200D+", value: 200 },
];

const TIER_MODES: Array<{ value: CraftMinTierMode; label: string; hint: string }> = [
  { value: "any", label: "任意 T 级", hint: "只看词缀组命中（偏宽松）" },
  { value: "mid", label: "中配 T3+", hint: "要求中高 T 级数值（实战推荐）" },
  { value: "high", label: "高配 T1-T2", hint: "严苛追顶级词缀（适合后期做装）" },
];

function budgetToSliderIdx(d: number): number {
  let best = 0;
  let bestDiff = Infinity;
  for (let i = 0; i < BUDGET_STOPS.length; i++) {
    const diff = Math.abs(BUDGET_STOPS[i] - d);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = i;
    }
  }
  return best;
}

const STATUS_BADGE: Record<EvaluatedGearCopyItem["status"], string> = {
  owned: "muted",
  "level-blocked": "red",
  "mandatory-buy": "gold",
  "within-budget": "green",
  "over-budget-sub": "unique",
};

const VERDICT_STYLE: Record<
  CraftDecisionResponse["recommended"],
  { badge: string; cls: string }
> = {
  buy_finished: { badge: "🟢 直接买成品", cls: "green" },
  craft_semi: { badge: "🟡 买半成品加工", cls: "gold" },
  craft_raw: { badge: "🟣 买白底自做", cls: "purple" },
  inconclusive: { badge: "⚪ 待补参考价", cls: "blue" },
};

function fmtD(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  if (v >= 10) return `${Math.round(v * 10) / 10} D`;
  return `${Math.round(v * 100) / 100} D`;
}

export const CopyBuildBar: React.FC<CopyBuildBarProps> = ({
  profile,
  league,
  backendOnline,
  priceIndex,
  context,
  onChange,
  uniquePrices,
  tradeQuotes,
  onQuoteUpdate,
  onOpenExternalUrl,
  streaming,
  copyText,
  copyReasoning,
  copyError,
  copyPlan,
  purchasedKeys,
  onTogglePurchased,
  onGenerateCopyPlan,
  onStopStream,
  onConsultWithoutPob,
}) => {
  const level = context.characterLevel ?? 90;
  const budget = context.budgetDivine ?? 15;
  const owned = context.ownedItems || [];

  const [minTierMode, setMinTierMode] = useState<CraftMinTierMode>("mid");
  const [showAllItems, setShowAllItems] = useState(false);
  const [busyBySlot, setBusyBySlot] = useState<Record<string, string>>({});
  const [errorBySlot, setErrorBySlot] = useState<Record<string, string>>({});
  const [decisionBySlot, setDecisionBySlot] = useState<Record<string, CraftDecisionResponse>>({});
  const [expandedRecipeSlot, setExpandedRecipeSlot] = useState<Record<string, boolean>>({});
  const [htcPasteText, setHtcPasteText] = useState<string>("");

  const htcPasteLink = useMemo(
    () =>
      htcPasteText.trim()
        ? buildHtcLinkFromClipboardTexts({
            mode: "have_item",
            existingText: htcPasteText,
            minTierMode,
          })
        : null,
    [htcPasteText, minTierMode]
  );

  const localPlan = useMemo(() => {
    if (!profile) return null;
    return evaluateLocalCopyPlan(profile, context, uniquePrices, tradeQuotes);
  }, [profile, context, uniquePrices, tradeQuotes]);

  const toggleableUniques = useMemo(() => {
    if (!profile) return [];
    const seen = new Set<string>();
    const names: string[] = [];
    for (const it of [...profile.build.equippedItems, ...profile.build.jewels, ...profile.build.flasksAndCharms]) {
      if (it.rarity.toUpperCase() === "UNIQUE" && it.name && !seen.has(it.name.toLowerCase())) {
        seen.add(it.name.toLowerCase());
        names.push(it.name);
      }
    }
    return names.slice(0, 10);
  }, [profile]);

  const toggleOwned = (name: string) => {
    const exists = owned.some((x) => x.toLowerCase() === name.toLowerCase());
    const next = exists ? owned.filter((x) => x.toLowerCase() !== name.toLowerCase()) : [...owned, name];
    onChange({ ...context, ownedItems: next });
  };

  async function handleQuickOpenTrade(item: ParsedItem, kind: PriceKind) {
    setBusyBySlot((prev) => ({ ...prev, [item.slot]: "打开市集..." }));
    setErrorBySlot((prev) => ({ ...prev, [item.slot]: "" }));
    try {
      const url = await buildDirectTradeUrlForItem(item, league, kind, { build: profile?.build });
      onOpenExternalUrl(url);
    } catch (err) {
      setErrorBySlot((prev) => ({ ...prev, [item.slot]: (err as Error).message }));
    } finally {
      setBusyBySlot((prev) => ({ ...prev, [item.slot]: "" }));
    }
  }

  async function handleQuickPriceUnique(item: ParsedItem) {
    setBusyBySlot((prev) => ({ ...prev, [item.slot]: "查实价..." }));
    setErrorBySlot((prev) => ({ ...prev, [item.slot]: "" }));
    try {
      const res = await priceSingleItem(item, league, "exact", priceIndex, undefined, profile?.build);
      if (res.rateLimited) {
        setErrorBySlot((prev) => ({ ...prev, [item.slot]: "触发市集 429 限流，已熔断保护，请稍后再试。" }));
      } else if (res.quote) {
        onQuoteUpdate(res.quote);
      } else if (!res.ok) {
        setErrorBySlot((prev) => ({ ...prev, [item.slot]: res.error || "查价失败" }));
      } else {
        setErrorBySlot((prev) => ({ ...prev, [item.slot]: "当前条件 0 件在售，可点直达市集手动放宽。" }));
      }
    } catch (err) {
      setErrorBySlot((prev) => ({ ...prev, [item.slot]: (err as Error).message }));
    } finally {
      setBusyBySlot((prev) => ({ ...prev, [item.slot]: "" }));
    }
  }

  async function handleThreeWayCompare(item: ParsedItem) {
    setBusyBySlot((prev) => ({ ...prev, [item.slot]: "1/3 查成品价..." }));
    setErrorBySlot((prev) => ({ ...prev, [item.slot]: "" }));
    try {
      const market = await priceItemThreeWay(
        item,
        league,
        priceIndex,
        (msg) => {
          setBusyBySlot((prev) => ({ ...prev, [item.slot]: msg }));
        },
        profile?.build
      );
      if (market.finishedQuote) onQuoteUpdate(market.finishedQuote);
      if (market.semiQuote) onQuoteUpdate(market.semiQuote);
      if (market.baseQuote) onQuoteUpdate(market.baseQuote);
      if (market.error) {
        setErrorBySlot((prev) => ({ ...prev, [item.slot]: market.error || "" }));
      }

      let decision: CraftDecisionResponse;
      if (backendOnline) {
        setBusyBySlot((prev) => ({ ...prev, [item.slot]: "HTC 引擎计算买/做最优解..." }));
        try {
          decision = await workbenchClient.compareCraft({
            slot: item.slot,
            baseType: item.baseType || undefined,
            league,
            ilvl: Math.max(82, item.levelReq || 82),
            wantAffixes: market.wantAffixes.length > 0 ? market.wantAffixes : item.mods.slice(0, 4),
            existingAffixes: market.existingAffixes.length > 0 ? market.existingAffixes : item.mods.slice(0, 2),
            finishedBuyPrice: market.finishedQuote?.divineValue,
            semiBasePrice: market.semiQuote?.divineValue,
            whiteBasePrice: market.baseQuote?.divineValue,
            minTierMode,
          });
          // 用实际带精确数值下限的官方市集链接覆盖默认链接，确保直达更准
          decision = {
            ...decision,
            finishedTradeUrl: market.finishedUrl || decision.finishedTradeUrl,
            semiTradeUrl: market.semiUrl || decision.semiTradeUrl,
            whiteBaseTradeUrl: market.baseUrl || decision.whiteBaseTradeUrl,
            bestAction: decision.bestAction
              ? {
                  ...decision.bestAction,
                  tradeUrl:
                    decision.recommended === "craft_semi"
                      ? market.semiUrl || decision.bestAction.tradeUrl
                      : decision.recommended === "craft_raw"
                      ? market.baseUrl || decision.bestAction.tradeUrl
                      : market.finishedUrl || decision.bestAction.tradeUrl,
                }
              : undefined,
          };
        } catch {
          decision = buildOfflineThreeWayDecision(item, market, league);
        }
      } else {
        decision = buildOfflineThreeWayDecision(item, market, league);
      }

      setDecisionBySlot((prev) => ({ ...prev, [item.slot]: decision }));
      if (decision.recommended === "craft_semi" || decision.recommended === "craft_raw") {
        setExpandedRecipeSlot((prev) => ({ ...prev, [item.slot]: true }));
      }
    } catch (err) {
      setErrorBySlot((prev) => ({ ...prev, [item.slot]: (err as Error).message }));
    } finally {
      setBusyBySlot((prev) => ({ ...prev, [item.slot]: "" }));
    }
  }

  const actionableItems = useMemo(() => {
    if (!localPlan) return [];
    const list = localPlan.items.filter((x) => x.status !== "owned" || x.tier === "mandatory");
    return showAllItems ? list : list.slice(0, 10);
  }, [localPlan, showAllItems]);

  async function handleOpenAiCopyItemTrade(item: CopyPlanItem) {
    if (item.tradeUrl) {
      onOpenExternalUrl(item.tradeUrl);
      return;
    }
    const fakeParsed: ParsedItem = {
      slot: item.slot,
      itemId: "ai",
      rarity: item.rarity || "RARE",
      name: item.itemName,
      baseType: item.baseType || "",
      corrupted: item.corrupted === true,
      socketCount: item.minSockets || 0,
      socketedRunes: [],
      mods: (item.selectedAffixes || []).filter((a) => a.required !== false).map((a) => a.text).filter(Boolean),
      itemLevel: item.minIlvl || undefined,
      rawText: "",
    };
    const kind: PriceKind =
      item.action === "craft_semi"
        ? "semi"
        : item.action === "buy_base"
        ? "base"
        : item.action === "buy_budget"
        ? "budget"
        : "exact";
    try {
      const idx = await ensureStatsIndex();
      const rows = matchItemModsWithIndex(fakeParsed, idx, kind, profile?.build);
      const payload = buildTradeQuery(fakeParsed, rows, {
        kind,
        matchBase: item.lockBase,
        minIlvl: item.minIlvl || undefined,
        minSockets: item.minSockets || undefined,
        filterSockets: Boolean(item.minSockets && item.minSockets >= 2),
        build: profile?.build,
      });
      onOpenExternalUrl(buildFallbackTradeUrl(league, payload));
    } catch {
      onOpenExternalUrl(
        `https://www.pathofexile.com/trade2/search/poe2/${encodeURIComponent(league || "Standard")}`
      );
    }
  }

  return (
    <section className="card gold">
      <div className="card-title">
        <span>🛒 现在买什么 · 怎么做（一键直达市集 &amp; HTC 做装比价）</span>
        <span className={`badge ${backendOnline ? "green" : "blue"}`}>
          {backendOnline ? "HTC 精确引擎在线" : "纯插件模式"}
        </span>
      </div>

      {/* 等级 + 预算输入 */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 8 }}>
        <div>
          <div className="muted" style={{ marginBottom: 3 }}>
            我的等级：<strong style={{ color: "#fff" }}>Lv.{level}</strong>
          </div>
          <div className="row" style={{ gap: 6 }}>
            <input
              type="range"
              min={1}
              max={100}
              value={level}
              onChange={(e) => onChange({ ...context, characterLevel: Number(e.target.value) })}
              style={{ flex: 1 }}
            />
            <input
              type="number"
              min={1}
              max={100}
              value={level}
              onChange={(e) =>
                onChange({ ...context, characterLevel: Math.max(1, Math.min(100, Number(e.target.value) || 1)) })
              }
              className="input"
              style={{ width: 52, padding: "2px 6px" }}
            />
          </div>
        </div>

        <div>
          <div className="muted" style={{ marginBottom: 3 }}>
            我的预算：<strong style={{ color: "var(--gold-strong)" }}>{budget} D</strong>
          </div>
          <div className="row" style={{ gap: 6 }}>
            <input
              type="range"
              min={0}
              max={BUDGET_STOPS.length - 1}
              value={budgetToSliderIdx(budget)}
              onChange={(e) => onChange({ ...context, budgetDivine: BUDGET_STOPS[Number(e.target.value)] || 10 })}
              style={{ flex: 1 }}
            />
            <input
              type="number"
              min={0}
              step={0.5}
              value={budget}
              onChange={(e) => onChange({ ...context, budgetDivine: Math.max(0, Number(e.target.value) || 0) })}
              className="input"
              style={{ width: 58, padding: "2px 6px" }}
            />
          </div>
        </div>
      </div>

      {/* 快捷预算 + 做装 T 级档位 */}
      <div className="row between" style={{ marginBottom: 8, gap: 6 }}>
        <div className="chips">
          {BUDGET_PRESETS.map((p) => (
            <button
              key={p.value}
              type="button"
              className={`btn sm ${budget === p.value ? "primary" : ""}`}
              style={{ padding: "2px 7px", fontSize: 11 }}
              onClick={() => onChange({ ...context, budgetDivine: p.value })}
            >
              {p.label}
            </button>
          ))}
        </div>
        {profile && (
          <div className="row" style={{ gap: 3 }}>
            {TIER_MODES.map((tm) => (
              <button
                key={tm.value}
                type="button"
                title={tm.hint}
                className={`btn sm ${minTierMode === tm.value ? "primary" : ""}`}
                style={{ padding: "2px 6px", fontSize: 10 }}
                onClick={() => setMinTierMode(tm.value)}
              >
                {tm.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 无目标 PoB 码时的自由咨询模式 */}
      {!profile && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 8 }}>
          <div className="muted">还没有目标 PoB 码？直接填意向升华/玩法，按你的等级与预算生成起步思路：</div>
          <div className="row" style={{ gap: 6 }}>
            <input
              type="text"
              className="input"
              placeholder="意向职业/升华（如：古灵使徒 / 先知）"
              value={context.freeformQuery?.ascendancy || ""}
              onChange={(e) =>
                onChange({
                  ...context,
                  freeformQuery: { ...context.freeformQuery, ascendancy: e.target.value },
                })
              }
            />
            <input
              type="text"
              className="input"
              placeholder="核心技能/玩法（如：旋龙 / 冰电捷）"
              value={context.freeformQuery?.skillIdea || ""}
              onChange={(e) =>
                onChange({
                  ...context,
                  freeformQuery: { ...context.freeformQuery, skillIdea: e.target.value },
                })
              }
            />
          </div>
        </div>
      )}

      {/* 有目标 BD 时：勾选已有核心暗金 */}
      {profile && toggleableUniques.length > 0 && (
        <details style={{ marginBottom: 8 }}>
          <summary className="muted" style={{ cursor: "pointer" }}>
            ✓ 勾选已有核心件（已勾 {owned.length} 件，自动免重复计价）
          </summary>
          <div className="chips" style={{ marginTop: 6 }}>
            {toggleableUniques.map((name) => {
              const active = owned.some((x) => x.toLowerCase() === name.toLowerCase());
              return (
                <button
                  key={name}
                  type="button"
                  className={`badge ${active ? "green" : ""}`}
                  style={{ cursor: "pointer", border: "1px solid rgba(255,255,255,0.12)" }}
                  onClick={() => toggleOwned(name)}
                >
                  {active ? "✓ " : "+ "}
                  {name}
                </button>
              );
            })}
          </div>
        </details>
      )}

      {/* 核心：可执行购物 & 做装决策卡列表 */}
      {localPlan && (
        <div style={{ marginBottom: 10 }}>
          <div className="row between" style={{ marginBottom: 6 }}>
            <span className="muted">
              覆盖 ~<strong style={{ color: "var(--gold-strong)" }}>{localPlan.coveredCostDivine}D</strong> · 剩{" "}
              {localPlan.remainingDivine}D
              {localPlan.missingPassivePoints > 0 ? ` · 少 ${localPlan.missingPassivePoints} 点天赋` : ""}
            </span>
            <div className="row" style={{ gap: 4 }}>
              {localPlan.levelBlockedCount > 0 && (
                <span className="badge red">等级未到 {localPlan.levelBlockedCount}</span>
              )}
              {localPlan.overBudgetCount > 0 && (
                <span className="badge unique">需平替 {localPlan.overBudgetCount}</span>
              )}
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
            {actionableItems.map((entry) => {
              const it = entry.rawItem;
              const isUnique = entry.rarity.toUpperCase() === "UNIQUE";
              const busyMsg = busyBySlot[entry.slot];
              const slotErr = errorBySlot[entry.slot];
              const decision = decisionBySlot[entry.slot];
              const showRecipe = Boolean(expandedRecipeSlot[entry.slot]);
              const liveQuote =
                tradeQuotes[`${entry.slot}:exact`] ||
                tradeQuotes[`${entry.slot}:budget`] ||
                Object.values(tradeQuotes).find((q) => q.slot === entry.slot);
              const displayPrice =
                liveQuote?.formatted ||
                (entry.estimatedDivine != null
                  ? `~${entry.estimatedDivine}D${entry.priceSource === "estimate" ? "(估)" : ""}`
                  : "未核价");

              const semiExp =
                decision?.policySummary?.semiExpectedDivine ??
                decision?.estimates?.find((e) => e.strategy === "essence_lock" || e.strategy === "htc_policy")
                  ?.expectedCostDivine ??
                null;
              const rawExp =
                decision?.policySummary?.rawExpectedDivine ??
                decision?.estimates?.find((e) => e.strategy === "htc_raw_policy")?.expectedCostDivine ??
                null;

              return (
                <div key={`${entry.slot}-${entry.name}`} className="gear-card action-card">
                  {/* 第一行：优先级 + 装备名 + 实时参考价 */}
                  <div className="row between">
                    <div className="row" style={{ gap: 5, minWidth: 0 }}>
                      <span className={`badge ${STATUS_BADGE[entry.status]}`}>{entry.statusLabel}</span>
                      <strong style={{ fontSize: 12.5, color: "#fff" }}>{entry.name}</strong>
                      <span className="muted">{entry.slot}</span>
                    </div>
                    <span className={`badge ${liveQuote ? "green" : "gold"}`}>{displayPrice}</span>
                  </div>

                  {/* 第二行：简短一句话定位 + 深度天赋/符文插槽联动芯片 */}
                  <div className="muted" style={{ fontSize: 11 }}>
                    {entry.reason}
                    {entry.baseType && entry.baseType !== entry.name ? ` · 基底 ${entry.baseType}` : ""}
                  </div>
                  {entry.deepAnalysis && (
                    <div style={{ marginTop: 2 }}>
                      <div className="chips" style={{ marginBottom: 2 }}>
                        {entry.deepAnalysis.isSpeedWeapon && <span className="badge blue">⚡ 攻速工具组</span>}
                        {entry.deepAnalysis.requireBaseType && (
                          <span className="badge gold" title={entry.deepAnalysis.baseTypeReason}>
                            🔒 必保基底
                          </span>
                        )}
                        {entry.deepAnalysis.defenseFloor && (
                          <span className="badge green" title={entry.deepAnalysis.defenseFloor.reason}>
                            🛡️ 总{entry.deepAnalysis.defenseFloor.stat.toUpperCase()}≥{entry.deepAnalysis.defenseFloor.budgetMin}
                          </span>
                        )}
                        {entry.deepAnalysis.passiveSynergies.slice(0, 2).map((ps) => (
                          <span key={ps} className="badge purple">
                            🌳 {ps}
                          </span>
                        ))}
                        {(entry.deepAnalysis.socketAnalysis.exceedsCraftableSockets ||
                          entry.deepAnalysis.socketAnalysis.hasSocketEffectMod) && (
                          <span className="badge red" title={entry.deepAnalysis.socketAnalysis.advice}>
                            💎 需≥{entry.deepAnalysis.socketAnalysis.socketCount}符文孔
                          </span>
                        )}
                        {entry.deepAnalysis.socketAnalysis.runeUnlockedPools.map((pool) => (
                          <span key={pool} className="badge unique">
                            💎 {pool}符文池
                          </span>
                        ))}
                      </div>
                      <div className="muted" style={{ fontSize: 10.5, lineHeight: 1.35 }}>
                        {isUnique
                          ? `🟠 ${entry.deepAnalysis.budgetSummary}`
                          : entry.status === "over-budget-sub"
                          ? `平替：${entry.deepAnalysis.budgetSummary}`
                          : `半成品：${entry.deepAnalysis.semiSummary}`}
                      </div>
                    </div>
                  )}

                  {/* 第三行：一键直达市集 & 买/做三路比价按钮组 */}
                  <div className="action-row">
                    {isUnique ? (
                      <>
                        <button
                          type="button"
                          className="btn sm primary"
                          disabled={Boolean(busyMsg)}
                          onClick={() => void handleQuickOpenTrade(it, "exact")}
                        >
                          🛒 一键直达市集
                        </button>
                        <button
                          type="button"
                          className="btn sm"
                          disabled={Boolean(busyMsg)}
                          onClick={() => void handleQuickPriceUnique(it)}
                        >
                          {busyMsg || "🔍 查实时价"}
                        </button>
                      </>
                    ) : entry.isRareCraftable ? (
                      <>
                        <button
                          type="button"
                          className="btn sm primary"
                          title="一键把该装备的基底、物等与词缀带入 poe2htc.com 精确计算最省钱做装路线"
                          onClick={() => {
                            const htc = buildHtcLinkFromParsedItem(it, { mode: "scratch", minTierMode });
                            onOpenExternalUrl(htc.url);
                          }}
                        >
                          ⚒️ 带词缀去 HTC
                        </button>
                        <button
                          type="button"
                          className="btn sm"
                          disabled={Boolean(busyMsg)}
                          onClick={() =>
                            void handleQuickOpenTrade(
                              it,
                              entry.status === "over-budget-sub" ? "budget" : "exact"
                            )
                          }
                        >
                          🛒 {entry.status === "over-budget-sub" ? "搜平替" : "买成品"}
                        </button>
                        <button
                          type="button"
                          className="btn sm"
                          disabled={Boolean(busyMsg)}
                          onClick={() => void handleQuickOpenTrade(it, "semi")}
                        >
                          🧱 搜半成品
                        </button>
                        <button
                          type="button"
                          className="btn sm"
                          disabled={Boolean(busyMsg)}
                          onClick={() => void handleQuickOpenTrade(it, "base")}
                        >
                          ⬜ 搜白底
                        </button>
                        <button
                          type="button"
                          className="btn sm"
                          disabled={Boolean(busyMsg)}
                          title="查市集三档实时价（成品/半成品/白底）"
                          onClick={() => void handleThreeWayCompare(it)}
                        >
                          {busyMsg || "⚖️ 查三档价"}
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="btn sm primary"
                          disabled={Boolean(busyMsg)}
                          onClick={() =>
                            void handleQuickOpenTrade(
                              it,
                              entry.status === "over-budget-sub" ? "budget" : "exact"
                            )
                          }
                        >
                          🛒 一键直达市集
                        </button>
                        <button
                          type="button"
                          className="btn sm"
                          disabled={Boolean(busyMsg)}
                          onClick={() => void handleQuickPriceUnique(it)}
                        >
                          {busyMsg || "🔍 查实时价"}
                        </button>
                      </>
                    )}
                  </div>

                  {slotErr && <div className="muted" style={{ color: "var(--red)" }}>{slotErr}</div>}

                  {/* 内联三路决策结论卡（买成品 vs 半成品加工 vs 白底自做） */}
                  {decision && (
                    <div className="verdict-box">
                      <div className="row between" style={{ marginBottom: 5 }}>
                        <span className={`badge ${VERDICT_STYLE[decision.recommended].cls}`}>
                          {VERDICT_STYLE[decision.recommended].badge}
                        </span>
                        {decision.bestAction?.saveDivine != null && decision.bestAction.saveDivine > 0 && (
                          <span className="badge green">省 ~{decision.bestAction.saveDivine} D</span>
                        )}
                      </div>

                      <div style={{ fontSize: 11.5, color: "#f3f4f6", marginBottom: 6 }}>
                        {decision.bestAction?.headline || decision.summary}
                      </div>

                      {/* 三路比价条 */}
                      <div className="three-way-bar">
                        <div className={`three-way-cell ${decision.recommended === "buy_finished" ? "win" : ""}`}>
                          <div className="muted">直买成品</div>
                          <strong>{fmtD(decision.finishedBuyPrice)}</strong>
                        </div>
                        <div className={`three-way-cell ${decision.recommended === "craft_semi" ? "win" : ""}`}>
                          <div className="muted">半成品加工</div>
                          <strong>{fmtD(semiExp ?? decision.semiBasePrice)}</strong>
                        </div>
                        <div className={`three-way-cell ${decision.recommended === "craft_raw" ? "win" : ""}`}>
                          <div className="muted">白底自做</div>
                          <strong>{fmtD(rawExp)}</strong>
                        </div>
                      </div>

                      {/* 主推荐行动按钮 + 配方展开 + poe2htc 外链复核 */}
                      <div className="action-row" style={{ marginTop: 6 }}>
                        {decision.bestAction?.tradeUrl && (
                          <button
                            type="button"
                            className="btn sm success"
                            onClick={() => onOpenExternalUrl(decision.bestAction!.tradeUrl!)}
                          >
                            🚀 {decision.bestAction.tradeButtonLabel}
                          </button>
                        )}
                        {(decision.bestAction?.steps?.length || 0) > 0 && (
                          <button
                            type="button"
                            className="btn sm"
                            onClick={() =>
                              setExpandedRecipeSlot((prev) => ({ ...prev, [entry.slot]: !prev[entry.slot] }))
                            }
                          >
                            {showRecipe ? "收起做装步骤 ▴" : `📋 做装步骤 (${decision.bestAction!.steps.length}步)`}
                          </button>
                        )}
                        <button
                          type="button"
                          className="btn sm"
                          title="带着这件装备的基底与词缀打开 POE2HTC 精确计算"
                          onClick={() => {
                            const htc = buildHtcLinkFromParsedItem(it, { mode: "scratch", minTierMode });
                            onOpenExternalUrl(htc.url);
                          }}
                        >
                          ↗ 带词缀去 HTC
                        </button>
                      </div>

                      {/* 逐步做装配方（含歪了怎么办） */}
                      {showRecipe && decision.bestAction?.steps && decision.bestAction.steps.length > 0 && (
                        <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 6 }}>
                          {decision.bestAction.steps.map((st) => (
                            <div key={st.step} className="recipe-step">
                              <div className="row between">
                                <strong>
                                  {st.step}. {st.action}
                                </strong>
                                {st.costDivine !== undefined && (
                                  <span className="muted">~{st.costDivine} D</span>
                                )}
                              </div>
                              <div className="muted">{st.detail}</div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {localPlan.items.length > 10 && (
            <button
              type="button"
              className="btn sm"
              style={{ width: "100%", marginTop: 6 }}
              onClick={() => setShowAllItems((v) => !v)}
            >
              {showAllItems ? "收起其余部位 ▴" : `展开全部 ${localPlan.items.length} 件装备 & 珠宝 ▾`}
            </button>
          )}
        </div>
      )}

      {/* 快捷粘贴游戏内半成品装备文本 → 一键带词缀打开 POE2HTC */}
      <details style={{ marginTop: 6 }}>
        <summary className="muted" style={{ cursor: "pointer" }}>
          ⚒️ 粘贴游戏内半成品装备文本（Ctrl+C）→ 一键带词缀去 POE2HTC
        </summary>
        <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 6 }}>
          <textarea
            className="input"
            rows={4}
            style={{ width: "100%", fontFamily: "monospace", fontSize: 11, resize: "vertical" }}
            placeholder={"直接粘贴游戏内或市集 Ctrl+C 复制的半成品装备文本：\nItem Class: Body Armours\nRarity: Rare\n...\nItem Level: 82\n--------\n+112 to maximum Life"}
            value={htcPasteText}
            onChange={(e) => setHtcPasteText(e.target.value)}
          />
          {htcPasteLink && (
            <div className="row between" style={{ gap: 6 }}>
              <span className="muted" style={{ fontSize: 11 }}>
                {htcPasteLink.supported
                  ? `${htcPasteLink.baseClass} · iLvl ${htcPasteLink.ilvl} · 识别 ${htcPasteLink.transferableExistingCount} 条词缀`
                  : `⚠️ ${htcPasteLink.unsupportedReason}`}
              </span>
              <button
                type="button"
                className="btn sm primary"
                disabled={!htcPasteLink.supported}
                onClick={() => onOpenExternalUrl(htcPasteLink.url)}
              >
                🚀 打开 POE2HTC 自动填入
              </button>
            </div>
          )}
        </div>
      </details>

      {/* 可选：AI 深度文字顾问（已有保存方案或正在生成时自动展开） */}
      <details open={streaming || !profile || Boolean(copyPlan?.items?.length || copyText)} style={{ marginTop: 6 }}>
        <summary className="muted" style={{ cursor: "pointer" }}>
          ✨ 需要文字版抄作业/无码咨询？点击展开 AI 顾问
        </summary>
        <div style={{ marginTop: 8 }}>
          <input
            type="text"
            className="input"
            style={{ width: "100%", marginBottom: 8 }}
            placeholder="补充需求（可选）：如「只刷 T15」「不买猎首」「想保高生存」"
            value={context.goalNote || ""}
            onChange={(e) => onChange({ ...context, goalNote: e.target.value })}
          />

          <div className="row between">
            {profile ? (
              <button
                type="button"
                className="btn sm primary"
                disabled={streaming}
                onClick={onGenerateCopyPlan}
              >
                {streaming ? "流式生成中..." : `✨ 生成 Lv.${level} / ${budget}D 文字建议`}
              </button>
            ) : (
              <button
                type="button"
                className="btn sm primary"
                disabled={streaming || (!context.freeformQuery?.skillIdea && !context.freeformQuery?.ascendancy)}
                onClick={onConsultWithoutPob}
              >
                {streaming ? "流式咨询中..." : `✨ 无 PoB 码：按 Lv.${level} / ${budget}D 咨询构筑`}
              </button>
            )}
            {streaming && (
              <button type="button" className="btn sm" onClick={onStopStream}>
                ⏹ 停止
              </button>
            )}
          </div>

          {copyError && (
            <div className="alert error" style={{ marginTop: 8 }}>
              {copyError}
            </div>
          )}

          {copyReasoning && (
            <details style={{ marginTop: 8, fontSize: 11, color: "var(--muted)" }}>
              <summary style={{ cursor: "pointer" }}>🧠 思考过程（{copyReasoning.length} 字）</summary>
              <div style={{ whiteSpace: "pre-wrap", marginTop: 4, padding: 6, background: "rgba(0,0,0,0.22)", borderRadius: 6 }}>
                {copyReasoning}
              </div>
            </details>
          )}

          {copyPlan && copyPlan.items.length > 0 && (
            <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
              <div className="muted" style={{ color: "var(--gold-strong)", fontWeight: 600 }}>
                🛒 AI 推荐选词 &amp; 一键直达市集（{copyPlan.items.filter((x) => x.action !== "skip").length} 件）
              </div>
              {copyPlan.budgetSummary && (
                <div className="muted" style={{ fontSize: 11 }}>{copyPlan.budgetSummary}</div>
              )}
              {copyPlan.items
                .filter((x) => x.action !== "skip")
                .map((it, i) => {
                  const pKey = `${it.slot}::${it.itemName || it.baseType || i}`;
                  const isDone = Boolean(purchasedKeys?.[pKey]);
                  return (
                  <div
                    key={`${it.slot}-${it.itemName}-${i}`}
                    className="gear-card action-card"
                    style={isDone ? { opacity: 0.72, borderColor: "rgba(69,202,140,0.45)" } : undefined}
                  >
                    <div className="row between">
                      <div className="row" style={{ gap: 4, minWidth: 0 }}>
                        <span className="badge blue">{it.slot}</span>
                        <strong style={{ fontSize: 12, color: "#fff" }}>{it.itemName}</strong>
                      </div>
                      <div className="row" style={{ gap: 4 }}>
                        {it.budgetDivine ? <span className="badge gold">~{it.budgetDivine}D</span> : null}
                        {onTogglePurchased && (
                          <button
                            type="button"
                            className={`btn sm ${isDone ? "success" : ""}`}
                            style={{ fontSize: 10, padding: "1px 6px" }}
                            onClick={() => onTogglePurchased(pKey)}
                          >
                            {isDone ? "✓ 已入手" : "☐ 已入手"}
                          </button>
                        )}
                      </div>
                    </div>
                    {it.reason && <div className="muted" style={{ fontSize: 11 }}>{it.reason}</div>}
                    {(it.selectedAffixes || []).length > 0 && (
                      <div className="chips" style={{ marginTop: 3 }}>
                        {it.selectedAffixes.map((aff, j) => (
                          <span key={j} className={`badge ${aff.statId ? "green" : ""}`} style={{ fontSize: 10 }}>
                            {aff.zh || aff.text}
                            {aff.min != null && !(aff.zh || aff.text).includes(String(aff.min)) ? ` ≥${aff.min}` : ""}
                          </span>
                        ))}
                      </div>
                    )}
                    <div className="action-row" style={{ marginTop: 4 }}>
                      <button
                        type="button"
                        className="btn sm primary"
                        onClick={() => void handleOpenAiCopyItemTrade(it)}
                      >
                        🛒 一键直达市集
                        {it.matchedStatCount ? ` (${it.matchedStatCount}词)` : ""}
                      </button>
                    </div>
                  </div>
                  );
                })}
            </div>
          )}

          {copyText && (
            <div
              style={{
                marginTop: 8,
                padding: "8px 10px",
                borderRadius: 6,
                background: "rgba(0,0,0,0.22)",
              }}
            >
              <MarkdownView content={copyText} streaming={streaming} compact />
            </div>
          )}
        </div>
      </details>
    </section>
  );
};
