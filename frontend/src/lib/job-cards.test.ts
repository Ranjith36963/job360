import { describe, expect, it } from "vitest";
import type { ApplicationDetail, Ask } from "@/lib/api";
import { buildCard, fallbackGuess, initialRow, groupAsks, isSensitiveQuestion, isSimulation, linkedinFor, mergeQuestions, peopleToReach, rowKind, sortCards } from "./job-cards";

let nextId = 1;
function ask(over: Partial<Ask> = {}): Ask {
  return {
    id: nextId++, application_id: 7, job_title: "AI Engineer", job_company: "Acme", question: "Why us?",
    context: "", asked_by: "Claude", asked_at: "2026-10-20T10:00:00+00:00", answer: null, answered_by: null,
    answered_at: null, answered_by_user: false, withdrawn_at: null, status: "open", ...over,
  } as Ask;
}

const msg = (text: string, at: string) => ({ text, occurred_at: at });
function detail(over: Record<string, unknown> = {}): ApplicationDetail {
  return {
    id: 7, updated_at: "2026-10-20T09:00:00+00:00", form_state: null, artifacts: [], contacts: [],
    job: { job_company: "Acme", job_title: "AI Engineer", job_url: "https://jobs.example.com/7" },
    ...over,
  } as unknown as ApplicationDetail;
}

describe("rowKind", () => {
  it.each([
    ["Please solve the CAPTCHA and press Submit", "captcha"],
    ["The browser could not upload your CV", "upload"],
    ["Sign in to Workday so I can continue", "login"],
    ["Stopped at a safety block on the last page", "safety"],
    ["Send this LinkedIn message yourself", "outreach"],
    ["Do you need visa sponsorship for Germany?", "yes_no"],
    ["Expected yearly salary (EUR)", "question"],
    ["Your assistant got stuck: a bot check at Indeed Apply button on uk.indeed.com.", "captcha"],
    ["Certa asks: \"Do you meet this requirement?\" You live in London now. Yes or No?", "yes_no"],
    ["Is it BJAK's confirmation? Reply yes or no.", "yes_no"],
  ])("%s -> %s", (question, kind) => {
    expect(rowKind(ask({ question }))).toBe(kind);
  });
});

describe("rowKind with a stored kind", () => {
  it("trusts the stored kind over the words, and lists questions as structured", () => {
    expect(rowKind(ask({ question: "Please check the form", kind: "upload" } as Partial<Ask>))).toBe("upload");
    expect(rowKind(ask({ question: "Solve the CAPTCHA", kind: "question" } as Partial<Ask>))).toBe("question");
    const listed = { question: "Form answers", kind: "question", questions: [{ key: "a", question: "A?", answer: "x", source: "cv" }] };
    expect(rowKind(ask(listed as unknown as Partial<Ask>))).toBe("structured");
  });
});

describe("groupAsks", () => {
  it("puts every ask for one job on one card, keeps job-less asks apart and drops practice asks", () => {
    const a = ask({ application_id: 7 });
    const b = ask({ application_id: 7, question: "Solve the CAPTCHA" });
    const general = ask({ application_id: null });
    const sim = ask({ application_id: 9, question: "SIMULATION lesson: have numbers ready" });
    const { byJob, general: rest } = groupAsks([a, b, general, sim]);
    expect([...byJob.keys()]).toEqual([7]);
    expect(byJob.get(7)).toEqual([a, b]);
    expect(rest).toEqual([general]);
    expect(isSimulation(ask({ job_title: "LIFECYCLE TEST - do not apply" }))).toBe(true);
  });
});

describe("people to reach", () => {
  const contact = (over: Record<string, unknown>) => ({
    id: 1, name: "Ada Lovelace", role: "CTO", company: null, linkedin_url: "", found_via: "company_site",
    outreach: { messages: [], last_sent: null }, ...over,
  });

  it("lists a drafted message only until it is marked sent", () => {
    const drafted = contact({ outreach: { messages: [msg("Hi Ada", "2026-10-20T10:00:00Z")], last_sent: null } });
    const sent = contact({ id: 2, outreach: { messages: [msg("Hi", "2026-10-20T10:00:00Z")], last_sent: msg("", "2026-10-21T10:00:00Z") } });
    const redrafted = contact({ id: 3, outreach: { messages: [msg("New", "2026-10-22T10:00:00Z")], last_sent: msg("", "2026-10-21T10:00:00Z") } });
    const ids = peopleToReach(detail({ contacts: [drafted, sent, redrafted] })).map((p) => p.contact.id);
    expect(ids).toEqual([1, 3]);
  });

  it("opens the saved LinkedIn profile, else a people search for name and company", () => {
    expect(linkedinFor(contact({ linkedin_url: "https://www.linkedin.com/in/ada" }) as never, "Acme")).toBe("https://www.linkedin.com/in/ada");
    expect(linkedinFor(contact({ linkedin_url: "javascript:alert(1)" }) as never, "Acme")).toBe(
      "https://www.linkedin.com/search/results/people/?keywords=Ada%20Lovelace%20Acme",
    );
  });
});

