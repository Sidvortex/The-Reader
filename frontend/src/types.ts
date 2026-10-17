export type Call = {
  name: string;
  kind: "internal" | "external" | "builtin" | "unknown";
  target?: string;
  module?: string;
  /** set in repo mode when the call lands in another file of the project */
  xfile?: string;
  xsymbol?: string;
  line: number;
  col: number;
  len: number;
};

export type Sym = {
  id: string;
  kind: "function" | "method" | "class" | "module";
  name: string;
  qualname: string;
  parent: string | null;
  start_line: number;
  end_line: number;
  is_async: boolean;
  params: string[];
  decorators: string[];
  bases: string[];
  returns: string | null;
  docstring: string | null;
  complexity: number;
  calls: Call[];
  called_by: string[];
  uses_modules: string[];
  children: string[];
  /** repo mode: callers that live in other files */
  called_by_ext?: { file: string; symbol: string }[];
};

export type FlowNodeKind =
  | "start" | "end" | "block" | "decision" | "loop" | "return" | "raise"
  | "raise_end" | "try" | "except" | "finally" | "context";

export type FlowChart = {
  nodes: { id: string; kind: FlowNodeKind; label: string; start_line: number; end_line: number }[];
  edges: { from: string; to: string; label: string | null; back: boolean }[];
};

export type Analysis = {
  file: {
    name: string; language: string; lines: number; hash: string; docstring: string | null; has_errors: boolean;
    path?: string; main_guard?: number | null;
  };
  source: string;
  imports: { module: string; names: { name: string; alias: string | null }[]; alias: string | null; line: number }[];
  globals: { name: string; line: number }[];
  symbols: Sym[];
  call_edges: { from: string; to: string; count: number }[];
  flowcharts: Record<string, FlowChart>;
  reading_order: string[];
  /** repo mode: where this file sits in the project */
  repo?: { id: string; path: string; uses: string[]; used_by: string[]; own_packages?: string[] };
};

/** ai: the server pays for AI explanations itself. public: running as a website (GitHub links, visitors' own keys). */
export type ProviderInfo = { id: string; name: string; model: string };
export type Health = {
  version: string; public: boolean;
  ai: boolean; ai_name: string | null; // the server offers AI explanations itself
  providers: ProviderInfo[]; custom_server: boolean;
};

/** Where a project came from when it was opened from a GitHub link. */
export type Source = { kind: "github"; owner: string; repo: string; ref: string | null; commit: string; url: string };

export type Explanation = { markdown: string; source: "ai" | "structural"; cached: boolean };

export type DirStats = { files: number; code_files: number; py_files: number; lines: number; functions: number; classes: number };

export type RepoDir = {
  path: string; name: string; parent: string | null; dirs: string[]; files: string[]; doc: string | null; stats: DirStats;
};

/** [id, kind, name, start_line, parent] */
export type SymRow = [string, Sym["kind"], string, number, string | null];

export type RepoFile = {
  lang: string | null; lines: number;
  doc?: string | null; module?: string; errors?: boolean; main?: boolean; symbols?: SymRow[]; skipped?: string;
};

export type EntryPoint = { kind: "command" | "module" | "script"; label: string; detail: string | null; file: string | null; symbol: string | null };

export type RepoIndex = {
  id: string; root: string; name: string;
  source?: Source | null;
  git: { branch: string | null; commit: string } | null;
  stats: DirStats & { py_analyzed: number };
  languages: { name: string; files: number; lines: number }[];
  readme: { path: string; title: string | null; excerpt: string | null } | null;
  entry_points: EntryPoint[];
  dependencies: { source: string; ecosystem: string; names: string[] }[];
  dirs: Record<string, RepoDir>;
  files: Record<string, RepoFile>;
  /** [from, to, imports, calls] */
  edges: [string, string, number, number][];
  notes: string[];
};

export type RepoStatus = {
  id: string; state: "scanning" | "ready" | "error"; error: string | null; name: string; root: string; phase: string; done: number; total: number;
  source?: Source | null;
  /** after opening a link to a folder or file inside a repo: where it pointed */
  path?: string;
};

export type Initial = { kind: "file"; analysis: Analysis } | { kind: "repo"; id: string; path?: string } | null;

export type RepoLevel = "repo" | "dir" | "file" | "symbol";

export type ExportOptions = { scope: string; depth: "folders" | "files" | "functions"; format: "pdf" | "docx"; skip_tests: boolean };
export type ExportStatus = {
  id: string; state: "waiting" | "working" | "ready" | "error"; phase: string;
  done: number; total: number; error: string | null; filename: string;
};
