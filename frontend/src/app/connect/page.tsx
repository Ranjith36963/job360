import { permanentRedirect } from "next/navigation";

// The old /connect address (now /settings/connect). Any query string is kept.
export default async function OldConnectPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(await searchParams)) {
    for (const v of Array.isArray(value) ? value : value === undefined ? [] : [value]) params.append(key, v);
  }
  const query = params.toString();
  permanentRedirect(query ? `/settings/connect?${query}` : "/settings/connect");
}
