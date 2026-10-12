/** Where you are in a project, mirrored in the address bar so Back/Forward and reloads work. */
export type Loc = { path: string; symbol: string | null; view: "map" | "flow" };

export const ROOT: Loc = { path: "", symbol: null, view: "map" };

// #/r/<repo id>/<path>?s=<symbol>&v=flow
// A repo id is a hash for a folder, or gh~owner~repo~commit for a GitHub link (enough to fetch it again).
const RE = /^#\/r\/([0-9a-f]{6,40}|gh~[A-Za-z0-9-]+~[A-Za-z0-9._-]+~[0-9a-f]{40})(\/[^?]*)?(?:\?(.*))?$/;

export function parseRepoHash(hash: string): { id: string; loc: Loc } | null {
  const m = RE.exec(hash);
  if (!m) return null;
  const path = (m[2] ?? "/").slice(1).split("/").map(decodeURIComponent).join("/");
  const q = new URLSearchParams(m[3] ?? "");
  return { id: m[1], loc: { path, symbol: q.get("s"), view: q.get("v") === "flow" ? "flow" : "map" } };
}

export function repoHash(id: string, loc: Loc): string {
  const path = loc.path.split("/").map(encodeURIComponent).join("/");
  const q = new URLSearchParams();
  if (loc.symbol) q.set("s", loc.symbol);
  if (loc.view === "flow") q.set("v", "flow");
  const qs = q.toString();
  return `#/r/${id}/${path}${qs ? `?${qs}` : ""}`;
}
