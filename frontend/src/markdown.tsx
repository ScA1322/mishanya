// Упрощённый рендер Markdown Discord для предпросмотра. Строит React-элементы — без innerHTML.
import { createContext, useContext, type ReactNode } from "react";

/** Подставляет имена ролей и каналов вместо <@&id> / <#id> (если предпросмотр знает ресурсы сервера). */
export type MentionResolver = (raw: string) => { label: string; color?: string | null } | null;
export const MentionCtx = createContext<MentionResolver | null>(null);

function Mention({ raw }: { raw: string }) {
  const resolve = useContext(MentionCtx);
  const r = resolve?.(raw);
  if (!r) return <span className="md-mention">{raw}</span>;
  const style = r.color ? { color: r.color, background: `${r.color}26` } : undefined;
  return <span className="md-mention" style={style}>{r.label}</span>;
}

type Rule = { re: RegExp; render: (m: RegExpExecArray, key: string) => ReactNode };

const RULES: Rule[] = [
  { re: /`([^`\n]+)`/, render: (m, k) => <code key={k} className="md-code">{m[1]}</code> },
  { re: /\*\*([\s\S]+?)\*\*/, render: (m, k) => <strong key={k}>{inline(m[1], k)}</strong> },
  { re: /__([\s\S]+?)__/, render: (m, k) => <u key={k}>{inline(m[1], k)}</u> },
  { re: /~~([\s\S]+?)~~/, render: (m, k) => <s key={k}>{inline(m[1], k)}</s> },
  { re: /\|\|([\s\S]+?)\|\|/, render: (m, k) => <span key={k} className="md-spoiler" title="Спойлер">{inline(m[1], k)}</span> },
  { re: /\*([^*\n]+?)\*|(?<![\w])_([^_\n]+?)_(?![\w])/, render: (m, k) => <em key={k}>{inline(m[1] ?? m[2], k)}</em> },
  { re: /<(a?):[\w~]{2,32}:(\d{15,22})>/, render: (m, k) => (
    <img key={k} className="md-emoji" alt="" src={`https://cdn.discordapp.com/emojis/${m[2]}.${m[1] ? "gif" : "png"}?size=48`} />) },
  { re: /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/, render: (m, k) => <span key={k} className="md-link" title={m[2]}>{inline(m[1], k)}</span> },
  { re: /<@[!&]?\d+>|<#\d+>|@everyone|@here|@[\p{L}\p{N}_.]+|#[\p{L}\p{N}_-]+/u, render: (m, k) => <Mention key={k} raw={m[0]} /> },
  { re: /https?:\/\/[^\s<]+[^\s<.,:;"')\]]/, render: (m, k) => <span key={k} className="md-link">{m[0]}</span> },
];

function inline(text: string, prefix = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let rest = text;
  let n = 0;
  while (rest) {
    let best: { m: RegExpExecArray; rule: Rule } | null = null;
    for (const rule of RULES) {
      const m = rule.re.exec(rest);
      if (m && (!best || m.index < best.m.index)) best = { m, rule };
    }
    if (!best) {
      out.push(rest);
      break;
    }
    if (best.m.index > 0) out.push(rest.slice(0, best.m.index));
    out.push(best.rule.render(best.m, `${prefix}-${n++}`));
    rest = rest.slice(best.m.index + best.m[0].length);
  }
  return out;
}

function lines(text: string, prefix: string): ReactNode[] {
  return text.split("\n").map((line, i) => {
    const key = `${prefix}-${i}`;
    const h = /^(#{1,3}) (.+)$/.exec(line);
    if (h) return <div key={key} className={`md-h md-h${h[1].length}`}>{inline(h[2], key)}</div>;
    if (line.startsWith("> ")) return <div key={key} className="md-quote">{inline(line.slice(2), key)}</div>;
    if (/^[-*] /.test(line)) return <div key={key} className="md-li">• {inline(line.slice(2), key)}</div>;
    return <div key={key} className="md-line">{line ? inline(line, key) : " "}</div>;
  });
}

export function Markdown({ text }: { text: string }) {
  const parts = text.split(/```(?:[a-z0-9]*\n)?([\s\S]*?)```/i);
  return (
    <>
      {parts.map((part, i) => {
        if (i % 2 === 1) return <pre key={i} className="md-pre">{part}</pre>;
        const clean = part.replace(/^\n|\n$/g, "");
        return clean ? <div key={i}>{lines(clean, `b${i}`)}</div> : null;
      })}
    </>
  );
}
