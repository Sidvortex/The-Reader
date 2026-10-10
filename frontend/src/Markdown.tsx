import { Fragment, type ReactNode } from "react";

/** Small, safe markdown renderer for explanations: paragraphs, lists, headings, **bold**, _italic_, `code`.
 *  `name` spans that match a symbol in the file become links. */
function inline(text: string, link: (name: string) => ReactNode | null): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*|(?<![\w*])_[^_]+_(?!\w)|(?<![\w*])\*[^*\s][^*]*\*(?!\w))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("`")) {
      const name = t.slice(1, -1);
      out.push(link(name) ?? <code key={k++}>{name}</code>);
    } else if (t.startsWith("**")) out.push(<strong key={k++}>{inline(t.slice(2, -2), link)}</strong>);
    else out.push(<em key={k++}>{inline(t.slice(1, -1), link)}</em>);
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export default function Markdown({ md, link }: { md: string; link: (name: string) => ReactNode | null }) {
  const blocks: ReactNode[] = [];
  const lines = md.replace(/\r/g, "").split("\n");
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (line.startsWith("```")) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) body.push(lines[i++]);
      i++;
      blocks.push(<pre key={k++} className="md-pre">{body.join("\n")}</pre>);
      continue;
    }
    const h = /^(#{1,4})\s+(.*)/.exec(line);
    if (h) { blocks.push(<h4 key={k++}>{inline(h[2], link)}</h4>); i++; continue; }
    if (/^\s*([-*]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+[.)])\s+/.test(lines[i])) {
        let item = lines[i].replace(/^\s*([-*]|\d+[.)])\s+/, "");
        i++;
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*]|\d+[.)])\s+/.test(lines[i])) item += " " + lines[i++].trim();
        items.push(item);
      }
      const L = ordered ? "ol" : "ul";
      blocks.push(<L key={k++}>{items.map((it, j) => <li key={j}>{inline(it, link)}</li>)}</L>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*([-*]|\d+[.)])\s+)/.test(lines[i])) para.push(lines[i++]);
    blocks.push(<p key={k++}>{inline(para.join(" "), link)}</p>);
  }
  return <div className="md">{blocks.map((b, j) => <Fragment key={j}>{b}</Fragment>)}</div>;
}
