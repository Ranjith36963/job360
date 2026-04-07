"""Job360 Web Dashboard — Streamlit UI with compact table-row layout."""

import html
import logging
import sqlite3
import json
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("job360.dashboard")

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB


def _safe_url(url: str) -> str:
    """Sanitize URL to prevent javascript: and data: URI attacks."""
    if not url:
        return "#"
    stripped = url.strip()
    parsed = urllib.parse.urlparse(stripped)
    if parsed.scheme and parsed.scheme.lower() not in ("http", "https"):
        return "#"
    return html.escape(stripped)


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
    page_title="Job360 Dashboard",
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
    .bucket-hdr-3 { border-color: #2196f3; }
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
    /* Tighten Streamlit default padding */
    .block-container { padding-top: 1.5rem !important; }
    div[data-testid="stMetric"] { padding: 4px 0 !important; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Database helpers (synchronous – Streamlit-friendly)
# ---------------------------------------------------------------------------
def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


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
    except Exception as e:
        logger.error("Failed to load jobs: %s", e)
        return []
    finally:
        conn.close()

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
    except Exception as e:
        logger.error("Failed to load run logs: %s", e)
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
        url = _safe_url(job.get("apply_url", ""))
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

        rows.append(
            f'<tr class="jrow">'
            f'<td><span class="jrow-score" style="background:{color}">{score}</span></td>'
            f'<td class="jrow-title"><a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a>'
            f'<span class="jrow-company">@ {company}</span></td>'
            f'<td class="jrow-loc">{location}</td>'
            f'<td class="jrow-salary">{salary}</td>'
            f'<td class="jrow-time">{posted}</td>'
            f'<td>{badges}</td>'
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
        '</tr></thead><tbody>'
    )
    return header + "\n".join(rows) + "</tbody></table>"


# ---------------------------------------------------------------------------
# Load data (lazy — avoids crash if DB doesn't exist at import time)
# ---------------------------------------------------------------------------
def _load_data():
    return load_jobs_7day(), load_run_logs()

all_jobs, df_runs = _load_data()

# ---------------------------------------------------------------------------
# Sidebar — Profile Setup
# ---------------------------------------------------------------------------
_has_profile = profile_exists()

with st.sidebar:
    with st.expander("Profile Setup", expanded=not _has_profile):
        uploaded_cv = st.file_uploader("Upload CV", type=["pdf", "docx"])
        if uploaded_cv and uploaded_cv.size > MAX_UPLOAD_SIZE:
            st.error(f"File too large ({uploaded_cv.size / 1024 / 1024:.1f} MB). Maximum: 10 MB.")
            uploaded_cv = None
        prof_titles = st.text_area("Target Job Titles (comma-separated)",
                                   placeholder="e.g. Software Engineer, Product Manager")
        prof_skills = st.text_area("Skills (comma-separated)",
                                   placeholder="e.g. Python, SQL, Project Management")
        prof_about = st.text_area("About Me", placeholder="Brief summary of your background")
        prof_negatives = st.text_input("Exclude Keywords (comma-separated)",
                                       placeholder="e.g. intern, junior")
        prof_locations = st.multiselect("Preferred Locations",
                                        ["London", "Manchester", "Birmingham", "Edinburgh",
                                         "Cambridge", "Bristol", "Remote", "Hybrid"])
        prof_arrangement = st.selectbox("Work Arrangement",
                                        ["", "remote", "hybrid", "onsite"])
        prof_salary_col1, prof_salary_col2 = st.columns(2)
        with prof_salary_col1:
            prof_salary_min = st.number_input("Salary Min", min_value=0, value=0, step=5000)
        with prof_salary_col2:
            prof_salary_max = st.number_input("Salary Max", min_value=0, value=0, step=5000)

        # LinkedIn data export
        st.markdown("---")
        st.markdown("**LinkedIn Data Export**")
        st.caption("Download your data from LinkedIn Settings > Get a copy of your data")
        uploaded_linkedin = st.file_uploader("Upload LinkedIn ZIP", type=["zip"])
        if uploaded_linkedin and uploaded_linkedin.size > MAX_UPLOAD_SIZE:
            st.error(f"File too large ({uploaded_linkedin.size / 1024 / 1024:.1f} MB). Maximum: 10 MB.")
            uploaded_linkedin = None

        # GitHub username
        st.markdown("---")
        st.markdown("**GitHub Profile**")
        prof_github = st.text_input("GitHub Username", placeholder="e.g. octocat")

        if st.button("Save Profile"):
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
                    import asyncio
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
                github_username=prof_github or "",
            )

            if cv_data.skills or cv_data.job_titles:
                prefs = merge_cv_and_preferences(cv_data.skills, cv_data.job_titles, prefs)

            profile = UserProfile(cv_data=cv_data, preferences=prefs)
            save_profile(profile)
            st.success("Profile saved! Next search will use your keywords.")
            st.rerun()

        if _has_profile:
            _loaded_prof = load_profile()
            _sources = ["CV"] if (_loaded_prof and _loaded_prof.cv_data.raw_text) else []
            if _loaded_prof and _loaded_prof.cv_data.linkedin_skills:
                _sources.append("LinkedIn")
            if _loaded_prof and _loaded_prof.cv_data.github_skills_inferred:
                _sources.append("GitHub")
            _src_label = ", ".join(_sources) if _sources else "Manual"
            st.caption(f"Profile active ({_src_label}) — searches use your keywords")
        else:
            st.caption("No profile — using default keywords")

    st.divider()

# ---------------------------------------------------------------------------
# Sidebar — Filters
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("\U0001F50D Filters")

    search_text = st.text_input("Search", placeholder="e.g. AI Engineer London")

    time_range = st.selectbox("Time Range", [
        "All 7 days", "Last 24h", "Last 48h", "Last 72h", "Last 3 days",
    ])

    min_score = st.slider("Min Score", 30, 100, MIN_MATCH_SCORE)

    # Populate options from data
    if all_jobs:
        all_locations = sorted(set(j.get("location", "") for j in all_jobs if j.get("location")))
        all_sources = sorted(set(j.get("source", "") for j in all_jobs if j.get("source")))
    else:
        all_locations = []
        all_sources = []

    selected_locations = st.multiselect("Location", all_locations)
    selected_sources = st.multiselect("Source", all_sources)

    visa_filter = st.radio("Visa Sponsorship", ["All", "Only sponsorship mentioned"])
    salary_filter = st.radio("Salary", ["All", "Has salary"])

    st.divider()
    st.subheader("\u2699\uFE0F Actions")
    trigger_search = st.button("\U0001F680 Refresh Now", width='stretch')
    export_csv_btn = st.button("\U0001F4E5 Export CSV", width='stretch')
    export_md_btn = st.button("\U0001F4DD Export Markdown", width='stretch')

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
time_max_hours = {"All 7 days": 168, "Last 24h": 24, "Last 48h": 48, "Last 72h": 72, "Last 3 days": 72}
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

# Salary filter
if salary_filter == "Has salary":
    filtered_jobs = [j for j in filtered_jobs if j.get("has_salary")]

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
        st.success("Search complete! Refreshing data...")
        st.cache_data.clear()
        st.rerun()
    else:
        st.error(f"Search failed:\n```\n{result.stderr[-1000:]}\n```")

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.title("\U0001F4BC Job360 Dashboard")
_mode_label = "Profile-driven" if _has_profile else "Default"
st.caption(f"{_mode_label} Job Search Aggregator \u2014 UK & Remote \u2014 Time-Prioritized View")

# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------
if not all_jobs:
    st.info("No jobs in the database yet. Click **Refresh Now** in the sidebar to get started!")
    st.stop()

# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------
bucketed = bucket_jobs(filtered_jobs, min_score=0)  # already filtered above
counts = bucket_summary_counts(bucketed)

source_counts = {}
for j in filtered_jobs:
    s = j.get("source", "")
    source_counts[s] = source_counts.get(s, 0) + 1

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Jobs", len(filtered_jobs))
c2.metric("New 24h", counts["last_24h"])
c3.metric("Avg Score", f"{sum(j.get('match_score', 0) for j in filtered_jobs) / max(len(filtered_jobs), 1):.0f}")
c4.metric("Visa Count", sum(1 for j in filtered_jobs if j.get("visa_flag")))

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

# ---------------------------------------------------------------------------
# Time-bucketed job tables
# ---------------------------------------------------------------------------
BUCKET_COLORS = ["#f44336", "#ff9800", "#ffc107", "#2196f3"]

# Bucket navigation pills
nav_pills = []
for idx in range(4):
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

for idx in range(4):
    label, emoji_unicode, _, _, _ = BUCKETS[idx]
    bucket_list = bucketed.get(idx, [])
    count = len(bucket_list)

    st.markdown(
        f'<div class="bucket-hdr bucket-hdr-{idx}">'
        f'{emoji_unicode} {label}<span class="bucket-count">{count}</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown(render_job_table(bucket_list), unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Export buttons
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Charts + Run History
# ---------------------------------------------------------------------------
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
                width="stretch", hide_index=True,
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
