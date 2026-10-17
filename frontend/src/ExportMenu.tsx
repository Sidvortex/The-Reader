import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { useDismiss } from "./hooks";
import { parseRepoHash } from "./route";
import type { ExportOptions, ExportStatus, RepoIndex } from "./types";

type Depth = ExportOptions["depth"];

/** The folder you're looking at: the folder itself, or the folder of the file you're in. */
function currentFolder(index: RepoIndex): string {
  const p = parseRepoHash(location.hash);
  const path = p?.loc.path ?? "";
  if (index.dirs[path] !== undefined) return path;
  for (const d of Object.values(index.dirs)) if (d.files.includes(path)) return d.path;
  return "";
}

function download(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export default function ExportMenu({ index }: { index: RepoIndex }) {
  const [open, setOpen] = useState(false);
  const [folder, setFolder] = useState("");
  const [scope, setScope] = useState<"all" | "folder">("all");
  const [depth, setDepth] = useState<Depth>(index.stats.py_analyzed <= 300 ? "functions" : "files");
  const [skipTests, setSkipTests] = useState(true);
  const [job, setJob] = useState<ExportStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useDismiss(open, ref, useCallback(() => setOpen(false), []));

  const busy = !!job && (job.state === "waiting" || job.state === "working");

  // follow a document while it's being made, then download it
  useEffect(() => {
    if (!busy || !job) return;
    let live = true;
    const t = setTimeout(() => {
      api.exportStatus(job.id)
        .then((s) => {
          if (!live) return;
          setJob(s);
          if (s.state === "ready") download(api.exportFileUrl(s.id));
          if (s.state === "error") setError(s.error);
        })
        .catch((e) => live && (setJob(null), setError(e.message)));
    }, 500);
    return () => { live = false; clearTimeout(t); };
  }, [busy, job]);

  const toggle = () => {
    if (!open) {
      const f = currentFolder(index);
      setFolder(f);
      if (!f) setScope("all");
    }
    setOpen((o) => !o);
  };

  const start = (format: ExportOptions["format"]) => {
    setError(null);
    api.exportStart(index.id, { scope: scope === "folder" ? folder : "", depth, format, skip_tests: skipTests })
      .then(setJob)
      .catch((e) => setError(e.message));
  };

  const pct = job && job.total ? Math.min(100, Math.round((100 * job.done) / job.total)) : null;

  return (
    <div className="popover-anchor" ref={ref}>
      <button className="ghost" aria-expanded={open} onClick={toggle} title="Save the explanation as a PDF or Word file">
        Export
      </button>
      {open && (
        <div className="popover export-popover" role="dialog" aria-label="Save as a document">
          <h3>Save as a document</h3>
          <p>
            One file with the whole explanation, to read, print or share: the overview, then each folder, file and
            function.
          </p>
          <fieldset disabled={busy}>
            {folder && (
              <div className="export-row">
                <span className="export-label">Part</span>
                <label><input type="radio" checked={scope === "all"} onChange={() => setScope("all")} /> The whole project</label>
                <label>
                  <input type="radio" checked={scope === "folder"} onChange={() => setScope("folder")} /> This folder:{" "}
                  <code>{folder}/</code>
                </label>
              </div>
            )}
            <div className="export-row">
              <label className="export-label" htmlFor="export-depth">Goes down to</label>
              <select id="export-depth" value={depth} onChange={(e) => setDepth(e.target.value as Depth)}>
                <option value="folders">Folders</option>
                <option value="files">Folders and files</option>
                <option value="functions">Every class and function</option>
              </select>
            </div>
            <label className="export-check">
              <input type="checkbox" checked={skipTests} onChange={(e) => setSkipTests(e.target.checked)} /> Leave out
              tests and examples
            </label>
            <div className="export-actions">
              <button className="primary" onClick={() => start("pdf")}>Save as PDF</button>
              <button className="ghost" onClick={() => start("docx")}>Save as Word</button>
            </div>
          </fieldset>
          {busy && job && (
            <div className="export-progress" role="status">
              <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined}>
                <i style={{ width: `${job.phase.startsWith("Writing") ? 100 : pct ?? 6}%` }} className={job.phase.startsWith("Writing") ? "pulse" : ""} />
              </div>
              <span>
                {job.phase}
                {job.total && job.phase.startsWith("Collecting") ? `: ${job.done.toLocaleString()} of ${job.total.toLocaleString()} files` : ""}…
              </span>
            </div>
          )}
          {job?.state === "ready" && (
            <p className="export-done" role="status">
              Saved <strong>{job.filename}</strong>.{" "}
              <a href={api.exportFileUrl(job.id)} download>Download again</a>
            </p>
          )}
          {error && <p className="export-error" role="alert">{error}</p>}
          <p className="hint">AI explanations you've already opened are included. The rest are worked out from the code.</p>
        </div>
      )}
    </div>
  );
}
