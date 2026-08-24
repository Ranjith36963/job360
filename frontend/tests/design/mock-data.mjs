/**
 * Deterministic API fixtures for the design tools.
 *
 * WHY MOCK AT ALL, when a real local backend exists: the dev Postgres on :5433
 * is shared by every worktree on this machine, and its schema is migrated by
 * whichever branch touched it last (`migrations.runner status` reports
 * 0031_universal_shelf as an "orphan" — applied, but absent from this branch).
 * That mismatch makes /api/jobs 500 here, and repairing a database other
 * sessions are using to review a stylesheet is the wrong trade.
 *
 * WHY MOCKING IS ALSO BETTER, not just safer: a design review needs the cases
 * that BREAK layouts, and real data rarely contains them on demand. These
 * fixtures deliberately include a title long enough to wrap twice, a company
 * name with no spaces to break on, a missing salary, both score extremes, and
 * a sponsor badge — so spacing and overflow are tested where they actually
 * fail rather than where the sample happens to be tidy.
 *
 * These drive SCREENSHOTS and GEOMETRY only. They never assert behaviour, so
 * they cannot make a broken page look passing — a wrong shape here shows up as
 * an empty or broken render, which is exactly what the tools photograph.
 */

const now = Date.now();
const iso = (hoursAgo) => new Date(now - hoursAgo * 3600_000).toISOString();

const DIMS = {
  role: 0,
  skill: 0,
  seniority_score: 0,
  experience: 0,
  credentials: 0,
  location_score: 0,
  recency: 0,
  semantic: 0,
  penalty: 0,
};

function job(over) {
  return {
    id: 1,
    title: "Data Engineer",
    company: "Monzo",
    location: "London, UK",
    salary: "£70,000 - £85,000",
    match_score: 70,
    source: "reed",
    date_found: iso(3),
    apply_url: "https://example.test/1",
    visa_flag: false,
    experience_level: "mid",
    action: null,
    bucket: "24h",
    posted_at: iso(5),
    first_seen_at: iso(5),
    last_seen_at: iso(1),
    date_confidence: "high",
    staleness_state: "active",
    ...DIMS,
    ...over,
  };
}

export const MOCK_JOBS = [
  job({ id: 1, match_score: 98, title: "Senior Data Engineer", company: "Monzo" }),
  // Wraps to two or three lines at 390px — the case that pushes a card's
  // internals out of alignment with its neighbours.
  job({
    id: 2,
    match_score: 84,
    title:
      "Principal Machine Learning Platform Engineer, Real-Time Personalisation & Ranking Systems",
    company: "InternationalConsolidatedFinancialServicesGroup",
    location: "Manchester, United Kingdom (Hybrid — 2 days on site)",
    salary: null, // rule #29: an absent salary must read as absent, not as £0
    visa_flag: true,
    bucket: "48h",
  }),
  job({ id: 3, match_score: 67, title: "Analytics Engineer", company: "Deliveroo", salary: null }),
  job({
    id: 4,
    match_score: 41,
    title: "Data Platform Engineer",
    company: "BT Group",
    location: "Remote (UK)",
    bucket: "3d",
    staleness_state: "stale",
    date_confidence: "low",
  }),
  job({ id: 5, match_score: 12, title: "Junior Data Analyst", company: "Octopus Energy", bucket: "7d" }),
];

export const MOCK_ROUTES = [
  {
    pattern: "**/api/auth/me**",
    body: { id: "design-user", email: "design-pass@example.com" },
  },
  {
    pattern: "**/api/status**",
    body: { jobs_total: MOCK_JOBS.length, sources_total: 47, last_run: iso(2) },
  },
  {
    pattern: "**/api/jobs**",
    body: { jobs: MOCK_JOBS, total: MOCK_JOBS.length, filters_applied: {} },
  },
];

/**
 * Attach the fixtures to a Playwright page. Only the routes above are faked;
 * anything else still reaches the real server, so a page that depends on an
 * endpoint nobody mocked fails visibly instead of rendering a convincing lie.
 */
export async function installMocks(page) {
  for (const { pattern, body } of MOCK_ROUTES) {
    await page.route(pattern, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      }),
    );
  }
}
