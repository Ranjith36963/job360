/**
 * Owner 2026-10-24: an answer that is a web address is clickable wherever answers show. The value is
 * untrusted (written by an assistant or a job form), so ONLY http/https is ever linked. A bare domain
 * ("linkedin.com/in/x") gets https://; anything else (javascript:, data:, mailto:, a relative path, an
 * email address, a sentence) stays plain text. The visible text is always the value as stored.
 */

// A whole value that is a domain (at least one dot, letters-only last label), optionally with a path.
const BARE_DOMAIN = /^(?:[a-z0-9-]+\.)+[a-z]{2,}(?:[/?#]\S*)?$/i;

export function safeHref(value: string | null | undefined): string | null {
  const text = (value ?? "").trim();
  if (!text || /\s/.test(text)) return null;
  let candidate: string;
  if (/^https?:\/\//i.test(text)) candidate = text;
  else if (BARE_DOMAIN.test(text)) candidate = `https://${text}`;
  else return null;
  try {
    const url = new URL(candidate);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    if (!url.hostname.includes(".") || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}
