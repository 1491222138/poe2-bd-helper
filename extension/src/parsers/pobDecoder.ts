/**
 * 浏览器原生零依赖 PoB2 分享码离线解码器（用于本地后端未启动时的兜底降级）。
 */
import type { ParsedBuildData, ParsedGem, ParsedItem, ParsedModDetail, ParsedModOrigin, ParsedSkillGroup } from "@contracts";
import { getCachedPatchDict, hydratePassiveTree, loadPatchDict, type PatchDictData } from "./patchDict";

const META_PREFIXES = [
  "Rarity:", "Unique ID:", "Item Level:", "Quality:", "Sockets:",
  "LevelReq:", "Implicits:", "Armour:", "Evasion:", "Energy Shield:",
  "Ward:", "Physical Damage:", "Elemental Damage:", "Chaos Damage:",
  "Critical Strike Chance:", "Attacks per Second:", "Weapon Range:",
  "Radius:", "Limited to:", "Variant:", "Selected Variant:",
  "Has Alt Variant:", "League:", "Prefix:", "Suffix:", "Crafted:",
  "Rune:", "Charm Slots:",
];

function base64UrlToUint8Array(code: string): Uint8Array {
  let normalized = code.trim().replace(/\s+/g, "").replace(/-/g, "+").replace(/_/g, "/");
  while (normalized.length % 4 !== 0) {
    normalized += "=";
  }
  const binary = atob(normalized);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

async function decompressBytes(bytes: Uint8Array, format: CompressionFormat): Promise<string> {
  const ds = new DecompressionStream(format);
  const writer = ds.writable.getWriter();
  writer.write(bytes as unknown as BufferSource);
  writer.close();
  return new Response(ds.readable).text();
}

export async function decodePobCodeToXml(rawCode: string): Promise<string> {
  const cleaned = rawCode.trim();
  if (!cleaned) {
    throw new Error("PoB 分享码为空");
  }
  if (cleaned.startsWith("<?xml") || cleaned.startsWith("<PathOfBuilding")) {
    return cleaned;
  }
  const bytes = base64UrlToUint8Array(cleaned);
  try {
    return await decompressBytes(bytes, "deflate");
  } catch (e1) {
    try {
      return await decompressBytes(bytes, "deflate-raw");
    } catch {
      throw new Error(`PoB 分享码解压失败，请确认复制完整 (${(e1 as Error).message})`);
    }
  }
}

export function parseItemText(rawText: string): Omit<ParsedItem, "slot" | "itemId"> {
  const lines = (rawText || "")
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);

  let rarity = "UNKNOWN";
  let corrupted = false;
  let socketCount = 0;
  let levelReq = 0;
  let itemLevel = 0;
  const socketedRunes: string[] = [];
  const headerLines: string[] = [];
  const properties: Record<string, string> = {};
  const mods: string[] = [];
  const modDetails: ParsedModDetail[] = [];
  let inMods = false;
  let implicitsRemaining = 0;
  const activeVariants = new Set<string>();
  const KEEP_PROPS = [
    "Ward:",
    "Energy Shield:",
    "Evasion:",
    "Armour:",
    "Spirit:",
    "Charm Slots:",
    "Physical Damage:",
    "Elemental Damage:",
    "Critical Strike Chance:",
    "Attacks per Second:",
  ];

  for (const line of lines) {
    if (line.startsWith("Selected Variant:") || line.startsWith("Alt Variant")) {
      const v = line.split(":")[1]?.trim();
      if (v && v !== "0") activeVariants.add(v);
    }
  }

  const variantActive = (line: string): boolean => {
    if (activeVariants.size === 0) return true;
    const m = line.match(/\{variant:([0-9,]+)\}/i);
    if (!m) return true;
    return m[1].split(",").some((v) => activeVariants.has(v.trim()));
  };

  for (const line of lines) {
    if (line.startsWith("Rarity:")) {
      rarity = line.slice("Rarity:".length).trim().toUpperCase();
      continue;
    }
    if (line.startsWith("Item Level:")) {
      const m = line.slice("Item Level:".length).match(/\d+/);
      if (m) itemLevel = parseInt(m[0], 10) || 0;
      continue;
    }
    if (line.startsWith("LevelReq:")) {
      const m = line.slice("LevelReq:".length).match(/\d+/);
      if (m) levelReq = parseInt(m[0], 10) || 0;
      continue;
    }
    if (line.startsWith("Sockets:")) {
      const tokens = line.slice("Sockets:".length).match(/[A-Za-z]/g);
      if (tokens) socketCount = tokens.length;
      continue;
    }
    if (line.startsWith("Rune:")) {
      const rt = line.slice("Rune:".length).trim();
      if (rt) socketedRunes.push(rt);
      continue;
    }
    if (line === "Corrupted") {
      corrupted = true;
      continue;
    }
    if (line.startsWith("Implicits:")) {
      inMods = true;
      const m = line.slice("Implicits:".length).match(/\d+/);
      implicitsRemaining = m ? parseInt(m[0], 10) || 0 : 0;
      continue;
    }
    if (!inMods) {
      const propHit = KEEP_PROPS.find((p) => line.startsWith(p));
      if (propHit) {
        properties[propHit.slice(0, -1)] = line.slice(propHit.length).trim();
        continue;
      }
      if (!META_PREFIXES.some((p) => line.startsWith(p)) && !line.startsWith("{") && headerLines.length < 2) {
        headerLines.push(line);
      }
    } else {
      if (!variantActive(line)) continue;
      const low = line.toLowerCase();
      const isRune = /\{rune\}/i.test(line) || /\(rune\)\s*$/i.test(line);
      const clean = line.replace(/\{[^}]*\}/g, "").replace(/\s*\(rune\)\s*$/i, "").trim();
      if (clean && !clean.startsWith("Note:")) {
        const wasImplicitSlot = implicitsRemaining > 0;
        if (implicitsRemaining > 0) implicitsRemaining -= 1;
        const isAnoint = /^allocates\s+/i.test(clean);
        const isBonded = /^bonded\s*:/i.test(clean);
        let origin: ParsedModOrigin = "explicit";
        if (isRune) {
          origin = "rune";
          socketedRunes.push(clean);
        } else if (low.includes("{fractured}")) {
          origin = "fractured";
          mods.push(clean);
        } else if (low.includes("{desecrated}")) {
          origin = "desecrated";
          mods.push(clean);
        } else if (low.includes("{crafted}")) {
          origin = "crafted";
          mods.push(clean);
        } else if (low.includes("{enchant}") || isAnoint) {
          origin = "enchant";
          mods.push(clean);
        } else if (wasImplicitSlot) {
          origin = "implicit";
          mods.push(clean);
        } else {
          origin = "explicit";
          mods.push(clean);
        }
        modDetails.push({
          text: clean,
          origin,
          ...(isAnoint ? { isAnoint: true } : {}),
          ...(isBonded ? { isBonded: true } : {}),
        });
      }
    }
  }

  const name = headerLines[0] || "";
  const baseType = headerLines.length >= 2 ? headerLines[1] : name;

  return {
    rarity,
    name,
    baseType,
    corrupted,
    socketCount,
    socketedRunes: Array.from(new Set(socketedRunes)),
    mods: mods.slice(0, 14),
    modDetails,
    rawText: (rawText || "").trim(),
    ...(itemLevel > 0 ? { itemLevel } : {}),
    ...(levelReq > 0 ? { levelReq } : {}),
    ...(Object.keys(properties).length > 0 ? { properties } : {}),
  };
}

