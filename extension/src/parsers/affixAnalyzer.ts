/**
 * 深度装备词条分析器（联动天赋树 / 升华 / 技能 / 双武器组 / 符文插槽与基底属性）。
 *
 * 为平替 (budget)、半成品 (semi)、白底 (base) 与原件 (exact) 提供基于当前 BD 语境的：
 *  1. 天赋联动与死词缀判定（CI/EB/MoM/RT/血魔法/火之化身/Subterfuge Mask/Spectral Ward/
 *     Beastial Skin/Lead me through Grace/Crimson Power/Feathered Fletching/Falcon Dive/
 *     Hollow Palm/属性堆叠等，支持按官方天赋 stats 原文动态识别）；
 *  2. 符文插槽与孔位上限判定（工匠石自然打孔上限 vs 卓越/瓦尔超孔、镶嵌物效果增幅联动、
 *     符文解锁词池如 Marksman/Destruction、Bonded 绑定词条生效判定）；
 *  3. 半成品锁词难度与前后缀归属分析（优先锁破裂/最难出核心词条，跳过涂油/死词缀/低 T 杂词）。
 */
import type {
  ParsedBuildData,
  ParsedItem,
  ParsedModDetail,
  ParsedModOrigin,
  TreeNodeInfo,
} from "@contracts";

export type AffixSide = "prefix" | "suffix" | "implicit" | "enchant" | "unknown";
export type AffixDifficulty = "hard" | "medium" | "easy" | "unrollable";
export type AffixRole = "core" | "synergy" | "defense" | "utility" | "flexible" | "dead";

export interface SlotPassiveSynergy {
  slotKey: "helmet" | "body" | "gloves" | "boots" | "shield" | "focus" | "ring" | "amulet" | "quiver" | "weapon" | "armour";
  statKind: "es" | "ev" | "ar" | "all_def" | "all_mods" | "spirit";
  nodeName: string;
  label: string;
  boost: number;
  penalize?: boolean;
}

export interface BuildSynergyProfile {
  lifeDead: boolean;
  chaosResDead: boolean;
  manaDead: boolean;
  critDead: boolean;
  nonFireDead: boolean;
  gearSpiritDead: boolean;
  esCore: boolean;
  esToMana: boolean;
  manaCore: boolean;
  evasionToArmour: boolean;
  bondedRunesActive: boolean;
  projSpeedIsDamage: boolean;
  accuracyIsDamage: boolean;
  deflectionScaling: boolean;
  elementalAilmentScaling: boolean;
  needsWardBase: boolean;
  hasTravelAttack: boolean;
  strScaling: boolean;
  dexScaling: boolean;
  intScaling: boolean;
  giantBlood: boolean;
  hollowPalm: boolean;
  isSpellBuild: boolean;
  isAttackBuild: boolean;
  isProjectileBuild: boolean;
  isMinionBuild: boolean;
  isCritBuild: boolean;
  speedWeaponSlots: Set<string>;
  slotSynergies: SlotPassiveSynergy[];
  activeNodeNames: Set<string>;
}

export interface ItemSocketAnalysis {
  socketCount: number;
  naturalMaxSockets: number;
  exceedsCraftableSockets: boolean;
  hasSocketEffectMod: boolean;
  bondedActive: boolean;
  runeNames: string[];
  runeEffects: string[];
  bondedEffects: string[];
  runeUnlockedPools: string[];
  requireSocketsInBudget: boolean;
  requireSocketsInSemi: boolean;
  advice: string;
}

export interface AnalyzedAffix {
  index: number;
  raw: string;
  cleaned: string;
  origin: ParsedModOrigin;
  isAnoint: boolean;
  side: AffixSide;
  difficulty: AffixDifficulty;
  role: AffixRole;
  contextualScore: number;
  synergyTags: string[];
  excludeFromTradeLock: boolean;
  reason: string;
}

export interface DefenseFloorHint {
  stat: "es" | "ev" | "ar" | "ward";
  rawValue: number;
  budgetMin: number;
  exactMin: number;
  reason: string;
}

export interface ItemDeepAnalysis {
  slot: string;
  isSpeedWeapon: boolean;
  requireBaseType: boolean;
  baseTypeReason: string;
  defenseFloor: DefenseFloorHint | null;
  passiveSynergies: string[];
  socketAnalysis: ItemSocketAnalysis;
  affixes: AnalyzedAffix[];
  budgetSummary: string;
  semiSummary: string;
}

const TWO_HAND_BASE_RE =
  /\b(bow|crossbow|quarterstaff|warstaff|staff|greataxe|greatsword|greatmace|two hand|talisman)\b/i;
const ONE_HAND_BASE_RE =
  /\b(spear|wand|sceptre|dagger|claw|flail|sword|axe|mace|club|rapier)\b/i;
const SOCKET_EFFECT_RE =
  /increased\s+effect\s+of\s+socketed\s+(augment\s+items|runes|soul\s+cores|items)/i;
const RUNE_POOL_RE = /^can\s+roll\s+(.+?)\s+modifiers?$/i;

function parseFirstNum(val: string | undefined): number {
  if (!val) return 0;
  const m = String(val).match(/(\d+(?:\.\d+)?)/);
  return m ? Number(m[1]) : 0;
}

/**
 * PoE2 工匠石（Artificer's Orb）可打出的自然孔位上限：
 * - 胸甲 / 双手武器：2 孔（3 孔需卓越底子或瓦尔腐化）
 * - 头/手/鞋/盾/法器/单手武器：1 孔（2 孔需卓越底子或瓦尔腐化）
 * - 戒指/项链/腰带/箭袋/珠宝：0 孔
 */
export function getNaturalMaxSockets(slot: string, baseType = ""): number {
  const s = (slot || "").toLowerCase();
  const b = (baseType || "").toLowerCase();
  if (/ring|amulet|belt|quiver|jewel|flask|charm/.test(s)) return 0;
  if (s.includes("body") || s.includes("chest")) return 2;
  if (s.includes("helmet") || s.includes("gloves") || s.includes("boots") || s.includes("shield") || s.includes("focus")) {
    return 1;
  }
  if (TWO_HAND_BASE_RE.test(b) && !ONE_HAND_BASE_RE.test(b.replace(/quarterstaff|warstaff/gi, ""))) {
    return 2;
  }
  if (s.includes("weapon")) {
    if (TWO_HAND_BASE_RE.test(b)) return 2;
    return 1;
  }
  return 0;
}

function slotToKey(slot: string): SlotPassiveSynergy["slotKey"] | null {
  const s = (slot || "").toLowerCase();
  if (s.includes("helmet")) return "helmet";
  if (s.includes("body")) return "body";
  if (s.includes("gloves")) return "gloves";
  if (s.includes("boots")) return "boots";
  if (s.includes("shield") || s.includes("buckler")) return "shield";
  if (s.includes("focus")) return "focus";
  if (s.includes("ring")) return "ring";
  if (s.includes("amulet")) return "amulet";
  if (s.includes("quiver")) return "quiver";
  if (s.includes("weapon")) return "weapon";
  return null;
}

function isArmourSlot(slot: string): boolean {
  const k = slotToKey(slot);
  return k === "helmet" || k === "body" || k === "gloves" || k === "boots" || k === "shield" || k === "focus";
}

