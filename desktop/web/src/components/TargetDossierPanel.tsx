import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  BilingualTranslator,
  BuildAblationDossier,
  BuildProfileResponse,
  ItemAblationDetail,
  SkillGroupAblationDetail,
  SlotAffixReport,
} from "@contracts";
import { desktopApi } from "../api/workbenchApi";
import { buildOfficialTradeSearchUrl, fmtCompact, openExternalUrl, openExternalUrlAsync } from "../utils/handoffParser";
import { DossierAscendancyTab } from "./dossier/DossierAscendancyTab";
import { DossierCopyTab } from "./dossier/DossierCopyTab";
import { DossierItemDetail } from "./dossier/DossierItemDetail";
import { DossierSkillDetail } from "./dossier/DossierSkillDetail";
import {
  COPY_TIER_CLASS,
  fmtD,
  fmtLoss,
  makeItemKey,
  pickLockedAffixesForDossier,
  ROLE_BADGE_CLASS,
  type DossierTab,
  type ResolvedTradeMeta,
} from "./dossier/dossierUtils";

interface TargetDossierPanelProps {
  t: BilingualTranslator;
  league: string;
  targetProfile: BuildProfileResponse;
  dossier: BuildAblationDossier | null;
  dossierLoading: boolean;
  dossierError: string;
  pricingBusy: string;
  pricingStatus: string;
  onRefreshNinjaPrices: () => void;
  onOverrideItemPrice: (slot: string, divineValue: number, itemName?: string) => Promise<void>;
  onRefreshDossier: () => void;
  onOpenCraftWithSlot: (rep: SlotAffixReport) => void;
}

