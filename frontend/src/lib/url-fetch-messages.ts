// ---------------------------------------------------------------------------
// URL fetch on the web — the frontend's OWN copy map
// (docs/plans/2026-09-04-url-fetch/spec.md R2/R12).
// ---------------------------------------------------------------------------
//
// The backend sends `message` too, but the frontend owns its OWN sentence per
// outcome rather than trusting the wire — keyed off the GENERATED type
// (`components["schemas"]["FetchUrlResponse"]["outcome"]`) so a new backend
// outcome value is a TYPE ERROR here, not a blank screen. Frozen spec item 40
// forces this: the e2e test sends an EMPTY server `message` for every outcome
// and still expects a distinct, non-empty sentence per value.

import type { components } from "./api-types";

export type FetchUrlOutcome = components["schemas"]["FetchUrlResponse"]["outcome"];

/** One plain sentence per outcome — what the user does next (spec R3's table). */
export const FETCH_URL_MESSAGES: Record<FetchUrlOutcome, string> = {
  ok: "Filled from the link — check it before you submit.",
  ssrf_denied: "That link points somewhere we won't fetch. Use a different link.",
  invalid_url: "That doesn't look like a web link. Fix it and try again.",
  unreachable: "We couldn't reach that link. Check it, or paste the ad below instead.",
  blocked: "That site blocked this fetch — paste the ad text below instead.",
  timeout: "That took too long to load — paste the ad text below instead, or retry.",
  too_large: "That page is too large for us to read — paste the ad text below instead.",
  unsupported_content: "That link isn't a web page — paste the ad text below instead.",
};

/** Which of the four form fields a `found` entry marks as "filled from the link". */
export const FETCH_URL_FILLABLE_FIELDS = ["title", "company", "location", "description"] as const;
