// ---------------------------------------------------------------------------
// Pure logic behind Profile -> Memory. No React, no fetching.
//
// Memory is six `user_info.*` blocks plus one preference
// (`preferences.salary_by_country`). Every write REPLACES the whole block, so
// each row knows how to READ its value out of a block and how to WRITE a new
// value into a copy of it (`readRow` / `writeRow`). Provenance ("Saved by
// Claude, 3 Oct") is rebuilt from the field's edit history, never guessed.
// ---------------------------------------------------------------------------

import { whoLabel } from "@/lib/event-labels";
import { countryName } from "@/lib/countries";

export const CONTACT = "user_info.contact";
export const RTW = "user_info.right_to_work";
export const LOGISTICS = "user_info.logistics";
export const LANGUAGES = "user_info.languages";
export const EQUALITY = "user_info.equality";
export const ANSWERS = "user_info.answers";
export const SALARY = "preferences.salary_by_country";
export const MEMORY_PATHS = [CONTACT, RTW, LOGISTICS, LANGUAGES, EQUALITY, ANSWERS, SALARY] as const;

export const PNS = "Prefer not to say";
export const WORK_AUTH_OPTIONS = ["citizen", "permanent_resident", "visa", "needs_sponsorship"] as const;
export const LEVEL_OPTIONS = ["native", "fluent", "professional", "basic"] as const;
const WORK_AUTH_LABEL: Record<string, string> = {
  citizen: "Citizen",
  permanent_resident: "Permanent resident",
  visa: "On a visa",
  needs_sponsorship: "Needs sponsorship",
};

/** Plain names for every closed key the Memory tab shows. */
export const FIELD_LABEL: Record<string, string> = {
  name: "Full name",
  email: "Email",
  phone: "Phone",
  address: "Home address",
  date_of_birth: "Date of birth",
  preferred_name: "Preferred name",
  work_authorization: "Allowed to work here",
  needs_sponsorship: "Needs sponsorship",
  visa_type: "Visa type",
  visa_expires: "Visa expires",
  citizenship: "Citizenship",
  sanctions_country_citizen: "Citizen of a sanctioned country?",
  notice_period: "Notice period",
  earliest_start: "Earliest start",
  willing_to_relocate: "Willing to relocate",
  relocate_where: "Relocate where",
  travel_ok_pct: "Travel is fine, up to (%)",
  driving_licence: "Driving licence",
  languages: "Languages",
  gender: "Gender",
  ethnicity: "Ethnicity",
  disability: "Disability",
  veteran: "Veteran",
  sexual_orientation: "Sexual orientation",
  transgender: "Transgender",
  salary: "Salary",
};

export type PartInput = "text" | "date" | "number" | "textarea" | "yesno" | "select" | "countries" | "languages";

export interface Part {
  key: string;
  label: string;
  input: PartInput;
  options?: readonly string[];
  /** Offer "Prefer not to say" first (equality + sanctions). */
  pns?: boolean;
  placeholder?: string;
}

/** Where a row's value sits inside its block. */
export type Scope =
  | { kind: "dict" }
  | { kind: "country"; code: string } // block.countries[] record
  | { kind: "salary"; code: string } // the root list, one record per country
  | { kind: "whole" }; // the block IS the value (languages)

export interface RowSpec {
  id: string; // the test id suffix: memory-row-<id>
  path: string; // the PATCH path
  label: string;
  parts: Part[];
  scope: Scope;
  sensitive: boolean;
  /** Several parts that form one value ({first, last} -> "Full name"). */
  composite?: boolean;
}

type Block = unknown;
const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

export function isEmpty(v: unknown): boolean {
  if (v === undefined || v === null) return true;
  if (typeof v === "string") return v.trim() === "";
  if (Array.isArray(v)) return v.length === 0;
  if (isObj(v)) return Object.keys(v).length === 0;
  return false;
}

export function isPns(v: unknown): boolean {
  return typeof v === "string" && v.trim().toLowerCase() === PNS.toLowerCase();
}

// ---- Which rows are sensitive (confirm before saving) ---------------------

