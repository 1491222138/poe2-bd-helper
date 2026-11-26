import React, { useMemo, useRef, useState } from "react";
import type { BilingualTranslator, ParsedBuildData, ParsedItem, PriceKind } from "@contracts";
import type { AttachedUniquePrice, LeaguePriceIndex } from "../../market/ninjaPrice";
import {
  applyPriceKindPreset,
  batchPriceItems,
  buildFallbackTradeUrl,
  buildTradeQuery,
  matchItemMods,
  priceSingleItem,
  type BatchPriceController,
  type ItemTradeQuote,
  type MatchedModRow,
} from "../../market/tradeBuilder";
import { analyzeItemDeeply, type ItemDeepAnalysis } from "../../parsers/affixAnalyzer";

interface MarketPricingPanelProps {
  t: BilingualTranslator;
  build: ParsedBuildData;
  league: string;
  priceIndex: LeaguePriceIndex | null;
  loadingNinja: boolean;
  onRefreshNinja: () => void;
  uniquePrices: AttachedUniquePrice[];
  tradeQuotes: Record<string, ItemTradeQuote>;
  onQuoteUpdate: (quote: ItemTradeQuote) => void;
  onOpenExternalUrl: (url: string) => void;
}

const KIND_OPTIONS: Array<{ kind: PriceKind; label: string; hint: string }> = [
  { kind: "exact", label: "原件 (-10%)", hint: "同底材 + 3~4条核心词缀 + 孔位" },
  { kind: "budget", label: "平替 (-25%)", hint: "同大类 + 2~3条主词缀降配" },
  { kind: "semi", label: "半成品底子", hint: "仅锁1~2条最难出词缀 + 非腐化" },
  { kind: "base", label: "白底底材", hint: "同基底高物等普通非腐化白底" },
];

