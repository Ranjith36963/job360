/** Group OAuth grants by app so one app shows as one row. */

export interface GrantLike {
  id: number;
  client_name: string;
  created_at: string;
}

export interface GrantGroup<T extends GrantLike> {
  /** Stable, testid-safe key: the client name, lowercased and slugged. */
  key: string;
  name: string;
  newest: T;
  older: T[];
}

function time(iso: string): number {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? 0 : t;
}

/** Newest first; ties broken by the higher id. */
function newerFirst<T extends GrantLike>(a: T, b: T): number {
  return time(b.created_at) - time(a.created_at) || b.id - a.id;
}

export function grantKey(clientName: string): string {
  const slug = clientName
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "app";
}

/**
 * The API gives a grant a client name only (no client id), so the name is the
 * app identity. Apps are sorted by their newest grant, newest first.
 */
export function groupGrants<T extends GrantLike>(grants: T[]): GrantGroup<T>[] {
  const byKey = new Map<string, T[]>();
  for (const g of grants) {
    const key = grantKey(g.client_name);
    const list = byKey.get(key);
    if (list) list.push(g);
    else byKey.set(key, [g]);
  }
  const groups: GrantGroup<T>[] = [];
  for (const [key, list] of byKey) {
    const sorted = [...list].sort(newerFirst);
    groups.push({
      key,
      name: sorted[0].client_name,
      newest: sorted[0],
      older: sorted.slice(1),
    });
  }
  return groups.sort((a, b) => newerFirst(a.newest, b.newest));
}
