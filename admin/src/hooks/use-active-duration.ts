import { useEffect, useRef } from "react";

/**
 * Tracks the number of seconds the user has had this component actively
 * visible since mount. Pauses via the Page Visibility API — if the user
 * switches to another tab, the timer stops accumulating until they return.
 *
 * Returns a ref whose `.current` getter yields the current elapsed seconds.
 * Reset by remounting.
 */
export function useActiveDuration() {
  const accumulatedMs = useRef(0);
  const lastStart = useRef<number | null>(null);

  useEffect(() => {
    const now = () => performance.now();

    const startIfVisible = () => {
      if (!document.hidden && lastStart.current === null) {
        lastStart.current = now();
      }
    };

    const pauseIfRunning = () => {
      if (lastStart.current !== null) {
        accumulatedMs.current += now() - lastStart.current;
        lastStart.current = null;
      }
    };

    const onVisibility = () => {
      if (document.hidden) pauseIfRunning();
      else startIfVisible();
    };

    startIfVisible();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      pauseIfRunning();
    };
  }, []);

  const get = () => {
    const active = lastStart.current !== null ? performance.now() - lastStart.current : 0;
    return Math.round((accumulatedMs.current + active) / 1000);
  };

  return { get };
}
