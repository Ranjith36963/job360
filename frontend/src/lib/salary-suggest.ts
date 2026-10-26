// Suggested answers for salary questions, taken ONLY from what the user already saved
// (Memory: user_info.logistics.current_salary and preferences.salary_by_country).
// Pure functions: nothing here invents a number or calls the network.

export type SalaryAmount = { amount: number; currency: string; period: string };
export type SalaryRange = { country: string; min: number | null; max: number | null; currency: string; period: string };
export type SalaryFacts = { current: SalaryAmount | null; expected: SalaryRange[] };

export const NO_SALARY_FACTS: SalaryFacts = { current: null, expected: [] };

type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown) => (typeof v === "string" ? v.trim() : "");
function num(v: unknown): number | null {
  const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : NaN;
  return Number.isFinite(n) && n > 0 ? n : null;
}

/** Pull the two salary facts out of a profile response. Anything missing or malformed reads as "not saved". */
export function salaryFactsFromProfile(profile: unknown): SalaryFacts {
  if (!isRec(profile)) return NO_SALARY_FACTS;
  let current: SalaryAmount | null = null;
  const logistics = isRec(profile.user_info) ? profile.user_info.logistics : null;
  const cs = isRec(logistics) ? logistics.current_salary : null;
  if (isRec(cs)) {
    const amount = num(cs.amount);
    if (amount !== null) current = { amount, currency: str(cs.currency), period: str(cs.period) };
  }
  const expected: SalaryRange[] = [];
  const list = isRec(profile.preferences) ? profile.preferences.salary_by_country : null;
  if (Array.isArray(list)) {
    for (const e of list) {
      if (!isRec(e)) continue;
      const min = num(e.min);
      const max = num(e.max);
      if (min === null && max === null) continue;
      expected.push({ country: str(e.country), min, max, currency: str(e.currency), period: str(e.period) });
    }
  }
  return { current, expected };
}

const SALARY_WORD = /\b(salary|salaries|pay|package|compensation|ctc|remuneration)\b/i;
const CURRENT_WORD = /\b(current|currently|last|present|existing|previous|ctc)\b/i;
const EXPECTED_WORD = /\b(expect\w*|desired|target|looking for|wanted)\b/i;

/** Which salary the question asks for, or null when it is not a salary question. */
export function salaryKind(question: string): "current" | "expected" | null {
  if (!SALARY_WORD.test(question)) return null;
  if (EXPECTED_WORD.test(question)) return "expected";
  if (CURRENT_WORD.test(question)) return "current";
  return "expected"; // plain "salary" that is not about the current one
}

const PERIOD: Record<string, string> = {
  year: "a year", yearly: "a year", annual: "a year", annum: "a year", annually: "a year",
  month: "a month", monthly: "a month",
  week: "a week", weekly: "a week",
  day: "a day", daily: "a day",
  hour: "an hour", hourly: "an hour",
};

function money(n: number, currency: string): string {
  const code = currency.toUpperCase();
  const digits = Number.isInteger(n) ? 0 : 2;
  try {
    return new Intl.NumberFormat(code === "INR" ? "en-IN" : "en-GB", {
      style: "currency",
      currency: code,
      currencyDisplay: "narrowSymbol",
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(n);
  } catch {
    // No or unknown currency: the plain number, with the code when there is one.
    const plain = new Intl.NumberFormat("en-GB", { maximumFractionDigits: digits }).format(n);
    return code ? `${code} ${plain}` : plain;
  }
}

function withPeriod(text: string, period: string): string {
  const p = PERIOD[period.toLowerCase()];
  return p ? `${text} ${p}` : text;
}

/** "£45,000 a year", "£60,000 – £80,000 a year", "£60,000 a year" (min only), "up to £80,000 a year" (max only). */
export function formatSalary(min: number | null, max: number | null, currency: string, period: string): string | null {
  if (min !== null && max !== null && min !== max) return withPeriod(`${money(min, currency)} – ${money(max, currency)}`, period);
  const one = min ?? max;
  if (one === null) return null;
  if (min === null) return withPeriod(`up to ${money(one, currency)}`, period);
  return withPeriod(money(one, currency), period);
}

/** The text to suggest for this question, or null (not a salary question, or nothing saved to reuse). */
export function suggestSalary(question: string, jobCountry: string | null | undefined, facts: SalaryFacts | undefined): string | null {
  if (!facts) return null;
  const kind = salaryKind(question);
  if (kind === "current") {
    const c = facts.current;
    return c ? formatSalary(c.amount, null, c.currency, c.period) : null;
  }
  if (kind === "expected") {
    const cc = (jobCountry ?? "").trim().toLowerCase();
    const match = cc ? facts.expected.find((e) => e.country.toLowerCase() === cc) : undefined;
    const pick = match ?? (facts.expected.length === 1 ? facts.expected[0] : undefined);
    return pick ? formatSalary(pick.min, pick.max, pick.currency, pick.period) : null;
  }
  return null;
}
