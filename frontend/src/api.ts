import type { Analysis, Explanation, Health, Initial, RepoIndex, RepoLevel, RepoStatus } from "./types";

// The person's own AI settings. Kept in this browser and sent only with explanation requests.
export type AiSettings = { key: string; provider: string; model: string; baseUrl: string };
const STORE = "the-reader-ai";
const OLD_KEY = "the-reader-anthropic-key"; // from the first version, which only took one kind of key
let settings: AiSettings | null = null;
try {
  const raw = localStorage.getItem(STORE);
  if (raw) settings = JSON.parse(raw);
  else {
    const old = localStorage.getItem(OLD_KEY);
    if (old) settings = { key: old, provider: "", model: "", baseUrl: "" };
  }
} catch { /* storage unavailable */ }

export function getAi(): AiSettings | null {
  return settings;
}

export function setAi(next: AiSettings | null): void {
  const usable = next && (next.key.trim() || next.provider === "custom");
  settings = usable ? { ...next, key: next.key.trim(), model: next.model.trim(), baseUrl: next.baseUrl.trim() } : null;
  try {
    localStorage.removeItem(OLD_KEY);
    if (settings) localStorage.setItem(STORE, JSON.stringify(settings));
    else localStorage.removeItem(STORE);
  } catch { /* the settings then last until the page is closed */ }
}

async function req<T>(url: string, body?: unknown, withKey = false): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (withKey && settings) {
    if (settings.key) headers["X-AI-Key"] = settings.key;
    if (settings.provider) headers["X-AI-Provider"] = settings.provider;
    if (settings.model) headers["X-AI-Model"] = settings.model;
    if (settings.baseUrl) headers["X-AI-Base-URL"] = settings.baseUrl;
  }
  const r = await fetch(url, body === undefined ? { headers } : { method: "POST", headers, body: JSON.stringify(body) });
  if (!r.ok) {
    let msg = "Something went wrong. Try again.";
    try { msg = (await r.json()).detail ?? msg; } catch { /* keep the plain message */ }
    throw Object.assign(new Error(typeof msg === "string" ? msg : "Something went wrong. Try again."), { status: r.status });
  }
  return r.json();
}

const q = (params: Record<string, string>) => new URLSearchParams(params).toString();

export const api = {
  health: () => req<Health>("/api/health"),
  initial: () => req<Initial>("/api/initial"),
  example: () => req<Analysis>("/api/example"),
  analyze: (filename: string, source: string) => req<Analysis>("/api/analyze", { filename, source }),
  explain: (hash: string, symbol: string, ai: boolean) => req<Explanation>("/api/explain", { hash, symbol, ai }, ai),

  /** a folder on this computer, or a GitHub link */
  repoOpen: (path: string) => req<RepoStatus>("/api/repo/open", { path }),
  repoStatus: (id: string) => req<RepoStatus>(`/api/repo/status?${q({ id })}`),
  repoIndex: (id: string) => req<RepoIndex>(`/api/repo/index?${q({ id })}`),
  repoFile: (id: string, path: string) => req<Analysis>(`/api/repo/file?${q({ id, path })}`),
  repoExplain: (id: string, level: RepoLevel, path: string, symbol: string | null, ai: boolean) =>
    req<Explanation>("/api/repo/explain", { id, level, path, symbol, ai }, ai),
};
