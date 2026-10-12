import { useState } from "react";

/** A project opened before: a folder path, or a GitHub link (https://github.com/owner/repo). */
export type Recent = { id: string; path: string; name: string; at: number };
const KEY = "the-reader-recent";

export const isGithubLink = (path: string) => /^https:\/\/github\.com\//.test(path);

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

type Props = {
  onOpen: (path: string) => void;
  busy?: boolean;
  autoFocus?: boolean;
  compact?: boolean;
  /** running as a website: only GitHub links */
  publicMode?: boolean;
};

/** Type or paste a folder path or a GitHub link; recent projects are one click away. */
export default function OpenFolder({ onOpen, busy, autoFocus, compact, publicMode }: Props) {
  const [path, setPath] = useState("");
  const recents = loadRecents().filter((r) => !publicMode || isGithubLink(r.path));
  const id = compact ? "project-path-top" : "project-path";
  return (
    <div className={`open-folder${compact ? " compact" : ""}`}>
      <form onSubmit={(e) => { e.preventDefault(); if (path.trim()) onOpen(path.trim()); }}>
        <label htmlFor={id} className="visually-hidden">{publicMode ? "GitHub link" : "Project folder or GitHub link"}</label>
        <input id={id} value={path} onChange={(e) => setPath(e.target.value)}
          placeholder={publicMode ? "https://github.com/owner/repo" : "~/code/my-project or a GitHub link"}
          spellCheck={false} autoComplete="off" autoFocus={autoFocus} />
        <button className="primary" type="submit" disabled={!path.trim() || busy}>{busy ? "Opening…" : publicMode ? "Read it" : "Open"}</button>
      </form>
      {recents.length > 0 && (
        <ul className="recents" aria-label="Recent projects">
          {recents.map((r) => (
            <li key={r.path}>
              <button onClick={() => onOpen(r.path)} title={r.path}>
                <span className="r-name">{r.name}</span>
                <span className="r-path">{r.path.replace(/^https:\/\//, "")}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
