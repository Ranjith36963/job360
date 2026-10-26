"use client";

import { useLayoutEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

/**
 * Reads the named query params ONCE into state, then removes the whole query
 * string and hash from the address bar (history.replaceState) before any
 * effect or network call runs. Keeps single-use tokens out of the URL that
 * analytics, error reporters and screenshots see. Pass the params the page
 * needs (e.g. ["token", "next"]); they stay available from the returned object.
 */
export function useScrubbedUrlParams(names: string[]): Record<string, string | null> {
  const searchParams = useSearchParams();
  const [values] = useState(() => {
    const out: Record<string, string | null> = {};
    for (const n of names) out[n] = searchParams.get(n);
    return out;
  });
  useLayoutEffect(() => {
    if (window.location.search || window.location.hash) {
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);
  return values;
}
