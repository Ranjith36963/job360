/** How long a settled SUCCESSFUL read stays shared for the same key. Layout consumers (the badge, the
 *  pause hook) wait for the signed-in user, so they ask a moment AFTER Home read: without this window
 *  the same page load made the same GET twice (real-browser check 2026-11-13). */
export const SHARED_READ_TTL_MS = 5_000;

const resets: (() => void)[] = [];

/**
 * One shared read per key. The same key shares the in-flight request AND, for SHARED_READ_TTL_MS after
 * it succeeded, its answer. A failed read is never kept. A different key always starts a fresh request
 * and replaces the kept entry, so reads after the user's own action (unique keys) stay fresh.
 */
export function createSharedRead<T>(read: () => Promise<T>): (key: string) => Promise<T> {
  let entry: { key: string; promise: Promise<T>; doneAt: number | null } | null = null;
  resets.push(() => {
    entry = null;
  });
  return (key) => {
    if (entry?.key === key && (entry.doneAt === null || Date.now() - entry.doneAt < SHARED_READ_TTL_MS)) {
      return entry.promise;
    }
    const mine: NonNullable<typeof entry> = { key, promise: read(), doneAt: null };
    entry = mine;
    mine.promise.then(
      () => {
        mine.doneAt = Date.now();
      },
      () => {
        if (entry === mine) entry = null;
      },
    );
    return mine.promise;
  };
}

/** Drop every kept answer. For tests only (src/test/setup.ts runs it between tests): in the browser the
 *  window is what makes one page load one request. */
export function resetSharedReads(): void {
  for (const reset of resets) reset();
}
