"""Pydantic models for Job360 FastAPI backend."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str


class SourceInfo(BaseModel):
    name: str
    type: str
    health: dict


class SourcesResponse(BaseModel):
    sources: list[SourceInfo]


class StatusResponse(BaseModel):
    jobs_total: int
    last_run: Optional[dict]
    sources_active: int
    sources_total: int
    profile_exists: bool


class JobResponse(BaseModel):
    id: int
    title: str
    company: str
    location: str
    salary: Optional[str]
    match_score: int
    source: str
    date_found: str
    apply_url: str
    visa_flag: bool
    job_type: str = ""
    experience_level: str = ""
    role: int = 0
    skill: int = 0
    seniority: int = 0
    experience: int = 0
    credentials: int = 0
    location_score: int = 0
    recency: int = 0
    semantic: int = 0
    penalty: int = 0
    matched_skills: list[str] = []
    missing_required: list[str] = []
    transferable_skills: list[str] = []
    action: Optional[str] = None
    bucket: str = ""


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
    filters_applied: dict


class ActionRequest(BaseModel):
    action: str
    notes: str = ""


class ActionResponse(BaseModel):
    ok: bool
    job_id: int
    action: str


class ActionsListResponse(BaseModel):
    actions: list[ActionResponse]


class ProfileSummary(BaseModel):
    is_complete: bool
    job_titles: list[str]
    skills_count: int
    cv_length: int
    has_linkedin: bool
    has_github: bool
    education: list[str]
    experience_level: str


class CVDetail(BaseModel):
    """Full extracted CV data for transparent display."""
    raw_text: str = ""
    skills: list[str] = []
    job_titles: list[str] = []
    education: list[str] = []
    certifications: list[str] = []
    summary_text: str = ""
    experience_text: str = ""


class ProfileResponse(BaseModel):
    summary: ProfileSummary
    preferences: dict
    cv_detail: CVDetail | None = None


class LinkedInResponse(BaseModel):
    ok: bool
    merged: bool


class GitHubResponse(BaseModel):
    ok: bool
    merged: bool


class SearchStartResponse(BaseModel):
    run_id: str
    status: str


class SearchStatusResponse(BaseModel):
    run_id: str
    status: str
    progress: str
    result: Optional[dict] = None


class PipelineApplication(BaseModel):
    job_id: int
    stage: str
    created_at: str
    updated_at: str
    notes: str = ""
    title: str = ""
    company: str = ""


class PipelineListResponse(BaseModel):
    applications: list[PipelineApplication]


class PipelineAdvanceRequest(BaseModel):
    stage: str


class PipelineRemindersResponse(BaseModel):
    reminders: list[PipelineApplication]
