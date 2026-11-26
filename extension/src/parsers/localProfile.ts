/**
 * 本地后端离线时的兜底定位提取器（纯函数，输出与后端完全一致的 BuildProfileResponse 契约）。
 * 后端在线时优先调用 POST /builds/profile 以获得完整天赋树节点名称 + 常驻 PoB2 引擎实算。
 */
import type {
  BuildHealthIssue,
  BuildIntentSummary,
  BuildPositioning,
  BuildProfileResponse,
  ContentCapabilityItem,
  ContentGrade,
  ContentReadinessSummary,
  CoreGearHighlight,
  ParsedBuildData,
  ParsedItem,
  WeaponSetRole,
} from "@contracts";
import { analyzeItemDeeply } from "./affixAnalyzer";

const TRAVEL_RE = /Travel|Righteous Descent|Shield Charge|Leap Slam|Blink|Dash|Roll|Whirling Slash/i;

export function scoreModImportance(modLine: string, slot = ""): number {
  const t = (modLine || "").toLowerCase();
  const s = (slot || "").toLowerCase();
  let score = 10;
  if (/to level of all .*skills/.test(t)) score += 95;
  if (/to spirit\b|increased spirit\b/.test(t)) score += 85;
  if (/additional arrow|additional projectile/.test(t)) score += 90;
  if (/gain .* as extra .* damage/.test(t)) score += 85;
  if (/critical (hit |strike )?chance/.test(t)) score += 72;
  if (/critical (spell )?damage bonus/.test(t)) score += 70;
  if (/increased attack speed|increased cast speed|increased skill speed/.test(t)) score += 75;
  if (/adds .* to .* (physical|lightning|cold|fire|chaos) damage/.test(t)) score += 74;
  if (/increased physical damage|increased spell damage|increased elemental damage/.test(t)) score += 72;
  if (/increased movement speed/.test(t)) score += s.includes("boot") ? 98 : 75;
  if (/to maximum mana\b|increased maximum mana\b/.test(t)) score += 68;
  if (/increased mana regeneration rate|mana cost efficiency/.test(t)) score += 60;
  if (/increased effect of socketed (augment items|runes|soul cores|items)|explicit speed modifier magnitudes/.test(t)) score += 76;
  if (/increased projectile speed|increased projectile damage/.test(t)) score += 72;
  if (/deflection rating equal to|deflect/.test(t)) score += 72;
  if (/to maximum life\b/.test(t)) score += 62;
  if (/increased (evasion|energy shield(?!\s+recharge)|armour)/.test(t)) score += /body|helmet|shield|focus/.test(s) ? 68 : 35;
  if (/to maximum energy shield/.test(t)) score += 64;
  if (/to chaos resistance/.test(t)) score += 55;
  if (/to all elemental resistances/.test(t)) score += 58;
  if (/to (fire|cold|lightning) resistance/.test(t)) score += 42;
  if (/increased rarity of items found/.test(t)) score += 45;
  if (/charm slot/.test(t)) score += 65;
  if (/light radius|stun threshold|thorns|life regeneration|reduced attribute requirements|accuracy rating/.test(t)) {
    score -= 30;
  }
  return score;
}

