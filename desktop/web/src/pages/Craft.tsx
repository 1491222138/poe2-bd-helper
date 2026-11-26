import React, { useEffect, useMemo, useState } from "react";
import {
  buildHtcLinkFromClipboardTexts,
  buildPoe2PasteTextFromPayload,
  type BilingualTranslator,
  type CraftMinTierMode,
  type HtcMode,
  type NinjaRatesSnapshot,
  type SlotAffixReport,
} from "@contracts";
import { desktopApi, type CraftMetaResponse } from "../api/workbenchApi";
import { openExternalUrl, openExternalUrlAsync } from "../utils/handoffParser";

interface CraftProps {
  t: BilingualTranslator;
  seedSlot: SlotAffixReport | null;
  league: string;
}

const COMMON_SLOTS = [
  "Spears",
  "Bows",
  "Crossbows",
  "Wands",
  "Sceptres",
  "Body Armours",
  "Helmets",
  "Gloves",
  "Boots",
  "Amulets",
  "Rings",
  "Belts",
  "Quivers",
  "Shields",
];

export const Craft: React.FC<CraftProps> = ({ t, seedSlot, league }) => {
  const [slot, setSlot] = useState<string>(seedSlot?.slot || "Body Armours");
  const [baseType, setBaseType] = useState<string>(seedSlot?.baseType || "");
  const [ilvl, setIlvl] = useState<number>(82);
  const [minTierMode, setMinTierMode] = useState<CraftMinTierMode>("mid");

  const [htcMode, setHtcMode] = useState<HtcMode>(seedSlot ? "scratch" : "have_item");
  const [htcExistingText, setHtcExistingText] = useState<string>("");
  const [htcCopied, setHtcCopied] = useState<boolean>(false);
  const [htcTargetText, setHtcTargetText] = useState<string>(
    seedSlot?.mustAffixes?.length
      ? seedSlot.mustAffixes.slice(0, 6).join("\n")
      : "+120 to maximum Life\n+35% to Fire Resistance\n+35% to Cold Resistance"
  );

  const [meta, setMeta] = useState<CraftMetaResponse | null>(null);
  const [rates, setRates] = useState<NinjaRatesSnapshot | null>(null);
  const [refreshingRates, setRefreshingRates] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!seedSlot) return;
    setSlot(seedSlot.slot);
    setBaseType(seedSlot.baseType || "");
    const must =
      seedSlot.mustAffixes.length > 0 ? seedSlot.mustAffixes : seedSlot.affixes.slice(0, 4).map((a) => a.line);
    setHtcTargetText(must.slice(0, 6).join("\n"));
    setHtcMode("scratch");
  }, [seedSlot]);

  const htcLink = useMemo(
    () =>
      buildHtcLinkFromClipboardTexts({
        mode: htcMode,
        targetText: htcTargetText,
        existingText: htcMode === "have_item" ? htcExistingText : undefined,
        fallbackSlot: slot,
        fallbackBaseType: baseType,
        ilvlOverride: ilvl,
        minTierMode,
      }),
    [htcMode, htcTargetText, htcExistingText, slot, baseType, ilvl, minTierMode]
  );

  useEffect(() => {
    let active = true;
    desktopApi
      .getCraftMeta({ slot, ilvl, baseType, league })
      .then((m) => {
        if (!active) return;
        setMeta(m);
        setRates(m.rates);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [slot, ilvl, baseType, league]);

  async function handleRefreshRates() {
    setRefreshingRates(true);
    setError("");
    try {
      const r = await desktopApi.getMarketRates(league, true);
      setRates(r);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setRefreshingRates(false);
    }
  }

  async function openCraftTradeDirect(kind: "exact" | "semi" | "base") {
    setError("");
    try {
      const rawLines = (kind === "semi" && htcMode === "have_item" && htcExistingText.trim()
        ? htcExistingText
        : kind === "base"
        ? ""
        : htcTargetText
      )
        .split(/\r?\n/)
        .map((s) => s.trim())
        .filter(Boolean);
      const affLines = kind === "semi" ? rawLines.slice(0, 2) : rawLines.slice(0, 4);

      await openExternalUrlAsync(async () => {
        const built = await desktopApi.buildTradeUrl({
          league,
          slot,
          baseType: baseType.trim() || undefined,
          rarity: "RARE",
          action: kind === "base" ? "buy_base" : kind === "semi" ? "craft_semi" : "buy_finished",
          affixes: affLines,
          minIlvl: ilvl,
          corrupted: kind === "exact" ? null : false,
        });
        return built.tradeUrl;
      });
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="page">
      {/* 唯一做装主入口：一键带消融必保词缀直达 POE2HTC */}
      <section className="card gold">
        {seedSlot &&
          (seedSlot.socketAnalysis?.exceedsCraftableSockets ||
            (seedSlot.socketAnalysis?.runeUnlockedPools?.length || 0) > 0 ||
            (seedSlot.passiveSynergies?.length || 0) > 0) && (
            <div className="banner info" style={{ marginBottom: 10 }}>
              <span>
                🔬 <strong>{t.slot(seedSlot.slot)} 联动提醒：</strong>
                {seedSlot.passiveSynergies?.length ? `天赋联动「${seedSlot.passiveSynergies.join(" / ")}」；` : ""}
                {seedSlot.socketAnalysis?.advice || ""}
              </span>
            </div>
          )}

        <div className="card-header">
          <span className="card-title">🚀 一键带词缀直达 POE2HTC（精确做装 &amp; 半成品加工）</span>
          <span className="badge green">BD 拆解直通 · 由 POE2HTC 官方求解</span>
        </div>

        <div className="muted" style={{ marginBottom: 12 }}>
          本工作台专注 <strong>BD 实算拆解与抄作业选词</strong>；确定必保词缀后，一键通过{" "}
          <a href="https://poe2htc.com/" target="_blank" rel="noopener noreferrer" style={{ color: "var(--gold)" }}>
            poe2htc.com
          </a>{" "}
          原生分享协议带入基底、变体、物等、已有词缀（含破裂锁词）与目标 T 级，直接获取最准确的通货/精华/预兆做装路线。
        </div>

        <div className="row between" style={{ flexWrap: "wrap", gap: 8, marginBottom: 10 }}>
          <div className="row" style={{ gap: 6 }}>
            <button
              type="button"
              className={`btn sm ${htcMode === "have_item" ? "primary" : ""}`}
              onClick={() => setHtcMode("have_item")}
            >
              🧱 已有半成品继续加工 (I have an item)
            </button>
            <button
              type="button"
              className={`btn sm ${htcMode === "scratch" ? "primary" : ""}`}
              onClick={() => setHtcMode("scratch")}
            >
              ⬜ 从白底规划 / 对比买成品 (Plan from scratch)
            </button>
          </div>

          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            <select
              className="input"
              style={{ width: 135 }}
              value={slot}
              onChange={(e) => setSlot(e.target.value)}
              title="若粘贴的是纯词缀行（无 Item Class 头），将按此槽位映射"
            >
              {Array.from(new Set([slot, ...COMMON_SLOTS, ...(meta?.supportedClasses || [])])).map((s) => (
                <option key={s} value={s}>
                  {t.slot(s)}
                </option>
              ))}
            </select>
            <input
              className="input"
              style={{ width: 150 }}
              placeholder="基底名（可选）"
              value={baseType}
              onChange={(e) => setBaseType(e.target.value)}
            />
            <input
              type="number"
              className="input"
              style={{ width: 68 }}
              min={1}
              max={100}
              value={ilvl}
              title="目标物等 iLvl"
              onChange={(e) => setIlvl(Math.max(1, Math.min(100, Number(e.target.value) || 82)))}
            />
            <select
              className="input"
              style={{ width: 148 }}
              value={minTierMode}
              onChange={(e) => setMinTierMode(e.target.value as CraftMinTierMode)}
            >
              <option value="any">T 级：任意/宽松</option>
              <option value="mid">T 级：按装备数值/中配</option>
              <option value="high">T 级：追高配 T1-T2</option>
            </select>
          </div>
        </div>

        <div className={htcMode === "have_item" ? "grid-2" : ""} style={{ marginBottom: 10 }}>
          {htcMode === "have_item" && (
            <div>
              <div className="row between" style={{ marginBottom: 4 }}>
                <strong>1. 粘贴手里的半成品装备文本（游戏内 Ctrl+C / 市集复制）</strong>
                {htcExistingText && (
                  <button type="button" className="btn sm" onClick={() => setHtcExistingText("")}>
                    清空
                  </button>
                )}
              </div>
              <textarea
                className="textarea"
                style={{ minHeight: 140 }}
                placeholder={
                  "直接 Ctrl+V 粘贴游戏内复制的半成品装备文本：\nItem Class: Body Armours\nRarity: Rare\n...\nItem Level: 82\n--------\n+112 to maximum Life\n+35% to Fire Resistance"
                }
                value={htcExistingText}
                onChange={(e) => setHtcExistingText(e.target.value)}
              />
            </div>
          )}

          <div>
            <div className="row between" style={{ marginBottom: 4 }}>
              <strong>
                {htcMode === "have_item"
                  ? "2. 目标成品装备文本 / 必保词缀（已自动带入当前 BD 槽位）"
                  : "目标装备文本（Ctrl+C）或 PoB2 消融拆出的必保词缀"}
              </strong>
              {htcTargetText && (
                <button type="button" className="btn sm" onClick={() => setHtcTargetText("")}>
                  清空
                </button>
              )}
            </div>
            <textarea
              className="textarea"
              style={{ minHeight: 140 }}
              placeholder={
                "粘贴完整目标装备文本，或每行一条英文词缀：\n+120 to maximum Life\n+35% to Fire Resistance\n+35% to Cold Resistance"
              }
              value={htcTargetText}
              onChange={(e) => setHtcTargetText(e.target.value)}
            />
          </div>
        </div>

        <div className="row between" style={{ flexWrap: "wrap", gap: 8 }}>
          <div className="chips">
            {htcLink.supported ? (
              <>
                <span className="badge blue">基底：{htcLink.baseClass}</span>
                {htcLink.variantHint && <span className="badge purple">变体：{htcLink.variantHint}</span>}
                <span className="badge">iLvl {htcLink.ilvl}</span>
                {htcMode === "have_item" && (
                  <span className="badge gold">已有词缀 {htcLink.transferableExistingCount} 条</span>
                )}
                <span className="badge green">目标词缀 {htcLink.transferableTargetCount} 条</span>
                {htcLink.skippedNotes.length > 0 && (
                  <span className="badge amber" title={htcLink.skippedNotes.join("\n")}>
                    自动跳过 {htcLink.skippedNotes.length} 条非前后缀
                  </span>
                )}
              </>
            ) : (
              <span className="badge red">⚠️ {htcLink.unsupportedReason}</span>
            )}
          </div>

          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn sm"
              disabled={!htcLink.supported || !htcLink.payload}
              title="复制标准 PoE2 装备文本，可直接粘贴进 POE2HTC 的「Paste your item」"
              onClick={() => {
                if (!htcLink.payload) return;
                const txt = buildPoe2PasteTextFromPayload(htcLink.payload);
                void navigator.clipboard?.writeText(txt);
                setHtcCopied(true);
                setTimeout(() => setHtcCopied(false), 1600);
              }}
            >
              {htcCopied ? "✓ 已复制装备文本" : "📋 复制装备文本"}
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={!htcLink.supported}
              onClick={() => openExternalUrl(htcLink.url)}
            >
              🚀 带词缀打开 POE2HTC 计算
            </button>
          </div>
        </div>

        {error && (
          <div className="banner error" style={{ marginTop: 10 }}>
            <span>{error}</span>
          </div>
        )}
      </section>

      {/* 快捷比价区：通货汇率 + 成品/半成品/白底三档市集直达 */}
      <section className="card">
        <div className="row between" style={{ flexWrap: "wrap", gap: 8 }}>
          <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
            <strong style={{ color: "var(--gold)" }}>💱 通货汇率 &amp; 三档市集比价直达</strong>
            {rates ? (
              <>
                <span className={`badge ${rates.source === "poe.ninja" ? "green" : "amber"}`}>
                  {rates.source === "poe.ninja" ? "poe.ninja 实时" : "离线参考汇率"}
                </span>
                <span className="badge gold">1 D ≈ {rates.exaltPerDivine} E</span>
                <span className="badge">1 D ≈ {rates.chaosPerDivine} C</span>
              </>
            ) : (
              <span className="muted">加载中...</span>
            )}
          </div>

          <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
            <button type="button" className="btn sm" disabled={refreshingRates} onClick={() => void handleRefreshRates()}>
              {refreshingRates ? "刷新中..." : "↻ 刷新汇率"}
            </button>
            <button type="button" className="btn sm" onClick={() => void openCraftTradeDirect("exact")}>
              🌐 市集搜成品
            </button>
            <button type="button" className="btn sm" onClick={() => void openCraftTradeDirect("semi")}>
              🧱 市集搜半成品
            </button>
            <button type="button" className="btn sm" onClick={() => void openCraftTradeDirect("base")}>
              ⬜ 市集搜白底
            </button>
          </div>
        </div>
        <div className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>
          💡 建议流程：先点右侧「市集搜成品」看直买价，再点上方「带词缀打开 POE2HTC 计算」核对自做期望成本，低于市集成品价且风险可控时再动手加工。
        </div>
      </section>
    </div>
  );
};