const SENSITIVE_CONTACT = new Set(["date_of_birth", "address"]);
/** Visa, equality, salary, birth date and address ask first. */
export function isSensitive(path: string, key: string): boolean {
  if (path === RTW || path === EQUALITY || path === SALARY) return true;
  return path === CONTACT && SENSITIVE_CONTACT.has(key);
}

// ---- Row builders ----------------------------------------------------------

const ADDRESS_PARTS: Part[] = [
  { key: "address_lines", label: "Street (up to 3 lines)", input: "textarea" },
  { key: "address_city", label: "City", input: "text" },
  { key: "address_postcode", label: "Postcode", input: "text" },
  { key: "address_country", label: "Country code (like GB)", input: "text" },
];
const NAME_PARTS: Part[] = [
  { key: "legal_first_name", label: "First name", input: "text" },
  { key: "legal_last_name", label: "Last name", input: "text" },
];
const SALARY_PARTS: Part[] = [
  { key: "min", label: "From", input: "number" },
  { key: "max", label: "Up to (leave empty for one figure)", input: "number" },
  { key: "currency", label: "Currency (like GBP)", input: "text" },
  { key: "period", label: "Per", input: "select", options: ["year", "month"] },
];

function single(path: string, id: string, key: string, input: PartInput, scope: Scope, extra: Partial<Part> = {}): RowSpec {
  const label = FIELD_LABEL[key] ?? key;
  return {
    id, path, label, scope, sensitive: isSensitive(path, key),
    parts: [{ key, label, input, ...extra }],
  };
}

export function contactRows(): RowSpec[] {
  const d = { kind: "dict" } as const;
  return [
    { id: `${CONTACT}.name`, path: CONTACT, label: FIELD_LABEL.name, scope: d, sensitive: false, composite: true, parts: NAME_PARTS },
    single(CONTACT, `${CONTACT}.email`, "email", "text", d),
    single(CONTACT, `${CONTACT}.phone`, "phone", "text", d),
    { id: `${CONTACT}.address`, path: CONTACT, label: FIELD_LABEL.address, scope: d, sensitive: true, composite: true, parts: ADDRESS_PARTS },
    single(CONTACT, `${CONTACT}.date_of_birth`, "date_of_birth", "date", d),
  ];
}

/** The four right-to-work facts for one country. */
export function rtwCountryRows(code: string): RowSpec[] {
  const s = { kind: "country", code } as const;
  const p = (key: string) => `${RTW}.${code}.${key}`;
  return [
    single(RTW, p("work_authorization"), "work_authorization", "select", s, { options: WORK_AUTH_OPTIONS }),
    single(RTW, p("needs_sponsorship"), "needs_sponsorship", "yesno", s),
    single(RTW, p("visa_type"), "visa_type", "text", s),
    single(RTW, p("visa_expires"), "visa_expires", "text", s, { placeholder: "2027-03 or 2027-03-14" }),
  ];
}

export function rtwTopRows(): RowSpec[] {
  const d = { kind: "dict" } as const;
  return [
    single(RTW, `${RTW}.citizenship`, "citizenship", "countries", d),
    single(RTW, `${RTW}.sanctions_country_citizen`, "sanctions_country_citizen", "yesno", d, { pns: true }),
  ];
}

export function logisticsRows(): RowSpec[] {
  const d = { kind: "dict" } as const;
  return [
    single(LOGISTICS, `${LOGISTICS}.notice_period`, "notice_period", "text", d),
    single(LOGISTICS, `${LOGISTICS}.earliest_start`, "earliest_start", "text", d, { placeholder: "A date, or words like 'two weeks'" }),
    {
      id: LANGUAGES, path: LANGUAGES, label: FIELD_LABEL.languages, scope: { kind: "whole" }, sensitive: false,
      parts: [{ key: "languages", label: FIELD_LABEL.languages, input: "languages", placeholder: "English (fluent), German (basic)" }],
    },
  ];
}

