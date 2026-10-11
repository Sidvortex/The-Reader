import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import FileWorkspace from "./FileWorkspace";
import OpenFolder, { loadRecents, saveRecent } from "./OpenFolder";
import RepoWorkspace from "./RepoWorkspace";
import { parseRepoHash, repoHash, ROOT } from "./route";
import type { Analysis, Health, RepoIndex } from "./types";

type Theme = "light" | "dark";
type Screen = { kind: "welcome" } | { kind: "file"; analysis: Analysis } | { kind: "repo"; id: string };

function initialTheme(): Theme {
  try {
    const t = localStorage.getItem("the-reader-theme");
    if (t === "light" || t === "dark") return t;
  } catch { /* storage unavailable */ }
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

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
  const fileInput = useRef<HTMLInputElement>(null);
  const popover = useRef<HTMLDivElement>(null);

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

  /** A project link from a reload or bookmark. If the server restarted, reopen it from the recent list. */
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
      setError("That project isn't open any more. Open its folder again.");
    }
  }, [showRepo]);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setError("Can't reach The Reader's server. Is the-reader still running in your terminal?"));
    const fromHash = parseRepoHash(location.hash);
    api.initial()
      .then((init) => {
        if (fromHash) return resumeRepo(fromHash.id);
        if (init?.kind === "repo") return showRepo(init.id);
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

  useEffect(() => {
    if (!folderOpen) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !popover.current?.contains(e.target as Node)) setFolderOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [folderOpen]);

  const openFolder = async (path: string) => {
    setBusy(true);
    setError(null);
    try {
      const st = await api.repoOpen(path);
      saveRecent({ id: st.id, path: st.root, name: st.name });
      window.history.pushState({ readerDepth: 0 }, "", repoHash(st.id, ROOT));
      showRepo(st.id);
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

  const dropAllowed = screen.kind !== "repo";

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
            {screen.analysis.file.has_errors && <span className="warn" title="The parser hit a syntax error; parts of the file may be missing">syntax errors</span>}
          </div>
        )}
        {screen.kind === "repo" && repoIndex && (
          <div className="file-meta">
            <strong title={repoIndex.root}>{repoIndex.name}</strong>
            <span>{repoIndex.stats.py_analyzed.toLocaleString()} Python files</span>
            {repoIndex.git?.branch && <span className="branch">{repoIndex.git.branch}</span>}
          </div>
        )}
        <div className="top-actions">
          <label className={`ai-toggle${health?.ai ? "" : " off"}`}
            title={health?.ai ? `Explanations by ${health.model}` : "Set ANTHROPIC_API_KEY before starting the-reader to turn on AI explanations"}>
            <input type="checkbox" checked={ai && !!health?.ai} disabled={!health?.ai} onChange={(e) => setAi(e.target.checked)} />
            <span className="switch" aria-hidden />
            AI explanations
          </label>
          <button className="ghost" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle dark mode">
            {theme === "dark" ? "☀" : "☾"}
          </button>
          {screen.kind !== "welcome" && (
            <div className="popover-anchor" ref={popover}>
              <button className="ghost" aria-expanded={folderOpen} onClick={() => setFolderOpen((o) => !o)}>Open folder</button>
              {folderOpen && (
                <div className="popover" role="dialog" aria-label="Open a project folder">
                  <OpenFolder onOpen={openFolder} busy={busy} autoFocus compact />
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
        <RepoWorkspace key={screen.id} id={screen.id} ai={ai && !!health?.ai} onIndex={setRepoIndex}
          onFail={(m) => { goHome(); setError(m); }} />
      ) : screen.kind === "file" ? (
        <FileWorkspace analysis={screen.analysis} ai={ai && !!health?.ai} />
      ) : (
        <main className="welcome">
          <div className="welcome-inner">
            <h1>Read code the way you'd explain it to someone.</h1>
            <p className="lede">Open a project folder to get an overview, a map of how its folders and files depend on each other, and a flowchart for every function. Or read a single Python file.</p>
            <section className="start-block">
              <h2>Read a project</h2>
              <OpenFolder onOpen={openFolder} busy={busy} />
              <p className="hint">Or from a terminal: <code>the-reader path/to/project</code></p>
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
              <a href="https://github.com/Sidvortex/The-Reader/blob/main/LICENSE.md" target="_blank" rel="noreferrer">License</a>
            </footer>
          </div>
        </main>
      )}
      {dragging && <div className="drop-overlay"><span>Drop to read this file</span></div>}
    </div>
  );
}
