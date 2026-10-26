/**
 * Sentry `beforeSend` / `beforeSendTransaction` scrubber. Single-use auth
 * tokens ride in page URLs (?token=...) and capability tokens in
 * /api/files|proof|calendar/<token>; neither may reach Sentry.
 */
import { stripSecretsFromUrl } from "./stripSecretsFromUrl";

type Bag = Record<string, unknown>;

interface ScrubbableEvent {
  transaction?: string;
  message?: string;
  request?: { url?: string; query_string?: unknown; headers?: Record<string, string> };
  breadcrumbs?: Array<{ data?: Bag }>;
  spans?: Array<{ description?: string; data?: Bag }>;
}

const URL_KEYS = ["url", "from", "to", "http.url", "url.full", "http.target"];

function scrubBag(data: Bag | undefined): void {
  if (!data) return;
  for (const key of URL_KEYS) {
    if (typeof data[key] === "string") data[key] = stripSecretsFromUrl(data[key] as string);
  }
  delete data["http.query"];
  delete data["url.query"];
  delete data["http.fragment"];
}

/** Redacts URLs in an event or transaction in place and returns it. */
export function scrubSentryEvent<T extends ScrubbableEvent>(event: T): T {
  if (typeof event.transaction === "string") event.transaction = stripSecretsFromUrl(event.transaction);
  const req = event.request;
  if (req) {
    if (typeof req.url === "string") req.url = stripSecretsFromUrl(req.url);
    delete req.query_string;
    const headers = req.headers;
    if (headers) {
      for (const h of Object.keys(headers)) {
        if (/^(referer|referrer)$/i.test(h)) headers[h] = stripSecretsFromUrl(headers[h]);
      }
    }
  }
  for (const crumb of event.breadcrumbs ?? []) scrubBag(crumb.data);
  for (const span of event.spans ?? []) {
    if (typeof span.description === "string") {
      span.description = span.description.replace(/\S+/g, (w) =>
        /^(\/|[a-z]+:\/\/)/i.test(w) ? stripSecretsFromUrl(w) : w,
      );
    }
    scrubBag(span.data);
  }
  return event;
}