export function logisticsCountryRows(code: string): RowSpec[] {
  const s = { kind: "country", code } as const;
  const p = (key: string) => `${LOGISTICS}.${code}.${key}`;
  return [
    single(LOGISTICS, p("willing_to_relocate"), "willing_to_relocate", "yesno", s),
    single(LOGISTICS, p("relocate_where"), "relocate_where", "text", s),
    single(LOGISTICS, p("travel_ok_pct"), "travel_ok_pct", "number", s),
    single(LOGISTICS, p("driving_licence"), "driving_licence", "yesno", s),
  ];
}

export function equalityRows(): RowSpec[] {
  const d = { kind: "dict" } as const;
  return ["gender", "ethnicity", "disability", "veteran", "sexual_orientation", "transgender"].map((k) =>
    single(EQUALITY, `${EQUALITY}.${k}`, k, "text", d, { pns: true }),
  );
}

export function salaryRow(code: string): RowSpec {
  return {
    id: `${SALARY}.${code}`, path: SALARY, label: countryName(code), scope: { kind: "salary", code },
    sensitive: true, composite: true, parts: SALARY_PARTS,
  };
}

// ---- Reading and writing a block ------------------------------------------

function recordsOf(list: unknown): Record<string, unknown>[] {
  return Array.isArray(list) ? list.filter(isObj) : [];
}

/** A salary record as min/max. A record saved before ranges has one `amount`:
 *  it reads as min = max. */
export function normalizeSalary(rec: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const min = rec.min ?? rec.amount;
  if (!isEmpty(min)) out.min = min;
  const max = rec.max ?? min;
  if (!isEmpty(max)) out.max = max;
  if (!isEmpty(rec.currency)) out.currency = rec.currency;
  if (!isEmpty(rec.period)) out.period = rec.period;
  return out;
}

function pickParts(rec: Record<string, unknown>, parts: Part[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of parts) if (!isEmpty(rec[p.key])) out[p.key] = rec[p.key];
  return out;
}

/** The row's current value in a block; `undefined` when nothing is saved. */
export function readRow(spec: RowSpec, block: Block): unknown {
  const { scope, parts, composite } = spec;
  if (scope.kind === "whole") return isEmpty(block) ? undefined : block;
  let rec: Record<string, unknown> | undefined;
  if (scope.kind === "dict") rec = isObj(block) ? block : undefined;
  else if (scope.kind === "country") {
    rec = recordsOf(isObj(block) ? block.countries : undefined).find((r) => r.country === scope.code);
  } else {
    const found = recordsOf(block).find((r) => r.country === scope.code);
    rec = found ? normalizeSalary(found) : undefined;
  }
  if (!rec) return undefined;
  if (!composite) return isEmpty(rec[parts[0].key]) ? undefined : rec[parts[0].key];
  const picked = pickParts(rec, parts);
  return isEmpty(picked) ? undefined : picked;
}

/** A copy of `block` with the row set to `value` (empty removes it). */
export function writeRow(spec: RowSpec, block: Block, value: unknown): unknown {
  const { scope, parts, composite } = spec;
  const apply = (rec: Record<string, unknown>): Record<string, unknown> => {
    const out = { ...rec };
    for (const part of parts) {
      const v = composite ? (isObj(value) ? value[part.key] : undefined) : value;
      if (isEmpty(v)) delete out[part.key];
      else out[part.key] = v;
    }
    return out;
  };
  if (scope.kind === "whole") return isEmpty(value) ? [] : value;
  if (scope.kind === "dict") return apply(isObj(block) ? block : {});
  if (scope.kind === "country") {
    const base = isObj(block) ? block : {};
    const list = recordsOf(base.countries);
    const at = list.findIndex((r) => r.country === scope.code);
    const next = apply(at >= 0 ? list[at] : { country: scope.code });
    const countries = at >= 0 ? list.map((r, i) => (i === at ? next : r)) : [...list, next];
    return { ...base, countries };
  }
  const list = recordsOf(block);
  const without = list.filter((r) => r.country !== scope.code);
  if (isEmpty(value)) return without;
  const rec: Record<string, unknown> = { country: scope.code, ...(isObj(value) ? value : {}) };
  if (rec.max === undefined && rec.min !== undefined) rec.max = rec.min;
  const at = list.findIndex((r) => r.country === scope.code);
  return at >= 0 ? list.map((r, i) => (i === at ? rec : r)) : [...list, rec];
}

