export type InlineToken =
  | { type: "text"; text: string }
  | { type: "bold"; children: InlineToken[] }
  | { type: "italic"; children: InlineToken[] }
  | { type: "strike"; children: InlineToken[] }
  | { type: "code"; text: string }
  | { type: "link"; label: string; href: string };

export type MarkdownBlock =
  | { type: "heading"; level: 1 | 2 | 3 | 4; tokens: InlineToken[] }
  | { type: "paragraph"; tokens: InlineToken[] }
  | { type: "list"; ordered: boolean; items: InlineToken[][] }
  | { type: "blockquote"; lines: InlineToken[][] }
  | { type: "code"; lang: string; code: string }
  | { type: "table"; headers: InlineToken[][]; rows: InlineToken[][][] }
  | { type: "hr" };

const SAFE_URL_RE = /^https?:\/\/[^\s)]+$/i;

export function parseInlineTokens(input: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  let i = 0;
  let buf = "";

  const flush = () => {
    if (buf) {
      tokens.push({ type: "text", text: buf });
      buf = "";
    }
  };

  while (i < input.length) {
    // Inline code: `...`
    if (input[i] === "`") {
      const end = input.indexOf("`", i + 1);
      if (end > i + 1) {
        flush();
        tokens.push({ type: "code", text: input.slice(i + 1, end) });
        i = end + 1;
        continue;
      }
    }

    // Link: [label](url)
    if (input[i] === "[") {
      const closeBracket = input.indexOf("]", i + 1);
      if (closeBracket > i + 1 && input[closeBracket + 1] === "(") {
        const closeParen = input.indexOf(")", closeBracket + 2);
        if (closeParen > closeBracket + 2) {
          const label = input.slice(i + 1, closeBracket);
          const href = input.slice(closeBracket + 2, closeParen).trim();
          if (SAFE_URL_RE.test(href)) {
            flush();
            tokens.push({ type: "link", label, href });
            i = closeParen + 1;
            continue;
          }
        }
      }
    }

    // Bold: **...**
    if (input[i] === "*" && input[i + 1] === "*") {
      const end = input.indexOf("**", i + 2);
      if (end > i + 2) {
        flush();
        tokens.push({ type: "bold", children: parseInlineTokens(input.slice(i + 2, end)) });
        i = end + 2;
        continue;
      }
    }

    // Strikethrough: ~~...~~
    if (input[i] === "~" && input[i + 1] === "~") {
      const end = input.indexOf("~~", i + 2);
      if (end > i + 2) {
        flush();
        tokens.push({ type: "strike", children: parseInlineTokens(input.slice(i + 2, end)) });
        i = end + 2;
        continue;
      }
    }

    // Italic: *...* (not followed by space or *)
    if (input[i] === "*" && input[i + 1] !== "*" && input[i + 1] !== " ") {
      const end = input.indexOf("*", i + 1);
      if (end > i + 1 && input[end - 1] !== " ") {
        flush();
        tokens.push({ type: "italic", children: parseInlineTokens(input.slice(i + 1, end)) });
        i = end + 1;
        continue;
      }
    }

    buf += input[i];
    i += 1;
  }

  flush();
  return tokens;
}

function splitTableCells(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((c) => c.trim());
}

function isTableSeparator(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.includes("|") && !trimmed.includes("-")) return false;
  const cells = splitTableCells(trimmed);
  return cells.length >= 2 && cells.every((c) => /^:?-{2,}:?$/.test(c));
}

function isBlockStart(lines: string[], idx: number): boolean {
  const line = lines[idx];
  if (!line.trim()) return true;
  if (/^```/.test(line.trim())) return true;
  if (/^#{1,4}\s+/.test(line)) return true;
  if (/^(\*{3,}|-{3,}|_{3,})\s*$/.test(line.trim())) return true;
  if (/^\s*>\s?/.test(line)) return true;
  if (/^\s*[-*•]\s+/.test(line)) return true;
  if (/^\s*\d+[.)]\s+/.test(line)) return true;
  if (line.includes("|") && idx + 1 < lines.length && isTableSeparator(lines[idx + 1])) return true;
  return false;
}

export function parseMarkdownBlocks(raw: string): MarkdownBlock[] {
  if (!raw) return [];
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  const blocks: MarkdownBlock[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i += 1;
      continue;
    }

    // Fenced code block
    const fenceMatch = trimmed.match(/^```(\w*)/);
    if (fenceMatch) {
      const lang = fenceMatch[1] || "";
      const codeLines: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i]);
        i += 1;
      }
      if (i < lines.length && lines[i].trim().startsWith("```")) {
        i += 1;
      }
      blocks.push({ type: "code", lang, code: codeLines.join("\n") });
      continue;
    }

    // Horizontal rule
    if (/^(\*{3,}|-{3,}|_{3,})\s*$/.test(trimmed)) {
      blocks.push({ type: "hr" });
      i += 1;
      continue;
    }

    // Heading
    const headingMatch = line.match(/^(#{1,4})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length as 1 | 2 | 3 | 4;
      blocks.push({ type: "heading", level, tokens: parseInlineTokens(headingMatch[2].trim()) });
      i += 1;
      continue;
    }

    // Table
    if (line.includes("|") && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const headerCells = splitTableCells(line).map((c) => parseInlineTokens(c));
      const rows: InlineToken[][][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        const rowCells = splitTableCells(lines[i]).map((c) => parseInlineTokens(c));
        rows.push(rowCells);
        i += 1;
      }
      blocks.push({ type: "table", headers: headerCells, rows });
      continue;
    }

    // Blockquote
    if (/^\s*>\s?/.test(line)) {
      const quoteLines: InlineToken[][] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quoteLines.push(parseInlineTokens(lines[i].replace(/^\s*>\s?/, "")));
        i += 1;
      }
      blocks.push({ type: "blockquote", lines: quoteLines });
      continue;
    }

    // Unordered list
    if (/^\s*[-*•]\s+/.test(line)) {
      const items: InlineToken[][] = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) {
        items.push(parseInlineTokens(lines[i].replace(/^\s*[-*•]\s+/, "")));
        i += 1;
      }
      blocks.push({ type: "list", ordered: false, items });
      continue;
    }

    // Ordered list
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: InlineToken[][] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(parseInlineTokens(lines[i].replace(/^\s*\d+[.)]\s+/, "")));
        i += 1;
      }
      blocks.push({ type: "list", ordered: true, items });
      continue;
    }

    // Paragraph: merge consecutive plain lines
    const paraLines: string[] = [line];
    i += 1;
    while (i < lines.length && !isBlockStart(lines, i)) {
      paraLines.push(lines[i]);
      i += 1;
    }
    blocks.push({ type: "paragraph", tokens: parseInlineTokens(paraLines.join("\n")) });
  }

  return blocks;
}