export function classifyWeaponSet(items: ParsedItem[]): WeaponSetRole {
  const text = (items || [])
    .map((it) => [...(it.mods || []), ...(it.socketedRunes || [])].join(" "))
    .join(" ");
  const bases = (items || []).map((it) => (it.baseType || "").toLowerCase()).join(" ");
  const attackSpeed = (text.match(/(\d+)% increased Attack Speed/gi) || [])
    .map((m) => Number((m.match(/\d+/) || [0])[0]))
    .reduce((a, b) => a + b, 0);
  const physPct = Number((text.match(/(\d+)% increased Physical Damage/i) || [0, 0])[1]) || 0;
  let addedAvg = 0;
  for (const m of text.matchAll(/Adds (\d+) to (\d+) [A-Za-z ]*Damage/gi)) {
    addedAvg += (Number(m[1]) + Number(m[2])) / 2;
  }
  const hasCrit = /Critical Hit Chance|Critical Damage Bonus/i.test(text);
  const hasSpell =
    /increased Spell Damage|to Level of all (Spell|Lightning|Fire|Cold|Chaos|Minion) Skills|increased Cast Speed/i.test(
      text
    );
  const hasMartial = /\b(spear|bow|crossbow|quarterstaff|mace|axe|sword|claw|dagger|flail|halberd)\b/i.test(bases);
  const hasSceptre = bases.includes("sceptre");

  if (attackSpeed >= 20 && physPct === 0 && addedAvg < 50 && !hasSpell) {
    return {
      role: "speed",
      label: "攻速/位移工具组",
      detail: `攻速合计 +${attackSpeed}%，专职跑图/叠层`,
      attackSpeed,
    };
  }
  if (hasMartial && (physPct > 0 || addedAvg >= 50 || hasCrit)) {
    return {
      role: "damage",
      label: "主输出攻击组",
      detail: [
        physPct > 0 && `物理 ${physPct}%`,
        addedAvg >= 50 && `点伤均值 ${Math.round(addedAvg)}`,
        hasCrit && "暴击",
        hasSceptre && "副手精魂权杖",
      ]
        .filter(Boolean)
        .join(" + "),
      attackSpeed,
    };
  }
  if (hasSpell && physPct === 0 && addedAvg < 40) {
    return { role: "caster", label: "法术/召唤输出组", detail: "法术/技能等级驱动", attackSpeed };
  }
  if (physPct > 0 || addedAvg >= 50 || hasCrit) {
    return {
      role: "damage",
      label: "主输出武器组",
      detail: [physPct > 0 && `物理 ${physPct}%`, addedAvg >= 50 && `点伤均值 ${Math.round(addedAvg)}`, hasCrit && "暴击"]
        .filter(Boolean)
        .join(" + "),
      attackSpeed,
    };
  }
  return { role: "unknown", label: "常规武器组", detail: "", attackSpeed };
}

function scoreToGrade(score: number): ContentGrade {
  if (score >= 88) return "S";
  if (score >= 74) return "A";
  if (score >= 58) return "B";
  if (score >= 42) return "C";
  return "D";
}

function gradeVerdict(grade: ContentGrade): string {
  switch (grade) {
    case "S":
      return "碾压速通";
    case "A":
      return "稳健推荐";
    case "B":
      return "可打需操作";
    case "C":
      return "吃力易暴毙";
    default:
      return "红线卡住/不推荐";
  }
}

