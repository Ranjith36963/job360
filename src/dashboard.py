"""Job360 Web Dashboard — Streamlit UI with time-bucketed card layout."""

import os
import sqlite3
import json
import subprocess
import sys
import tempfile
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
    .job-card {
        border: 1px solid #e0e0e0;
        border-radius: 10px;
        padding: 16px;
        margin-bottom: 12px;
        background: #fafafa;
    }
    .score-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 16px;
        color: white;
        font-weight: bold;
        font-size: 14px;
    }
    .visa-badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 10px;
        font-size: 12px;
        font-weight: 600;
    }
    .visa-yes { background: #4CAF50; color: white; }
    .visa-no { background: #e0e0e0; color: #666; }
    .source-badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 10px;
        background: #e3f2fd;
        color: #1565c0;
        font-size: 12px;
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
# Job card renderer
# ---------------------------------------------------------------------------
def render_job_card(job: dict):
    """Render a single job as a styled card."""
    score = job.get("match_score", 0)
    color = score_color_hex(score)
    visa = job.get("visa_flag", False)
    url = job.get("apply_url", "#")
    title = job.get("title", "Unknown")

    st.markdown(f"### [{title}]({url})")

    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        st.markdown(f"**{job.get('company', 'Unknown')}**")
        st.caption(f"\U0001F4CD {job.get('location', 'N/A')} &nbsp; | &nbsp; \U0001F4B0 {job.get('salary_display', 'N/A')}")
    with col2:
        posted = format_relative_time(job.get("date_found", ""))
        st.caption(f"\U0001F4C5 {posted}")
        st.markdown(f'<span class="source-badge">{job.get("source", "")}</span>', unsafe_allow_html=True)
    with col3:
        st.markdown(
            f'<span class="score-badge" style="background:{color}">{score}</span>',
            unsafe_allow_html=True,
        )
        visa_class = "visa-yes" if visa else "visa-no"
        visa_text = "Visa \u2713" if visa else "No visa info"
        st.markdown(f'<span class="visa-badge {visa_class}">{visa_text}</span>', unsafe_allow_html=True)

    # Apply button
    st.link_button("Apply Now \u2192", url)

    # Skills expander
    skills = extract_matched_skills(job.get("description", ""))
    has_skills = any(skills.values())
    if has_skills:
        with st.expander("Skills Matched"):
            for tier, tier_skills in skills.items():
                if tier_skills:
                    st.markdown(f"**{tier.title()}:** " + " ".join(f"`{s}`" for s in tier_skills))

    st.divider()


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
all_jobs = load_jobs_7day()
df_runs = load_run_logs()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("\U0001F50D Filters")

    search_text = st.text_input("Search", placeholder="e.g. Software Engineer London")

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
    st.subheader("Your Profile")

    from src.cv_parser import (
        extract_text,
        extract_profile,
        save_profile,
        load_profile,
    )
    from src.preferences import (
        load_preferences,
        save_preferences,
        get_empty_preferences,
    )
    from src.filters.skill_matcher import reload_profile
    from src.config.settings import CV_PROFILE_PATH, USER_PREFERENCES_PATH

    # --- Tab layout for the three input layers ---
    cv_tab, prefs_tab, linkedin_tab = st.tabs(["CV", "Preferences", "LinkedIn"])

    # ---- TAB 1: CV Upload ----
    with cv_tab:
        uploaded_cv = st.file_uploader(
            "Upload CV (PDF/DOCX)",
            type=["pdf", "docx"],
            key="cv_upload",
            help="Upload your CV to personalise job matching. "
            "Skills are extracted automatically.",
        )

        if uploaded_cv is not None:
            suffix = Path(uploaded_cv.name).suffix
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            try:
                os.write(tmp_fd, uploaded_cv.getvalue())
                os.close(tmp_fd)
                text = extract_text(tmp_path)
                profile = extract_profile(text)
                profile["source_file"] = uploaded_cv.name
                save_profile(profile)
                reload_profile()
                total_skills = (
                    len(profile["primary_skills"])
                    + len(profile["secondary_skills"])
                    + len(profile["tertiary_skills"])
                )
                st.success(
                    f"Extracted {total_skills} skills from **{uploaded_cv.name}**"
                )
                st.cache_data.clear()
                st.rerun()
            except (ValueError, FileNotFoundError) as exc:
                st.error(f"Failed to process CV: {exc}")
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        _cv_profile = load_profile()
        if _cv_profile:
            st.success(f"Active: {_cv_profile.get('source_file', 'unknown')}")
            st.caption(f"Extracted: {_cv_profile.get('extracted_at', 'N/A')}")
            with st.expander("CV Skills Profile"):
                for label, key in [
                    ("Job Titles", "job_titles"),
                    ("Primary Skills", "primary_skills"),
                    ("Secondary Skills", "secondary_skills"),
                    ("Tertiary Skills", "tertiary_skills"),
                    ("Locations", "locations"),
                ]:
                    items = _cv_profile.get(key, [])
                    st.write(f"**{label} ({len(items)}):** {', '.join(items) if items else 'None'}")
            if st.button("Reset CV Profile", use_container_width=True):
                CV_PROFILE_PATH.unlink(missing_ok=True)
                reload_profile()
                st.cache_data.clear()
                st.rerun()
        else:
            st.info("No CV uploaded yet.")

    # ---- TAB 2: Preferences ----
    with prefs_tab:
        st.caption("Add info beyond your CV — titles you'd accept, skills you know, etc.")
        _prefs = load_preferences() or get_empty_preferences()

        pref_titles = st.text_area(
            "Job Titles (one per line)",
            value="\n".join(_prefs.get("job_titles", [])),
            height=80,
            key="pref_titles",
            help="Roles you'd consider, e.g. 'AI Platform Engineer', 'Cloud ML Engineer'",
        )
        pref_skills = st.text_area(
            "Skills (one per line)",
            value="\n".join(_prefs.get("skills", [])),
            height=80,
            key="pref_skills",
            help="Skills you have but may not be on your CV, e.g. 'Azure', 'GCP'",
        )
        pref_locations = st.text_area(
            "Locations (one per line)",
            value="\n".join(_prefs.get("locations", [])),
            height=60,
            key="pref_locations",
            help="Where you'd like to work, e.g. 'Remote', 'London', 'Berlin'",
        )
        pref_about = st.text_area(
            "About Me",
            value=_prefs.get("about_me", ""),
            height=80,
            key="pref_about",
            help="Brief career objective or personal summary",
        )
        pref_projects = st.text_area(
            "Projects (one per line)",
            value="\n".join(_prefs.get("projects", [])),
            height=80,
            key="pref_projects",
            help="Notable projects you've worked on",
        )
        pref_certs = st.text_area(
            "Certifications / Licenses (one per line)",
            value="\n".join(_prefs.get("certifications", [])),
            height=60,
            key="pref_certs",
            help="e.g. 'AWS Solutions Architect', 'PMP', 'CKA'",
        )

        if st.button("Save Preferences", use_container_width=True, type="primary"):
            new_prefs = {
                "job_titles": [t.strip() for t in pref_titles.strip().split("\n") if t.strip()],
                "skills": [s.strip() for s in pref_skills.strip().split("\n") if s.strip()],
                "locations": [l.strip() for l in pref_locations.strip().split("\n") if l.strip()],
                "about_me": pref_about.strip(),
                "projects": [p.strip() for p in pref_projects.strip().split("\n") if p.strip()],
                "certifications": [c.strip() for c in pref_certs.strip().split("\n") if c.strip()],
            }
            # Preserve LinkedIn data if it was imported
            if _prefs.get("linkedin"):
                new_prefs["linkedin"] = _prefs["linkedin"]
            save_preferences(new_prefs)
            reload_profile()
            st.cache_data.clear()
            st.success("Preferences saved!")
            st.rerun()

        if load_preferences():
            if st.button("Clear Preferences", use_container_width=True):
                USER_PREFERENCES_PATH.unlink(missing_ok=True)
                reload_profile()
                st.cache_data.clear()
                st.rerun()

    # ---- TAB 3: LinkedIn Import ----
    with linkedin_tab:
        st.caption(
            "Import your LinkedIn data export (ZIP) for comprehensive profile data. "
            "Download from LinkedIn: Settings > Data Privacy > Get a copy of your data."
        )
        uploaded_linkedin = st.file_uploader(
            "Upload LinkedIn Export (ZIP)",
            type=["zip"],
            key="linkedin_upload",
        )

        if uploaded_linkedin is not None:
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip")
            try:
                os.write(tmp_fd, uploaded_linkedin.getvalue())
                os.close(tmp_fd)
                from src.linkedin_import import parse_linkedin_zip
                li_data = parse_linkedin_zip(tmp_path)
                # Store LinkedIn data inside preferences
                prefs_current = load_preferences() or get_empty_preferences()
                prefs_current["linkedin"] = li_data
                save_preferences(prefs_current)
                reload_profile()
                st.success(
                    f"LinkedIn imported: {len(li_data.get('job_titles', []))} titles, "
                    f"{len(li_data.get('skills', []))} skills, "
                    f"{len(li_data.get('certifications', []))} certifications"
                )
                st.cache_data.clear()
                st.rerun()
            except (ValueError, FileNotFoundError) as exc:
                st.error(f"Failed to process LinkedIn export: {exc}")
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        # Show LinkedIn data if imported
        _prefs_li = load_preferences()
        if _prefs_li and _prefs_li.get("linkedin"):
            li = _prefs_li["linkedin"]
            with st.expander("LinkedIn Data"):
                for label, key in [
                    ("Job Titles", "job_titles"),
                    ("Skills", "skills"),
                    ("Locations", "locations"),
                    ("Certifications", "certifications"),
                    ("Companies", "companies"),
                    ("Education", "education"),
                    ("Projects", "projects"),
                ]:
                    items = li.get(key, [])
                    if items:
                        st.write(f"**{label} ({len(items)}):** {', '.join(items[:20])}")
            if st.button("Remove LinkedIn Data", use_container_width=True):
                _prefs_li.pop("linkedin", None)
                save_preferences(_prefs_li)
                reload_profile()
                st.cache_data.clear()
                st.rerun()
        else:
            st.info("No LinkedIn data imported yet.")

    # ---- Merged Profile Summary ----
    st.divider()
    from src.filters.skill_matcher import _load_active_profile
    _merged = _load_active_profile()
    _has_cv = load_profile() is not None
    _has_prefs = load_preferences() is not None
    _sources = []
    if _has_cv:
        _sources.append("CV")
    if _has_prefs:
        _sources.append("Preferences")
        if (load_preferences() or {}).get("linkedin"):
            _sources.append("LinkedIn")
    if _sources:
        st.caption(f"Active sources: {', '.join(_sources)}")
        with st.expander("Merged Profile"):
            st.write(f"**Job Titles ({len(_merged.get('job_titles', []))}):** "
                     f"{', '.join(_merged.get('job_titles', [])[:15]) or 'None'}")
            st.write(f"**Primary Skills ({len(_merged.get('primary_skills', []))}):** "
                     f"{', '.join(_merged.get('primary_skills', [])[:15]) or 'None'}")
            st.write(f"**Secondary Skills ({len(_merged.get('secondary_skills', []))}):** "
                     f"{', '.join(_merged.get('secondary_skills', [])[:15]) or 'None'}")
            st.write(f"**Tertiary Skills ({len(_merged.get('tertiary_skills', []))}):** "
                     f"{', '.join(_merged.get('tertiary_skills', [])[:15]) or 'None'}")
            st.write(f"**Locations ({len(_merged.get('locations', []))}):** "
                     f"{', '.join(_merged.get('locations', [])) or 'None'}")
    else:
        st.info("No profile data. Upload a CV or set preferences to personalise your search.")

    if _has_cv or _has_prefs:
        if st.button("Reset Everything", use_container_width=True):
            CV_PROFILE_PATH.unlink(missing_ok=True)
            USER_PREFERENCES_PATH.unlink(missing_ok=True)
            reload_profile()
            st.cache_data.clear()
            st.rerun()

    st.divider()
    st.subheader("\u2699\uFE0F Actions")
    trigger_search = st.button("\U0001F680 Refresh Now", use_container_width=True)
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
st.caption("Personalised Job Search Aggregator \u2014 Powered by Your CV, Preferences & LinkedIn")

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

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Total 7d Jobs", len(filtered_jobs))
c2.metric("New 24h", counts["last_24h"])
c3.metric("Avg Score", f"{sum(j.get('match_score', 0) for j in filtered_jobs) / max(len(filtered_jobs), 1):.0f}")

# Top 3 sources
source_counts = {}
for j in filtered_jobs:
    s = j.get("source", "")
    source_counts[s] = source_counts.get(s, 0) + 1
top_sources = sorted(source_counts.items(), key=lambda x: x[1], reverse=True)[:3]
c4.metric("Top Sources", ", ".join(s for s, _ in top_sources) if top_sources else "N/A")

c5.metric("Visa Count", sum(1 for j in filtered_jobs if j.get("visa_flag")))
c6.metric("Sources Active", len(set(j.get("source", "") for j in filtered_jobs)))

st.divider()

# ---------------------------------------------------------------------------
# Time-bucketed job cards
# ---------------------------------------------------------------------------
BUCKET_EMOJIS = ["\U0001f534", "\U0001f7e0", "\U0001f7e1", "\U0001f535"]

for idx in range(4):
    label, emoji_unicode, _, _, _ = BUCKETS[idx]
    bucket_list = bucketed.get(idx, [])
    count = len(bucket_list)

    st.subheader(f"{emoji_unicode} {label} ({count} jobs)")

    if not bucket_list:
        st.caption("No new jobs in this period")
    else:
        for job in bucket_list:
            render_job_card(job)

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
with st.expander("Charts", expanded=False):
    chart_left, chart_right = st.columns(2)
    with chart_left:
        if filtered_jobs:
            df_chart = pd.DataFrame(filtered_jobs)
            fig_hist = px.histogram(
                df_chart, x="match_score", nbins=20,
                color_discrete_sequence=["#1a73e8"],
                title="Score Distribution",
                labels={"match_score": "Match Score", "count": "Jobs"},
            )
            fig_hist.add_vline(x=MIN_MATCH_SCORE, line_dash="dash", line_color="red",
                               annotation_text=f"Min ({MIN_MATCH_SCORE})")
            fig_hist.update_layout(bargap=0.1, height=350)
            st.plotly_chart(fig_hist, use_container_width=True)
    with chart_right:
        if filtered_jobs:
            sc = pd.DataFrame(list(source_counts.items()), columns=["source", "count"])
            fig_pie = px.pie(sc, values="count", names="source", title="Jobs by Source", hole=0.35)
            fig_pie.update_layout(height=350)
            st.plotly_chart(fig_pie, use_container_width=True)

with st.expander("Run History", expanded=False):
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
            if len(df_runs) > 1:
                fig_line = px.line(
                    df_runs.sort_values("timestamp"), x="timestamp", y="new_jobs",
                    title="New Jobs per Run", markers=True,
                )
                fig_line.update_layout(height=300)
                st.plotly_chart(fig_line, use_container_width=True)
        if len(df_runs):
            latest = df_runs.iloc[0]
            ps = latest["per_source"]
            if ps:
                st.subheader("Latest Run \u2014 Per Source")
                ps_df = pd.DataFrame(list(ps.items()), columns=["Source", "Jobs Found"]).sort_values("Jobs Found", ascending=False)
                fig_bar = px.bar(ps_df, x="Source", y="Jobs Found", color_discrete_sequence=["#34a853"])
                fig_bar.update_layout(height=300)
                st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.info("No runs recorded yet.")
