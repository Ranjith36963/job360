/**
 * CVViewer — "What we extracted" sections.
 *
 * CVViewer rendered the CV summary/skills/titles/education/certifications
 * but was wired into NOWHERE in the app (a dead component). This batch wires
 * it into the profile page and extends it with sections the backend already
 * returned but nothing ever rendered: dated CV work history (cv_positions),
 * LinkedIn detail (linkedin_subsections — including LinkedIn's own dated
 * work history via `positions`), GitHub detail (github_temporal), and
 * achievements (cv_detail.achievements). These tests pin: (1) a full profile
 * renders every new section with real content, and (2) an empty/older
 * profile renders none of them — no crash, no empty-section noise.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CVViewer } from "./CVViewer";
import type { CVDetail } from "@/lib/types";

const FULL_CV: CVDetail = {
  raw_text: "Jane Doe — Senior Engineer at Acme. Python, TypeScript.",
  skills: ["Python", "TypeScript"],
  job_titles: ["Senior Engineer"],
  companies: ["Acme"],
  education: ["BSc Computer Science, Imperial College London"],
  certifications: ["AWS Certified Solutions Architect"],
  summary_text: "Senior engineer with 8 years building distributed systems.",
  experience_text: "",
  name: "Jane Doe",
  headline: "Senior Engineer @ Acme",
  location: "London, UK",
  achievements: [
    "Won the internal hackathon 2023",
    "Published a paper at a NeurIPS workshop",
  ],
  cv_positions: [
    {
      company: "Acme",
      title: "Senior Engineer",
      dates: "2021 – Present",
      location: "Shoreditch, London",
      bullets: ["Led the payments migration", "Mentored 4 engineers"],
    },
    {
      company: "Beta Corp",
      title: "Software Engineer",
      dates: "2018 – 2021",
      location: "Remote",
      bullets: [],
    },
  ],
  highlights: ["Python", "TypeScript", "Senior Engineer"],
  extraction_score: { verdict: "good", coverage: 0.9 },
};

const SKILL_PROVENANCE = {
  Python: ["cv_explicit", "github_llm"],
  TypeScript: ["linkedin"],
};

const LINKEDIN_SUBSECTIONS = {
  positions: [
    {
      title: "Product Engineer",
      company: "Widgets Ltd",
      start: "2019",
      end: "2021",
      description: "Owned the checkout flow end to end.",
    },
  ],
  languages: [{ language: "French", proficiency: "Professional working" }],
  projects: [
    {
      title: "Open Source Router",
      description: "A tiny HTTP router.",
      start: "2022",
      end: "2023",
      url: "https://example.com",
    },
  ],
  volunteer: [
    { role: "Mentor", organisation: "CodeBar", cause: "Education" },
  ],
  courses: [{ title: "Distributed Systems", institution: "Coursera" }],
};

const GITHUB_TEMPORAL = {
  languages: { TypeScript: 8000, Python: 2000 },
  topics: { fastapi: 1, nextjs: 1 },
};

describe("CVViewer — extracted sections", () => {
  it("renders identity, work history, LinkedIn detail, and GitHub detail with real content", () => {
    render(
      <CVViewer
        cv={FULL_CV}
        skillProvenance={SKILL_PROVENANCE}
        linkedinSubsections={LINKEDIN_SUBSECTIONS}
        githubTemporal={GITHUB_TEMPORAL}
      />
    );

    // Identity
    expect(screen.getByText("Jane Doe")).toBeInTheDocument();
    expect(screen.getByText("Senior Engineer @ Acme")).toBeInTheDocument();
    expect(screen.getByText("London, UK")).toBeInTheDocument();

    // Work history — dated positions, the headline new capability
    expect(screen.getByText(/Work History \(2\)/)).toBeInTheDocument();
    expect(screen.getByText("Led the payments migration")).toBeInTheDocument();
    expect(screen.getByText("Beta Corp", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("2021 – Present")).toBeInTheDocument();

    // Skills carry a provenance tooltip
    const pythonBadge = screen.getByText("Python");
    expect(pythonBadge).toHaveAttribute("title", "Found in: CV, GitHub");

    // LinkedIn detail
    expect(screen.getByText("LinkedIn detail")).toBeInTheDocument();
    expect(screen.getByText(/French/)).toBeInTheDocument();
    expect(screen.getByText("Open Source Router")).toBeInTheDocument();
    expect(screen.getByText("Mentor")).toBeInTheDocument();
    expect(screen.getByText(/CodeBar/)).toBeInTheDocument();
    expect(screen.getByText(/Distributed Systems/)).toBeInTheDocument();

    // LinkedIn Work History — LinkedIn's own dated positions, separate from
    // the CV's cv_positions
    expect(screen.getByText(/Work History \(1\)/)).toBeInTheDocument();
    expect(screen.getByText("Product Engineer")).toBeInTheDocument();
    expect(screen.getByText(/Widgets Ltd/)).toBeInTheDocument();
    expect(screen.getByText("2019 – 2021")).toBeInTheDocument();
    expect(
      screen.getByText("Owned the checkout flow end to end.")
    ).toBeInTheDocument();

    // GitHub detail
    expect(screen.getByText("GitHub detail")).toBeInTheDocument();
    expect(screen.getByText(/TypeScript · 80%/)).toBeInTheDocument();
    expect(screen.getByText("fastapi")).toBeInTheDocument();

    // Achievements
    expect(screen.getByText(/Achievements \(2\)/)).toBeInTheDocument();
    expect(screen.getByText("Won the internal hackathon 2023")).toBeInTheDocument();
    expect(
      screen.getByText("Published a paper at a NeurIPS workshop")
    ).toBeInTheDocument();
  });

  it("renders no new sections (and does not crash) for an older/empty profile", () => {
    const emptyCV: CVDetail = {
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
      cv_positions: [],
      highlights: [],
      extraction_score: {},
    };

    // Also pass linkedinSubsections/githubTemporal with an empty `positions`
    // array (as an older cached response would), to pin that an empty
    // positions list doesn't crash or render a Work History block either.
    render(
      <CVViewer
        cv={emptyCV}
        linkedinSubsections={{ positions: [] }}
        githubTemporal={{}}
      />
    );

    expect(screen.queryByText(/Work History/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Achievements/)).not.toBeInTheDocument();
    expect(screen.queryByText("LinkedIn detail")).not.toBeInTheDocument();
    expect(screen.queryByText("GitHub detail")).not.toBeInTheDocument();
    expect(screen.queryByText(/Skills Extracted/)).not.toBeInTheDocument();
    // The "Full CV Text" panel was REMOVED from this view on 2026-08-08 (owner
    // decision): the identical text already renders in the "CV Uploaded" card
    // on the same page, and showing the same wall of text twice is noise. This
    // assertion is inverted deliberately — it now guards against the duplicate
    // coming back, and the section header below proves the view still renders.
    expect(screen.queryByText("Full CV Text")).not.toBeInTheDocument();
    expect(
      screen.getByText("What we extracted from your profile")
    ).toBeInTheDocument();
  });
});
