import type { Analysis, Explanation, Health, Initial, RepoIndex, RepoLevel, RepoStatus } from "./types";

async function req<T>(url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, body === undefined ? undefined : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
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
  explain: (hash: string, symbol: string, ai: boolean) => req<Explanation>("/api/explain", { hash, symbol, ai }),

  repoOpen: (path: string) => req<RepoStatus>("/api/repo/open", { path }),
  repoStatus: (id: string) => req<RepoStatus>(`/api/repo/status?${q({ id })}`),
  repoIndex: (id: string) => req<RepoIndex>(`/api/repo/index?${q({ id })}`),
  repoFile: (id: string, path: string) => req<Analysis>(`/api/repo/file?${q({ id, path })}`),
  repoExplain: (id: string, level: RepoLevel, path: string, symbol: string | null, ai: boolean) =>
    req<Explanation>("/api/repo/explain", { id, level, path, symbol, ai }),
};
