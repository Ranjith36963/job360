// Regions (owner decision 2026-10-26): one Where-you'll-work line can cover a
// whole region. Codes match backend services/profile/regions.py; which countries
// each covers lives there (a country's own line wins, then its region's, then
// "All other countries").

export const REGIONS = [
  { code: "NORTH_AMERICA", name: "North America", hint: "US, Canada, Mexico, Central America, Caribbean" },
  { code: "SOUTH_AMERICA", name: "South America", hint: "Brazil, Argentina, Chile, Colombia…" },
  { code: "EUROPE", name: "Europe", hint: "UK, Germany, Netherlands, Switzerland, Poland…" },
  { code: "MIDDLE_EAST", name: "Middle East", hint: "UAE (Dubai), Saudi Arabia, Qatar, Israel, Turkey…" },
  { code: "ASIA", name: "Asia", hint: "India, Singapore, Malaysia, Japan, China…" },
  { code: "AFRICA", name: "Africa", hint: "South Africa, Nigeria, Kenya, Egypt…" },
  { code: "OCEANIA", name: "Oceania", hint: "Australia, New Zealand, Pacific islands" },
] as const;

const NAME: Record<string, string> = Object.fromEntries(REGIONS.map((r) => [r.code, r.name]));

export function isRegion(code: string): boolean {
  return code in NAME;
}

/** A region's plain name, or undefined for anything else. */
export function regionName(code: string): string | undefined {
  return NAME[code];
}
