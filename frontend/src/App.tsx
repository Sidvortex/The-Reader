import { useCallback, useEffect, useRef, useState } from "react";
import ExportMenu from "./ExportMenu";
import { useDismiss } from "./hooks";
import { api, getAi, setAi as saveAi, type AiSettings } from "./api";
import FileWorkspace from "./FileWorkspace";
import OpenFolder, { loadRecents, saveRecent } from "./OpenFolder";
import RepoWorkspace from "./RepoWorkspace";
import { parseRepoHash, repoHash, ROOT } from "./route";
import type { Analysis, Health, RepoIndex, RepoStatus } from "./types";

type Theme = "light" | "dark";
type Screen = { kind: "welcome" } | { kind: "file"; analysis: Analysis } | { kind: "repo"; id: string };

const EXAMPLES = ["psf/requests", "pallets/flask", "encode/httpx"];

function initialTheme(): Theme {
  try {
    const t = localStorage.getItem("the-reader-theme");
    if (t === "light" || t === "dark") return t;
  } catch { /* storage unavailable */ }
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Close a popover on Escape or a click outside it. */
const masked = (k: string) => (k.length > 12 ? `${k.slice(0, 4)}…${k.slice(-4)}` : "••••");

export default function App() {
  const [screen, setScreen] = useState<Screen>({ kind: "welcome" });
  const [repoIndex, setRepoIndex] = useState<RepoIndex | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ai, setAi] = useState(true);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [paste, setPaste] = useState("");
  const [dragging, setDragging] = useState(false);
  const [folderOpen, setFolderOpen] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);
  const [mine, setMine] = useState<AiSettings | null>(getAi);
  const [draft, setDraft] = useState<AiSettings>({ key: "", provider: "", model: "", baseUrl: "" });
  const [moreOpen, setMoreOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const popover = useRef<HTMLDivElement>(null);
  const keyPopover = useRef<HTMLDivElement>(null);
  useDismiss(folderOpen, popover, useCallback(() => setFolderOpen(false), []));
  useDismiss(keyOpen, keyPopover, useCallback(() => setKeyOpen(false), []));

  const publicMode = !!health?.public;
  // a website pays for AI with the visitor's own key, unless it offers some free explanations itself
  const aiPossible = !!health && (health.ai || !!mine);
  const aiOn = ai && aiPossible;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("the-reader-theme", theme); } catch { /* ignore */ }
  }, [theme]);

  const showRepo = useCallback((id: string) => {
    setScreen({ kind: "repo", id });
    setRepoIndex(null);
    setError(null);
    setFolderOpen(false);
  }, []);

  const showFile = useCallback((a: Analysis) => {
    if (location.hash) window.history.pushState(null, "", location.pathname);
    setScreen({ kind: "file", analysis: a });
    setRepoIndex(null);
    setError(null);
    document.title = `${a.file.name} — The Reader`;
  }, []);

  const goHome = useCallback(() => {
    if (location.hash) window.history.pushState(null, "", location.pathname);
    setScreen({ kind: "welcome" });
    setRepoIndex(null);
    document.title = "The Reader";
  }, []);

  /** A project link from a reload, a bookmark or someone else. The server reads GitHub projects again by
   *  itself; a folder opened before a restart is reopened from the recent list. */
  const resumeRepo = useCallback(async (id: string) => {
    try {
      await api.repoStatus(id);
      showRepo(id);
    } catch {
      const r = loadRecents().find((x) => x.id === id);
      if (r) {
        try { showRepo((await api.repoOpen(r.path)).id); return; } catch { /* fall through */ }
      }
      window.history.replaceState(null, "", location.pathname);
      setError("That project isn't open any more. Open it again.");
    }
  }, [showRepo]);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setError("The Reader can't be reached right now. Reload the page, or try again in a moment."));
    const fromHash = parseRepoHash(location.hash);
    api.initial()
      .then((init) => {
        if (fromHash) return resumeRepo(fromHash.id);
        if (init?.kind === "repo") {
          if (init.path) window.history.replaceState({ readerDepth: 0 }, "", repoHash(init.id, { ...ROOT, path: init.path }));
          return showRepo(init.id);
        }
        if (init?.kind === "file") return showFile(init.analysis);
      })
      .catch(() => { if (fromHash) resumeRepo(fromHash.id); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Back / Forward between the start page, single files and projects
  useEffect(() => {
    const onPop = () => {
      const p = parseRepoHash(location.hash);
      setScreen((s) => {
        if (p && (s.kind !== "repo" || s.id !== p.id)) return { kind: "repo", id: p.id };
        if (!p && s.kind === "repo") return { kind: "welcome" };
        return s;
      });
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  /** A folder path or a GitHub link (on a website, only links). */
  const openProject = async (text: string) => {
    setBusy(true);
    setError(null);
    try {
      const st: RepoStatus = await api.repoOpen(text);
      saveRecent({ id: st.id, path: st.root, name: st.source ? `${st.source.owner}/${st.source.repo}` : st.name });
      // a link into the project that's already open just moves there, and Back returns
      const same = screen.kind === "repo" && screen.id === st.id;
      const depth = same ? ((window.history.state?.readerDepth as number) ?? 0) + 1 : 0;
      window.history.pushState({ readerDepth: depth }, "", repoHash(st.id, { ...ROOT, path: st.path ?? "" }));
      if (same) {
        window.dispatchEvent(new PopStateEvent("popstate", { state: window.history.state }));
        setFolderOpen(false);
      } else {
        showRepo(st.id);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const analyze = async (name: string, source: string) => {
    setBusy(true);
    setError(null);
    try { showFile(await api.analyze(name, source)); }
    catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  };

  const openFile = async (f: File | undefined) => {
    if (!f) return;
    if (f.size > 2_000_000) return setError(`${f.name} is larger than 2 MB.`);
    analyze(f.name, await f.text());
  };

  const saveKey = () => {
    saveAi(draft);
    setMine(getAi());
    setDraft({ key: "", provider: "", model: "", baseUrl: "" });
    setAi(true);
    setKeyOpen(false);
  };
  const custom = draft.provider === "custom";
  const canSave = custom ? /^https?:\/\/\S+$/.test(draft.baseUrl.trim()) : !!draft.key.trim();
  const mineName = mine ? (health?.providers.find((p) => p.id === mine.provider)?.name ?? (mine.provider === "custom" ? "your AI server" : null)) : null;

  const dropAllowed = screen.kind !== "repo";
  const aiTitle = aiPossible
    ? mine ? "Explanations are written with your API key" : "Explanations are written by AI"
    : "Add an API key (the Add key button) to turn on AI explanations";

  return (
    <div
      className={`app${dragging ? " dragging" : ""}`}
      onDragOver={(e) => { if (!dropAllowed) return; e.preventDefault(); setDragging(true); }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false); }}
      onDrop={(e) => { if (!dropAllowed) return; e.preventDefault(); setDragging(false); openFile(e.dataTransfer.files[0]); }}
    >
      <header className="topbar">
        <button className="brand" onClick={goHome} title="Start page">
          <span className="brand-mark" aria-hidden><i /><i /><i /></span>
          <span>The Reader</span>
        </button>
        {screen.kind === "file" && (
          <div className="file-meta">
            <strong>{screen.analysis.file.name}</strong>
            <span>{screen.analysis.file.lines} lines</span>
            {screen.analysis.file.has_errors && <span className="warn" title="This file has mistakes in its code, so parts of it may be missing">code errors</span>}
          </div>
        )}
        {screen.kind === "repo" && repoIndex && (
          <div className="file-meta">
            <strong title={repoIndex.root}>{repoIndex.source ? `${repoIndex.source.owner}/${repoIndex.source.repo}` : repoIndex.name}</strong>
            <span>{repoIndex.stats.py_analyzed.toLocaleString()} Python files</span>
            {repoIndex.git?.branch && <span className="branch">{repoIndex.git.branch}</span>}
          </div>
        )}
        <div className="top-actions">
          <label className={`ai-toggle${aiPossible ? "" : " off"}`} title={aiTitle}>
            <input type="checkbox" checked={aiOn} disabled={!aiPossible} onChange={(e) => setAi(e.target.checked)} />
            <span className="switch" aria-hidden />
            AI explanations
          </label>
          <div className="popover-anchor" ref={keyPopover}>
            <button className={`ghost${mine ? " key-set" : ""}`} aria-expanded={keyOpen} onClick={() => setKeyOpen((o) => !o)}
              title={mine ? "Your API key is set" : "Add an API key for AI explanations"}>
              {mine ? "Key ✓" : "Add key"}
            </button>
            {keyOpen && (
              <div className="popover key-popover" role="dialog" aria-label="API key for AI explanations">
                <p>
                  To get AI explanations, paste an API key from an AI service. It stays in this browser and is only
                  used to write explanations. Keys from OpenAI, Google Gemini, Anthropic, Groq, Mistral, DeepSeek,
                  xAI, OpenRouter and similar services work.
                </p>
                {mine ? (
                  <div className="key-row">
                    <code>{mine.key ? masked(mine.key) : "your AI server"}</code>
                    {mineName && <span className="key-for">{mineName}</span>}
                    <button className="ghost small" onClick={() => { saveAi(null); setMine(null); }}>Remove</button>
                  </div>
                ) : (
                  <form className="key-form" onSubmit={(e) => { e.preventDefault(); if (canSave) saveKey(); }}>
                    <div className="key-row">
                      <label htmlFor="ai-key" className="visually-hidden">API key</label>
                      <input id="ai-key" type="password" value={draft.key} onChange={(e) => setDraft({ ...draft, key: e.target.value })}
                        placeholder={custom ? "API key (if it needs one)" : "Paste your API key"} autoComplete="off" spellCheck={false} autoFocus />
                      <button className="primary" type="submit" disabled={!canSave}>Save</button>
                    </div>
                    <button type="button" className="link-btn" aria-expanded={moreOpen} onClick={() => setMoreOpen((o) => !o)}>
                      {moreOpen ? "Hide options" : "Options"}
                    </button>
                    {moreOpen && (
                      <div className="key-more">
                        <label>
                          Service
                          <select value={draft.provider} onChange={(e) => setDraft({ ...draft, provider: e.target.value })}>
                            <option value="">Detect from the key</option>
                            {health?.providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                            {health?.custom_server && <option value="custom">My own AI server</option>}
                          </select>
                        </label>
                        {custom && (
                          <label>
                            Server address
                            <input value={draft.baseUrl} onChange={(e) => setDraft({ ...draft, baseUrl: e.target.value })}
                              placeholder="http://localhost:11434/v1" spellCheck={false} />
                          </label>
                        )}
                        <label>
                          <span>Model <span className="optional">(optional)</span></span>
                          <input value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })}
                            placeholder="Leave empty to pick one for you" spellCheck={false} />
                        </label>
                      </div>
                    )}
                  </form>
                )}
                <p className="hint">Everything else works without a key.</p>
              </div>
            )}
          </div>
          {screen.kind === "repo" && repoIndex && <ExportMenu key={repoIndex.id} index={repoIndex} />}
          <button className="ghost" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle dark mode">
            {theme === "dark" ? "☀" : "☾"}
          </button>
          {screen.kind !== "welcome" && (
            <div className="popover-anchor" ref={popover}>
              <button className="ghost" aria-expanded={folderOpen} onClick={() => setFolderOpen((o) => !o)}>
                {publicMode ? "Open link" : "Open project"}
              </button>
              {folderOpen && (
                <div className="popover" role="dialog" aria-label={publicMode ? "Open a GitHub link" : "Open a project"}>
                  <OpenFolder onOpen={openProject} busy={busy} autoFocus compact publicMode={publicMode} />
                </div>
              )}
            </div>
          )}
          <button className={screen.kind === "welcome" ? "ghost" : "primary"} onClick={() => fileInput.current?.click()}>Open file</button>
          <input ref={fileInput} type="file" accept=".py,.pyw,.pyi" hidden onChange={(e) => { openFile(e.target.files?.[0]); e.target.value = ""; }} />
        </div>
      </header>

      {error && <div className="banner" role="alert">{error}<button onClick={() => setError(null)} aria-label="Dismiss">✕</button></div>}

      {screen.kind === "repo" ? (
        <RepoWorkspace key={screen.id} id={screen.id} ai={aiOn} onIndex={setRepoIndex}
          onFail={(m) => { goHome(); setError(m); }} />
      ) : screen.kind === "file" ? (
        <FileWorkspace analysis={screen.analysis} ai={aiOn} />
      ) : (
        <main className="welcome">
          <div className="welcome-inner">
            <h1>Read code the way you'd explain it to someone.</h1>
            <p className="lede">
              {publicMode
                ? "Paste a link to a public GitHub repository to get an overview, a map of how its folders and files depend on each other, and a flowchart for every function. Or read a single Python file."
                : "Open a project folder or a GitHub link to get an overview, a map of how its folders and files depend on each other, and a flowchart for every function. Or read a single Python file."}
            </p>
            <section className="start-block">
              <h2>{publicMode ? "Read a project on GitHub" : "Read a project"}</h2>
              <OpenFolder onOpen={openProject} busy={busy} publicMode={publicMode} />
              {publicMode ? (
                <p className="hint">
                  Try{" "}
                  {EXAMPLES.map((x, i) => (
                    <span key={x}>
                      <button className="link-btn" disabled={busy} onClick={() => openProject(`https://github.com/${x}`)}>{x}</button>
                      {i < EXAMPLES.length - 1 ? ", " : ". "}
                    </span>
                  ))}
                  Python projects work best. The first read of a big one takes a minute or two.
                </p>
              ) : null}
            </section>
            <section className="start-block">
              <h2>Read one file</h2>
              <div className="drop" onClick={() => fileInput.current?.click()} role="button" tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && fileInput.current?.click()}>
                <strong>{busy ? "Reading…" : "Drop a .py file here"}</strong>
                <span>or click to choose one</span>
              </div>
              <div className="paste">
                <textarea value={paste} onChange={(e) => setPaste(e.target.value)} placeholder="…or paste code here" spellCheck={false} rows={5} />
                <div className="paste-actions">
                  <button className="primary" disabled={!paste.trim() || busy} onClick={() => analyze("pasted.py", paste)}>Read pasted code</button>
                  <button className="ghost" disabled={busy} onClick={() => api.example().then(showFile).catch((e) => setError(e.message))}>Try an example file</button>
                </div>
              </div>
            </section>
            <dl className="keys">
              <div><dt>j / k</dt><dd>next or previous step in reading order</dd></div>
              <div><dt>m / f</dt><dd>map or flowchart</dd></div>
              <div><dt>[</dt><dd>back</dd></div>
              <div><dt>u</dt><dd>up a level</dd></div>
              <div><dt>/</dt><dd>find a file or function</dd></div>
            </dl>
            <footer className="credit">
              The Reader by <a href="https://github.com/Sidvortex" target="_blank" rel="noreferrer">Ravada Siddharth (Sidvortex)</a>.
              Free for personal, study and research use; commercial use needs written permission.{" "}
              <a href="LICENSE.md" target="_blank" rel="noreferrer">License</a>
              {" · "}
              <a href="THIRD_PARTY_NOTICES.md" target="_blank" rel="noreferrer">Third-party notices</a>
            </footer>
          </div>
        </main>
      )}
      {dragging && <div className="drop-overlay"><span>Drop to read this file</span></div>}
    </div>
  );
}
