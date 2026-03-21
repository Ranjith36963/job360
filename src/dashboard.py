"""Job360 Web Dashboard — Professional Streamlit UI."""

import html
import sqlite3
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure project root is on sys.path so "src" package resolves
# when Streamlit runs this file directly.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st
import pandas as pd
import plotly.express as px

from src.config.settings import DB_PATH, EXPORTS_DIR, MIN_MATCH_SCORE
from src.profile.storage import load_profile, save_profile, profile_exists
from src.profile.models import UserProfile, CVData, UserPreferences
from src.profile.cv_parser import parse_cv_from_bytes
from src.profile.preferences import merge_cv_and_preferences
from src.utils.time_buckets import (
    BUCKETS,
    bucket_jobs,
    bucket_summary_counts,
    format_relative_time,
    get_job_age_hours,
    assign_bucket,
    score_color_hex,
    extract_matched_skills,
)
from src.notifications.report_generator import generate_markdown_report
from src.models import Job


def _run_async(coro):
    """Run async coroutine safely, handling existing event loops."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Job360",
    page_icon="\U0001F4BC",
    layout="wide",
    initial_sidebar_state="expanded",
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    /* --- Compact table-row layout --- */
    .jrow-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
        line-height: 1.3;
    }
    .jrow-table thead th {
        position: sticky;
        top: 0;
        background: #f8f9fa;
        padding: 6px 8px;
        text-align: left;
        font-weight: 600;
        font-size: 11px;
        color: #666;
        text-transform: uppercase;
        letter-spacing: 0.3px;
        border-bottom: 2px solid #dee2e6;
    }
    .jrow {
        border-bottom: 1px solid #eee;
        height: 44px;
    }
    .jrow:hover { background: #f0f6ff; }
    .jrow:nth-child(even) { background: #fafbfc; }
    .jrow:nth-child(even):hover { background: #f0f6ff; }
    .jrow td { padding: 5px 8px; vertical-align: middle; white-space: nowrap; }
    .jrow-score {
        display: inline-block;
        min-width: 32px;
        padding: 2px 6px;
        border-radius: 10px;
        color: white;
        font-weight: 700;
        font-size: 12px;
        text-align: center;
    }
    .jrow-title a {
        color: #1a73e8;
        text-decoration: none;
        font-weight: 600;
        font-size: 13px;
    }
    .jrow-title a:hover { text-decoration: underline; }
    .jrow-company { color: #555; font-size: 12px; margin-left: 6px; }
    .jrow-loc, .jrow-salary, .jrow-time {
        color: #666;
        font-size: 12px;
        max-width: 150px;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .jrow-pill {
        display: inline-block;
        padding: 1px 6px;
        border-radius: 8px;
        font-size: 10px;
        font-weight: 600;
        margin-right: 3px;
    }
    .jrow-pill-source { background: #e3f2fd; color: #1565c0; }
    .jrow-pill-visa { background: #4CAF50; color: white; }
    /* Skills hover tooltip */
    .jrow-skills {
        position: relative;
        display: inline-block;
        cursor: default;
    }
    .jrow-skills .jrow-pill { background: #f3e5f5; color: #7b1fa2; }
    .jrow-skills-tip {
        display: none;
        position: absolute;
        bottom: 100%;
        left: 50%;
        transform: translateX(-50%);
        background: #333;
        color: #fff;
        padding: 6px 10px;
        border-radius: 6px;
        font-size: 11px;
        white-space: pre-line;
        z-index: 100;
        min-width: 160px;
        max-width: 280px;
        text-align: left;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }
    .jrow-skills:hover .jrow-skills-tip { display: block; }
    /* Match breakdown tooltip */
    .jrow-breakdown {
        position: relative;
        display: inline-block;
        cursor: default;
    }
    .jrow-breakdown-tip {
        display: none;
        position: absolute;
        bottom: 100%;
        left: 50%;
        transform: translateX(-50%);
        background: #1a1a2e;
        color: #fff;
        padding: 10px 14px;
        border-radius: 8px;
        font-size: 11px;
        white-space: nowrap;
        z-index: 200;
        min-width: 220px;
        max-width: 320px;
        text-align: left;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    }
    .jrow-breakdown:hover .jrow-breakdown-tip { display: block; }
    .jrow-dim-row { display: flex; align-items: center; gap: 6px; margin: 2px 0; }
    .jrow-dim-label { width: 70px; font-size: 10px; color: #aaa; text-transform: uppercase; }
    .jrow-dim-bar { flex: 1; height: 6px; background: #333; border-radius: 3px; overflow: hidden; }
    .jrow-dim-fill { height: 100%; border-radius: 3px; }
    .jrow-dim-val { width: 28px; text-align: right; font-size: 10px; font-weight: 600; }
    .jrow-skill-pill {
        display: inline-block;
        padding: 1px 5px;
        border-radius: 6px;
        font-size: 9px;
        font-weight: 600;
        margin: 1px 2px;
    }
    .jrow-skill-match { background: #1b5e20; color: #a5d6a7; }
    .jrow-skill-miss { background: #b71c1c; color: #ef9a9a; }
    .jrow-skill-transfer { background: #e65100; color: #ffcc80; }
    /* Bucket headers */
    .bucket-hdr {
        padding: 8px 0 4px 0;
        margin-top: 12px;
        font-size: 15px;
        font-weight: 700;
        border-bottom: 3px solid #ddd;
    }
    .bucket-hdr .bucket-count {
        display: inline-block;
        padding: 1px 8px;
        border-radius: 10px;
        font-size: 12px;
        font-weight: 600;
        margin-left: 6px;
        background: #eee;
        color: #555;
    }
    .bucket-hdr-0 { border-color: #f44336; }
    .bucket-hdr-1 { border-color: #ff9800; }
    .bucket-hdr-2 { border-color: #ffc107; }
    .bucket-hdr-3 { border-color: #4CAF50; }
    .bucket-hdr-4 { border-color: #2196f3; }
    /* Bucket navigation pills */
    .bucket-nav { display: flex; gap: 8px; margin-bottom: 8px; }
    .bucket-nav-pill {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 3px 10px;
        border-radius: 12px;
        font-size: 12px;
        font-weight: 600;
        background: #f5f5f5;
        color: #333;
    }
    .bucket-nav-pill .bn-dot {
        width: 8px; height: 8px;
        border-radius: 50%;
        display: inline-block;
    }
    /* Streamlit padding — keep enough room so title isn't clipped */
    .block-container { padding-top: 4rem !important; }

    /* --- Professional styling --- */

    /* Profile header gradient */
    .profile-header {
        background: linear-gradient(135deg, #1a73e8, #4285f4);
        color: white;
        padding: 16px 20px;
        border-radius: 12px 12px 0 0;
        font-size: 18px;
        font-weight: 700;
        margin-bottom: 0;
        letter-spacing: 0.3px;
    }

    /* CV mandatory badge */
    .cv-mandatory-badge {
        background: #fff3cd;
        border-left: 4px solid #ffc107;
        padding: 10px 14px;
        border-radius: 0 8px 8px 0;
        font-size: 13px;
        font-weight: 600;
        color: #856404;
        margin: 8px 0;
        animation: pulse-badge 2s ease-in-out infinite;
    }
    @keyframes pulse-badge {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.7; }
    }

    /* Profile status indicators */
    .profile-status-active {
        background: #e8f5e9;
        color: #2e7d32;
        padding: 8px 14px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 600;
        text-align: center;
        margin-top: 8px;
    }
    .profile-status-inactive {
        background: #fbe9e7;
        color: #bf360c;
        padding: 8px 14px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 600;
        text-align: center;
        margin-top: 8px;
    }

    /* Metric cards */
    div[data-testid="stMetric"] {
        background: #e8f0fe;
        border-left: 4px solid #1a73e8;
        border-radius: 8px;
        padding: 12px 16px !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    div[data-testid="stMetricLabel"] {
        font-size: 11px !important;
        color: #5f6368 !important;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    div[data-testid="stMetricValue"] {
        font-size: 24px !important;
        font-weight: 700 !important;
        color: #1a73e8 !important;
    }

    /* Welcome box (empty state) */
    .welcome-box {
        text-align: center;
        padding: 60px 40px;
        margin: 40px auto;
        max-width: 500px;
    }
    .welcome-box h2 {
        background: linear-gradient(135deg, #1a73e8, #4285f4);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        font-size: 32px;
        margin: 16px 0 8px 0;
    }
    .welcome-box p {
        color: #5f6368;
        font-size: 16px;
        line-height: 1.5;
    }

    /* Top bar brand row */
    .top-brand {
        display: flex;
        align-items: baseline;
        gap: 12px;
    }
    .top-brand-name {
        font-size: 28px;
        font-weight: 800;
        background: linear-gradient(135deg, #1a73e8, #4285f4);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        letter-spacing: -0.5px;
    }
    .top-brand-sub {
        font-size: 14px;
        color: #5f6368;
        font-weight: 400;
    }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Database helpers (synchronous – Streamlit-friendly)
# ---------------------------------------------------------------------------
def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _load_user_actions() -> dict[int, str]:
    """Load all user actions as {job_id: action_string}."""
    conn = _get_conn()
    try:
        cursor = conn.execute("SELECT job_id, action FROM user_actions")
        return {row["job_id"]: row["action"] for row in cursor.fetchall()}
    except Exception:
        return {}
    finally:
        conn.close()


def _set_user_action(job_id: int, action: str) -> None:
    """Set or replace a user action on a job (sync).
    When action is 'applied', auto-creates an application entry if none exists."""
    from datetime import datetime, timezone
    conn = _get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO user_actions (job_id, action, timestamp, notes) VALUES (?, ?, ?, '')",
            (job_id, action, now),
        )
        # Auto-create application tracking when marking as "applied"
        if action == "applied":
            from src.pipeline.reminders import compute_next_reminder
            from src.pipeline.tracker import PipelineStage
            next_rem = compute_next_reminder(PipelineStage.applied, now)
            conn.execute(
                "INSERT OR IGNORE INTO applications "
                "(job_id, status, date_applied, next_reminder, notes, last_updated) "
                "VALUES (?, ?, ?, ?, '', ?)",
                (job_id, "applied", now, next_rem, now),
            )
        conn.commit()
    finally:
        conn.close()


def _remove_user_action(job_id: int) -> None:
    """Remove a user action on a job (sync)."""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM user_actions WHERE job_id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()


@st.cache_data(ttl=60)
def load_jobs_7day() -> list[dict]:
    """Load jobs from last 7 days with score >= MIN_MATCH_SCORE."""
    conn = _get_conn()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        cursor = conn.execute(
            "SELECT * FROM jobs WHERE first_seen >= ? AND match_score >= ? ORDER BY date_found DESC",
            (cutoff, MIN_MATCH_SCORE),
        )
        rows = [dict(row) for row in cursor.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()

    # Join user actions
    actions = _load_user_actions()
    for job in rows:
        job["user_action"] = actions.get(job.get("id"), "")

    # Compute bucket index and salary display for each job
    for job in rows:
        age = get_job_age_hours(job.get("date_found", ""), job.get("first_seen", ""))
        job["bucket_idx"] = assign_bucket(age)
        job["age_hours"] = age
        # Salary formatting
        smin, smax = job.get("salary_min"), job.get("salary_max")
        if smin and smax:
            job["salary_display"] = f"\u00a3{int(smin):,} \u2013 \u00a3{int(smax):,}"
        elif smin:
            job["salary_display"] = f"\u00a3{int(smin):,}+"
        elif smax:
            job["salary_display"] = f"Up to \u00a3{int(smax):,}"
        else:
            job["salary_display"] = "Not disclosed"
        job["has_salary"] = bool(smin or smax)
    return rows


@st.cache_data(ttl=60)
def load_run_logs() -> pd.DataFrame:
    conn = _get_conn()
    try:
        df = pd.read_sql_query("SELECT * FROM run_log ORDER BY id DESC", conn)
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()

    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df["per_source"] = df["per_source"].apply(lambda x: json.loads(x) if x else {})
    return df


# ---------------------------------------------------------------------------
# Job table renderer
# ---------------------------------------------------------------------------
def render_job_table(jobs: list[dict]) -> str:
    """Render a list of jobs as a single compact HTML table string."""
    if not jobs:
        return '<p style="color:#999;font-size:13px;padding:4px 0;">No jobs in this period</p>'

    rows = []
    for job in jobs:
        score = job.get("match_score", 0)
        color = score_color_hex(score)
        visa = job.get("visa_flag", False)
        url = html.escape(job.get("apply_url", "#"))
        title = html.escape(job.get("title", "Unknown"))
        company = html.escape(job.get("company", "Unknown"))
        location = html.escape(job.get("location", "N/A"))
        salary = html.escape(job.get("salary_display", ""))
        posted = html.escape(format_relative_time(job.get("date_found", "")))
        source = html.escape(job.get("source", ""))

        # Badges column
        badges = f'<span class="jrow-pill jrow-pill-source">{source}</span>'
        if visa:
            badges += ' <span class="jrow-pill jrow-pill-visa">Visa</span>'

        # Skills tooltip
        skills = extract_matched_skills(job.get("description", ""))
        skill_count = sum(len(v) for v in skills.values())
        if skill_count:
            tip_lines = []
            for tier, tier_skills in skills.items():
                if tier_skills:
                    tip_lines.append(f"{tier.title()}: {', '.join(tier_skills)}")
            tip_text = html.escape("\n".join(tip_lines))
            badges += (
                f' <span class="jrow-skills">'
                f'<span class="jrow-pill">{skill_count} skills</span>'
                f'<span class="jrow-skills-tip">{tip_text}</span>'
                f'</span>'
            )

        # Match breakdown tooltip (from match_data JSON)
        breakdown_tip = ""
        raw_md = job.get("match_data", "")
        if raw_md:
            try:
                md = json.loads(raw_md) if isinstance(raw_md, str) else raw_md
                dim_colors = {
                    "role": "#42a5f5", "skill": "#66bb6a", "seniority": "#ab47bc",
                    "experience": "#ffa726", "credentials": "#26c6da",
                    "location": "#ef5350", "recency": "#78909c", "semantic": "#ec407a",
                }
                dim_html = ""
                for dim in ["role", "skill", "seniority", "experience",
                            "credentials", "location", "recency", "semantic"]:
                    val = md.get(dim, 0)
                    if val is None:
                        val = 0
                    pct = min(100, max(0, int(val * 100)))
                    dim_color = dim_colors.get(dim, "#888")
                    dim_html += (
                        f'<div class="jrow-dim-row">'
                        f'<span class="jrow-dim-label">{dim}</span>'
                        f'<span class="jrow-dim-bar">'
                        f'<span class="jrow-dim-fill" style="width:{pct}%;background:{dim_color}"></span>'
                        f'</span>'
                        f'<span class="jrow-dim-val">{pct}%</span>'
                        f'</div>'
                    )
                # Matched / missing skills pills
                skill_pills = ""
                matched = md.get("matched", [])
                missing_req = md.get("missing_required", [])
                missing_pref = md.get("missing_preferred", [])
                transferable = md.get("transferable", [])
                if matched:
                    skill_pills += '<div style="margin-top:4px">'
                    for s in matched[:8]:
                        skill_pills += f'<span class="jrow-skill-pill jrow-skill-match">{html.escape(str(s))}</span>'
                    skill_pills += '</div>'
                if missing_req:
                    skill_pills += '<div>'
                    for s in missing_req[:5]:
                        skill_pills += f'<span class="jrow-skill-pill jrow-skill-miss">{html.escape(str(s))}</span>'
                    skill_pills += '</div>'
                if transferable:
                    skill_pills += '<div>'
                    for s in transferable[:5]:
                        skill_pills += f'<span class="jrow-skill-pill jrow-skill-transfer">{html.escape(str(s))}</span>'
                    skill_pills += '</div>'
                breakdown_tip = (
                    f'<span class="jrow-breakdown-tip">'
                    f'<div style="font-weight:700;margin-bottom:4px;font-size:12px">Score Breakdown</div>'
                    f'{dim_html}{skill_pills}'
                    f'</span>'
                )
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass

        # User action badge
        user_action = job.get("user_action", "")
        action_badge = ""
        if user_action == "liked":
            action_badge = '<span class="jrow-pill" style="background:#e3f2fd;color:#1565c0">Liked</span>'
        elif user_action == "applied":
            action_badge = '<span class="jrow-pill" style="background:#e8f5e9;color:#2e7d32">Applied</span>'
        elif user_action == "not_interested":
            action_badge = '<span class="jrow-pill" style="background:#fbe9e7;color:#bf360c">Pass</span>'

        # Score cell: wrap in breakdown tooltip if match_data available
        if breakdown_tip:
            score_cell = (
                f'<td><span class="jrow-breakdown">'
                f'<span class="jrow-score" style="background:{color}">{score}</span>'
                f'{breakdown_tip}</span></td>'
            )
        else:
            score_cell = f'<td><span class="jrow-score" style="background:{color}">{score}</span></td>'

        rows.append(
            f'<tr class="jrow">'
            f'{score_cell}'
            f'<td class="jrow-title"><a href="{url}" target="_blank">{title}</a>'
            f'<span class="jrow-company">@ {company}</span></td>'
            f'<td class="jrow-loc">{location}</td>'
            f'<td class="jrow-salary">{salary}</td>'
            f'<td class="jrow-time">{posted}</td>'
            f'<td>{badges}</td>'
            f'<td>{action_badge}</td>'
            f'</tr>'
        )

    header = (
        '<table class="jrow-table"><thead><tr>'
        '<th style="width:50px">Score</th>'
        '<th>Title / Company</th>'
        '<th>Location</th>'
        '<th>Salary</th>'
        '<th>Posted</th>'
        '<th>Tags</th>'
        '<th>Action</th>'
        '</tr></thead><tbody>'
    )
    return header + "\n".join(rows) + "</tbody></table>"


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
all_jobs = load_jobs_7day()
df_runs = load_run_logs()

# ---------------------------------------------------------------------------
# Profile state + pre-population
# ---------------------------------------------------------------------------
_has_profile = profile_exists()
_loaded_prof = load_profile() if _has_profile else None

# Extract existing values for form pre-population
_existing_titles = ""
_existing_skills = ""
_existing_about = ""
_existing_neg = ""
_existing_locs: list[str] = []
_existing_arrangement = ""
_existing_salary_min = 0
_existing_salary_max = 0

if _loaded_prof:
    _p = _loaded_prof.preferences
    _existing_titles = ", ".join(_p.target_job_titles) if _p.target_job_titles else ""
    _existing_skills = ", ".join(_p.additional_skills) if _p.additional_skills else ""
    _existing_about = _p.about_me or ""
    _existing_neg = ", ".join(_p.negative_keywords) if _p.negative_keywords else ""
    _existing_locs = _p.preferred_locations or []
    _existing_arrangement = _p.work_arrangement or ""
    _existing_salary_min = int(_p.salary_min) if _p.salary_min else 0
    _existing_salary_max = int(_p.salary_max) if _p.salary_max else 0

_arrangement_options = ["", "remote", "hybrid", "onsite"]
_arrangement_idx = (
    _arrangement_options.index(_existing_arrangement)
    if _existing_arrangement in _arrangement_options
    else 0
)
_location_options = [
    "London", "Manchester", "Birmingham", "Edinburgh",
    "Cambridge", "Bristol", "Remote", "Hybrid",
]
_valid_existing_locs = [loc for loc in _existing_locs if loc in _location_options]

# ---------------------------------------------------------------------------
# Sidebar — Profile Setup (always visible, never collapsed)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        '<div class="profile-header">\U0001F464 Profile Setup</div>',
        unsafe_allow_html=True,
    )

    if not _has_profile:
        st.markdown(
            '<div class="cv-mandatory-badge">'
            '\u26A0\uFE0F CV Required \u2014 Upload to start searching'
            '</div>',
            unsafe_allow_html=True,
        )

    with st.container(border=True):
        uploaded_cv = st.file_uploader("Upload CV (Mandatory)", type=["pdf", "docx"])
        prof_titles = st.text_area(
            "Target Job Titles (comma-separated)",
            value=_existing_titles,
            placeholder="e.g. Software Engineer, Product Manager",
        )
        prof_skills = st.text_area(
            "Skills (comma-separated)",
            value=_existing_skills,
            placeholder="e.g. Python, SQL, Project Management",
        )
        prof_about = st.text_area(
            "About Me",
            value=_existing_about,
            placeholder="Brief summary of your background",
        )
        prof_negatives = st.text_input(
            "Exclude Keywords (comma-separated)",
            value=_existing_neg,
            placeholder="e.g. intern, junior",
        )
        prof_locations = st.multiselect(
            "Preferred Locations",
            _location_options,
            default=_valid_existing_locs,
        )
        prof_arrangement = st.selectbox(
            "Work Arrangement",
            _arrangement_options,
            index=_arrangement_idx,
        )
        prof_salary_col1, prof_salary_col2 = st.columns(2)
        with prof_salary_col1:
            prof_salary_min = st.number_input(
                "Salary Min", min_value=0, value=_existing_salary_min, step=5000,
            )
        with prof_salary_col2:
            prof_salary_max = st.number_input(
                "Salary Max", min_value=0, value=_existing_salary_max, step=5000,
            )

        st.markdown("---")
        st.markdown("**LinkedIn Data Export**")
        st.caption("Download your data from LinkedIn Settings > Get a copy of your data")
        uploaded_linkedin = st.file_uploader("Upload LinkedIn ZIP", type=["zip"])

        st.markdown("---")
        st.markdown("**GitHub Profile**")
        prof_github = st.text_input("GitHub Username", placeholder="e.g. octocat")

        if st.button("Save Profile", type="primary", use_container_width=True):
            cv_data = CVData()
            if uploaded_cv:
                cv_data = parse_cv_from_bytes(uploaded_cv.read(), uploaded_cv.name)

            # Enrich from LinkedIn
            if uploaded_linkedin:
                try:
                    from src.profile.linkedin_parser import parse_linkedin_zip_from_bytes, enrich_cv_from_linkedin
                    linkedin_data = parse_linkedin_zip_from_bytes(uploaded_linkedin.read())
                    cv_data = enrich_cv_from_linkedin(cv_data, linkedin_data)
                    n_skills = len(linkedin_data.get("skills", []))
                    n_pos = len(linkedin_data.get("positions", []))
                    st.info(f"LinkedIn: {n_skills} skills, {n_pos} positions imported")
                except Exception as e:
                    st.error(f"Failed to parse LinkedIn ZIP: {e}")

            # Enrich from GitHub
            if prof_github:
                try:
                    from src.profile.github_enricher import fetch_github_profile, enrich_cv_from_github
                    github_data = _run_async(fetch_github_profile(prof_github))
                    cv_data = enrich_cv_from_github(cv_data, github_data)
                    n_repos = len(github_data.get("repositories", []))
                    n_skills = len(github_data.get("skills_inferred", []))
                    st.info(f"GitHub: {n_repos} repos, {n_skills} skills inferred")
                except Exception as e:
                    st.error(f"Failed to fetch GitHub data: {e}")

            prefs = UserPreferences(
                target_job_titles=[t.strip() for t in prof_titles.split(",") if t.strip()],
                additional_skills=[s.strip() for s in prof_skills.split(",") if s.strip()],
                negative_keywords=[n.strip() for n in prof_negatives.split(",") if n.strip()],
                preferred_locations=prof_locations,
                work_arrangement=prof_arrangement,
                salary_min=prof_salary_min if prof_salary_min > 0 else None,
                salary_max=prof_salary_max if prof_salary_max > 0 else None,
                about_me=prof_about,
            )

            if cv_data.skills or cv_data.job_titles:
                prefs = merge_cv_and_preferences(cv_data.skills, cv_data.job_titles, prefs)

            profile = UserProfile(cv_data=cv_data, preferences=prefs)
            save_profile(profile)
            st.toast("Profile saved! Next search will use your keywords.")
            st.rerun()

    # Status indicator
    if _has_profile:
        _sources = ["CV"] if (_loaded_prof and _loaded_prof.cv_data.raw_text) else []
        if _loaded_prof and _loaded_prof.cv_data.linkedin_skills:
            _sources.append("LinkedIn")
        if _loaded_prof and _loaded_prof.cv_data.github_skills_inferred:
            _sources.append("GitHub")
        _src_label = ", ".join(_sources) if _sources else "Manual"
        st.markdown(
            f'<div class="profile-status-active">\u25CF Profile Active ({_src_label})</div>',
            unsafe_allow_html=True,
        )
        # Display detected domains
        if _loaded_prof:
            from src.profile.domain_detector import detect_domains
            _detected = detect_domains(_loaded_prof)
            if _detected:
                st.markdown(f"**Domains:** {' · '.join(_detected)}")
    else:
        st.markdown(
            '<div class="profile-status-inactive">\u25CB No Profile</div>',
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------------
# Top Bar — Title + Filter / Sort / Actions popovers
# ---------------------------------------------------------------------------
col_title, col_filters, col_sort, col_actions = st.columns([6, 1, 1, 1])

with col_title:
    _mode_label = "Profile-driven" if _has_profile else "Upload CV to start"
    st.markdown(
        f'<div class="top-brand">'
        f'<span class="top-brand-name">Job360</span>'
        f'<span class="top-brand-sub">{_mode_label} Job Search \u2014 UK &amp; Remote</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

# Populate filter options from data
if all_jobs:
    all_locations = sorted(set(j.get("location", "") for j in all_jobs if j.get("location")))
    all_sources = sorted(set(j.get("source", "") for j in all_jobs if j.get("source")))
else:
    all_locations = []
    all_sources = []

with col_filters:
    with st.popover("\U0001F50D Filters", use_container_width=True):
        search_text = st.text_input("Search", placeholder="e.g. AI Engineer London")
        time_range = st.selectbox("Time Range", [
            "Last 7 days", "Last 24h", "Last 48h", "Last 3 days",
            "Last 5 days",
        ])
        min_score = st.slider("Min Score", 30, 100, MIN_MATCH_SCORE)
        selected_locations = st.multiselect("Location", all_locations)
        work_arrangement = st.selectbox(
            "Work Type", ["Any", "Remote", "Hybrid", "On-site"],
        )
        selected_sources = st.multiselect("Source", all_sources)
        visa_filter = st.radio("Visa Sponsorship", ["All", "Only sponsorship mentioned"])
        salary_band = st.selectbox(
            "Salary", ["Any", "£35K+", "£45K+", "£55K+", "£65K+", "£75K+"],
        )
        # Build job type options from results
        all_job_types = sorted(set(
            j.get("job_type", "") for j in all_jobs if j.get("job_type")
        ))
        selected_job_types = st.multiselect("Job Type", all_job_types)
        action_filter = st.selectbox(
            "My Actions", ["All", "Liked", "Applied", "Not Interested", "No action"],
        )

with col_sort:
    with st.popover("\u2195\uFE0F Sort", use_container_width=True):
        sort_option = st.radio("Sort by", [
            "Score (high \u2192 low)",
            "Score (low \u2192 high)",
            "Date (newest first)",
            "Date (oldest first)",
            "Salary (high \u2192 low)",
            "Salary (low \u2192 high)",
        ])

with col_actions:
    with st.popover("\u26A1 Actions", use_container_width=True):
        trigger_search = st.button(
            "\U0001F680 Refresh Now", type="primary", use_container_width=True,
        )
        export_csv_btn = st.button("\U0001F4E5 Export CSV", use_container_width=True)
        export_md_btn = st.button("\U0001F4DD Export Markdown", use_container_width=True)

# ---------------------------------------------------------------------------
# Apply filters
# ---------------------------------------------------------------------------
filtered_jobs = list(all_jobs)

if search_text:
    q = search_text.lower()
    filtered_jobs = [
        j for j in filtered_jobs
        if q in j.get("title", "").lower()
        or q in j.get("company", "").lower()
        or q in j.get("location", "").lower()
    ]

# Time range filter
time_max_hours = {
    "Last 7 days": 168, "Last 24h": 24, "Last 48h": 48,
    "Last 3 days": 72, "Last 5 days": 120,
}
max_hours = time_max_hours.get(time_range, 168)
if max_hours < 168:
    filtered_jobs = [j for j in filtered_jobs if j.get("age_hours", 999) <= max_hours]

# Score filter
filtered_jobs = [j for j in filtered_jobs if j.get("match_score", 0) >= min_score]

# Location filter
if selected_locations:
    filtered_jobs = [j for j in filtered_jobs if j.get("location") in selected_locations]

# Source filter
if selected_sources:
    filtered_jobs = [j for j in filtered_jobs if j.get("source") in selected_sources]

# Visa filter
if visa_filter == "Only sponsorship mentioned":
    filtered_jobs = [j for j in filtered_jobs if j.get("visa_flag")]

# Salary band filter
_SALARY_BAND_MAP = {
    "Any": 0, "£35K+": 35000, "£45K+": 45000,
    "£55K+": 55000, "£65K+": 65000, "£75K+": 75000,
}
band_min = _SALARY_BAND_MAP.get(salary_band, 0)
if band_min > 0:
    filtered_jobs = [
        j for j in filtered_jobs
        if (j.get("salary_max") or 0) >= band_min
    ]

# Work arrangement filter
if work_arrangement != "Any":
    wa_lower = work_arrangement.lower()
    filtered_jobs = [
        j for j in filtered_jobs
        if wa_lower in (j.get("location", "") or "").lower()
    ]

# Job type filter
if selected_job_types:
    filtered_jobs = [
        j for j in filtered_jobs
        if j.get("job_type", "") in selected_job_types
    ]

# Action filter
if action_filter == "Liked":
    filtered_jobs = [j for j in filtered_jobs if j.get("user_action") == "liked"]
elif action_filter == "Applied":
    filtered_jobs = [j for j in filtered_jobs if j.get("user_action") == "applied"]
elif action_filter == "Not Interested":
    filtered_jobs = [j for j in filtered_jobs if j.get("user_action") == "not_interested"]
elif action_filter == "No action":
    filtered_jobs = [j for j in filtered_jobs if not j.get("user_action")]

# ---------------------------------------------------------------------------
# Apply sort
# ---------------------------------------------------------------------------
_sort_map = {
    "Score (high \u2192 low)": ("match_score", True),
    "Score (low \u2192 high)": ("match_score", False),
    "Date (newest first)": ("date_found", True),
    "Date (oldest first)": ("date_found", False),
    "Salary (high \u2192 low)": ("salary_max", True),
    "Salary (low \u2192 high)": ("salary_min", False),
}
_sort_key, _sort_reverse = _sort_map.get(sort_option, ("match_score", True))
_sort_default = "" if "date" in _sort_key else 0
filtered_jobs.sort(key=lambda j: j.get(_sort_key) or _sort_default, reverse=_sort_reverse)

# ---------------------------------------------------------------------------
# Trigger new search
# ---------------------------------------------------------------------------
if trigger_search:
    with st.spinner("Fetching from all sources... this takes 2-3 minutes"):
        result = subprocess.run(
            [sys.executable, "-m", "src.main"],
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=300,
        )
    if result.returncode == 0:
        st.toast("Search complete! Refreshing data...")
        st.cache_data.clear()
        st.rerun()
    else:
        st.error(f"Search failed:\n```\n{result.stderr[-1000:]}\n```")

# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------
if not all_jobs:
    st.markdown(
        '<div class="welcome-box">'
        '<div style="font-size:64px">\U0001F4BC</div>'
        '<h2>Welcome to Job360</h2>'
        '<p>Your intelligent job search companion</p>'
        '<p>Upload your CV in the sidebar to get started, '
        'then click <b>\u26A1 Actions \u2192 \U0001F680 Refresh Now</b>.</p>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.stop()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_jobs, tab_pipeline = st.tabs(["\U0001F4CB Jobs", "\U0001F4CA Pipeline"])

with tab_pipeline:
    def _load_applications() -> list[dict]:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT a.*, j.title, j.company, j.location, j.apply_url "
                "FROM applications a JOIN jobs j ON a.job_id = j.id "
                "ORDER BY a.last_updated DESC"
            ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []
        finally:
            conn.close()

    apps = _load_applications()
    if not apps:
        st.info("No applications tracked yet. Mark a job as 'Applied' to start tracking.")
    else:
        # Stage metrics
        stage_counts = {}
        for app in apps:
            s = app.get("status", "unknown")
            stage_counts[s] = stage_counts.get(s, 0) + 1

        stage_cols = st.columns(min(len(stage_counts), 6))
        for i, (stage_name, count) in enumerate(stage_counts.items()):
            stage_cols[i % len(stage_cols)].metric(stage_name.replace("_", " ").title(), count)

        # Due reminders
        from src.pipeline.reminders import get_pending_reminders, format_reminder_message
        due = get_pending_reminders(apps)
        if due:
            st.warning(f"{len(due)} reminder(s) due!")
            for app in due:
                st.caption(format_reminder_message(app))

        # Kanban-style columns per stage
        all_stages = ["applied", "outreach_week1", "outreach_week2", "outreach_week3",
                       "interview", "offer", "rejected", "withdrawn"]
        active_stages = [s for s in all_stages if s in stage_counts]
        if active_stages:
            kanban_cols = st.columns(len(active_stages))
            for col_idx, stage_name in enumerate(active_stages):
                with kanban_cols[col_idx]:
                    st.subheader(stage_name.replace("_", " ").title())
                    stage_apps = [a for a in apps if a.get("status") == stage_name]
                    for app in stage_apps:
                        with st.container(border=True):
                            st.markdown(
                                f"**{app.get('title', '?')}**  \n"
                                f"_{app.get('company', '?')}_  \n"
                                f"Applied: {app.get('date_applied', '?')[:10]}"
                            )

with tab_jobs:
    # -----------------------------------------------------------------------
    # Summary metrics
    # -----------------------------------------------------------------------
    bucketed = bucket_jobs(filtered_jobs, min_score=0)  # already filtered above
    counts = bucket_summary_counts(bucketed)

    source_counts = {}
    for j in filtered_jobs:
        s = j.get("source", "")
        source_counts[s] = source_counts.get(s, 0) + 1

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Jobs", len(filtered_jobs))
    c2.metric("New 24h", counts["last_24h"])
    c3.metric("Avg Score", f"{sum(j.get('match_score', 0) for j in filtered_jobs) / max(len(filtered_jobs), 1):.0f}")
    c4.metric("Visa Count", sum(1 for j in filtered_jobs if j.get("visa_flag")))
    c5.metric("Liked", sum(1 for j in filtered_jobs if j.get("user_action") == "liked"))

    # Inline mini score distribution
    if filtered_jobs:
        df_mini = pd.DataFrame(filtered_jobs)
        fig_mini = px.histogram(
            df_mini, x="match_score", nbins=20,
            color_discrete_sequence=["#1a73e8"],
            labels={"match_score": "Score", "count": ""},
        )
        fig_mini.add_vline(x=MIN_MATCH_SCORE, line_dash="dash", line_color="red")
        fig_mini.update_layout(
            height=100, margin=dict(l=0, r=0, t=0, b=0),
            showlegend=False, bargap=0.1,
            xaxis=dict(showticklabels=True, dtick=10),
            yaxis=dict(showticklabels=False),
        )
        st.plotly_chart(fig_mini, use_container_width=True)

    # -------------------------------------------------------------------
    # Time-bucketed job tables
    # -------------------------------------------------------------------
    BUCKET_COLORS = ["#f44336", "#ff9800", "#ffc107", "#4CAF50", "#2196f3"]

    # Bucket navigation pills
    nav_pills = []
    for idx in range(len(BUCKETS)):
        label, _, _, _, _ = BUCKETS[idx]
        count = len(bucketed.get(idx, []))
        color = BUCKET_COLORS[idx]
        nav_pills.append(
            f'<span class="bucket-nav-pill">'
            f'<span class="bn-dot" style="background:{color}"></span>'
            f'{label}: {count}'
            f'</span>'
        )
    st.markdown('<div class="bucket-nav">' + "".join(nav_pills) + '</div>', unsafe_allow_html=True)

    for idx in range(len(BUCKETS)):
        label, emoji_unicode, _, _, _ = BUCKETS[idx]
        bucket_list = bucketed.get(idx, [])
        count = len(bucket_list)

        st.markdown(
            f'<div class="bucket-hdr bucket-hdr-{idx}">'
            f'{emoji_unicode} {label}<span class="bucket-count">{count}</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown(render_job_table(bucket_list), unsafe_allow_html=True)

        # Action buttons for each job in this bucket
        for job in bucket_list:
            job_id = job.get("id")
            if job_id is None:
                continue
            current_action = job.get("user_action", "")
            cols = st.columns([3, 1, 1, 1])
            cols[0].caption(f"{job.get('title', '')} @ {job.get('company', '')}")
            if cols[1].button("Like" if current_action != "liked" else "Unlike",
                              key=f"like_{idx}_{job_id}"):
                if current_action == "liked":
                    _remove_user_action(job_id)
                else:
                    _set_user_action(job_id, "liked")
                st.cache_data.clear()
                st.rerun()
            if cols[2].button("Applied" if current_action != "applied" else "Unapply",
                              key=f"applied_{idx}_{job_id}"):
                if current_action == "applied":
                    _remove_user_action(job_id)
                else:
                    _set_user_action(job_id, "applied")
                st.cache_data.clear()
                st.rerun()
            if cols[3].button("Pass" if current_action != "not_interested" else "Unpass",
                              key=f"pass_{idx}_{job_id}"):
                if current_action == "not_interested":
                    _remove_user_action(job_id)
                else:
                    _set_user_action(job_id, "not_interested")
                st.cache_data.clear()
                st.rerun()

    # -------------------------------------------------------------------
    # Export buttons
    # -------------------------------------------------------------------
    if export_csv_btn and filtered_jobs:
        df_export = pd.DataFrame(filtered_jobs)
        csv_data = df_export.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download CSV",
            data=csv_data,
            file_name=f"job360_export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
        )

    if export_md_btn and filtered_jobs:
        # Convert dicts to Job objects for report generator
        job_objs = []
        for j in filtered_jobs:
            job_objs.append(Job(
                title=j.get("title", ""),
                company=j.get("company", ""),
                apply_url=j.get("apply_url", ""),
                source=j.get("source", ""),
                date_found=j.get("date_found", ""),
                location=j.get("location", ""),
                salary_min=j.get("salary_min"),
                salary_max=j.get("salary_max"),
                description=j.get("description", ""),
                match_score=j.get("match_score", 0),
                visa_flag=bool(j.get("visa_flag")),
            ))
        stats = {"total_found": len(filtered_jobs), "new_jobs": len(filtered_jobs), "per_source": source_counts}
        md_report = generate_markdown_report(job_objs, stats)
        st.download_button(
            label="Download Markdown",
            data=md_report.encode("utf-8"),
            file_name=f"job360_report_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
            mime="text/markdown",
        )

    # -------------------------------------------------------------------
    # Charts + Run History
    # -------------------------------------------------------------------
    with st.expander("Charts & Run History", expanded=False):
        # Source pie chart
        chart_left, chart_right = st.columns(2)
        with chart_left:
            if filtered_jobs:
                sc = pd.DataFrame(list(source_counts.items()), columns=["source", "count"])
                fig_pie = px.pie(sc, values="count", names="source", title="Jobs by Source", hole=0.35)
                fig_pie.update_layout(height=350)
                st.plotly_chart(fig_pie, use_container_width=True)
        with chart_right:
            if not df_runs.empty and len(df_runs) > 1:
                fig_line = px.line(
                    df_runs.sort_values("timestamp"), x="timestamp", y="new_jobs",
                    title="New Jobs per Run", markers=True,
                )
                fig_line.update_layout(height=350)
                st.plotly_chart(fig_line, use_container_width=True)

        # Run history table + per-source bar
        if not df_runs.empty:
            col_a, col_b = st.columns(2)
            with col_a:
                st.dataframe(
                    df_runs[["timestamp", "total_found", "new_jobs"]].rename(columns={
                        "timestamp": "Time", "total_found": "Total Found", "new_jobs": "New Jobs",
                    }),
                    use_container_width=True, hide_index=True,
                )
            with col_b:
                if len(df_runs):
                    latest = df_runs.iloc[0]
                    ps = latest["per_source"]
                    if ps:
                        ps_df = pd.DataFrame(list(ps.items()), columns=["Source", "Jobs Found"]).sort_values("Jobs Found", ascending=False)
                        fig_bar = px.bar(ps_df, x="Source", y="Jobs Found", title="Latest Run \u2014 Per Source", color_discrete_sequence=["#34a853"])
                        fig_bar.update_layout(height=300)
                        st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("No runs recorded yet.")