function isJewel(slotName: string, baseType: string): boolean {
  const s = slotName.toLowerCase();
  const b = baseType.toLowerCase();
  return (
    s.includes("jewel") ||
    s.includes("socket") ||
    b.includes("jewel") ||
    b.includes("diamond") ||
    b.includes("emerald") ||
    b.includes("ruby") ||
    b.includes("sapphire") ||
    b.includes("time-lost")
  );
}

function isFlaskOrCharm(slotName: string): boolean {
  const s = slotName.toLowerCase();
  return s.includes("flask") || s.includes("charm");
}

export function parsePobXmlBrowser(xmlText: string, patchDict?: PatchDictData | null): ParsedBuildData {
  const parser = new DOMParser();
  const doc = parser.parseFromString(xmlText, "text/xml");
  if (doc.querySelector("parsererror")) {
    throw new Error("PoB XML 格式解析失败，请检查分享码是否完整。");
  }

  const buildEl = doc.querySelector("Build");
  const stats: Record<string, number> = {};
  doc.querySelectorAll("Build > PlayerStat").forEach((st) => {
    const k = st.getAttribute("stat");
    const v = Number(st.getAttribute("value"));
    if (k && Number.isFinite(v)) {
      stats[k] = v;
    }
  });

  const character = {
    className: buildEl?.getAttribute("className") || "Unknown",
    ascendancy: buildEl?.getAttribute("ascendClassName") || "None",
    level: Number(buildEl?.getAttribute("level") || 0),
    mainSocketGroup: Number(buildEl?.getAttribute("mainSocketGroup") || 1),
    stats,
  };

  const itemsById: Record<string, Omit<ParsedItem, "slot" | "itemId">> = {};
  doc.querySelectorAll("Items > Item").forEach((itemEl) => {
    const id = itemEl.getAttribute("id");
    if (!id) return;
    itemsById[id] = parseItemText(itemEl.textContent || "");
  });

  const itemsContainer = doc.querySelector("Items");
  const activeItemSetId = itemsContainer?.getAttribute("activeItemSet") || "1";
  const activeItemSet =
    doc.querySelector(`Items > ItemSet[id="${activeItemSetId}"]`) ||
    doc.querySelector("Items > ItemSet");

  const equippedItems: ParsedItem[] = [];
  const jewels: ParsedItem[] = [];
  const flasksAndCharms: ParsedItem[] = [];
  const usedIds = new Set<string>();

  const slotEls = activeItemSet ? activeItemSet.querySelectorAll("Slot") : doc.querySelectorAll("Items > Slot");
  slotEls.forEach((slotEl) => {
    const slot = slotEl.getAttribute("name") || "";
    const itemId = slotEl.getAttribute("itemId") || "0";
    if (!slot || itemId === "0" || !itemsById[itemId]) return;
    usedIds.add(itemId);
    const entry: ParsedItem = { slot, itemId, ...itemsById[itemId] };
    if (isJewel(slot, entry.baseType)) jewels.push(entry);
    else if (isFlaskOrCharm(slot)) flasksAndCharms.push(entry);
    else equippedItems.push(entry);
  });

  const treeEl = doc.querySelector("Tree");
  const activeSpecIdx = parseInt(treeEl?.getAttribute("activeSpec") || "1", 10);
  const specs = Array.from(doc.querySelectorAll("Tree > Spec"));
  const activeSpec = specs[activeSpecIdx - 1] || specs[0];
  let treeVersion = "";
  let rawNodeIds: string[] = [];
  const ws1Ids = new Set<string>();
  const ws2Ids = new Set<string>();

  if (activeSpec) {
    treeVersion = activeSpec.getAttribute("treeVersion") || "";
    const nodesStr = activeSpec.getAttribute("nodes") || "";
    rawNodeIds = nodesStr
      .split(",")
      .map((n) => n.trim())
      .filter(Boolean);
    (activeSpec.querySelector("WeaponSet1")?.getAttribute("nodes") || "")
      .split(",")
      .map((n) => n.trim())
      .filter(Boolean)
      .forEach((id) => ws1Ids.add(id));
    (activeSpec.querySelector("WeaponSet2")?.getAttribute("nodes") || "")
      .split(",")
      .map((n) => n.trim())
      .filter(Boolean)
      .forEach((id) => ws2Ids.add(id));

    activeSpec.querySelectorAll("Sockets > Socket").forEach((sock) => {
      const itemId = sock.getAttribute("itemId") || "0";
      const nodeId = sock.getAttribute("nodeId") || "?";
      if (itemId === "0" || !itemsById[itemId] || usedIds.has(itemId)) return;
      usedIds.add(itemId);
      jewels.push({
        slot: `Tree Jewel (Node ${nodeId})`,
        itemId,
        ...itemsById[itemId],
      });
    });
  }

  const anointedRaw: Array<{ slot: string; itemName: string; name: string }> = [];
  const seenAnoint = new Set<string>();
  for (const it of equippedItems) {
    for (const mod of it.mods) {
      const m = /^Allocates\s+(.+)$/i.exec(mod.trim());
      if (!m) continue;
      const aname = m[1].trim();
      if (!aname || seenAnoint.has(aname.toLowerCase())) continue;
      seenAnoint.add(aname.toLowerCase());
      anointedRaw.push({ slot: it.slot, itemName: it.name || it.baseType, name: aname });
    }
  }
  const activeDict = patchDict !== undefined ? patchDict : getCachedPatchDict();
  const passiveTree = hydratePassiveTree(rawNodeIds, ws1Ids, ws2Ids, treeVersion, anointedRaw, activeDict);

  const config: Record<string, string | number | boolean> = {};
  const cfgContainer = doc.querySelector("Config");
  const activeCfgId = cfgContainer?.getAttribute("activeConfigSet") || "1";
  const activeCfg =
    doc.querySelector(`Config > ConfigSet[id="${activeCfgId}"]`) ||
    doc.querySelector("Config > ConfigSet") ||
    cfgContainer;
  activeCfg?.querySelectorAll("Input").forEach((inp) => {
    const name = (inp.getAttribute("name") || "").trim();
    if (!name || name.startsWith("quest")) return;
    if (inp.hasAttribute("boolean")) {
      config[name] = (inp.getAttribute("boolean") || "").toLowerCase() === "true";
    } else if (inp.hasAttribute("number")) {
      const n = Number(inp.getAttribute("number"));
      config[name] = Number.isFinite(n) ? n : inp.getAttribute("number") || "";
    } else if (inp.hasAttribute("string")) {
      config[name] = inp.getAttribute("string") || "";
    }
  });

  const skillsContainer = doc.querySelector("Skills");
  const activeSkillSetId = skillsContainer?.getAttribute("activeSkillSet") || "1";
  const activeSkillSet =
    doc.querySelector(`Skills > SkillSet[id="${activeSkillSetId}"]`) || skillsContainer;

  const skillGroups: ParsedSkillGroup[] = [];
  if (activeSkillSet) {
    activeSkillSet.querySelectorAll(":scope > Skill").forEach((sk, idx) => {
      if (sk.getAttribute("enabled") === "false") return;
      const slot = sk.getAttribute("slot") || "";
      const weaponSet =
        sk.getAttribute("weaponSet") ||
        sk.getAttribute("activeWeaponSet") ||
        (slot.toLowerCase().includes("swap") ? "2" : "");
      const gems: ParsedGem[] = [];
      sk.querySelectorAll("Gem").forEach((gem) => {
        if (gem.getAttribute("enabled") === "false") return;
        gems.push({
          name: gem.getAttribute("nameSpec") || gem.getAttribute("skillId") || gem.getAttribute("gemId") || "Unknown",
          skillId: gem.getAttribute("skillId") || "",
          level: Number(gem.getAttribute("level") || 1),
          quality: Number(gem.getAttribute("quality") || 0),
          weaponSet: gem.getAttribute("weaponSet") || "",
        });
      });
      if (gems.length > 0) {
        skillGroups.push({
          index: idx + 1,
          isMain: idx + 1 === character.mainSocketGroup,
          slot,
          label: sk.getAttribute("label") || "",
          weaponSet,
          gems,
        });
      }
    });
  }

  const set1 = equippedItems.filter((it) => ["weapon 1", "weapon 2"].includes(it.slot.toLowerCase()));
  const set2 = equippedItems.filter((it) => ["weapon 1 swap", "weapon 2 swap"].includes(it.slot.toLowerCase()));

  return {
    character,
    passiveTree,
    weaponSets: {
      hasDualWeaponSet:
        set2.length > 0 || (passiveTree.weaponSet1Notables.length > 0 && passiveTree.weaponSet2Notables.length > 0),
      set1,
      set2,
    },
    equippedItems,
    jewels,
    flasksAndCharms,
    skillGroups,
    config,
    notes: (doc.querySelector("Notes")?.textContent || "").trim().slice(0, 1200),
  };
}

export async function parsePobCodeBrowser(rawCode: string): Promise<ParsedBuildData> {
  const [xml, dict] = await Promise.all([decodePobCodeToXml(rawCode), loadPatchDict()]);
  return parsePobXmlBrowser(xml, dict);
}
