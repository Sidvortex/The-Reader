import { memo, type ReactNode } from "react";
import { api } from "./api";
import Markdown from "./Markdown";
import { useExplanation } from "./explanations";
import type { RepoIndex } from "./types";

type Props = {
  index: RepoIndex;
  dir: string;
  ai: boolean;
  linkPath: (name: string) => ReactNode | null;
  /** project overview only: read the folder again after the code changed */
  onRescan?: () => void;
};

/** Right-hand panel for the top layers: the project overview, or one folder. */
export default memo(function RepoDetail({ index, dir, ai, linkPath, onRescan }: Props) {
  const isRoot = dir === "";
  const node = index.dirs[dir];
  const { structural, ai: aiEx, loading, error } = useExplanation(
    `repo:${index.id}:${dir}`,
    (useAi) => api.repoExplain(index.id, isRoot ? "repo" : "dir", dir, null, useAi),
    ai,
  );
  const ex = aiEx ?? structural;
  return (
    <aside className="detail" aria-label={isRoot ? "Project overview" : "Folder explanation"}>
      <header className="detail-head">
        <div className="kicker">{isRoot ? "Project overview" : "Folder"}</div>
        <h2>{isRoot ? index.readme?.title ?? index.name : `${node.path}/`}</h2>
        {isRoot && (index.source ? (
          <div className="path-line" title={`${index.source.url} at ${index.source.commit}`}>
            <a href={`${index.source.url}/tree/${index.source.commit}`} target="_blank" rel="noreferrer">
              github.com/{index.source.owner}/{index.source.repo}
            </a>
            {index.source.ref ? `, ${index.source.ref}` : ""} at {index.source.commit.slice(0, 7)}
          </div>
        ) : (
          <div className="path-line" title={index.root}>
            {index.root}{index.git?.branch ? `, branch ${index.git.branch}` : ""}
          </div>
        ))}
        {onRescan && <button className="link-btn rescan" onClick={onRescan}>Read the folder again</button>}
      </header>
      <section className="explain full">
        {ex && <Markdown md={ex.markdown} link={linkPath} />}
        {loading && <p className="thinking">{isRoot ? "Reading the whole project…" : "Reading this folder…"}</p>}
        {error && <p className="err">{error}</p>}
        {ex && !loading && (
          <p className="source-note">{ex.source === "ai" ? "Written by AI" : "Summary worked out from the code"}</p>
        )}
        {aiEx && structural && (
          <details className="facts">
            <summary>Details from the code</summary>
            <Markdown md={structural.markdown} link={linkPath} />
          </details>
        )}
      </section>
    </aside>
  );
});
