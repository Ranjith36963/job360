/**
 * Plain words for the backend's closed sets (APPLICATION_RECEIPT_CHANNELS,
 * JOB_FOUND_ON, CONTACT_FOUND_VIA in `backend/src/core/settings.py`). The
 * lists below are the finite OPTIONS offered in selects; a stored value that
 * is not in them (legacy free text, a future member) is shown humanised,
 * never dropped.
 */

export const CLOSED_SET_LABEL: Record<string, string> = {
  // receipt channels
  company_site: "Company site",
  linkedin_easy_apply: "LinkedIn Easy Apply",
  job_board: "Job board",
  email: "Email",
  referral: "Referral",
  recruiter: "Recruiter",
  // job found on
  indeed: "Indeed",
  linkedin: "LinkedIn",
  company_careers: "Company careers page",
  visa_sponsor_list: "Visa-sponsor list",
  pasted_by_user: "You pasted it",
  // contact found via
  apollo: "Apollo",
  job_ad: "Job ad",
  event: "Event",
  other: "Other",
};

export const JOB_FOUND_ON_VALUES = [
  "indeed",
  "linkedin",
  "company_careers",
  "job_board",
  "referral",
  "visa_sponsor_list",
  "pasted_by_user",
  "other",
] as const;

export const CONTACT_FOUND_VIA_VALUES = [
  "company_site",
  "linkedin",
  "apollo",
  "referral",
  "job_ad",
  "email",
  "event",
  "other",
] as const;

/** "some_value" -> "Some value" (unknown members). */
function humanise(value: string): string {
  const spaced = value.replace(/_/g, " ").trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : value;
}

/** Plain words for one closed-set value. Free text with spaces/capitals
 * (a legacy receipt channel) is returned as-is; null/empty = "Not set". */
export function closedSetLabel(value: string | null | undefined): string {
  if (!value) return "Not set";
  const known = CLOSED_SET_LABEL[value];
  if (known) return known;
  // Only snake_case is a (new) closed-set member; anything else is legacy
  // free text the user or an older client wrote — show it untouched.
  return /^[a-z0-9]+(_[a-z0-9]+)+$/.test(value) ? humanise(value) : value;
}
