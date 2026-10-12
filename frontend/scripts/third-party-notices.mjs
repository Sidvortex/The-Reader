// Writes THIRD_PARTY_NOTICES.md: every package that ships inside the built interface, with its license text.
// Runs after `vite build` (see package.json), so the notices always match what's bundled.
import { execSync } from "node:child_process";
import { copyFileSync, existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const frontend = join(here, "..");
const project = join(frontend, "..");

let raw;
try {
  raw = execSync("npm ls --omit=dev --all --json --long", { cwd: frontend, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
} catch (e) {
  raw = e.stdout; // npm ls exits non-zero for harmless warnings but still prints the tree
}
const tree = JSON.parse(raw);

const pkgs = new Map();
(function walk(deps) {
  for (const [name, info] of Object.entries(deps ?? {})) {
    if (!info.path || pkgs.has(`${name}@${info.version}`)) continue;
    pkgs.set(`${name}@${info.version}`, { name, version: info.version, path: info.path });
    walk(info.dependencies);
  }
})(tree.dependencies);

const parts = [
  "# Third-party notices",
  "",
  "The Reader's own code is covered by LICENSE.md. The built interface in `src/thereader/frontend_build/` also",
  "contains the open-source packages and fonts below. Each keeps its own license, reproduced here as those licenses require.",
  "",
];
for (const { name, version, path } of [...pkgs.values()].sort((a, b) => a.name.localeCompare(b.name))) {
  const meta = JSON.parse(readFileSync(join(path, "package.json"), "utf8"));
  const license = typeof meta.license === "string" ? meta.license : meta.license?.type ?? "see the package";
  const file = existsSync(path) ? readdirSync(path).find((f) => /^(licen[cs]e|copying)(\.[a-z]+)?$/i.test(f)) : undefined;
  const text = file ? readFileSync(join(path, file), "utf8").trim() : `No license file is included in the package; its package.json declares: ${license}.`;
  const source = `https://www.npmjs.com/package/${name}/v/${version}`;
  parts.push(`## ${name} ${version}`, "", `License: ${license}  `, `Source: ${source}`, "", "```", text, "```", "");
}
const out = parts.join("\n");
writeFileSync(join(project, "THIRD_PARTY_NOTICES.md"), out);
const built = join(project, "src", "thereader", "frontend_build");
if (existsSync(built)) {
  // served next to the page, so the license and notices are one click away even when The Reader runs as a website
  writeFileSync(join(built, "THIRD_PARTY_NOTICES.md"), out);
  copyFileSync(join(project, "LICENSE.md"), join(built, "LICENSE.md"));
}
console.log(`third-party notices: ${pkgs.size} packages`);
