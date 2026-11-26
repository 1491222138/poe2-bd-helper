import React from "react";
import type { BilingualTranslator, ItemAblationDetail, SlotAffixReport } from "@contracts";
import { openExternalUrl } from "../../utils/handoffParser";
import {
  buildCraftSeedFromDossier,
  COPY_ACTION_LABEL,
  COPY_TIER_CLASS,
  fmtD,
  fmtLoss,
  ORIGIN_TAG,
  priceSourceBadgeLabel,
  PURPOSE_BADGE_CLASS,
  ROLE_BADGE_CLASS,
  type ResolvedTradeMeta,
} from "./dossierUtils";

interface DossierItemDetailProps {
  t: BilingualTranslator;
  league: string;
  item: ItemAblationDetail;
  itemKey: string;
  resolved?: ResolvedTradeMeta;
  openingTradeKey: string | null;
  slotTradeLink?: { url: string; label: string };
  manualPriceValue: string;
  slotPriceMsg?: string;
  simDps: number;
  simEhp: number;
  detailPanelRef: React.RefObject<HTMLDivElement | null>;
  onTradeDirect: (item: ItemAblationDetail, itemKey: string, kind: "budget" | "semi") => void;
  onOpenCraftWithSlot: (rep: SlotAffixReport) => void;
  onManualPriceChange: (slot: string, value: string) => void;
  onSaveManualPrice: (slot: string, itemName: string) => Promise<void>;
  onClose: () => void;
}

