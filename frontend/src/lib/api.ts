// ---------------------------------------------------------------------------
// Job360 Frontend — API client (fetch-based, typed)
// ---------------------------------------------------------------------------

import { ApiError } from "./api-error";
import type { components } from "./api-types";
import type {
  ActionRequest,
  ActionResponse,
  ApplicationTimelineResponse,
  DuplicateJobsResponse,
  HealthResponse,
  JobFilters,
  JobListResponse,
  JobResponse,
  JsonResumeResponse,
  NotificationLedgerListResponse,
  NotificationRule,
  NotificationRuleCreate,
  NotificationRuleListResponse,
  NotificationRuleUpdate,
  PipelineAdvanceRequest,
  PipelineApplication,
  PreferencesRequest,
  ProfileResponse,
  ProfileVersionDiff,
  ProfileVersionsListResponse,
  RecentRunsResponse,
  SearchStartResponse,
  SearchStatusResponse,
  SourceInfo,
  StatusResponse,
} from "./types";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // credentials: 'include' so the session cookie rides on every call.
  const res = await fetch(`${API}${path}`, {
    credentials: "include",
    ...init,
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

    throw new ApiError(res.status, detail, code, retryAfter);
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
// Health / Status / Sources
// ---------------------------------------------------------------------------

export async function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health");
}

export async function getStatus(): Promise<StatusResponse> {
  return request<StatusResponse>("/api/status");
}

export async function getSources(): Promise<SourceInfo[]> {
  const data = await request<{ sources: SourceInfo[] }>("/api/sources");
  return data.sources;
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export async function getJobs(filters: JobFilters = {}): Promise<JobListResponse> {
  return request<JobListResponse>(`/api/jobs${qs(filters as Record<string, unknown>)}`);
}

export async function getJob(id: number): Promise<JobResponse> {
  return request<JobResponse>(`/api/jobs/${id}`);
}

export async function exportJobsCsv(): Promise<void> {
  const res = await fetch(`${API}/api/jobs/export`, { credentials: "include" });
  if (!res.ok) {
    throw new ApiError(res.status, `CSV export failed`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `job360_export_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

export async function setJobAction(
  jobId: number,
  body: ActionRequest
): Promise<ActionResponse> {
  return request<ActionResponse>(`/api/jobs/${jobId}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function removeJobAction(jobId: number): Promise<ActionResponse> {
  return request<ActionResponse>(`/api/jobs/${jobId}/action`, {
    method: "DELETE",
  });
}

export async function getActions(): Promise<{ actions: ActionResponse[] }> {
  return request<{ actions: ActionResponse[] }>("/api/actions");
}

export async function getActionCounts(): Promise<Record<string, number>> {
  return request<Record<string, number>>("/api/actions/counts");
}

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------

export async function getProfile(): Promise<ProfileResponse> {
  return request<ProfileResponse>("/api/profile");
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
// Search
// ---------------------------------------------------------------------------

export async function startSearch(options?: {
  source?: string;
  safe?: boolean;
}): Promise<SearchStartResponse> {
  const params: Record<string, unknown> = {};
  if (options?.source) params.source = options.source;
  if (options?.safe !== undefined) params.safe = options.safe;
  return request<SearchStartResponse>(`/api/search${qs(params)}`, {
    method: "POST",
  });
}

export async function getSearchStatus(
  runId: string
): Promise<SearchStatusResponse> {
  return request<SearchStatusResponse>(`/api/search/${runId}/status`);
}

// ---------------------------------------------------------------------------
// Pipeline (application tracking)
// ---------------------------------------------------------------------------

export async function getPipelineApplications(
  stage?: string
): Promise<{ applications: PipelineApplication[] }> {
  const q = stage ? qs({ stage }) : "";
  return request<{ applications: PipelineApplication[] }>(
    `/api/pipeline${q}`
  );
}

export async function createPipelineApplication(
  jobId: number
): Promise<PipelineApplication> {
  return request<PipelineApplication>(`/api/pipeline/${jobId}`, {
    method: "POST",
  });
}

export async function advancePipelineStage(
  jobId: number,
  body: PipelineAdvanceRequest
): Promise<PipelineApplication> {
  return request<PipelineApplication>(`/api/pipeline/${jobId}/advance`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function getPipelineReminders(): Promise<{
  reminders: PipelineApplication[];
}> {
  return request<{ reminders: PipelineApplication[] }>("/api/pipeline/reminders");
}

export async function getPipelineCounts(): Promise<Record<string, number>> {
  return request<Record<string, number>>("/api/pipeline/counts");
}

// ---------------------------------------------------------------------------
// Auth (Batch 2)
// ---------------------------------------------------------------------------

export type User = { id: string; email: string };

export async function register(email: string, password: string): Promise<User> {
  return request<User>("/api/auth/register", {
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

export async function me(): Promise<User | null> {
  try {
    return await request<User>("/api/auth/me");
  } catch {
    return null;
  }
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

export async function getEmailVerified(): Promise<{ email_verified: boolean }> {
  return await request<{ email_verified: boolean }>("/api/auth/me/email-verified");
}

// ---------------------------------------------------------------------------
// Channel config (Batch 2)
// ---------------------------------------------------------------------------

// `Channel` derives non-tightened fields from the generated ChannelOut schema.
// `channel_type` is intentionally narrowed from `string` to a literal union —
// the backend OpenAPI declares it as string but only these five values are valid.
export type Channel = Omit<_Schemas["ChannelOut"], "channel_type"> & {
  channel_type: "email" | "slack" | "discord" | "telegram" | "webhook";
};

export type ChannelTestResult = { ok: boolean; error: string | null };

export async function listChannels(): Promise<Channel[]> {
  return request<Channel[]>("/api/settings/channels");
}

export async function createChannel(body: {
  channel_type: Channel["channel_type"];
  display_name: string;
  credential: string;
}): Promise<Channel> {
  return request<Channel>("/api/settings/channels", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deleteChannel(id: number): Promise<void> {
  await request<void>(`/api/settings/channels/${id}`, { method: "DELETE" });
}

export async function testChannel(id: number): Promise<ChannelTestResult> {
  return request<ChannelTestResult>(`/api/settings/channels/${id}/test`, {
    method: "POST",
  });
}

// ---- Step-3: Notification rules ----

export async function getNotificationRules(): Promise<NotificationRuleListResponse> {
  return request<NotificationRuleListResponse>("/api/settings/notification-rules");
}

export async function createNotificationRule(
  body: NotificationRuleCreate
): Promise<NotificationRule> {
  return request<NotificationRule>("/api/settings/notification-rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateNotificationRule(
  id: number,
  body: NotificationRuleUpdate
): Promise<NotificationRule> {
  return request<NotificationRule>(`/api/settings/notification-rules/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deleteNotificationRule(id: number): Promise<void> {
  await request<void>(`/api/settings/notification-rules/${id}`, {
    method: "DELETE",
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

export async function deleteAccount(): Promise<void> {
  await request<void>("/api/auth/users/me", { method: "DELETE" });
}

// ---- Step-3: Notification ledger ----

export async function getNotificationLedger(
  limit = 20,
  offset = 0
): Promise<NotificationLedgerListResponse> {
  return request<NotificationLedgerListResponse>(
    `/api/notifications?limit=${limit}&offset=${offset}`
  );
}

// ---- Step-3: Duplicate jobs ----

export async function getJobDuplicates(id: number): Promise<DuplicateJobsResponse> {
  return request<DuplicateJobsResponse>(`/api/jobs/${id}/duplicates`);
}

// ---- Step-3: Profile version diff ----

export async function getProfileVersionDiff(
  v1: number,
  v2: number
): Promise<ProfileVersionDiff> {
  return request<ProfileVersionDiff>(`/api/profile/versions/${v1}/diff/${v2}`);
}

// ---- Step-3: Application timeline ----

export async function getApplicationTimeline(
  jobId: number
): Promise<ApplicationTimelineResponse> {
  return request<ApplicationTimelineResponse>(`/api/pipeline/${jobId}/timeline`);
}

export async function updateApplicationNotes(
  jobId: number,
  notes: string
): Promise<void> {
  await request<void>(`/api/pipeline/${jobId}/notes`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notes }),
  });
}

// ---- Channels: OAuth connect + Telegram poll ----

export type ChannelProviders = { slack: boolean; discord: boolean; telegram: boolean };

export async function getProviders(): Promise<ChannelProviders> {
  return request<ChannelProviders>("/api/settings/channels/providers");
}

export async function connectTelegram(): Promise<{ deep_link: string; state: string }> {
  return request<{ deep_link: string; state: string }>(
    "/api/settings/channels/connect/telegram"
  );
}

export async function pollTelegram(
  state: string
): Promise<{ connected: boolean; target_label: string | null }> {
  return request<{ connected: boolean; target_label: string | null }>(
    `/api/settings/channels/connect/telegram/poll?state=${encodeURIComponent(state)}`
  );
}

// ---- Step-3: Recent runs ----

export async function getRecentRuns(
  limit = 10,
  offset = 0
): Promise<RecentRunsResponse> {
  return request<RecentRunsResponse>(`/api/runs/recent?limit=${limit}&offset=${offset}`);
}

// ---- Phase −2 item D: per-source health ----
//
// Both types derive non-tightened fields from the generated schema so they
// can't drift. Only `health` (SourceHealthEntry) is intentionally narrowed
// from `string` to a literal union — the backend OpenAPI declares it as string
// but the runtime always emits one of the three values below.

type _Schemas = components["schemas"];

export type SourceHealthEntry = Omit<_Schemas["SourceHealthEntry"], "health"> & {
  health: "ok" | "warning" | "critical";
};

export type SourceHealthResponse = Omit<_Schemas["SourceHealthResponse"], "sources"> & {
  sources: SourceHealthEntry[];
};

export async function getSourceHealth(runs = 20): Promise<SourceHealthResponse> {
  return request<SourceHealthResponse>(`/api/runs/source-health?runs=${runs}`);
}
