/**
 * CVUpload — owner-approved copy pass (2026-09-27).
 *
 * Rule #29: an empty shelf stays silent. "Roles: 0" / "Education: 0" used to
 * print a real zero for fields the extractor hasn't reached yet, reading as
 * "you have none" rather than "not read yet". And the skills a CV upload
 * actually found were never listed anywhere on this card — only a count.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { CVUpload } from "./CVUpload";
import type { ProfileSummary, CVDetail } from "@/lib/types";

function summary(over: Partial<ProfileSummary> = {}): ProfileSummary {
  return {
    is_complete: true,
    job_titles: [],
    skills_count: 2,
    cv_length: 100,
    has_linkedin: false,
    has_github: false,
    education: [],
    experience_level: "",
    ...over,
  } as ProfileSummary;
}

function cvDetail(over: Partial<CVDetail> = {}): CVDetail {
  return {
    raw_text: "",
    skills: [],
    job_titles: [],
    companies: [],
    education: [],
    certifications: [],
    summary_text: "",
    experience_text: "",
    name: "",
    headline: "",
    location: "",
    achievements: [],
    ...over,
  } as unknown as CVDetail;
}

function renderCard(s: ProfileSummary, cv: CVDetail) {
  return render(
    <CVUpload
      onUpload={vi.fn()}
      onLinkedinUpload={vi.fn()}
      onGithubEnrich={vi.fn()}
      profile={s}
      cvDetail={cv}
      loading={false}
    />
  );
}

describe("CVUpload — roles/history stay silent when empty (rule #29)", () => {
  it("shows a plain note instead of 'Roles: 0' / 'Education: 0' when both are empty", () => {
    renderCard(summary({ job_titles: [], education: [] }), cvDetail());
    expect(screen.getByTestId("roles-history-empty")).toHaveTextContent(
      /your assistant fills these in once connected/i
    );
    expect(screen.queryByText(/^0$/)).not.toBeInTheDocument();
    expect(screen.queryByText("Roles:")).not.toBeInTheDocument();
    expect(screen.queryByText("Education:")).not.toBeInTheDocument();
  });

  it("keeps a real Roles count and drops only the empty Education badge", () => {
    renderCard(summary({ job_titles: ["ML Engineer"], education: [] }), cvDetail());
    expect(screen.getByText("Roles:")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.queryByText("Education:")).not.toBeInTheDocument();
    expect(screen.queryByTestId("roles-history-empty")).not.toBeInTheDocument();
  });

  it("shows both badges once both have real counts", () => {
    renderCard(
      summary({ job_titles: ["ML Engineer"], education: ["BSc Computer Science"] }),
      cvDetail()
    );
    expect(screen.getByText("Roles:")).toBeInTheDocument();
    expect(screen.getByText("Education:")).toBeInTheDocument();
    expect(screen.queryByTestId("roles-history-empty")).not.toBeInTheDocument();
  });
});

describe("CVUpload — skills read from the CV are listed, not just counted", () => {
  it("lists each skill as a chip", () => {
    renderCard(summary({ skills_count: 2 }), cvDetail({ skills: ["Python", "PyTorch"] }));
    const list = screen.getByTestId("cv-skills-list");
    expect(list).toHaveTextContent("Python");
    expect(list).toHaveTextContent("PyTorch");
  });

  it("caps the list at 30 and says how many more", () => {
    const skills = Array.from({ length: 35 }, (_, i) => `Skill${i + 1}`);
    renderCard(summary({ skills_count: 35 }), cvDetail({ skills }));
    const list = screen.getByTestId("cv-skills-list");
    expect(list).toHaveTextContent("Skill1");
    expect(list).toHaveTextContent("Skill30");
    expect(list).not.toHaveTextContent("Skill31");
    expect(list).toHaveTextContent("and 5 more");
  });

  it("shows nothing when the CV yielded no skills", () => {
    renderCard(summary({ skills_count: 0 }), cvDetail({ skills: [] }));
    expect(screen.queryByTestId("cv-skills-list")).not.toBeInTheDocument();
  });
});

describe("CVUpload — no false matching claim", () => {
  it("never claims LinkedIn/GitHub improve semantic matching (Job360 never matches jobs)", () => {
    renderCard(summary({ has_linkedin: false, has_github: false }), cvDetail());
    expect(screen.queryByText(/semantic matching/i)).not.toBeInTheDocument();
    expect(screen.getByText(/your assistant can read these too/i)).toBeInTheDocument();
  });
});
