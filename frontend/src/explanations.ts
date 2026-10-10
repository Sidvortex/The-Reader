import { useEffect, useState } from "react";
import type { Explanation } from "./types";

const cache = new Map<string, Explanation>();

export type ExplainState = { structural: Explanation | null; ai: Explanation | null; loading: boolean; error: string | null };

/**
 * The instant structural explanation, plus the AI one when `ai` is on. Both are cached per key,
 * so flipping back to something you've already read is instant.
 */
export function useExplanation(key: string, fetcher: (ai: boolean) => Promise<Explanation>, ai: boolean): ExplainState {
  const [state, setState] = useState<ExplainState>({ structural: null, ai: null, loading: false, error: null });
  useEffect(() => {
    let live = true;
    const sk = `${key}|structural`, ak = `${key}|ai`;
    setState({ structural: cache.get(sk) ?? null, ai: ai ? cache.get(ak) ?? null : null, loading: ai && !cache.has(ak), error: null });
    if (!cache.has(sk)) {
      fetcher(false)
        .then((ex) => { cache.set(sk, ex); if (live) setState((s) => ({ ...s, structural: ex })); })
        .catch((e) => live && setState((s) => ({ ...s, error: e.message })));
    }
    if (ai && !cache.has(ak)) {
      fetcher(true)
        .then((ex) => {
          cache.set(ak, ex);
          if (live) setState((s) => ({ ...s, ai: ex.source === "ai" ? ex : null, loading: false }));
        })
        .catch((e) => live && setState((s) => ({ ...s, loading: false, error: e.message })));
    }
    return () => { live = false; };
    // the fetcher closes over the same key, so the key alone decides when to refetch
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, ai]);
  return state;
}
