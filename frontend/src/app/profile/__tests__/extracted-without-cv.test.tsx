/**
 * Defect: LinkedIn/GitHub data is extracted, stored, and sent to the browser
 * -- then rendered by nothing, whenever the user connects LinkedIn or GitHub
 * BEFORE uploading a CV.
 *
 * Root cause: the "What we extracted" block in page.tsx was gated on
 * `profile?.cv_detail`, which the backend only ever fills in when the CV's
 * raw_text is non-empty (`_build_profile_response` in
 * backend/src/api/routes/profile.py). LinkedIn and GitHub data arrive on the
 * SAME response independently of the CV, so a LinkedIn-only or GitHub-only
 * profile was carrying real content that the page threw away — the section
 * just vanished, which reads as "the enrichment failed" even though it
 * worked. The page's own copy calls LinkedIn/GitHub "Optional" (rule #29);
 * the render must not silently require the one input it never asked for.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import ProfilePage from "../page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getProfile: vi.fn(),
}));

const BASE_SUMMARY = {
  is_complete: false,
  job_titles: [],
  skills_count: 0,
  cv_length: 0,
  has_linkedin: true,
  has_github: true,
  education: [],
  experience_level: "",
  cv_filename: "",
  cv_uploaded_at: "",
  linkedin_filename: "linkedin-export.pdf",
  linkedin_uploaded_at: "2026-08-10",
  github_username: "ranjith-dev",
  github_connected_at: "2026-08-10",
  github_repo_count: 3,
};

// A LinkedIn export uploaded and a GitHub handle connected, but NO CV — the
// exact order the defect report describes. cv_detail is null exactly like
// the backend leaves it when cv.raw_text is empty.
const NO_CV_PROFILE = {
  summary: BASE_SUMMARY,
  preferences: {},
  ai_suggestions: [],
  cv_detail: null,
  skill_tiers: {},
  skill_esco: {},
  skill_provenance: {},
  skills_by_source: {},
  linkedin_subsections: {
    positions: [
      {
        title: "Senior Backend Engineer",
        company: "Acme Robotics",
        start: "2022",
        end: "Present",
        description: "Owns the payments platform.",
      },
    ],
  },
  github_temporal: {
    languages: { Python: 42000, TypeScript: 18000 },
  },
  github_detail: {
    repos: [
      {
        name: "job-matcher",
        language: "Python",
        description: "A job matching engine",
        topics: ["fastapi"],
        stars: 12,
        forks: 2,
      },
    ],
  },
};

// Control case: everything NO_CV_PROFILE has, plus a real CV — the normal
// case that must keep rendering exactly as before the gate was split.
const WITH_CV_PROFILE = {
  ...NO_CV_PROFILE,
  summary: {
    ...BASE_SUMMARY,
    cv_length: 800,
    cv_filename: "cv.pdf",
    cv_uploaded_at: "2026-08-01",
  },
  cv_detail: {
    achievements: [],
    certifications: [],
    companies: [],
    cv_experience_level: "",
    cv_positions: [],
    cv_projects: [],
    cv_right_to_work: "",
    education: [],
    experience_text: "",
    extraction_score: {},
    headline: "",
    highlights: [],
    job_titles: ["Backend Engineer"],
    location: "",
    name: "Ranjith",
    raw_text: "Ranjith's CV text",
    skills: ["Python"],
    summary_text: "Backend engineer with 5 years experience.",
  },
};

describe("ProfilePage — LinkedIn/GitHub render without a CV (Unit B)", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("shows LinkedIn and GitHub content on screen when there is no CV", async () => {
    const api = await import("@/lib/api");
    vi.mocked(api.getProfile).mockResolvedValueOnce(
      NO_CV_PROFILE as unknown as Awaited<ReturnType<typeof api.getProfile>>
    );

    render(<ProfilePage />);

    // LinkedIn work history — proves the LinkedIn subsections rendered.
    await waitFor(() => {
      expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();
    });
    expect(screen.getByText(/Acme Robotics/)).toBeInTheDocument();

    // GitHub repo — proves the GitHub detail rendered too, in the same pass.
    expect(screen.getByText("job-matcher")).toBeInTheDocument();

    // The CV-only sections must stay silent (rule #29) -- there is no CV
    // data to show -- but that must not have hidden the LinkedIn/GitHub
    // sections above. "Skills Extracted" is CVViewer's CV-skills heading,
    // only rendered when cv.skills is non-empty (unlike "professional
    // summary", which also appears as PreferencesForm's about-me hint and
    // would give a false positive here).
    expect(screen.queryByText(/skills extracted/i)).not.toBeInTheDocument();
  });

  it("still renders everything (CV + LinkedIn + GitHub) when a CV is present", async () => {
    const api = await import("@/lib/api");
    vi.mocked(api.getProfile).mockResolvedValueOnce(
      WITH_CV_PROFILE as unknown as Awaited<ReturnType<typeof api.getProfile>>
    );

    render(<ProfilePage />);

    await waitFor(() => {
      expect(screen.getByText("Ranjith")).toBeInTheDocument();
    });
    expect(
      screen.getByText("Backend engineer with 5 years experience.")
    ).toBeInTheDocument();
    expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();
    expect(screen.getByText("job-matcher")).toBeInTheDocument();
  });
});
