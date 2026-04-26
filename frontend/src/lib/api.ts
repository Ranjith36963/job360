// ---------------------------------------------------------------------------
// Job360 Frontend — API client (fetch-based, typed)
// ---------------------------------------------------------------------------

import { ApiError } from "./api-error";
import type {
  ActionRequest,
  ActionResponse,
  ApplicationTimelineResponse,
  HealthResponse,
  JobFilters,
  JobListResponse,
  JobResponse,
  JsonResumeResponse,
  PipelineAdvanceRequest,
  PipelineApplication,
  PreferencesRequest,
  ProfileResponse,
  ProfileVersionsListResponse,
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

export async function updateApplicationNotes(
  jobId: number,
  notes: string
): Promise<PipelineApplication> {
  return request<PipelineApplication>(`/api/pipeline/${jobId}/notes`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ notes }),
  });
}

export async function getApplicationTimeline(
  jobId: number
): Promise<ApplicationTimelineResponse> {
  return request<ApplicationTimelineResponse>(`/api/pipeline/${jobId}/timeline`);
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
// Channel config (Batch 2)
// ---------------------------------------------------------------------------

export type Channel = {
  id: number;
  channel_type: "email" | "slack" | "discord" | "telegram" | "webhook";
  display_name: string;
  enabled: boolean;
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
