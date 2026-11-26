import React, { useState } from "react";
import type {
  BilingualTranslator,
  BuildAblationDossier,
  BuildProfileResponse,
  CopyBuildContext,
  CopyPlanItemUserState,
  GapAnalysisReport,
  LlmAnalyzeMode,
  PriceSnapshot,
  SavedCopyPlanRecord,
  SlotAffixReport,
  TargetBuildPayload,
} from "@contracts";
import type { LlmAnalyzeResult, MyBuildRecord } from "../api/workbenchApi";
import { CopyPlanActionList } from "../components/CopyPlanActionList";
import { MarkdownView } from "../components/MarkdownView";
import { PassiveTreePanel } from "../components/PassiveTreePanel";
import { RadarRatingChart } from "../components/RadarRatingChart";
import { TargetDossierPanel } from "../components/TargetDossierPanel";
import { countCopyPlanProgress } from "../utils/copyPlanStorage";
import { fmtCompact, fmtDeltaPct } from "../utils/handoffParser";

const ResBadges: React.FC<{ fire: number; cold: number; lightning: number; chaos: number; showGap?: boolean }> = ({
  fire,
  cold,
  lightning,
  chaos,
  showGap = false,
}) => {
  const f = Math.round(fire);
  const c = Math.round(cold);
  const l = Math.round(lightning);
  const ch = Math.round(chaos);
  return (
    <span className="res-strip">
      <span className={`res-chip fire ${f < 75 ? "uncapped" : ""}`} title={`火抗 ${f}%`}>
        火 {f}%{showGap && f < 75 ? ` (${f - 75})` : ""}
      </span>
      <span className={`res-chip cold ${c < 75 ? "uncapped" : ""}`} title={`冰抗 ${c}%`}>
        冰 {c}%{showGap && c < 75 ? ` (${c - 75})` : ""}
      </span>
      <span className={`res-chip lightning ${l < 75 ? "uncapped" : ""}`} title={`电抗 ${l}%`}>
        电 {l}%{showGap && l < 75 ? ` (${l - 75})` : ""}
      </span>
      <span className={`res-chip chaos ${ch < 0 ? "uncapped" : ""}`} title={`混沌抗性 ${ch}%`}>
        混 {ch}%
      </span>
    </span>
  );
};

interface OverviewProps {
  t: BilingualTranslator;
  league: string;
  backendOnline: boolean;
  myBuild: MyBuildRecord | null;
  targetBuild: TargetBuildPayload | null;
  targetProfile: BuildProfileResponse | null;
  targetDossier: BuildAblationDossier | null;
  dossierLoading: boolean;
  dossierError: string;
  priceSnapshot: PriceSnapshot | null;
  pricingBusy: string;
  pricingStatus: string;
  onRefreshNinjaPrices: () => void;
  onOverrideItemPrice: (slot: string, divineValue: number, itemName?: string) => Promise<void>;
  gapReport: GapAnalysisReport | null;
  analyzing: boolean;
  aiEnabled: boolean;
  aiLoading: boolean;
  aiResult: LlmAnalyzeResult | null;
  aiError: string;
  aiRatingLoading: boolean;
  aiRatingError: string;
  onRateTarget: (forceRefresh?: boolean) => void;
  copyContext: CopyBuildContext;
  onCopyContextChange: (next: CopyBuildContext) => void;
  currentPobHash: string;
  savedPlans: SavedCopyPlanRecord[];
  activePlan: SavedCopyPlanRecord | null;
  onSelectSavedPlan: (plan: SavedCopyPlanRecord) => void;
  onDeleteSavedPlan: (planId: string) => void;
  onUpdateCopyPlanItemState: (itemKey: string, patch: Partial<CopyPlanItemUserState>) => void;
  onSaveMyBuild: (code: string) => Promise<void>;
  onSaveTargetInput: (raw: string) => Promise<void>;
  onRefreshDossier: () => void;
  onOpenCraftWithSlot: (rep: SlotAffixReport) => void;
  onRunGap: () => Promise<void>;
  onGoUpgrade: () => void;
  onRunAi: (mode: LlmAnalyzeMode, forceRefresh?: boolean) => Promise<void>;
  onStopAi: () => void;
  onOpenAiSettings: () => void;
  onNotify?: (text: string) => void;
}

