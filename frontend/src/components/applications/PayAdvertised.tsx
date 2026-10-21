import type { components } from "@/lib/api-types";

export type PayAdvertisedValue = components["schemas"]["PayAdvertisedOut"];

const PERIOD_WORDS: Record<string, string> = { annual: "per year", monthly: "per month", hourly: "per hour" };
const PERIOD_IN_AMOUNT = /\b(?:year|yr|annum|month|hour|hr|lpa)\b|\/\s?(?:yr|mo|hr)/i;

/** "90k · per year · USD". Exactly what the ad says: the period and currency are
 * shown only when the ad gave them, and nothing is converted or annualised. */
export function describePay(p: PayAdvertisedValue): string {
  const parts = [p.amount];
  if (p.period && PERIOD_WORDS[p.period] && !PERIOD_IN_AMOUNT.test(p.amount)) {
    parts.push(PERIOD_WORDS[p.period]);
  }
  if (p.currency && p.currency !== "UNKNOWN" && !p.amount.includes(p.currency)) parts.push(p.currency);
  if (p.equity) parts.push("plus equity");
  return parts.join(" · ");
}

/** The pay the ad states, labelled "as advertised". Hidden when the ad has none. */
export function PayAdvertised({ pay }: { pay: PayAdvertisedValue | null | undefined }) {
  if (!pay) return null;
  return (
    <p data-testid="pay-advertised" className="mt-1 text-sm text-muted-foreground" title={pay.evidence}>
      <span className="font-mono text-[11px] uppercase tracking-[0.09em] text-faint">Pay, as advertised</span>{" "}
      <span data-testid="pay-advertised-text">{describePay(pay)}</span>
    </p>
  );
}