// ---- Value <-> editor inputs ----------------------------------------------

function partToInput(part: Part, v: unknown): string {
  if (v === undefined || v === null) return "";
  switch (part.input) {
    case "yesno":
      return isPns(v) ? "pns" : v === true ? "yes" : v === false ? "no" : "";
    case "countries":
      return Array.isArray(v) ? v.join(",") : "";
    case "textarea":
      return Array.isArray(v) ? v.join("\n") : String(v);
    case "languages":
      return Array.isArray(v)
        ? recordsOf(v).map((r) => `${String(r.language)} (${String(r.level)})`).join(", ")
        : "";
    default:
      return String(v);
  }
}

/** The editor's starting text for each part. */
export function toInputs(spec: RowSpec, value: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of spec.parts) {
    const v = spec.composite ? (isObj(value) ? value[part.key] : undefined) : value;
    out[part.key] = partToInput(part, v);
  }
  return out;
}

/** Parse "English (fluent), German (basic)"; `null` when a piece is not understood. */
export function parseLanguages(text: string): { language: string; level: string }[] | null {
  const out: { language: string; level: string }[] = [];
  for (const piece of text.split(",").map((s) => s.trim()).filter(Boolean)) {
    const m = /^(.+?)\s*\(\s*([A-Za-z]+)\s*\)$/.exec(piece);
    const level = m?.[2].toLowerCase();
    if (!m || !level || !(LEVEL_OPTIONS as readonly string[]).includes(level)) return null;
    out.push({ language: m[1].trim(), level });
  }
  return out;
}

function inputToPart(part: Part, text: string): unknown {
  const t = text.trim();
  if (t === "") return undefined;
  switch (part.input) {
    case "yesno":
      return t === "pns" ? PNS : t === "yes";
    case "number":
      return Number(t);
    case "countries":
      return t.split(",").filter(Boolean);
    case "textarea":
      return text.split("\n").map((s) => s.trim()).filter(Boolean);
    case "languages":
      return parseLanguages(t) ?? undefined;
    default:
      return t;
  }
}

/** The value the editor's text stands for; `undefined` = cleared. */
export function fromInputs(spec: RowSpec, inputs: Record<string, string>): unknown {
  if (!spec.composite) return inputToPart(spec.parts[0], inputs[spec.parts[0].key] ?? "");
  const out: Record<string, unknown> = {};
  for (const part of spec.parts) {
    const v = inputToPart(part, inputs[part.key] ?? "");
    if (!isEmpty(v)) out[part.key] = v;
  }
  return isEmpty(out) ? undefined : out;
}

/** Why the editor text cannot be saved, or `null`. */
export function inputProblem(spec: RowSpec, inputs: Record<string, string>): string | null {
  for (const part of spec.parts) {
    const t = (inputs[part.key] ?? "").trim();
    if (part.input === "languages" && t && parseLanguages(t) === null) {
      return "Write each one like English (fluent). Levels: native, fluent, professional, basic.";
    }
    if (part.input === "number" && t && !Number.isFinite(Number(t))) return "Numbers only, please.";
  }
  if (spec.scope.kind === "salary") {
    const min = Number(inputs.min);
    const max = inputs.max?.trim() ? Number(inputs.max) : min;
    const any = Object.values(inputs).some((v) => v.trim());
    if (any && !(min > 0 && max >= min && (inputs.currency ?? "").trim().length === 3 && inputs.period)) {
      return "Salary needs a figure above 0, a 3-letter currency, and a period. Up to cannot be below From.";
    }
  }
  return null;
}

// ---- Showing a value -------------------------------------------------------

const SYMBOL: Record<string, string> = { GBP: "£", EUR: "€", USD: "$" };

function short(n: number): string {
  if (n >= 1000 && n % 100 === 0) return `${n / 1000}k`;
  return n.toLocaleString("en-GB");
}

