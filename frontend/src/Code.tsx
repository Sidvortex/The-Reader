import { memo, useEffect, useMemo, useRef, useState } from "react";
import { createHighlighterCore, type HighlighterCore, type ThemedToken } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";
import type { Analysis, Call } from "./types";

let hl: Promise<HighlighterCore> | null = null;
function highlighter() {
  hl ??= createHighlighterCore({
    themes: [import("@shikijs/themes/github-light-default"), import("@shikijs/themes/github-dark-dimmed")],
    langs: [import("@shikijs/langs/python")],
    engine: createJavaScriptRegexEngine(),
  });
  return hl;
}

// each language's grammar is fetched the first time a file in it is shown
const GRAMMARS: Record<string, () => Promise<unknown>> = {
  javascript: () => import("@shikijs/langs/javascript"),
  jsx: () => import("@shikijs/langs/jsx"),
  typescript: () => import("@shikijs/langs/typescript"),
  tsx: () => import("@shikijs/langs/tsx"),
  vue: () => import("@shikijs/langs/vue"),
  svelte: () => import("@shikijs/langs/svelte"),
  html: () => import("@shikijs/langs/html"),
  css: () => import("@shikijs/langs/css"),
};
const loading = new Map<string, Promise<void>>();

async function withLang(lang: string): Promise<[HighlighterCore, string]> {
  const h = await highlighter();
  if (lang === "python" || !GRAMMARS[lang]) return [h, GRAMMARS[lang] ? lang : "python"];
  if (!loading.has(lang)) {
    loading.set(lang, GRAMMARS[lang]().then((m: any) => h.loadLanguage(m.default ?? m)));
  }
  try {
    await loading.get(lang);
    return [h, lang];
  } catch {
    return [h, "text"];  // the code still shows, just without colours
  }
}

function useTokens(source: string, lang: string) {
  const [tokens, setTokens] = useState<ThemedToken[][] | null>(null);
  useEffect(() => {
    let live = true;
    setTokens(null);
    withLang(lang).then(([h, l]) => {
      if (!live) return;
      const r = h.codeToTokens(source, {
        lang: l as any,
        themes: { light: "github-light-default", dark: "github-dark-dimmed" },
        defaultColor: "light",
      });
      setTokens(r.tokens);
    });
    return () => { live = false; };
  }, [source, lang]);
  return tokens;
}

type Props = {
  analysis: Analysis;
  from: number;
  to: number;
  highlight: [number, number] | null;
  onFollow: (target: string) => void;
  onFollowExternal?: (file: string, symbol: string) => void;
};

/** Syntax-highlighted code where every call into this file is a link you can follow. */
export default memo(function Code({ analysis, from, to, highlight, onFollow, onFollowExternal }: Props) {
  const tokens = useTokens(analysis.source, analysis.file.lang ?? "python");
  const plain = useMemo(() => analysis.source.split("\n"), [analysis.source]);
  const callsByLine = useMemo(() => {
    const m = new Map<number, Call[]>();
    for (const s of analysis.symbols) for (const c of s.calls) {
      if (!c.target && !(c.xfile && onFollowExternal)) continue;
      (m.get(c.line) ?? m.set(c.line, []).get(c.line)!).push(c);
    }
    return m;
  }, [analysis, onFollowExternal]);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!highlight || !box.current) return;
    box.current.querySelector(`[data-line="${highlight[0]}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [highlight]);
  useEffect(() => { box.current?.scrollTo({ top: 0 }); }, [from]);

  const rows = [];
  for (let ln = from; ln <= to && ln <= plain.length; ln++) {
    const toks = tokens?.[ln - 1];
    const calls = callsByLine.get(ln) ?? [];
    let col = 0;
    const content = toks
      ? toks.map((t, i) => {
          const start = col;
          col += t.content.length;
          const style = t.htmlStyle as React.CSSProperties | undefined;
          const call = calls.find((c) => c.col === start && c.len === t.content.length);
          if (call?.xfile) {
            return (
              <button key={i} className="tok call ext" style={style} onClick={() => onFollowExternal!(call.xfile!, call.xsymbol!)}
                title={`Go to ${call.xsymbol} in ${call.xfile}`}>
                {t.content}
              </button>
            );
          }
          if (call) {
            return (
              <button key={i} className="tok call" style={style} onClick={() => onFollow(call.target!)} title={`Go to ${call.target}`}>
                {t.content}
              </button>
            );
          }
          return <span key={i} className="tok" style={style}>{t.content}</span>;
        })
      : plain[ln - 1];
    const hot = highlight && ln >= highlight[0] && ln <= highlight[1];
    rows.push(
      <div key={ln} data-line={ln} className={`cl${hot ? " hot" : ""}`}>
        <span className="ln">{ln}</span>
        <span className="lc">{content}{"\n"}</span>
      </div>,
    );
  }
  return <div className="code" ref={box}><pre>{rows}</pre></div>;
});
