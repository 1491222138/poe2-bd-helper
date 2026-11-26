import React from "react";
import type { BilingualTranslator, SkillGroupAblationDetail } from "@contracts";
import { COPY_TIER_CLASS, fmtLoss, ROLE_BADGE_CLASS } from "./dossierUtils";

interface DossierSkillDetailProps {
  t: BilingualTranslator;
  sg: SkillGroupAblationDetail;
  detailPanelRef: React.RefObject<HTMLDivElement | null>;
  onClose: () => void;
}

const DossierSkillDetailInner: React.FC<DossierSkillDetailProps> = ({
  t,
  sg,
  detailPanelRef,
  onClose,
}) => {
  return (
    <div ref={detailPanelRef} className="dossier-detail-panel inline-row-detail">
      <div className="row between" style={{ marginBottom: 8 }}>
        <div className="row" style={{ gap: 8 }}>
          {sg.isMain && <span className="badge gold">主技能组</span>}
          <strong style={{ fontSize: 14, color: "#fff" }}>{t.term(sg.mainGemName, "gems")}</strong>
          <span className={`badge ${ROLE_BADGE_CLASS[sg.roleKind] || ""}`}>{sg.roleLabel}</span>
          <span className={`badge ${COPY_TIER_CLASS[sg.copyTier] || ""}`}>{sg.copyTierLabel}</span>
          {sg.spiritSaved > 0 && <span className="badge amber">占用精魂 {Math.round(sg.spiritSaved)}</span>}
        </div>
        <button type="button" className="btn sm" onClick={onClose}>
          ✕ 收起
        </button>
      </div>

      <div className="kpi-card" style={{ marginBottom: 10 }}>
        <div style={{ fontSize: 12 }}>{sg.coreRoleSummary}</div>
        {sg.defenseAnalysis?.isDefensive && (
          <div style={{ marginTop: 6, paddingTop: 6, borderTop: "1px solid rgba(148,163,184,0.16)" }}>
            <div className="chips" style={{ marginBottom: 4 }}>
              <span className="badge green">🛡️ 技能防御分析</span>
              {sg.defenseAnalysis.defenseLayers.map((lyr) => (
                <span key={lyr} className="badge blue" style={{ fontSize: 10.5 }}>
                  {lyr}
                </span>
              ))}
              {(sg.ehpLossPct ?? 0) >= 0.5 && <span className="metric-pill def">EHP {fmtLoss(sg.ehpLossPct)}</span>}
              {(sg.physMaxHitLossPct ?? 0) >= 1 && (
                <span className="metric-pill def">物一击 {fmtLoss(sg.physMaxHitLossPct)}</span>
              )}
              {(sg.eleMaxHitLossPct ?? 0) >= 1 && (
                <span className="metric-pill def">元一击 {fmtLoss(sg.eleMaxHitLossPct)}</span>
              )}
              {(sg.maxResDrop ?? 0) >= 5 && (
                <span className="metric-pill def">抗性 -{Math.round(sg.maxResDrop)}%</span>
              )}
              {(sg.evadeDrop ?? 0) >= 1.5 && (
                <span className="metric-pill def">闪避 -{sg.evadeDrop!.toFixed(1)}%</span>
              )}
              {(sg.deflectDrop ?? 0) >= 1.5 && (
                <span className="metric-pill def">偏转 -{sg.deflectDrop!.toFixed(1)}%</span>
              )}
              {(sg.lifeRecoveryDrop ?? 0) >= 8 && (
                <span className="metric-pill def">生命回复 -{Math.round(sg.lifeRecoveryDrop!)}/s</span>
              )}
              {(sg.esRecoveryDrop ?? 0) >= 10 && (
                <span className="metric-pill def">护盾回复 -{Math.round(sg.esRecoveryDrop!)}/s</span>
              )}
            </div>
            {sg.defenseAnalysis.defenseSummary && (
              <div className="muted" style={{ fontSize: 11 }}>
                {sg.defenseAnalysis.defenseSummary}
              </div>
            )}
          </div>
        )}
        <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
          💡 抄作业建议：{sg.copyAdvice}
        </div>
      </div>

      <div style={{ overflowX: "auto" }}>
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: "22%" }}>宝石 (等级/品质)</th>
              <th style={{ width: "16%" }}>角色定位</th>
              <th style={{ width: "20%" }}>拆掉实算影响</th>
              <th style={{ width: "14%" }}>21/23 → 20/20</th>
              <th style={{ width: "28%" }}>选择目的 &amp; 抄作业提示</th>
            </tr>
          </thead>
          <tbody>
            {sg.gems.map((g, idx) => (
              <tr key={`${g.name}-${idx}`}>
                <td>
                  <div className="row" style={{ gap: 5 }}>
                    <span className={`badge ${g.isMainGem ? "gold" : "blue"}`} style={{ fontSize: 10 }}>
                      {g.isMainGem ? "主动" : "辅助"}
                    </span>
                    <strong>{t.term(g.name, "gems")}</strong>
                  </div>
                  <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
                    Lv.{g.level} / {g.quality}%Q
                  </div>
                </td>
                <td>
                  <span className={`badge ${g.dpsLossPct >= 12 ? "gold" : (g.ehpLossPct >= 3 || g.isDefensive) ? "green" : ""}`}>
                    {g.roleLabel}
                  </span>
                </td>
                <td>
                  {g.isMainGem ? (
                    <div className="chips">
                      <span className="muted">主动本体</span>
                      {(g.defenseLayers || []).map((dl) => (
                        <span key={dl} className="metric-pill def">🛡️ {dl}</span>
                      ))}
                    </div>
                  ) : g.tested ? (
                    <div className="chips">
                      {Math.abs(g.dpsLossPct) >= 0.2 && (
                        <span className={`metric-pill ${g.dpsLossPct >= 10 ? "high" : ""}`}>
                          DPS {fmtLoss(g.dpsLossPct)}
                        </span>
                      )}
                      {Math.abs(g.ehpLossPct) >= 0.2 && (
                        <span className="metric-pill def">EHP {fmtLoss(g.ehpLossPct)}</span>
                      )}
                      {(g.physMaxHitLossPct ?? 0) >= 1.5 && (
                        <span className="metric-pill def">物一击 {fmtLoss(g.physMaxHitLossPct)}</span>
                      )}
                      {(g.maxResDrop ?? 0) >= 5 && (
                        <span className="metric-pill def">抗性 -{Math.round(g.maxResDrop!)}%</span>
                      )}
                      {(g.lifeRecoveryDrop ?? 0) >= 8 && (
                        <span className="metric-pill def">回血 -{Math.round(g.lifeRecoveryDrop!)}/s</span>
                      )}
                      {(g.esRecoveryDrop ?? 0) >= 10 && (
                        <span className="metric-pill def">回盾 -{Math.round(g.esRecoveryDrop!)}/s</span>
                      )}
                      {Math.abs(g.manaCostDeltaPct) >= 5 && (
                        <span className="metric-pill">
                          魔耗 {g.manaCostDeltaPct > 0 ? `+${g.manaCostDeltaPct}%` : `${g.manaCostDeltaPct}%`}
                        </span>
                      )}
                      {(g.defenseLayers || []).slice(0, 2).map((dl) => (
                        <span key={dl} className="metric-pill def">🛡️ {dl}</span>
                      ))}
                      {Math.abs(g.dpsLossPct) < 0.2 &&
                        Math.abs(g.ehpLossPct) < 0.2 &&
                        (g.lifeRecoveryDrop ?? 0) < 8 &&
                        (g.defenseLayers?.length || 0) === 0 && (
                          <span className="muted">机制/手感类</span>
                        )}
                    </div>
                  ) : (
                    <div className="chips">
                      <span className="muted">随整组评估</span>
                      {(g.defenseLayers || []).slice(0, 1).map((dl) => (
                        <span key={dl} className="metric-pill def">🛡️ {dl}</span>
                      ))}
                    </div>
                  )}
                </td>
                <td>
                  {g.downgrade2020DpsLossPct != null ? (
                    <span className={`badge ${g.downgrade2020DpsLossPct < 4 ? "green" : "amber"}`}>
                      DPS {fmtLoss(g.downgrade2020DpsLossPct)}
                    </span>
                  ) : (
                    <span className="muted">≤ 20/20</span>
                  )}
                </td>
                <td>
                  <div style={{ fontSize: 11.5 }}>{g.purposeDetail}</div>
                  <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
                    {g.copyAdvice}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export const DossierSkillDetail = React.memo(DossierSkillDetailInner);
