import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// Validates that a URL from the backend uses http/https before rendering or opening.
// Prevents javascript: protocol XSS from malformed scraper output.
export function safeUrl(url: string | null | undefined): string {
  if (!url) return "#";
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? url : "#";
  } catch {
    return "#";
  }
}

/** "2 h ago" / "just now" — a short, human relative time for an ISO
 * timestamp. Never throws on a bad/empty input: falls back to "". A future
 * timestamp (clock skew) reads as "just now" rather than a negative duration. */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";

  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";

  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} m ago`;

  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;

  const days = Math.round(hours / 24);
  if (days < 30) return `${days} d ago`;

  const months = Math.round(days / 30);
  if (months < 12) return `${months} mo ago`;

  const years = Math.round(months / 12);
  return `${years} y ago`;
}
