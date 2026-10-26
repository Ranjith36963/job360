import { permanentRedirect } from "next/navigation";
import { queryString, type SearchParamsInput } from "@/lib/redirect-query";

// Old /settings: Account is the settings home now. Query kept.
export default async function OldSettingsPage({ searchParams }: { searchParams: Promise<SearchParamsInput> }) {
  permanentRedirect(`/account${queryString(await searchParams)}`);
}
