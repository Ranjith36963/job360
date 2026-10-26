import { permanentRedirect } from "next/navigation";
import { queryString, type SearchParamsInput } from "@/lib/redirect-query";

// Old /settings/*: Account is /account; Connect your assistant (with its ?tab=) is
// /connect; Job sites is a tab of it. Anything else lands on Account. Query kept.
export default async function OldSettingsCatchAllPage({
  params,
  searchParams,
}: {
  params: Promise<{ rest: string[] }>;
  searchParams: Promise<SearchParamsInput>;
}) {
  const { rest } = await params;
  const sp = await searchParams;
  if (rest[0] === "connect") permanentRedirect(`/connect${queryString(sp)}`);
  if (rest[0] === "job-sites") permanentRedirect(`/connect${queryString(sp, { tab: "job-sites" })}`);
  permanentRedirect(`/account${queryString(sp)}`);
}
