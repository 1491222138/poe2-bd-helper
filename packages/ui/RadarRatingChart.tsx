import React, { useState } from "react";
import type { ContentReadinessSummary, RadarDimensionScore } from "@contracts";

export interface RadarRatingChartProps {
  readiness: ContentReadinessSummary;
  compact?: boolean;
  variant?: "full" | "minimal";
  onRerate?: () => void;
  rerating?: boolean;
}

const DEFAULT_AXES: Array<{ id: RadarDimensionScore["id"]; label: string; short: string }> = [
  { id: "clearSpeed", label: "清图效率", short: "清图" },
  { id: "bossing", label: "攻坚爆发", short: "攻坚" },
  { id: "mobility", label: "机动位移", short: "机动" },
  { id: "survivability", label: "生存硬度", short: "生存" },
  { id: "sustain", label: "续航手感", short: "续航" },
  { id: "costEfficiency", label: "造价性价比", short: "性价比" },
];

function scoreColor(score: number): string {
  if (score >= 88) return "#f5c451"; // gold
  if (score >= 74) return "#4ade80"; // green
  if (score >= 58) return "#60a5fa"; // blue
  if (score >= 42) return "#fbbf24"; // amber
  return "#f87171"; // red
}

function gradeBadgeCls(g?: string): string {
  if (g === "S") return "gold";
  if (g === "A") return "green";
  if (g === "B") return "blue";
  if (g === "C") return "amber";
  return "red";
}

function polarPoint(cx: number, cy: number, radius: number, index: number, total = 6): [number, number] {
  const angle = -Math.PI / 2 + (index * 2 * Math.PI) / total;
  return [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)];
}