export function evaluateLocalContentReadiness(
  build: ParsedBuildData,
  intent: BuildIntentSummary,
  healthChecks: BuildHealthIssue[]
): ContentReadinessSummary {
  const s = build.character.stats || {};
  let dps = Math.max(s.FullDPS || 0, s.CombinedDPS || 0, s.TotalDPS || 0);
  let ehp = s.TotalEHP || 0;
  let pool = (s.Life || 0) + (s.EnergyShield || 0);
  const physHit = s.PhysicalMaximumHitTaken || 0;
  const minEleRes = Math.min(s.FireResist ?? 75, s.ColdResist ?? 75, s.LightningResist ?? 75);
  const effChaos = s.ChaosResist ?? 0;
  const msMod = s.EffectiveMovementSpeedMod ?? 1.15;
  const hasReal = dps > 0 || ehp > 0 || pool > 0;
  const confidence: ContentReadinessSummary["confidence"] = hasReal ? "xml-stats" : "heuristic";

  if (!hasReal) {
    const lv = build.character.level || 0;
    dps = lv >= 92 ? 180000 : lv >= 80 ? 85000 : 35000;
    ehp = lv >= 92 ? 38000 : lv >= 80 ? 22000 : 12000;
    pool = lv >= 90 ? 3200 : 2000;
  }

  const hasFail = healthChecks.some((c) => !c.suppressedByIntent && c.severity === "fail");
  const pen = hasFail ? 24 : minEleRes < 75 ? 10 : 0;

  const gT15 = scoreToGrade(
    Math.max(
      10,
      (dps >= 250000 ? 42 : dps >= 120000 ? 34 : dps >= 60000 ? 24 : 12) +
        (ehp >= 35000 ? 34 : ehp >= 20000 ? 26 : 14) +
        (intent.hasTravelSkill || msMod >= 1.32 ? 24 : msMod >= 1.18 ? 17 : 10) -
        pen
    )
  );
  const gJuice = scoreToGrade(
    Math.max(
      10,
      (dps >= 450000 ? 40 : dps >= 250000 ? 32 : dps >= 120000 ? 22 : 10) +
        (ehp >= 55000 ? 30 : ehp >= 35000 ? 22 : 12) +
        (effChaos >= 50 ? 18 : effChaos >= 25 ? 12 : effChaos >= 0 ? 5 : 0) +
        (physHit >= 14000 || (physHit === 0 && ehp >= 45000) ? 12 : 6) -
        pen
    )
  );
  const gBoss2 = scoreToGrade(
    Math.max(
      10,
      (dps >= 500000 ? 48 : dps >= 250000 ? 38 : dps >= 120000 ? 26 : 12) +
        (ehp >= 45000 ? 36 : ehp >= 28000 ? 28 : 16) +
        (minEleRes >= 75 && effChaos >= 20 ? 16 : minEleRes >= 75 ? 8 : 0) -
        pen
    )
  );
  const gBoss4 = scoreToGrade(
    Math.max(
      10,
      (dps >= 900000 ? 50 : dps >= 500000 ? 40 : dps >= 280000 ? 28 : dps >= 150000 ? 16 : 6) +
        (ehp >= 70000 ? 32 : ehp >= 45000 ? 24 : 14) +
        (effChaos >= 45 && minEleRes >= 75 ? 18 : minEleRes >= 75 ? 10 : 0) -
        pen * 1.15
    )
  );
  const gTrial = scoreToGrade(
    Math.max(
      10,
      (dps >= 220000 ? 34 : dps >= 110000 ? 26 : 15) +
        (intent.hasTravelSkill || msMod >= 1.28 ? 28 : 16) +
        (pool >= 3500 ? 22 : pool >= 2200 ? 14 : 8) +
        (effChaos >= 35 ? 16 : effChaos >= 0 ? 9 : 0) -
        pen * 0.85
    )
  );

  const items: ContentCapabilityItem[] = [
    {
      id: "t15_mapping",
      label: "T15 常规速刷",
      grade: gT15,
      verdict: gradeVerdict(gT15),
      reason: `DPS ${Math.round(dps / 1000)}k / EHP ${(ehp / 1000).toFixed(1)}k，${intent.hasTravelSkill ? "含位移技能" : `移速×${msMod.toFixed(2)}`}`,
    },
    {
      id: "juiced_t15_delirium",
      label: "T15+ 高危/迷雾/裂隙",
      grade: gJuice,
      verdict: gradeVerdict(gJuice),
      reason: `清图 ${Math.round(dps / 1000)}k DPS，混抗 ${Math.round(effChaos)}%`,
    },
    {
      id: "pinnacle_t0_t2",
      label: "终局首领 (T0~T2)",
      grade: gBoss2,
      verdict: gradeVerdict(gBoss2),
      reason: `单体 ${Math.round(dps / 1000)}k DPS，血盾池 ${Math.round(pool)}`,
    },
    {
      id: "pinnacle_t4_uber",
      label: "终局满难 Boss (T4)",
      grade: gBoss4,
      verdict: gradeVerdict(gBoss4),
      reason: `当前 ${Math.round(dps / 1000)}k DPS / ${(ehp / 1000).toFixed(1)}k EHP`,
    },
    {
      id: "trials_sekhema_chaos",
      label: "圣所/混沌试炼",
      grade: gTrial,
      verdict: gradeVerdict(gTrial),
      reason: `血盾 ${Math.round(pool)}、${intent.hasTravelSkill ? "带位移" : `移速×${msMod.toFixed(2)}`}、混抗 ${Math.round(effChaos)}%`,
    },
  ];

  let overallGrade: ContentGrade = "C";
  let overallTier = "异界开荒 / 成型过渡档";
  if ((gBoss4 === "S" || gBoss4 === "A") && (gJuice === "S" || gJuice === "A")) {
    overallGrade = gBoss4 === "S" ? "S" : "A";
    overallTier = "终局 T4 全通毕业档";
  } else if ((gJuice === "S" || gJuice === "A") && (gBoss2 === "S" || gBoss2 === "A")) {
    overallGrade = "A";
    overallTier = "T15+ 高危速刷 & T2 首领稳通档";
  } else if (gT15 === "S" || gT15 === "A") {
    overallGrade = "B";
    overallTier = "常规 T15 稳刷 / 中期攻坚档";
  } else if (gT15 === "D") {
    overallGrade = "D";
    overallTier = "红线未达标 / 需先修补面板";
  }

  return {
    overallTier,
    overallGrade,
    confidence,
    summary:
      confidence === "heuristic"
        ? "当前基于角色等级与装备结构估算；连接本地后端并勾选「同步 PoB2 引擎实算」可得精确内容分级。"
        : `主力胜任：${items.filter((x) => x.grade === "S" || x.grade === "A").map((x) => x.label).join("、") || "常规 T15"}`,
    items,
  };
}

