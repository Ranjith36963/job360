import { permanentRedirect } from "next/navigation";
import { queryString, type SearchParamsInput } from "@/lib/redirect-query";

// Old /people and /people/...: People lives under Outreach now. Subpath and query kept.
export default async function OldPeoplePage({
  params,
  searchParams,
}: {
  params: Promise<{ rest?: string[] }>;
  searchParams: Promise<SearchParamsInput>;
}) {
  const { rest } = await params;
  const sub = rest && rest.length > 0 ? `/${rest.map(encodeURIComponent).join("/")}` : "";
  permanentRedirect(`/outreach/people${sub}${queryString(await searchParams)}`);
}