function isSpeedUtilityWeapon(item: ParsedItem): boolean {
  const s = (item.slot || "").toLowerCase();
  if (!s.includes("weapon")) return false;
  const text = [...(item.mods || []), ...(item.socketedRunes || [])].join(" ");
  const speedMatches = text.match(/(\d+)%\s+increased\s+Attack\s+Speed/gi) || [];
  const totalSpeed = speedMatches.reduce((sum, m) => sum + parseFirstNum(m), 0);
  const hasSpeedMag = /explicit\s+speed\s+modifier\s+magnitudes/i.test(text);
  const physPct = parseFirstNum((text.match(/(\d+)%\s+increased\s+Physical\s+Damage/i) || [])[1]);
  let addedAvg = 0;
  for (const m of text.matchAll(/Adds\s+(\d+)\s+to\s+(\d+)\s+[A-Za-z ]*Damage/gi)) {
    addedAvg += (Number(m[1]) + Number(m[2])) / 2;
  }
  const hasSpell =
    /increased\s+Spell\s+Damage|to\s+Level\s+of\s+all\s+(Spell|Lightning|Fire|Cold|Chaos|Minion)\s+Skills|increased\s+Cast\s+Speed/i.test(
      text
    );
  return (totalSpeed >= 20 || hasSpeedMag) && physPct === 0 && addedAvg < 50 && !hasSpell;
}

/**
 * 从 ParsedBuildData 提取完整的天赋/升华/技能/武器组联动画像。
 * 同时读取节点名和官方 stats 原文，确保版本变动后依然稳健。
 */