export function buildLocalProfile(build: ParsedBuildData): BuildProfileResponse {
  const stats = build.character.stats || {};
  const mainGroup =
    build.skillGroups.find((g) => g.isMain) ||
    [...build.skillGroups].sort((a, b) => b.gems.length - a.gems.length)[0];
  const mainGem = mainGroup?.gems.find((g) => !/^Support/i.test(g.name)) || mainGroup?.gems[0];
  const mainSkill = mainGem?.name || "核心技能";

  const weaponSetRoles: BuildIntentSummary["weaponSetRoles"] = {};
  if (build.weaponSets.set1.length) weaponSetRoles.set1 = classifyWeaponSet(build.weaponSets.set1);
  if (build.weaponSets.set2.length) weaponSetRoles.set2 = classifyWeaponSet(build.weaponSets.set2);

  const hasTravelSkill = build.skillGroups.some((g) => g.gems.some((gem) => TRAVEL_RE.test(gem.name)));
  const asc = build.character.ascendancy !== "None" ? build.character.ascendancy : build.character.className;
  const archetype = [asc, mainSkill].filter(Boolean).join(" / ");

  const intent: BuildIntentSummary = {
    mainSkill,
    archetype,
    hasDualWeaponSet: build.weaponSets.hasDualWeaponSet,
    weaponSetRoles,
    keyScalingVectors: [],
    intentionalExceptions: [],
    hasTravelSkill,
  };

  if (stats.Speed > 0) {
    intent.keyScalingVectors.push({ key: "speed", label: `频率 ${stats.Speed.toFixed(2)}/s`, note: "输出与手感" });
  }
  if (stats.CritChance >= 35) {
    const mult = stats.CritMultiplier > 20 ? stats.CritMultiplier / 100 : stats.CritMultiplier;
    intent.keyScalingVectors.push({ key: "crit", label: `暴击 ${Math.round(stats.CritChance)}% × ${mult.toFixed(1)}x`, note: "核心乘区" });
  }
  if (build.weaponSets.hasDualWeaponSet) {
    intent.keyScalingVectors.push({ key: "dualWeaponSet", label: "双武器组分工", note: "按组配装" });
  }

  const allGear = [...build.equippedItems, ...build.jewels];
  const coreUniques = Array.from(
    new Set(allGear.filter((i) => i.rarity.toUpperCase() === "UNIQUE").map((i) => i.name).filter(Boolean))
  ).slice(0, 8);

  const coreGearHighlights: CoreGearHighlight[] = allGear
    .map((it) => {
      const deep = analyzeItemDeeply(it, build);
      const deepScored = deep.affixes
        .filter((a) => !a.excludeFromTradeLock && a.role !== "dead")
        .sort((a, b) => b.contextualScore - a.contextualScore);
      const scored =
        deepScored.length > 0
          ? deepScored.map((a) => ({ m: a.cleaned, s: a.contextualScore }))
          : [...it.mods].map((m) => ({ m, s: scoreModImportance(m, it.slot) })).sort((a, b) => b.s - a.s);
      const isUnique = it.rarity.toUpperCase() === "UNIQUE";
      return {
        slot: it.slot,
        name: it.name || it.baseType,
        baseType: it.baseType,
        rarity: it.rarity,
        roleTag: isUnique ? "核心暗金" : it.slot.toLowerCase().includes("weapon") ? "核心武器" : "关键黄装",
        keyMods: scored.slice(0, 4).map((x) => x.m),
        importance: (scored[0]?.s || 10) + (isUnique ? 35 : 0) + (it.slot.toLowerCase().includes("weapon") ? 25 : 0),
        socketCount: it.socketCount,
        corrupted: it.corrupted,
      };
    })
    .sort((a, b) => b.importance - a.importance);

  const affixBucket: Array<[string, RegExp]> = [
    ["+技能等级", /to level of all .*skills/i],
    ["精魂 (Spirit)", /to spirit\b|increased spirit\b/i],
    ["额外投射物", /additional (arrow|projectile)/i],
    ["移动速度", /increased movement speed/i],
    ["攻速 / 施速", /increased (attack|cast|skill) speed/i],
    ["武器/法术高点伤", /adds \d+ to \d+ .*damage/i],
    ["暴击率 / 暴击伤害", /critical (hit |strike )?chance|critical (spell )?damage bonus/i],
    ["最大魔力 (Mana)", /to maximum mana\b|increased maximum mana\b/i],
    ["魔力回复 / 魔耗效率", /increased mana regeneration rate|mana cost efficiency/i],
    ["高护盾 / 生命 / 闪避", /maximum (life|energy shield)|increased evasion/i],
    ["混沌抗 / 全抗", /chaos resistance|all elemental resistances/i],
  ];
  const affixCounts = new Map<string, number>();
  for (const it of allGear) {
    for (const mod of it.mods) {
      for (const [label, re] of affixBucket) {
        if (re.test(mod)) {
          affixCounts.set(label, (affixCounts.get(label) || 0) + 1);
          break;
        }
      }
    }
  }
  const coreAffixes = Array.from(affixCounts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map(([label]) => label);

  const healthChecks: BuildHealthIssue[] = [];
  for (const [k, label] of [["FireResist", "火焰"], ["ColdResist", "冰霜"], ["LightningResist", "闪电"]] as const) {
    if (stats[k] !== undefined && stats[k] < 75) {
      healthChecks.push({
        ruleId: "res.element",
        category: "defense",
        severity: stats[k] < 60 ? "fail" : "warn",
        title: `${label}抗性 ${Math.round(stats[k])}%`,
        detail: `差 ${Math.round(75 - stats[k])} 点满抗`,
        targetId: k,
      });
    }
  }
  if (stats.SpiritUnreserved !== undefined && stats.SpiritUnreserved < 0) {
    healthChecks.push({
      ruleId: "spirit.overflow",
      category: "resource",
      severity: "fail",
      title: `精魂超额 ${Math.abs(Math.round(stats.SpiritUnreserved))}`,
      detail: "有光环或召唤物实际无法开启",
      targetId: "Spirit",
    });
  }

  const coreKeystones = build.passiveTree.keystones.map((n) => n.name).filter(Boolean);
  const coreAscendancy = build.passiveTree.ascendancyNodes.map((n) => n.name).filter(Boolean);
  const hasHydratedTree = coreKeystones.length > 0 || coreAscendancy.length > 0 || build.passiveTree.notables.length > 0;

  const pitfalls = healthChecks
    .filter((c) => c.severity !== "info")
    .map((c) => `【${c.title}】${c.detail}`);
  if (!hasHydratedTree) {
    pitfalls.push("当前未加载版本词典：请在右上角「⚙️ 设置」点击热更新词典，或启动本地工作台后端。");
  } else {
    pitfalls.push("当前为纯插件独立模式（已加载离线版本词典）；点击「✨ AI 流式本质拆解」可深度分析联动与盲区，连接桌面端可解锁 PoB2 引擎精确消融。");
  }

  // 评级不再使用静态正则/阈值规则，统一留空等待 AI 结合 PoB2 引擎实算数据（位移速度、输出、防御等）进行评级
  const positioning: BuildPositioning = {
    archetype,
    oneLineIdea: `以「${mainSkill}」为主轴${build.weaponSets.hasDualWeaponSet ? "（双武器组分工）" : ""}${coreUniques.length ? `，配合核心暗金「${coreUniques.slice(0, 2).join(" / ")}」` : ""}${coreKeystones.length ? `，基石「${coreKeystones.slice(0, 2).join(" / ")}」` : ""}。`,
    scenarioTags: [asc, build.weaponSets.hasDualWeaponSet ? "双武器组分工" : "单武器体系", coreUniques.length >= 3 ? "多暗金驱动" : "黄装为主"].filter(Boolean),
    budgetTier: coreUniques.length >= 4 ? "high" : coreUniques.length >= 2 ? "mid" : "league-start",
    coreUniques,
    coreAffixes,
    coreKeystones,
    coreAscendancy,
    pitfalls,
    contentReadiness: undefined,
    analyzedBy: "fallback",
  };

  return {
    source: "local-fallback",
    build,
    positioning,
    intent,
    coreGearHighlights,
    healthChecks,
    simulation: null,
  };
}
