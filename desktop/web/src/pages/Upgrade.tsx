import React, { useEffect, useState } from "react";
import type {
  BilingualTranslator,
  BudgetRoadmapReport,
  GapAnalysisReport,
  SlotAffixReport,
  SlotUpgradeStep,
} from "@contracts";
import { desktopApi } from "../api/workbenchApi";
import { buildOfficialTradeSearchUrl, fmtDeltaPct, openExternalUrl, openExternalUrlAsync } from "../utils/handoffParser";

interface UpgradeProps {
  t: BilingualTranslator;
  league: string;
  gapReport: GapAnalysisReport | null;
  priceOverrides: Record<string, number>;
  onPriceOverrideChange: (slot: string, value: number | null) => void;
  onInspectAffixes: (slot: string) => Promise<SlotAffixReport>;
  onPlanRoadmap: (budget: number, overrides: Record<string, number>) => Promise<BudgetRoadmapReport>;
  onOpenCraftWithSlot: (affixReport: SlotAffixReport) => void;
}

const BUDGET_PRESETS = [5, 15, 30, 60, 120];

export const Upgrade: React.FC<UpgradeProps> = ({
  t,
  league,
  gapReport,
  priceOverrides,
  onPriceOverrideChange,
  onInspectAffixes,
  onPlanRoadmap,
  onOpenCraftWithSlot,
}) => {
  const [selectedSlot, setSelectedSlot] = useState<string | null>(null);
  const [affixReport, setAffixReport] = useState<SlotAffixReport | null>(null);
  const [loadingAffix, setLoadingAffix] = useState(false);
  const [affixError, setAffixError] = useState("");

  const [budget, setBudget] = useState<number>(30);
  const [roadmap, setRoadmap] = useState<BudgetRoadmapReport | null>(null);
  const [planning, setPlanning] = useState(false);
  const [tradeFeedback, setTradeFeedback] = useState("");
  const [tradeFeedbackUrl, setTradeFeedbackUrl] = useState("");
  const [openingSlot, setOpeningSlot] = useState<string | null>(null);

  useEffect(() => {
    if (!gapReport) return;
    let cancelled = false;
    setPlanning(true);
    onPlanRoadmap(budget, priceOverrides)
      .then((rm) => {
        if (!cancelled) setRoadmap(rm);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setPlanning(false);
      });
    return () => {
      cancelled = true;
    };
  }, [gapReport, budget, priceOverrides, onPlanRoadmap]);

  if (!gapReport) {
    return (
      <div className="page">
        <section className="card">
          <div className="muted">
            请先在「总览 & 0 成本诊断」页完成一次自身 vs 目标全景诊断，再查看逐件试穿榜单、单词缀剥离与预算升级路线。
          </div>
        </section>
      </div>
    );
  }

  async function handleInspect(slot: string) {
    setSelectedSlot(slot);
    setLoadingAffix(true);
    setAffixError("");
    try {
      const rep = await onInspectAffixes(slot);
      setAffixReport(rep);
    } catch (err) {
      setAffixError((err as Error).message);
      setAffixReport(null);
    } finally {
      setLoadingAffix(false);
    }
  }

  async function handleOpenTradeDirect(step: SlotUpgradeStep, kind: "exact" | "budget" | "semi") {
    const isUnique = (step.targetRarity || "").toUpperCase() === "UNIQUE";
    const opKey = `${step.slot}:${kind}`;
    setOpeningSlot(opKey);
    setTradeFeedback(`正在为 ${step.slot} 生成${isUnique ? "暗金" : "官方加权平替"}市集链接并跳转...`);
    setTradeFeedbackUrl("");
    try {
      let summary = "";
      const finalUrl = await openExternalUrlAsync(async () => {
        const built = await desktopApi.buildTradeUrl({
          league,
          slot: step.slot,
          itemName: step.targetItemName || "",
          baseType: step.targetBaseType || "",
          rarity: step.targetRarity || "RARE",
          action: isUnique ? "buy_unique" : kind === "semi" ? "craft_semi" : kind === "exact" ? "buy_finished" : "buy_budget",
          useWeighted: !isUnique,
        });
        if (!isUnique && built.mode === "weighted" && built.minScore) {
          summary = `${step.slot} 已打开官方加权市集（Weighted Sum ≥ ${built.minScore} / 目标 ${built.targetScore}，共 ${built.weightedTerms?.length || 0} 项权重）`;
        } else {
          summary = `${step.slot} 已打开官方市集搜索页`;
        }
        return built.tradeUrl;
      });
      setTradeFeedback(summary || `${step.slot} 已打开官方市集搜索页`);
      if (finalUrl) setTradeFeedbackUrl(finalUrl);
    } catch {
      const fallback = step.tradeUrl || buildOfficialTradeSearchUrl(league, step.targetItemName || "", step.targetBaseType || "");
      openExternalUrl(fallback);
      setTradeFeedback(`${step.slot} 已按兜底链接打开官方市集`);
      setTradeFeedbackUrl(fallback);
    } finally {
      setOpeningSlot(null);
    }
  }

  return (
    <div className="page">
      {tradeFeedback && (
        <div className="banner info">
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <span>{tradeFeedback}</span>
            {tradeFeedbackUrl && (
              <a
                href={tradeFeedbackUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="btn sm primary"
                style={{ textDecoration: "none" }}
                onClick={(e) => {
                  e.preventDefault();
                  openExternalUrl(tradeFeedbackUrl);
                }}
              >
                ↗ 手动打开官方市集页
              </a>
            )}
          </div>
          <button
            type="button"
            className="btn sm"
            onClick={() => {
              setTradeFeedback("");
              setTradeFeedbackUrl("");
            }}
          >
            关闭
          </button>
        </div>
      )}

      {/* 1. 逐件装备试穿榜单 */}
      <section className="card">
        <div className="card-header">
          <span className="card-title">🛡️ 逐件装备试穿榜单（把目标装备逐件穿到你身上实算）</span>
          <span className="muted">可手改任意槽位参考价 (D)，下方 0-1 背包路线即时重算</span>
        </div>
        <div className="muted" style={{ fontSize: 11.5, marginBottom: 8 }}>
          💡 <strong>🌐 加权搜平替</strong>：按 PoB2 消融权重求和直达官方市集（按价低排序，不死锁单条词缀）；
          <strong>⚒️ 搜半成品</strong>：锁基底 + 非腐化 + 1~2 条最难核心词缀；看清实价后直接在「参考价 (D)」手填即可即时重算预算路线。
        </div>

        <div style={{ overflowX: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>槽位 & 目标件</th>
                <th>ΔDPS%</th>
                <th>ΔEHP%</th>
                <th>红线预警</th>
                <th>参考价 (D)</th>
                <th>性价比</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {(() => {
                const maxAbsDps = Math.max(5, ...gapReport.slotUpgrades.map((s) => Math.abs(s.deltaDpsPct)));
                const maxAbsEhp = Math.max(5, ...gapReport.slotUpgrades.map((s) => Math.abs(s.deltaEhpPct)));
                return gapReport.slotUpgrades.map((step) => {
                const effectivePrice = priceOverrides[step.slot] ?? step.priceDivine ?? null;
                const roi =
                  effectivePrice && effectivePrice > 0
                    ? (step.deltaDpsPct / effectivePrice).toFixed(2)
                    : null;
                const dpsBarPct = Math.min(100, Math.round((Math.abs(step.deltaDpsPct) / maxAbsDps) * 100));
                const ehpBarPct = Math.min(100, Math.round((Math.abs(step.deltaEhpPct) / maxAbsEhp) * 100));

                return (
                  <tr key={step.slot}>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <strong>{t.slot(step.slot)}</strong>
                        {step.targetRarity && (
                          <span className={`badge ${step.targetRarity === "UNIQUE" ? "unique" : "gold"}`}>
                            {step.targetRarity === "UNIQUE" ? "暗金" : "黄装"}
                          </span>
                        )}
                        {step.isIdentical && <span className="badge green">已同款</span>}
                      </div>
                      <div style={{ marginTop: 2 }}>{t.itemName(step.targetItemName, step.targetBaseType)}</div>
                      <div className="muted">当前：{step.myItemName ? t.itemName(step.myItemName) : "-"}</div>
                    </td>

                    <td>
                      <div className="delta-cell">
                        <span className={`delta-value ${step.deltaDpsPct > 0.2 ? "pos" : step.deltaDpsPct < -0.2 ? "neg" : "muted"}`}>
                          {fmtDeltaPct(step.deltaDpsPct)}
                        </span>
                        {Math.abs(step.deltaDpsPct) >= 0.2 && (
                          <div className="delta-bar-track">
                            <div
                              className={`delta-bar-fill ${step.deltaDpsPct >= 0 ? "dps" : "neg"}`}
                              style={{ width: `${Math.max(4, dpsBarPct)}%` }}
                            />
                          </div>
                        )}
                      </div>
                    </td>

                    <td>
                      <div className="delta-cell">
                        <span className={`delta-value ${step.deltaEhpPct > 0.2 ? "pos" : step.deltaEhpPct < -0.2 ? "neg" : "muted"}`}>
                          {fmtDeltaPct(step.deltaEhpPct)}
                        </span>
                        {Math.abs(step.deltaEhpPct) >= 0.2 && (
                          <div className="delta-bar-track">
                            <div
                              className={`delta-bar-fill ${step.deltaEhpPct >= 0 ? "ehp" : "neg"}`}
                              style={{ width: `${Math.max(4, ehpBarPct)}%` }}
                            />
                          </div>
                        )}
                      </div>
                    </td>

                    <td>
                      {step.redlines.length > 0 ? (
                        <div className="chips">
                          {step.redlines.slice(0, 2).map((r, i) => (
                            <span key={i} className={`badge ${r.severity === "fail" ? "red" : "amber"}`}>
                              {r.message}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="badge green">安全</span>
                      )}
                    </td>

                    <td>
                      <input
                        type="number"
                        min={0}
                        step={0.5}
                        className="input"
                        style={{ width: 72 }}
                        placeholder="填 D"
                        value={effectivePrice ?? ""}
                        onChange={(e) => {
                          const raw = e.target.value.trim();
                          const n = Number(raw);
                          onPriceOverrideChange(step.slot, raw === "" || !Number.isFinite(n) ? null : n);
                        }}
                      />
                      {(step.exactPriceDivine || step.budgetPriceDivine || step.semiPriceDivine) && (
                        <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>
                          {step.exactPriceDivine ? `原${step.exactPriceDivine}D ` : ""}
                          {step.budgetPriceDivine ? `替${step.budgetPriceDivine}D ` : ""}
                          {step.semiPriceDivine ? `坯${step.semiPriceDivine}D` : ""}
                        </div>
                      )}
                    </td>

                    <td>{roi ? <span className="badge blue">{roi}%/D</span> : <span className="muted">-</span>}</td>

                    <td>
                      <div className="row" style={{ gap: 5 }}>
                        {!step.isIdentical && (
                          <button
                            type="button"
                            className={`btn sm ${selectedSlot === step.slot ? "primary" : ""}`}
                            onClick={() => void handleInspect(step.slot)}
                          >
                            🔬 拆解词缀
                          </button>
                        )}
                        <button
                          type="button"
                          className="btn sm primary"
                          disabled={openingSlot === `${step.slot}:budget`}
                          title="在浏览器直接打开官方市集加权平替搜索（自动带上 PoB2 消融权重，按价低排序）"
                          onClick={() => void handleOpenTradeDirect(step, "budget")}
                        >
                          {openingSlot === `${step.slot}:budget`
                            ? "打开中..."
                            : `🌐 ${step.targetRarity === "UNIQUE" ? "直达市集" : "加权搜平替"}`}
                        </button>
                        {step.targetRarity !== "UNIQUE" && (
                          <button
                            type="button"
                            className="btn sm"
                            disabled={openingSlot === `${step.slot}:semi`}
                            title="在浏览器直接打开该槽位非腐化半成品底子搜索"
                            onClick={() => void handleOpenTradeDirect(step, "semi")}
                          >
                            {openingSlot === `${step.slot}:semi` ? "打开中..." : "⚒️ 搜半成品"}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              });
              })()}
            </tbody>
          </table>
        </div>
      </section>

      {/* 2. 关键稀有槽位词缀逐条剥离抽屉 */}
      {selectedSlot && (
        <section className="card gold">
          <div className="card-header">
            <span className="card-title">
              🔬 槽位词缀逐条剥离实算：{t.slot(selectedSlot)}
              {affixReport ? ` · ${t.itemName(affixReport.itemName, affixReport.baseType)}` : ""}
            </span>
            <div className="row">
              {affixReport && (affixReport.rarity || "").toUpperCase() !== "UNIQUE" && (
                <button
                  type="button"
                  className="btn sm primary"
                  onClick={() => onOpenCraftWithSlot(affixReport)}
                >
                  带入做装对比台 →
                </button>
              )}
              <button type="button" className="btn sm" onClick={() => setSelectedSlot(null)}>
                收起
              </button>
            </div>
          </div>

          {loadingAffix && <div className="muted">正在常驻 PoB2 引擎中逐条剥离词缀并实算...</div>}
          {affixError && <div className="banner error">{affixError}</div>}

          {affixReport && !loadingAffix && (
            <>
              <div className="row" style={{ marginBottom: 10, flexWrap: "wrap", gap: 6 }}>
                <span className="badge red">必保词缀 {affixReport.mustAffixes.length} 条</span>
                <span className="badge green">可降配/可砍 {affixReport.cuttableAffixes.length} 条</span>
                {affixReport.passiveSynergies &&
                  affixReport.passiveSynergies.map((ps) => (
                    <span key={ps} className="badge purple">
                      🌳 {ps}
                    </span>
                  ))}
                <span className="muted">
                  穿上整件 ΔDPS {fmtDeltaPct(affixReport.deltaDpsPct)} · ΔEHP {fmtDeltaPct(affixReport.deltaEhpPct)} · 耗时{" "}
                  {affixReport.elapsedMs}ms
                </span>
              </div>

              {/* 符文插槽与平替/半成品深度建议 */}
              {(affixReport.socketAnalysis || affixReport.budgetPlan || affixReport.semiPlan) && (
                <div className="grid-3" style={{ marginBottom: 10 }}>
                  {affixReport.socketAnalysis && (
                    <div className="kpi-card">
                      <div className="row between" style={{ marginBottom: 4 }}>
                        <strong>💎 符文插槽分析</strong>
                        <span
                          className={`badge ${
                            affixReport.socketAnalysis.exceedsCraftableSockets ? "red" : "blue"
                          }`}
                        >
                          {affixReport.socketAnalysis.socketCount} 孔 / 工匠上限{" "}
                          {affixReport.socketAnalysis.naturalMaxSockets}
                        </span>
                      </div>
                      {(affixReport.socketAnalysis.runeDeltaDpsPct !== undefined ||
                        affixReport.socketAnalysis.runeDeltaEhpPct !== undefined) && (
                        <div className="chips" style={{ marginBottom: 4 }}>
                          <span className="badge gold">
                            符文贡献 ΔDPS {fmtDeltaPct(affixReport.socketAnalysis.runeDeltaDpsPct || 0)}
                          </span>
                          <span className="badge green">
                            ΔEHP {fmtDeltaPct(affixReport.socketAnalysis.runeDeltaEhpPct || 0)}
                          </span>
                          {affixReport.socketAnalysis.runeSpiritLoss ? (
                            <span className="badge blue">精魂 -{affixReport.socketAnalysis.runeSpiritLoss}</span>
                          ) : null}
                        </div>
                      )}
                      <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.45 }}>
                        {affixReport.socketAnalysis.advice}
                      </div>
                    </div>
                  )}

                  {(affixReport.rarity || "").toUpperCase() === "UNIQUE" ? (
                    <div className="kpi-card" style={{ gridColumn: "span 2" }}>
                      <div className="row between" style={{ marginBottom: 4 }}>
                        <strong>🟠 传奇/暗金直买说明（无需复杂做装）</strong>
                        <span className="badge unique">暗金直买</span>
                      </div>
                      <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.5 }}>
                        传奇装备不可使用通货/精华/半成品重做词缀：按预算直接搜成品即可，重点看下方高贡献核心词条的数值 roll；
                        仅需按需补满工匠孔插符文、提品质、涂油（项链），或直接买已瓦尔出额外孔/好瓦尔基底的成品。
                      </div>
                    </div>
                  ) : (
                    <>
                      {affixReport.budgetPlan && (
                        <div className="kpi-card">
                          <div className="row between" style={{ marginBottom: 4 }}>
                            <strong>🔄 平替锁词策略</strong>
                            {affixReport.budgetPlan.requireSockets && (
                              <span className="badge red">锁 ≥{affixReport.budgetPlan.requireSockets} 孔</span>
                            )}
                          </div>
                          <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.45, marginBottom: 4 }}>
                            {affixReport.budgetPlan.summary}
                          </div>
                          {affixReport.budgetPlan.lockAffixes.length > 0 && (
                            <div className="chips">
                              {affixReport.budgetPlan.lockAffixes.map((l) => (
                                <span key={l} className="badge blue">
                                  {l}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      )}

                      {affixReport.semiPlan && (
                        <div className="kpi-card">
                          <div className="row between" style={{ marginBottom: 4 }}>
                            <strong>🧱 半成品底子策略</strong>
                            {affixReport.semiPlan.requireSockets && (
                              <span className="badge red">需 ≥{affixReport.semiPlan.requireSockets} 孔卓越底</span>
                            )}
                          </div>
                          <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.45, marginBottom: 4 }}>
                            {affixReport.semiPlan.summary}
                          </div>
                          {affixReport.semiPlan.lockAffixes.length > 0 && (
                            <div className="chips">
                              {affixReport.semiPlan.lockAffixes.map((l) => (
                                <span key={l} className="badge gold">
                                  锁: {t.mod(l)}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}

              <table className="table">
                <thead>
                  <tr>
                    <th>重要度分级</th>
                    <th>装备词缀 &amp; 天赋/符文联动</th>
                    <th>词缀贡献 ΔDPS%</th>
                    <th>词缀贡献 ΔEHP%</th>
                    <th>去掉后红线影响</th>
                  </tr>
                </thead>
                <tbody>
                  {affixReport.affixes.map((aff, idx) => (
                    <tr key={idx}>
                      <td>
                        <span
                          className={`badge ${
                            aff.tier === "must" ? "red" : aff.tier === "nice" ? "amber" : "green"
                          }`}
                        >
                          {aff.tier === "must" ? "必保" : aff.tier === "nice" ? "增益" : "可砍"}
                        </span>
                      </td>
                      <td>
                        <div>
                          {aff.side === "prefix" ? "[前] " : aff.side === "suffix" ? "[后] " : ""}
                          {t.mod(aff.line)}
                        </div>
                        {aff.synergyTags && aff.synergyTags.length > 0 && (
                          <div className="chips" style={{ marginTop: 3 }}>
                            {aff.synergyTags.map((tag) => (
                              <span
                                key={tag}
                                className={`badge ${
                                  tag.includes("🌳") ? "purple" : tag.includes("💎") ? "gold" : ""
                                }`}
                                style={{ fontSize: 10.5 }}
                              >
                                {tag}
                              </span>
                            ))}
                          </div>
                        )}
                      </td>
                      <td>
                        <div className="delta-cell">
                          <span className={`delta-value ${aff.deltaDpsPct >= 2 ? "pos" : "muted"}`}>
                            {fmtDeltaPct(aff.deltaDpsPct)}
                          </span>
                          {Math.abs(aff.deltaDpsPct) >= 0.5 && (
                            <div className="delta-bar-track">
                              <div
                                className={`delta-bar-fill ${aff.deltaDpsPct >= 0 ? "dps" : "neg"}`}
                                style={{ width: `${Math.min(100, Math.max(5, Math.round(Math.abs(aff.deltaDpsPct) * 3)))}%` }}
                              />
                            </div>
                          )}
                        </div>
                      </td>
                      <td>
                        <div className="delta-cell">
                          <span className={`delta-value ${aff.deltaEhpPct >= 2 ? "pos" : "muted"}`}>
                            {fmtDeltaPct(aff.deltaEhpPct)}
                          </span>
                          {Math.abs(aff.deltaEhpPct) >= 0.5 && (
                            <div className="delta-bar-track">
                              <div
                                className={`delta-bar-fill ${aff.deltaEhpPct >= 0 ? "ehp" : "neg"}`}
                                style={{ width: `${Math.min(100, Math.max(5, Math.round(Math.abs(aff.deltaEhpPct) * 3)))}%` }}
                              />
                            </div>
                          )}
                        </div>
                      </td>
                      <td>
                        {aff.triggeredRedlines && aff.triggeredRedlines.length > 0 ? (
                          <div className="chips">
                            {aff.triggeredRedlines.map((r, i) => (
                              <span key={i} className="badge red">
                                {r.message}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="muted">无红线断裂</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </section>
      )}

      {/* 3. 预算滑块最优升级路线（0-1 背包） */}
      <section className="card">
        <div className="card-header">
          <span className="card-title">🧭 预算档位最优升级路线（0-1 背包 + 阶段排序）</span>
          <div className="row">
            {BUDGET_PRESETS.map((p) => (
              <button
                key={p}
                type="button"
                className={`btn sm ${budget === p ? "primary" : ""}`}
                onClick={() => setBudget(p)}
              >
                {p} D
              </button>
            ))}
          </div>
        </div>

        <div className="row between" style={{ marginBottom: 10 }}>
          <div style={{ flex: 1, minWidth: 220 }}>
            <input
              type="range"
              className="slider"
              min={0}
              max={200}
              step={1}
              value={Math.min(200, budget)}
              onChange={(e) => setBudget(Number(e.target.value))}
            />
          </div>
          <div className="row">
            <span className="muted">当前预算：</span>
            <input
              type="number"
              className="input"
              style={{ width: 78 }}
              min={0}
              value={budget}
              onChange={(e) => setBudget(Math.max(0, Number(e.target.value) || 0))}
            />
            <span className="muted">D</span>
          </div>
        </div>

        {roadmap && (
          <>
            <div className="grid-4" style={{ marginBottom: 12 }}>
              <div className="kpi-card">
                <div className="kpi-label">计划花费 / 剩余预算</div>
                <div className="kpi-value">
                  {roadmap.spentDivine} D <span className="muted">/ 剩 {roadmap.remainingDivine} D</span>
                </div>
              </div>
              <div className="kpi-card">
                <div className="kpi-label">
                  {roadmap.verifiedCombo ? "合穿实算 ΔDPS%（线性）" : "累计预期 ΔDPS%"}
                </div>
                <div className="kpi-value pos">
                  {roadmap.verifiedCombo
                    ? fmtDeltaPct(roadmap.verifiedCombo.deltaDpsPct)
                    : fmtDeltaPct(roadmap.expectedDeltaDpsPct)}
                  {roadmap.verifiedCombo && (
                    <span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>
                      (线性 {fmtDeltaPct(roadmap.verifiedCombo.linearDeltaDpsPct)})
                    </span>
                  )}
                </div>
              </div>
              <div className="kpi-card">
                <div className="kpi-label">
                  {roadmap.verifiedCombo ? "合穿实算 ΔEHP%（红线）" : "累计预期 ΔEHP%"}
                </div>
                <div className="kpi-value pos">
                  {roadmap.verifiedCombo
                    ? fmtDeltaPct(roadmap.verifiedCombo.deltaEhpPct)
                    : fmtDeltaPct(roadmap.expectedDeltaEhpPct)}
                  {roadmap.verifiedCombo && (
                    <span
                      className={`badge ${roadmap.verifiedCombo.allGreen ? "green" : "red"}`}
                      style={{ fontSize: 10, marginLeft: 6, verticalAlign: "middle" }}
                    >
                      {roadmap.verifiedCombo.allGreen
                        ? "全绿"
                        : `${roadmap.verifiedCombo.redlines.length} 红线`}
                    </span>
                  )}
                </div>
              </div>
              <div className="kpi-card">
                <div className="kpi-label">待补价高价值槽位</div>
                <div className="kpi-value" style={{ color: "var(--amber)" }}>
                  {roadmap.unpriced.length} 件 {planning ? "…" : ""}
                </div>
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {roadmap.phases.map((ph) => (
                <div key={ph.phase} className="kpi-card">
                  <div className="row between" style={{ marginBottom: 6 }}>
                    <div>
                      <strong style={{ color: "var(--gold-strong)" }}>{ph.title}</strong>
                      <div className="muted">{ph.subtitle}</div>
                    </div>
                    <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                      <span className="badge gold">花费 {ph.costDivine} D</span>
                      {ph.verifiedCombo ? (
                        <>
                          <span
                            className="badge green"
                            title={`PoB2 合穿实算（${ph.verifiedCombo.cumulative ? "含前序阶段累计" : "本阶段合穿"}）：${fmtDeltaPct(
                              ph.verifiedCombo.deltaDpsPct
                            )} DPS，相对线性累加 ${fmtDeltaPct(ph.verifiedCombo.dpsSynergyDiffPct)}`}
                          >
                            🧪 实算 ΔDPS {fmtDeltaPct(ph.verifiedCombo.deltaDpsPct)}
                          </span>
                          <span className="badge blue">
                            🧪 实算 ΔEHP {fmtDeltaPct(ph.verifiedCombo.deltaEhpPct)}
                          </span>
                          {!ph.verifiedCombo.allGreen && (
                            <span
                              className="badge red"
                              title={ph.verifiedCombo.redlines.map((r) => r.message).join("；")}
                            >
                              ⚠️ 合穿 {ph.verifiedCombo.redlines.length} 条红线
                            </span>
                          )}
                        </>
                      ) : (
                        <>
                          <span className="badge green">ΔDPS {fmtDeltaPct(ph.expectedDeltaDpsPct)}</span>
                          <span className="badge blue">ΔEHP {fmtDeltaPct(ph.expectedDeltaEhpPct)}</span>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="chips">
                    {ph.steps.map((s) => (
                      <span key={s.slot} className="badge">
                        {t.slot(s.slot)} · {s.priceDivine ? `${s.priceDivine}D` : "0D"} ({fmtDeltaPct(s.deltaDpsPct)} DPS /{" "}
                        {fmtDeltaPct(s.deltaEhpPct)} EHP)
                      </span>
                    ))}
                  </div>
                </div>
              ))}

              {roadmap.unpriced.length > 0 && (
                <div className="banner info">
                  <span>
                    有 {roadmap.unpriced.length} 个有正提升的槽位尚未填价（
                    {roadmap.unpriced.map((u) => u.slot).join("、")}
                    ），可在浏览器扩展批量核价推送，或在上方表格手动填入估算 D 价。
                  </span>
                </div>
              )}

              {roadmap.deferred.length > 0 && (
                <div className="muted">
                  当前预算暂缓项（后期再补）：
                  {roadmap.deferred.map((d) => `${d.slot} (${d.priceDivine}D)`).join(" · ")}
                </div>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
};
