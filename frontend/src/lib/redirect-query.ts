export type SearchParamsInput = Record<string, string | string[] | undefined>;

/** `?a=1&b=2` (or "") from a Next.js `searchParams` object, repeated keys kept.
 *  `set` forces keys to a single value (used when an old URL maps to a tab). */
export function queryString(sp: SearchParamsInput, set: Record<string, string> = {}): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(sp)) {
    if (key in set) continue;
    for (const v of Array.isArray(value) ? value : value === undefined ? [] : [value]) params.append(key, v);
  }
  for (const [k, v] of Object.entries(set)) params.set(k, v);
  const q = params.toString();
  return q ? `?${q}` : "";
}
