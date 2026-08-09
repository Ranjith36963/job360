/**
 * Every GitHub shelf must reach the screen.
 *
 * MEASURED ON A LIVE PROFILE, 2026-08-09. GitHub returned 14 languages, 10
 * topics, 49 frameworks, 13 repos, 25 inferred skills and 30 LLM-read skills.
 * Only languages and topics rendered — 92 pieces of extracted signal sat in the
 * database and appeared NOWHERE. Same shape as cv_positions and the upload
 * receipts before them: a value computed, stored, and shown to nobody.
 *
 * It matters most on THIS input. A CV *claims* "FastAPI"; a requirements.txt in
 * shipped code *proves* it — which is why skill tiering already weights GitHub
 * dependency and README evidence (1.5) above a language guess (1.0). That
 * evidence was scoring the user's job matches while being invisible to them.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CVViewer } from "./CVViewer";
import type { CVDetail } from "@/lib/types";

const cv = {
  raw_text: "Ada Lovelace\nEngineer",
  skills: ["Python"],
  job_titles: ["ML Engineer"],
  companies: [],
  education: [],
  certifications: [],
  summary_text: "Builds things.",
  experience_text: "",
  name: "Ada Lovelace",
  headline: "Engineer",
  location: "London",
  achievements: [],
} as unknown as CVDetail;

const githubDetail = {
  username: "adalovelace",
  connected_at: "2026-08-09T12:00:00+00:00",
  repos: [
    {
      name: "job360",
      language: "Python",
      description: "A UK job search engine",
      topics: ["fastapi", "llm"],
      stars: 7,
      pushed_at: "2026-08-01T10:00:00Z",
    },
    {
      name: "quiet-repo",
      language: "",
      description: "",
      topics: [],
      stars: 0,
      pushed_at: "",
    },
  ],
  frameworks: ["fastapi", "next", "langgraph"],
  skills_inferred: ["TypeScript", "Dart"],
  llm_skills: ["Tailwind CSS", "Framer Motion"],
  bio: "Aspiring analytical engineer",
  profile_readme: "# Hello, I build things",
};

function renderViewer(detail: Record<string, unknown> | undefined) {
  render(
    <CVViewer
      cv={cv}
      githubTemporal={{ languages: { Python: 100 }, topics: { llm: 1 } }}
      githubDetail={detail}
    />
  );
}

describe("GitHub detail shows everything GitHub gave us", () => {
  it("renders the structured identity — open-to-work and location included", () => {
    renderViewer({
      ...githubDetail,
      identity: {
        name: "Ada Lovelace",
        company: "Analytical Engines Ltd",
        location: "London, UK",
        blog: "https://ada.dev",
        twitter: "ada",
        hireable: true,
        account_created_at: "2019-04-01T00:00:00Z",
        followers: 12,
        public_repos: 30,
      },
    });
    expect(screen.getByText("Analytical Engines Ltd")).toBeTruthy();
    // The two matching-grade fields: a place the UK gate can reason about,
    // and GitHub's own "open to work" flag.
    expect(screen.getByText("London, UK")).toBeTruthy();
    expect(screen.getByText("Open to work")).toBeTruthy();
    expect(screen.getByText("Yes")).toBeTruthy();
    expect(screen.getByText("@ada")).toBeTruthy();
    expect(screen.getByText(/2019/)).toBeTruthy();
  });

  it("hireable is TRI-STATE — 'never said' is not 'not looking'", () => {
    // hireable: null must render NO row at all, exactly like the visa signal:
    // silence is not a negative answer (rule #31).
    renderViewer({ identity: { location: "Leeds", hireable: null } });
    expect(screen.getByText("Leeds")).toBeTruthy();
    expect(screen.queryByText("Open to work")).not.toBeInTheDocument();
  });

  it("an unset identity field renders no empty row (rule #29)", () => {
    renderViewer({ identity: { location: "Leeds" } });
    expect(screen.getByText("Leeds")).toBeTruthy();
    expect(screen.queryByText("Company")).not.toBeInTheDocument();
    expect(screen.queryByText("Followers")).not.toBeInTheDocument();
  });

  it("shows repo tenure, forks and archived state", () => {
    renderViewer({
      repos: [
        {
          name: "long-runner",
          language: "Python",
          description: "",
          topics: [],
          stars: 0,
          forks: 4,
          archived: true,
          created_at: "2023-01-01T00:00:00Z",
          pushed_at: "2025-06-01T00:00:00Z",
        },
      ],
    });
    // Tenure is a DIFFERENT claim from recency: built over 2 yrs vs touched
    // last week. Both are on screen.
    expect(screen.getByText(/built over 2 yrs/)).toBeTruthy();
    expect(screen.getByText(/⑂ 4/)).toBeTruthy();
    expect(screen.getByText("archived")).toBeTruthy();
  });

  it("renders repositories with language, stars and last-updated", () => {
    renderViewer(githubDetail);
    expect(screen.getByText("job360")).toBeTruthy();
    expect(screen.getByText("A UK job search engine")).toBeTruthy();
    expect(screen.getByText(/★ 7/)).toBeTruthy();
    // Recency is the signal the engine has nowhere else — assert the YEAR so
    // the test does not break on a different locale's date format.
    expect(screen.getByText(/updated .*2026/)).toBeTruthy();
  });

  it("renders dependency-file frameworks AND says why they count", () => {
    renderViewer(githubDetail);
    // "fastapi" is legitimately on screen twice — as a repo TOPIC and as a
    // dependency-file FRAMEWORK. Those are different claims about the same
    // word, so both belong; assert at least one rather than exactly one.
    expect(screen.getAllByText("fastapi").length).toBeGreaterThan(0);
    expect(screen.getByText("langgraph")).toBeTruthy();
    // The distinction between claimed and demonstrated is the whole value.
    expect(screen.getByText(/dependency files/i)).toBeTruthy();
    expect(screen.getByText(/shipped code/i)).toBeTruthy();
  });

  it("renders README-read skills and repo-inferred skills separately", () => {
    renderViewer(githubDetail);
    expect(screen.getByText("Tailwind CSS")).toBeTruthy();
    expect(screen.getByText("Dart")).toBeTruthy();
    expect(screen.getByText(/READMEs \(2\)/)).toBeTruthy();
    expect(screen.getByText(/inferred from repos \(2\)/)).toBeTruthy();
  });

  it("renders the bio and profile README", () => {
    renderViewer(githubDetail);
    expect(screen.getByText("Aspiring analytical engineer")).toBeTruthy();
    expect(screen.getByText(/# Hello, I build things/)).toBeTruthy();
  });

  it("a repo with no description or topics still renders its name", () => {
    renderViewer(githubDetail);
    expect(screen.getByText("quiet-repo")).toBeTruthy();
  });

  it("stays SILENT when there is no GitHub data at all (rule #29)", () => {
    render(<CVViewer cv={cv} />);
    expect(screen.queryByText("GitHub detail")).not.toBeInTheDocument();
  });

  it("renders what exists when the fetch was partial", () => {
    // A rate-limited enrich can return frameworks but no repos. Each block is
    // independent, so the page shows what it has instead of all-or-nothing.
    renderViewer({ frameworks: ["fastapi"], repos: [], llm_skills: [] });
    expect(screen.getByText("fastapi")).toBeTruthy();
    expect(screen.queryByText(/Repositories/)).not.toBeInTheDocument();
  });

  it("never renders undefined or Invalid Date from a malformed entry", () => {
    renderViewer({
      repos: [{ name: "odd", language: null, topics: "nope", pushed_at: "xx" }],
      frameworks: ["ok"],
    });
    expect(screen.getByText("odd")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/undefined|Invalid Date|NaN/);
  });
});
