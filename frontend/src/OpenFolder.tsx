import { useState } from "react";

export type Recent = { id: string; path: string; name: string; at: number };
const KEY = "the-reader-recent";

export function loadRecents(): Recent[] {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(v) ? v.filter((r) => r && typeof r.path === "string") : [];
  } catch {
    return [];
  }
}

export function saveRecent(r: Omit<Recent, "at">): void {
  try {
    const list = [{ ...r, at: Date.now() }, ...loadRecents().filter((x) => x.path !== r.path)].slice(0, 8);
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch { /* storage unavailable: recents are a convenience */ }
}

type Props = { onOpen: (path: string) => void; busy?: boolean; autoFocus?: boolean; compact?: boolean };

/** Type or paste a folder path; recent projects are one click away. */
export default function OpenFolder({ onOpen, busy, autoFocus, compact }: Props) {
  const [path, setPath] = useState("");
  const recents = loadRecents();
  return (
    <div className={`open-folder${compact ? " compact" : ""}`}>
      <form onSubmit={(e) => { e.preventDefault(); if (path.trim()) onOpen(path.trim()); }}>
        <label htmlFor={compact ? "folder-path-top" : "folder-path"} className="visually-hidden">Project folder</label>
        <input id={compact ? "folder-path-top" : "folder-path"} value={path} onChange={(e) => setPath(e.target.value)}
          placeholder="~/Documents/github/my-project" spellCheck={false} autoComplete="off" autoFocus={autoFocus} />
        <button className="primary" type="submit" disabled={!path.trim() || busy}>{busy ? "Opening…" : "Open folder"}</button>
      </form>
      {recents.length > 0 && (
        <ul className="recents" aria-label="Recent projects">
          {recents.map((r) => (
            <li key={r.path}>
              <button onClick={() => onOpen(r.path)} title={r.path}>
                <span className="r-name">{r.name}</span>
                <span className="r-path">{r.path}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
