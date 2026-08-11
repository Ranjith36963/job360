/**
 * Every LinkedIn section must reach the screen.
 *
 * AUDIT, 2026-08-09. ``_SECTION_HEADINGS`` recognises 20 LinkedIn headings, but
 * only 11 had an extractor and a shelf. The other seven — Honors & Awards,
 * Publications, Patents, Organizations, Test Scores, Recommendations, Interests
 * — were split out purely so they acted as boundaries, then discarded. The
 * Contact block was parsed only to confirm the file WAS a LinkedIn export.
 *
 * Same shape as the 92 GitHub signals that were stored and rendered nowhere,
 * on the other input.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CVViewer } from "./CVViewer";
import type { CVDetail } from "@/lib/types";

const cv = {
  raw_text: "Ada Lovelace",
  skills: [],
  job_titles: [],
  companies: [],
  education: [],
  certifications: [],
  summary_text: "",
  experience_text: "",
  name: "Ada Lovelace",
  headline: "Engineer",
  location: "London",
  achievements: [],
} as unknown as CVDetail;

function renderWith(sub: Record<string, Record<string, unknown>[]>) {
  render(<CVViewer cv={cv} linkedinSubsections={sub} />);
}

describe("LinkedIn detail shows every section", () => {
  it("renders the structured Contact block", () => {
    renderWith({
      contact: [
        {
          email: "ada@example.com",
          phone: "+447442564327",
          linkedin_url: "linkedin.com/in/ada",
          websites: ["ada.dev"],
        },
      ],
    });
    // The full address, not a truncated local part — an existing _EMAIL_RE with
    // a capture group once shadowed the contact one and stored "ada".
    expect(screen.getByText("ada@example.com")).toBeTruthy();
    expect(screen.getByText("+447442564327")).toBeTruthy();
    expect(screen.getByText("ada.dev")).toBeTruthy();
  });

  it("renders recommendations as third-party evidence, with attribution", () => {
    renderWith({
      recommendations: [
        { author: "Grace H.", relationship: "managed directly", text: "Ada shipped the compiler." },
      ],
    });
    expect(screen.getByText("Ada shipped the compiler.")).toBeTruthy();
    expect(screen.getByText(/Grace H\. · managed directly/)).toBeTruthy();
  });

  it("renders publications, patents, honors, organisations and test scores", () => {
    renderWith({
      publications: [{ title: "On Computable Numbers", publisher: "LMS", date: "1936" }],
      patents: [{ title: "Analytical Engine", number: "GB1234", status: "Issued" }],
      honors: [{ title: "Fellow of the Royal Society", issuer: "RS", date: "1840" }],
      organizations: [{ name: "BCS", role: "Member", start: "2020" }],
      test_scores: [{ name: "IELTS", score: "8.0", date: "2021" }],
    });
    expect(screen.getByText("On Computable Numbers")).toBeTruthy();
    expect(screen.getByText("Analytical Engine")).toBeTruthy();
    expect(screen.getByText("Fellow of the Royal Society")).toBeTruthy();
    expect(screen.getByText("BCS")).toBeTruthy();
    // IELTS is UK-visa relevant and stated by no other input.
    expect(screen.getByText(/IELTS · 8\.0/)).toBeTruthy();
  });

  it("renders the LinkedIn About section", () => {
    // This text is the person's own first-person prose. It was discarded
    // whenever the CV already carried a summary — so most profiles lost it —
    // and once it finally had a shelf it went straight to the judge and the
    // vector while remaining invisible on screen. Extracted, stored and matched
    // on but never shown is the third way a shelf goes dark.
    renderWith({
      summary: [{ text: "AI/ML Engineer building production GenAI systems." }],
    });
    expect(
      screen.getByText(/AI\/ML Engineer building production GenAI systems\./),
    ).toBeTruthy();
  });

  it("stays silent when there is no About text", () => {
    renderWith({ summary: [{ text: "" }] });
    expect(screen.queryByText("About")).not.toBeInTheDocument();
  });

  it("renders interests", () => {
    renderWith({ interests: [{ name: "DeepMind" }, { name: "Royal Society" }] });
    expect(screen.getByText("DeepMind")).toBeTruthy();
  });

  it("stays SILENT for a profile with none of these (rule #29)", () => {
    render(<CVViewer cv={cv} />);
    expect(screen.queryByText("LinkedIn detail")).not.toBeInTheDocument();
  });

  it("renders only the sections present — a sparse profile shows no empty headings", () => {
    renderWith({ publications: [{ title: "Solo Paper" }] });
    expect(screen.getByText("Solo Paper")).toBeTruthy();
    expect(screen.queryByText(/Patents/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Test scores/)).not.toBeInTheDocument();
    expect(screen.queryByText("Contact")).not.toBeInTheDocument();
  });

  it("never renders undefined from a malformed row", () => {
    renderWith({
      publications: [{ title: "Half Row", publisher: null, date: undefined }],
      honors: [{ issuer: "no title here" }],
    });
    expect(screen.getByText("Half Row")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/undefined|NaN|null/);
  });
});
