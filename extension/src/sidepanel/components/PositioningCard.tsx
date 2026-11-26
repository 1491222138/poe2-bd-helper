import React from "react";
import type { BilingualTranslator, BuildProfileResponse } from "@contracts";
import { MarkdownView } from "./MarkdownView";
import { RadarRatingChart } from "./RadarRatingChart";

interface PositioningCardProps {
  t: BilingualTranslator;
  profile: BuildProfileResponse;
  backendOnline?: boolean;
  aiLoading?: boolean;
  aiText?: string;
  aiReasoning?: string;
  aiModel?: string;
  aiError?: string;
  onRunAiAnalysis?: () => void;
  onStopAiAnalysis?: () => void;
}

const BUDGET_META: Record<BuildProfileResponse["positioning"]["budgetTier"], { label: string; cls: string }> = {
  "league-start": { label: "低造价 / 开荒友好", cls: "green" },
  mid: { label: "中预算成型", cls: "blue" },
  high: { label: "中高造价", cls: "gold" },
  mirror: { label: "镜级 / 毕业档", cls: "purple" },
};

const GRADE_BADGE_CLASS: Record<string, string> = {
  S: "gold",
  A: "green",
  B: "blue",
  C: "unique",
  D: "red",
};

const CONFIDENCE_LABEL: Record<string, string> = {
  ai: "AI + PoB2 引擎实算评级",
  simulated: "PoB2 引擎实算",
  "xml-stats": "XML 面板评估",
  heuristic: "静态结构估算",
};

function fmtNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(1)}k`;
  return Math.round(n).toLocaleString();
}

export const PositioningCard: React.FC<PositioningCardProps> = ({
  t,
  profile,
  backendOnline = false,
  aiLoading = false,
  aiText = "",
  aiReasoning = "",
  aiModel = "",
  aiError = "",
  onRunAiAnalysis,
  onStopAiAnalysis,
}) => {
  const { positioning, build, intent, simulation, healthChecks } = profile;
  const budget = BUDGET_META[positioning.budgetTier];
  const tree = build.passiveTree;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {/* 总览定位卡 */}
      <section className="card gold">
        <div className="row between" style={{ marginBottom: 6 }}>
          <div style={{ fontWeight: 700, fontSize: 15, color: "#fff" }}>{positioning.archetype}</div>
          <span className={`badge ${budget.cls}`}>{budget.label}</span>
        </div>

        <div className="idea-box">{positioning.oneLineIdea}</div>
        {positioning.defenseSystem && (
          <div className="muted" style={{ marginTop: 6, fontSize: 11.5 }}>
            🛡️ 防御体系：{positioning.defenseSystem}
          </div>
        )}

        <div className="chips" style={{ marginTop: 8 }}>
          <span className={`badge ${profile.source === "backend" ? "green" : "blue"}`}>
            {profile.source === "backend" ? "后端引擎实算" : "纯插件词典解析"}
          </span>
          {positioning.analyzedBy === "ai" && <span className="badge purple">✨ AI 本质已校准</span>}
          <span className="badge">Lv.{build.character.level || "?"}</span>
          {positioning.scenarioTags.map((tag) => (
            <span key={tag} className="badge gold">
              {tag}
            </span>
          ))}
        </div>
      </section>

      {/* 第一核心：🔬 目标 BD 本质拆解（纯插件离线 & 后端满血双轨流式） */}
      {onRunAiAnalysis && (
        <section className="card gold">
          <div className="card-title">
            <span>🔬 目标 BD 本质拆解（核心循环 / 乘区 / 暗坑 / 装备分级）</span>
            <div className="row" style={{ gap: 6 }}>
              {aiLoading && onStopAiAnalysis && (
                <button type="button" className="btn sm" onClick={onStopAiAnalysis}>
                  ⏹ 停止
                </button>
              )}
              <button type="button" className="btn sm primary" disabled={aiLoading} onClick={onRunAiAnalysis}>
                {aiLoading ? "流式拆解中..." : aiText ? "↻ 重新流式拆解" : "✨ AI 流式本质拆解"}
              </button>
            </div>
          </div>

          <div className="muted" style={{ fontSize: 11, marginBottom: 6 }}>
            {backendOnline
              ? "满血双轨：PoB2 引擎实算 + 消融占比 + 官方天赋/宝石原文 + 流式 AI"
              : "纯插件模式：离线版本词典（天赋/宝石官方原文）+ 扩展直连流式 AI"}
            {aiModel ? ` · ${aiModel}` : ""}
          </div>

          {aiError && <div className="alert error">{aiError}</div>}

          {positioning.gearTiers && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 8 }}>
              {positioning.gearTiers.mandatory.length > 0 && (
                <div className="row" style={{ flexWrap: "wrap", gap: 4 }}>
                  <span className="badge red">🔴 机制锁死</span>
                  {positioning.gearTiers.mandatory.map((x) => (
                    <span key={x} className="badge">
                      {x}
                    </span>
                  ))}
                </div>
              )}
              {positioning.gearTiers.amplifier.length > 0 && (
                <div className="row" style={{ flexWrap: "wrap", gap: 4 }}>
                  <span className="badge gold">🟡 核心放大</span>
                  {positioning.gearTiers.amplifier.map((x) => (
                    <span key={x} className="badge">
                      {x}
                    </span>
                  ))}
                </div>
              )}
              {positioning.gearTiers.luxury.length > 0 && (
                <div className="row" style={{ flexWrap: "wrap", gap: 4 }}>
                  <span className="badge green">🟢 顶配奢侈</span>
                  {positioning.gearTiers.luxury.map((x) => (
                    <span key={x} className="badge">
                      {x}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}

          {aiReasoning && (
            <details style={{ marginBottom: 8, fontSize: 11, color: "var(--muted)" }}>
              <summary style={{ cursor: "pointer" }}>🧠 模型思考过程（点击展开，{aiReasoning.length} 字）</summary>
              <div style={{ whiteSpace: "pre-wrap", marginTop: 4, padding: 6, background: "rgba(0,0,0,0.22)", borderRadius: 6 }}>
                {aiReasoning}
              </div>
            </details>
          )}

          {aiText ? (
            <details open={aiLoading} style={{ marginTop: 4 }}>
              <summary className="muted" style={{ cursor: "pointer" }}>
                📄 {aiLoading ? "正在流式输出完整拆解..." : `展开完整 AI 文字拆解（${aiText.length} 字，默认折叠）`}
              </summary>
              <div style={{ marginTop: 6 }}>
                <MarkdownView content={aiText} streaming={aiLoading} compact />
              </div>
            </details>
          ) : (
            <div className="muted">
              点击右上角按钮提取装备三档分级与暗坑（长文默认折叠，可直接操作上方购物/做装决策卡）。
            </div>
          )}
        </section>
      )}

      {/* 可打内容评估与分级（仅展示 AI 结合 PoB2 引擎实算的评级，不再做静态正则评级） */}
      {positioning.contentReadiness &&
      (positioning.contentReadiness.confidence === "ai" || positioning.analyzedBy === "ai") ? (
        <section className="card">
          <div className="card-title">
            <span>🤖 AI + PoB2 引擎终局评级</span>
            <span className="muted">
              {CONFIDENCE_LABEL[positioning.contentReadiness.confidence] || positioning.contentReadiness.confidence}
            </span>
          </div>

          <div className="row between" style={{ marginBottom: 8 }}>
            <div className="row">
              <span className={`badge ${GRADE_BADGE_CLASS[positioning.contentReadiness.overallGrade] || ""}`}>
                综合 {positioning.contentReadiness.overallGrade} 级
              </span>
              <strong style={{ color: "#fff" }}>{positioning.contentReadiness.overallTier}</strong>
            </div>
          </div>

          <RadarRatingChart readiness={positioning.contentReadiness} />

          <div className="muted" style={{ marginBottom: 8 }}>
            {positioning.contentReadiness.summary}
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {positioning.contentReadiness.items.map((item) => (
              <div key={item.id} className="gear-card" style={{ padding: "6px 8px", gap: 3 }}>
                <div className="row between">
                  <strong>{item.label}</strong>
                  <div className="row" style={{ gap: 4 }}>
                    <span className="muted">{item.verdict}</span>
                    <span className={`badge ${GRADE_BADGE_CLASS[item.grade] || ""}`}>{item.grade}</span>
                  </div>
                </div>
                <div className="muted">{item.reason}</div>
              </div>
            ))}
          </div>
        </section>
      ) : (
        onRunAiAnalysis && (
          <section className="card">
            <div className="row between">
              <div>
                <div style={{ fontWeight: 600, fontSize: 12.5 }}>🤖 终局评级待 AI 结合 PoB2 引擎评定</div>
                <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
                  已停用静态正则评级；点击「✨ AI 流式本质拆解」将基于 PoB2 实算位移速度、输出与防御综合评级。
                </div>
              </div>
              <button type="button" className="btn sm primary" disabled={aiLoading} onClick={onRunAiAnalysis}>
                {aiLoading ? "评级中..." : "AI 评级"}
              </button>
            </div>
          </section>
        )
      )}

      {/* 常驻引擎实算面板（若已启用） */}
      {simulation && (
        <section className="card">
          <div className="card-title">
            <span>⚡ PoB2 引擎实算面板</span>
            <span className="muted">{simulation.dpsSource}</span>
          </div>
          <div className="stat-grid">
            <div className="stat-cell">
              <div className="stat-label">DPS</div>
              <div className="stat-value" style={{ color: "var(--gold-strong)" }}>
                {fmtNum(simulation.dps)}
              </div>
            </div>
            <div className="stat-cell">
              <div className="stat-label">有效血量 EHP</div>
              <div className="stat-value" style={{ color: "var(--green)" }}>
                {fmtNum(simulation.ehp)}
              </div>
            </div>
            <div className="stat-cell">
              <div className="stat-label">血 / 护盾</div>
              <div className="stat-value">
                {Math.round(simulation.life)} / {Math.round(simulation.es)}
              </div>
            </div>
            <div className="stat-cell">
              <div className="stat-label">三抗 / 混抗</div>
              <div className="res-strip" style={{ marginTop: 3 }}>
                <span className={`res-chip fire ${Math.round(simulation.fireRes) < 75 ? "uncapped" : ""}`}>
                  {Math.round(simulation.fireRes)}
                </span>
                <span className={`res-chip cold ${Math.round(simulation.coldRes) < 75 ? "uncapped" : ""}`}>
                  {Math.round(simulation.coldRes)}
                </span>
                <span className={`res-chip lightning ${Math.round(simulation.lightningRes) < 75 ? "uncapped" : ""}`}>
                  {Math.round(simulation.lightningRes)}
                </span>
                <span className={`res-chip chaos ${Math.round(simulation.chaosRes) < 0 ? "uncapped" : ""}`}>
                  {Math.round(simulation.chaosRes)}
                </span>
              </div>
            </div>
            <div className="stat-cell">
              <div className="stat-label">剩余精魂</div>
              <div
                className="stat-value"
                style={{ color: simulation.spiritUnreserved < 0 ? "var(--red)" : "var(--text)" }}
              >
                {Math.round(simulation.spiritUnreserved)} / {Math.round(simulation.spirit)}
              </div>
            </div>
            <div className="stat-cell">
              <div className="stat-label">暴击 / 移速</div>
              <div className="stat-value">
                {Math.round(simulation.critChance)}% · ×{simulation.moveSpeedMod.toFixed(2)}
              </div>
            </div>
            {simulation.travelSkills && simulation.travelSkills.length > 0 && (
              <div className="stat-cell">
                <div className="stat-label">位移技能 (PoB2)</div>
                <div className="stat-value" style={{ fontSize: 12 }}>
                  {t.term(simulation.travelSkills[0].name, "gems")}{" "}
                  {simulation.travelSkills[0].cooldownSec >= 0.15 && simulation.travelSkills[0].usesPerSec <= 2.5
                    ? `CD ${simulation.travelSkills[0].cooldownSec.toFixed(2)}s`
                    : simulation.travelSkills[0].usesPerSec > 0
                    ? `${simulation.travelSkills[0].usesPerSec.toFixed(2)}/s`
                    : ""}
                </div>
              </div>
            )}
          </div>
        </section>
      )}

      {/* 双武器组分工 */}
      {intent.hasDualWeaponSet && (
        <section className="card">
          <div className="card-title">
            <span>⚔️ 双武器组分工</span>
            <span className="badge blue">Weapon Set I / II</span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {intent.weaponSetRoles.set1 && (
              <div className="gear-card">
                <div className="row between">
                  <strong>Set I · {intent.weaponSetRoles.set1.label}</strong>
                  <span className="muted">{build.weaponSets.set1.map((w) => w.baseType).join(" + ")}</span>
                </div>
                {intent.weaponSetRoles.set1.detail && (
                  <div className="muted">{intent.weaponSetRoles.set1.detail}</div>
                )}
              </div>
            )}
            {intent.weaponSetRoles.set2 && (
              <div className="gear-card">
                <div className="row between">
                  <strong>Set II · {intent.weaponSetRoles.set2.label}</strong>
                  <span className="muted">{build.weaponSets.set2.map((w) => w.baseType).join(" + ")}</span>
                </div>
                {intent.weaponSetRoles.set2.detail && (
                  <div className="muted">{intent.weaponSetRoles.set2.detail}</div>
                )}
              </div>
            )}
          </div>
        </section>
      )}

      {/* 核心暗金 & 核心词缀 */}
      <section className="card">
        <div className="card-title">
          <span>💎 核心暗金 & 核心词缀</span>
        </div>
        <div style={{ marginBottom: 8 }}>
          <div className="muted" style={{ marginBottom: 4 }}>
            核心暗金（{positioning.coreUniques.length} 件）
          </div>
          {positioning.coreUniques.length > 0 ? (
            <div className="chips">
              {positioning.coreUniques.map((u) => (
                <span key={u} className="badge unique">
                  {u}
                </span>
              ))}
            </div>
          ) : (
            <div className="muted">无强绑定暗金，以稀有黄装为主。</div>
          )}
        </div>

        <div className="divider" />

        <div>
          <div className="muted" style={{ marginBottom: 4 }}>
            优先必保词缀方向
          </div>
          <div className="chips">
            {positioning.coreAffixes.map((aff) => (
              <span key={aff} className="badge rare">
                {aff}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* 全量珠宝配置总览 */}
      <section className="card">
        <div className="card-title">
          <span>💠 珠宝配置总览（{build.jewels.length} 颗）</span>
          <span className="muted">含装备孔与天赋树珠宝孔</span>
        </div>
        {build.jewels.length > 0 ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {build.jewels.map((j) => (
              <div key={`${j.slot}-${j.itemId}`} className="gear-card" style={{ padding: "6px 8px", gap: 3 }}>
                <div className="row between">
                  <div className="row" style={{ gap: 5 }}>
                    <span className={`badge ${j.rarity.toUpperCase() === "UNIQUE" ? "unique" : "rare"}`}>
                      {j.rarity.toUpperCase() === "UNIQUE" ? "暗金" : "黄装"}
                    </span>
                    <strong>{t.itemName(j.name, j.baseType)}</strong>
                  </div>
                  <span className="muted">{t.slot(j.slot)}</span>
                </div>
                {j.mods.length > 0 && (
                  <div className="muted" style={{ lineHeight: 1.35 }}>
                    {j.mods.slice(0, 4).map((m) => t.mod(m)).join(" · ")}
                    {j.mods.length > 4 ? ` …(+${j.mods.length - 4})` : ""}
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="muted">该 BD 未检测到已插放的珠宝。</div>
        )}
      </section>

      {/* 核心天赋（Keystone / 升华 / 双武器 Notable） */}
      <section className="card">
        <div className="card-title">
          <span>🌳 核心天赋</span>
          <span className="muted">
            已点 {tree.totalAllocated} 点{tree.treeVersion ? ` · v${tree.treeVersion}` : ""}
          </span>
        </div>

        {positioning.coreKeystones.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            <div className="muted" style={{ marginBottom: 4 }}>基石天赋 (Keystones)</div>
            <div className="chips">
              {tree.keystones.map((n) => (
                <span key={n.id} className="badge purple" title={n.stats.map((s) => t.mod(s)).join("\n")}>
                  ★ {t.term(n.name, "passives")}
                </span>
              ))}
            </div>
          </div>
        )}

        {positioning.coreAscendancy.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            <div className="muted" style={{ marginBottom: 4 }}>升华大点 (Ascendancy)</div>
            <div className="chips">
              {tree.ascendancyNodes.map((n) => (
                <span key={n.id} className="badge gold" title={n.stats.map((s) => t.mod(s)).join("\n")}>
                  {t.term(n.name, "passives")}
                </span>
              ))}
            </div>
          </div>
        )}

        {tree.notables.length > 0 ? (
          <div>
            <div className="muted" style={{ marginBottom: 4 }}>
              核心大点 (Notables · 前 {Math.min(12, tree.notables.length)}/{tree.notables.length})
            </div>
            <div className="chips">
              {tree.notables.slice(0, 12).map((n) => (
                <span key={n.id} className="badge" title={n.stats.map((s) => t.mod(s)).join("\n")}>
                  {n.weaponSet ? `[W${n.weaponSet}] ` : ""}
                  {t.term(n.name, "passives")}
                </span>
              ))}
            </div>
          </div>
        ) : (
          <div className="muted">
            {profile.source === "local-fallback"
              ? "离线模式无法映射 PoB2 天赋树节点名称，启动本地后端后自动展示全部大点与升华。"
              : "未检测到已分配大点。"}
          </div>
        )}
      </section>

      {/* 技能连法 */}
      <section className="card">
        <div className="card-title">
          <span>🔥 技能连法（{build.skillGroups.length} 组）</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {build.skillGroups.slice(0, 6).map((sg) => (
            <div key={sg.index} className="gear-card">
              <div className="row between">
                <div className="row">
                  {sg.isMain && <span className="badge gold">主技能</span>}
                  {sg.weaponSet && <span className="badge blue">Set {sg.weaponSet}</span>}
                  <strong>{sg.gems[0]?.name ? t.term(sg.gems[0].name, "gems") : "技能组"}</strong>
                </div>
                <span className="muted">
                  Lv.{sg.gems[0]?.level}/{sg.gems[0]?.quality}%Q
                </span>
              </div>
              {sg.gems.length > 1 && (
                <div className="muted">
                  辅助：{sg.gems.slice(1).map((g) => `${t.term(g.name, "gems")}(${g.level}/${g.quality}%)`).join(" · ")}
                </div>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* 避坑与红线检查 */}
      <section className="card">
        <div className="card-title">
          <span>⚠️ 避坑提示 & 红线检查</span>
        </div>
        <ul className="list">
          {positioning.pitfalls.map((p, idx) => (
            <li key={idx}>{p}</li>
          ))}
        </ul>

        {healthChecks.some((c) => c.suppressedByIntent) && (
          <div style={{ marginTop: 8 }}>
            <div className="muted">已按构筑意图自动豁免的特殊配法：</div>
            <div className="chips" style={{ marginTop: 4 }}>
              {healthChecks
                .filter((c) => c.suppressedByIntent)
                .map((c, idx) => (
                  <span key={idx} className="badge" title={c.suppressedByIntent?.reason}>
                    ✓ {c.title}
                  </span>
                ))}
            </div>
          </div>
        )}
      </section>
    </div>
  );
};
