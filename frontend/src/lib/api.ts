// ---------------------------------------------------------------------------
// Job360 Frontend — API client (fetch-based, typed)
// ---------------------------------------------------------------------------

import { ApiError } from "./api-error";
import type { components } from "./api-types";
import type {
  BringJobRequest,
  BringJobResponse,
  HealthResponse,
  JsonResumeResponse,
  PreferencesRequest,
  ProfileResponse,
  ProfileVersionDiff,
  ProfileVersionsListResponse,
  Receipt,
  ReceiptListResponse,
  TailorBundle,
  TailorDocKind,
  TailoredDocOut,
} from "./types";

// `fetch` calls default to a RELATIVE base ("") so requests hit the SAME origin
// as the frontend and are proxied to the backend by the Next rewrite in
// next.config.ts (`/api/:path*`). Same-origin = the session cookie (host-only,
// SameSite=Lax) is always sent → the middleware auth gate works in any
// deployment (fixes the split-host cookie bug). Set NEXT_PUBLIC_API_URL only to
// force a direct cross-origin backend (e.g. a dev setup without the proxy).
const API = process.env.NEXT_PUBLIC_API_URL ?? "";

// ---------------------------------------------------------------------------
// Email-not-verified notifier
// ---------------------------------------------------------------------------
// A 403 email_not_verified means a signed-in user hasn't confirmed their email
// and hit a verified-only route. The fetch client MUST NOT navigate on its own:
// a background refetch calling `window.location.href` silently yanks the user
// off a half-filled form and discards their input. Instead `request()` (a)
// throws a typed ApiError (`err.isEmailNotVerified`) the caller can catch, and
// (b) notifies subscribers so ONE top-level handler (AuthProvider) owns the
// redirect decision — navigation is a deliberate app-level response, not a
// hidden effect in every fetch.

type EmailNotVerifiedListener = () => void;
const emailNotVerifiedListeners = new Set<EmailNotVerifiedListener>();

/** Subscribe to email-not-verified auth failures. Returns an unsubscribe fn. */
export function onEmailNotVerified(listener: EmailNotVerifiedListener): () => void {
  emailNotVerifiedListeners.add(listener);
  return () => {
    emailNotVerifiedListeners.delete(listener);
  };
}