/** "£80k–95k GBP" (one figure when min == max; never converted). */
export function salaryText(rec: Record<string, unknown>): string {
  const r = normalizeSalary(rec);
  const min = Number(r.min);
  const max = Number(r.max ?? r.min);
  if (!Number.isFinite(min)) return "";
  const cur = String(r.currency ?? "").toUpperCase();
  const sym = SYMBOL[cur] ?? "";
  const figures = min === max ? `${sym}${short(min)}` : `${sym}${short(min)}–${short(max)}`;
  const per = r.period === "month" ? " / month" : "";
  return `${figures}${cur ? ` ${cur}` : ""}${per}`;
}

function formatDateText(text: string): string {
  const full = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  const month = /^(\d{4})-(\d{2})$/.exec(text);
  const m = full ?? month;
  if (!m) return text;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, full ? Number(m[3]) : 1));
  return d.toLocaleDateString("en-GB", { day: full ? "numeric" : undefined, month: "short", year: "numeric", timeZone: "UTC" });
}

/** The row's value as a person reads it. Empty -> "". */
export function showRow(spec: RowSpec, value: unknown): string {
  if (isEmpty(value)) return "";
  if (spec.scope.kind === "salary") return isObj(value) ? salaryText(value) : "";
  if (spec.composite && isObj(value)) {
    if (spec.parts === ADDRESS_PARTS) {
      const place = [value.address_city, value.address_postcode].filter((x) => !isEmpty(x)).join(", ");
      const lines = isEmpty(value.address_lines) ? "" : "full address saved";
      return [place, lines].filter(Boolean).join(" · ");
    }
    return spec.parts.map((p) => value[p.key]).filter((x) => !isEmpty(x)).join(" ");
  }
  const key = spec.parts[0].key;
  if (isPns(value)) return PNS;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (key === "work_authorization") return WORK_AUTH_LABEL[String(value)] ?? String(value);
  if (key === "citizenship" && Array.isArray(value)) return value.map((c) => countryName(String(c))).join(", ");
  if (key === "languages" && Array.isArray(value)) {
    return recordsOf(value).map((r) => `${String(r.language)} (${String(r.level)})`).join(" · ");
  }
  if (key === "visa_expires" || key === "date_of_birth") return formatDateText(String(value));
  if (key === "travel_ok_pct") return `${String(value)}%`;
  return Array.isArray(value) ? value.join(", ") : String(value);
}

// ---- Provenance ------------------------------------------------------------

export interface HistoryRow { set_by: string; set_at: string; value?: unknown }
export interface Provenance { by: string; at: string; previous: unknown }

function stable(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stable).join(",")}]`;
  if (isObj(v)) {
    return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${stable(v[k])}`).join(",")}}`;
  }
  return JSON.stringify(v) ?? "undefined";
}
const same = (a: unknown, b: unknown) => stable(isEmpty(a) ? undefined : a) === stable(isEmpty(b) ? undefined : b);

/** Who last changed this row, when, and what it held before. Walks the
 *  history OLDEST -> NEWEST (the API sends newest first); a null row is an
 *  empty block. If the rebuilt value is not what is stored now, the history
 *  cannot vouch for it, so the answer is `null` (stay silent). */
export function fieldProvenance(
  history: HistoryRow[],
  read: (block: Block) => unknown,
  current: unknown,
): Provenance | null {
  let prev: unknown;
  let last: Provenance | null = null;
  for (const row of [...history].reverse()) {
    const val = read(row.value ?? null);
    if (!same(val, prev)) {
      last = { by: row.set_by, at: row.set_at, previous: prev };
      prev = val;
    }
  }
  return same(prev, current) ? last : null;
}

export function shortDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

/** "Saved by Claude, 3 Oct" / "You changed this, 5 Oct". */
export function provenanceText(p: Provenance): string {
  const who = whoLabel(p.by);
  const when = shortDate(p.at);
  return p.by === "web" ? `You changed this, ${when}` : `Saved by ${who.name}, ${when}`;
}
