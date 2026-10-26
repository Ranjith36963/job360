import { describe, expect, it } from "vitest";
import type { ReadyAnswer, ReadyFlag } from "@/lib/api";
import { askRowLabel, flagsFor, readyRowLabel } from "@/lib/answer-status";
import { dayMonth } from "@/lib/ready-to-send";

// One row per line of the label mapping table (Needs-you redesign, PR 1).

const q = (over: Record<string, unknown> = {}) => ({ key: "city", question: "City?", answer: "Berlin", source: "guess", ...over }) as {
  key: string; question: string; answer: string; source: string; choices?: string[] | null; remembered_on?: string | null;
};
const ask = (over: Record<string, unknown>, row?: { value: string; ok: boolean }, editing = false) => {
  const item = q(over);
  return askRowLabel(item, row ?? { value: item.answer, ok: false }, editing);
};

describe("askRowLabel: the job card's rows", () => {
  it("a saved fact reads OK with where it came from", () => {
    expect(ask({ source: "profile" })).toEqual({ status: "ok", source: "From your profile" });
    expect(ask({ source: "cv" })).toEqual({ status: "ok", source: "From your CV" });
    expect(ask({ source: "memory", remembered_on: "2026-10-03" })).toEqual({
      status: "ok", source: `From your Memory · saved ${dayMonth("2026-10-03")}`,
    });
    expect(ask({ source: "memory" })).toEqual({ status: "ok", source: "From your Memory" });
    expect(ask({ source: "answered" })).toEqual({ status: "ok", source: "You wrote this" });
  });

  it("a guess or a draft reads Check until the owner's OK", () => {
    expect(ask({ source: "guess" })).toEqual({ status: "check", source: "Your assistant's guess" });
    expect(ask({ source: "draft" })).toEqual({ status: "check", source: "Written by your assistant" });
    expect(ask({ source: "guess" }, { value: "Berlin", ok: true })).toEqual({ status: "ok", source: "You confirmed this" });
  });

  it("an empty row reads Needs you, split by why", () => {
    expect(ask({ source: "not_known", answer: "" })).toEqual({ status: "needs_you", source: "Your assistant could not guess this" });
    expect(ask({ source: "none", answer: "" })).toEqual({ status: "needs_you", source: "Your assistant will draft this, or write it now" });
    expect(ask({ source: "none", answer: "", choices: ["Yes", "No"] }).status).toBe("needs_you");
  });

  it("a stored draft note reads Needs you, but not while the owner edits it", () => {
    const stub = { question: "Structured JSON outputs", answer: "drafted" };
    expect(ask(stub)).toEqual({ status: "needs_you", source: "Draft missing, write it or ask again" });
    expect(ask(stub, { value: "drafted", ok: false }, true).status).toBe("check");
  });

  it("the owner's own change reads OK, You changed this", () => {
    expect(ask({ source: "guess" }, { value: "Paris", ok: true })).toEqual({ status: "ok", source: "You changed this" });
    expect(ask({ source: "none", answer: "" }, { value: "Paris", ok: true })).toEqual({ status: "ok", source: "You wrote this" });
  });

  it("a sensitive row is Needs you until the owner's own tap, whatever its source", () => {
    const gender = { key: "gender", question: "Gender", answer: "Female" };
    expect(ask({ ...gender, source: "profile" })).toEqual({ status: "needs_you", source: "From your profile · only you can choose" });
    expect(ask({ ...gender, source: "guess" }).status).toBe("needs_you");
    expect(ask({ ...gender, source: "profile" }, { value: "Female", ok: true })).toEqual({ status: "ok", source: "From your profile" });
  });

  it("a choice row with a pre-guess says the guess in words, never as a pressed button", () => {
    const consent = { key: "gdpr", question: "Do you consent to data processing?", answer: "Yes", source: "guess", choices: ["Yes", "No"] };
    expect(ask(consent)).toEqual({ status: "needs_you", source: "Your assistant's guess: Yes · only you can choose" });
  });
});

const ra = (over: Record<string, unknown> = {}) =>
  ({ question: "Visa?", answer: "no", source: "guessed", key: null, verified: false, redacted: false, saved_by: null, saved_at: null, check: "verify", ...over }) as ReadyAnswer;
const flag = (code: string, question: string, key: string | null = null): ReadyFlag => ({ code, text: `x: ${question}`, key });
const ready = (a: ReadyAnswer, flags: ReadyFlag[] = []) => readyRowLabel(a, { flags });

