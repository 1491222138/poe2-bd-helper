import React from "react";
import type { BuildAblationDossier } from "@contracts";

interface DossierCopyTabProps {
  dossier: BuildAblationDossier;
}

const DossierCopyTabInner: React.FC<DossierCopyTabProps> = ({ dossier }) => {
  return (
    <div className="grid-3">
      <div className="kpi-card">
        <div className="row between" style={{ marginBottom: 6 }}>
          <strong style={{ color: "var(--gold-strong)" }}>🔴 优先必保核心</strong>
          <span className="badge red">{dossier.summary.mandatoryCount} 件必保</span>
        </div>
        {dossier.copyFoundation.topPriorities.length > 0 ? (
          <ul className="dossier-list">
            {dossier.copyFoundation.topPriorities.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        ) : (
          <div className="muted">无强锁死单件，可灵活按预算搭配。</div>
        )}
      </div>

      <div className="kpi-card">
        <div className="row between" style={{ marginBottom: 6 }}>
          <strong style={{ color: "var(--green)" }}>🟢 省钱平替 &amp; 可砍点</strong>
          <span className="badge green">可砍 {dossier.summary.cuttableAffixCount} 词</span>
        </div>
        {dossier.copyFoundation.budgetCuts.length > 0 ? (
          <ul className="dossier-list">
            {dossier.copyFoundation.budgetCuts.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        ) : (
          <div className="muted">暂无明显高溢价可砍项。</div>
        )}
      </div>

      <div className="kpi-card">
        <div className="row between" style={{ marginBottom: 6 }}>
          <strong style={{ color: "var(--amber)" }}>⚡ 换装红线锚点</strong>
          <span className="badge amber">必保 {dossier.summary.mustAffixCount} 词</span>
        </div>
        {dossier.copyFoundation.redlineAnchors.length > 0 ? (
          <ul className="dossier-list">
            {dossier.copyFoundation.redlineAnchors.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        ) : (
          <div className="muted">换装门槛分散均匀，无单件卡死精魂/属性风险。</div>
        )}
      </div>
    </div>
  );
};

export const DossierCopyTab = React.memo(DossierCopyTabInner);
