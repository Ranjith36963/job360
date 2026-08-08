/**
 * Upload receipts must REACH THE SCREEN.
 *
 * The backend tests prove the API returns cv_filename / linkedin_filename /
 * github_username. They cannot prove the page renders them — and "a value
 * stored, returned, and shown to nobody" is the exact failure this repo has hit
 * before (CLAUDE.md rule #21, value-presence over schema-presence).
 *
 * Found on a live smoke test 2026-08-08: after an upload the only feedback was
 * a small tick in the card header. The owner's words: "I get a neon colour tick
 * mark but I didn't catch that" — and nothing named the file, so he could not
 * tell WHICH CV was on file or whether a re-upload had replaced it. GitHub had
 * no confirmation at all.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { CVUpload } from "./CVUpload";
import type { ProfileSummary, CVDetail } from "@/lib/types";

const RAW_CV = "Ada Lovelace\nMachine Learning Engineer\nBuilds production ML.";

function summary(over: Partial<ProfileSummary> = {}): ProfileSummary {
  return {
    is_complete: true,
    job_titles: ["ML Engineer"],
    skills_count: 2,
    cv_length: RAW_CV.length,
    has_linkedin: true,
    has_github: true,
    education: [],
    experience_level: "senior",
    cv_filename: "Ada_Lovelace_CV_2026.pdf",
    cv_uploaded_at: "2026-08-08T10:30:00+00:00",
    linkedin_filename: "Profile_Ada.pdf",
    linkedin_uploaded_at: "2026-08-07T09:00:00+00:00",
    github_username: "adalovelace",
    github_connected_at: "2026-08-06T08:00:00+00:00",
    github_repo_count: 14,
    ...over,
  } as ProfileSummary;
}

const cvDetail = {
  raw_text: RAW_CV,
  skills: ["Python", "PyTorch"],
  job_titles: ["ML Engineer"],
  companies: [],
  education: [],
  certifications: [],
  summary_text: "Builds production ML.",
  experience_text: "",
  name: "Ada Lovelace",
  headline: "Machine Learning Engineer",
  location: "London",
  achievements: [],
} as unknown as CVDetail;

function renderCard(s: ProfileSummary) {
  return render(
    <CVUpload
      onUpload={vi.fn()}
      onLinkedinUpload={vi.fn()}
      onGithubEnrich={vi.fn()}
      profile={s}
      cvDetail={cvDetail}
      loading={false}
    />
  );
}

describe("upload receipts reach the screen", () => {
  it("names the actual CV file and when it arrived", () => {
    renderCard(summary());
    const receipts = screen.getAllByTestId("upload-receipt");
    // One per input: CV, LinkedIn, GitHub.
    expect(receipts).toHaveLength(3);
    expect(within(receipts[0]).getByText("Ada_Lovelace_CV_2026.pdf")).toBeTruthy();
    // The date is rendered in the viewer's locale — assert the YEAR, not an
    // exact format string, so this does not break on a different locale.
    expect(receipts[0].textContent).toMatch(/added/i);
    expect(receipts[0].textContent).toContain("2026");
  });

  it("names the LinkedIn file", () => {
    renderCard(summary());
    const receipts = screen.getAllByTestId("upload-receipt");
    expect(within(receipts[1]).getByText("Profile_Ada.pdf")).toBeTruthy();
  });

  it("shows the GitHub handle and repo count — its receipt has no file", () => {
    renderCard(summary());
    const receipts = screen.getAllByTestId("upload-receipt");
    expect(within(receipts[2]).getByText("@adalovelace")).toBeTruthy();
    expect(receipts[2].textContent).toContain("14 public repos read");
  });

  it("a re-upload shows the NEW name, never the old one", () => {
    const { rerender } = renderCard(summary());
    rerender(
      <CVUpload
        onUpload={vi.fn()}
        onLinkedinUpload={vi.fn()}
        onGithubEnrich={vi.fn()}
        profile={summary({ cv_filename: "Ada_CV_v2.pdf" })}
        cvDetail={cvDetail}
        loading={false}
      />
    );
    const receipts = screen.getAllByTestId("upload-receipt");
    expect(receipts[0].textContent).toContain("Ada_CV_v2.pdf");
    expect(receipts[0].textContent).not.toContain("Ada_Lovelace_CV_2026.pdf");
  });

  it("a profile saved before receipts existed degrades to a plain state", () => {
    // Old rows load with empty strings. They must read as "on file" and must
    // NEVER render undefined / NaN / Invalid Date.
    renderCard(
      summary({
        cv_filename: "",
        cv_uploaded_at: "",
        linkedin_filename: "",
        linkedin_uploaded_at: "",
        github_username: "",
        github_connected_at: "",
        github_repo_count: 0,
      })
    );
    const receipts = screen.getAllByTestId("upload-receipt");
    expect(receipts[0].textContent).toContain("CV on file");
    expect(receipts[1].textContent).toContain("LinkedIn profile on file");
    expect(receipts[2].textContent).toContain("GitHub connected");
    for (const r of receipts) {
      expect(r.textContent).not.toMatch(/undefined|NaN|Invalid Date/);
    }
  });

  it("a bad timestamp never renders 'Invalid Date'", () => {
    renderCard(summary({ cv_uploaded_at: "not-a-date" }));
    const receipts = screen.getAllByTestId("upload-receipt");
    expect(receipts[0].textContent).toContain("Ada_Lovelace_CV_2026.pdf");
    expect(receipts[0].textContent).not.toMatch(/Invalid Date/);
  });

  it("no receipt is shown for an input that was never provided", () => {
    renderCard(summary({ has_linkedin: false, has_github: false }));
    // Only the CV receipt remains — an absent input stays silent (rule #29).
    expect(screen.getAllByTestId("upload-receipt")).toHaveLength(1);
  });
});