describe("buildCard and sortCards", () => {
  it("never links a form URL that is not http(s), and finds the newest CV", () => {
    const card = buildCard(
      detail({
        job: { job_company: "Acme", job_title: "AI Engineer", job_url: "javascript:alert(1)" },
        artifacts: [{ id: 3, kind: "cv", version_no: 1 }, { id: 5, kind: "cv", version_no: 2 }, { id: 6, kind: "outreach", version_no: 9 }],
      }),
      [ask()],
      null,
    );
    expect(card.formUrl).toBe("");
    expect(card.latestCv).toEqual({ artifactId: 5, version: 2 });
  });

  it("puts a blocker on a filled form first, then blockers, then questions", () => {
    const questions = buildCard(detail({ id: 1 }), [ask({ asked_at: "2026-10-01T00:00:00Z" })], null);
    const blocked = buildCard(detail({ id: 2 }), [ask({ question: "Solve the CAPTCHA" })], null);
    const ready = buildCard(detail({ id: 3, form_state: "filled" }), [ask({ question: "Attach your CV: upload failed" })], null);
    expect(sortCards([questions, blocked, ready]).map((c) => c.applicationId)).toEqual([3, 2, 1]);
  });
});

describe("mergeQuestions", () => {
  const q = (key: string, question: string, answer: string, source: string) => ({ key, question, answer, source });
  const withQs = (questions: unknown[]) => ask({ questions } as Partial<Ask>);

  it("dedupes by key, falls back to normalised text, and keeps the best copy", () => {
    const a = withQs([q("city", "City?", "", "none"), q("", "Phone  number?", "1", "guess")]);
    const b = withQs([q("city", "City?", "Berlin", "profile"), q("", " phone NUMBER? ", "2", "memory")]);
    const m = mergeQuestions([a, b]);
    expect(m.rows).toHaveLength(2);
    expect(m.rows[0].q.answer).toBe("Berlin");
    expect(m.rows[0].askId).toBe(b.id);
    expect(m.rows[0].askIds).toEqual([a.id, b.id]);
    expect(m.rows[1].q.source).toBe("memory");
    expect(m.byAsk.get(a.id)).toEqual([{ key: "city", rowId: "city" }, { key: "", rowId: "phone number?" }]);
  });
});

describe("isSensitiveQuestion", () => {
  const s = (key: string, question: string, sensitive?: boolean) => isSensitiveQuestion({ key, question, sensitive });
  it("flags consent and personal categories", () => {
    expect(s("gdpr_consent", "OK?")).toBe(true);
    expect(s("x", "Ethnic origin")).toBe(true);
    expect(s("x", "Do you have a disability?")).toBe(true);
    expect(s("date_of_birth", "When?")).toBe(true);
    expect(s("x", "Anything", true)).toBe(true);
  });
  it("leaves ordinary and right-to-work questions alone", () => {
    expect(s("city", "Where do you live?")).toBe(false);
    expect(s("rtw", "Do you have the right to work in Germany?")).toBe(false);
    expect(s("x", "Describe a trace of your work")).toBe(false);
  });
});

describe("needsOwnBrowser", () => {
  it("is true only when browser.kind is own_browser; absent or other kinds are Job360's browser", async () => {
    const { needsOwnBrowser } = await import("./job-cards");
    const a = { question: "q" } as never;
    const b = (kind: string) => ({ ...(a as object), browser: { kind, label: "l", reason: "r" } }) as never;
    expect(needsOwnBrowser(a)).toBe(false);
    expect(needsOwnBrowser(b("job360"))).toBe(false);
    expect(needsOwnBrowser(b("human"))).toBe(false);
    expect(needsOwnBrowser(b("bogus"))).toBe(false);
    expect(needsOwnBrowser(b("own_browser"))).toBe(true);
  });
});

describe("fallbackGuess", () => {
  const g = (question: string, choices?: string[], key = "q") => fallbackGuess({ key, question, choices });
  it("guesses Yes for data-processing / privacy consent and I agree", () => {
    expect(g("I agree to the processing of my personal data")).toBe("Yes");
    expect(g("Do you consent to our privacy policy?")).toBe("Yes");
    expect(g("Anything", undefined, "gdpr_consent")).toBe("Yes");
  });
  it("guesses No for referred by an employee", () => {
    expect(g("Were you referred by an employee?")).toBe("No");
    expect(g("Do you have an employee referral?")).toBe("No");
  });
  it("leaves unrelated or negated questions alone", () => {
    expect(g("Why do you want to work here?")).toBe("");
    expect(g("Expected salary")).toBe("");
    expect(g("Do you object to data processing?")).toBe("");
  });
  it("uses the matching choice text, or nothing when no choice fits", () => {
    expect(g("Were you referred by an employee?", ["Yes, by someone", "No, I was not"])).toBe("No, I was not");
    expect(g("I agree to the privacy policy", ["Maybe", "Later"])).toBe("");
  });
});

describe("initialRow", () => {
  it("starts Verified sources checked and Guessed or empty ones unchecked", () => {
    expect(initialRow({ key: "city", question: "City", answer: "x", source: "profile" }).ok).toBe(true);
    expect(initialRow({ key: "city", question: "City", answer: "x", source: "memory" }).ok).toBe(true);
    expect(initialRow({ key: "city", question: "City", answer: "x", source: "cv" }).ok).toBe(true);
    expect(initialRow({ key: "city", question: "City", answer: "x", source: "guess" }).ok).toBe(false);
    expect(initialRow({ key: "city", question: "City", answer: "x", source: "draft" }).ok).toBe(false);
    expect(initialRow({ key: "city", question: "City", answer: "", source: "profile" }).ok).toBe(false);
  });
});

describe("initialRow never pre-checks a sensitive row", () => {
  it("a consent or EEO question claiming a profile source still waits for the owner's click", async () => {
    const { initialRow } = await import("./job-cards");
    expect(initialRow({ key: "gdpr", question: "Do you consent to data processing?", answer: "Yes", source: "profile" }).ok).toBe(false);
    expect(initialRow({ key: "gender", question: "Gender", answer: "Male", source: "memory" }).ok).toBe(false);
    expect(initialRow({ key: "city", question: "City", answer: "London", source: "profile" }).ok).toBe(true);
  });
});