export function extractBuildSynergyProfile(build?: ParsedBuildData | null): BuildSynergyProfile {
  const profile: BuildSynergyProfile = {
    lifeDead: false,
    chaosResDead: false,
    manaDead: false,
    critDead: false,
    nonFireDead: false,
    gearSpiritDead: false,
    esCore: false,
    esToMana: false,
    manaCore: false,
    evasionToArmour: false,
    bondedRunesActive: false,
    projSpeedIsDamage: false,
    accuracyIsDamage: false,
    deflectionScaling: false,
    elementalAilmentScaling: false,
    needsWardBase: false,
    hasTravelAttack: false,
    strScaling: false,
    dexScaling: false,
    intScaling: false,
    giantBlood: false,
    hollowPalm: false,
    isSpellBuild: false,
    isAttackBuild: false,
    isProjectileBuild: false,
    isMinionBuild: false,
    isCritBuild: false,
    speedWeaponSlots: new Set<string>(),
    slotSynergies: [],
    activeNodeNames: new Set<string>(),
  };

  if (!build) return profile;

  const tree = build.passiveTree;
  const allNodes: Array<{ name: string; stats: string[] }> = [
    ...(tree?.keystones || []),
    ...(tree?.ascendancyNodes || []),
    ...(tree?.notables || []),
    ...(tree?.anointedNotables || []).map((a) => ({ name: a.name, stats: a.stats || [] })),
  ];

  const seenSynergy = new Set<string>();
  const addSlotSyn = (syn: SlotPassiveSynergy) => {
    const k = `${syn.slotKey}:${syn.statKind}:${syn.nodeName}`;
    if (seenSynergy.has(k)) return;
    seenSynergy.add(k);
    profile.slotSynergies.push(syn);
  };

  for (const node of allNodes) {
    const name = (node.name || "").trim();
    const nl = name.toLowerCase();
    const st = (node.stats || []).join(" | ").toLowerCase();
    if (nl) profile.activeNodeNames.add(nl);

    // 基石 & 死词缀
    if (nl === "chaos inoculation" || st.includes("maximum life becomes 1")) {
      profile.lifeDead = true;
      profile.chaosResDead = true;
      profile.esCore = true;
    }
    if (nl === "blood magic" || st.includes("you have no mana")) {
      profile.manaDead = true;
    }
    if (nl === "resolute technique" || st.includes("never deal critical")) {
      profile.critDead = true;
    }
    if (nl === "avatar of fire" || st.includes("deal no non-fire damage")) {
      profile.nonFireDead = true;
    }
    if (nl === "eldritch battery" || st.includes("converts all energy shield to mana")) {
      profile.esToMana = true;
      profile.manaCore = true;
    }
    if (nl === "mind over matter" || st.includes("taken from mana before life")) {
      profile.manaCore = true;
    }
    if (nl === "iron reflexes" || st.includes("converts all evasion rating to armour")) {
      profile.evasionToArmour = true;
    }
    if (nl === "giant's blood" || st.includes("attribute requirements of")) {
      profile.giantBlood = true;
      profile.strScaling = true;
    }
    if (nl === "hollow palm technique" || st.includes("per 75 item evasion on equipped armour items")) {
      profile.hollowPalm = true;
      addSlotSyn({ slotKey: "armour", statKind: "ev", nodeName: name || "Hollow Palm", label: "空手道(防具闪避→攻速)", boost: 36 });
      addSlotSyn({ slotKey: "armour", statKind: "es", nodeName: name || "Hollow Palm", label: "空手道(防具ES→基础暴击)", boost: 36 });
    }

    // 精魂与符文绑定升华
    if (st.includes("cannot gain spirit from equipment") || st.includes("you have no spirit") || nl === "embrace the darkness") {
      profile.gearSpiritDead = true;
    }
    if (nl === "lead me through grace..." || (st.includes("spirit for every") && st.includes("equipped body armour"))) {
      profile.gearSpiritDead = true;
      addSlotSyn({ slotKey: "body", statKind: "es", nodeName: name || "Lead me through Grace...", label: "Invoker(胸甲ES→精魂)", boost: 44 });
      addSlotSyn({ slotKey: "body", statKind: "ev", nodeName: name || "Lead me through Grace...", label: "Invoker(胸甲闪避→精魂)", boost: 40 });
    }
    if (nl === "wisdom of the maji" || st.includes("bonded modifiers on runes")) {
      profile.bondedRunesActive = true;
    }

    // 投射物速度 / 命中 / 偏转 / 异常联动
    if (nl === "feathered fletching" || st.includes("projectile speed also apply to damage")) {
      profile.projSpeedIsDamage = true;
    }
    if (
      nl === "falcon dive" ||
      nl === "penetrate" ||
      (nl === "critical strike" && st.includes("accuracy")) ||
      st.includes("attack speed per 400 accuracy") ||
      st.includes("excess chance to hit") ||
      st.includes("weapon's accuracy rating")
    ) {
      profile.accuracyIsDamage = true;
    }
    if (st.includes("deflection rating equal to") || nl === "enhanced reflexes") {
      profile.deflectionScaling = true;
    }
    if (nl === "harness the elements" || st.includes("for each type of elemental ailment")) {
      profile.elementalAilmentScaling = true;
    }

    // 属性堆叠 / 魔力增伤
    if (nl === "falcon technique" || st.includes("per 25 dexterity")) profile.dexScaling = true;
    if (nl === "the winter owl" || nl === "pure power" || st.includes("per 10 intelligence")) profile.intScaling = true;
    if (nl === "powerful casting" || nl === "brute strength" || st.includes("per 10 strength") || st.includes("per 15 strength")) {
      profile.strScaling = true;
    }
    if (nl === "arcane intensity" || nl === "blackened heart" || st.includes("per 100 maximum mana")) {
      profile.manaCore = true;
    }

    // 部位防御转换与倍增（直接读官方 stats 原文 + 节点名双保险）
    if (nl === "subterfuge mask" || (st.includes("evasion rating per") && st.includes("energy shield on equipped helmet"))) {
      addSlotSyn({ slotKey: "helmet", statKind: "es", nodeName: name || "Subterfuge Mask", label: "头盔ES→闪避(Subterfuge Mask)", boost: 45 });
    }
    if (nl === "illuminated crown" || (st.includes("increased energy shield from equipped helmet"))) {
      addSlotSyn({ slotKey: "helmet", statKind: "es", nodeName: name || "Illuminated Crown", label: "头盔ES放大(Illuminated Crown)", boost: 34 });
    }
    if (nl === "ruinic helm" || (st.includes("energy shield per") && st.includes("armour on equipped helmet"))) {
      addSlotSyn({ slotKey: "helmet", statKind: "ar", nodeName: name || "Ruinic Helm", label: "头盔护甲→ES(Ruinic Helm)", boost: 42 });
    }
    if (nl === "spectral ward" || (st.includes("energy shield per") && st.includes("evasion on equipped body armour"))) {
      addSlotSyn({ slotKey: "body", statKind: "ev", nodeName: name || "Spectral Ward", label: "胸甲闪避→ES(Spectral Ward)", boost: 44 });
    }
    if (nl === "beastial skin" || st.includes("increased evasion rating from equipped body armour")) {
      addSlotSyn({ slotKey: "body", statKind: "ev", nodeName: name || "Beastial Skin", label: "胸甲闪避翻倍(Beastial Skin)", boost: 40 });
    }
    if (nl === "crimson power" || (st.includes("maximum life equal to") && st.includes("energy shield on equipped body armour"))) {
      addSlotSyn({ slotKey: "body", statKind: "es", nodeName: name || "Crimson Power", label: "胸甲ES→生命(Crimson Power)", boost: 44 });
    }
    if (nl === "stone skin" || nl === "sturdy metal" || st.includes("armour from equipped body armour")) {
      addSlotSyn({ slotKey: "body", statKind: "ar", nodeName: name || "Stone Skin", label: `胸甲护甲放大(${name})`, boost: 36 });
    }
    if (nl === "ancient aegis") {
      addSlotSyn({ slotKey: "body", statKind: "es", nodeName: name, label: "胸甲护盾放大(Ancient Aegis)", boost: 32 });
      addSlotSyn({ slotKey: "body", statKind: "ar", nodeName: name, label: "胸甲护甲放大(Ancient Aegis)", boost: 32 });
    }
    if (nl === "leather bound gauntlets" || (st.includes("evasion rating per") && st.includes("armour on equipped gloves"))) {
      addSlotSyn({ slotKey: "gloves", statKind: "ar", nodeName: name || "Leather Bound Gauntlets", label: "手套护甲→闪避", boost: 38 });
    }
    if (nl === "iron slippers" || (st.includes("armour per") && st.includes("energy shield on equipped boots"))) {
      addSlotSyn({ slotKey: "boots", statKind: "es", nodeName: name || "Iron Slippers", label: "鞋子ES→护甲(Iron Slippers)", boost: 38 });
    }
    if (nl === "stalking panther" || st.includes("evasion rating from equipped helmet, gloves and boots is doubled")) {
      addSlotSyn({ slotKey: "helmet", statKind: "ev", nodeName: name || "Stalking Panther", label: "头/手/鞋闪避翻倍(Stalking Panther)", boost: 42 });
      addSlotSyn({ slotKey: "gloves", statKind: "ev", nodeName: name || "Stalking Panther", label: "头/手/鞋闪避翻倍(Stalking Panther)", boost: 42 });
      addSlotSyn({ slotKey: "boots", statKind: "ev", nodeName: name || "Stalking Panther", label: "头/手/鞋闪避翻倍(Stalking Panther)", boost: 42 });
      addSlotSyn({ slotKey: "body", statKind: "ev", nodeName: name || "Stalking Panther", label: "胸甲闪避减半(Stalking Panther)", boost: -25, penalize: true });
    }
    if (nl === "dreamcatcher" || nl === "warding fetish" || st.includes("energy shield from equipped focus")) {
      addSlotSyn({ slotKey: "focus", statKind: "es", nodeName: name || "Dreamcatcher", label: `法器ES放大(${name})`, boost: 36 });
    }
    if (nl === "greatest defence" || nl === "spiked shield" || (st.includes("attack damage per") && st.includes("equipped shield"))) {
      addSlotSyn({ slotKey: "shield", statKind: "all_def", nodeName: name, label: `盾牌防值→攻击增伤(${name})`, boost: 42 });
    }
    if (nl === "fortified aegis" || nl === "towering shield") {
      addSlotSyn({ slotKey: "shield", statKind: "all_def", nodeName: name, label: `盾牌防值放大(${name})`, boost: 30 });
    }
    if (nl === "mystic attunement" || st.includes("bonuses gained from equipped rings and amulets")) {
      addSlotSyn({ slotKey: "ring", statKind: "all_mods", nodeName: name || "Mystic Attunement", label: "戒指词条+25%(Mystic Attunement)", boost: 12 });
      addSlotSyn({ slotKey: "amulet", statKind: "all_mods", nodeName: name || "Mystic Attunement", label: "项链词条+25%(Mystic Attunement)", boost: 12 });
    }
    if (nl === "wrapped quiver" || nl === "master fletching" || st.includes("bonuses gained from equipped quiver")) {
      addSlotSyn({ slotKey: "quiver", statKind: "all_mods", nodeName: name, label: `箭袋词条放大(${name})`, boost: 16 });
    }
  }

  // 技能组 & 辅助宝石联动
  const allGemNames: string[] = [];
  for (const sg of build.skillGroups || []) {
    for (const g of sg.gems || []) {
      const gn = (g.name || "").trim();
      if (gn) allGemNames.push(gn.toLowerCase());
    }
  }
  const gemBlob = allGemNames.join(" | ");
  if (gemBlob.includes("projectile acceleration")) profile.projSpeedIsDamage = true;
  if (gemBlob.includes("olroth's conviction")) profile.needsWardBase = true;
  if (gemBlob.includes("archmage")) profile.manaCore = true;
  if (/whirling slash|shield charge|leap slam|tempest flurry/.test(gemBlob)) {
    profile.hasTravelAttack = true;
  }
  if (/skeletal |raging spirits|raise zombie|spectre|minion/.test(gemBlob)) {
    profile.isMinionBuild = true;
  }

  // 暗金装备联动检测（如 HOWA / Pillar / Yoke / The Taming / Astramentis / Ward）
  for (const it of [...(build.equippedItems || []), ...(build.jewels || [])]) {
    const nm = (it.name || "").toLowerCase();
    if (nm.includes("hand of wisdom and action")) {
      profile.intScaling = true;
      profile.dexScaling = true;
    }
    if (nm.includes("pillar of the caged god")) {
      profile.strScaling = true;
      profile.dexScaling = true;
      profile.intScaling = true;
    }
    if (nm.includes("astramentis")) {
      profile.strScaling = true;
      profile.dexScaling = true;
      profile.intScaling = true;
    }
    if (nm.includes("yoke of suffering") || nm.includes("the taming")) {
      profile.elementalAilmentScaling = true;
    }
    if (parseFirstNum(it.properties?.Ward) >= 50) {
      profile.needsWardBase = true;
    }
    if (isSpeedUtilityWeapon(it)) {
      profile.speedWeaponSlots.add(it.slot.toLowerCase());
    }
  }

  // 判定攻击 / 法术 / 投射物 / 暴击体系
  const stats = build.character?.stats || {};
  if (!profile.critDead && ((stats.CritChance || 0) >= 28 || allNodes.some((n) => /critical (hit|damage)/i.test((n.stats || []).join(" "))))) {
    profile.isCritBuild = true;
  }
  if ((stats.EnergyShield || 0) >= 1200) profile.esCore = true;
  if ((stats.Mana || 0) >= 2800) profile.manaCore = true;

  const gearText = (build.equippedItems || []).map((it) => `${it.baseType} ${(it.mods || []).join(" ")}`).join(" ").toLowerCase();
  const spellSignal =
    /increased spell damage|to level of all spell skills|cast speed/.test(gearText) ||
    /spark|arc|fireball|comet|frostbolt|hexblast|essence drain|contagion|bonestorm|eye of winter/.test(gemBlob);
  const attackSignal =
    /\b(spear|bow|crossbow|quarterstaff|mace|axe|sword|claw|dagger|flail)\b/.test(gearText) ||
    /twister|lightning arrow|lightning spear|whirling slash|ice strike|tempest flurry|explosive shot|galvanic/.test(gemBlob);
  if (spellSignal && !attackSignal) profile.isSpellBuild = true;
  else if (attackSignal && !spellSignal) profile.isAttackBuild = true;
  else if (spellSignal && attackSignal) {
    profile.isSpellBuild = true;
    profile.isAttackBuild = true;
  }
  if (/projectile|arrow|spear|twister|bow|crossbow|quiver|spark|frostbolt/.test(`${gemBlob} ${gearText}`)) {
    profile.isProjectileBuild = true;
  }

  return profile;
}

