// Markdown.tsx — tiny, dependency-free markdown renderer tuned for LLM output.
// Supports: #/##/### headings, - / * / 1. lists, ``` fenced code, > blockquotes,
// --- rules, paragraphs, and inline **bold**, *italic*, `code`, [links](url).
import { Fragment, type ReactNode } from "react";

// ── inline formatting ──────────────────────────────────────────────────────
function renderInline(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // order matters: code first (so ** inside code is literal), then link, bold, italic
  const re = /(`[^`]+`)|(\[[^\]]+\]\([^)]+\))|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(__[^_]+__)|(_[^_]+_)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(<Fragment key={`${keyBase}-t${i}`}>{text.slice(last, m.index)}</Fragment>);
    const tok = m[0];
    const k = `${keyBase}-m${i}`;
    if (tok.startsWith("`")) {
      nodes.push(
        <code key={k} className="rounded bg-app px-1.5 py-0.5 font-mono text-[0.85em] text-lava-warm">
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (tok.startsWith("[")) {
      const mm = /\[([^\]]+)\]\(([^)]+)\)/.exec(tok)!;
      nodes.push(
        <a key={k} href={mm[2]} target="_blank" rel="noreferrer" className="text-brand underline underline-offset-2 hover:text-brand-hover">
          {mm[1]}
        </a>,
      );
    } else if (tok.startsWith("**") || tok.startsWith("__")) {
      nodes.push(
        <strong key={k} className="font-semibold text-text-primary">
          {tok.slice(2, -2)}
        </strong>,
      );
    } else {
      nodes.push(
        <em key={k} className="italic">
          {tok.slice(1, -1)}
        </em>,
      );
    }
    last = m.index + tok.length;
    i++;
  }
  if (last < text.length) nodes.push(<Fragment key={`${keyBase}-tend`}>{text.slice(last)}</Fragment>);
  return nodes;
}

// ── block parsing ────────────────────────────────────────────────────────
export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  const lines = (text || "").replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  const flushList = (items: string[], ordered: boolean) => {
    if (!items.length) return;
    const Tag = ordered ? "ol" : "ul";
    blocks.push(
      <Tag
        key={`b${key++}`}
        className={`my-2 space-y-1 pl-5 text-[13px] leading-relaxed text-text-secondary ${ordered ? "list-decimal" : "list-disc"}`}
      >
        {items.map((it, idx) => (
          <li key={idx} className="pl-1 marker:text-text-disabled">
            {renderInline(it, `l${key}-${idx}`)}
          </li>
        ))}
      </Tag>,
    );
  };

  while (i < lines.length) {
    const line = lines[i];

    // fenced code block
    if (/^```/.test(line.trim())) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i].trim())) {
        buf.push(lines[i]);
        i++;
      }
      i++; // skip closing fence
      blocks.push(
        <pre key={`b${key++}`} className="my-3 overflow-x-auto rounded-lg border border-line bg-app p-3 font-mono text-[12px] leading-relaxed text-text-primary">
          <code>{buf.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    // blank line
    if (line.trim() === "") {
      i++;
      continue;
    }

    // horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line.trim())) {
      blocks.push(<hr key={`b${key++}`} className="my-4 border-line" />);
      i++;
      continue;
    }

    // heading
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length;
      const sizes = ["text-lg", "text-base", "text-sm", "text-sm"];
      blocks.push(
        <div key={`b${key++}`} className={`mt-4 mb-1.5 font-semibold text-text-primary first:mt-0 ${sizes[level - 1]}`}>
          {renderInline(h[2], `h${key}`)}
        </div>,
      );
      i++;
      continue;
    }

    // blockquote
    if (/^>\s?/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^>\s?/, ""));
        i++;
      }
      blocks.push(
        <blockquote key={`b${key++}`} className="my-2 border-l-2 border-brand/50 pl-3 text-[13px] italic text-text-secondary">
          {renderInline(buf.join(" "), `q${key}`)}
        </blockquote>,
      );
      continue;
    }

    // unordered list
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
        i++;
      }
      flushList(items, false);
      continue;
    }

    // ordered list
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ""));
        i++;
      }
      flushList(items, true);
      continue;
    }

    // paragraph (gather consecutive non-blank, non-special lines)
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^```/.test(lines[i].trim()) &&
      !/^(#{1,4})\s+/.test(lines[i]) &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+[.)]\s+/.test(lines[i]) &&
      !/^>\s?/.test(lines[i])
    ) {
      para.push(lines[i]);
      i++;
    }
    blocks.push(
      <p key={`b${key++}`} className="my-2 text-[13px] leading-relaxed text-text-secondary first:mt-0">
        {renderInline(para.join(" "), `p${key}`)}
      </p>,
    );
  }

  return <div className={className}>{blocks}</div>;
}
