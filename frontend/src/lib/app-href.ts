"use client";

// The Needs you page tells the links under it which chip it shows, so a link to an application carries it.
import { createContext, useContext } from "react";
import { appHref } from "@/lib/back-link";

/** The chip the Needs you page is showing; null outside that page, so links stay plain there. */
export const NeedsYouOrigin = createContext<string | null>(null);

/** Link builder for a page that may be inside Needs you: adds ?from=needs-you&show= when it is. */
export function useAppHref(): (id: number, hash?: string) => string {
  const show = useContext(NeedsYouOrigin);
  return (id, hash = "") => (show === null ? `/applications/${id}` : appHref(id, "needs-you", show)) + hash;
}