/** 从原始文本或 modDetails 还原词条来源（implicit/enchant/fractured/desecrated/explicit）。 */
export function resolveModOrigins(item: ParsedItem): Array<{ text: string; origin: ParsedModOrigin; isAnoint: boolean }> {
  if (item.modDetails && item.modDetails.length > 0) {
    return item.modDetails
      .filter((d) => d.origin !== "rune")
      .map((d) => ({
        text: d.text,
        origin: d.origin,
        isAnoint: Boolean(d.isAnoint || /^allocates\s+/i.test(d.text)),
      }));
  }

  // 回退：若 mock item 没带 modDetails，直接从 item.mods 推断
  return (item.mods || [])
    .filter((m) => !/\(rune\)|\{rune\}/i.test(m))
    .map((raw) => {
      const low = raw.toLowerCase();
      const cleaned = raw
        .replace(/\{[^}]*\}/g, "")
        .replace(/\s*\((implicit|crafted|fractured|rune|desecrated|mutated)\)\s*$/gi, "")
        .trim();
      const isAnoint = /^allocates\s+/i.test(cleaned);
      let origin: ParsedModOrigin = "explicit";
      if (low.includes("(fractured)") || low.includes("{fractured}")) origin = "fractured";
      else if (low.includes("(desecrated)") || low.includes("{desecrated}")) origin = "desecrated";
      else if (low.includes("(crafted)") || low.includes("{crafted}")) origin = "crafted";
      else if (low.includes("(implicit)") || low.includes("{implicit}") || /^grants\s+(skill:|1 additional skill slot)/i.test(cleaned)) {
        origin = "implicit";
      } else if (isAnoint || low.includes("{enchant}")) origin = "enchant";
      return { text: cleaned, origin, isAnoint };
    });
}

