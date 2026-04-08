// ---------------------------------------------------------------------------
// Job360 Frontend — API client (fetch-based, typed)
// ---------------------------------------------------------------------------

import type {
  ActionRequest,
  ActionResponse,
  HealthResponse,
  JobFilters,
  JobListResponse,
  JobResponse,
  PipelineAdvanceRequest,
  PipelineApplication,
  PreferencesRequest,
  ProfileResponse,
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
  const res = await fetch(`${API}${path}`, init);
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(
      `API ${init?.method ?? "GET"} ${path} failed (${res.status}): ${body}`
    );
  }
  return res.json() as Promise<T>;
}

function qs(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") {
      sp.set(k, String(v));
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
  const res = await fetch(`${API}/api/jobs/export`);
  if (!res.ok) {
    throw new Error(`CSV export failed (${res.status})`);
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