export const Overview: React.FC<OverviewProps> = ({
  t,
  league,
  backendOnline,
  myBuild,
  targetBuild,
  targetProfile,
  targetDossier,
  dossierLoading,
  dossierError,
  priceSnapshot,
  pricingBusy,
  pricingStatus,
  onRefreshNinjaPrices,
  onOverrideItemPrice,
  gapReport,
  analyzing,
  aiEnabled,
  aiLoading,
  aiResult,
  aiError,
  aiRatingLoading,
  aiRatingError,
  onRateTarget,
  copyContext,
  onCopyContextChange,
  currentPobHash,
  savedPlans,
  activePlan,
  onSelectSavedPlan,
  onDeleteSavedPlan,
  onUpdateCopyPlanItemState,
  onSaveMyBuild,
  onSaveTargetInput,
  onRefreshDossier,
  onOpenCraftWithSlot,
  onRunGap,
  onGoUpgrade,
  onRunAi,
  onStopAi,
  onOpenAiSettings,
  onNotify,
}) => {
  const [myInput, setMyInput] = useState("");
  const [tgtInput, setTgtInput] = useState("");
  const [savingMy, setSavingMy] = useState(false);
  const [savingTgt, setSavingTgt] = useState(false);
  const [editingMy, setEditingMy] = useState(false);
  const [editingTgt, setEditingTgt] = useState(false);
  const [showAllSavedPlans, setShowAllSavedPlans] = useState(false);

  const currentBuildSavedPlans = savedPlans.filter((p) => (p.pobHash || "") === (currentPobHash || ""));
  const visibleSavedPlans = showAllSavedPlans
    ? savedPlans
    : currentBuildSavedPlans.length > 0
    ? currentBuildSavedPlans
    : savedPlans.slice(0, 8);

  function formatSavedPlanLabel(p: SavedCopyPlanRecord): string {
    if (p.mode === "copy") {
      const lv = p.copyContext?.characterLevel ?? p.copyPlan?.characterLevel ?? 90;
      const bd = p.copyContext?.budgetDivine ?? p.copyPlan?.budgetDivine ?? 15;
      return `🎚️ Lv.${lv} · ${bd}D`;
    }
    if (p.mode === "target") return "🔬 本质拆解";
    if (p.mode === "gap") return "🧠 双码差距";
    const bd = p.copyContext?.budgetDivine ?? p.copyPlan?.budgetDivine ?? 15;
    return `✨ 无码 ${bd}D`;
  }

  async function handleSaveMy() {
    if (!myInput.trim()) return;
    setSavingMy(true);
    try {
      await onSaveMyBuild(myInput.trim());
      setMyInput("");
      setEditingMy(false);
    } finally {
      setSavingMy(false);
    }
  }

  async function handleSaveTarget() {
    if (!tgtInput.trim()) return;
    setSavingTgt(true);
    try {
      await onSaveTargetInput(tgtInput.trim());
      setTgtInput("");
      setEditingTgt(false);
    } finally {
      setSavingTgt(false);
    }
  }

  const showMyEditor = !myBuild || editingMy;
  const showTgtEditor = !targetBuild || editingTgt;

  const pos =
    targetProfile?.positioning?.contentReadiness?.confidence === "ai"
      ? targetProfile.positioning
      : targetBuild?.positioning?.contentReadiness?.confidence === "ai"
      ? targetBuild.positioning
      : targetProfile?.positioning || targetBuild?.positioning;

  const aiReadiness =
    pos?.contentReadiness &&
    (pos.contentReadiness.confidence === "ai" || pos.analyzedBy === "ai")
      ? pos.contentReadiness
      : null;

  return (
    <div className="page">
      {/* 双 BD 管理区（已导入时自动折叠为紧凑摘要条，释放首屏空间） */}
      <div className="grid-2">
        <section className="card">
          <div className="card-header">
            <span className="card-title">👤 我的 BD（当前角色）</span>
            <div className="row" style={{ gap: 6 }}>
              {myBuild ? (
                <span className="badge green">已保存</span>
              ) : (
                <span className="badge amber">未导入</span>
              )}
              {myBuild && (
                <button type="button" className="btn sm" onClick={() => setEditingMy((v) => !v)}>
                  {editingMy ? "收起" : "✏️ 更换 PoB 码"}
                </button>
              )}
            </div>
          </div>

          {myBuild && !showMyEditor ? (
            <div className="build-summary-box">
              <div className="row between">
                <strong style={{ color: "#fff" }}>{myBuild.title || "我的构筑"}</strong>
                {gapReport && <span className="badge">Lv.{gapReport.mySummary.level}</span>}
              </div>
              <div className="row between">
                <span className="muted">
                  {myBuild.updatedAt ? `更新于 ${new Date(myBuild.updatedAt).toLocaleTimeString()}` : "已持久化保存"}
                </span>
                {gapReport && (
                  <span className="muted">
                    DPS {fmtCompact(gapReport.myStats.dps)} · EHP {fmtCompact(gapReport.myStats.ehp)}
                  </span>
                )}
              </div>
            </div>
          ) : (
            <>
              <textarea
                className="textarea"
                placeholder="粘贴你的 PoB2 分享码或 XML（保存后持久化在 ~/.poe2-workbench/my_build.json）..."
                value={myInput}
                onChange={(e) => setMyInput(e.target.value)}
              />
              <div className="row between" style={{ marginTop: 8 }}>
                <span className="muted">
                  {myBuild?.updatedAt ? `更新于 ${new Date(myBuild.updatedAt).toLocaleTimeString()}` : "支持直接粘贴 PoB2 码"}
                </span>
                <button
                  type="button"
                  className="btn primary sm"
                  disabled={!myInput.trim() || savingMy}
                  onClick={() => void handleSaveMy()}
                >
                  {savingMy ? "保存中..." : "保存我的 BD"}
                </button>
              </div>
            </>
          )}
        </section>

        <section className="card gold">
          <div className="card-header">
            <span className="card-title">🎯 目标 BD（扩展自动推送 / 手动粘贴）</span>
            <div className="row" style={{ gap: 6 }}>
              {targetBuild ? (
                <span className="badge gold">{targetBuild.title || pos?.archetype || "已就绪"}</span>
              ) : (
                <span className="badge amber">等待扩展推送</span>
              )}
              {targetBuild && (
                <button type="button" className="btn sm" onClick={() => setEditingTgt((v) => !v)}>
                  {editingTgt ? "收起" : "✏️ 更换目标"}
                </button>
              )}
            </div>
          </div>

          {pos && (
            <div style={{ marginBottom: showTgtEditor ? 8 : 0 }}>
              <div style={{ fontSize: 12, marginBottom: 6 }}>{pos.oneLineIdea}</div>
              <div className="chips">
                {!aiReadiness && aiRatingLoading && (
                  <span className="badge purple">⏳ AI 结合 PoB2 引擎实算绘制六维能力图中...</span>
                )}
                {!aiReadiness && !aiRatingLoading && aiEnabled && targetBuild?.pobCode && (
                  <button
                    type="button"
                    className="badge purple"
                    style={{ cursor: "pointer", border: "none" }}
                    onClick={() => onRateTarget(false)}
                  >
                    ⬡ 点击生成 AI + PoB2 六维能力图
                  </button>
                )}
                {!aiReadiness && !aiRatingLoading && !aiEnabled && targetBuild?.pobCode && (
                  <button
                    type="button"
                    className="badge"
                    style={{ cursor: "pointer", border: "none" }}
                    onClick={onOpenAiSettings}
                  >
                    ⬡ 启用 AI 生成 PoB2 六维能力图
                  </button>
                )}
                {pos.scenarioTags.map((tag) => (
                  <span key={tag} className="badge gold">
                    {tag}
                  </span>
                ))}
                {targetBuild?.league && <span className="badge">赛季：{targetBuild.league}</span>}
                {priceSnapshot && (
                  <span className="badge green">
                    已同步扩展价格：{priceSnapshot.entries?.length || Object.keys(priceSnapshot.prices).length} 条
                  </span>
                )}
              </div>

              {aiRatingError && (
                <div className="muted" style={{ fontSize: 11, color: "var(--amber)", marginTop: 5 }}>
                  ⚠️ AI 评级失败：{aiRatingError}
                </div>
              )}

              {aiReadiness && (
                <RadarRatingChart
                  readiness={aiReadiness}
                  rerating={aiRatingLoading}
                  onRerate={() => onRateTarget(true)}
                />
              )}
            </div>
          )}

          {!pos && targetBuild && !showTgtEditor && (
            <div className="build-summary-box">
              <div className="row between">
                <strong style={{ color: "#fff" }}>{targetBuild.title || "目标构筑"}</strong>
                <span className="muted">{targetBuild.league ? `赛季：${targetBuild.league}` : "已导入"}</span>
              </div>
            </div>
          )}

          {showTgtEditor && (
            <>
              <textarea
                className="textarea"
                placeholder="在浏览器扩展点「推送目标 BD」会自动同步到这里；也可直接粘贴 PoB2 码或扩展 JSON 交接包..."
                value={tgtInput}
                onChange={(e) => setTgtInput(e.target.value)}
              />
              <div className="row between" style={{ marginTop: 8 }}>
                <span className="muted">
                  {targetBuild?.league ? `赛季：${targetBuild.league}` : "支持 PoB2 码或离线 JSON 交接包"}
                </span>
                <button
                  type="button"
                  className="btn sm"
                  disabled={!tgtInput.trim() || savingTgt}
                  onClick={() => void handleSaveTarget()}
                >
                  {savingTgt ? "导入中..." : "手动更新目标 BD"}
                </button>
              </div>
            </>
          )}
        </section>
      </div>

      {/* 目标 BD 引擎消融剖析（外面简洁，点进查看逐条词缀与宝石消融目的 + 抄作业底座） */}
      {targetProfile && (
        <TargetDossierPanel
          t={t}
          league={league}
          targetProfile={targetProfile}
          dossier={targetDossier}
          dossierLoading={dossierLoading}
          dossierError={dossierError}
          pricingBusy={pricingBusy}
          pricingStatus={pricingStatus}
          onRefreshNinjaPrices={onRefreshNinjaPrices}
          onOverrideItemPrice={onOverrideItemPrice}
          onRefreshDossier={onRefreshDossier}
          onOpenCraftWithSlot={onOpenCraftWithSlot}
        />
      )}

      {/* 大模型流式本质拆解 & 灵活等级/预算抄作业 & 差距诊断 */}
      <section className="card gold">
        <div className="card-header">
          <span className="card-title">🔬 流式 AI 本质拆解 &amp; 灵活抄作业（支持无自身 PoB 码）</span>
          <div className="row">
            <button type="button" className="btn sm" onClick={onOpenAiSettings}>
              ⚙️ {aiEnabled ? "修改 AI 配置" : "配置大模型 API"}
            </button>
            <button
              type="button"
              className="btn sm primary"
              disabled={!targetBuild?.pobCode || aiLoading}
              onClick={() => void onRunAi("target")}
            >
              {aiLoading && aiResult?.mode === "target" ? "流式拆解中..." : "🔬 本质拆解目标 BD"}
            </button>
            <button
              type="button"
              className="btn sm"
              disabled={!targetBuild?.pobCode || aiLoading}
              onClick={() => void onRunAi("copy")}
            >
              🎚️ 按 Lv.{copyContext.characterLevel ?? 90} / {copyContext.budgetDivine ?? 15}D 抄作业
            </button>
            <button
              type="button"
              className="btn sm"
              disabled={!gapReport || aiLoading}
              onClick={() => void onRunAi("gap")}
            >
              🧠 诊断双码差距
            </button>
            {aiLoading && (
              <button type="button" className="btn sm" onClick={onStopAi}>
                ⏹ 停止
              </button>
            )}
          </div>
        </div>

        {/* 灵活抄作业条件条：等级 + 造价预算滑块 + 一句话补充 */}
        <div className="kpi-card" style={{ marginBottom: 10 }}>
          <div className="row between" style={{ flexWrap: "wrap", gap: 10 }}>
            <div className="row" style={{ gap: 8 }}>
              <span className="muted">我的等级：</span>
              <input
                type="number"
                min={1}
                max={100}
                value={copyContext.characterLevel ?? 90}
                onChange={(e) =>
                  onCopyContextChange({
                    ...copyContext,
                    characterLevel: Math.max(1, Math.min(100, Number(e.target.value) || 1)),
                  })
                }
                style={{ width: 64, padding: "4px 6px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-2)", color: "#fff" }}
              />
              <span className="muted">造价预算 (D)：</span>
              <input
                type="range"
                min={1}
                max={200}
                value={Math.min(200, copyContext.budgetDivine ?? 15)}
                onChange={(e) => onCopyContextChange({ ...copyContext, budgetDivine: Number(e.target.value) })}
                style={{ width: 120 }}
              />
              <input
                type="number"
                min={0}
                step={0.5}
                value={copyContext.budgetDivine ?? 15}
                onChange={(e) =>
                  onCopyContextChange({ ...copyContext, budgetDivine: Math.max(0, Number(e.target.value) || 0) })
                }
                style={{ width: 68, padding: "4px 6px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-2)", color: "var(--gold-strong)" }}
              />
            </div>
            <div className="chips">
              {[2, 10, 30, 80, 200].map((v) => (
                <button
                  key={v}
                  type="button"
                  className={`btn sm ${copyContext.budgetDivine === v ? "primary" : ""}`}
                  onClick={() => onCopyContextChange({ ...copyContext, budgetDivine: v })}
                >
                  {v}D
                </button>
              ))}
            </div>
          </div>

          <div className="row" style={{ marginTop: 8, gap: 8 }}>
            <input
              type="text"
              placeholder="补充需求（可选，如：不买猎首 / 只刷 T15 / 无目标码时可填想玩的升华和技能）"
              value={copyContext.goalNote || ""}
              onChange={(e) =>
                onCopyContextChange({
                  ...copyContext,
                  goalNote: e.target.value,
                  freeformQuery: { ...copyContext.freeformQuery, skillIdea: e.target.value },
                })
              }
              style={{ flex: 1, padding: "6px 10px", borderRadius: 6, border: "1px solid var(--border)", background: "var(--bg-2)", color: "#fff", fontSize: 12 }}
            />
            {!targetBuild?.pobCode && (
              <button
                type="button"
                className="btn sm primary"
                disabled={aiLoading || !copyContext.goalNote?.trim()}
                onClick={() => void onRunAi("consult")}
              >
                ✨ 无 PoB 码直接咨询
              </button>
            )}
          </div>
        </div>

        {/* 抄作业交互式天赋图（自动同步当前等级拓扑裁剪 + 双武器组 + 涂油/珠宝 + 双码差异对比） */}
        {targetBuild?.pobCode && (
          <PassiveTreePanel
            t={t}
            targetPobCode={targetBuild.pobCode}
            myPobCode={myBuild?.pobCode}
            characterLevel={copyContext.characterLevel ?? 90}
            backendOnline={backendOnline}
            onNotify={onNotify}
          />
        )}

        {pos?.gearTiers && (
          <div className="chips" style={{ marginBottom: 8 }}>
            {pos.gearTiers.mandatory.map((m) => (
              <span key={`m-${m}`} className="badge red">
                🔴 机制锁死：{m}
              </span>
            ))}
            {pos.gearTiers.amplifier.map((a) => (
              <span key={`a-${a}`} className="badge gold">
                🟡 核心放大：{a}
              </span>
            ))}
            {pos.gearTiers.luxury.map((l) => (
              <span key={`l-${l}`} className="badge green">
                🟢 顶配奢侈：{l}
              </span>
            ))}
          </div>
        )}

        {savedPlans.length > 0 && (
          <div className="saved-plans-bar">
            <div className="row between" style={{ gap: 6, marginBottom: 6 }}>
              <div className="row" style={{ gap: 6 }}>
                <span style={{ fontSize: 11.5, fontWeight: 700, color: "var(--gold)" }}>
                  💾 本地已存方案（自动持久化，换页/重启不丢）
                </span>
                {savedPlans.length > currentBuildSavedPlans.length && currentBuildSavedPlans.length > 0 && (
                  <button
                    type="button"
                    className="btn sm"
                    style={{ fontSize: 10.5, padding: "2px 7px" }}
                    onClick={() => setShowAllSavedPlans((v) => !v)}
                  >
                    {showAllSavedPlans
                      ? `仅看当前 BD (${currentBuildSavedPlans.length})`
                      : `展开全部 BD (${savedPlans.length})`}
                  </button>
                )}
              </div>
              {activePlan && !aiLoading && (
                <button
                  type="button"
                  className="btn sm"
                  style={{ fontSize: 10.5, padding: "2px 8px" }}
                  title="忽略本地与服务端缓存，调用大模型重新分析当前条件"
                  onClick={() => void onRunAi(activePlan.mode, true)}
                >
                  ↻ 重新分析当前方案
                </button>
              )}
            </div>
            <div className="chips">
              {visibleSavedPlans.map((sp) => {
                const isActive = activePlan?.id === sp.id;
                const prog = countCopyPlanProgress(sp);
                const isOtherBuild = Boolean(currentPobHash && sp.pobHash && sp.pobHash !== currentPobHash);
                return (
                  <div
                    key={sp.id}
                    className={`saved-plan-chip ${isActive ? "active" : ""}`}
                    title={`${sp.buildTitle || "未命名构筑"} · ${sp.league || "Standard"} · 更新于 ${new Date(
                      sp.updatedAt || sp.createdAt
                    ).toLocaleString()}${sp.copyContext?.goalNote ? ` · 备注：${sp.copyContext.goalNote}` : ""}`}
                    onClick={() => onSelectSavedPlan(sp)}
                  >
                    <span>{formatSavedPlanLabel(sp)}</span>
                    {(showAllSavedPlans || isOtherBuild) && sp.buildTitle && (
                      <span className="saved-plan-title">{sp.buildTitle.slice(0, 14)}</span>
                    )}
                    {prog.total > 0 && (
                      <span className={`saved-plan-prog ${prog.done === prog.total ? "done" : ""}`}>
                        {prog.done}/{prog.total}
                      </span>
                    )}
                    <button
                      type="button"
                      className="saved-plan-del"
                      title="删除这条本地记录"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteSavedPlan(sp.id);
                      }}
                    >
                      ×
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {aiError && (
          <div className="banner error" style={{ marginBottom: 8 }}>
            <span>{aiError}</span>
          </div>
        )}

        {aiResult && (aiResult.text || aiResult.reasoning || aiResult.copyPlan || aiResult.toolStatus) ? (
          <div>
            <div className="row between" style={{ marginBottom: 6, gap: 8 }}>
              <span className="muted">
                由 {aiResult.model || "大模型"} 基于 PoB2 实算 + 倒推灵敏度 + 官方天赋/宝石原文生成
                {activePlan
                  ? `（已存本地 · ${new Date(activePlan.updatedAt || activePlan.createdAt).toLocaleString()}）`
                  : aiResult.cached
                  ? "（缓存命中）"
                  : ""}
              </span>
              {!aiLoading && aiResult.mode && (
                <button
                  type="button"
                  className="btn sm"
                  style={{ fontSize: 10.5, padding: "2px 8px" }}
                  onClick={() => void onRunAi(aiResult.mode!, true)}
                >
                  ↻ 强制重算
                </button>
              )}
            </div>
            {aiLoading && aiResult.toolStatus && (
              <div className="banner info" style={{ marginBottom: 8, padding: "6px 10px", fontSize: 12 }}>
                <span>🧪 {aiResult.toolStatus}</span>
              </div>
            )}
            {aiResult.reasoning && (
              <details style={{ marginBottom: 8, fontSize: 12, color: "var(--muted)" }}>
                <summary style={{ cursor: "pointer" }}>🧠 模型思考过程（点击展开，{aiResult.reasoning.length} 字）</summary>
                <div style={{ whiteSpace: "pre-wrap", marginTop: 6, padding: 10, background: "rgba(0,0,0,0.22)", borderRadius: 8 }}>
                  {aiResult.reasoning}
                </div>
              </details>
            )}
            {aiResult.copyPlan && aiResult.copyPlan.items.length > 0 && (
              <CopyPlanActionList
                t={t}
                league={league}
                copyPlan={aiResult.copyPlan}
                itemStates={activePlan?.itemStates}
                onItemStateChange={activePlan ? onUpdateCopyPlanItemState : undefined}
                onOpenCraftWithSlot={onOpenCraftWithSlot}
              />
            )}
            <div
              style={{
                background: "rgba(0,0,0,0.22)",
                padding: "12px 14px",
                borderRadius: 8,
              }}
            >
              {aiResult.text ? (
                <MarkdownView content={aiResult.text} streaming={aiLoading} />
              ) : (
                <span className="muted">
                  {aiLoading
                    ? aiResult.mode === "copy" || aiResult.mode === "consult"
                      ? "正在生成结构化选词清单（将自动匹配官方市集词条）与详细方案..."
                      : "正在生成结构化定位头与正文..."
                    : ""}
                </span>
              )}
            </div>
          </div>
        ) : (
          <div className="muted">
            {aiEnabled
              ? "大模型已启用（已升级为原生 SSE 流式防超时）：先点「🔬 本质拆解目标 BD」看透核心循环、隐藏门槛与装备三档分级，再调上方等级与造价滑块点「🎚️ 抄作业」，无需自己 PoB 码即可落地！"
              : "未启用大模型：PoB2 引擎与本地规则已正常运作；点击右上角「配置大模型 API」即可解锁流式本质拆解与按预算抄作业。"}
          </div>
        )}
      </section>

      {/* 未运行诊断时的引导卡 */}
      {!gapReport && (
        <section className="card">
          <div className="row between">
            <div>
              <strong>准备好开始追赶差距了吗？</strong>
              <div className="muted" style={{ marginTop: 3 }}>
                导入「我的 BD」与「目标 BD」后，点击右上角「运行自身 vs 目标全景诊断」，常驻 PoB2 LuaJIT 引擎将并发实算 0 成本天赋/技能提升与全槽位逐件试穿。
              </div>
            </div>
            <button
              type="button"
              className="btn primary"
              disabled={!myBuild?.pobCode || !targetBuild?.pobCode || analyzing}
              onClick={() => void onRunGap()}
            >
              {analyzing ? "计算中..." : "⚡ 立即全景诊断"}
            </button>
          </div>
        </section>
      )}

      {/* 全景面板差距对比 */}
      {gapReport && (
        <>
          <section className="card">
            <div className="card-header">
              <span className="card-title">📊 自身 vs 目标面板全景对比</span>
              <span className="muted">
                我的：{gapReport.mySummary.archetype} (Lv.{gapReport.mySummary.level}) vs 目标：
                {gapReport.targetSummary.archetype} (Lv.{gapReport.targetSummary.level}) · 耗时 {gapReport.elapsedMs}ms
              </span>
            </div>

            <div className="grid-4">
              <div className="kpi-card">
                <div className="kpi-label">核心输出 DPS ({gapReport.myStats.dpsSource})</div>
                <div className="kpi-value" style={{ color: "var(--gold-strong)" }}>
                  {fmtCompact(gapReport.myStats.dps)} → {fmtCompact(gapReport.targetStats.dps)}
                </div>
                <div className="muted">
                  目标为当前 {gapReport.myStats.dps > 0 ? (gapReport.targetStats.dps / gapReport.myStats.dps).toFixed(2) : "-"}x
                </div>
              </div>

              <div className="kpi-card">
                <div className="kpi-label">有效血量 EHP</div>
                <div className="kpi-value" style={{ color: "var(--green)" }}>
                  {fmtCompact(gapReport.myStats.ehp)} → {fmtCompact(gapReport.targetStats.ehp)}
                </div>
                <div className="muted">
                  血/盾：{Math.round(gapReport.myStats.life)}/{Math.round(gapReport.myStats.es)} →{" "}
                  {Math.round(gapReport.targetStats.life)}/{Math.round(gapReport.targetStats.es)}
                </div>
              </div>

              <div className="kpi-card">
                <div className="kpi-label">四抗（火/冰/电 · 混，低于 75% 标红）</div>
                <div style={{ marginTop: 4 }}>
                  <ResBadges
                    fire={gapReport.myStats.fireRes}
                    cold={gapReport.myStats.coldRes}
                    lightning={gapReport.myStats.lightningRes}
                    chaos={gapReport.myStats.chaosRes}
                    showGap
                  />
                </div>
                <div className="row" style={{ marginTop: 6, gap: 4 }}>
                  <span className="muted" style={{ fontSize: 11 }}>目标：</span>
                  <ResBadges
                    fire={gapReport.targetStats.fireRes}
                    cold={gapReport.targetStats.coldRes}
                    lightning={gapReport.targetStats.lightningRes}
                    chaos={gapReport.targetStats.chaosRes}
                  />
                </div>
              </div>

              <div className="kpi-card">
                <div className="kpi-label">剩余精魂 / 移速</div>
                <div className="kpi-value" style={{ fontSize: 15 }}>
                  {Math.round(gapReport.myStats.spiritUnreserved)}/{Math.round(gapReport.myStats.spirit)} · +
                  {Math.round(gapReport.myStats.moveSpeed)}%
                </div>
                <div className="muted">
                  目标精魂 {Math.round(gapReport.targetStats.spiritUnreserved)}/{Math.round(gapReport.targetStats.spirit)} · 移速 +
                  {Math.round(gapReport.targetStats.moveSpeed)}%
                </div>
              </div>
            </div>
          </section>

          {/* 当前自身红线告警 */}
          {gapReport.myRedlines.length > 0 && (
            <section className="card" style={{ borderColor: "rgba(240,101,98,0.45)" }}>
              <div className="card-header">
                <span className="card-title" style={{ color: "var(--red)" }}>
                  🚨 当前自身 BD 红线预警（优先修复）
                </span>
              </div>
              <div className="chips">
                {gapReport.myRedlines.map((r, i) => (
                  <span key={i} className={`badge ${r.severity === "fail" ? "red" : "amber"}`}>
                    {r.severity === "fail" ? "⛔" : "⚠️"} {r.message}
                  </span>
                ))}
              </div>
            </section>
          )}

          {/* 0 成本提升诊断：实算 + 天赋差距 + 技能差距 */}
          <section className="card gold">
            <div className="card-header">
              <span className="card-title">🆓 0 成本提升优先清单（先不花通货把免费提升拿满）</span>
              <button type="button" className="btn sm primary" onClick={onGoUpgrade}>
                前往逐件装备试穿 & 预算路线 →
              </button>
            </div>

            <div className="grid-2" style={{ marginBottom: 12 }}>
              {gapReport.zeroCostSteps.map((step) => (
                <div key={step.slot} className="kpi-card">
                  <div className="row between">
                    <strong>{step.label}</strong>
                    <span className="badge green">0 D</span>
                  </div>
                  <div className="row" style={{ marginTop: 6 }}>
                    <span className={step.deltaDpsPct >= 0 ? "pos" : "neg"}>
                      ΔDPS {fmtDeltaPct(step.deltaDpsPct)}
                    </span>
                    <span className={step.deltaEhpPct >= 0 ? "pos" : "neg"}>
                      ΔEHP {fmtDeltaPct(step.deltaEhpPct)}
                    </span>
                    {step.deltaMoveSpeedPct !== 0 && (
                      <span className="muted">Δ移速 {fmtDeltaPct(step.deltaMoveSpeedPct)}</span>
                    )}
                  </div>
                  {step.redlines.length > 0 && (
                    <div className="chips" style={{ marginTop: 6 }}>
                      {step.redlines.map((r, idx) => (
                        <span key={idx} className={`badge ${r.severity === "fail" ? "red" : "amber"}`}>
                          {r.message}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>

            <div className="grid-2">
              {/* 天赋树差距 */}
              <div className="kpi-card">
                <div className="row between" style={{ marginBottom: 6 }}>
                  <strong>🌳 天赋树大点 / 升华差距</strong>
                  <span className="muted">
                    已点 {gapReport.treeDiff.myAllocated} vs 目标 {gapReport.treeDiff.targetAllocated}
                  </span>
                </div>
                {gapReport.treeDiff.missingKeystones.length > 0 && (
                  <div style={{ marginBottom: 6 }}>
                    <div className="muted">缺少基石 (Keystones)：</div>
                    <div className="chips" style={{ marginTop: 3 }}>
                      {gapReport.treeDiff.missingKeystones.map((n) => (
                        <span key={n} className="badge purple">
                          + {t.term(n, "passives")}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {gapReport.treeDiff.missingAscendancy.length > 0 && (
                  <div style={{ marginBottom: 6 }}>
                    <div className="muted">缺少升华大点：</div>
                    <div className="chips" style={{ marginTop: 3 }}>
                      {gapReport.treeDiff.missingAscendancy.map((n) => (
                        <span key={n} className="badge gold">
                          + {t.term(n, "passives")}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {gapReport.treeDiff.missingNotables.length > 0 ? (
                  <div>
                    <div className="muted">
                      目标已点但我缺失的大点（{gapReport.treeDiff.missingNotables.length} 个）：
                    </div>
                    <div className="chips" style={{ marginTop: 3 }}>
                      {gapReport.treeDiff.missingNotables.slice(0, 10).map((n) => (
                        <span key={n} className="badge blue">
                          + {t.term(n, "passives")}
                        </span>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="muted">核心天赋大点已基本与目标对齐。</div>
                )}
              </div>

              {/* 技能连法与品质差距 */}
              <div className="kpi-card">
                <div className="row between" style={{ marginBottom: 6 }}>
                  <strong>🔥 技能连法 & 宝石品质差距</strong>
                  <span className="muted">
                    主技：{gapReport.skillsDiff.myMainSkill ? t.term(gapReport.skillsDiff.myMainSkill, "gems") : "-"} →{" "}
                    {gapReport.skillsDiff.targetMainSkill ? t.term(gapReport.skillsDiff.targetMainSkill, "gems") : "-"}
                  </span>
                </div>
                {gapReport.skillsDiff.gaps.length > 0 ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                    {gapReport.skillsDiff.gaps.slice(0, 8).map((g, i) => (
                      <div key={i} className="row between" style={{ fontSize: 12 }}>
                        <span>{g.detail}</span>
                        <span className={`badge ${g.severity === "high" ? "red" : g.severity === "medium" ? "amber" : "blue"}`}>
                          {g.kind === "missing_support"
                            ? "缺辅助"
                            : g.kind === "quality_gap"
                            ? "补品质"
                            : g.kind === "level_gap"
                            ? "等级差"
                            : "缺技能"}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="muted">主技能连法与宝石品质已与目标一致。</div>
                )}
              </div>
            </div>
          </section>
        </>
      )}
    </div>
  );
};