function emitEmailNotVerified(): void {
  for (const listener of emailNotVerifiedListeners) listener();
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // Auto-inject Content-Type: application/json when the body is a JSON string
  // and the caller hasn't already set one. FormData bodies are intentionally
  // excluded (typeof FormData !== "string") so uploads set their own
  // multipart/form-data; boundary=... header via the browser.
  const headers = new Headers(init?.headers as HeadersInit | undefined);
  if (init?.body && typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  // credentials: 'include' so the session cookie rides on every call.
  const res = await fetch(`${API}${path}`, {
    credentials: "include",
    ...init,
    headers,
  });

  if (!res.ok) {
    let detail = "";
    let code = "api_error";
    let retryAfter: number | null = null;

    try {
      const body = await res.json();
      detail = body?.detail ?? JSON.stringify(body);
      code = body?.code ?? code;
    } catch {
      detail = await res.text().catch(() => "");
    }

    if (res.status === 429) {
      const ra = res.headers.get("Retry-After");
      retryAfter = ra ? parseInt(ra, 10) : 60;
    }

    const err = new ApiError(res.status, detail, code, retryAfter);

    // #15 / M16: an unverified user hit a verified-only route. Do NOT navigate
    // from here — a background refetch firing `window.location.href` would yank
    // the user off a half-filled form. Notify the top-level handler
    // (AuthProvider) so the redirect is a deliberate app-level decision, then
    // throw the typed error so foreground callers can catch it too.
    if (err.isEmailNotVerified) {
      emitEmailNotVerified();
    }

    throw err;
  }

  // 204 No Content — logout returns empty body
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function qs(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") {
      if (Array.isArray(v)) {
        for (const item of v) sp.append(k, String(item));
      } else {
        sp.set(k, String(v));
      }
    }
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export async function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health");
}

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------

export async function getProfile(): Promise<ProfileResponse> {
  return request<ProfileResponse>("/api/profile");
}

/**
 * Apply 1..N agent-overlay edits to one or more profile fields (the same
 * PATCH the connected assistant's `update_profile` MCP tool uses — R12).
 * Returns the freshly-merged profile so a caller never needs a second
 * round trip.
 */
export async function updateProfileFields(
  edits: { path: string; value: unknown }[]
): Promise<ProfileResponse> {
  const res = await request<{ profile: ProfileResponse }>("/api/profile", {
    method: "PATCH",
    body: JSON.stringify({ edits }),
  });
  return res.profile;
}

export async function uploadProfile(
  cv: File | null,
  preferences?: PreferencesRequest
): Promise<ProfileResponse> {
  const form = new FormData();
  if (cv) {
    form.append("cv", cv);
  }
  if (preferences) {
    form.append("preferences", JSON.stringify(preferences));
  }
  return request<ProfileResponse>("/api/profile", {
    method: "POST",
    body: form,
  });
}

export async function uploadLinkedin(
  file: File
): Promise<{ ok: boolean; merged: boolean }> {
  const form = new FormData();
  form.append("file", file);
  return request<{ ok: boolean; merged: boolean }>("/api/profile/linkedin", {
    method: "POST",
    body: form,
  });
}

export async function uploadGithub(
  username: string
): Promise<{ ok: boolean; merged: boolean }> {
  const form = new FormData();
  form.append("username", username);
  return request<{ ok: boolean; merged: boolean }>("/api/profile/github", {
    method: "POST",
    body: form,
  });
}

/** Which part of the profile to empty. "all" wipes the lot. */
export type ClearSection = "cv" | "linkedin" | "github" | "preferences" | "all";

/**
 * Empty one input (or the whole profile) so the next upload starts clean.
 *
 * Every clear is snapshotted server-side before it writes, so it is undoable
 * from the History drawer — that is what makes a destructive-looking button
 * safe to offer.
 */
export async function clearProfileSection(
  section: ClearSection
): Promise<ProfileResponse> {
  const form = new FormData();
  form.append("section", section);
  return request<ProfileResponse>("/api/profile/clear", {
    method: "POST",
    body: form,
  });
}

// ---- Profile version management (Step-2 A1, S3-MVP endpoints) ----

export async function getProfileVersions(): Promise<ProfileVersionsListResponse> {
  return request<ProfileVersionsListResponse>("/api/profile/versions");
}

export async function restoreProfileVersion(
  versionId: number
): Promise<ProfileResponse> {
  return request<ProfileResponse>(`/api/profile/versions/${versionId}/restore`, {
    method: "POST",
  });
}

export async function getJsonResume(): Promise<JsonResumeResponse> {
  return request<JsonResumeResponse>("/api/profile/json-resume");
}

// ---------------------------------------------------------------------------
// Auth (Batch 2)
// ---------------------------------------------------------------------------

export type User = { id: string; email: string; timezone?: string };

// M2 — register no longer returns the user or a session (no account-enumeration:
// a new-user response that differs from a duplicate would leak whether an email
// exists). It returns a generic acknowledgement; the caller sends the user to
// sign in.
export async function register(
  email: string,
  password: string,
): Promise<{ status: string; message: string }> {
  return request<{ status: string; message: string }>("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export async function login(email: string, password: string): Promise<User> {
  return request<User>("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export async function logout(): Promise<void> {
  await request<void>("/api/auth/logout", { method: "POST" });
}

/**
 * `me()` could not tell whether the session is valid (429, 5xx, network error,
 * abort). NOT the same as "signed out" — callers must keep their last known
 * state and retry, never treat this as a logout.
 */
export class AuthUnknownError extends Error {
  readonly status: number | null;
  /** Seconds the server asked us to wait (429 Retry-After), else null. */
  readonly retryAfter: number | null;

  constructor(cause: unknown) {
    super("Could not verify the session");
    this.name = "AuthUnknownError";
    this.status = cause instanceof ApiError ? cause.status : null;
    this.retryAfter = cause instanceof ApiError ? cause.retryAfter : null;
    this.cause = cause;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/** The signed-in user, or `null` ONLY on a definite 401. Any other failure
 * throws AuthUnknownError (transient — do not log the user out). */
export async function me(): Promise<User | null> {
  try {
    return await request<User>("/api/auth/me");
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null;
    throw new AuthUnknownError(err);
  }
}

/** Owner decision, 2026-09-25 — the ONE write door for the account's time
 * zone (rule #29: only a real Save call ever writes it; a browser-detected
 * prefill on the settings page never calls this on its own). */
export async function setTimezone(timezone: string): Promise<{ timezone: string }> {
  return request<{ timezone: string }>("/api/auth/me/timezone", {
    method: "PUT",
    body: JSON.stringify({ timezone }),
  });
}

// ---------------------------------------------------------------------------
// Passwordless magic-link login
// ---------------------------------------------------------------------------
//
// request always returns 204 — no enumeration (the account is created lazily
// on consume). consume returns the signed-in user (and sets the session
// cookie) on success, or throws on an invalid / expired / used token.

export async function requestMagicLink(email: string, next?: string): Promise<void> {
  await request<void>("/api/auth/magic-link/request", {
    method: "POST",
    body: JSON.stringify(next ? { email, next } : { email }),
  });
}

export async function consumeMagicLink(token: string): Promise<User> {
  return request<User>("/api/auth/magic-link/consume", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

// ---------------------------------------------------------------------------
// Password reset (Phase −2 item A)
// ---------------------------------------------------------------------------
//
// The request endpoint always returns 204 — no enumeration. Caller treats
// "204" as "we'll email you if you have an account" regardless.
//
// The confirm endpoint returns 204 on success or 400 on any failure
// (unknown / expired / used / soft-deleted-user). Backend deliberately
// doesn't distinguish — would leak which tokens exist.

export async function requestPasswordReset(email: string): Promise<void> {
  await request<void>("/api/auth/password-reset/request", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export async function confirmPasswordReset(
  token: string,
  newPassword: string,
): Promise<void> {
  await request<void>("/api/auth/password-reset/confirm", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword }),
  });
}

// ---------------------------------------------------------------------------
// Email verification (Phase −2 item B)
// ---------------------------------------------------------------------------
//
// Resend requires a session — there's no public "resend by email" because
// that lets an attacker spam any address. The verify endpoint takes the
// token directly from the email link.

export async function resendVerificationEmail(): Promise<void> {
  await request<void>("/api/auth/verify-email/request", { method: "POST" });
}

export async function confirmEmailVerification(token: string): Promise<void> {
  await request<void>("/api/auth/verify-email/confirm", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

// ---- Step-3: Account management ----

export async function changePassword(
  current_password: string,
  new_password: string
): Promise<void> {
  await request<void>("/api/auth/users/me/password", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password, new_password }),
  });
}

export async function changeEmail(
  current_password: string,
  new_email: string
): Promise<void> {
  await request<void>("/api/auth/users/me/email", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ current_password, new_email }),
  });
}

export async function deleteAccount(currentPassword: string): Promise<void> {
  await request<void>("/api/auth/users/me", {
    method: "DELETE",
    body: JSON.stringify({ current_password: currentPassword }),
  });
}

// ---- Step-3: Profile version diff ----

export async function getProfileVersionDiff(
  v1: number,
  v2: number
): Promise<ProfileVersionDiff> {
  return request<ProfileVersionDiff>(`/api/profile/versions/${v1}/diff/${v2}`);
}

type _Schemas = components["schemas"];

// ---- Tailored docs ----
//
// Decision 28 (2026-09-21, slice A): Job360 has no LLM. The user's AGENT writes
// the tailored CV / cover letter and saves it (MCP `save_artifact`); these calls
// read the newest saved version back, save a human edit as a NEW version, and
// render DOCX / PDF. There is no generate call any more.

export async function getTailored(jobId: number): Promise<TailorBundle> {
  return request<TailorBundle>(`/api/tailor/${jobId}`);
}

/** One line of the doc + whether it's grounded in the user's CV (their fact) or AI-added. */
export interface ProvenanceSegment {
  text: string;
  grounded: boolean;
}

/** Per-line provenance for a saved doc — your own facts vs lines added on top. */
export async function getTailoredProvenance(
  jobId: number,
  kind: TailorDocKind
): Promise<ProvenanceSegment[]> {
  return request<ProvenanceSegment[]>(`/api/tailor/${jobId}/${kind}/provenance`);
}

export async function saveTailored(
  jobId: number,
  kind: TailorDocKind,
  text: string
): Promise<TailoredDocOut> {
  return request<TailoredDocOut>(`/api/tailor/${jobId}/${kind}`, {
    method: "PATCH",
    body: JSON.stringify({ text }),
  });
}

export type TailorFormat = "pdf" | "docx";

/** Relative path for the download endpoint (GET also marks the doc kept). */
export function tailorDownloadUrl(
  jobId: number,
  kind: TailorDocKind,
  fmt: TailorFormat = "pdf"
): string {
  return `/api/tailor/${jobId}/${kind}/download?fmt=${fmt}`;
}

/**
 * Fetch the tailored doc as a PDF or DOCX and trigger a browser download — a
 * fetch-blob-anchor pattern. credentials:'include' so the session cookie
 * rides on the request.
 */
export async function downloadTailored(
  jobId: number,
  kind: TailorDocKind,
  fmt: TailorFormat = "pdf"
): Promise<void> {
  // POST, not GET — the endpoint marks the doc kept + learns from it, so it is a
  // mutation and is Origin-checked server-side (docs/fable/01 S6). We already read
  // the response as a blob, so the method change is invisible to the user.
  const res = await fetch(`${API}${tailorDownloadUrl(jobId, kind, fmt)}`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    let detail = "Download failed";
    try {
      const body = await res.json();
      detail = body?.detail ?? detail;
    } catch {
      // no JSON body — keep the fallback detail
    }
    throw new ApiError(res.status, detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${kind}_${jobId}.${fmt}`;
  a.click();
  URL.revokeObjectURL(url);
}

/** Download ONE stored CV / cover-letter version as Word or PDF. POST like
 * `downloadTailored` (Origin-checked); same blob + anchor pattern. */
export async function downloadApplicationArtifact(
  applicationId: number,
  artifactId: number,
  fmt: TailorFormat,
  filenameStem = "document"
): Promise<void> {
  const res = await fetch(
    `${API}/api/applications/${applicationId}/artifacts/${artifactId}/download?fmt=${fmt}`,
    { method: "POST", credentials: "include" }
  );
  if (!res.ok) {
    let detail = "Download failed";
    try {
      const body = await res.json();
      detail = body?.detail ?? detail;
    } catch {
      // no JSON body — keep the fallback detail
    }
    throw new ApiError(res.status, detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${filenameStem}.${fmt}`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Bring a job + application receipts (career-ops pivot, slice one)
// ---------------------------------------------------------------------------

/** The user pastes the ad; the backend stores it and births the Application. */
export async function bringJob(body: BringJobRequest): Promise<BringJobResponse> {
  return request<BringJobResponse>(`/api/jobs/bring`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * Fetch a job-ad URL under the backend's SSRF guard (docs/plans/
 * 2026-09-04-url-fetch/spec.md). Always resolves with a closed `outcome` —
 * "the site refused us" is a normal response, not a thrown error. A 404
 * (URL_FETCH_ENABLED off) or a 429 (rate limited) still throw via the
 * shared `request()` — the caller shows those the same way any other
 * ApiError is shown.
 */
export type FetchUrlResponse = _Schemas["FetchUrlResponse"];

export async function fetchJobUrl(url: string): Promise<FetchUrlResponse> {
  return request<FetchUrlResponse>("/api/jobs/fetch-url", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export async function listReceipts(jobId?: number): Promise<ReceiptListResponse> {
  return request<ReceiptListResponse>(`/api/receipts${qs({ job_id: jobId })}`);
}

export async function getReceipt(receiptId: number): Promise<Receipt> {
  return request<Receipt>(`/api/receipts/${receiptId}`);
}

// ---- Personal API tokens (agent access) ----
//
// A token lets an MCP client (Claude Code, Claude Desktop…) act as the user via
// `Authorization: Bearer j360_…` — see backend/src/api/mcp_server.py. The plain
// token is returned ONCE by createToken and never again; the list only carries
// the display prefix. Minting and revoking are cookie-session-only on the
// backend, so a stolen token cannot mint more tokens.

export type TokenCreated = _Schemas["TokenCreated"];
export type TokenSummary = _Schemas["TokenSummary"];

export async function createToken(name: string): Promise<TokenCreated> {
  return request<TokenCreated>("/api/tokens", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function listTokens(): Promise<TokenSummary[]> {
  const res = await request<_Schemas["TokenListResponse"]>("/api/tokens");
  return res.tokens;
}

export async function revokeToken(tokenId: number): Promise<void> {
  await request<void>(`/api/tokens/${tokenId}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// OAuth 2.1 authorization server — consent screen + connected apps
// (docs/plans/2026-09-03-oauth-mcp/spec.md R4, R8, R9). Shapes come from the
// generated api-types (the routes carry `response_model`), so the drift gate
// catches a backend rename.
// ---------------------------------------------------------------------------

/** GET /api/oauth/authorize/{rid} — what the consent screen shows. */
export type ConsentRequest = _Schemas["ConsentRequestResponse"];

export type ConsentDecisionResult = _Schemas["ConsentDecisionResponse"];

/**
 * Load one authorization request for the consent screen. Throws a 404
 * `ApiError` when the request is unknown, already consumed, or expired —
 * callers show the "this request has expired" copy for that case.
 */
export async function getConsentRequest(rid: string): Promise<ConsentRequest> {
  return request<ConsentRequest>(`/api/oauth/authorize/${rid}`);
}

/** Approve or deny the request; `redirect_to` is where the browser goes next. */
export async function decideConsent(
  rid: string,
  approve: boolean
): Promise<ConsentDecisionResult> {
  return request<ConsentDecisionResult>(`/api/oauth/authorize/${rid}/decision`, {
    method: "POST",
    body: JSON.stringify({ approve }),
  });
}

/** A connected app (one active grant per client), shown under Settings → Connect. */
export type OAuthGrant = _Schemas["OAuthGrantOut"];

export async function listGrants(): Promise<OAuthGrant[]> {
  const res = await request<_Schemas["GrantListResponse"]>("/api/oauth/grants");
  return res.grants;
}

/** Revoking kills every token under the grant on the next request (spec S5). */
export async function revokeGrant(id: number): Promise<void> {
  await request<void>(`/api/oauth/grants/${id}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// The application spine (docs/plans/2026-09-04-application-spine/spec.md)
// ---------------------------------------------------------------------------
//
// Shapes come straight from the generated `_Schemas` (backend `response_model`
// on `src/api/routes/applications.py` — same pattern as the OAuth helpers
// above). Run `npm run gen:types` after any change to those response models.

export type ApplicationSummary = _Schemas["ApplicationSummaryOut"];
export type ApplicationEvent = _Schemas["ApplicationEventOut"];
export type ApplicationArtifact = _Schemas["ApplicationArtifactOut"];
export type ApplicationArtifactRow = _Schemas["ApplicationArtifactRowOut"];
export type ApplicationReceiptEntry = _Schemas["ApplicationReceiptOut"];
export type ApplicationFit = _Schemas["ApplicationFitOut"];
export type ApplicationDetail = _Schemas["ApplicationDetailOut"];

// ---- Contacts (slice 4, docs/plans/2026-09-05-contacts-stats/spec.md R1-R3) ----

export type Contact = _Schemas["ContactOut"];
export type AddContactResult = _Schemas["AddContactResponse"];

/** {path, value, set_by, set_at, previous_value} — one live ASSISTANT edit on
 *  `ProfileResponse.agent_edits` (R11; "was X", 2026-09-25). */
export type AgentEdit = _Schemas["AgentEditOut"];

/** One row of one field's history, newest first — both the human's web saves
 *  (`set_by: "web"`) and the assistant's edits. `value: null` is a clear. */
export type ProfileEditHistoryRow = _Schemas["ProfileEditHistoryRow"];

export async function getProfileEditHistory(path: string): Promise<ProfileEditHistoryRow[]> {
  const res = await request<_Schemas["ProfileEditHistoryResponse"]>(
    `/api/profile/edits/history${qs({ path })}`
  );
  return res.rows;
}

/** "Take back" an assistant's change: the field falls back to the CV / form
 *  value. Returns the rebuilt profile. */
/** "Keep" an assistant's change: the human accepts it as their own (it moves
 *  into the base, the mark goes). Returns the rebuilt profile. */
export async function keepProfileEdit(path: string): Promise<ProfileResponse> {
  return request<ProfileResponse>("/api/profile/edits/keep", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export async function takeBackProfileEdit(path: string): Promise<ProfileResponse> {
  return request<ProfileResponse>("/api/profile/edits/take-back", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

export async function listApplications(
  params: {
    status?: string;
    updated_since?: string;
    limit?: number;
    offset?: number;
    // Owner decision, 2026-09-25 — "what's due" / "gone quiet".
    due?: boolean;
    quiet_days?: number;
  } = {}
): Promise<_Schemas["ListApplicationsResponse"]> {
  const query = { limit: 20, ...params };
  return request(`/api/applications${qs(query as Record<string, unknown>)}`);
}

// ---- Needs you (asks the assistant could not answer without the user) ----
export type Ask = _Schemas["AskOut"];
export type AskStatus = "open" | "answered" | "all";

export async function listAsks(
  status: AskStatus = "open",
  offset = 0
): Promise<_Schemas["ListAsksResponse"]> {
  return request(`/api/asks${qs({ status, offset: offset || undefined })}`);
}

/** Fired after the Needs-you page reloads, so the header badge follows. */
export const ASKS_CHANGED_EVENT = "job360:asks-changed";

export async function answerAsk(id: number, answer: string): Promise<Ask> {
  return request<Ask>(`/api/asks/${id}/answer`, {
    method: "POST",
    body: JSON.stringify({ answer }),
  });
}

export async function withdrawAsk(id: number): Promise<Ask> {
  return request<Ask>(`/api/asks/${id}/withdraw`, { method: "POST" });
}

// ---- Assistant settings (S2, owner decision 2026-10-08) ----
// A RISKIER change an assistant asks for (more freedom for it) is never applied;
// it waits as a request that only the signed-in user can confirm here. The full
// settings page is S5; this slice is the "Waiting for your OK" cards only.
export type AssistantSettingsView = _Schemas["AssistantSettingsView"];
export type SettingRequest = _Schemas["SettingRequestOut"];

export async function getAssistantSettings(): Promise<AssistantSettingsView> {
  return request<AssistantSettingsView>("/api/assistant-settings");
}

export async function confirmSettingRequest(id: number): Promise<AssistantSettingsView> {
  return request<AssistantSettingsView>(`/api/assistant-settings/requests/${id}/confirm`, {
    method: "POST",
  });
}

export async function declineSettingRequest(id: number): Promise<AssistantSettingsView> {
  return request<AssistantSettingsView>(`/api/assistant-settings/requests/${id}/decline`, {
    method: "POST",
  });
}

/** Every change to ONE assistant setting, newest first (both sides). */
export async function getSettingHistory(path: string): Promise<ProfileEditHistoryRow[]> {
  const res = await request<_Schemas["ProfileEditHistoryResponse"]>(`/api/assistant-settings/history${qs({ path })}`);
  return res.rows;
}

/** "Take back" the newest change to one setting: a new web row with the value before it. */
export async function takeBackSetting(path: string): Promise<AssistantSettingsView> {
  return request<AssistantSettingsView>("/api/assistant-settings/take-back", {
    method: "POST",
    body: JSON.stringify({ path }),
  });
}

// ---- Lessons (slice 9, docs/plans/2026-09-11-lessons/spec.md) ----
// "Flag for next time": written through recordApplicationEvent(event_type
// "lesson"); read back here for the profile's Lessons list. The agent gets
// the same rows (last PROFILE_LESSONS_MAX) on `get_profile`.
export type Lesson = _Schemas["LessonOut"];

export async function listLessons(
  params: { limit?: number; offset?: number } = {}
): Promise<_Schemas["LessonsResponse"]> {
  return request(`/api/applications/lessons${qs(params as Record<string, unknown>)}`);
}

/** Job facts the assistant (or you) read off the ad — ISO country, remote,
 *  where it was found. Only the keys sent change; null clears one. */
export type JobFacts = _Schemas["JobFactsOut"];
export type JobFactsPatch = _Schemas["UpdateJobFactsRequest"];

export async function updateApplicationJob(applicationId: number, body: JobFactsPatch): Promise<JobFacts> {
  return request(`/api/applications/${applicationId}/job`, { method: "PATCH", body: JSON.stringify(body) });
}

export async function getApplication(id: number, withArtifactText = false): Promise<ApplicationDetail> {
  // Query string omitted entirely in the (default) false case — not just an
  // empty value — so the URL is a bare `/api/applications/{id}` when no
  // artifact text is requested (matches the hermetic e2e mock's route
  // pattern, which has no query-string wildcard on this endpoint).
  const query = withArtifactText ? qs({ with_artifact_text: true }) : "";
  return request(`/api/applications/${id}${query}`);
}

/**
 * The fit picture (slice — alignment view): the stored fit verdict plus
 * which of the user's own profile skills occur in the ad text. Backend
 * route not yet in the generated `api-types.ts` (built in parallel), so
 * hand-typed here — regenerate + replace once `npm run gen:types` picks it
 * up.
 */
/** The fit picture (2026-09-20): the agent's stored verdict beside which of
 * the user's own profile skills occur in the stored ad text. Read-only. */
export type Alignment = _Schemas["AlignmentOut"];

export async function getAlignment(applicationId: number): Promise<Alignment> {
  return request(`/api/applications/${applicationId}/alignment`);
}

export async function getApplicationArtifact(
  applicationId: number,
  artifactId: number
): Promise<ApplicationArtifactRow> {
  return request(`/api/applications/${applicationId}/artifacts/${artifactId}`);
}

export type ArtifactDiff = _Schemas["ArtifactDiffOut"];

/** Slice 8 (#515) — original vs tailored, read-only. `against` is `"profile"`
 * or another artifact id of the same kind; omitted = the backend's default
 * (profile for a cv, the previous version otherwise). */
export async function getArtifactDiff(
  applicationId: number,
  artifactId: number,
  against?: string
): Promise<ArtifactDiff> {
  const query = against ? qs({ against }) : "";
  return request(`/api/applications/${applicationId}/artifacts/${artifactId}/diff${query}`);
}

// ---- Visa / sponsorship signal (slice 7, #514) ----
// docs/plans/2026-09-11-visa-signal/spec.md — the human door at the browser
// (PUT /applications/{id}/visa); an agent uses save_fit / bring_job instead.
export type VisaShape = _Schemas["ApplicationVisaOut"];

export async function setApplicationVisa(
  applicationId: number,
  body: { visa_signal: string; visa_detail?: string; visa_country?: string }
): Promise<_Schemas["SetVisaResponse"]> {
  return request(`/api/applications/${applicationId}/visa`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function recordApplicationEvent(
  applicationId: number,
  body: {
    event_type: string;
    detail?: string;
    payload?: Record<string, unknown>;
    occurred_at?: string;
    corrects_event_id?: number;
    // Owner decision, 2026-09-25 — omit to leave the date alone, "" to
    // clear it, "YYYY-MM-DD" to set it. Works on any event_type.
    follow_up_on?: string;
  }
): Promise<_Schemas["RecordEventResponse"]> {
  return request(`/api/applications/${applicationId}/events`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** "I applied" (or any receipt) — the rich record. Body fields all optional. */
export async function recordApplicationReceipt(
  applicationId: number,
  body: {
    channel?: string;
    note?: string;
    confirmation?: string;
    cv_artifact_id?: number;
    cover_letter_artifact_id?: number;
    applied_at?: string;
  } = {}
): Promise<_Schemas["RecordApplicationReceiptResponse"]> {
  return request(`/api/applications/${applicationId}/receipt`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ── S3 application kit: the four human-in-the-loop buttons ────────────────────
// Every one is a signed-in WEB action (the server refuses a token / OAuth caller).

/** Who / where (web | chat) / when for a decision on an application. */
export type DecisionMark = _Schemas["KitSeenOut"];
/** What the four buttons show: CV seen / approved, don't-send, autofill, duplicate. */
export type ApplicationControls = _Schemas["ApplicationControlsOut"];

export async function getApplicationControls(applicationId: number): Promise<ApplicationControls> {
  return request(`/api/applications/${applicationId}/controls`);
}

/** "I've checked this CV" - marks the latest CV seen (where=web). */
export async function markCvSeen(applicationId: number, artifactId?: number): Promise<ApplicationControls> {
  return request(`/api/applications/${applicationId}/cv-seen`, {
    method: "POST",
    body: JSON.stringify(artifactId == null ? {} : { artifact_id: artifactId }),
  });
}

/** "Send this one" - the user says yes to sending with the latest CV. `seen`
 * (S5d) names the CV and the fill on screen: if either is no longer the newest
 * the server answers 409 and records nothing. */
export async function approveSend(
  applicationId: number,
  seen?: { artifactId?: number | null; formFilledEventId?: number | null },
): Promise<ApplicationControls> {
  const q = qs({ artifact_id: seen?.artifactId ?? undefined, form_filled_event_id: seen?.formFilledEventId ?? undefined });
  return request(`/api/applications/${applicationId}/send/approve${q}`, { method: "POST" });
}

/** "Don't send" - the gate stops until a later "Send this one". */
export async function declineSend(applicationId: number): Promise<ApplicationControls> {
  return request(`/api/applications/${applicationId}/send/decline`, { method: "POST" });
}

/** "Autofill" / "Don't autofill" - may the assistant type into this form. */
export async function setAutofill(applicationId: number, mode: "allow" | "deny"): Promise<ApplicationControls> {
  return request(`/api/applications/${applicationId}/autofill`, {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
}

/** "Not a duplicate, go ahead" - clears the duplicate warning for this application. */
export async function clearDuplicate(applicationId: number): Promise<ApplicationControls> {
  return request(`/api/applications/${applicationId}/duplicate/clear`, { method: "POST" });
}

/** "Mark resolved" - the user sorted out what stopped their assistant; records `unblocked` {resolution: user_did_it}. */
export async function resolveBlocked(applicationId: number): Promise<ApplicationControls> {
  return request(`/api/applications/${applicationId}/blocked/resolve`, { method: "POST" });
}

export type WhatsNewResponse = _Schemas["WhatsNewResponse"];
export type WhatsNewEvent = _Schemas["WhatsNewEventOut"];
export type StatsResponse = _Schemas["StatsResponse"];

/** What changed since `since` (events oldest first, `limit` max 200). Page on
 * with `next_since` + `next_after_id` while `truncated` is true. */
export async function whatsNew(
  params: { since?: string; after_id?: number; limit?: number } = {}
): Promise<WhatsNewResponse> {
  return request(`/api/whats-new${qs(params as Record<string, unknown>)}`);
}

/** The hunt in counts (brought / applied / replied / interview …). */
export async function getStats(): Promise<StatsResponse> {
  return request("/api/applications/stats");
}
/** Add a person to an application (spec R1/R2). 201 for a new row, 200
 * (`already_existed: true`) for the same non-empty email seen again on this
 * application — the caller reads the status from the response body, not the
 * HTTP code, since `request()` doesn't surface it. */
export async function addContact(
  applicationId: number,
  body: {
    name: string;
    role?: string;
    email?: string;
    linkedin_url?: string;
    notes?: string;
    found_via?: string;
    occurred_at?: string;
  }
): Promise<AddContactResult> {
  return request(`/api/applications/${applicationId}/contacts`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ---- Outreach tracking (owner decisions, 2026-09-25) ----
// A person can be linked to a job or to none (cold networking); Job360
// remembers every message version, who/when/channel, sent, reply.

/** Correct a contact's own details — the OLD value is kept, never lost
 *  (the response's `edit_history` shows every value with who/when). */
export async function updateContact(
  contactId: number,
  body: {
    name?: string;
    role?: string;
    email?: string;
    linkedin_url?: string;
    notes?: string;
    found_via?: string; // "" clears it; null would mean "not given"
  }
): Promise<Contact> {
  return request(`/api/contacts/${contactId}`, { method: "PATCH", body: JSON.stringify(body) });
}

// ---- Morning check (S5b) ----
export type MorningCheck = _Schemas["MorningCheckOut"];

export async function getMorningCheck(since: string): Promise<MorningCheck> {
  return request<MorningCheck>(`/api/morning-check${qs({ since })}`);
}

// ---- Ready to send (S5d) ----
export type ReadyToSend = _Schemas["ReadyToSendOut"];
export type ReadyCardData = _Schemas["ReadyCard"];
export type ReadyAnswer = _Schemas["ReadyAnswer"];
export type ReadyFlag = _Schemas["ReadyFlag"];

/** The approval cards. `limit: 0` = counts only; `applicationId` narrows to one. */
export async function getReadyToSend(params: { applicationId?: number; limit?: number } = {}): Promise<ReadyToSend> {
  return request<ReadyToSend>(`/api/ready-to-send${qs({ application_id: params.applicationId, limit: params.limit })}`);
}
