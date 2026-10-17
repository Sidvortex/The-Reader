import { useEffect, type RefObject } from "react";

/** Close a popover on Escape or a click outside it. */
export function useDismiss(open: boolean, ref: RefObject<HTMLDivElement | null>, close: () => void) {
  useEffect(() => {
    if (!open) return;
    const onEvent = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) close();
    };
    document.addEventListener("mousedown", onEvent);
    document.addEventListener("keydown", onEvent);
    return () => { document.removeEventListener("mousedown", onEvent); document.removeEventListener("keydown", onEvent); };
  }, [open, ref, close]);
}