const TargetDossierPanelInner: React.FC<TargetDossierPanelProps> = ({
  t,
  league,
  targetProfile,
  dossier,
  dossierLoading,
  dossierError,
  pricingBusy,
  pricingStatus,
  onRefreshNinjaPrices,
  onOverrideItemPrice,
  onRefreshDossier,
  onOpenCraftWithSlot,
}) => {
  const [tab, setTab] = useState<DossierTab>("gear");
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    try {
      return window.localStorage.getItem("poe2wb.dossierCollapsed") === "1";
    } catch {
      return false;
    }
  });
  const [sortBy, setSortBy] = useState<"impact" | "cost">("impact");
  const [manualPriceInput, setManualPriceInput] = useState<Record<string, string>>({});
  const [slotPriceMsg, setSlotPriceMsg] = useState<Record<string, string>>({});
  const [slotTradeLink, setSlotTradeLink] = useState<Record<string, { url: string; label: string }>>({});
  const [openingTradeKey, setOpeningTradeKey] = useState<string | null>(null);
  const [selectedItemKey, setSelectedItemKey] = useState<string | null>(null);
  const [selectedSkillIdx, setSelectedSkillIdx] = useState<number | null>(null);
  const [isSingleCol, setIsSingleCol] = useState<boolean>(() =>
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia("(max-width: 1080px)").matches
      : false
  );
  const [resolvedTradeUrls, setResolvedTradeUrls] = useState<Record<string, ResolvedTradeMeta>>({});
  const detailPanelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(max-width: 1080px)");
    const onChange = (e: MediaQueryListEvent) => setIsSingleCol(e.matches);
    setIsSingleCol(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // 切换目标 BD 消融账本时重置展开状态，防止残留旧键导致点击错乱
  useEffect(() => {
    setSelectedItemKey(null);
    setSelectedSkillIdx(null);
    setResolvedTradeUrls({});
  }, [dossier?.buildHash]);

  // 展开新详情时，若抽屉位于视口边缘外，平滑滚动至可见区域
  useEffect(() => {
    if (!selectedItemKey && selectedSkillIdx === null) return;
    const raf = window.requestAnimationFrame(() => {
      detailPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    return () => window.cancelAnimationFrame(raf);
  }, [selectedItemKey, selectedSkillIdx]);

  const itemsList = useMemo(() => {
    const raw = dossier?.items || [];
    if (sortBy === "cost") {
      return [...raw].sort((a, b) => (b.cost?.estimatedDivine || 0) - (a.cost?.estimatedDivine || 0));
    }
    return raw;
  }, [dossier, sortBy]);

  const jewelsList = useMemo(() => {
    const raw = dossier?.jewels || [];
    if (sortBy === "cost") {
      return [...raw].sort((a, b) => (b.cost?.estimatedDivine || 0) - (a.cost?.estimatedDivine || 0));
    }
    return raw;
  }, [dossier, sortBy]);

  const skillsList = dossier?.skills || [];

  const maxItemImpact = useMemo(() => {
    const all = [...(dossier?.items || []), ...(dossier?.jewels || [])];
    return {
      dps: Math.max(10, ...all.map((x) => Math.abs(x.compensatedDpsLossPct ?? x.dpsLossPct ?? 0))),
      ehp: Math.max(10, ...all.map((x) => Math.abs(x.compensatedEhpLossPct ?? x.ehpLossPct ?? 0))),
    };
  }, [dossier]);

  const activeItemEntries = useMemo(() => {
    const list = tab === "jewels" ? jewelsList : itemsList;
    return list.map((item, idx) => ({ item, key: makeItemKey(item, idx), idx }));
  }, [tab, itemsList, jewelsList]);

  const selectedItemEntry = useMemo(() => {
    if (!selectedItemKey || (tab !== "gear" && tab !== "jewels")) return null;
    return activeItemEntries.find((e) => e.key === selectedItemKey) || null;
  }, [selectedItemKey, tab, activeItemEntries]);

  const selectedItem = selectedItemEntry?.item || null;

  const selectedSkillIdxPos = useMemo(() => {
    if (selectedSkillIdx === null || tab !== "skills") return -1;
    return skillsList.findIndex((s) => s.index === selectedSkillIdx);
  }, [selectedSkillIdx, tab, skillsList]);

  const selectedSkill = selectedSkillIdxPos >= 0 ? skillsList[selectedSkillIdxPos] : null;

  // 展开某装备详情时，后台预解析带词缀筛选的官方市集链接，点击即可同步直达且不被弹窗拦截
  useEffect(() => {
    if (!selectedItem || !selectedItemKey) return;
    if (resolvedTradeUrls[selectedItemKey]?.budget) return;
    let cancelled = false;
    const isUnique = (selectedItem.rarity || "").toUpperCase() === "UNIQUE";
    const needSockets = selectedItem.runeImpact?.exceedsCraftableSockets ? selectedItem.socketCount : null;

    const reqBudget = desktopApi
      .buildTradeUrl({
        league,
        slot: selectedItem.slot,
        itemName: selectedItem.name,
        baseType: selectedItem.baseType,
        rarity: selectedItem.rarity,
        action: isUnique ? "buy_unique" : "buy_budget",
        lockBase: isUnique ? false : Boolean(selectedItem.requireBaseType),
        useWeighted: !isUnique,
        affixes: isUnique ? [] : pickLockedAffixesForDossier(selectedItem, "budget"),
        minSockets: needSockets,
      })
      .catch(() => null);

    const reqSemi = !isUnique
      ? desktopApi
          .buildTradeUrl({
            league,
            slot: selectedItem.slot,
            itemName: selectedItem.name,
            baseType: selectedItem.baseType,
            rarity: selectedItem.rarity,
            action: "craft_semi",
            lockBase: true,
            useWeighted: true,
            affixes: pickLockedAffixesForDossier(selectedItem, "semi"),
            minSockets: needSockets,
          })
          .catch(() => null)
      : Promise.resolve(null);

    Promise.all([reqBudget, reqSemi]).then(([bRes, sRes]) => {
      if (cancelled) return;
      if (bRes || sRes) {
        setResolvedTradeUrls((prev) => ({
          ...prev,
          [selectedItemKey]: {
            budget: bRes?.tradeUrl,
            semi: sRes?.tradeUrl,
            statCount: bRes?.matchedStatCount ?? 0,
            mode: bRes?.mode,
            weightedTerms: bRes?.weightedTerms || [],
            targetScore: bRes?.targetScore,
            minScore: bRes?.minScore,
            equipmentFloors: bRes?.equipmentFloors,
            mandatorySkills: bRes?.mandatorySkills || [],
          },
        }));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [selectedItem, selectedItemKey, league, resolvedTradeUrls]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem("poe2wb.dossierCollapsed", next ? "1" : "0");
      } catch {
        // ignore storage errors
      }
      return next;
    });
  }, []);

  const handleTabChange = useCallback(
    (next: DossierTab) => {
      setTab(next);
      setSelectedItemKey(null);
      setSelectedSkillIdx(null);
      if (collapsed) {
        setCollapsed(false);
        try {
          window.localStorage.setItem("poe2wb.dossierCollapsed", "0");
        } catch {
          // ignore
        }
      }
    },
    [collapsed]
  );

  const handleTradeDirect = useCallback(
    async (item: ItemAblationDetail, itemKey: string, kind: "budget" | "semi") => {
      const isUnique = (item.rarity || "").toUpperCase() === "UNIQUE";
      const cached = resolvedTradeUrls[itemKey];
      const cachedUrl = kind === "semi" ? cached?.semi : cached?.budget;
      if (cachedUrl) {
        openExternalUrl(cachedUrl);
        const label =
          !isUnique && cached?.mode === "weighted" && cached?.minScore
            ? `已打开官方加权市集（Weighted Sum ≥ ${cached.minScore} / 目标 ${cached.targetScore}）`
            : "已打开官方市集搜索页";
        setSlotTradeLink((prev) => ({ ...prev, [item.slot]: { url: cachedUrl, label } }));
        return;
      }

      const opKey = `${itemKey}:${kind}`;
      setOpeningTradeKey(opKey);
      try {
        const needSockets = item.runeImpact?.exceedsCraftableSockets ? item.socketCount : null;
        let builtMeta: { mode?: string; minScore?: number; targetScore?: number } = {};
        const finalUrl = await openExternalUrlAsync(async () => {
          const res = await desktopApi.buildTradeUrl({
            league,
            slot: item.slot,
            itemName: item.name,
            baseType: item.baseType,
            rarity: item.rarity,
            action: isUnique ? "buy_unique" : kind === "semi" ? "craft_semi" : "buy_budget",
            lockBase: isUnique ? false : kind === "semi" ? true : Boolean(item.requireBaseType),
            useWeighted: !isUnique,
            affixes: isUnique ? [] : pickLockedAffixesForDossier(item, kind),
            minSockets: needSockets,
          });
          builtMeta = { mode: res.mode, minScore: res.minScore, targetScore: res.targetScore };
          setResolvedTradeUrls((prev) => ({
            ...prev,
            [itemKey]: {
              ...(prev[itemKey] || {}),
              [kind]: res.tradeUrl,
              statCount: res.matchedStatCount ?? prev[itemKey]?.statCount ?? 0,
              mode: res.mode || prev[itemKey]?.mode,
              weightedTerms: res.weightedTerms?.length ? res.weightedTerms : prev[itemKey]?.weightedTerms || [],
              targetScore: res.targetScore ?? prev[itemKey]?.targetScore,
              minScore: res.minScore ?? prev[itemKey]?.minScore,
              equipmentFloors: res.equipmentFloors || prev[itemKey]?.equipmentFloors,
              mandatorySkills: res.mandatorySkills?.length ? res.mandatorySkills : prev[itemKey]?.mandatorySkills || [],
            },
          }));
          return res.tradeUrl || buildOfficialTradeSearchUrl(league, item.name, item.baseType);
        });
        if (finalUrl) {
          const label =
            !isUnique && builtMeta.mode === "weighted" && builtMeta.minScore
              ? `已打开官方加权市集（Weighted Sum ≥ ${builtMeta.minScore} / 目标 ${builtMeta.targetScore}）`
              : "已打开官方市集搜索页";
          setSlotTradeLink((prev) => ({ ...prev, [item.slot]: { url: finalUrl, label } }));
        }
      } catch {
        const fallback = buildOfficialTradeSearchUrl(league, item.name, item.baseType);
        openExternalUrl(fallback);
        setSlotTradeLink((prev) => ({ ...prev, [item.slot]: { url: fallback, label: "已打开官方市集（兜底查询）" } }));
      } finally {
        setOpeningTradeKey(null);
      }
    },
    [league, resolvedTradeUrls]
  );

  const handleManualPriceChange = useCallback((slot: string, value: string) => {
    setManualPriceInput((prev) => ({ ...prev, [slot]: value }));
  }, []);

  const handleSaveManualPrice = useCallback(
    async (slot: string, itemName: string) => {
      const v = Number(manualPriceInput[slot]);
      if (!(v > 0)) return;
      await onOverrideItemPrice(slot, v, itemName);
      setManualPriceInput((prev) => ({ ...prev, [slot]: "" }));
      setSlotPriceMsg((prev) => ({ ...prev, [slot]: `已更正为 ${v} D（已写入本地缓存）` }));
    },
    [manualPriceInput, onOverrideItemPrice]
  );

  const handleCloseItemDetail = useCallback(() => setSelectedItemKey(null), []);
  const handleCloseSkillDetail = useCallback(() => setSelectedSkillIdx(null), []);

  function renderCompactItemCard(item: ItemAblationDetail, key: string) {
    const isSelected = selectedItemKey === key;
    const netDps = item.compensatedDpsLossPct ?? item.dpsLossPct ?? 0;
    const netEhp = item.compensatedEhpLossPct ?? item.ehpLossPct ?? 0;
    const isUnique = (item.rarity || "").toUpperCase() === "UNIQUE";
    const cost = item.cost || { estimatedDivine: null, priceSource: "unknown" };
    const affStats = item.affixStats || { total: 0, must: 0, nice: 0, cut: 0, dead: 0 };
    const synergies = item.passiveSynergies || [];

    return (
      <div
        key={key}
        role="button"
        tabIndex={0}
        aria-expanded={isSelected}
        className={`dossier-card ${isSelected ? "selected" : ""}`}
        onClick={() => setSelectedItemKey(isSelected ? null : key)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setSelectedItemKey(isSelected ? null : key);
          }
        }}
      >
        <div className="row between" style={{ gap: 6, flexWrap: "nowrap" }}>
          <div className="row" style={{ gap: 6, minWidth: 0, flexWrap: "nowrap" }}>
            <span className={`badge ${isUnique ? "unique" : "blue"}`} style={{ flexShrink: 0 }}>
              {item.kind === "jewel" ? (isUnique ? "暗金珠" : "黄珠") : t.slot(item.slot)}
            </span>
            <strong className="ellipsis-text">{t.itemName(item.name, item.baseType)}</strong>
          </div>
          <div className="row" style={{ gap: 5, flexShrink: 0 }}>
            <span className={`badge ${ROLE_BADGE_CLASS[item.roleKind] || ""}`}>{item.roleLabel}</span>
            <span
              className={`badge ${
                cost.estimatedDivine ? (cost.priceSource === "estimate" ? "amber" : "gold") : ""
              }`}
            >
              {fmtD(cost.estimatedDivine, cost.priceSource)}
            </span>
          </div>
        </div>

        <div className="chips" style={{ marginTop: 6 }}>
          {Math.abs(netDps) >= 0.5 && (
            <span className={`metric-pill ${netDps >= 10 ? "high" : ""}`}>DPS {fmtLoss(netDps)}</span>
          )}
          {Math.abs(netEhp) >= 0.5 && (
            <span className={`metric-pill ${netEhp >= 10 ? "def" : ""}`}>EHP {fmtLoss(netEhp)}</span>
          )}
          {(item.spiritLoss || 0) >= 10 && <span className="metric-pill warn">精魂 -{Math.round(item.spiritLoss)}</span>}
          {(item.maxResDrop || 0) >= 20 && <span className="metric-pill">抗性 -{Math.round(item.maxResDrop)}%</span>}
          {item.attrBroken && <span className="metric-pill warn">属性门槛</span>}
          {item.requireBaseType && !isUnique && (
            <span className="metric-pill warn" title={item.baseTypeReason || "需锁定基底"}>
              🔒 锁基底
            </span>
          )}
          {(item.runeImpact?.socketedItems?.length || 0) > 0 && (() => {
            const sItems = item.runeImpact!.socketedItems!;
            const topCore = [...sItems].sort(
              (a, b) =>
                (b.dpsLossPct + b.ehpLossPct + (b.grantsKeystone ? 25 : 0)) -
                (a.dpsLossPct + a.ehpLossPct + (a.grantsKeystone ? 25 : 0))
            )[0];
            const hasCore = sItems.some((s) => s.tier === "must");
            return (
              <span
                className={`metric-pill ${hasCore ? "high" : ""}`}
                title={sItems.map((s) => `${s.name} (${s.purposeLabel})`).join(" / ")}
              >
                💎 {sItems.length}插槽物
                {topCore && topCore.dpsLossPct >= 3
                  ? ` · ${topCore.name} -${topCore.dpsLossPct.toFixed(1)}%`
                  : topCore && topCore.ehpLossPct >= 5
                  ? ` · ${topCore.name} EHP -${topCore.ehpLossPct.toFixed(1)}%`
                  : ""}
              </span>
            );
          })()}
          {cost.dpsPerDivine != null && cost.dpsPerDivine > 0 && (
            <span className="metric-pill roi">{cost.dpsPerDivine}%/D</span>
          )}
          <span className={`badge ${COPY_TIER_CLASS[item.copyTier] || ""}`} style={{ marginLeft: "auto" }}>
            {item.copyTierLabel}
          </span>
        </div>

        {(netDps >= 1 || netEhp >= 1) && (
          <div className="dossier-bars">
            {netDps >= 1 && (
              <div className="dossier-bar-row">
                <span>DPS</span>
                <div className="delta-bar-track">
                  <div
                    className="delta-bar-fill dps"
                    style={{ width: `${Math.min(100, Math.max(4, Math.round((netDps / maxItemImpact.dps) * 100)))}%` }}
                  />
                </div>
              </div>
            )}
            {netEhp >= 1 && (
              <div className="dossier-bar-row">
                <span>EHP</span>
                <div className="delta-bar-track">
                  <div
                    className="delta-bar-fill ehp"
                    style={{ width: `${Math.min(100, Math.max(4, Math.round((netEhp / maxItemImpact.ehp) * 100)))}%` }}
                  />
                </div>
              </div>
            )}
          </div>
        )}

        <div className="row between" style={{ marginTop: 6, gap: 8 }}>
          <span className="muted ellipsis-text" style={{ fontSize: 11 }}>
            {affStats.total > 0
              ? `必保 ${affStats.must} · 增益 ${affStats.nice} · 可砍 ${
                  affStats.cut + affStats.dead
                }${synergies.length > 0 ? ` · ${synergies[0]}` : ""}`
              : item.coreRoleSummary}
          </span>
          <span className="dossier-expand-hint">{isSelected ? "收起详情 ▴" : "点看词缀目的 ▸"}</span>
        </div>
      </div>
    );
  }

  function renderCompactSkillCard(sg: SkillGroupAblationDetail) {
    const isSelected = selectedSkillIdx === sg.index;
    const supportNames = sg.gems
      .filter((g) => g.isSupport)
      .slice(0, 4)
      .map((g) => t.term(g.name, "gems"));

    return (
      <div
        key={sg.index}
        role="button"
        tabIndex={0}
        className={`dossier-card ${isSelected ? "selected" : ""}`}
        onClick={() => setSelectedSkillIdx(isSelected ? null : sg.index)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setSelectedSkillIdx(isSelected ? null : sg.index);
          }
        }}
      >
        <div className="row between" style={{ gap: 6, flexWrap: "nowrap" }}>
          <div className="row" style={{ gap: 6, minWidth: 0, flexWrap: "nowrap" }}>
            {sg.isMain && (
              <span className="badge gold" style={{ flexShrink: 0 }}>
                主技能
              </span>
            )}
            {sg.weaponSet && (
              <span className="badge blue" style={{ flexShrink: 0 }}>
                Set {sg.weaponSet}
              </span>
            )}
            <strong className="ellipsis-text">{t.term(sg.mainGemName, "gems")}</strong>
          </div>
          <span className={`badge ${ROLE_BADGE_CLASS[sg.roleKind] || ""}`} style={{ flexShrink: 0 }}>
            {sg.roleLabel}
          </span>
        </div>

        <div className="chips" style={{ marginTop: 6 }}>
          {!sg.isMain && Math.abs(sg.dpsLossPct) >= 0.5 && (
            <span className={`metric-pill ${sg.dpsLossPct >= 10 ? "high" : ""}`}>关掉 DPS {fmtLoss(sg.dpsLossPct)}</span>
          )}
          {!sg.isMain && Math.abs(sg.ehpLossPct) >= 0.5 && (
            <span className={`metric-pill ${sg.ehpLossPct >= 10 ? "def" : ""}`}>EHP {fmtLoss(sg.ehpLossPct)}</span>
          )}
          {!sg.isMain && (sg.physMaxHitLossPct ?? 0) >= 2 && (
            <span className="metric-pill def">物一击 {fmtLoss(sg.physMaxHitLossPct)}</span>
          )}
          {!sg.isMain && (sg.maxResDrop ?? 0) >= 5 && (
            <span className="metric-pill def">抗性 -{Math.round(sg.maxResDrop)}%</span>
          )}
          {!sg.isMain && (sg.deflectDrop ?? 0) >= 2 && (
            <span className="metric-pill def">偏转 -{sg.deflectDrop!.toFixed(0)}%</span>
          )}
          {!sg.isMain && (sg.evadeDrop ?? 0) >= 2 && (
            <span className="metric-pill def">闪避 -{sg.evadeDrop!.toFixed(0)}%</span>
          )}
          {!sg.isMain && (sg.lifeRecoveryDrop ?? 0) >= 10 && (
            <span className="metric-pill def">回血 -{Math.round(sg.lifeRecoveryDrop!)}/s</span>
          )}
          {(sg.defenseAnalysis?.defenseLayers?.length || 0) > 0 && (
            <span className="metric-pill def" title={sg.defenseAnalysis?.defenseSummary || ""}>
              🛡️ {sg.defenseAnalysis!.defenseLayers[0]}
            </span>
          )}
          {sg.spiritSaved > 0 && <span className="metric-pill warn">占精魂 {Math.round(sg.spiritSaved)}</span>}
          {sg.maxDowngrade2020LossPct != null && (
            <span className="metric-pill roi">21→20 最多-{sg.maxDowngrade2020LossPct}%</span>
          )}
          <span className="metric-pill">{sg.gems.length} 连</span>
          <span className={`badge ${COPY_TIER_CLASS[sg.copyTier] || ""}`} style={{ marginLeft: "auto" }}>
            {sg.copyTierLabel}
          </span>
        </div>

        <div className="row between" style={{ marginTop: 6, gap: 8 }}>
          <span className="muted ellipsis-text" style={{ fontSize: 11 }}>
            {supportNames.length > 0 ? `辅助：${supportNames.join(" · ")}` : sg.coreRoleSummary}
          </span>
          <span className="dossier-expand-hint">{isSelected ? "收起详情 ▴" : "点看宝石消融 ▸"}</span>
        </div>
      </div>
    );
  }

  return (
    <section className="card">
      <div className="card-header" style={collapsed ? { marginBottom: 0 } : undefined}>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <span className="card-title">
            🧪 目标 BD 引擎消融剖析（装备 · 插槽 · 珠宝 · 技能攻防 · 升华）
          </span>
          {dossierLoading && <span className="badge blue">⚡ 引擎正在并行消融全部物件与词缀...</span>}
          {!dossierLoading && dossier && (
            <span className="badge green">
              ✓ 已实测 {dossier.summary.totalSimRuns} 次 ({dossier.summary.totalAffixesTested} 词缀
              {dossier.cached ? " · 缓存秒开" : ` · ${(dossier.elapsedMs / 1000).toFixed(1)}s`})
            </span>
          )}
          {dossier && (
            <span className="badge gold">
              {dossier.totalEstimatedDivine > 0
                ? `已核造价 ${dossier.totalEstimatedDivine} D（${dossier.pricedItemCount}/${dossier.totalItemCount} 件${
                    dossier.gemsCostDivine ? ` · 含宝石 ${dossier.gemsCostDivine}D` : ""
                  }）`
                : `待核价（${dossier.league || league}）`}
            </span>
          )}
          {collapsed && targetProfile.simulation && (
            <>
              <span className="badge gold">DPS {fmtCompact(targetProfile.simulation.dps)}</span>
              <span className="badge green">EHP {fmtCompact(targetProfile.simulation.ehp)}</span>
            </>
          )}
        </div>

        <div className="row" style={{ gap: 6 }}>
          {!collapsed && (
            <>
              <button
                type="button"
                className={`btn sm ${tab === "gear" ? "primary" : ""}`}
                onClick={() => handleTabChange("gear")}
              >
                🛡️ 装备 ({dossier ? dossier.items.length : targetProfile.build.equippedItems.length})
              </button>
              <button
                type="button"
                className={`btn sm ${tab === "jewels" ? "primary" : ""}`}
                onClick={() => handleTabChange("jewels")}
              >
                💠 珠宝 ({dossier ? dossier.jewels.length : targetProfile.build.jewels.length})
              </button>
              <button
                type="button"
                className={`btn sm ${tab === "skills" ? "primary" : ""}`}
                onClick={() => handleTabChange("skills")}
              >
                🔥 技能 ({dossier ? dossier.skills.length : targetProfile.build.skillGroups.length})
              </button>
              <button
                type="button"
                className={`btn sm ${tab === "ascendancy" ? "primary" : ""}`}
                onClick={() => handleTabChange("ascendancy")}
              >
                👑 升华 ({dossier?.ascendancy?.nodes.length ?? targetProfile.build.passiveTree.ascendancyNodes.length})
              </button>
              <button
                type="button"
                className={`btn sm ${tab === "copy" ? "primary" : ""}`}
                onClick={() => handleTabChange("copy")}
              >
                🧭 抄作业底座
              </button>
              {(tab === "gear" || tab === "jewels") && dossier && (
                <button
                  type="button"
                  className="btn sm"
                  title="切换按消融贡献排序或按造价排序"
                  onClick={() => setSortBy((s) => (s === "impact" ? "cost" : "impact"))}
                >
                  {sortBy === "impact" ? "↓ 按贡献" : "↓ 按造价"}
                </button>
              )}
              <button
                type="button"
                className="btn sm"
                disabled={dossierLoading}
                title="强制重新运行 PoB2 引擎全量消融"
                onClick={onRefreshDossier}
              >
                ↻
              </button>
            </>
          )}
          <button
            type="button"
            className="btn sm"
            aria-expanded={!collapsed}
            title={collapsed ? "展开目标 BD 引擎消融剖析详情" : "收起剖析面板，为下方抄作业腾出空间"}
            onClick={toggleCollapsed}
          >
            {collapsed ? "▾ 展开剖析" : "▴ 收起"}
          </button>
        </div>
      </div>

      {!collapsed && (
        <>
          <div
            className="kpi-card"
            style={{ marginBottom: 10, padding: "8px 10px", display: "flex", flexDirection: "column", gap: 6 }}
          >
            <div className="row between" style={{ flexWrap: "wrap", gap: 8 }}>
              <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                <strong style={{ fontSize: 12, color: "var(--gold-strong)" }}>💰 目标 BD 造价与本地缓存（{league}）</strong>
                {dossier && (
                  <span className="muted" style={{ fontSize: 11 }}>
                    已核 {dossier.pricedItemCount} 件装备/珠宝
                    {dossier.gemsCostDivine ? ` + ${dossier.pricedGems?.length || 0} 颗血脉宝石 (${dossier.gemsCostDivine}D)` : ""}
                    {dossier.unpricedItemCount ? ` · 另有 ${dossier.unpricedItemCount} 件黄装可点卡片「直达市集」看底价后手填` : " · 全部已核"}
                  </span>
                )}
              </div>
              <div className="row" style={{ gap: 5, flexWrap: "wrap" }}>
                <button
                  type="button"
                  className="btn sm"
                  disabled={Boolean(pricingBusy)}
                  title="从 poe.ninja 拉取当前赛季全部暗金装备、暗金珠宝、药剂与血脉宝石实时价，并直接更正到本地缓存（不重跑消融）"
                  onClick={onRefreshNinjaPrices}
                >
                  {pricingBusy === "ninja" ? "同步中..." : "🔄 刷新 Ninja 暗金/宝石价"}
                </button>
              </div>
            </div>
            <div className="muted" style={{ fontSize: 11 }}>
              💡 消融底座已持久化在本地；刷新 Ninja 价、切换赛季或展开卡片手填 D 价时均直接在本地结果上更正造价，不会重新消融。
            </div>
            {pricingStatus && (
              <div className="muted" style={{ fontSize: 11, color: pricingStatus.includes("失败") ? "var(--amber)" : "var(--green)" }}>
                {pricingStatus}
              </div>
            )}
          </div>

          {targetProfile.simulation && (
            <div className="chips" style={{ marginBottom: 10 }}>
              <span className="badge gold">DPS {fmtCompact(targetProfile.simulation.dps)}</span>
              <span className="badge green">EHP {fmtCompact(targetProfile.simulation.ehp)}</span>
              <span className="badge">
                血/盾 {Math.round(targetProfile.simulation.life)}/{Math.round(targetProfile.simulation.es)}
              </span>
              <span className="res-strip">
                <span className={`res-chip fire ${Math.round(targetProfile.simulation.fireRes) < 75 ? "uncapped" : ""}`}>
                  火 {Math.round(targetProfile.simulation.fireRes)}%
                </span>
                <span className={`res-chip cold ${Math.round(targetProfile.simulation.coldRes) < 75 ? "uncapped" : ""}`}>
                  冰 {Math.round(targetProfile.simulation.coldRes)}%
                </span>
                <span className={`res-chip lightning ${Math.round(targetProfile.simulation.lightningRes) < 75 ? "uncapped" : ""}`}>
                  电 {Math.round(targetProfile.simulation.lightningRes)}%
                </span>
                <span className={`res-chip chaos ${Math.round(targetProfile.simulation.chaosRes) < 0 ? "uncapped" : ""}`}>
                  混 {Math.round(targetProfile.simulation.chaosRes)}%
                </span>
              </span>
              <span className="badge blue">
                暴击 {targetProfile.simulation.critChance}% / 暴伤 {Math.round(targetProfile.simulation.critMult)}%
              </span>
              {(() => {
                const sim = targetProfile.simulation;
                const topTravel = sim.travelSkills?.[0];
                const msPct = sim.moveSpeed ?? Math.round((sim.moveSpeedMod - 1) * 100);
                return (
                  <>
                    {topTravel && (
                      <span className="badge purple" title={sim.mobilitySummary || "PoB2 引擎实算位移技能速度"}>
                        位移 {t.term(topTravel.name, "gems")}{" "}
                        {topTravel.cooldownSec >= 0.15 && topTravel.usesPerSec <= 2.5
                          ? `CD ${topTravel.cooldownSec.toFixed(2)}s`
                          : topTravel.usesPerSec > 0
                          ? `${topTravel.usesPerSec.toFixed(2)}/s`
                          : ""}
                      </span>
                    )}
                    <span className="badge" title={sim.mobilitySummary || "PoB2 引擎实算步行移速"}>
                      移速 {msPct >= 0 ? `+${Math.round(msPct)}%` : `${Math.round(msPct)}%`}
                    </span>
                    {(sim.duration ?? 0) > 0 && (
                      <span className="badge" title="PoB2 实算主技能持续时间">
                        持续 {sim.duration!.toFixed(2)}s
                      </span>
                    )}
                    {(sim.projSpeedMod ?? 0) > 0 && Math.abs((sim.projSpeedMod ?? 1) - 1) >= 0.03 && (
                      <span className="badge" title="PoB2 实算投射物速度倍率">
                        投速 ×{sim.projSpeedMod!.toFixed(2)}
                      </span>
                    )}
                    {(sim.projectileCount ?? 0) > 1 && (
                      <span className="badge" title="PoB2 实算投射物数量">
                        {sim.projectileCount} 投射物
                      </span>
                    )}
                    {(sim.aoeRadius ?? 0) > 0 && (
                      <span className="badge" title="PoB2 实算技能范围半径">
                        范围 {sim.aoeRadius!.toFixed(1)}m
                      </span>
                    )}
                    {(sim.skillStageCount ?? 0) > 0 && (
                      <span className="badge amber" title="PoB2 实算技能阶段数">
                        阶段 {sim.skillStageCount}
                        {(sim.skillStageMax ?? 0) > 0 ? `/${sim.skillStageMax}` : ""}
                      </span>
                    )}
                  </>
                );
              })()}
              {dossier && (
                <span className="muted" style={{ marginLeft: "auto", fontSize: 11 }}>
                  点击任意卡片展开逐条词缀 / 辅助宝石消融详情
                </span>
              )}
            </div>
          )}

          {dossierError && (
            <div className="banner error" style={{ marginBottom: 10 }}>
              <span>全量消融失败：{dossierError}</span>
              <button type="button" className="btn sm" onClick={onRefreshDossier}>
                重试
              </button>
            </div>
          )}

          {!dossier && (
            <div className="grid-2">
              {tab === "gear" &&
                targetProfile.build.equippedItems.map((it) => (
                  <div key={`${it.slot}-${it.itemId}`} className="kpi-card">
                    <div className="row between">
                      <div className="row" style={{ gap: 6 }}>
                        <span className={`badge ${it.rarity.toUpperCase() === "UNIQUE" ? "unique" : "blue"}`}>
                          {t.slot(it.slot)}
                        </span>
                        <strong>{t.itemName(it.name, it.baseType)}</strong>
                      </div>
                      <span className="muted">{dossierLoading ? "消融中..." : "待消融"}</span>
                    </div>
                  </div>
                ))}
              {tab === "jewels" &&
                targetProfile.build.jewels.map((j) => (
                  <div key={`${j.slot}-${j.itemId}`} className="kpi-card">
                    <div className="row between">
                      <div className="row" style={{ gap: 6 }}>
                        <span className={`badge ${j.rarity.toUpperCase() === "UNIQUE" ? "unique" : "blue"}`}>
                          {j.rarity.toUpperCase() === "UNIQUE" ? "暗金珠" : "黄珠"}
                        </span>
                        <strong>{t.itemName(j.name, j.baseType)}</strong>
                      </div>
                      <span className="muted">{dossierLoading ? "消融中..." : "待消融"}</span>
                    </div>
                  </div>
                ))}
              {tab === "skills" &&
                targetProfile.build.skillGroups.map((sg) => (
                  <div key={sg.index} className="kpi-card">
                    <div className="row between">
                      <strong>{sg.gems[0]?.name ? t.term(sg.gems[0].name, "gems") : "技能组"}</strong>
                      <span className="muted">{dossierLoading ? "消融中..." : `${sg.gems.length} 连`}</span>
                    </div>
                  </div>
                ))}
              {tab === "ascendancy" &&
                targetProfile.build.passiveTree.ascendancyNodes.map((n) => (
                  <div key={n.id} className="kpi-card">
                    <div className="row between">
                      <strong>{t.term(n.name, "passives")}</strong>
                      <span className="muted">{dossierLoading ? "升华消融中..." : "待消融"}</span>
                    </div>
                  </div>
                ))}
              {tab === "copy" && (
                <div className="muted">
                  {dossierLoading ? "正在运行引擎消融以生成抄作业基础信息..." : "点击右上角 ↻ 运行全量消融。"}
                </div>
              )}
            </div>
          )}

          {dossier && (tab === "gear" || tab === "jewels") && (() => {
            if (activeItemEntries.length === 0) {
              return <div className="muted">{tab === "jewels" ? "该 BD 暂无已插放的珠宝。" : "暂无装备数据。"}</div>;
            }
            const selIdx = selectedItemEntry ? selectedItemEntry.idx : -1;
            const insertAfterIdx =
              selIdx >= 0
                ? isSingleCol
                  ? selIdx
                  : Math.min(activeItemEntries.length - 1, Math.floor(selIdx / 2) * 2 + 1)
                : -1;
            return (
              <div className="grid-2">
                {activeItemEntries.map(({ item, key, idx }) => (
                  <React.Fragment key={key}>
                    {renderCompactItemCard(item, key)}
                    {idx === insertAfterIdx && selectedItem && selectedItemKey && (
                      <DossierItemDetail
                        t={t}
                        league={league}
                        item={selectedItem}
                        itemKey={selectedItemKey}
                        resolved={resolvedTradeUrls[selectedItemKey]}
                        openingTradeKey={openingTradeKey}
                        slotTradeLink={slotTradeLink[selectedItem.slot]}
                        manualPriceValue={manualPriceInput[selectedItem.slot] ?? ""}
                        slotPriceMsg={slotPriceMsg[selectedItem.slot]}
                        simDps={targetProfile.simulation?.dps || 0}
                        simEhp={targetProfile.simulation?.ehp || 0}
                        detailPanelRef={detailPanelRef}
                        onTradeDirect={handleTradeDirect}
                        onOpenCraftWithSlot={onOpenCraftWithSlot}
                        onManualPriceChange={handleManualPriceChange}
                        onSaveManualPrice={handleSaveManualPrice}
                        onClose={handleCloseItemDetail}
                      />
                    )}
                  </React.Fragment>
                ))}
              </div>
            );
          })()}

          {dossier && tab === "skills" && (() => {
            if (skillsList.length === 0) {
              return <div className="muted">暂无技能数据。</div>;
            }
            const defList = dossier.skillDefenseSummary || [];
            const insertAfterIdx =
              selectedSkillIdxPos >= 0
                ? isSingleCol
                  ? selectedSkillIdxPos
                  : Math.min(skillsList.length - 1, Math.floor(selectedSkillIdxPos / 2) * 2 + 1)
                : -1;
            return (
              <>
                {defList.length > 0 && (
                  <div
                    className="kpi-card"
                    style={{ marginBottom: 10, padding: "8px 10px", background: "rgba(74, 222, 128, 0.06)" }}
                  >
                    <div className="row between" style={{ marginBottom: 6 }}>
                      <strong style={{ fontSize: 12, color: "var(--green)" }}>
                        🛡️ 技能端防御体系总览（共识别 {defList.length} 组防御 / 回复 / 控场技能）
                      </strong>
                      <span className="muted" style={{ fontSize: 11 }}>
                        点击下方任意技能卡片可展开辅助宝石逐颗防御消融
                      </span>
                    </div>
                    <div className="chips">
                      {defList.map((d) => {
                        const bits: string[] = [];
                        if (d.ehpLossPct >= 0.5) bits.push(`EHP -${d.ehpLossPct.toFixed(1)}%`);
                        if (d.physMaxHitLossPct >= 1.5) bits.push(`物一击 -${d.physMaxHitLossPct.toFixed(1)}%`);
                        if (d.maxResDrop >= 5) bits.push(`抗性 -${Math.round(d.maxResDrop)}%`);
                        if (d.deflectDrop >= 2) bits.push(`偏转 -${d.deflectDrop.toFixed(0)}%`);
                        if (d.evadeDrop >= 2) bits.push(`闪避 -${d.evadeDrop.toFixed(0)}%`);
                        if (d.lifeRecoveryDrop >= 10) bits.push(`回血 -${Math.round(d.lifeRecoveryDrop)}/s`);
                        if (bits.length === 0 && d.defenseLayers[0]) bits.push(d.defenseLayers[0]);
                        return (
                          <button
                            key={`def-hl-${d.index}`}
                            type="button"
                            className="badge green"
                            style={{ cursor: "pointer", border: "none" }}
                            title={d.defenseSummary || d.defenseLayers.join(" · ")}
                            onClick={() => setSelectedSkillIdx(d.index)}
                          >
                            {t.term(d.mainGemName, "gems")}：{bits.join(" / ")}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}
                <div className="grid-2">
                  {skillsList.map((sg, idx) => (
                    <React.Fragment key={sg.index}>
                      {renderCompactSkillCard(sg)}
                      {idx === insertAfterIdx && selectedSkill && (
                        <DossierSkillDetail
                          t={t}
                          sg={selectedSkill}
                          detailPanelRef={detailPanelRef}
                          onClose={handleCloseSkillDetail}
                        />
                      )}
                    </React.Fragment>
                  ))}
                </div>
              </>
            );
          })()}

          {dossier && tab === "ascendancy" && (
            <DossierAscendancyTab t={t} ascendancy={dossier.ascendancy} />
          )}

          {dossier && tab === "copy" && <DossierCopyTab dossier={dossier} />}
        </>
      )}
    </section>
  );
};

export const TargetDossierPanel = React.memo(TargetDossierPanelInner);
