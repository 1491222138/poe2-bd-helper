/**
 * 注入在 https://poe.ninja/poe2/*、https://pobb.in/* 与 https://poe2htc.com/* 页面的抓取与自动填表脚本。
 */
import {
  buildPoe2PasteTextFromPayload,
  decodeHtcHandoffHash,
  normalizeHtcModTemplate,
  type CraftMinTierMode,
  type HtcCompactMod,
  type HtcHandoffPayload,
} from "@contracts";

interface PageBuildExtraction {
  url: string;
  title: string;
  isPoeNinjaPoe2: boolean;
  isPobbin: boolean;
  pobCode: string;
  pobbinUrl: string;
  detectedEntities: string[];
  extractedAt: string;
}

(function () {
  const w = window as unknown as { __POE2_WB_CONTENT_LOADED__?: boolean };
  if (w.__POE2_WB_CONTENT_LOADED__) return;
  w.__POE2_WB_CONTENT_LOADED__ = true;

  function createFloatingButton(): void {
    if (document.getElementById("poe2-wb-fab")) return;

    const btn = document.createElement("button");
    btn.id = "poe2-wb-fab";
    btn.textContent = "⚡ PoE2 侦察兵";
    Object.assign(btn.style, {
      position: "fixed",
      right: "20px",
      bottom: "24px",
      zIndex: "99999",
      padding: "10px 16px",
      borderRadius: "999px",
      border: "1px solid #d4a84f",
      background: "linear-gradient(135deg, #1b1f29, #10131a)",
      color: "#f5d78c",
      fontSize: "13px",
      fontWeight: "600",
      cursor: "pointer",
      boxShadow: "0 6px 20px rgba(0, 0, 0, 0.5)",
      transition: "transform 0.15s ease, box-shadow 0.15s ease",
    });

    btn.addEventListener("mouseenter", () => {
      btn.style.transform = "translateY(-2px)";
      btn.style.boxShadow = "0 8px 24px rgba(212, 168, 79, 0.35)";
    });
    btn.addEventListener("mouseleave", () => {
      btn.style.transform = "translateY(0)";
      btn.style.boxShadow = "0 6px 20px rgba(0, 0, 0, 0.5)";
    });
    btn.addEventListener("click", () => {
      chrome.runtime.sendMessage({ type: "OPEN_SIDE_PANEL" });
    });

    document.body.appendChild(btn);
  }

  function findPobCodeInDom(): string {
    const inputs = Array.from(document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input, textarea"));
    for (const el of inputs) {
      const val = (el.value || "").trim();
      if (val.length > 100 && /^[A-Za-z0-9+/_=-]+$/.test(val)) {
        return val;
      }
    }
    // 兼容 poe.ninja 将 PoB 码放在 code / pre 或 data 属性中的场景
    const codeNodes = Array.from(document.querySelectorAll<HTMLElement>("code, pre, [data-pob], [data-clipboard-text]"));
    for (const el of codeNodes) {
      const candidate = (el.dataset.pob || el.dataset.clipboardText || el.textContent || "").trim();
      if (candidate.length > 120 && /^[A-Za-z0-9+/_=-]+$/.test(candidate)) {
        return candidate;
      }
    }
    return "";
  }

  function findPobbinLink(): string {
    const url = window.location.href;
    if (/^https?:\/\/pobb\.in\/[A-Za-z0-9_-]+/i.test(url)) {
      return url;
    }
    const links = Array.from(document.querySelectorAll<HTMLAnchorElement>("a[href*='pobb.in/']"));
    for (const a of links) {
      if (/^https?:\/\/pobb\.in\/[A-Za-z0-9_-]+/i.test(a.href)) {
        return a.href;
      }
    }
    return "";
  }

  function extractPageBuildData(): PageBuildExtraction {
    const url = window.location.href;
    const title = document.title;
    const mainEl = document.querySelector("main") || document.body;

    const imgAlts = Array.from(mainEl.querySelectorAll("img[alt]"))
      .map((img) => img.getAttribute("alt")?.trim() || "")
      .filter((alt) => alt.length > 1 && alt.length < 80);

    return {
      url,
      title,
      isPoeNinjaPoe2: url.includes("poe.ninja/poe2"),
      isPobbin: url.includes("pobb.in/"),
      pobCode: findPobCodeInDom(),
      pobbinUrl: findPobbinLink(),
      detectedEntities: Array.from(new Set(imgAlts)).slice(0, 80),
      extractedAt: new Date().toISOString(),
    };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "EXTRACT_BUILD_DATA") {
      try {
        sendResponse({ ok: true, data: extractPageBuildData() });
      } catch (err) {
        sendResponse({ ok: false, error: (err as Error).message });
      }
      return true;
    }
    return false;
  });

  function sleep(ms: number): Promise<void> {
    return new Promise((r) => setTimeout(r, ms));
  }

  function setReactInputValue(el: HTMLInputElement, value: string): void {
    const proto = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value");
    if (proto?.set) proto.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function setReactTextareaValue(el: HTMLTextAreaElement, value: string): void {
    const proto = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value");
    if (proto?.set) proto.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function setReactSelectValue(el: HTMLSelectElement, value: string): void {
    const proto = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value");
    if (proto?.set) proto.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function getLabelOwnText(labelEl: HTMLLabelElement): string {
    const clone = labelEl.cloneNode(true) as HTMLLabelElement;
    clone.querySelectorAll("select, input, textarea, button").forEach((n) => n.remove());
    return (clone.textContent || "").replace(/\s+/g, " ").trim();
  }

  function findLabeledControl<T extends HTMLElement>(labelPattern: RegExp, controlSelector: string): T | null {
    const labels = Array.from(document.querySelectorAll<HTMLLabelElement>("label"));
    for (const lbl of labels) {
      const ownText = getLabelOwnText(lbl);
      if (!labelPattern.test(ownText)) continue;
      const nested = lbl.querySelector<T>(controlSelector);
      if (nested) return nested;
      if (lbl.htmlFor) {
        const byFor = document.getElementById(lbl.htmlFor);
        if (byFor && byFor.matches(controlSelector)) return byFor as T;
      }
    }
    const direct = Array.from(document.querySelectorAll<T>(controlSelector));
    for (const el of direct) {
      const aria = (el.getAttribute("aria-label") || "").trim();
      if (aria && labelPattern.test(aria)) return el;
    }
    return null;
  }

  function normalizeLooseKey(str: string): string {
    return (str || "").toLowerCase().replace(/[\s_-]+/g, "");
  }

  function toSearchKeyword(modText: string): string {
    return (modText || "")
      .replace(/\s*\((implicit|crafted|fractured|rune|desecrated|mutated|enchant|local|unscalable)\)\s*/gi, " ")
      .replace(/^[+-]?\d+(?:\.\d+)?%?\s*(?:to\s+\d+(?:\.\d+)?%?\s*)?/i, "")
      .replace(/\b\d+(?:\.\d+)?\s*(?:to|[–-])\s*\d+(?:\.\d+)?%?\b/gi, "")
      .replace(/[+-]?\d+(?:\.\d+)?%?/g, "")
      .replace(/\b(adds|to)\s+$/i, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function tokenizeMod(norm: string): Set<string> {
    const stop = new Set(["to", "of", "the", "a", "an", "with", "for", "and", "in", "on", "#"]);
    return new Set(norm.split(/[^a-z0-9%]+/).filter((t) => t.length >= 2 && !stop.has(t)));
  }

  type ModTagKind = "normal" | "ess" | "desec" | "alloy" | "perf";

  function parseCandidateTemplateAndTag(rawText: string): { cleanTemplate: string; tagKind: ModTagKind } {
    let raw = (rawText || "")
      .replace(/^[PS]\s*·\s*/i, "")
      .replace(/\s*—\s*Already in the target\s*$/i, "")
      .replace(/\s*·\s*on your item\s*/gi, " ")
      .replace(/\s+/g, " ")
      .trim();

    let tagKind: ModTagKind = "normal";
    if (/\bdesec\b|desecrated/i.test(raw)) tagKind = "desec";
    else if (/perfect essence|\bperf\b/i.test(raw)) tagKind = "perf";
    else if (/\bess\b|essence only|essence/i.test(raw)) tagKind = "ess";
    else if (/\balloy\b/i.test(raw)) tagKind = "alloy";

    raw = raw
      .replace(/\s*·\s*(Essence only|Perfect Essence|Alloy|Desecrated)\b/gi, " ")
      .replace(/\b(ess|desec|alloy|perf)\b/gi, " ")
      .replace(/\s+/g, " ")
      .trim();

    return { cleanTemplate: normalizeHtcModTemplate(raw), tagKind };
  }

  function extractRowTemplateText(rowEl: Element): { cleanTemplate: string; tagKind: ModTagKind } {
    const clone = rowEl.cloneNode(true) as Element;
    clone.querySelectorAll("button, select, input").forEach((b) => b.remove());
    return parseCandidateTemplateAndTag(clone.textContent || "");
  }

  function scoreModTemplateMatch(
    qNorm: string,
    qTokens: Set<string>,
    cleanTemplate: string,
    tagKind: ModTagKind,
    mod: HtcCompactMod
  ): number {
    if (!qNorm || !cleanTemplate) return 0;
    let score = 0;
    if (cleanTemplate === qNorm) {
      score = 1.0;
    } else {
      const rTokens = tokenizeMod(cleanTemplate);
      if (qTokens.size > 0 && rTokens.size > 0) {
        let inter = 0;
        for (const tok of qTokens) if (rTokens.has(tok)) inter++;
        const jacc = inter / Math.max(1, qTokens.size + rTokens.size - inter);
        const recall = inter / qTokens.size;
        score = jacc * 0.65 + recall * 0.35;
      }
    }
    if (score < 0.72) return 0;

    if (mod.d) {
      score += tagKind === "desec" ? 0.08 : -0.05;
    } else {
      if (tagKind === "normal") score += 0.06;
      else if (tagKind === "ess" || tagKind === "alloy" || tagKind === "perf") score -= 0.12;
      else if (tagKind === "desec") score -= 0.06;
    }
    return score;
  }

  function parseTierOptionBounds(txt: string): { lo: number | null; hi: number | null } {
    const parts = txt.split("·").map((s) => s.trim());
    const valuePart = parts.length > 1 ? parts[parts.length - 1] : txt.replace(/^T\d+\b/i, "").replace(/ilvl\s*\d+/gi, "");
    const nums = (valuePart.match(/\d+(?:\.\d+)?/g) || []).map((n) => parseFloat(n)).filter((n) => Number.isFinite(n));
    if (nums.length >= 4) {
      const lo = (nums[0] + nums[2]) / 2;
      const hi = (nums[1] + nums[3]) / 2;
      return { lo: Math.min(lo, hi), hi: Math.max(lo, hi) };
    }
    if (nums.length >= 2) {
      return { lo: Math.min(nums[0], nums[1]), hi: Math.max(nums[0], nums[1]) };
    }
    if (nums.length === 1) {
      return { lo: nums[0], hi: nums[0] };
    }
    return { lo: null, hi: null };
  }

  function pickBestTierSelectOption(
    selectEl: HTMLSelectElement | null,
    targetVal: number | null | undefined,
    minTierMode: CraftMinTierMode = "mid"
  ): string {
    if (!selectEl || selectEl.options.length === 0) return "";
    const parsed = Array.from(selectEl.options).map((opt, idx) => {
      const txt = (opt.textContent || "").trim();
      const { lo, hi } = parseTierOptionBounds(txt);
      return { opt, txt, idx, lo, hi };
    });

    let chosen = parsed[0];
    if (targetVal != null && Number.isFinite(targetVal)) {
      const withBounds = parsed.filter((p) => p.lo != null && p.hi != null);
      if (withBounds.length > 0) {
        const exact = withBounds.find((p) => targetVal >= p.lo! && targetVal <= p.hi!);
        if (exact) {
          chosen = exact;
        } else {
          const topHi = Math.max(...withBounds.map((p) => p.hi!));
          if (targetVal > topHi) {
            chosen = withBounds[0];
          } else {
            const floorHit = withBounds.find((p) => targetVal >= p.lo! * 0.92);
            if (floorHit) {
              chosen = floorHit;
            } else {
              let best = withBounds[0];
              let bestDist = Infinity;
              for (const p of withBounds) {
                const dist = Math.abs((p.lo! + p.hi!) / 2 - targetVal);
                if (dist < bestDist) {
                  bestDist = dist;
                  best = p;
                }
              }
              chosen = best;
            }
          }
        }
      }
    } else if (minTierMode === "high") {
      chosen = parsed[0];
    } else if (minTierMode === "any") {
      chosen = parsed[parsed.length - 1];
    } else {
      const midIdx = Math.min(parsed.length - 1, Math.floor(parsed.length * 0.35));
      chosen = parsed[midIdx];
    }

    setReactSelectValue(selectEl, chosen.opt.value);
    return chosen.txt;
  }

  async function selectHtcBaseClass(baseClass: string): Promise<boolean> {
    const sel = findLabeledControl<HTMLSelectElement>(/^base$/i, "select");
    if (!sel) return false;
    const targetLow = baseClass.trim().toLowerCase();
    const targetLoose = normalizeLooseKey(baseClass);
    const opt = Array.from(sel.options).find((o) => {
      const txt = (o.textContent || "").trim().toLowerCase();
      const val = (o.value || "").trim().toLowerCase();
      return (
        txt === targetLow ||
        val === targetLow ||
        normalizeLooseKey(txt) === targetLoose ||
        normalizeLooseKey(val) === targetLoose
      );
    });
    if (!opt) return false;
    if (sel.value !== opt.value) {
      setReactSelectValue(sel, opt.value);
      await sleep(260);
    }
    return true;
  }

  async function selectHtcVariant(variantHint?: string, baseType?: string): Promise<boolean> {
    const sel = findLabeledControl<HTMLSelectElement>(/^variant$/i, "select");
    if (!sel) return false;
    const baseLow = (baseType || "").trim().toLowerCase();
    const hintParts = (variantHint || "")
      .split("/")
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);

    const opts = Array.from(sel.options);
    let hit: HTMLOptionElement | undefined;
    if (baseLow) {
      hit = opts.find((o) => (o.textContent || "").toLowerCase().includes(baseLow));
    }
    if (!hit && hintParts.length > 0) {
      hit = opts.find((o) => {
        const txt = `${o.textContent || ""} ${o.value || ""}`.toLowerCase();
        return !/^any\b/.test((o.textContent || "").trim().toLowerCase()) && hintParts.every((p) => txt.includes(p));
      });
    }
    if (!hit) return false;
    if (sel.value !== hit.value) {
      setReactSelectValue(sel, hit.value);
      await sleep(220);
    }
    return true;
  }

  async function setHtcItemLevel(ilvl: number): Promise<boolean> {
    const inp = findLabeledControl<HTMLInputElement>(/^item level$/i, "input");
    if (!inp) return false;
    if (inp.value !== String(ilvl)) {
      setReactInputValue(inp, String(ilvl));
      await sleep(100);
    }
    return true;
  }

  async function setHtcRarity(rarity?: "n" | "m" | "r"): Promise<boolean> {
    const sel = findLabeledControl<HTMLSelectElement>(/^rarity$/i, "select");
    if (!sel) return false;
    const targetVal = rarity === "m" ? "magic" : "rare";
    const opt = Array.from(sel.options).find((o) => o.value.toLowerCase() === targetVal);
    if (!opt) return false;
    if (sel.value !== opt.value) {
      setReactSelectValue(sel, opt.value);
      await sleep(120);
    }
    return true;
  }

  async function setHtcRunes(runes?: string[]): Promise<void> {
    if (!runes || runes.length === 0) return;
    const wanted = runes.map((r) => r.toLowerCase().replace(/[’']/g, "'"));
    const checkboxes = Array.from(document.querySelectorAll<HTMLInputElement>("fieldset input[type='checkbox']"));
    for (const cb of checkboxes) {
      const lbl = (cb.closest("label")?.textContent || "").toLowerCase().replace(/[’']/g, "'");
      const shouldCheck = wanted.some((w) => lbl.includes(w));
      if (shouldCheck && !cb.checked) {
        cb.click();
        await sleep(60);
      }
    }
  }

  function findScratchModifierRow(
    mod: HtcCompactMod
  ): { row: Element; addBtn: HTMLButtonElement; tierSelect: HTMLSelectElement | null; cleanTemplate: string; score: number } | null {
    const qNorm = normalizeHtcModTemplate(mod.t);
    const qTokens = tokenizeMod(qNorm);
    if (!qNorm) return null;

    const plusBtns = Array.from(document.querySelectorAll<HTMLButtonElement>("button")).filter(
      (b) => (b.textContent || "").trim() === "+" && !b.disabled
    );

    let best: {
      row: Element;
      addBtn: HTMLButtonElement;
      tierSelect: HTMLSelectElement | null;
      cleanTemplate: string;
      score: number;
    } | null = null;

    for (const addBtn of plusBtns) {
      let row: Element | null = addBtn.parentElement;
      for (let depth = 0; depth < 3 && row; depth++) {
        const txt = (row.textContent || "").trim();
        if (txt.length > 8) break;
        row = row.parentElement;
      }
      if (!row) continue;
      if (/^Any (prefix|suffix)/i.test((row.textContent || "").trim())) continue;

      const { cleanTemplate, tagKind } = extractRowTemplateText(row);
      const score = scoreModTemplateMatch(qNorm, qTokens, cleanTemplate, tagKind, mod);
      if (score <= 0) continue;

      if (!best || score > best.score) {
        best = {
          row,
          addBtn,
          tierSelect: row.querySelector<HTMLSelectElement>("select"),
          cleanTemplate,
          score,
        };
      }
    }
    return best;
  }

  function findHaveItemAddButton(mod: HtcCompactMod): { btn: HTMLButtonElement; cleanTemplate: string; score: number } | null {
    const qNorm = normalizeHtcModTemplate(mod.t);
    const qTokens = tokenizeMod(qNorm);
    if (!qNorm) return null;

    const addBtns = Array.from(document.querySelectorAll<HTMLButtonElement>("button[aria-label]")).filter((b) => {
      if (b.disabled) return false;
      const aria = b.getAttribute("aria-label") || "";
      return /^Add .+ to your item$/i.test(aria);
    });

    let best: { btn: HTMLButtonElement; cleanTemplate: string; score: number } | null = null;
    for (const btn of addBtns) {
      const aria = btn.getAttribute("aria-label") || "";
      const ariaTemplate = aria.replace(/^Add\s+/i, "").replace(/\s+to your item$/i, "");
      const visibleText = (btn.textContent || "").trim();
      const { cleanTemplate, tagKind } = parseCandidateTemplateAndTag(
        visibleText.length >= ariaTemplate.length ? visibleText : ariaTemplate
      );
      const score = scoreModTemplateMatch(qNorm, qTokens, cleanTemplate, tagKind, mod);
      if (score <= 0) continue;
      if (!best || score > best.score) best = { btn, cleanTemplate, score };
    }
    return best;
  }

  function findMatchingAriaControl<T extends HTMLElement>(
    selector: string,
    prefixRe: RegExp,
    modTemplateNorm: string
  ): T | null {
    const nodes = Array.from(document.querySelectorAll<T>(selector));
    const qTokens = tokenizeMod(modTemplateNorm);
    let best: { el: T; score: number } | null = null;
    for (const el of nodes) {
      const aria = el.getAttribute("aria-label") || "";
      if (!prefixRe.test(aria)) continue;
      const rest = aria.replace(prefixRe, "").trim();
      const norm = normalizeHtcModTemplate(rest);
      if (norm === modTemplateNorm) return el;
      const rTokens = tokenizeMod(norm);
      if (qTokens.size > 0 && rTokens.size > 0) {
        let inter = 0;
        for (const t of qTokens) if (rTokens.has(t)) inter++;
        const score = inter / Math.max(1, qTokens.size + rTokens.size - inter);
        if (score >= 0.72 && (!best || score > best.score)) best = { el, score };
      }
    }
    return best ? best.el : null;
  }

  function findPasteAccordionToggle(): HTMLButtonElement | null {
    return (
      Array.from(document.querySelectorAll<HTMLButtonElement>("button[aria-expanded]")).find((b) =>
        /paste your item/i.test(b.textContent || "")
      ) || null
    );
  }

  async function ensurePasteAccordionOpen(): Promise<HTMLTextAreaElement | null> {
    const toggle = findPasteAccordionToggle();
    if (toggle && toggle.getAttribute("aria-expanded") !== "true") {
      toggle.click();
      await sleep(180);
    }
    return document.querySelector<HTMLTextAreaElement>("textarea#paste-item, textarea");
  }

  async function tryNativePasteItem(rawItemText: string): Promise<{ ok: boolean; summary: string }> {
    const textarea = await ensurePasteAccordionOpen();
    if (!textarea) return { ok: false, summary: "" };
    setReactTextareaValue(textarea, rawItemText);
    await sleep(320);

    const useBtn = Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find(
      (b) => /^use this item$/i.test((b.textContent || "").trim()) && !b.disabled
    );
    if (!useBtn) return { ok: false, summary: "" };

    const previewHeader = textarea.parentElement?.querySelector("p strong")?.textContent?.trim() || "";
    useBtn.click();
    await sleep(320);
    return { ok: true, summary: previewHeader || "Paste your item" };
  }

  async function fillHaveItemTargetMods(
    payload: HtcHandoffPayload
  ): Promise<{ matched: Array<{ text: string; tierLabel: string }>; unmatched: string[] }> {
    const matched: Array<{ text: string; tierLabel: string }> = [];
    const unmatched: string[] = [];
    if (!payload.targetMods || payload.targetMods.length === 0) return { matched, unmatched };

    const fullPlanBtn = Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find((b) =>
      /^full plan to a target$/i.test((b.textContent || "").trim())
    );
    if (fullPlanBtn && fullPlanBtn.getAttribute("aria-pressed") !== "true") {
      fullPlanBtn.click();
      await sleep(240);
    }

    const addSelect = findLabeledControl<HTMLSelectElement>(/^add a target mod$/i, "select");
    if (!addSelect) {
      for (const m of payload.targetMods) unmatched.push(m.t);
      return { matched, unmatched };
    }

    const seen = new Set<string>();
    for (const mod of payload.targetMods) {
      const qNorm = normalizeHtcModTemplate(mod.t);
      if (!qNorm || seen.has(qNorm)) continue;
      seen.add(qNorm);
      const qTokens = tokenizeMod(qNorm);

      let best: { opt: HTMLOptionElement; cleanTemplate: string; score: number } | null = null;
      for (const opt of Array.from(addSelect.options)) {
        if (opt.disabled || !opt.value || opt.value.startsWith("any:")) continue;
        const { cleanTemplate, tagKind } = parseCandidateTemplateAndTag(opt.textContent || "");
        const score = scoreModTemplateMatch(qNorm, qTokens, cleanTemplate, tagKind, mod);
        if (score <= 0) continue;
        if (!best || score > best.score) best = { opt, cleanTemplate, score };
      }

      if (!best) {
        unmatched.push(mod.t);
        continue;
      }

      setReactSelectValue(addSelect, best.opt.value);
      await sleep(150);

      const tierSelect = findMatchingAriaControl<HTMLSelectElement>(
        "select[aria-label]",
        /^Target tier for\s+/i,
        best.cleanTemplate
      );
      const tierLabel = pickBestTierSelectOption(tierSelect, mod.v, payload.minTierMode || "mid");
      matched.push({ text: mod.t, tierLabel });
      await sleep(90);
    }

    return { matched, unmatched };
  }

  interface HtcAutoFillReport {
    baseSelected: boolean;
    usedNativePaste: boolean;
    matchedExisting: Array<{ text: string; tierLabel: string }>;
    matchedTarget: Array<{ text: string; tierLabel: string }>;
    unmatched: string[];
    statusText: string;
  }

  function renderHtcFloatingStatus(payload: HtcHandoffPayload, report: HtcAutoFillReport, onRetry: () => void): void {
    let panel = document.getElementById("poe2-wb-htc-panel");
    if (!panel) {
      panel = document.createElement("div");
      panel.id = "poe2-wb-htc-panel";
      Object.assign(panel.style, {
        position: "fixed",
        right: "18px",
        bottom: "18px",
        zIndex: "999999",
        width: "356px",
        maxHeight: "72vh",
        overflowY: "auto",
        padding: "12px 14px",
        borderRadius: "10px",
        border: "1px solid #d4a84f",
        background: "rgba(15, 18, 25, 0.96)",
        color: "#eef2f7",
        fontSize: "12px",
        lineHeight: "1.45",
        boxShadow: "0 10px 28px rgba(0,0,0,0.6)",
        fontFamily: "system-ui, -apple-system, sans-serif",
      });
      document.body.appendChild(panel);
    }

    const skipped = payload.skippedNotes || [];
    const modeZh = payload.mode === "have_item" ? "已有半成品 (I have an item)" : "从白底规划 (Plan from scratch)";

    panel.innerHTML = "";
    const header = document.createElement("div");
    Object.assign(header.style, { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px", gap: "6px" });
    const title = document.createElement("strong");
    title.style.color = "#f5d78c";
    title.textContent = "⚡ PoE2 Workbench → POE2HTC";
    const actions = document.createElement("div");
    Object.assign(actions.style, { display: "flex", gap: "5px", alignItems: "center" });

    const copyItemBtn = document.createElement("button");
    copyItemBtn.textContent = "📋 复制装备";
    copyItemBtn.title = "复制标准 PoE2 装备文本，可直接粘贴到 Paste your item";
    Object.assign(copyItemBtn.style, {
      background: "#262c3a",
      color: "#cbd5e1",
      border: "1px solid #475569",
      borderRadius: "5px",
      padding: "2px 7px",
      cursor: "pointer",
      fontSize: "11px",
    });
    copyItemBtn.onclick = () => {
      const pasteText = buildPoe2PasteTextFromPayload(payload);
      void navigator.clipboard?.writeText(pasteText);
      copyItemBtn.textContent = "✓ 已复制";
      setTimeout(() => {
        copyItemBtn.textContent = "📋 复制装备";
      }, 1600);
    };

    const retryBtn = document.createElement("button");
    retryBtn.textContent = "🔄 重填";
    Object.assign(retryBtn.style, {
      background: "#262c3a",
      color: "#f5d78c",
      border: "1px solid #52472e",
      borderRadius: "5px",
      padding: "2px 7px",
      cursor: "pointer",
      fontSize: "11px",
    });
    retryBtn.onclick = onRetry;

    const closeBtn = document.createElement("button");
    closeBtn.textContent = "✕";
    Object.assign(closeBtn.style, {
      background: "transparent",
      color: "#9aa4b2",
      border: "none",
      cursor: "pointer",
      fontSize: "13px",
    });
    closeBtn.onclick = () => panel?.remove();

    actions.append(copyItemBtn, retryBtn, closeBtn);
    header.append(title, actions);
    panel.appendChild(header);

    const meta = document.createElement("div");
    meta.style.color = "#b8c2d1";
    meta.style.marginBottom = "6px";
    meta.textContent = `${payload.itemName || payload.baseClass} · ${payload.baseClass} · iLvl ${payload.ilvl} · ${modeZh}`;
    panel.appendChild(meta);

    const statusEl = document.createElement("div");
    statusEl.style.marginBottom = "8px";
    statusEl.style.color = report.baseSelected ? "#86efac" : "#fca5a5";
    statusEl.textContent = report.statusText;
    panel.appendChild(statusEl);

    if (report.usedNativePaste) {
      const note = document.createElement("div");
      note.style.marginBottom = "6px";
      note.style.color = "#93c5fd";
      note.textContent = "📋 已自动展开「Paste your item」并完成原生导入。";
      panel.appendChild(note);
    }

    if (report.matchedExisting.length > 0) {
      const sec = document.createElement("div");
      sec.style.marginBottom = "6px";
      const lbl = document.createElement("div");
      lbl.style.color = "#93c5fd";
      lbl.style.fontWeight = "600";
      lbl.textContent = `🧱 已填入半成品现有词缀 (${report.matchedExisting.length} 条)`;
      sec.appendChild(lbl);
      for (const m of report.matchedExisting) {
        const row = document.createElement("div");
        row.style.color = "#dbe4f0";
        row.textContent = `• ${m.text}${m.tierLabel ? ` → ${m.tierLabel}` : ""}`;
        sec.appendChild(row);
      }
      panel.appendChild(sec);
    }

    if (report.matchedTarget.length > 0) {
      const sec = document.createElement("div");
      sec.style.marginBottom = "6px";
      const lbl = document.createElement("div");
      lbl.style.color = "#86efac";
      lbl.style.fontWeight = "600";
      lbl.textContent = `✅ 已自动勾选目标词缀 (${report.matchedTarget.length} 条)`;
      sec.appendChild(lbl);
      for (const m of report.matchedTarget) {
        const row = document.createElement("div");
        row.style.color = "#dbe4f0";
        row.textContent = `• ${m.text}${m.tierLabel ? ` → ${m.tierLabel}` : ""}`;
        sec.appendChild(row);
      }
      panel.appendChild(sec);
    }

    if (report.unmatched.length > 0) {
      const sec = document.createElement("div");
      sec.style.marginBottom = "6px";
      const lbl = document.createElement("div");
      lbl.style.color = "#fcd34d";
      lbl.style.fontWeight = "600";
      lbl.textContent = `⚠️ 未自动匹配 (${report.unmatched.length} 条，点击复制搜索关键词)`;
      sec.appendChild(lbl);
      for (const u of report.unmatched) {
        const kw = toSearchKeyword(u) || u;
        const btn = document.createElement("button");
        btn.textContent = `📋 ${u}`;
        btn.title = `点击复制搜索关键词「${kw}」`;
        Object.assign(btn.style, {
          display: "block",
          width: "100%",
          textAlign: "left",
          marginTop: "3px",
          padding: "3px 6px",
          borderRadius: "4px",
          border: "1px solid rgba(252,211,77,0.3)",
          background: "rgba(252,211,77,0.08)",
          color: "#fde68a",
          cursor: "pointer",
          fontSize: "11px",
        });
        btn.onclick = () => {
          void navigator.clipboard?.writeText(kw);
          btn.textContent = `✓ 已复制关键词：${kw}`;
        };
        sec.appendChild(btn);
      }
      panel.appendChild(sec);
    }

    if (skipped.length > 0) {
      const sec = document.createElement("div");
      sec.style.color = "#9aa4b2";
      sec.style.fontSize = "11px";
      sec.textContent = `已自动跳过非前后缀词条：${skipped.slice(0, 4).join("；")}`;
      panel.appendChild(sec);
    }
  }

  async function runPoe2HtcAutoFill(payload: HtcHandoffPayload, isRetry = false): Promise<void> {
    const retry = () => {
      void runPoe2HtcAutoFill(payload, true);
    };
    renderHtcFloatingStatus(
      payload,
      {
        baseSelected: true,
        usedNativePaste: false,
        matchedExisting: [],
        matchedTarget: [],
        unmatched: [],
        statusText: "正在等待 POE2HTC 词缀数据加载...",
      },
      retry
    );

    // 等待页面主 UI 与 Base 下拉选项加载完成
    for (let i = 0; i < 45; i++) {
      const baseSel = findLabeledControl<HTMLSelectElement>(/^base$/i, "select");
      const hasTabs = Array.from(document.querySelectorAll("button")).some((b) =>
        /^(Plan from scratch|I have an item)$/i.test((b.textContent || "").trim())
      );
      if (hasTabs && baseSel && baseSel.options.length > 0) break;
      await sleep(200);
    }

    // 若已通过 POE2HTC 原生 ?s= 协议载入，不再重复覆写 React 表单，仅展示状态与辅助操作
    if (payload.nativeS === 1 && !isRetry) {
      if (payload.mode === "have_item" && (!payload.existingMods || payload.existingMods.length === 0)) {
        await sleep(320);
        await ensurePasteAccordionOpen();
      }
      const unmatchedNative = payload.nativeUnmatched || [];
      const cta = payload.mode === "have_item" ? "Compute plan" : "Find plans";
      const nativeStatus =
        payload.mode === "have_item" && (!payload.existingMods || payload.existingMods.length === 0)
          ? `✅ 已通过 POE2HTC 原生协议载入 ${payload.baseClass} 与目标词缀！上方「Paste your item」已展开，可直接粘贴半成品后点「${cta}」。`
          : `✅ 已通过 POE2HTC 原生协议直接载入 ${payload.baseClass} 与词缀，可直接点「${cta}」计算最优路线！`;
      renderHtcFloatingStatus(
        payload,
        {
          baseSelected: true,
          usedNativePaste: false,
          matchedExisting: [],
          matchedTarget: [],
          unmatched: unmatchedNative,
          statusText: nativeStatus,
        },
        retry
      );
      return;
    }

    const targetTabLabel = payload.mode === "have_item" ? "I have an item" : "Plan from scratch";
    const tabBtn = Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find(
      (b) => (b.textContent || "").trim().toLowerCase() === targetTabLabel.toLowerCase()
    );
    if (tabBtn && tabBtn.getAttribute("aria-pressed") !== "true") {
      tabBtn.click();
      await sleep(260);
    }

    if (isRetry) {
      const resetBtn = Array.from(document.querySelectorAll<HTMLButtonElement>("button")).find(
        (b) => /^reset$/i.test((b.textContent || "").trim()) && !b.disabled
      );
      if (resetBtn) {
        resetBtn.click();
        await sleep(220);
      }
    }

    const matchedExisting: Array<{ text: string; tierLabel: string }> = [];
    const matchedTarget: Array<{ text: string; tierLabel: string }> = [];
    const unmatched: string[] = [];
    let baseSelected = false;
    let usedNativePaste = false;

    if (payload.mode === "have_item") {
      if (payload.rawExistingText) {
        const pasteRes = await tryNativePasteItem(payload.rawExistingText);
        if (pasteRes.ok) {
          usedNativePaste = true;
          baseSelected = true;
          await setHtcRunes(payload.runes);
        }
      }

      if (!usedNativePaste) {
        baseSelected = await selectHtcBaseClass(payload.baseClass);
        await selectHtcVariant(payload.variantHint, payload.baseType);
        await setHtcItemLevel(payload.ilvl);
        await setHtcRarity(payload.rarity);
        await setHtcRunes(payload.runes);

        if (payload.existingMods && payload.existingMods.length > 0) {
          const seenEx = new Set<string>();
          for (const mod of payload.existingMods) {
            const norm = normalizeHtcModTemplate(mod.t);
            if (!norm || seenEx.has(norm)) continue;
            seenEx.add(norm);

            const hit = findHaveItemAddButton(mod);
            if (!hit) {
              unmatched.push(mod.t);
              continue;
            }
            hit.btn.click();
            await sleep(140);

            const tierSel = findMatchingAriaControl<HTMLSelectElement>(
              "select[aria-label]",
              /^Tier this mod is rolled at:\s*/i,
              hit.cleanTemplate
            );
            const tierLabel = pickBestTierSelectOption(tierSel, mod.v, payload.minTierMode || "mid");

            if (mod.f) {
              const fracBtn = findMatchingAriaControl<HTMLButtonElement>(
                "button[aria-label]",
                /^Fractured \(locked on the item\):\s*/i,
                hit.cleanTemplate
              );
              if (fracBtn && fracBtn.getAttribute("aria-pressed") !== "true") {
                fracBtn.click();
                await sleep(50);
              }
            }
            if (mod.d) {
              const desecBtn = findMatchingAriaControl<HTMLButtonElement>(
                "button[aria-label]",
                /^Placed by a Desecration:\s*/i,
                hit.cleanTemplate
              );
              if (desecBtn && desecBtn.getAttribute("aria-pressed") !== "true") {
                desecBtn.click();
                await sleep(50);
              }
            }

            matchedExisting.push({ text: mod.t, tierLabel });
          }
        } else {
          // 未携带半成品词缀时，自动展开 Paste your item 文本框方便玩家直接 Ctrl+V
          await ensurePasteAccordionOpen();
        }
      }

      if (payload.targetMods && payload.targetMods.length > 0) {
        const targetRes = await fillHaveItemTargetMods(payload);
        matchedTarget.push(...targetRes.matched);
        unmatched.push(...targetRes.unmatched);
      }

      const totalFilled = matchedExisting.length + matchedTarget.length + (usedNativePaste ? 1 : 0);
      const statusText = !baseSelected
        ? `未能定位基底「${payload.baseClass}」，请点「🔄 重填」或点「📋 复制装备」后粘贴到 Paste your item。`
        : totalFilled > 0
        ? payload.targetMods.length > 0
          ? usedNativePaste
            ? `已自动导入半成品并勾选 ${matchedTarget.length} 条目标词缀，可直接点「Compute plan」计算！`
            : matchedExisting.length > 0
            ? `已填入 ${matchedExisting.length} 条现有词缀与 ${matchedTarget.length} 条目标词缀，可直接点「Compute plan」计算！`
            : `已切换至 ${payload.baseClass} 并勾选 ${matchedTarget.length} 条目标词缀；上方「Paste your item」已展开，可直接粘贴半成品后点「Compute plan」。`
          : "已自动填入半成品装备！可在下方选择「Quick currency check」或「Full plan to a target」。"
        : `已定位基底 ${payload.baseClass}（iLvl ${payload.ilvl}），可直接点「📋 复制装备」粘贴到上方「Paste your item」。`;

      renderHtcFloatingStatus(
        payload,
        { baseSelected, usedNativePaste, matchedExisting, matchedTarget, unmatched, statusText },
        retry
      );
      return;
    }

    // Plan from scratch 模式
    baseSelected = await selectHtcBaseClass(payload.baseClass);
    await selectHtcVariant(payload.variantHint, payload.baseType);
    await setHtcItemLevel(payload.ilvl);
    if (payload.budgetExalts && payload.budgetExalts > 0) {
      const budgetInp = findLabeledControl<HTMLInputElement>(/^budget/i, "input");
      if (budgetInp) setReactInputValue(budgetInp, String(payload.budgetExalts));
    }
    if (payload.baseCostExalts && payload.baseCostExalts > 0) {
      const baseCostInp = findLabeledControl<HTMLInputElement>(/^base cost/i, "input");
      if (baseCostInp) setReactInputValue(baseCostInp, String(payload.baseCostExalts));
    }
    await setHtcRunes(payload.runes);

    const seenModNorm = new Set<string>();
    for (const mod of payload.targetMods || []) {
      const norm = normalizeHtcModTemplate(mod.t);
      if (!norm || seenModNorm.has(norm)) continue;
      seenModNorm.add(norm);

      const hit = findScratchModifierRow(mod);
      if (!hit) {
        unmatched.push(mod.t);
        continue;
      }

      const tierLabel = pickBestTierSelectOption(hit.tierSelect, mod.v, payload.minTierMode || "mid");
      await sleep(60);
      hit.addBtn.click();
      await sleep(140);

      if (mod.f) {
        const fracBtn = findMatchingAriaControl<HTMLButtonElement>(
          "button[aria-label]",
          /^Fractured on the base:\s*/i,
          hit.cleanTemplate
        );
        if (fracBtn && fracBtn.getAttribute("aria-pressed") !== "true") {
          fracBtn.click();
          await sleep(50);
        }
      }

      matchedTarget.push({ text: mod.t, tierLabel });
    }

    const finalStatus = !baseSelected
      ? `未能定位基底「${payload.baseClass}」，请等待页面加载完成后点「🔄 重填」。`
      : matchedTarget.length > 0
      ? `自动填入完成！已定位 ${payload.baseClass} 并勾选 ${matchedTarget.length} 条目标词缀，可直接点「Find plans」计算最优路线。`
      : `已切换基底 ${payload.baseClass}（iLvl ${payload.ilvl}），部分词缀未自动命中，可点下方词条复制搜索关键词。`;

    renderHtcFloatingStatus(
      payload,
      { baseSelected, usedNativePaste: false, matchedExisting: [], matchedTarget, unmatched, statusText: finalStatus },
      retry
    );
  }

  const host = window.location.hostname.toLowerCase();
  if (host === "poe2htc.com" || host === "www.poe2htc.com") {
    const payload = decodeHtcHandoffHash(window.location.hash);
    if (payload) {
      try {
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      } catch {
        // ignore history error
      }
      void runPoe2HtcAutoFill(payload);
    }
    return;
  }

  createFloatingButton();
})();
