import React, { useEffect, useState } from "react";
import type {
  BilingualTranslator,
  CopyPlanActionKind,
  CopyPlanAffix,
  CopyPlanItem,
  CopyPlanItemUserState,
  CopyPlanStructured,
  SlotAffixReport,
} from "@contracts";
import { desktopApi } from "../api/workbenchApi";
import { buildCopyPlanItemKey } from "../utils/copyPlanStorage";
import { buildTradeUrlFromCopyItem, fmtCompact, openExternalUrl, openExternalUrlAsync } from "../utils/handoffParser";

interface CopyPlanActionListProps {
  t: BilingualTranslator;
  league: string;
  copyPlan: CopyPlanStructured;
  itemStates?: Record<string, CopyPlanItemUserState>;
  onItemStateChange?: (itemKey: string, patch: Partial<CopyPlanItemUserState>) => void;
  onOpenCraftWithSlot: (rep: SlotAffixReport) => void;
}

const ACTION_META: Record<CopyPlanActionKind, { label: string; cls: string; tradeKind: "exact" | "budget" | "semi" | "base" }> = {
  buy_unique: { label: "必买暗金", cls: "unique", tradeKind: "exact" },
  buy_finished: { label: "直买成品", cls: "green", tradeKind: "exact" },
  buy_budget: { label: "搜平替", cls: "gold", tradeKind: "budget" },
  craft_semi: { label: "买半成品", cls: "purple", tradeKind: "semi" },
  buy_base: { label: "搜白底", cls: "blue", tradeKind: "base" },
  skip: { label: "本档暂缓", cls: "", tradeKind: "budget" },
};

function fmtBudgetD(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v) || v <= 0) return "";
  return v >= 10 ? `~${Math.round(v * 10) / 10}D` : `~${Math.round(v * 100) / 100}D`;
}

function buildCraftSeedFromCopyItem(item: CopyPlanItem, checkedAffixes: CopyPlanAffix[]): SlotAffixReport {
  const bpTerms = (item.searchBlueprint?.weightedGroups || [])
    .flatMap((g) => g.terms || [])
    .slice(0, 4)
    .map((x) => x.text || x.zh || "")
    .filter(Boolean);
  const activeAffixes = checkedAffixes.length > 0 ? checkedAffixes : item.selectedAffixes || [];
  const lines = activeAffixes.length > 0 ? activeAffixes.map((a) => a.text || a.zh || "").filter(Boolean) : bpTerms;
  return {
    slot: item.slot,
    itemName: item.itemName,
    baseType: item.baseType || "",
    rarity: item.rarity || "RARE",
    baseDps: 0,
    baseEhp: 0,
    swappedDps: 0,
    swappedEhp: 0,
    deltaDpsPct: 0,
    deltaEhpPct: 0,
    redlines: [],
    affixes: lines.map((line) => ({
      line,
      tier: "must" as const,
      deltaDpsPct: 0,
      deltaEhpPct: 0,
      triggeredRedlines: [],
      origin: "explicit" as const,
      side: "prefix" as const,
      craftDifficulty: "medium" as const,
      synergyTags: [],
    })),
    mustAffixes: lines,
    cuttableAffixes: item.cutAffixes || [],
    semiPlan: {
      lockAffixes: lines.slice(0, 2),
      summary: item.reason,
      requireBaseType: Boolean(item.lockBase),
      requireSockets: item.minSockets ?? null,
      notes: item.socketTip ? [item.socketTip] : [],
    },
    elapsedMs: 0,
  };
}

