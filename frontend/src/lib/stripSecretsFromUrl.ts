/**
 * Returns a URL that is safe to send to a third party or write to a log:
 * no query string, no hash, and the token tail of the capability-URL routes
 * (`/api/files/<token>`, `/api/proof/<token>`, `/api/calendar/<token>.ics`)
 * replaced by `[redacted]`. Never throws; works on absolute and relative URLs.
 */
const TOKEN_PATH = /^(\/(?:api\/)?(?:files|proof|calendar)\/)[^/?#]+/;

export function stripSecretsFromUrl(url: string): string {
  const cut = url.search(/[?#]/);
  const noQuery = cut === -1 ? url : url.slice(0, cut);
  const m = /^([a-z][a-z0-9+.-]*:\/\/[^/]*)?(.*)$/i.exec(noQuery);
  const origin = m?.[1] ?? "";
  const path = (m?.[2] ?? noQuery).replace(TOKEN_PATH, "$1[redacted]");
  return origin + path;
}
