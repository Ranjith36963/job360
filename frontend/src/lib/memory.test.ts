import { describe, it, expect } from "vitest";
import {
  CONTACT, RTW, SALARY, LANGUAGES, contactRows, equalityRows, fieldProvenance, fromInputs, inputProblem,
  isSensitive, parseLanguages, provenanceText, readRow, rtwCountryRows, rtwTopRows, salaryRow, salaryText,
  showRow, toInputs, writeRow, logisticsRows,
} from "./memory";

const phone = contactRows().find((r) => r.id.endsWith(".phone"))!;
const readPhone = (b: unknown) => readRow(phone, b);
const T1 = "2026-10-03T12:00:00Z";
const T2 = "2026-10-05T12:00:00Z";

describe("fieldProvenance", () => {
  it("assistant first, then the web: last change wins and remembers what it replaced", () => {
    const history = [
      { set_by: "web", set_at: T2, value: { phone: "+44 7700 900123" } },
      { set_by: "agent:Claude", set_at: T1, value: { phone: "+44 1" } },
    ];
    const prov = fieldProvenance(history, readPhone, "+44 7700 900123");
    expect(prov).toEqual({ by: "web", at: T2, previous: "+44 1" });
    expect(provenanceText(prov!)).toMatch(/^You changed this, \d{1,2} Oct$/);
  });

  it("names the assistant exactly", () => {
    const history = [{ set_by: "agent:Claude Code", set_at: T1, value: { phone: "+44 1" } }];
    const prov = fieldProvenance(history, readPhone, "+44 1");
    expect(provenanceText(prov!)).toMatch(/^Saved by Claude Code, \d{1,2} Oct$/);
    expect(prov!.previous).toBeUndefined();
  });

  it("a cleared row is an empty block: the next change has no 'was'", () => {
    const history = [
      { set_by: "web", set_at: T2, value: { phone: "+44 2" } },
      { set_by: "web", set_at: "2026-10-04T12:00:00Z", value: null },
      { set_by: "agent:Claude", set_at: T1, value: { phone: "+44 1" } },
    ];
    expect(fieldProvenance(history, readPhone, "+44 2")).toMatchObject({ by: "web", previous: undefined });
  });

  it("an edit to a DIFFERENT key does not move this row's mark", () => {
    const history = [
      { set_by: "web", set_at: T2, value: { phone: "+44 1", email: "a@b.co" } },
      { set_by: "agent:Claude", set_at: T1, value: { phone: "+44 1" } },
    ];
    expect(fieldProvenance(history, readPhone, "+44 1")).toMatchObject({ by: "agent:Claude", at: T1 });
  });

  it("stays silent when the history cannot explain today's value", () => {
    const history = [{ set_by: "agent:Claude", set_at: T1, value: { phone: "+44 1" } }];
    expect(fieldProvenance(history, readPhone, "+44 999")).toBeNull();
    expect(fieldProvenance([], readPhone, "+44 999")).toBeNull();
  });
});

describe("readRow / writeRow", () => {
  const sponsor = rtwCountryRows("DE").find((r) => r.id.endsWith("needs_sponsorship"))!;

  it("writes the whole block: a country field keeps its siblings, false is kept", () => {
    const block = { countries: [{ country: "GB", visa_type: "Graduate" }], citizenship: ["IN"] };
    const out = writeRow(sponsor, block, false) as { countries: Record<string, unknown>[]; citizenship: string[] };
    expect(out.citizenship).toEqual(["IN"]);
    expect(out.countries).toEqual([{ country: "GB", visa_type: "Graduate" }, { country: "DE", needs_sponsorship: false }]);
    expect(readRow(sponsor, out)).toBe(false);
  });

  it("empty removes the key, never writes a blank", () => {
    const block = { phone: "+44 1", email: "a@b.co" };
    expect(writeRow(phone, block, undefined)).toEqual({ email: "a@b.co" });
  });

  it("full name is two keys, one row", () => {
    const name = contactRows()[0];
    const out = writeRow(name, {}, { legal_first_name: "Ada", legal_last_name: "Lovelace" });
    expect(out).toEqual({ legal_first_name: "Ada", legal_last_name: "Lovelace" });
    expect(showRow(name, readRow(name, out))).toBe("Ada Lovelace");
  });

  it("salary: one record per country; an old 'amount' record reads as min = max and is rewritten as min/max", () => {
    const de = salaryRow("DE");
    const list = [{ country: "GB", amount: 80000, currency: "GBP", period: "year" }];
    expect(readRow(salaryRow("GB"), list)).toEqual({ min: 80000, max: 80000, currency: "GBP", period: "year" });
    const out = writeRow(de, list, { min: 85000, max: 95000, currency: "EUR", period: "year" }) as unknown[];
    expect(out).toEqual([list[0], { country: "DE", min: 85000, max: 95000, currency: "EUR", period: "year" }]);
    expect(writeRow(de, out, undefined)).toEqual([list[0]]);
  });

  it("languages: the list is the value", () => {
    const lang = logisticsRows()[2];
    expect(lang.path).toBe(LANGUAGES);
    expect(parseLanguages("English (fluent), Tamil (Native)")).toEqual([
      { language: "English", level: "fluent" }, { language: "Tamil", level: "native" },
    ]);
    expect(parseLanguages("English (great)")).toBeNull();
    expect(showRow(lang, [{ language: "English", level: "fluent" }])).toBe("English (fluent)");
  });
});

describe("editor text", () => {
  it("round-trips yes / no / Prefer not to say", () => {
    const sanctions = rtwTopRows()[1];
    expect(fromInputs(sanctions, { sanctions_country_citizen: "pns" })).toBe("Prefer not to say");
    expect(fromInputs(sanctions, { sanctions_country_citizen: "no" })).toBe(false);
    expect(toInputs(sanctions, "Prefer not to say")).toEqual({ sanctions_country_citizen: "pns" });
    expect(fromInputs(sanctions, { sanctions_country_citizen: "" })).toBeUndefined();
  });

  it("salary: a missing 'up to' means one figure; bad ranges are refused before sending", () => {
    const sal = salaryRow("GB");
    const ok = { min: "80000", max: "", currency: "gbp", period: "year" };
    expect(inputProblem(sal, ok)).toBeNull();
    expect(inputProblem(sal, { ...ok, max: "70000" })).toMatch(/Up to cannot be below From/);
    expect(inputProblem(sal, { ...ok, currency: "pounds" })).not.toBeNull();
  });
});

describe("showing values", () => {
  it("salary range with the currency, never converted", () => {
    expect(salaryText({ min: 80000, max: 95000, currency: "GBP", period: "year" })).toBe("£80k–95k GBP");
    expect(salaryText({ min: 85000, max: 95000, currency: "EUR", period: "year" })).toBe("€85k–95k EUR");
    expect(salaryText({ min: 85000, max: 85000, currency: "EUR", period: "year" })).toBe("€85k EUR");
    expect(salaryText({ amount: 25000, currency: "AED", period: "month" })).toBe("25k AED / month");
  });

  it("sensitive = visa, equality, salary, birth date, address; not phone", () => {
    expect(isSensitive(RTW, "visa_type")).toBe(true);
    expect(isSensitive(SALARY, "x")).toBe(true);
    expect(isSensitive(CONTACT, "date_of_birth")).toBe(true);
    expect(isSensitive(CONTACT, "address")).toBe(true);
    expect(isSensitive(CONTACT, "phone")).toBe(false);
    expect(equalityRows().every((r) => r.sensitive)).toBe(true);
    expect(phone.sensitive).toBe(false);
  });
});