function modSkeleton(text: string): string {
  return (text || "")
    .replace(/[+-]?\d+(?:\.\d+)?/g, "#")
    .replace(/\+#/g, "#")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
}

function isLocalDefenseStatOf(modText: string, stat: "es" | "ev" | "ar" | "ward"): boolean {
  const t = (modText || "").toLowerCase();
  if (/recharge|regeneration|delay|deflect/.test(t)) return false;
  if (stat === "es") return /maximum energy shield|increased (?:armour and |evasion and )?energy shield\b/.test(t);
  if (stat === "ev") return /to evasion rating|increased (?:armour and )?evasion/.test(t);
  if (stat === "ar") return /to armour|increased armour/.test(t);
  if (stat === "ward") return /\bward\b/.test(t);
  return false;
}

export function inferAffixSide(modText: string, origin: ParsedModOrigin): AffixSide {
  if (origin === "implicit") return "implicit";
  if (origin === "enchant") return "enchant";
  const t = (modText || "").toLowerCase();
  // 常见后缀：抗性、属性、攻速/施速、暴击率/暴伤、命中、稀有度、魔力回复、护盾充能、偏转、镶嵌物增幅、投射物速度、技能使用速度
  if (
    /resistance|strength|dexterity|intelligence|all attributes|attack speed|cast speed|skill speed|use speed|critical (hit |strike )?chance|critical .*damage bonus|accuracy rating|mana regeneration|life regeneration|energy shield recharge|stun threshold|light radius|deflection rating|effect of socketed|explicit speed modifier|reduced attribute|projectile speed|mana on kill|life on kill|leech/.test(
      t
    )
  ) {
    return "suffix";
  }
  if (/to level of all .*skills/.test(t)) return "suffix";
  // 常见前缀：生命/魔力/ES/闪避/护甲、点伤、物理%/法伤%/元素伤%、移动速度、精魂、额外元素伤、额外投射物
  if (
    /maximum life|maximum mana|maximum energy shield|evasion rating|to armour|increased armour|increased energy shield(?!\s+recharge)|increased evasion|adds \d+ to \d+|increased physical damage|increased spell damage|increased elemental damage|increased projectile damage|increased movement speed|to spirit\b|increased spirit|gain .* as extra|additional arrow|additional projectile|increased rarity of items found/.test(
      t
    )
  ) {
    return "prefix";
  }
  return "unknown";
}

export function inferCraftDifficulty(modText: string, origin: ParsedModOrigin, isAnoint: boolean): AffixDifficulty {
  const t = (modText || "").toLowerCase();
  if (isAnoint || origin === "implicit" || origin === "enchant" || /^grants skill:/i.test(t)) {
    return "unrollable";
  }
  if (origin === "fractured") return "hard";
  if (
    /to level of all .*skills|additional (arrow|projectile)|to spirit\b|increased spirit\b|increased movement speed|gain .* as extra|effect of socketed|explicit .* modifier magnitudes|deflection rating equal to|increased critical (hit |strike )?chance|critical .*damage bonus|\d{2,3}% increased physical damage|increased spell damage|increased attack speed|increased cast speed|increased projectile speed/.test(
      t
    )
  ) {
    return "hard";
  }
  if (
    /to chaos resistance|to all elemental resistances|adds \d+ to \d+ .* damage|increased (energy shield|evasion|armour)|to maximum (energy shield|mana|life)|mana cost efficiency|increased projectile damage|increased elemental damage/.test(
      t
    )
  ) {
    return "medium";
  }
  return "easy";
}

/** 分析单件装备的符文插槽、超工匠石上限孔位、镶嵌物增幅联动与符文词池解锁。 */
export function analyzeItemSockets(item: ParsedItem, buildProfile: BuildSynergyProfile): ItemSocketAnalysis {
  const naturalMax = getNaturalMaxSockets(item.slot, item.baseType);
  const socketCount = item.socketCount || 0;
  const exceedsCraftable = naturalMax > 0 && socketCount > naturalMax;

  const allNativeText = (item.mods || []).join("\n");
  const hasSocketEffectMod = SOCKET_EFFECT_RE.test(allNativeText);

  const runeNames: string[] = [];
  const runeEffects: string[] = [];
  const bondedEffects: string[] = [];
  const runeUnlockedPools: string[] = [];

  for (const r of item.socketedRunes || []) {
    const clean = (r || "").trim();
    if (!clean) continue;
    const poolM = clean.match(RUNE_POOL_RE);
    if (poolM) {
      if (!runeUnlockedPools.includes(poolM[1])) runeUnlockedPools.push(poolM[1]);
      continue;
    }
    if (/^bonded\s*:/i.test(clean)) {
      bondedEffects.push(clean.replace(/^bonded\s*:\s*/i, "").trim());
      continue;
    }
    if (/\d|gain |increased |reduced |to level |to spirit|to maximum |to all |grants /i.test(clean)) {
      runeEffects.push(clean);
    } else {
      runeNames.push(clean);
    }
  }

  const requireSocketsInBudget = (exceedsCraftable || hasSocketEffectMod) && socketCount >= 2;
  const requireSocketsInSemi = (exceedsCraftable || hasSocketEffectMod) && socketCount >= 2;

  const notes: string[] = [];
  if (exceedsCraftable) {
    notes.push(`当前 ${socketCount} 孔超过工匠石上限(${naturalMax}孔)，半成品/白底需买卓越多槽底子，成品需锁孔位`);
  } else if (socketCount > 0 && hasSocketEffectMod) {
    notes.push(`含「镶嵌物效果增幅」词条，必须配合 ${socketCount} 个符文插槽与高阶符文/灵核才吃满收益`);
  } else if (socketCount > 0 && runeEffects.length > 0) {
    notes.push(`${socketCount} 孔在工匠石上限(${naturalMax}孔)内，半成品可不锁孔，买后用工匠石打孔并补插：${runeNames.slice(0, 2).join(" + ") || runeEffects[0]}`);
  }

  if (runeUnlockedPools.length > 0) {
    notes.push(`符文解锁专属词池「${runeUnlockedPools.join(" / ")}」(${runeNames[0] || "符文"})，自做半成品需先插对应符文`);
  }
  if (bondedEffects.length > 0) {
    if (buildProfile.bondedRunesActive) {
      notes.push(`已点「Wisdom of the Maji」，符文 Bonded 词条生效（${bondedEffects[0]}）`);
    } else {
      notes.push(`未点萨满 Bonded 升华，符文上的 Bonded 词条不生效，平替无需为 Bonded 付溢价`);
    }
  }

  return {
    socketCount,
    naturalMaxSockets: naturalMax,
    exceedsCraftableSockets: exceedsCraftable,
    hasSocketEffectMod,
    bondedActive: buildProfile.bondedRunesActive && bondedEffects.length > 0,
    runeNames,
    runeEffects,
    bondedEffects,
    runeUnlockedPools,
    requireSocketsInBudget,
    requireSocketsInSemi,
    advice: notes.join("；") || (socketCount > 0 ? `${socketCount} 符文插槽（工匠石可打满）` : "无符文插槽"),
  };
}

/**
 * 深度分析单件装备的所有词条、基底需求、防御门槛、天赋联动与符文插槽依赖。
 */
export function analyzeItemDeeply(item: ParsedItem, build?: ParsedBuildData | null): ItemDeepAnalysis {
  const bp = extractBuildSynergyProfile(build);
  const slotL = (item.slot || "").toLowerCase();
  const baseL = (item.baseType || "").toLowerCase();
  const slotKey = slotToKey(item.slot);
  const isSpeedWeapon = bp.speedWeaponSlots.has(slotL) || isSpeedUtilityWeapon(item);
  const socketAnalysis = analyzeItemSockets(item, bp);

  // 1. 收集与当前槽位相关的天赋联动
  const relevantSynergies = bp.slotSynergies.filter(
    (s) => s.slotKey === slotKey || (s.slotKey === "armour" && isArmourSlot(item.slot))
  );
  const passiveSynergies: string[] = relevantSynergies.map((s) => s.label);

  // 2. 基底是否不可降级替换（如 Ward 底子、潜能戒 Unset Ring、带重要自带技能的特殊基底如 Absent/Lament/Portent Amulet、Shrine/Stoic/Omen Sceptre、Chiming Staff 等）
  const isUnique = (item.rarity || "").toUpperCase() === "UNIQUE";
  let requireBaseType = false;
  let baseTypeReason = "";
  const wardVal = parseFirstNum(item.properties?.Ward);
  const origins = resolveModOrigins(item);

  const SPECIAL_SKILL_BASE_RE =
    /absent amulet|lament amulet|portent amulet|shrine sceptre|stoic sceptre|omen sceptre|rattling sceptre|lupine sceptre|ochre sceptre|chiming staff|dueling wand|siphoning wand|attuned wand/i;
  const GENERIC_BASE_ATTACK_SET = new Set([
    "spear throw",
    "raise shield",
    "parry",
    "firebolt",
    "freezing shards",
    "lightning bolt",
    "chaos bolt",
    "bone blast",
  ]);
  const FUNCTIONAL_GRANTED_SKILL_RE =
    /eternal rage|purity of |discipline|malice|sigil of power|\bblink\b|cast on |invocation|spellslinger|archmage|trinity|elemental conflux|herald of |ghost dance|wind dancer|grim feast|combat frenzy|charge regulation|siphon elements|overwhelming presence|mana remnants|berserk|convalescence|blasphemy|time of need|withering presence|arctic armour|scavenged plating|skeletal |power siphon|mana drain|soul offering/i;

  const evalGrantedSkill = (text: string) => {
    const m = text.match(/^grants\s+skill:\s*(?:level\s+(\d+)\s+)?(.+)$/i);
    if (!m) return null;
    const skillName = m[2].trim();
    const skKey = skillName.toLowerCase().replace(/\s+minion$/, "").replace(/\s+/g, " ").trim();
    const isSpecialBase = SPECIAL_SKILL_BASE_RE.test(baseL);
    const isGeneric = GENERIC_BASE_ATTACK_SET.has(skKey) && !isSpecialBase;
    let isUsed = false;
    let isMain = false;
    const supports: string[] = [];
    for (const sg of build?.skillGroups || []) {
      const gems = sg.gems || [];
      if (gems.length === 0) continue;
      const g0 = (gems[0].name || "").toLowerCase().replace(/\s+minion$/, "").replace(/\s+/g, " ").trim();
      if (g0 === skKey) {
        isUsed = true;
        if (sg.isMain) isMain = true;
        for (const g of gems.slice(1)) {
          if (g.name && !supports.includes(g.name)) supports.push(g.name);
        }
      }
    }
    const isImportant =
      isMain || supports.length > 0 || isSpecialBase || FUNCTIONAL_GRANTED_SKILL_RE.test(skillName) || (isUsed && !isGeneric);
    return { skillName, isImportant, supports, isSpecialBase };
  };

  const importantGranted = origins
    .map((o) => evalGrantedSkill(o.text))
    .filter((x): x is NonNullable<typeof x> => Boolean(x && x.isImportant));

  if (wardVal >= 40 || (bp.needsWardBase && baseL.includes("runeforged"))) {
    requireBaseType = true;
    baseTypeReason = `含结界 Ward (${wardVal || "底子"})，支撑技能 Ward 消耗机制，平替不可换普通底子`;
  } else if (importantGranted.length > 0) {
    requireBaseType = true;
    const g0 = importantGranted[0];
    baseTypeReason = `基底自带重要技能「${g0.skillName}」${g0.supports.length > 0 ? `（挂载 ${g0.supports.length} 颗辅助）` : ""}，换普通基底会造成功能损失`;
  } else if (baseL.includes("unset ring") || origins.some((o) => /grants 1 additional skill slot/i.test(o.text))) {
    requireBaseType = true;
    baseTypeReason = "潜能戒指提供额外技能槽位，平替需保留 Unset Ring 底子";
  } else if (baseL.includes("solar amulet") && !bp.gearSpiritDead) {
    requireBaseType = true;
    baseTypeReason = "基底自带精魂 (Spirit)，平替建议保留 Solar Amulet";
  } else if (baseL.includes("breach ring")) {
    requireBaseType = true;
    baseTypeReason = "裂隙戒指支持 50% 品质放大词缀，平替建议保留 Breach Ring";
  }

  // 3. 防御型防具的总防值门槛（配合 Subterfuge Mask / Spectral Ward / Beastial Skin 等天赋）
  let defenseFloor: DefenseFloorHint | null = null;
  const esVal = parseFirstNum(item.properties?.["Energy Shield"]);
  const evVal = parseFirstNum(item.properties?.Evasion);
  const arVal = parseFirstNum(item.properties?.Armour);

  const hasSyn = (kind: SlotPassiveSynergy["statKind"]) =>
    relevantSynergies.find((s) => !s.penalize && (s.statKind === kind || s.statKind === "all_def"));

  if (wardVal >= 50) {
    defenseFloor = {
      stat: "ward",
      rawValue: wardVal,
      budgetMin: Math.floor(wardVal * 0.75),
      exactMin: Math.floor(wardVal * 0.9),
      reason: `结界底子 Ward ${wardVal}`,
    };
  } else if (isArmourSlot(item.slot)) {
    const esSyn = hasSyn("es");
    const evSyn = hasSyn("ev");
    const arSyn = hasSyn("ar");
    if (esSyn && esVal >= 120) {
      defenseFloor = {
        stat: "es",
        rawValue: esVal,
        budgetMin: Math.floor(esVal * 0.78),
        exactMin: Math.floor(esVal * 0.9),
        reason: `${esSyn.label} → 优先锁总护盾 ≥ ${Math.floor(esVal * 0.78)}`,
      };
    } else if (evSyn && evVal >= 400) {
      defenseFloor = {
        stat: "ev",
        rawValue: evVal,
        budgetMin: Math.floor(evVal * 0.78),
        exactMin: Math.floor(evVal * 0.9),
        reason: `${evSyn.label} → 优先锁总闪避 ≥ ${Math.floor(evVal * 0.78)}`,
      };
    } else if (arSyn && arVal >= 400) {
      defenseFloor = {
        stat: "ar",
        rawValue: arVal,
        budgetMin: Math.floor(arVal * 0.78),
        exactMin: Math.floor(arVal * 0.9),
        reason: `${arSyn.label} → 优先锁总护甲 ≥ ${Math.floor(arVal * 0.78)}`,
      };
    } else if (bp.esCore && esVal >= 220 && esVal >= evVal * 0.3) {
      defenseFloor = {
        stat: "es",
        rawValue: esVal,
        budgetMin: Math.floor(esVal * 0.78),
        exactMin: Math.floor(esVal * 0.9),
        reason: `高护盾体系 → 优先锁总护盾 ≥ ${Math.floor(esVal * 0.78)}`,
      };
    }
  }

  // 4. 逐条词缀深度评分与联动标注
  const affixes: AnalyzedAffix[] = origins.map((entry, idx) => {
    const cleaned = entry.text;
    const t = cleaned.toLowerCase();
    const origin = entry.origin;
    const isAnoint = entry.isAnoint;
    const side = inferAffixSide(cleaned, origin);
    const difficulty = inferCraftDifficulty(cleaned, origin, isAnoint);
    const tags: string[] = [];
    let score = 15;
    let role: AffixRole = "utility";
    let excludeFromTradeLock = false;
    const reasons: string[] = [];

    // 涂油 / 基底自带技能：永不锁进市集平替与半成品
    if (isAnoint) {
      return {
        index: idx,
        raw: cleaned,
        cleaned,
        origin,
        isAnoint: true,
        side: "enchant",
        difficulty: "unrollable",
        role: "flexible",
        contextualScore: 5,
        synergyTags: ["🫧 涂油可自涂(搜货不锁)"],
        excludeFromTradeLock: true,
        reason: "涂油大点可用蒸馏情感自行补涂，搜货锁定会过滤掉 99% 优质平替",
      };
    }
    if (/^grants skill:/i.test(t)) {
      const gs = evalGrantedSkill(cleaned);
      const isImp = Boolean(gs?.isImportant);
      return {
        index: idx,
        raw: cleaned,
        cleaned,
        origin: "implicit",
        isAnoint: false,
        side: "implicit",
        difficulty: "unrollable",
        role: isImp ? "synergy" : "utility",
        contextualScore: isImp ? 108 : 8,
        synergyTags: isImp
          ? [
              `⚡ 基底核心技能(${gs!.skillName})`,
              ...(gs!.supports.length > 0 ? [`🔗 挂载${gs!.supports.length}辅助`] : []),
              "🔒 换底即功能损失",
            ]
          : ["📌 类别默认基底技(未启用)"],
        excludeFromTradeLock: true,
        reason: isImp
          ? `基底自带重要技能「${gs!.skillName}」，换普通底子会丢失该功能，需锁基底`
          : "类别默认自带普攻技，未启用时换底无功能损失",
      };
    }
    if (/^grants 1 additional skill slot/i.test(t)) {
      return {
        index: idx,
        raw: cleaned,
        cleaned,
        origin: "implicit",
        isAnoint: false,
        side: "implicit",
        difficulty: "unrollable",
        role: "synergy",
        contextualScore: 96,
        synergyTags: ["⚡ 基底额外技能槽(Unset Ring 刚需)"],
        excludeFromTradeLock: true,
        reason: "潜能戒提供额外技能槽位，平替需保留 Unset Ring 基底",
      };
    }

    if (origin === "fractured") {
      tags.push("🔒 破裂词条");
      score += 22;
    }

    // --- 核心输出与机制类词缀 ---
    if (/to level of all .*skills/.test(t)) {
      const isSpellSkill = /spell|fire|cold|lightning|chaos/.test(t) && !/attack|projectile|melee/.test(t);
      const isMinionSkill = /minion/.test(t);
      if (isMinionSkill && build && !bp.isMinionBuild) {
        score = 0;
        role = "dead";
        tags.push("⚠️ 非召唤无效");
      } else if (isSpellSkill && build && bp.isAttackBuild && !bp.isSpellBuild) {
        score = 5;
        role = "dead";
        tags.push("⚠️ 攻击BD无效");
      } else {
        score += 98;
        role = "core";
        tags.push("🔥 核心+技能等级");
      }
    } else if (/additional (arrow|projectile)/.test(t)) {
      score += 94;
      role = "core";
      tags.push("🔥 核心额外投射物");
    } else if (/explicit\s+speed\s+modifier\s+magnitudes/.test(t)) {
      score += isSpeedWeapon ? 96 : 68;
      role = "core";
      tags.push(isSpeedWeapon ? "⚡ 攻速工具组核心增幅" : "🔥 速度词缀幅度增幅");
    } else if (SOCKET_EFFECT_RE.test(t)) {
      const bonus = socketAnalysis.socketCount >= 2 ? 92 : 58;
      score += bonus;
      role = socketAnalysis.socketCount >= 2 ? "synergy" : "utility";
      tags.push(`💎 符文插槽联动(配${socketAnalysis.socketCount || 2}孔)`);
      reasons.push("需搭配多孔位与高阶符文/灵核才生效");
    } else if (/to spirit\b|increased spirit\b/.test(t)) {
      if (bp.gearSpiritDead) {
        score = 0;
        role = "dead";
        tags.push("🌳 天赋冲突(装备精魂无效)");
        reasons.push("升华禁用了装备精魂，切勿为精魂词缀付溢价");
      } else {
        score += 88;
        role = "core";
        tags.push("🔥 核心精魂");
      }
    } else if (/gain .* as extra .* damage/.test(t)) {
      if (bp.nonFireDead && !/fire|all elements/.test(t)) {
        score = 2;
        role = "dead";
        tags.push("🌳 火之化身冲突(非火伤无效)");
      } else {
        score += 88;
        role = "core";
        tags.push("🔥 额外伤害乘区");
      }
    } else if (/increased projectile speed/.test(t)) {
      if (bp.projSpeedIsDamage) {
        score += 90;
        role = "synergy";
        tags.push("🌳 投速转增伤(天赋/加速辅助)");
        reasons.push("配合 Feathered Fletching 或投射物加速辅助直接转化为巨额增伤");
      } else if (bp.isProjectileBuild) {
        score += 62;
        role = "core";
        tags.push("🎯 投射物手感/射程");
      } else {
        score += 25;
      }
    } else if (/increased projectile damage/.test(t)) {
      score += bp.isProjectileBuild ? 78 : 35;
      role = bp.isProjectileBuild ? "core" : "utility";
      if (bp.isProjectileBuild) tags.push("🔥 投射物核心增伤");
    } else if (/for each (type of )?elemental ailment|elemental ailment/.test(t)) {
      score += bp.elementalAilmentScaling ? 86 : 64;
      role = "synergy";
      if (bp.elementalAilmentScaling) tags.push("🌳 异常增伤联动");
    } else if (/increased movement speed/.test(t)) {
      if (slotL.includes("boot")) {
        score += bp.hasTravelAttack ? 56 : 96;
        role = bp.hasTravelAttack ? "utility" : "core";
        if (bp.hasTravelAttack) tags.push("💡 位移技能跑图(鞋移速可降配)");
      } else {
        score += 76;
        role = "core";
      }
    } else if (/increased attack speed|increased skill speed/.test(t)) {
      if (build && bp.isSpellBuild && !bp.isAttackBuild && /attack speed/.test(t)) {
        score = 5;
        role = "dead";
        tags.push("⚠️ 法术BD攻速无效");
      } else {
        score += isSpeedWeapon ? 98 : 80;
        role = "core";
        if (isSpeedWeapon) tags.push("⚡ 攻速工具组必保");
      }
    } else if (/increased cast speed/.test(t)) {
      if (build && bp.isAttackBuild && !bp.isSpellBuild) {
        score = 5;
        role = "dead";
        tags.push("⚠️ 攻击BD施速无效");
      } else {
        score += 80;
        role = "core";
      }
    } else if (/critical (hit |strike )?chance|critical (spell )?damage bonus/.test(t)) {
      if (bp.critDead) {
        score = 0;
        role = "dead";
        tags.push("🌳 必中(RT)冲突(暴击无效)");
      } else {
        score += bp.isCritBuild ? 82 : 68;
        role = bp.isCritBuild ? "core" : "utility";
        if (bp.isCritBuild) tags.push("🔥 暴击体系核心");
      }
    } else if (/adds \d+ to \d+ (physical|lightning|cold|fire|chaos) damage/.test(t)) {
      const m = t.match(/adds (\d+) to (\d+)/);
      const avg = m ? (Number(m[1]) + Number(m[2])) / 2 : 0;
      const isNonFire = /cold|lightning|chaos/.test(t);
      if (isSpeedWeapon && avg < 45) {
        score = 6;
        role = "dead";
        tags.push("⚡ 攻速工具武器低T点伤(可砍)");
        reasons.push("此武器专职攻速/位移，低数值点伤不是核心词缀");
      } else if (bp.nonFireDead && isNonFire) {
        score = 2;
        role = "dead";
        tags.push("🌳 火之化身冲突");
      } else if (build && bp.isSpellBuild && !bp.isAttackBuild && /to attacks/.test(t)) {
        score = 4;
        role = "dead";
        tags.push("⚠️ 法术BD攻击点伤无效");
      } else {
        // 武器点伤按数值档位区分高T与垃圾低T
        const isWeapon = slotL.includes("weapon");
        if (isWeapon && avg > 0 && avg < 25) {
          score += 22;
          role = "flexible";
          tags.push("💡 低档点伤(可降配)");
        } else {
          score += 76;
          role = "core";
        }
      }
    } else if (/increased physical damage/.test(t)) {
      if (isSpeedWeapon) {
        score = 8;
        role = "flexible";
        tags.push("⚡ 攻速工具武器可砍物理%");
      } else {
        score += 80;
        role = "core";
      }
    } else if (/increased (spell|elemental|lightning|cold|fire|chaos|attack) damage/.test(t)) {
      if (/spell damage/.test(t) && build && bp.isAttackBuild && !bp.isSpellBuild) {
        score = 5;
        role = "dead";
        tags.push("⚠️ 攻击BD法伤无效");
      } else {
        score += 74;
        role = "core";
      }
    } else if (/deflection rating equal to|deflect/.test(t)) {
      score += bp.deflectionScaling ? 86 : 72;
      role = "defense";
      tags.push(bp.deflectionScaling ? "🌳 偏转防御联动核心" : "🛡️ 核心偏转减伤");
    } else if (/energy shield recharge/.test(t)) {
      score += bp.esCore ? 38 : 22;
      role = "utility";
    } else if (/to maximum energy shield|increased energy shield(?!\s+recharge)|increased evasion and energy shield|increased armour and energy shield/.test(t)) {
      const esSyn = hasSyn("es");
      const evSyn = hasSyn("ev");
      if (esSyn) {
        score += 76 + esSyn.boost * 0.45;
        role = "synergy";
        tags.push(`🌳 ${esSyn.label}`);
      } else if (/evasion/.test(t) && evSyn) {
        score += 74 + evSyn.boost * 0.4;
        role = "synergy";
        tags.push(`🌳 ${evSyn.label}`);
      } else if (bp.esToMana) {
        score += 80;
        role = "synergy";
        tags.push("🌳 异能魔力(ES→魔力)");
      } else if (bp.esCore || /helmet|body|shield|focus/.test(slotL)) {
        score += 68;
        role = "defense";
      } else {
        score += 42;
        role = "defense";
      }
    } else if (/to evasion rating|increased evasion/.test(t)) {
      const evSyn = hasSyn("ev");
      const evPen = relevantSynergies.find((s) => s.penalize && s.statKind === "ev");
      if (evPen) {
        score += 18;
        role = "flexible";
        tags.push(`🌳 ${evPen.label}`);
      } else if (evSyn) {
        score += 76 + evSyn.boost * 0.45;
        role = "synergy";
        tags.push(`🌳 ${evSyn.label}`);
      } else if (bp.evasionToArmour) {
        score += 68;
        role = "synergy";
        tags.push("🌳 钢铁反射(闪避→护甲)");
      } else {
        score += /body|helmet|boots|gloves|shield/.test(slotL) ? 66 : 36;
        role = "defense";
      }
    } else if (/to armour|increased armour/.test(t)) {
      const arSyn = hasSyn("ar");
      if (arSyn) {
        score += 76 + arSyn.boost * 0.45;
        role = "synergy";
        tags.push(`🌳 ${arSyn.label}`);
      } else {
        score += /body|helmet|boots|gloves|shield/.test(slotL) ? 62 : 34;
        role = "defense";
      }
    } else if (/to maximum life\b/.test(t)) {
      if (bp.lifeDead) {
        score = 0;
        role = "dead";
        tags.push("🌳 异灵之体(CI)生命无效");
        reasons.push("CI 血量锁 1 点，平替与半成品绝不要锁生命词条");
      } else {
        score += 64;
        role = "defense";
      }
    } else if (/to maximum mana\b|increased maximum mana\b|mana regeneration|mana cost efficiency/.test(t)) {
      if (bp.manaDead) {
        score = 0;
        role = "dead";
        tags.push("🌳 血魔法(魔力无效)");
      } else if (bp.manaCore) {
        score += 84;
        role = "synergy";
        tags.push("🌳 蓝池/大法师核心");
      } else {
        score += 48;
        role = "utility";
      }
    } else if (/to chaos resistance/.test(t)) {
      if (bp.chaosResDead) {
        score = 0;
        role = "dead";
        tags.push("🌳 异灵之体(CI)混抗无效");
      } else {
        score += 56;
        role = "defense";
      }
    } else if (/to all elemental resistances/.test(t)) {
      score += 60;
      role = "defense";
    } else if (/to (fire|cold|lightning) resistance/.test(t)) {
      score += 40;
      role = "flexible";
      tags.push("🔄 单抗可跨部位/符文调配");
    } else if (/accuracy rating/.test(t)) {
      if (bp.accuracyIsDamage) {
        score += 84;
        role = "synergy";
        tags.push("🌳 天赋联动(命中→攻速/暴击/伤害)");
        reasons.push("命中值直接通过天赋/升华转化为核心输出，切勿砍掉");
      } else {
        score = 6;
        role = "flexible";
      }
    } else if (/to all attributes/.test(t)) {
      score += bp.strScaling || bp.dexScaling || bp.intScaling ? 78 : 48;
      role = bp.strScaling || bp.dexScaling || bp.intScaling ? "synergy" : "utility";
      if (bp.strScaling || bp.dexScaling || bp.intScaling) tags.push("🌳 属性堆叠联动");
    } else if (/to strength\b/.test(t)) {
      score += bp.strScaling || bp.giantBlood ? 74 : 26;
      role = bp.strScaling || bp.giantBlood ? "synergy" : "flexible";
      if (bp.giantBlood) tags.push("🌳 巨人之血(力量刚需)");
      else if (bp.strScaling) tags.push("🌳 力量增伤联动");
    } else if (/to dexterity\b/.test(t)) {
      score += bp.dexScaling ? 74 : 26;
      role = bp.dexScaling ? "synergy" : "flexible";
      if (bp.dexScaling) tags.push("🌳 敏捷增伤/攻速联动");
    } else if (/to intelligence\b/.test(t)) {
      score += bp.intScaling || bp.manaCore ? 74 : 26;
      role = bp.intScaling || bp.manaCore ? "synergy" : "flexible";
      if (bp.intScaling) tags.push("🌳 智慧增伤联动");
    } else if (/increased rarity of items found/.test(t)) {
      score += 42;
      role = "flexible";
      tags.push("💰 打宝稀有度(平替可先降配)");
    } else if (/charm slot/.test(t)) {
      score += 66;
      role = "core";
    } else if (/mark skills have .* increased/.test(t)) {
      score += 38;
      role = "utility";
    } else if (/light radius|stun threshold|thorns|life regeneration|reduced attribute requirements/.test(t)) {
      score = 4;
      role = "flexible";
    }

    // 符文解锁词池标记（例如 Kolr's Hunt 解锁 Marksman 投射物/印记词条）
    if (
      socketAnalysis.runeUnlockedPools.some((p) => p.toLowerCase().includes("marksman")) &&
      /projectile speed|projectile damage|mark skills|level of all projectile/i.test(t)
    ) {
      tags.push("💎 符文池(Marksman)");
    }
    if (
      socketAnalysis.runeUnlockedPools.some((p) => p.toLowerCase().includes("destruction")) &&
      /explicit speed modifier|attack speed|critical/i.test(t)
    ) {
      tags.push("💎 符文池(Destruction)");
    }

    if (role === "dead") {
      excludeFromTradeLock = true;
    }

    return {
      index: idx,
      raw: cleaned,
      cleaned,
      origin,
      isAnoint: false,
      side,
      difficulty,
      role,
      contextualScore: Math.round(score),
      synergyTags: Array.from(new Set(tags)),
      excludeFromTradeLock,
      reason: reasons.join("；") || tags.join(" · "),
    };
  });

  // 5. 生成简明平替 / 半成品策略摘要（按词条骨架去重；当已启用总防值门槛时优先保非冗余词条如偏转/抗性）
  const dedupBySkeleton = (list: AnalyzedAffix[]): AnalyzedAffix[] => {
    const seen = new Set<string>();
    const out: AnalyzedAffix[] = [];
    for (const a of list) {
      const sk = modSkeleton(a.cleaned);
      if (seen.has(sk)) continue;
      seen.add(sk);
      out.push(a);
    }
    return out;
  };

  const activeAffixes = dedupBySkeleton(
    [...affixes]
      .filter((a) => !a.excludeFromTradeLock && a.role !== "dead")
      .sort((a, b) => b.contextualScore - a.contextualScore)
  );

  const nonRedundantForBudget = defenseFloor
    ? activeAffixes.filter((a) => !isLocalDefenseStatOf(a.cleaned, defenseFloor!.stat))
    : activeAffixes;
  const budgetCandidates = nonRedundantForBudget.filter((a) => a.contextualScore >= 55);
  const budgetTop = (budgetCandidates.length > 0 ? budgetCandidates : activeAffixes.filter((a) => a.contextualScore >= 55)).slice(0, 3);

  const semiTop = [...activeAffixes]
    .filter((a) => a.difficulty === "hard" || a.origin === "fractured" || a.contextualScore >= 75)
    .sort((a, b) => {
      const diffRank = (d: AffixDifficulty) => (d === "hard" ? 2 : d === "medium" ? 1 : 0);
      const fA = a.origin === "fractured" ? 1 : 0;
      const fB = b.origin === "fractured" ? 1 : 0;
      return fB - fA || diffRank(b.difficulty) - diffRank(a.difficulty) || b.contextualScore - a.contextualScore;
    })
    .slice(0, 2);

  const budgetParts: string[] = [];
  if (requireBaseType) budgetParts.push(`锁基底(${item.baseType})`);
  if (defenseFloor) budgetParts.push(defenseFloor.reason);
  if (socketAnalysis.requireSocketsInBudget) budgetParts.push(`锁 ≥${socketAnalysis.socketCount} 符文孔`);
  if (budgetTop.length > 0) budgetParts.push(`保 ${budgetTop.map((a) => a.cleaned).slice(0, 2).join(" + ")}`);
  const deadMods = affixes.filter((a) => a.role === "dead");
  if (deadMods.length > 0) budgetParts.push(`已自动剔除 ${deadMods.length} 条无效词条`);

  const semiParts: string[] = [];
  if (semiTop.length > 0) {
    semiParts.push(`锁难出词条：${semiTop.map((a) => `[${a.side === "prefix" ? "前" : a.side === "suffix" ? "后" : "词"}]${a.cleaned}`).join(" + ")}`);
  }
  if (socketAnalysis.requireSocketsInSemi) {
    semiParts.push(`必须买 ${socketAnalysis.socketCount} 孔非腐化底子(超工匠石上限)`);
  }
  if (socketAnalysis.runeUnlockedPools.length > 0) {
    semiParts.push(`加工前需插「${socketAnalysis.runeNames[0] || socketAnalysis.runeUnlockedPools[0]}」解锁词池`);
  }

  if (isUnique) {
    const uSummary = socketAnalysis.exceedsCraftableSockets
      ? `传奇直买：注意该件为瓦尔 ${socketAnalysis.socketCount} 孔（超工匠上限 ${socketAnalysis.naturalMaxSockets} 孔），无需做装`
      : "传奇/暗金直接买成品（挑关键数值 roll / 瓦尔基底 / 打满工匠孔插符文 / 涂油，无需复杂做装）";
    return {
      slot: item.slot,
      isSpeedWeapon,
      requireBaseType,
      baseTypeReason,
      defenseFloor,
      passiveSynergies,
      socketAnalysis,
      affixes,
      budgetSummary: uSummary,
      semiSummary: "传奇/暗金不可做装，仅挑 roll / 瓦尔额外孔或基底 / 插符文",
    };
  }

  return {
    slot: item.slot,
    isSpeedWeapon,
    requireBaseType,
    baseTypeReason,
    defenseFloor,
    passiveSynergies,
    socketAnalysis,
    affixes,
    budgetSummary: budgetParts.join(" · ") || "按核心词缀降配搜货",
    semiSummary: semiParts.join(" · ") || "锁 1~2 条最难出核心词缀 + 非腐化留空位加工",
  };
}