describe("readyRowLabel: the ready card's rows read the backend flags", () => {
  it("an unconfirmed guess with no matching flag still reads Check, never OK", () => {
    expect(ready(ra())).toEqual({ status: "check", source: "Your assistant's guess" });
    expect(ready(ra({ source: "written", answer: "I like it." }))).toEqual({ status: "check", source: "Written by your assistant" });
  });

  it("a guessed flag reads Check, Your assistant's guess", () => {
    expect(ready(ra(), [flag("guessed", "Visa?")])).toEqual({ status: "check", source: "Your assistant's guess" });
  });

  it("an approved answer with an unapproved flag reads Check, Written by your assistant", () => {
    const a = ra({ source: "approved", key: "why_us", answer: "I build agents.", saved_at: "2026-10-01T09:00:00Z" });
    expect(ready(a, [flag("unapproved", "Visa?", "why_us")])).toEqual({ status: "check", source: "Written by your assistant" });
  });

  it("an approved answer without that flag reads OK, You approved this · date", () => {
    const a = ra({ source: "approved", key: "why_us", answer: "I build agents.", verified: true, saved_at: "2026-10-01T09:00:00Z" });
    expect(ready(a)).toEqual({ status: "ok", source: `You approved this · ${dayMonth("2026-10-01T09:00:00Z")}` });
  });

  it("a verified guess with no flag reads OK, You confirmed this", () => {
    expect(ready(ra({ verified: true }))).toEqual({ status: "ok", source: "You confirmed this" });
  });

  it("a sensitive flag reads Needs you, only you can choose", () => {
    const a = ra({ question: "Gender", answer: "Female", source: "memory", key: "equality.gender", saved_by: "agent:Claude" });
    expect(ready(a, [flag("sensitive", "Gender", "equality.gender")])).toEqual({
      status: "needs_you", source: "From your Memory · your assistant saved it · only you can choose",
    });
  });

  it("a blank answer reads Needs you, Blank on the form", () => {
    expect(ready(ra({ answer: "" }), [flag("blank", "Visa?")])).toEqual({ status: "needs_you", source: "Blank on the form" });
  });

  it("a private answer stored hidden reads OK: it WAS typed on the form", () => {
    expect(ready(ra({ answer: "", redacted: true, source: "approved" }))).toEqual({ status: "ok", source: "Typed on the form, hidden for privacy" });
  });

  it("Memory reads OK: saved by the user with its date, or by the assistant", () => {
    expect(ready(ra({ source: "memory", key: "k", verified: true, saved_by: "web", saved_at: "2026-10-03T09:00:00Z" }))).toEqual({
      status: "ok", source: `From your Memory · saved ${dayMonth("2026-10-03T09:00:00Z")}`,
    });
    expect(ready(ra({ source: "memory", key: "k", saved_by: "agent:Claude" }))).toEqual({ status: "ok", source: "From your Memory · your assistant saved it" });
  });

  it("a profile answer reads OK, From your profile", () => {
    expect(ready(ra({ source: "profile", key: "contact.name" }))).toEqual({ status: "ok", source: "From your profile" });
  });

  it("an answer that will be typed instead reads OK: the owner's edit or the saved answer", () => {
    expect(ready(ra({ changed_to: "Yes", changed_reason: "owner_edit", verified: true }), [flag("changed", "Visa?")])).toEqual({
      status: "ok", source: "You changed this",
    });
    for (const reason of ["saved_answer", "approved_for_employer"]) {
      expect(ready(ra({ changed_to: "Yes", changed_reason: reason, verified: true }))).toEqual({
        status: "ok", source: "From your Memory · will be typed before sending",
      });
    }
  });

  it("a stored draft note reads Needs you", () => {
    expect(ready(ra({ question: "Structured JSON outputs", answer: "drafted" }))).toEqual({
      status: "needs_you", source: "Draft missing, write it or ask again",
    });
  });
});

describe("flagsFor", () => {
  it("matches by key, and a key-less row by the question its flag names", () => {
    const flags = [flag("guessed", "Visa?"), flag("guessed", "Notice?"), flag("sensitive", "Gender", "equality.gender")];
    expect(flagsFor({ key: null, question: "Visa?" }, flags).map((f) => f.text)).toEqual(["x: Visa?"]);
    expect(flagsFor({ key: "equality.gender", question: "Gender" }, flags).map((f) => f.code)).toEqual(["sensitive"]);
    expect(flagsFor({ key: "other", question: "Visa?" }, flags)).toEqual([]);
  });
});
