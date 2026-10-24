/**
 * Owner 2026-10-24: an answer that is a web address is clickable wherever answers show. The value is
 * untrusted (written by an assistant or a job form), so ONLY http/https is ever linked. A domain with a
 * path ("linkedin.com/in/x") or a "www." address gets https://; anything else (javascript:, data:, mailto:, a relative path, an
 * email address, a sentence) stays plain text. The visible text is always the value as stored.
 */

// A scheme-less web address: a domain WITH a path ("linkedin.com/in/x"), or one starting "www.".
// A bare "Next.js", "Node.js" or "resume.pdf" is a name or a file, never a link.
const BARE_DOMAIN = /^(?:(?:[a-z0-9-]+\.)+[a-z]{2,}\/\S*|www\.(?:[a-z0-9-]+\.)+[a-z]{2,}(?:[/?#]\S*)?)$/i;

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