export const MarketPricingPanel: React.FC<MarketPricingPanelProps> = ({
  t,
  build,
  league,
  priceIndex,
  loadingNinja,
  onRefreshNinja,
  uniquePrices,
  tradeQuotes,
  onQuoteUpdate,
  onOpenExternalUrl,
}) => {
  const [globalKind, setGlobalKind] = useState<PriceKind>("budget");
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchPaused, setBatchPaused] = useState(false);
  const [rateLimited, setRateLimited] = useState(false);
  const [progressText, setProgressText] = useState("");

  const [expandedSlot, setExpandedSlot] = useState<string | null>(null);
  const [modRowsBySlot, setModRowsBySlot] = useState<Record<string, MatchedModRow[]>>({});
  const [deepBySlot, setDeepBySlot] = useState<Record<string, ItemDeepAnalysis>>({});
  const [slotKindMap, setSlotKindMap] = useState<Record<string, PriceKind>>({});
  const [busySlot, setBusySlot] = useState<string | null>(null);
  const [slotNotice, setSlotNotice] = useState<Record<string, string>>({});

  const controllerRef = useRef<BatchPriceController>({});

  const uniqueBySlot = useMemo(() => {
    const map = new Map<string, AttachedUniquePrice>();
    for (const u of uniquePrices) map.set(u.slot, u);
    return map;
  }, [uniquePrices]);

  const allPriceableItems = useMemo(() => {
    return [...build.equippedItems, ...build.jewels];
  }, [build]);

  const totalEstimatedD = useMemo(() => {
    const slotVals = new Map<string, number>();
    for (const u of uniquePrices) slotVals.set(u.slot, u.divineValue);
    for (const q of Object.values(tradeQuotes)) {
      if (q.kind === "exact" || !slotVals.has(q.slot)) {
        slotVals.set(q.slot, q.divineValue);
      }
    }
    let sum = 0;
    for (const v of slotVals.values()) sum += v;
    return Math.round(sum * 10) / 10;
  }, [uniquePrices, tradeQuotes]);

  async function toggleExpand(item: ParsedItem) {
    if (expandedSlot === item.slot) {
      setExpandedSlot(null);
      return;
    }
    setExpandedSlot(item.slot);
    if (!deepBySlot[item.slot]) {
      setDeepBySlot((prev) => ({ ...prev, [item.slot]: analyzeItemDeeply(item, build) }));
    }
    if (!modRowsBySlot[item.slot]) {
      setBusySlot(item.slot);
      try {
        const kind = slotKindMap[item.slot] || globalKind;
        const rows = await matchItemMods(item, kind, build);
        setModRowsBySlot((prev) => ({ ...prev, [item.slot]: rows }));
        setSlotKindMap((prev) => ({ ...prev, [item.slot]: kind }));
      } catch (err) {
        setSlotNotice((prev) => ({ ...prev, [item.slot]: (err as Error).message }));
      } finally {
        setBusySlot(null);
      }
    }
  }

  function changeSlotKind(item: ParsedItem, kind: PriceKind) {
    setSlotKindMap((prev) => ({ ...prev, [item.slot]: kind }));
    const deep = deepBySlot[item.slot] || analyzeItemDeeply(item, build);
    setModRowsBySlot((prev) => {
      const existing = prev[item.slot];
      if (!existing) return prev;
      return { ...prev, [item.slot]: applyPriceKindPreset(existing, kind, deep) };
    });
  }

  function toggleModChecked(slot: string, index: number) {
    setModRowsBySlot((prev) => ({
      ...prev,
      [slot]: (prev[slot] || []).map((r) => (r.index === index ? { ...r, checked: !r.checked } : r)),
    }));
  }

  function updateModMin(slot: string, index: number, raw: string) {
    const parsed = raw.trim() === "" ? null : Number(raw);
    setModRowsBySlot((prev) => ({
      ...prev,
      [slot]: (prev[slot] || []).map((r) =>
        r.index === index ? { ...r, min: parsed !== null && Number.isFinite(parsed) ? parsed : null } : r
      ),
    }));
  }

  async function runSingleTradePrice(item: ParsedItem) {
    const kind = slotKindMap[item.slot] || globalKind;
    setBusySlot(item.slot);
    setSlotNotice((prev) => ({ ...prev, [item.slot]: "正在查询官方市集..." }));
    try {
      const res = await priceSingleItem(item, league, kind, priceIndex, modRowsBySlot[item.slot], build);
      if (res.rateLimited) {
        setRateLimited(true);
        setSlotNotice((prev) => ({ ...prev, [item.slot]: "触发 429 限流，已立即熔断保护，请稍候再试。" }));
      } else if (!res.ok) {
        setSlotNotice((prev) => ({ ...prev, [item.slot]: res.error || "市集查询失败" }));
      } else if (res.quote) {
        onQuoteUpdate(res.quote);
        setSlotNotice((prev) => ({
          ...prev,
          [item.slot]: `命中 ${res.quote!.totalListings} 件，参考价 ${res.quote!.formatted}`,
        }));
      } else {
        setSlotNotice((prev) => ({
          ...prev,
          [item.slot]: "当前条件 0 件在售，可切到「平替」或「半成品底子」再搜。",
        }));
      }
    } catch (err) {
      setSlotNotice((prev) => ({ ...prev, [item.slot]: (err as Error).message }));
    } finally {
      setBusySlot(null);
    }
  }

  async function openDirectTrade(item: ParsedItem) {
    const kind = slotKindMap[item.slot] || globalKind;
    const rows = modRowsBySlot[item.slot] || (await matchItemMods(item, kind, build));
    const deep = deepBySlot[item.slot] || analyzeItemDeeply(item, build);
    const payload = buildTradeQuery(item, rows, { kind, build, deepAnalysis: deep });
    onOpenExternalUrl(buildFallbackTradeUrl(league, payload));
  }

  async function startBatchPricing() {
    const rareOrUnpriced = allPriceableItems.filter(
      (it) => it.rarity.toUpperCase() !== "UNIQUE" || !uniqueBySlot.has(it.slot)
    );
    if (rareOrUnpriced.length === 0) {
      setProgressText("全部暗金已由 poe.ninja 自动核价，无需请求官方市集。");
      return;
    }

    setBatchRunning(true);
    setBatchPaused(false);
    setRateLimited(false);
    setProgressText(`准备按 1.6s 安全间隔核查 ${Math.min(20, rareOrUnpriced.length)} 件关键装备与珠宝...`);

    const ctrl: BatchPriceController = { paused: false, aborted: false, kind: globalKind };
    controllerRef.current = ctrl;

    try {
      const result = await batchPriceItems(
        rareOrUnpriced,
        league,
        priceIndex,
        ctrl,
        (p) => {
          if (p.state === "PRICED" && p.quote) {
            onQuoteUpdate(p.quote);
            setProgressText(`(${p.index}/${p.total}) ${p.slot}: ${p.quote.formatted}`);
          } else if (p.state === "RUNNING") {
            setProgressText(`(${p.index}/${p.total}) 正在查 ${p.slot} · ${p.itemName}...`);
          } else if (p.state === "PAUSED") {
            setProgressText(`已暂停 (${p.index}/${p.total})`);
          } else if (p.state === "RATE_LIMITED") {
            setRateLimited(true);
            setProgressText("检测到官方市集 429 限流，已自动熔断停止。");
          } else if (p.state === "EMPTY") {
            setProgressText(`(${p.index}/${p.total}) ${p.slot} 当前模式 0 件在售`);
          }
        },
        build
      );
      if (!result.rateLimited && !ctrl.aborted) {
        setProgressText(`完成：成功核出 ${result.quotes.length} 件官方市集实价。`);
      }
    } catch (err) {
      setProgressText(`核价异常：${(err as Error).message}`);
    } finally {
      setBatchRunning(false);
      setBatchPaused(false);
    }
  }

  function togglePause() {
    controllerRef.current.paused = !controllerRef.current.paused;
    setBatchPaused(Boolean(controllerRef.current.paused));
  }

  function abortBatch() {
    controllerRef.current.aborted = true;
    controllerRef.current.paused = false;
    setBatchPaused(false);
    setProgressText("已手动中止批量查价。");
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {/* 汇率与批量查价控制栏 */}
      <section className="card">
        <div className="card-title">
          <span>💰 市集核价控制台</span>
          <button type="button" className="btn sm" onClick={onRefreshNinja} disabled={loadingNinja}>
            {loadingNinja ? "刷新中..." : "刷新 Ninja 物价"}
          </button>
        </div>

        <div className="row between" style={{ marginBottom: 8 }}>
          <div className="muted">
            {priceIndex
              ? `1 D ≈ ${priceIndex.exaltPerDivine || "?"} E · 已缓存 ${priceIndex.itemCount} 项`
              : "正在拉取 poe.ninja 汇率..."}
          </div>
          <span className="badge gold">当前估算合计 ≈ {totalEstimatedD} D</span>
        </div>

        <div className="row" style={{ marginBottom: 8 }}>
          {KIND_OPTIONS.map((opt) => (
            <button
              key={opt.kind}
              type="button"
              className={`btn sm ${globalKind === opt.kind ? "primary" : ""}`}
              title={opt.hint}
              onClick={() => setGlobalKind(opt.kind)}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <div className="row">
          {!batchRunning ? (
            <button type="button" className="btn success" onClick={startBatchPricing}>
              ⚡ 一键安全核价 (防 429)
            </button>
          ) : (
            <>
              <button type="button" className="btn" onClick={togglePause}>
                {batchPaused ? "▶ 继续" : "⏸ 暂停"}
              </button>
              <button type="button" className="btn danger" onClick={abortBatch}>
                ⏹ 中止
              </button>
            </>
          )}
        </div>

        {progressText && (
          <div className={`alert ${rateLimited ? "error" : "info"}`} style={{ marginTop: 8 }}>
            {progressText}
          </div>
        )}
      </section>

      {/* 装备与珠宝逐件核价列表 */}
      <section className="card">
        <div className="card-title">
          <span>🛡️ 装备 & 珠宝清单（{allPriceableItems.length} 件）</span>
          <span className="muted">点击展开调词缀</span>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          {allPriceableItems.map((item) => {
            const ninja = uniqueBySlot.get(item.slot);
            const quote = tradeQuotes[`${item.slot}:${slotKindMap[item.slot] || globalKind}`] ||
              Object.values(tradeQuotes).find((q) => q.slot === item.slot);
            const isExpanded = expandedSlot === item.slot;
            const rows = modRowsBySlot[item.slot] || [];
            const activeKind = slotKindMap[item.slot] || globalKind;

            return (
              <div key={`${item.slot}-${item.itemId}`} className="gear-card">
                <div className="row between" style={{ cursor: "pointer" }} onClick={() => toggleExpand(item)}>
                  <div>
                    <div className="row" style={{ gap: 5 }}>
                      <span className="muted">{t.slot(item.slot)}</span>
                      <span className={`badge ${item.rarity === "UNIQUE" ? "unique" : "rare"}`}>
                        {item.rarity === "UNIQUE" ? "暗金" : "黄装"}
                      </span>
                    </div>
                    <div style={{ fontWeight: 600, marginTop: 2 }}>
                      {t.itemName(item.name, item.baseType)}
                    </div>
                  </div>

                  <div style={{ textAlign: "right" }}>
                    {quote ? (
                      <div>
                        <span className="badge green">{quote.formatted}</span>
                        <div className="muted">{quote.modeLabel}</div>
                      </div>
                    ) : ninja ? (
                      <div>
                        <span className="badge gold">{ninja.formatted}</span>
                        <div className="muted">poe.ninja</div>
                      </div>
                    ) : (
                      <span className="muted">未核价 ▾</span>
                    )}
                  </div>
                </div>

                {isExpanded && (
                  <div style={{ marginTop: 4 }}>
                    <div className="divider" />
                    <div className="row between" style={{ marginBottom: 6 }}>
                      <div className="row">
                        {item.rarity.toUpperCase() === "UNIQUE" ? (
                          <span className="badge unique">🟠 传奇直买（仅挑 roll/瓦尔/孔位，无需做装）</span>
                        ) : (
                          KIND_OPTIONS.map((opt) => (
                            <button
                              key={opt.kind}
                              type="button"
                              className={`btn sm ${activeKind === opt.kind ? "primary" : ""}`}
                              onClick={() => changeSlotKind(item, opt.kind)}
                            >
                              {opt.kind === "exact"
                                ? "原件"
                                : opt.kind === "budget"
                                ? "平替"
                                : opt.kind === "semi"
                                ? "半成品"
                                : "白底"}
                            </button>
                          ))
                        )}
                      </div>
                      <div className="row">
                        <button
                          type="button"
                          className="btn sm success"
                          disabled={busySlot === item.slot}
                          onClick={() => runSingleTradePrice(item)}
                        >
                          {busySlot === item.slot ? "查价中..." : "查这一件"}
                        </button>
                        <button type="button" className="btn sm" onClick={() => openDirectTrade(item)}>
                          🌐 去市集
                        </button>
                      </div>
                    </div>

                    {(() => {
                      const deep = deepBySlot[item.slot];
                      if (!deep) return null;
                      const sa = deep.socketAnalysis;
                      const hasChips =
                        deep.passiveSynergies.length > 0 ||
                        deep.requireBaseType ||
                        Boolean(deep.defenseFloor) ||
                        sa.exceedsCraftableSockets ||
                        sa.hasSocketEffectMod ||
                        sa.runeUnlockedPools.length > 0 ||
                        deep.isSpeedWeapon;
                      return (
                        <div
                          style={{
                            marginBottom: 6,
                            padding: "5px 7px",
                            borderRadius: 6,
                            background: "rgba(255,255,255,0.04)",
                            fontSize: 11,
                          }}
                        >
                          {hasChips && (
                            <div className="chips" style={{ marginBottom: 4 }}>
                              {deep.isSpeedWeapon && <span className="badge blue">⚡ 攻速/位移工具组</span>}
                              {deep.requireBaseType && (
                                <span className="badge gold" title={deep.baseTypeReason}>
                                  🔒 锁基底: {t.term(item.baseType, "bases")}
                                </span>
                              )}
                              {deep.defenseFloor && (
                                <span className="badge green" title={deep.defenseFloor.reason}>
                                  🛡️ 锁总{deep.defenseFloor.stat.toUpperCase()} ≥{" "}
                                  {activeKind === "exact" ? deep.defenseFloor.exactMin : deep.defenseFloor.budgetMin}
                                </span>
                              )}
                              {deep.passiveSynergies.slice(0, 2).map((ps) => (
                                <span key={ps} className="badge purple">
                                  🌳 {ps}
                                </span>
                              ))}
                              {(sa.exceedsCraftableSockets || sa.hasSocketEffectMod) && (
                                <span className="badge red">
                                  💎 锁 ≥{sa.socketCount} 符文孔
                                  {sa.exceedsCraftableSockets ? `(超工匠上限${sa.naturalMaxSockets})` : ""}
                                </span>
                              )}
                              {sa.runeUnlockedPools.map((pool) => (
                                <span key={pool} className="badge unique">
                                  💎 符文池: {pool}
                                </span>
                              ))}
                            </div>
                          )}
                          <div className="muted" style={{ lineHeight: 1.4 }}>
                            {item.rarity.toUpperCase() === "UNIQUE"
                              ? `🟠 ${deep.budgetSummary}`
                              : activeKind === "semi"
                              ? `🧱 半成品：${deep.semiSummary}`
                              : `💡 平替：${deep.budgetSummary}`}
                          </div>
                          {sa.socketCount > 0 && (
                            <div className="muted" style={{ lineHeight: 1.4, marginTop: 2 }}>
                              💎 符文插槽：{sa.advice}
                            </div>
                          )}
                        </div>
                      );
                    })()}

                    {rows.map((r) => (
                      <div key={r.index} className="mod-row" style={{ alignItems: "flex-start" }}>
                        <input
                          type="checkbox"
                          checked={r.checked}
                          disabled={!r.matched}
                          style={{ marginTop: 3 }}
                          onChange={() => toggleModChecked(item.slot, r.index)}
                        />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <span
                            style={{
                              opacity: r.matched && r.role !== "dead" ? 1 : 0.48,
                              textDecoration: r.role === "dead" ? "line-through" : "none",
                            }}
                          >
                            {r.side === "prefix" ? "[前] " : r.side === "suffix" ? "[后] " : ""}
                            {t.mod(r.cleaned)}
                          </span>
                          {r.synergyTags && r.synergyTags.length > 0 && (
                            <div className="chips" style={{ marginTop: 2 }}>
                              {r.synergyTags.slice(0, 2).map((tag) => (
                                <span
                                  key={tag}
                                  className={`badge ${
                                    r.role === "dead"
                                      ? "red"
                                      : tag.includes("🌳")
                                      ? "purple"
                                      : tag.includes("💎")
                                      ? "gold"
                                      : ""
                                  }`}
                                  style={{ fontSize: 10, padding: "1px 5px" }}
                                >
                                  {tag}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                        {r.matched && r.originalValue !== null && (
                          <input
                            type="number"
                            className="mod-min"
                            value={r.min ?? ""}
                            placeholder="min"
                            onChange={(e) => updateModMin(item.slot, r.index, e.target.value)}
                          />
                        )}
                      </div>
                    ))}

                    {slotNotice[item.slot] && (
                      <div className="muted" style={{ marginTop: 5 }}>
                        {slotNotice[item.slot]}
                      </div>
                    )}
                    {quote?.webUrl && (
                      <div style={{ marginTop: 4 }}>
                        <span className="link" onClick={() => onOpenExternalUrl(quote.webUrl)}>
                          ↗ 查看本次市集挂单 ({quote.totalListings} 件)
                        </span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
};