const DossierItemDetailInner: React.FC<DossierItemDetailProps> = ({
  t,
  league,
  item,
  itemKey,
  resolved,
  openingTradeKey,
  slotTradeLink,
  manualPriceValue,
  slotPriceMsg,
  simDps,
  simEhp,
  detailPanelRef,
  onTradeDirect,
  onOpenCraftWithSlot,
  onManualPriceChange,
  onSaveManualPrice,
  onClose,
}) => {
  const isUnique = (item.rarity || "").toUpperCase() === "UNIQUE";
  const cost = item.cost || { estimatedDivine: null, priceSource: "unknown" };
  const affixes = item.affixes || [];
  const synergies = item.passiveSynergies || [];
  const hasCompDiff =
    item.compensatedDpsLossPct != null && Math.abs((item.dpsLossPct ?? 0) - item.compensatedDpsLossPct) >= 5;

  return (
    <div ref={detailPanelRef} className="dossier-detail-panel inline-row-detail">
      <div className="row between" style={{ marginBottom: 8 }}>
        <div className="row" style={{ gap: 8 }}>
          <span className={`badge ${isUnique ? "unique" : "blue"}`}>{t.slot(item.slot)}</span>
          <strong style={{ fontSize: 14, color: "#fff" }}>{t.itemName(item.name, item.baseType)}</strong>
          <span className={`badge ${ROLE_BADGE_CLASS[item.roleKind] || ""}`}>{item.roleLabel}</span>
          <span className={`badge ${COPY_TIER_CLASS[item.copyTier] || ""}`}>{item.copyTierLabel}</span>
          {item.corrupted && <span className="badge red">已瓦尔</span>}
        </div>

        <div className="row" style={{ gap: 6 }}>
          <button
            type="button"
            className="btn sm primary"
            disabled={openingTradeKey === `${itemKey}:budget`}
            title={
              isUnique
                ? "打开当前赛季该暗金官方市集在售页（按价低排序）"
                : resolved?.mode === "weighted"
                ? `使用官方 Trade2 Weighted Sum 加权求和（门槛 ≥ ${resolved.minScore} / 目标 ${resolved.targetScore}），按价格升序直达市集`
                : resolved?.statCount
                ? `已自动预填 ${resolved.statCount} 条加权词缀，一键直达官方市集`
                : "直达官方市集加权平替搜索（自动带上 PoB2 消融权重）"
            }
            onClick={() => onTradeDirect(item, itemKey, "budget")}
          >
            {openingTradeKey === `${itemKey}:budget`
              ? "打开中..."
              : `🌐 ${isUnique ? "直达官方市集" : "直达加权搜平替"}${!isUnique && resolved?.statCount ? ` (${resolved.statCount}项)` : ""}`}
          </button>
          {!isUnique && (
            <button
              type="button"
              className="btn sm success"
              disabled={openingTradeKey === `${itemKey}:semi`}
              title="锁定基底 + 非腐化 + 仅锁 1~2 条最难核心词缀，直达官方市集买半成品胚子"
              onClick={() => onTradeDirect(item, itemKey, "semi")}
            >
              {openingTradeKey === `${itemKey}:semi` ? "打开中..." : "🧱 直达搜半成品"}
            </button>
          )}
          {!isUnique && item.kind === "item" && (
            <button
              type="button"
              className="btn sm"
              onClick={() => onOpenCraftWithSlot(buildCraftSeedFromDossier(item, simDps, simEhp))}
            >
              ⚒️ 带入做装台
            </button>
          )}
          <button type="button" className="btn sm" onClick={onClose}>
            ✕ 收起
          </button>
        </div>
      </div>

      {/* 直达市集作用说明 */}
      <div
        className="kpi-card"
        style={{ marginBottom: 10, padding: "7px 10px", background: "rgba(96, 165, 250, 0.06)" }}
      >
        {isUnique ? (
          <div style={{ fontSize: 11.5, color: "#cbd5e1", lineHeight: 1.55 }}>
            <strong style={{ color: "#93c5fd" }}>🌐 直达官方市集：</strong>
            打开 <strong>{league}</strong> 赛季该暗金的官方在售列表（按价格升序
            {item.runeImpact?.exceedsCraftableSockets ? `，已自动筛选 ≥ ${item.socketCount} 孔瓦尔多孔款` : ""}
            ）。暗金直接按需挑 roll 买成品即可，看清实价后可在下方「手填 D」直接更正本地造价。
          </div>
        ) : (
          <>
            <div style={{ fontSize: 11.5, color: "#cbd5e1", lineHeight: 1.55 }}>
              <div>
                <strong style={{ color: "#93c5fd" }}>🌐 直达加权搜平替：</strong>
                用官方 Trade2 <strong>加权求和 (Weighted Sum)</strong> 自动带上 PoB2 消融权重并按价低优先排序，不死锁单条词缀
                {resolved?.mode === "weighted" && resolved.minScore
                  ? `（目标分 ${resolved.targetScore} → 达标门槛 ≥ ${resolved.minScore}）`
                  : ""}
                ；打开市集后可微调权重底线，看中价格后顺手填入下方「手填 D」即自动更正本地造价。
              </div>
              <div style={{ marginTop: 2 }}>
                <strong style={{ color: "var(--green)" }}>🧱 直达搜半成品：</strong>
                锁定基底 + 非腐化 + 仅锁 1~2 条最难做核心词缀，方便买半成品胚子回「做装台」继续加工。
              </div>
            </div>
            {resolved?.mode === "weighted" &&
              ((resolved.weightedTerms?.length || 0) > 0 ||
                (resolved.mandatorySkills?.length || 0) > 0 ||
                Object.keys(resolved.equipmentFloors || {}).length > 0) && (
                <div className="chips" style={{ marginTop: 6 }}>
                  {(resolved.mandatorySkills || []).map((sk) => (
                    <span key={sk} className="badge red" style={{ fontSize: 10 }}>
                      必保技能：{sk}
                    </span>
                  ))}
                  {Object.entries(resolved.equipmentFloors || {}).map(([ek, ev]) => {
                    const label =
                      ek === "damage"
                        ? `单发均伤 ≥ ${ev}`
                        : ek === "reload_time"
                        ? `装填 ≤ ${ev}s`
                        : ek === "edps_max"
                        ? `元素DPS ≤ ${ev}`
                        : ek === "pdps_max"
                        ? `物理DPS ≤ ${ev}`
                        : `面板 ${ek.toUpperCase()} ≥ ${ev}`;
                    return (
                      <span key={ek} className="badge green" style={{ fontSize: 10 }}>
                        {label}
                      </span>
                    );
                  })}
                  {(resolved.weightedTerms || []).slice(0, 8).map((wt) => (
                    <span key={wt.statId} className={`badge ${wt.isResPool ? "blue" : "gold"}`} style={{ fontSize: 10.5 }}>
                      {wt.zh || wt.text} ×{wt.weight}
                    </span>
                  ))}
                </div>
              )}
          </>
        )}
      </div>

      {slotTradeLink && (
        <div className="banner info" style={{ marginBottom: 8, padding: "5px 10px", fontSize: 11.5 }}>
          <span>{slotTradeLink.label}</span>
          <a
            href={slotTradeLink.url}
            target="_blank"
            rel="noopener noreferrer"
            className="btn sm primary"
            style={{ textDecoration: "none", padding: "2px 8px", fontSize: 11 }}
          >
            ↗ 点此直接打开官方市集
          </a>
        </div>
      )}

      {/* 引擎实算摘要 + 造价 + 抄作业建议 */}
      <div className="grid-3" style={{ marginBottom: 10 }}>
        <div className="kpi-card">
          <div className="kpi-label">引擎脱装消融贡献</div>
          <div className="row" style={{ marginTop: 4, gap: 8 }}>
            <span className={(item.dpsLossPct ?? 0) >= 5 ? "pos" : "muted"}>DPS {fmtLoss(item.dpsLossPct)}</span>
            <span className={(item.ehpLossPct ?? 0) >= 5 ? "pos" : "muted"}>EHP {fmtLoss(item.ehpLossPct)}</span>
            {(item.manaLossPct ?? 0) >= 3 && <span className="muted">蓝池 {fmtLoss(item.manaLossPct)}</span>}
            {(item.spiritLoss ?? 0) >= 5 && <span className="neg">精魂 -{Math.round(item.spiritLoss)}</span>}
            {(item.maxResDrop ?? 0) >= 10 && <span className="muted">抗性 -{Math.round(item.maxResDrop)}%</span>}
          </div>
          {hasCompDiff && (
            <div className="muted" style={{ fontSize: 11, marginTop: 4, color: "var(--amber)" }}>
              ⚡ 补门槛后净贡献：DPS {fmtLoss(item.compensatedDpsLossPct!)} · EHP{" "}
              {fmtLoss(item.compensatedEhpLossPct ?? item.ehpLossPct)}
            </div>
          )}
        </div>

        <div className="kpi-card">
          <div className="row between">
            <div className="kpi-label">造价与性价比</div>
            <span className="muted" style={{ fontSize: 10 }}>
              {priceSourceBadgeLabel(cost.priceSource)}
              {cost.listings ? ` · ${cost.listings}件` : ""}
            </span>
          </div>
          <div className="row" style={{ marginTop: 4, gap: 6 }}>
            <strong style={{ color: "var(--gold-strong)" }}>{fmtD(cost.estimatedDivine, cost.priceSource)}</strong>
            {cost.costSharePct != null && <span className="muted">占总价 {cost.costSharePct}%</span>}
            {cost.dpsPerDivine != null && <span className="badge blue">DPS {cost.dpsPerDivine}%/D</span>}
            {cost.tradeUrl && (
              <button
                type="button"
                className="btn sm"
                style={{ padding: "1px 6px", fontSize: 10 }}
                onClick={() => openExternalUrl(cost.tradeUrl!)}
              >
                ↗ 挂单
              </button>
            )}
          </div>
          {!isUnique && (cost.budgetDivine || cost.semiDivine) && (
            <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>
              {cost.budgetDivine ? `平替 ${cost.budgetDivine}D ` : ""}
              {cost.semiDivine ? `半成品 ${cost.semiDivine}D` : ""}
            </div>
          )}
          <div className="row" style={{ marginTop: 5, gap: 4 }}>
            <input
              type="number"
              min={0}
              step={0.5}
              className="input"
              style={{ width: 72, padding: "2px 6px", fontSize: 11 }}
              placeholder="手填 D"
              value={manualPriceValue}
              onChange={(e) => onManualPriceChange(item.slot, e.target.value)}
            />
            <button
              type="button"
              className="btn sm"
              style={{ padding: "2px 7px", fontSize: 11 }}
              disabled={!(Number(manualPriceValue) > 0)}
              onClick={() => void onSaveManualPrice(item.slot, item.name)}
            >
              保存价
            </button>
          </div>
          {slotPriceMsg && (
            <div className="muted" style={{ fontSize: 10.5, marginTop: 3, color: "var(--gold)" }}>
              {slotPriceMsg}
            </div>
          )}
        </div>

        <div className="kpi-card">
          <div className="kpi-label">核心作用 &amp; 抄作业建议</div>
          <div style={{ fontSize: 12, marginTop: 3 }}>{item.coreRoleSummary}</div>
          <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>
            💡 {item.copyAdvice}
          </div>
        </div>
      </div>

      {/* 天赋联动 / 锁基底提醒 / 符文插槽消融 / 特别说明 */}
      {(synergies.length > 0 || item.requireBaseType || isUnique || item.runeImpact || item.note) && (
        <div className="chips" style={{ marginBottom: 10 }}>
          {isUnique && (
            <span className="badge unique">🟠 传奇直买 · 仅看 roll / 瓦尔额外孔或基底 / 符文 / 涂油（无需做装）</span>
          )}
          {item.requireBaseType && !isUnique && (
            <span className="badge red" title={item.baseTypeReason || ""}>
              🔒 必锁基底：{t.term(item.baseType, "bases")}
            </span>
          )}
          {synergies.map((ps) => (
            <span key={ps} className="badge purple">
              🌳 {ps}
            </span>
          ))}
          {item.runeImpact && (
            <span className={`badge ${item.runeImpact.exceedsCraftableSockets ? "red" : "gold"}`}>
              💎 符文插槽({item.runeImpact.socketCount}孔)：DPS {fmtLoss(item.runeImpact.dpsLossPct)} · EHP{" "}
              {fmtLoss(item.runeImpact.ehpLossPct)}
              {item.runeImpact.spiritLoss >= 5 ? ` · 精魂 -${Math.round(item.runeImpact.spiritLoss)}` : ""}
            </span>
          )}
          {item.note && <span className="badge amber">ℹ️ {item.note}</span>}
        </div>
      )}

      {item.runeImpact?.advice && (
        <div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>
          💎 符文与孔位说明：{item.runeImpact.advice}
        </div>
      )}

      {/* 装备插槽物品逐颗消融表 */}
      {(item.runeImpact?.socketedItems?.length || 0) > 0 && (
        <div
          className="kpi-card"
          style={{ marginBottom: 10, padding: "8px 10px", background: "rgba(167, 139, 250, 0.06)" }}
        >
          <div className="row between" style={{ marginBottom: 6 }}>
            <strong style={{ fontSize: 12, color: "#e9d5ff" }}>
              💎 装备插槽物品逐颗消融（共 {item.runeImpact!.socketedItems!.length} 颗镶嵌物）
            </strong>
            <span className="muted" style={{ fontSize: 11 }}>
              全部拔除合计：DPS {fmtLoss(item.runeImpact!.dpsLossPct)} · EHP {fmtLoss(item.runeImpact!.ehpLossPct)}
              {(item.runeImpact!.maxResDrop || 0) >= 5 ? ` · 抗性 -${Math.round(item.runeImpact!.maxResDrop)}%` : ""}
            </span>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th style={{ width: "26%" }}>插槽物品</th>
                  <th style={{ width: "26%" }}>赋予词条 / 联动</th>
                  <th style={{ width: "30%" }}>选择目的 &amp; 抄作业提示</th>
                  <th style={{ width: "18%" }}>单颗拔除实算</th>
                </tr>
              </thead>
              <tbody>
                {item.runeImpact!.socketedItems!.map((si) => (
                  <tr key={`sock-${si.index}-${si.name}`}>
                    <td>
                      <div className="row" style={{ gap: 4, flexWrap: "wrap" }}>
                        <span
                          className={`badge ${
                            si.runeTypeLabel === "遗物" || si.runeTypeLabel === "神像"
                              ? "unique"
                              : si.runeTypeLabel === "灵核"
                              ? "purple"
                              : "blue"
                          }`}
                          style={{ padding: "0 5px", fontSize: 10 }}
                        >
                          {si.runeTypeLabel}
                        </span>
                        <strong style={{ fontSize: 12 }}>{t.term(si.name, "any")}</strong>
                      </div>
                      <div className="chips" style={{ marginTop: 3 }}>
                        {si.estimatedDivine != null && si.estimatedDivine > 0 && (
                          <span className="badge gold" style={{ fontSize: 10 }}>
                            {fmtD(si.estimatedDivine, si.priceSource)}
                          </span>
                        )}
                        {si.grantsKeystone && (
                          <span className="badge red" style={{ fontSize: 10 }}>
                            ⚡ 基石：{si.grantsKeystone}
                          </span>
                        )}
                        {si.isSocketBound && (
                          <span className="badge amber" style={{ fontSize: 10 }}>
                            锁孔
                          </span>
                        )}
                      </div>
                    </td>
                    <td>
                      <div style={{ fontSize: 11, color: "#e2e8f0", lineHeight: 1.45 }}>
                        {(si.effects || []).map((ef, i) => (
                          <div key={i}>{t.mod(ef)}</div>
                        ))}
                        {(si.bondedEffects || []).length > 0 && (
                          <div
                            style={{
                              marginTop: 2,
                              color: si.bondedActive ? "var(--gold-strong)" : "var(--muted)",
                              opacity: si.bondedActive ? 1 : 0.65,
                            }}
                          >
                            {si.bondedActive ? "✓ Bonded 生效：" : "○ Bonded 未激活："}
                            {si.bondedEffects.map((b) => t.mod(b)).join("；")}
                          </div>
                        )}
                      </div>
                    </td>
                    <td>
                      <div className="row" style={{ gap: 5, marginBottom: 2 }}>
                        <span className={`badge ${PURPOSE_BADGE_CLASS[si.purposeKind] || ""}`}>
                          {si.purposeLabel}
                        </span>
                        {si.tier === "must" && <span className="badge red" style={{ fontSize: 10 }}>核心必插</span>}
                      </div>
                      <div className="muted" style={{ fontSize: 11 }}>{si.purposeDetail}</div>
                      {si.copyAdvice && (
                        <div className="muted" style={{ fontSize: 10.5, marginTop: 2, color: "#93c5fd" }}>
                          💡 {si.copyAdvice}
                        </div>
                      )}
                    </td>
                    <td>
                      <div className="chips">
                        {Math.abs(si.dpsLossPct ?? 0) >= 0.2 && (
                          <span className={`metric-pill ${si.dpsLossPct >= 3 ? "high" : ""}`}>
                            DPS {fmtLoss(si.dpsLossPct)}
                          </span>
                        )}
                        {Math.abs(si.ehpLossPct ?? 0) >= 0.2 && (
                          <span className={`metric-pill ${si.ehpLossPct >= 4 ? "def" : ""}`}>
                            EHP {fmtLoss(si.ehpLossPct)}
                          </span>
                        )}
                        {(si.physMaxHitLossPct ?? 0) >= 1.5 && (
                          <span className="metric-pill def">物一击 {fmtLoss(si.physMaxHitLossPct)}</span>
                        )}
                        {(si.maxResDrop ?? 0) >= 4 && (
                          <span className="metric-pill">抗性 -{Math.round(si.maxResDrop)}%</span>
                        )}
                        {(si.spiritLoss ?? 0) >= 5 && (
                          <span className="metric-pill warn">精魂 -{Math.round(si.spiritLoss)}</span>
                        )}
                        {(si.esLossPct ?? 0) >= 2 && (
                          <span className="metric-pill def">ES {fmtLoss(si.esLossPct)}</span>
                        )}
                        {si.isUnparsed && <span className="metric-pill warn">机制未折算</span>}
                        {Math.abs(si.dpsLossPct ?? 0) < 0.2 &&
                          Math.abs(si.ehpLossPct ?? 0) < 0.2 &&
                          (si.maxResDrop ?? 0) < 4 &&
                          (si.spiritLoss ?? 0) < 5 &&
                          !si.isUnparsed && <span className="muted">机制/池子辅助</span>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 逐条词缀引擎消融表 */}
      {affixes.length > 0 ? (
        <div style={{ overflowX: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: "36%" }}>词条内容</th>
                <th style={{ width: "36%" }}>选择目的（引擎实算 + 天赋联动）</th>
                <th style={{ width: "17%" }}>单条消融实算</th>
                <th style={{ width: "11%" }}>抄作业</th>
              </tr>
            </thead>
            <tbody>
              {affixes.map((aff, idx) => {
                const act = COPY_ACTION_LABEL[aff.copyAction] || COPY_ACTION_LABEL.skip;
                const originTag = ORIGIN_TAG[aff.origin];
                const sideTag = aff.side === "prefix" ? "前" : aff.side === "suffix" ? "后" : "";
                return (
                  <tr key={`${aff.line}-${idx}`}>
                    <td>
                      <div className="row" style={{ gap: 4, marginBottom: 2 }}>
                        {sideTag && (
                          <span className="badge" style={{ padding: "0 5px", fontSize: 10 }}>
                            {sideTag}
                          </span>
                        )}
                        {originTag && (
                          <span className="badge purple" style={{ padding: "0 5px", fontSize: 10 }}>
                            {originTag}
                          </span>
                        )}
                        <span
                          style={{
                            textDecoration: aff.purposeKind === "dead" ? "line-through" : "none",
                            opacity: aff.purposeKind === "dead" ? 0.55 : 1,
                          }}
                        >
                          {t.mod(aff.line)}
                        </span>
                      </div>
                    </td>
                    <td>
                      <div className="row" style={{ gap: 5, marginBottom: 2 }}>
                        <span className={`badge ${PURPOSE_BADGE_CLASS[aff.purposeKind] || ""}`}>
                          {aff.purposeLabel}
                        </span>
                      </div>
                      <div className="muted" style={{ fontSize: 11 }}>
                        {aff.purposeDetail}
                      </div>
                    </td>
                    <td>
                      <div className="chips">
                        {Math.abs(aff.dpsLossPct ?? 0) >= 0.2 && (
                          <span className={`metric-pill ${(aff.dpsLossPct ?? 0) >= 4 ? "high" : ""}`}>
                            DPS {fmtLoss(aff.dpsLossPct)}
                          </span>
                        )}
                        {Math.abs(aff.ehpLossPct ?? 0) >= 0.2 && (
                          <span className={`metric-pill ${(aff.ehpLossPct ?? 0) >= 4 ? "def" : ""}`}>
                            EHP {fmtLoss(aff.ehpLossPct)}
                          </span>
                        )}
                        {(aff.spiritLoss ?? 0) >= 5 && (
                          <span className="metric-pill warn">精魂 -{Math.round(aff.spiritLoss)}</span>
                        )}
                        {(aff.maxResDrop ?? 0) >= 5 && (
                          <span className="metric-pill">抗性 -{Math.round(aff.maxResDrop)}%</span>
                        )}
                        {aff.attrBroken && <span className="metric-pill warn">断属性</span>}
                        {aff.functionalLoss && (
                          <span className={`metric-pill ${aff.tier === "must" ? "warn" : ""}`}>
                            {aff.tier === "must" ? "⚠️ " : ""}
                            {aff.functionalLoss}
                          </span>
                        )}
                        {Math.abs(aff.dpsLossPct ?? 0) < 0.2 &&
                          Math.abs(aff.ehpLossPct ?? 0) < 0.2 &&
                          (aff.spiritLoss ?? 0) < 5 &&
                          (aff.maxResDrop ?? 0) < 5 &&
                          !aff.attrBroken &&
                          !aff.functionalLoss && <span className="muted">面板无直显</span>}
                      </div>
                    </td>
                    <td>
                      <span className={`badge ${isUnique && aff.copyAction === "skip" ? "unique" : act.cls}`}>
                        {isUnique && aff.copyAction === "skip" ? "暗金固有" : act.label}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="muted">该装备暂无可剥离的独立词缀行。</div>
      )}
    </div>
  );
};

export const DossierItemDetail = React.memo(DossierItemDetailInner);
