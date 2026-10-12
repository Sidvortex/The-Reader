import type { Analysis, Explanation, Health, Initial, RepoIndex, RepoLevel, RepoStatus } from "./types";

// A visitor's own Anthropic API key (public mode). Kept in this browser and sent only with explanation requests.
const KEY = "the-reader-anthropic-key";
let aiKey: string | null = null;
try { aiKey = localStorage.getItem(KEY); } catch { /* storage unavailable */ }

export function getAiKey(): string | null {
  return aiKey;
}

export function setAiKey(key: string | null): void {
  aiKey = key?.trim() || null;
  try {
    if (aiKey) localStorage.setItem(KEY, aiKey);
    else localStorage.removeItem(KEY);
  } catch { /* the key then lasts until the page is closed */ }
}

async function req<T>(url: string, body?: unknown, withKey = false): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (withKey && aiKey) headers["X-Anthropic-Key"] = aiKey;
  const r = await fetch(url, body === undefined ? { headers } : { method: "POST", headers, body: JSON.stringify(body) });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { msg = (await r.json()).detail ?? msg; } catch { /* keep status text */ }
    throw Object.assign(new Error(msg), { status: r.status });
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
