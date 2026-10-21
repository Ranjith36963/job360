// Shared test data for the Ready to send tests (not a test file).
const ISO = "2026-10-08T08:06:00+00:00";
export const answer = (over: Record<string, unknown> = {}) => ({
  question: "Right to work",
  answer: "citizen",
  source: "memory",
  key: "right_to_work.GB.work_authorization",
  saved_by: "agent:Claude",
  saved_at: "2026-10-03T09:00:00+00:00",
  ...over,
});
export const card = (id: number, over: Record<string, unknown> = {}) => ({
  application_id: id,
  form_filled_event_id: id * 10,
  filled_at: ISO,
  filled_by: "agent:Claude",
  job_title: "AI Engineer, Agents",
  job_company: "Poolside",
  job_location: "Paris",
  job_country: "FR",
  brought_by: "agent:Claude",
  brought_at: "2026-10-08T07:00:00+00:00",
  fit_score: 82,
  fit_by: "agent:Claude",
  cv: { artifact_id: id, version: 3, saved_at: "2026-10-08T08:02:00+00:00", made_by: "agent:Claude" },
  cover_letter: { artifact_id: id + 100, version: 1, saved_at: "2026-10-08T08:05:00+00:00", made_by: "agent:Claude" },
  answers: [
    answer(),
    answer({ question: "Full name", answer: "Alex Example", source: "profile", key: "contact.name", saved_by: null, saved_at: null }),
    answer({ question: "Why Poolside?", answer: "I build agents.", source: "written", key: null, saved_by: null, saved_at: null }),
    answer({ question: "Visa?", answer: "no", source: "guessed", key: null, saved_by: null, saved_at: null }),
  ],
  flags: [],
  ...over,
});
