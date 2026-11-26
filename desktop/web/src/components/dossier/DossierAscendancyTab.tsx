import React from "react";
import type { BilingualTranslator, BuildAblationDossier } from "@contracts";
import { fmtLoss, ROLE_BADGE_CLASS } from "./dossierUtils";

interface DossierAscendancyTabProps {
  t: BilingualTranslator;
  ascendancy: BuildAblationDossier["ascendancy"];
}

const DossierAscendancyTabInner: React.FC<DossierAscendancyTabProps> = ({ t, ascendancy }) => {
  if (!ascendancy || ascendancy.nodes.length === 0) {
    return <div className="muted">该 BD 暂未分配可消融的升华大点。</div>;
  }
  const tot = ascendancy.totalImpact;
  return (
    <div>
      <div
        className="kpi-card"
        style={{ marginBottom: 10, padding: "10px 12px", background: "rgba(245, 196, 81, 0.07)" }}
      >
        <div className="row between" style={{ marginBottom: 6, flexWrap: "wrap", gap: 6 }}>
          <div className="row" style={{ gap: 8 }}>
            <strong style={{ fontSize: 13.5, color: "var(--gold-strong)" }}>
              👑 升华天赋消融总览：{t.term(ascendancy.ascendancy, "ascendancies")}
            </strong>
            <span className="badge gold">{ascendancy.notableCount} 个核心升华大点</span>
            <span className="badge">共 {ascendancy.allocatedNodeCount} 升华点</span>
          </div>
          <div className="chips">
            {Math.abs(tot.dpsLossPct) >= 0.5 && (
              <span className={`metric-pill ${tot.dpsLossPct >= 10 ? "high" : ""}`}>
                整套 DPS {fmtLoss(tot.dpsLossPct)}
              </span>
            )}
            {Math.abs(tot.ehpLossPct) >= 0.5 && (
              <span className={`metric-pill ${tot.ehpLossPct >= 10 ? "def" : ""}`}>
                整套 EHP {fmtLoss(tot.ehpLossPct)}
              </span>
            )}
            {(tot.physMaxHitLossPct ?? 0) >= 2 && (
              <span className="metric-pill def">物一击 {fmtLoss(tot.physMaxHitLossPct)}</span>
            )}
            {(tot.eleMaxHitLossPct ?? 0) >= 2 && (
              <span className="metric-pill def">元一击 {fmtLoss(tot.eleMaxHitLossPct)}</span>
            )}
            {(tot.spiritLoss ?? 0) >= 5 && (
              <span className="metric-pill warn">精魂 -{Math.round(tot.spiritLoss)}</span>
            )}
            {(tot.moveSpeedDrop ?? 0) >= 3 && (
              <span className="metric-pill">移速 -{Math.round(tot.moveSpeedDrop!)}%</span>
            )}
            {tot.attrBroken && <span className="metric-pill warn">支撑属性门槛</span>}
          </div>
        </div>
        <div style={{ fontSize: 12, color: "#e2e8f0", lineHeight: 1.5 }}>{ascendancy.whyChosenSummary}</div>
      </div>

      <div className="grid-2">
        {ascendancy.nodes.map((node) => {
          const iso = node.isolated;
          const br = node.branch;
          return (
            <div key={node.id} className="kpi-card" style={{ padding: "10px 12px" }}>
              <div className="row between" style={{ gap: 6, marginBottom: 6 }}>
                <strong style={{ fontSize: 13, color: "#fff" }}>{t.term(node.name, "passives")}</strong>
                <div className="row" style={{ gap: 5 }}>
                  <span className={`badge ${ROLE_BADGE_CLASS[node.roleKind] || "purple"}`}>{node.roleLabel}</span>
                  {node.importanceTier === "core" && <span className="badge red">核心选择</span>}
                </div>
              </div>

              <div className="chips" style={{ marginBottom: 6 }}>
                <span className="muted" style={{ fontSize: 10.5 }}>孤立实算：</span>
                {Math.abs(iso.dpsLossPct) >= 0.3 && (
                  <span className={`metric-pill ${iso.dpsLossPct >= 6 ? "high" : ""}`}>
                    DPS {fmtLoss(iso.dpsLossPct)}
                  </span>
                )}
                {Math.abs(iso.ehpLossPct) >= 0.3 && (
                  <span className={`metric-pill ${iso.ehpLossPct >= 6 ? "def" : ""}`}>
                    EHP {fmtLoss(iso.ehpLossPct)}
                  </span>
                )}
                {(iso.physMaxHitLossPct ?? 0) >= 1.5 && (
                  <span className="metric-pill def">物一击 {fmtLoss(iso.physMaxHitLossPct)}</span>
                )}
                {(iso.spiritLoss ?? 0) >= 5 && (
                  <span className="metric-pill warn">精魂 -{Math.round(iso.spiritLoss)}</span>
                )}
                {(iso.moveSpeedDrop ?? 0) >= 3 && (
                  <span className="metric-pill">移速 -{Math.round(iso.moveSpeedDrop!)}%</span>
                )}
                {node.grantedSkill && (
                  <span className="metric-pill def">⚡ 赋予：{t.term(node.grantedSkill, "gems")}</span>
                )}
                {Math.abs(iso.dpsLossPct) < 0.3 &&
                  Math.abs(iso.ehpLossPct) < 0.3 &&
                  (iso.spiritLoss ?? 0) < 5 &&
                  !node.grantedSkill && (
                    <span className="muted" style={{ fontSize: 11 }}>
                      {node.isMechanical ? "机制/手感型（静态面板未完全折算）" : "前置过渡 / 辅助型"}
                    </span>
                  )}
              </div>

              {node.hasBranchDescendants && node.unlocksNotables.length > 0 && (
                <div className="muted" style={{ fontSize: 11, marginBottom: 5, color: "var(--amber)" }}>
                  ↳ 前置解锁分支：{node.unlocksNotables.join(" / ")}（整支 DPS {fmtLoss(br.dpsLossPct)} · EHP{" "}
                  {fmtLoss(br.ehpLossPct)}）
                </div>
              )}

              <div style={{ fontSize: 11.5, color: "#cbd5e1", lineHeight: 1.5, marginBottom: 6 }}>
                {node.selectionReason}
              </div>

              {node.stats.length > 0 && (
                <div
                  className="muted"
                  style={{
                    fontSize: 10.5,
                    paddingTop: 5,
                    borderTop: "1px solid rgba(148,163,184,0.14)",
                    lineHeight: 1.45,
                  }}
                >
                  {node.stats.slice(0, 4).map((st, idx) => (
                    <div key={idx}>• {t.mod(st)}</div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export const DossierAscendancyTab = React.memo(DossierAscendancyTabInner);
