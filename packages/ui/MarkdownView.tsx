import React, { useMemo } from "react";
import { parseMarkdownBlocks, type InlineToken } from "@contracts";

export interface MarkdownViewProps {
  content: string;
  streaming?: boolean;
  compact?: boolean;
  tableClassName?: string;
}

function renderInline(tokens: InlineToken[], keyPrefix: string): React.ReactNode {
  return tokens.map((tok, idx) => {
    const key = `${keyPrefix}-${idx}`;
    switch (tok.type) {
      case "text":
        return tok.text.includes("\n")
          ? tok.text.split("\n").map((seg, si, arr) => (
              <React.Fragment key={`${key}-l-${si}`}>
                {seg}
                {si < arr.length - 1 && <br />}
              </React.Fragment>
            ))
          : tok.text;
      case "bold":
        return <strong key={key}>{renderInline(tok.children, key)}</strong>;
      case "italic":
        return <em key={key}>{renderInline(tok.children, key)}</em>;
      case "strike":
        return <del key={key}>{renderInline(tok.children, key)}</del>;
      case "code":
        return (
          <code key={key} className="md-inline-code">
            {tok.text}
          </code>
        );
      case "link":
        return (
          <a key={key} href={tok.href} target="_blank" rel="noopener noreferrer" className="md-link">
            {tok.label}
          </a>
        );
    }
  });
}

export const MarkdownView: React.FC<MarkdownViewProps> = ({
  content,
  streaming = false,
  compact = false,
  tableClassName = "table md-table",
}) => {
  const blocks = useMemo(() => parseMarkdownBlocks(content), [content]);

  return (
    <div className={`md-view ${compact ? "compact" : ""}`}>
      {blocks.map((block, idx) => {
        const key = `b-${idx}`;
        switch (block.type) {
          case "heading": {
            const Tag = `h${block.level}` as "h1" | "h2" | "h3" | "h4";
            return (
              <Tag key={key} className={`md-h md-h${block.level}`}>
                {renderInline(block.tokens, key)}
              </Tag>
            );
          }
          case "paragraph":
            return (
              <p key={key} className="md-p">
                {renderInline(block.tokens, key)}
              </p>
            );
          case "list": {
            const ListTag = block.ordered ? "ol" : "ul";
            return (
              <ListTag key={key} className="md-list">
                {block.items.map((item, ii) => (
                  <li key={`${key}-i-${ii}`}>{renderInline(item, `${key}-i-${ii}`)}</li>
                ))}
              </ListTag>
            );
          }
          case "blockquote":
            return (
              <blockquote key={key} className="md-quote">
                {block.lines.map((ln, li) => (
                  <div key={`${key}-q-${li}`}>{renderInline(ln, `${key}-q-${li}`)}</div>
                ))}
              </blockquote>
            );
          case "code":
            return (
              <pre key={key} className="md-pre">
                <code>{block.code}</code>
              </pre>
            );
          case "table":
            return (
              <div key={key} className="md-table-wrap">
                <table className={tableClassName}>
                  <thead>
                    <tr>
                      {block.headers.map((h, hi) => (
                        <th key={`${key}-th-${hi}`}>{renderInline(h, `${key}-th-${hi}`)}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {block.rows.map((row, ri) => (
                      <tr key={`${key}-tr-${ri}`}>
                        {row.map((cell, ci) => (
                          <td key={`${key}-td-${ri}-${ci}`}>{renderInline(cell, `${key}-td-${ri}-${ci}`)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case "hr":
            return <hr key={key} className="md-hr" />;
        }
      })}
      {streaming && <span className="md-cursor" aria-hidden="true" />}
    </div>
  );
};