export const RadarRatingChart: React.FC<RadarRatingChartProps> = ({
  readiness,
  compact = false,
  variant = "full",
  onRerate,
  rerating = false,
}) => {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [showScenarios, setShowScenarios] = useState(false);

  const radarMap = new Map((readiness.radar || []).map((r) => [r.id, r]));
  const axes: Array<RadarDimensionScore & { short: string }> = DEFAULT_AXES.map((meta) => {
    const hit = radarMap.get(meta.id);
    return {
      id: meta.id,
      label: hit?.label || meta.label,
      short: meta.short,
      score: typeof hit?.score === "number" ? Math.max(0, Math.min(100, Math.round(hit.score))) : 0,
      metric: hit?.metric || "",
      reason: hit?.reason || "",
    };
  });

  const hasRadar = (readiness.radar || []).length >= 3;
  if (variant === "minimal" && !hasRadar) return null;

  const activeAxis = axes.find((a) => a.id === activeId) || axes[0];
  const isMinimal = variant === "minimal";
  const size = isMinimal ? 192 : compact ? 186 : 212;
  const cx = size / 2;
  const cy = size / 2;
  const maxR = isMinimal || compact ? 58 : 66;
  const labelR = isMinimal ? 78 : compact ? 76 : 87;
  const rings = [25, 50, 75, 100];
  const gradientId = isMinimal ? "poe2ExtRadarFill" : "poe2RadarFill";

  const dataPoints = axes.map((ax, i) => polarPoint(cx, cy, (Math.max(6, ax.score) / 100) * maxR, i));
  const dataPolygon = dataPoints.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");

  const radarSvg = (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label="PoE2 构筑六维能力雷达图"
      style={{ overflow: "visible", userSelect: "none" }}
    >
      <defs>
        <radialGradient id={gradientId} cx="50%" cy="50%" r="55%">
          <stop offset="0%" stopColor="#f5c451" stopOpacity="0.44" />
          <stop offset="100%" stopColor="#a78bfa" stopOpacity="0.18" />
        </radialGradient>
      </defs>

      {rings.map((pct) => {
        const r = (pct / 100) * maxR;
        const pts = axes
          .map((_, i) => polarPoint(cx, cy, r, i).map((n) => n.toFixed(1)).join(","))
          .join(" ");
        return (
          <polygon
            key={pct}
            points={pts}
            fill={pct === 100 ? "rgba(148, 163, 184, 0.04)" : "none"}
            stroke={pct === 100 ? "rgba(212, 175, 55, 0.35)" : "rgba(148, 163, 184, 0.2)"}
            strokeWidth={pct === 100 ? 1.2 : 0.8}
            strokeDasharray={!isMinimal && pct === 50 ? "2,2" : undefined}
          />
        );
      })}

      {axes.map((ax, i) => {
        const [x2, y2] = polarPoint(cx, cy, maxR, i);
        return (
          <line
            key={`spoke-${ax.id}`}
            x1={cx}
            y1={cy}
            x2={x2}
            y2={y2}
            stroke={activeAxis?.id === ax.id ? "rgba(245, 196, 81, 0.65)" : "rgba(148, 163, 184, 0.22)"}
            strokeWidth={activeAxis?.id === ax.id ? 1.3 : 0.9}
          />
        );
      })}

      <polygon points={dataPolygon} fill={`url(#${gradientId})`} stroke="#f5c451" strokeWidth={1.8} strokeLinejoin="round" />

      {axes.map((ax, i) => {
        const [px, py] = dataPoints[i];
        const [lx, ly] = polarPoint(cx, cy, labelR, i);
        const isSel = activeAxis?.id === ax.id;
        const col = scoreColor(ax.score);
        const anchor = Math.abs(lx - cx) < 6 ? "middle" : lx > cx ? "start" : "end";
        return (
          <g
            key={ax.id}
            style={{ cursor: "pointer" }}
            onMouseEnter={() => setActiveId(ax.id)}
            onClick={() => setActiveId(ax.id)}
          >
            <circle cx={px} cy={py} r={isSel ? (isMinimal ? 4.8 : 5) : isMinimal ? 3.2 : 3.4} fill={col} stroke="#0f172a" strokeWidth={isMinimal ? 1.1 : 1.2} />
            <circle cx={px} cy={py} r={isMinimal ? 10 : 11} fill="transparent" />
            <text
              x={lx}
              y={ly - 2}
              textAnchor={anchor}
              dominantBaseline="central"
              fontSize={isMinimal || compact ? 10 : 10.5}
              fontWeight={isSel ? 700 : 600}
              fill={isSel ? "#ffffff" : "#cbd5e1"}
            >
              {ax.short}
            </text>
            <text
              x={lx}
              y={ly + 10}
              textAnchor={anchor}
              dominantBaseline="central"
              fontSize={isMinimal || compact ? 10 : 10.5}
              fontWeight={700}
              fill={col}
            >
              {ax.score}
            </text>
          </g>
        );
      })}
    </svg>
  );

  if (isMinimal) {
    return (
      <div style={{ marginBottom: 8 }}>
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 6 }}>{radarSvg}</div>

        {activeAxis && (
          <div
            style={{
              padding: "5px 7px",
              borderRadius: 6,
              background: "rgba(0,0,0,0.24)",
              fontSize: 11,
              lineHeight: 1.4,
            }}
          >
            <div className="row between" style={{ gap: 6, marginBottom: 2 }}>
              <strong style={{ color: scoreColor(activeAxis.score) }}>
                {activeAxis.label} · {activeAxis.score}/100
              </strong>
              {activeAxis.metric && <span className="muted">{activeAxis.metric}</span>}
            </div>
            <div style={{ color: "#cbd5e1" }}>{activeAxis.reason || readiness.summary}</div>
          </div>
        )}

        {readiness.ascendancyVerdict?.verdict && (
          <div
            style={{
              marginTop: 6,
              padding: "5px 7px",
              borderRadius: 6,
              background: "rgba(245, 196, 81, 0.08)",
              border: "1px solid rgba(245, 196, 81, 0.22)",
              fontSize: 10.5,
              lineHeight: 1.4,
            }}
          >
            <div style={{ fontWeight: 700, color: "#f5c451", marginBottom: 2 }}>
              👑 升华选择评判{readiness.ascendancyVerdict.ascendancy ? `（${readiness.ascendancyVerdict.ascendancy}）` : ""}
            </div>
            <div style={{ color: "#f1f5f9" }}>{readiness.ascendancyVerdict.verdict}</div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      style={{
        marginTop: 6,
        padding: compact ? "8px 9px" : "9px 11px",
        borderRadius: 10,
        background: "rgba(15, 23, 42, 0.55)",
        border: "1px solid rgba(212, 175, 55, 0.24)",
      }}
    >
      <div className="row between" style={{ gap: 6, marginBottom: 6, flexWrap: "wrap" }}>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          <span className={`badge ${gradeBadgeCls(readiness.overallGrade)}`}>
            🤖 综合 {readiness.overallGrade} 级
          </span>
          <strong style={{ fontSize: 12, color: "#f8fafc" }}>{readiness.overallTier}</strong>
        </div>
        <div className="row" style={{ gap: 5 }}>
          {readiness.items && readiness.items.length > 0 && (
            <button
              type="button"
              className="btn sm"
              style={{ padding: "1px 7px", fontSize: 10.5 }}
              onClick={() => setShowScenarios((v) => !v)}
            >
              {showScenarios ? "收起场景 ▴" : "5项场景 ▾"}
            </button>
          )}
          {onRerate && (
            <button
              type="button"
              className="btn sm"
              style={{ padding: "1px 7px", fontSize: 10.5 }}
              disabled={rerating}
              title="结合最新 PoB2 引擎 7 维实算数据重新评估六边形"
              onClick={onRerate}
            >
              {rerating ? "重评中..." : "↻ 重评"}
            </button>
          )}
        </div>
      </div>

      {hasRadar ? (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: compact ? "1fr" : `${size}px minmax(0, 1fr)`,
            gap: compact ? 8 : 12,
            alignItems: "center",
          }}
        >
          <div style={{ display: "flex", justifyContent: "center" }}>{radarSvg}</div>

          <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
            {axes.map((ax) => {
              const isSel = activeAxis?.id === ax.id;
              const col = scoreColor(ax.score);
              return (
                <button
                  key={ax.id}
                  type="button"
                  onMouseEnter={() => setActiveId(ax.id)}
                  onClick={() => setActiveId(ax.id)}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "62px minmax(46px, 1fr) 26px",
                    alignItems: "center",
                    gap: 6,
                    padding: "3px 6px",
                    borderRadius: 6,
                    border: isSel ? "1px solid rgba(245, 196, 81, 0.45)" : "1px solid transparent",
                    background: isSel ? "rgba(245, 196, 81, 0.1)" : "rgba(255, 255, 255, 0.02)",
                    cursor: "pointer",
                    textAlign: "left",
                    color: "#e2e8f0",
                  }}
                >
                  <span style={{ fontSize: 11, fontWeight: isSel ? 700 : 500, whiteSpace: "nowrap" }}>
                    {ax.label}
                  </span>
                  <div
                    style={{
                      height: 6,
                      borderRadius: 99,
                      background: "rgba(148, 163, 184, 0.18)",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        width: `${ax.score}%`,
                        height: "100%",
                        borderRadius: 99,
                        background: col,
                        transition: "width 0.25s ease",
                      }}
                    />
                  </div>
                  <span style={{ fontSize: 11, fontWeight: 700, color: col, textAlign: "right" }}>
                    {ax.score}
                  </span>
                </button>
              );
            })}

            {activeAxis && (
              <div
                style={{
                  marginTop: 2,
                  padding: "5px 7px",
                  borderRadius: 6,
                  background: "rgba(0, 0, 0, 0.28)",
                  fontSize: 11,
                  lineHeight: 1.45,
                }}
              >
                <div className="row between" style={{ gap: 6, marginBottom: 2 }}>
                  <strong style={{ color: scoreColor(activeAxis.score) }}>
                    {activeAxis.label} · {activeAxis.score}/100
                  </strong>
                  {activeAxis.metric && (
                    <span className="muted" style={{ fontSize: 10.5 }}>
                      {activeAxis.metric}
                    </span>
                  )}
                </div>
                <div style={{ color: "#cbd5e1" }}>{activeAxis.reason || readiness.summary}</div>
              </div>
            )}
          </div>
        </div>
      ) : (
        <div style={{ fontSize: 11.5, color: "#cbd5e1" }}>{readiness.summary}</div>
      )}

      {(readiness.summary || readiness.engineMetricsSummary) && (
        <div
          style={{
            marginTop: 6,
            paddingTop: 5,
            borderTop: "1px solid rgba(148, 163, 184, 0.14)",
            fontSize: 11,
            color: "var(--muted)",
            lineHeight: 1.45,
          }}
        >
          {readiness.summary && <div style={{ color: "#cbd5e1" }}>{readiness.summary}</div>}
          {readiness.engineMetricsSummary && (
            <div style={{ marginTop: 2, fontSize: 10.5 }}>
              📐 PoB2 实算依据：{readiness.engineMetricsSummary}
              {readiness.model ? ` · ${readiness.model}` : ""}
            </div>
          )}
        </div>
      )}

      {readiness.ascendancyVerdict &&
        (readiness.ascendancyVerdict.verdict || (readiness.ascendancyVerdict.keyReasons?.length || 0) > 0) && (
          <div
            style={{
              marginTop: 6,
              padding: "6px 8px",
              borderRadius: 7,
              background: "rgba(245, 196, 81, 0.08)",
              border: "1px solid rgba(245, 196, 81, 0.22)",
              fontSize: 11,
              lineHeight: 1.45,
            }}
          >
            <div style={{ fontWeight: 700, color: "#f5c451", marginBottom: 2 }}>
              👑 升华选择评判{readiness.ascendancyVerdict.ascendancy ? `（${readiness.ascendancyVerdict.ascendancy}）` : ""}
            </div>
            {readiness.ascendancyVerdict.verdict && (
              <div style={{ color: "#f1f5f9" }}>{readiness.ascendancyVerdict.verdict}</div>
            )}
            {(readiness.ascendancyVerdict.keyReasons?.length || 0) > 0 && (
              <ul style={{ margin: "3px 0 0 14px", padding: 0, color: "#cbd5e1", fontSize: 10.5 }}>
                {readiness.ascendancyVerdict.keyReasons!.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            )}
            {readiness.ascendancyVerdict.tradeoff && (
              <div className="muted" style={{ marginTop: 3, fontSize: 10.5 }}>
                ⚖️ 取舍说明：{readiness.ascendancyVerdict.tradeoff}
              </div>
            )}
          </div>
        )}

      {showScenarios && readiness.items && readiness.items.length > 0 && (
        <div
          style={{
            marginTop: 7,
            paddingTop: 7,
            borderTop: "1px solid rgba(148, 163, 184, 0.16)",
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          {readiness.items.map((it) => (
            <div key={it.id} style={{ fontSize: 11 }}>
              <div className="row between">
                <strong>{it.label}</strong>
                <div className="row" style={{ gap: 4 }}>
                  <span className="muted">{it.verdict}</span>
                  <span className={`badge ${gradeBadgeCls(it.grade)}`}>{it.grade}</span>
                </div>
              </div>
              {it.reason && <div className="muted">{it.reason}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