const CopyPlanActionListInner: React.FC<CopyPlanActionListProps> = ({
  t,
  league,
  copyPlan,
  itemStates,
  onItemStateChange,
  onOpenCraftWithSlot,
}) => {
  const items = copyPlan.items || [];
  const activeLeague = copyPlan.league || league || "Standard";

  // 本地兜底状态（当外部传入 itemStates + onItemStateChange 时，以持久化状态为准并实时落盘）
  const [checkedByCard, setCheckedByCard] = useState<Record<number, Record<number, boolean>>>({});
  const [disabledWeightedByCard, setDisabledWeightedByCard] = useState<Record<number, Record<string, boolean>>>({});
  const [scoreScaleByCard, setScoreScaleByCard] = useState<Record<number, number>>({});
  const [lockBaseByCard, setLockBaseByCard] = useState<Record<number, boolean>>({});
  const [purchasedByCard, setPurchasedByCard] = useState<Record<number, boolean>>({});
  const [hidePurchased, setHidePurchased] = useState(false);

  useEffect(() => {
    const nextChecked: Record<number, Record<number, boolean>> = {};
    const nextDisabled: Record<number, Record<string, boolean>> = {};
    const nextScale: Record<number, number> = {};
    const nextLock: Record<number, boolean> = {};
    const nextPurchased: Record<number, boolean> = {};
    items.forEach((item, cIdx) => {
      const key = buildCopyPlanItemKey(item, cIdx);
      const saved = itemStates?.[key];
      const m: Record<number, boolean> = {};
      (item.selectedAffixes || []).forEach((aff, aIdx) => {
        m[aIdx] = saved?.checkedAffixes?.[aIdx] ?? aff.required !== false;
      });
      nextChecked[cIdx] = m;
      nextDisabled[cIdx] = saved?.disabledWeightedIds ? { ...saved.disabledWeightedIds } : {};
      if (typeof saved?.scoreScale === "number") nextScale[cIdx] = saved.scoreScale;
      nextLock[cIdx] = typeof saved?.lockBase === "boolean" ? saved.lockBase : Boolean(item.lockBase);
      nextPurchased[cIdx] = Boolean(saved?.purchased);
    });
    setCheckedByCard(nextChecked);
    setDisabledWeightedByCard(nextDisabled);
    setScoreScaleByCard(nextScale);
    setLockBaseByCard(nextLock);
    setPurchasedByCard(nextPurchased);
  }, [copyPlan, itemStates]);

  if (items.length === 0) return null;

  function itemKeyOf(item: CopyPlanItem, cardIdx: number): string {
    return buildCopyPlanItemKey(item, cardIdx);
  }

  function isCardPurchased(item: CopyPlanItem, cardIdx: number): boolean {
    const key = itemKeyOf(item, cardIdx);
    if (itemStates && key in itemStates && typeof itemStates[key]?.purchased === "boolean") {
      return Boolean(itemStates[key]?.purchased);
    }
    return Boolean(purchasedByCard[cardIdx]);
  }

  function togglePurchased(item: CopyPlanItem, cardIdx: number) {
    const next = !isCardPurchased(item, cardIdx);
    setPurchasedByCard((prev) => ({ ...prev, [cardIdx]: next }));
    onItemStateChange?.(itemKeyOf(item, cardIdx), { purchased: next });
  }

  function toggleAffix(item: CopyPlanItem, cardIdx: number, affixIdx: number) {
    const key = itemKeyOf(item, cardIdx);
    const prevMap = itemStates?.[key]?.checkedAffixes || checkedByCard[cardIdx] || {};
    const curVal = prevMap[affixIdx] ?? item.selectedAffixes?.[affixIdx]?.required !== false;
    const nextMap = { ...prevMap, [affixIdx]: !curVal };
    setCheckedByCard((prev) => ({ ...prev, [cardIdx]: nextMap }));
    onItemStateChange?.(key, { checkedAffixes: nextMap });
  }

  function toggleWeightedTerm(item: CopyPlanItem, cardIdx: number, statId: string) {
    const key = itemKeyOf(item, cardIdx);
    const prevMap = itemStates?.[key]?.disabledWeightedIds || disabledWeightedByCard[cardIdx] || {};
    const nextMap = { ...prevMap, [statId]: !(prevMap[statId] ?? false) };
    setDisabledWeightedByCard((prev) => ({ ...prev, [cardIdx]: nextMap }));
    onItemStateChange?.(key, { disabledWeightedIds: nextMap });
  }

  function setCardScale(item: CopyPlanItem, cardIdx: number, scale: number) {
    setScoreScaleByCard((prev) => ({ ...prev, [cardIdx]: scale }));
    onItemStateChange?.(itemKeyOf(item, cardIdx), { scoreScale: scale });
  }

  function toggleLockBase(item: CopyPlanItem, cardIdx: number) {
    const key = itemKeyOf(item, cardIdx);
    const cur =
      typeof itemStates?.[key]?.lockBase === "boolean"
        ? Boolean(itemStates[key]?.lockBase)
        : lockBaseByCard[cardIdx] ?? Boolean(item.lockBase);
    const next = !cur;
    setLockBaseByCard((prev) => ({ ...prev, [cardIdx]: next }));
    onItemStateChange?.(key, { lockBase: next });
  }

  function getCheckedAffixes(item: CopyPlanItem, cardIdx: number): CopyPlanAffix[] {
    const key = itemKeyOf(item, cardIdx);
    const map = itemStates?.[key]?.checkedAffixes || checkedByCard[cardIdx] || {};
    return (item.selectedAffixes || []).filter((aff, aIdx) => map[aIdx] ?? aff.required !== false);
  }

  function resolveCurrentUrl(item: CopyPlanItem, cardIdx: number): string {
    const key = itemKeyOf(item, cardIdx);
    const checked = getCheckedAffixes(item, cardIdx);
    const lockBase =
      typeof itemStates?.[key]?.lockBase === "boolean"
        ? Boolean(itemStates[key]?.lockBase)
        : lockBaseByCard[cardIdx] ?? Boolean(item.lockBase);
    const disabledMap = itemStates?.[key]?.disabledWeightedIds || disabledWeightedByCard[cardIdx] || {};
    const disabledSet = new Set(Object.entries(disabledMap).filter(([, off]) => off).map(([id]) => id));
    const scoreScale = itemStates?.[key]?.scoreScale ?? scoreScaleByCard[cardIdx] ?? 1.0;
    return buildTradeUrlFromCopyItem(item, activeLeague, checked, lockBase, {
      disabledWeightedIds: disabledSet,
      scoreScale,
    });
  }

  function formatAffixLabel(aff: CopyPlanAffix): string {
    const primary =
      t.mode === "en"
        ? aff.text || aff.statText || aff.zh || ""
        : aff.zh || (aff.text ? t.mod(aff.text) : aff.statText || "");
    const hasMin = aff.min !== null && aff.min !== undefined && Number.isFinite(Number(aff.min));
    if (hasMin && !primary.includes(String(aff.min))) {
      return `${primary} ≥ ${aff.min}`;
    }
    return primary;
  }

  const actionableCount = items.filter((x) => x.action !== "skip").length;
  const purchasedCount = items.filter((x, idx) => x.action !== "skip" && isCardPurchased(x, idx)).length;

  return (
    <div className="copy-plan-box">
      <div className="row between" style={{ marginBottom: 8, gap: 8 }}>
        <div className="row" style={{ gap: 6 }}>
          <strong style={{ color: "var(--gold-strong)", fontSize: 13 }}>
            🛒 AI 推荐选词 &amp; 一键直达市场（{actionableCount} 件可操作）
          </strong>
          {copyPlan.characterLevel ? <span className="badge">Lv.{copyPlan.characterLevel}</span> : null}
          {copyPlan.budgetDivine != null ? <span className="badge gold">预算 {copyPlan.budgetDivine}D</span> : null}
          <span className="badge blue">赛季：{activeLeague}</span>
          {actionableCount > 0 && (
            <span className={`badge ${purchasedCount === actionableCount ? "green" : purchasedCount > 0 ? "gold" : ""}`}>
              ✓ 已入手 {purchasedCount}/{actionableCount}
            </span>
          )}
        </div>
        <div className="row" style={{ gap: 6 }}>
          {purchasedCount > 0 && (
            <button
              type="button"
              className={`btn sm ${hidePurchased ? "primary" : ""}`}
              style={{ fontSize: 10.5, padding: "2px 8px" }}
              onClick={() => setHidePurchased((v) => !v)}
            >
              {hidePurchased ? `显示已入手 (${purchasedCount})` : "隐藏已入手"}
            </button>
          )}
          <span className="muted" style={{ fontSize: 11 }}>
            挑完可勾选「已入手」，词条微调与进度均自动保存本地
          </span>
        </div>
      </div>

      {copyPlan.budgetSummary && (
        <div className="banner info" style={{ marginBottom: 10, padding: "7px 11px", fontSize: 12 }}>
          <span>💡 {copyPlan.budgetSummary}</span>
        </div>
      )}

      {/* PoB2 沙盒整套终稿实测验收条 */}
      {copyPlan.pobVerification && (
        <div
          className={`banner ${copyPlan.pobVerification.allGreen ? "success" : "info"}`}
          style={{ marginBottom: 10, padding: "8px 11px", fontSize: 12, flexDirection: "column", alignItems: "flex-start", gap: 6 }}
        >
          <div className="row between" style={{ width: "100%", flexWrap: "wrap", gap: 6 }}>
            <strong>
              🧪 PoB2 沙盒整套实测验收
              {copyPlan.pobVerification.replacedSlots?.length
                ? `（已替换 ${copyPlan.pobVerification.replacedSlots.length} 件平替）`
                : ""}
            </strong>
            <div className="chips">
              <span className="badge gold">
                DPS {fmtCompact(copyPlan.pobVerification.dps)}（原 BD {copyPlan.pobVerification.dpsPctOfTarget}%）
              </span>
              <span className="badge green">
                EHP {fmtCompact(copyPlan.pobVerification.ehp)}（原 BD {copyPlan.pobVerification.ehpPctOfTarget}%）
              </span>
              <span className="badge blue">
                抗性 火{Math.round(copyPlan.pobVerification.fireRes)} / 冰{Math.round(copyPlan.pobVerification.coldRes)} / 电
                {Math.round(copyPlan.pobVerification.lightningRes)} / 混{Math.round(copyPlan.pobVerification.chaosRes)}
              </span>
              <span className="badge">精魂余 {Math.round(copyPlan.pobVerification.spiritUnreserved)}</span>
              {copyPlan.pobVerification.allGreen ? (
                <span className="badge green">✓ 红线全绿</span>
              ) : (
                <span className="badge red">⚠ {copyPlan.pobVerification.redlines.join(" · ")}</span>
              )}
            </div>
          </div>
          {copyPlan.toolTrace && copyPlan.toolTrace.length > 0 && (
            <details style={{ fontSize: 11, color: "var(--muted)", width: "100%" }}>
              <summary style={{ cursor: "pointer" }}>
                查看 AI 调用 PoB2 沙盒记录（{copyPlan.toolTrace.length} 次实测）
              </summary>
              <div style={{ marginTop: 4, display: "flex", flexDirection: "column", gap: 2 }}>
                {copyPlan.toolTrace.map((tr, i) => (
                  <div key={i}>
                    • 第 {tr.round} 轮 [{tr.tool}]：{tr.summary}
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}

      <div className="grid-2">
        {items.map((item, cardIdx) => {
          const meta = ACTION_META[item.action] || ACTION_META.buy_budget;
          const isUnique = (item.rarity || "").toUpperCase() === "UNIQUE" || item.action === "buy_unique";
          const isSkip = item.action === "skip";
          const isPurchased = !isSkip && isCardPurchased(item, cardIdx);
          if (hidePurchased && isPurchased) return null;

          const itemKey = itemKeyOf(item, cardIdx);
          const bp = item.searchBlueprint || null;
          const isBlueprint = Boolean(bp && !isUnique && item.action !== "buy_base");
          const scoreScale = itemStates?.[itemKey]?.scoreScale ?? scoreScaleByCard[cardIdx] ?? 1.0;
          const disabledMap = itemStates?.[itemKey]?.disabledWeightedIds || disabledWeightedByCard[cardIdx] || {};
          const checkedAffixes = getCheckedAffixes(item, cardIdx);
          const activeStatCount = isBlueprint
            ? (bp?.mandatoryHard?.length || 0) +
              (bp?.mandatoryAnyOf?.reduce((acc, g) => acc + (g.terms?.length || 0), 0) || 0) +
              (bp?.weightedGroups?.reduce(
                (acc, g) => acc + (g.terms || []).filter((tm) => !disabledMap[tm.statId]).length,
                0
              ) || 0)
            : checkedAffixes.filter((a) => Boolean(a.statId)).length;
          const lockBase =
            typeof itemStates?.[itemKey]?.lockBase === "boolean"
              ? Boolean(itemStates[itemKey]?.lockBase)
              : lockBaseByCard[cardIdx] ?? Boolean(item.lockBase);
          const budgetTxt = fmtBudgetD(item.budgetDivine);

          return (
            <div
              key={`${item.slot}-${item.itemName}-${cardIdx}`}
              className={`copy-action-card ${isSkip ? "is-skip" : ""} ${isPurchased ? "is-purchased" : ""}`}
            >
              {/* 第 1 行：优先级 + 动作 + 槽位 + 名称 + 预算 + 已入手勾选 */}
              <div className="row between" style={{ gap: 6, flexWrap: "nowrap" }}>
                <div className="row" style={{ gap: 5, minWidth: 0, flexWrap: "nowrap" }}>
                  <span className="badge" style={{ flexShrink: 0, padding: "1px 6px" }}>
                    #{item.priority || cardIdx + 1}
                  </span>
                  <span className={`badge ${meta.cls}`} style={{ flexShrink: 0 }}>
                    {meta.label}
                  </span>
                  <span className="badge blue" style={{ flexShrink: 0 }}>
                    {t.slot(item.slot)}
                  </span>
                  <strong className="ellipsis-text" style={{ color: "#fff", fontSize: 12.5 }}>
                    {t.itemName(item.itemName, item.baseType)}
                  </strong>
                </div>
                <div className="row" style={{ gap: 5, flexShrink: 0, flexWrap: "nowrap" }}>
                  {budgetTxt && (
                    <span className="badge gold" style={{ flexShrink: 0 }}>
                      {budgetTxt}
                    </span>
                  )}
                  {!isSkip && (
                    <button
                      type="button"
                      className={`btn sm ${isPurchased ? "success" : ""}`}
                      style={{ fontSize: 10.5, padding: "2px 7px", flexShrink: 0 }}
                      title={isPurchased ? "点击取消「已入手」标记" : "挑好/买完这件装备后勾选，进度自动存本地"}
                      onClick={() => togglePurchased(item, cardIdx)}
                    >
                      {isPurchased ? "✓ 已入手" : "☐ 已入手"}
                    </button>
                  )}
                </div>
              </div>

              {/* 第 2 行：AI 选词理由 + 硬门槛标签 */}
              {item.reason && (
                <div className="muted" style={{ fontSize: 11.5, marginTop: 5, lineHeight: 1.45 }}>
                  {item.reason}
                </div>
              )}

              {(item.minIlvl || (item.minSockets && item.minSockets >= 2) || item.corrupted === false || item.socketTip) && (
                <div className="chips" style={{ marginTop: 5 }}>
                  {item.minIlvl ? <span className="metric-pill">iLvl ≥ {item.minIlvl}</span> : null}
                  {item.minSockets && item.minSockets >= 2 ? (
                    <span className="metric-pill warn">≥ {item.minSockets} 符文孔</span>
                  ) : null}
                  {item.corrupted === false ? <span className="metric-pill def">非腐化</span> : null}
                  {item.socketTip ? <span className="metric-pill roi">💎 {item.socketTip}</span> : null}
                </div>
              )}

              {/* 第 3 行：新版四段式倒推加权蓝图（面板底线 + 硬绑定 + OR核心 + 攻/防加权 OR 池） */}
              {isBlueprint && bp && (
                <div style={{ marginTop: 7, display: "flex", flexDirection: "column", gap: 5 }}>
                  {(Object.keys(bp.equipmentFloors || {}).length > 0 ||
                    (bp.mandatoryHard?.length || 0) > 0 ||
                    (bp.mandatoryAnyOf?.length || 0) > 0) && (
                    <div className="chips">
                      {Object.entries(bp.equipmentFloors || {}).map(([ek, ev]) => {
                        const txt =
                          ek === "reload_time"
                            ? `装填 ≤ ${Math.round(ev * (1.0 + Math.max(0, 1.0 - scoreScale) * 0.25) * 100) / 100}s`
                            : ek === "edps_max"
                            ? `元素DPS ≤ ${Math.ceil(ev)}`
                            : ek === "pdps_max"
                            ? `物理DPS ≤ ${Math.ceil(ev)}`
                            : `${ek === "damage" ? "单发均伤" : `面板 ${ek.toUpperCase()}`} ≥ ${
                                ek === "aps" || ek === "crit"
                                  ? Math.round(ev * (0.95 + 0.05 * scoreScale) * 100) / 100
                                  : Math.floor(ev * Math.min(1.1, Math.max(0.75, scoreScale)))
                              }`;
                        return (
                          <span key={`eq-${ek}`} className="badge green" style={{ fontSize: 10.5 }}>
                            {txt}
                          </span>
                        );
                      })}
                      {(bp.mandatoryHard || []).map((mh, i) => (
                        <span key={`mh-${i}`} className="badge red" style={{ fontSize: 10.5 }}>
                          🔒 必保：{mh.zh || mh.text}
                          {mh.min != null ? ` ≥ ${mh.min}` : ""}
                        </span>
                      ))}
                      {(bp.mandatoryAnyOf || []).map((ao, i) => (
                        <span
                          key={`ao-${i}`}
                          className="badge purple"
                          style={{ fontSize: 10.5 }}
                          title={(ao.terms || []).map((x) => `${x.zh || x.text}${x.min != null ? `≥${x.min}` : ""}`).join(" OR ")}
                        >
                          OR 核心：{ao.label || (ao.terms || []).map((x) => x.zh || x.text).join(" / ")}
                        </span>
                      ))}
                    </div>
                  )}

                  {(bp.weightedGroups || []).map((wg, gIdx) => (
                    <div key={`wg-${gIdx}`}>
                      <div className="row between" style={{ marginBottom: 3 }}>
                        <span className="muted" style={{ fontSize: 10.5 }}>
                          ⚖️ {wg.label || "等价加权池 (OR)"}
                          {wg.minScore != null
                            ? `（达标分 ≥ ${Math.round(wg.minScore * scoreScale * 10) / 10}）`
                            : "（加权排序）"}
                        </span>
                        {gIdx === 0 && (
                          <div className="row" style={{ gap: 3 }}>
                            {[
                              { label: "宽松", val: 0.8 },
                              { label: "标准", val: 1.0 },
                              { label: "严格", val: 1.15 },
                            ].map((opt) => (
                              <button
                                key={opt.label}
                                type="button"
                                className={`btn sm ${Math.abs(scoreScale - opt.val) < 0.01 ? "primary" : ""}`}
                                style={{ fontSize: 10, padding: "1px 5px" }}
                                onClick={() => setCardScale(item, cardIdx, opt.val)}
                              >
                                {opt.label}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="chips">
                        {(wg.terms || []).map((tm) => {
                          const isOn = !disabledMap[tm.statId];
                          return (
                            <button
                              key={`${gIdx}-${tm.statId}`}
                              type="button"
                              className={`affix-toggle-chip ${isOn ? "on" : "off"} mapped`}
                              title={`OR 等价加权词条：${tm.statText || tm.text || tm.statId}（权重 ×${tm.weight ?? 1}，点击可排除）`}
                              onClick={() => toggleWeightedTerm(item, cardIdx, tm.statId)}
                            >
                              <span>{isOn ? "☑" : "☐"}</span>
                              <span>
                                {tm.zh || tm.text} ×{tm.weight ?? 1}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* 旧版兼容：可交互勾选的 AI 推荐词条 */}
              {!isBlueprint && (item.selectedAffixes || []).length > 0 && (
                <div style={{ marginTop: 7 }}>
                  <div className="muted" style={{ fontSize: 10.5, marginBottom: 4 }}>
                    应选词条（点击可开关筛选，已生效 {activeStatCount} 条市集词缀）：
                  </div>
                  <div className="chips">
                    {item.selectedAffixes.map((aff, aIdx) => {
                      const isOn =
                        itemStates?.[itemKey]?.checkedAffixes?.[aIdx] ??
                        checkedByCard[cardIdx]?.[aIdx] ??
                        aff.required !== false;
                      const mapped = Boolean(aff.statId);
                      return (
                        <button
                          key={`${aff.text}-${aIdx}`}
                          type="button"
                          className={`affix-toggle-chip ${isOn ? "on" : "off"} ${mapped ? "mapped" : "unmapped"}`}
                          title={
                            mapped
                              ? `已映射官方 Trade2：${aff.statText || aff.statId}（点击切换是否带入搜索）`
                              : `未自动匹配到 Trade2 statId（原文：${aff.text}），直达市集后可手动补充`
                          }
                          onClick={() => toggleAffix(item, cardIdx, aIdx)}
                        >
                          <span>{isOn ? "☑" : "☐"}</span>
                          <span>{formatAffixLabel(aff)}</span>
                          {!mapped && <span className="affix-unmapped-dot">?</span>}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* 可砍词条提醒 */}
              {(item.cutAffixes || []).length > 0 && (
                <div className="muted" style={{ fontSize: 10.5, marginTop: 5 }}>
                  ✂️ 可砍压价：{item.cutAffixes!.join(" · ")}
                </div>
              )}

              {/* 第 4 行：一键直达市集 / 带入做装台 / 锁基底切换 */}
              {!isSkip && (
                <div className="row between" style={{ marginTop: 8, gap: 6 }}>
                  <div className="row" style={{ gap: 5 }}>
                    <button
                      type="button"
                      className={`btn sm ${isBlueprint ? "success" : "primary"}`}
                      onClick={() => openExternalUrl(resolveCurrentUrl(item, cardIdx))}
                    >
                      {isUnique || item.action === "buy_base"
                        ? "🛒 一键直达市场"
                        : isBlueprint
                        ? `⚖️ 加权直达市集${activeStatCount > 0 ? ` (${activeStatCount}项OR)` : ""}`
                        : `🛒 按勾选词条搜${activeStatCount > 0 ? ` (${activeStatCount}词)` : ""}`}
                    </button>
                    {!isUnique && item.action !== "buy_base" && !isBlueprint && (
                      <button
                        type="button"
                        className="btn sm success"
                        title="按 PoB2 倒推灵敏度自动算全槽位等价权重（官方 Trade2 Weighted Sum），达标后按价低排序直达市集"
                        onClick={() =>
                          void openExternalUrlAsync(async () => {
                            const built = await desktopApi.buildTradeUrl({
                              league: activeLeague,
                              slot: item.slot,
                              itemName: item.itemName,
                              baseType: item.baseType || "",
                              rarity: item.rarity || "RARE",
                              action: item.action,
                              lockBase,
                              useWeighted: true,
                            });
                            return built.tradeUrl || resolveCurrentUrl(item, cardIdx);
                          }).catch(() => {
                            openExternalUrl(resolveCurrentUrl(item, cardIdx));
                          })
                        }
                      >
                        ⚖️ 倒推加权搜
                      </button>
                    )}
                    {!isUnique && (
                      <button
                        type="button"
                        className="btn sm"
                        title="把 AI 推荐词条带入做装对比台，计算买成品 vs 半成品加工"
                        onClick={() => onOpenCraftWithSlot(buildCraftSeedFromCopyItem(item, checkedAffixes))}
                      >
                        ⚒️ 做装比价
                      </button>
                    )}
                  </div>

                  {!isUnique && item.baseType && (
                    <button
                      type="button"
                      className={`btn sm ${lockBase ? "success" : ""}`}
                      style={{ fontSize: 10.5, padding: "3px 7px" }}
                      title={
                        lockBase
                          ? `当前锁定基底「${item.baseType}」，点此切换为按装备类别宽松搜`
                          : `当前按装备类别宽松搜索（防筛空），点此锁定基底「${item.baseType}」`
                      }
                      onClick={() => toggleLockBase(item, cardIdx)}
                    >
                      {lockBase ? `🔒 锁基底` : `🔓 不锁基底`}
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export const CopyPlanActionList = React.memo(CopyPlanActionListInner);
