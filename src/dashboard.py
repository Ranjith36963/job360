"""Job360 Web Dashboard — Streamlit UI for browsing, filtering, and managing job results."""

import sqlite3
import json
import subprocess
import sys
from datetime import datetime
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
# Database helpers (synchronous – Streamlit-friendly)
# ---------------------------------------------------------------------------
def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_data(ttl=60)
def load_jobs() -> pd.DataFrame:
    conn = _get_conn()
    try:
        df = pd.read_sql_query("SELECT * FROM jobs ORDER BY match_score DESC", conn)
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()

    if df.empty:
        return df

    df["visa_flag"] = df["visa_flag"].astype(bool)
    df["first_seen"] = pd.to_datetime(df["first_seen"], errors="coerce")
    df["date_found"] = pd.to_datetime(df["date_found"], errors="coerce")

    # Formatted salary column
    def _fmt_salary(row):
        smin, smax = row.get("salary_min"), row.get("salary_max")
        if pd.notna(smin) and pd.notna(smax):
            return f"\u00a3{int(smin):,} – \u00a3{int(smax):,}"
        if pd.notna(smin):
            return f"\u00a3{int(smin):,}"
        if pd.notna(smax):
            return f"\u00a3{int(smax):,}"
        return ""

    df["salary"] = df.apply(_fmt_salary, axis=1)
    return df


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
# Load data
# ---------------------------------------------------------------------------
df_jobs = load_jobs()
df_runs = load_run_logs()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("\U0001F50D Filters")

    search_text = st.text_input("Search", placeholder="e.g. AI Engineer London")

    score_range = st.slider("Match Score", 0, 100, (0, 100))

    if not df_jobs.empty:
        available_sources = sorted(df_jobs["source"].unique())
        selected_sources = st.multiselect("Sources", available_sources, default=available_sources)

        available_locations = sorted(df_jobs["location"].dropna().unique())
        selected_locations = st.multiselect("Locations", available_locations)

        visa_filter = st.radio("Visa Sponsorship", ["All", "Visa Only", "No Visa Flag"])
    else:
        selected_sources = []
        selected_locations = []
        visa_filter = "All"

    st.divider()
    st.subheader("\u2699\uFE0F Actions")
    trigger_search = st.button("\U0001F680 Run New Search", use_container_width=True)
    export_csv = st.button("\U0001F4E5 Export CSV", use_container_width=True)

# ---------------------------------------------------------------------------
# Apply filters
# ---------------------------------------------------------------------------
df_filtered = df_jobs.copy()

if not df_filtered.empty:
    # Text search across title, company, location, description
    if search_text:
        q = search_text.lower()
        mask = (
            df_filtered["title"].str.lower().str.contains(q, na=False)
            | df_filtered["company"].str.lower().str.contains(q, na=False)
            | df_filtered["location"].str.lower().str.contains(q, na=False)
            | df_filtered["description"].str.lower().str.contains(q, na=False)
        )
        df_filtered = df_filtered[mask]

    # Score range
    df_filtered = df_filtered[
        (df_filtered["match_score"] >= score_range[0])
        & (df_filtered["match_score"] <= score_range[1])
    ]

    # Source filter
    if selected_sources:
        df_filtered = df_filtered[df_filtered["source"].isin(selected_sources)]

    # Location filter
    if selected_locations:
        df_filtered = df_filtered[df_filtered["location"].isin(selected_locations)]

    # Visa filter
    if visa_filter == "Visa Only":
        df_filtered = df_filtered[df_filtered["visa_flag"]]
    elif visa_filter == "No Visa Flag":
        df_filtered = df_filtered[~df_filtered["visa_flag"]]

# ---------------------------------------------------------------------------
# Trigger new search
# ---------------------------------------------------------------------------
if trigger_search:
    with st.spinner("Running Job360 search... this may take 2-3 minutes."):
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
st.caption("AI/ML Job Search Aggregator — UK & Remote")

# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------
if df_jobs.empty:
    st.info(
        "No jobs in the database yet. Click **Run New Search** in the sidebar to get started!"
    )
    st.stop()

# ---------------------------------------------------------------------------
# KPI metrics
# ---------------------------------------------------------------------------
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total Jobs", len(df_filtered))
c2.metric("Avg Score", f"{df_filtered['match_score'].mean():.0f}" if len(df_filtered) else "—")
c3.metric("Top Score", int(df_filtered["match_score"].max()) if len(df_filtered) else "—")
c4.metric("Visa Sponsors", int(df_filtered["visa_flag"].sum()) if len(df_filtered) else 0)
c5.metric("Sources", df_filtered["source"].nunique() if len(df_filtered) else 0)

st.divider()

# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
chart_left, chart_right = st.columns(2)

with chart_left:
    if len(df_filtered):
        fig_hist = px.histogram(
            df_filtered,
            x="match_score",
            nbins=20,
            color_discrete_sequence=["#1a73e8"],
            title="Score Distribution",
            labels={"match_score": "Match Score", "count": "Jobs"},
        )
        fig_hist.add_vline(
            x=MIN_MATCH_SCORE,
            line_dash="dash",
            line_color="red",
            annotation_text=f"Min ({MIN_MATCH_SCORE})",
        )
        fig_hist.update_layout(bargap=0.1, height=350)
        st.plotly_chart(fig_hist, use_container_width=True)
    else:
        st.info("No data to chart.")

with chart_right:
    if len(df_filtered):
        source_counts = df_filtered["source"].value_counts().reset_index()
        source_counts.columns = ["source", "count"]
        fig_pie = px.pie(
            source_counts,
            values="count",
            names="source",
            title="Jobs by Source",
            hole=0.35,
        )
        fig_pie.update_layout(height=350)
        st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.info("No data to chart.")

st.divider()

# ---------------------------------------------------------------------------
# Job listings table
# ---------------------------------------------------------------------------
st.subheader(f"Job Listings ({len(df_filtered)})")

if len(df_filtered):
    display_cols = [
        "match_score", "title", "company", "location",
        "salary", "source", "visa_flag", "date_found", "apply_url",
    ]
    df_display = df_filtered[display_cols].copy()
    df_display = df_display.rename(columns={
        "match_score": "Score",
        "title": "Title",
        "company": "Company",
        "location": "Location",
        "salary": "Salary",
        "source": "Source",
        "visa_flag": "Visa",
        "date_found": "Date",
        "apply_url": "Apply",
    })

    st.dataframe(
        df_display,
        use_container_width=True,
        height=500,
        column_config={
            "Score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%d"
            ),
            "Visa": st.column_config.CheckboxColumn("Visa"),
            "Apply": st.column_config.LinkColumn("Apply", display_text="Apply"),
            "Date": st.column_config.DatetimeColumn("Date", format="YYYY-MM-DD"),
        },
        hide_index=True,
    )
else:
    st.info("No jobs match the current filters.")

# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------
if export_csv and len(df_filtered):
    csv_data = df_filtered.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download CSV",
        data=csv_data,
        file_name=f"job360_export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )

# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------
with st.expander("Run History", expanded=False):
    if not df_runs.empty:
        col_a, col_b = st.columns(2)

        with col_a:
            st.dataframe(
                df_runs[["timestamp", "total_found", "new_jobs"]].rename(columns={
                    "timestamp": "Time",
                    "total_found": "Total Found",
                    "new_jobs": "New Jobs",
                }),
                use_container_width=True,
                hide_index=True,
            )

        with col_b:
            if len(df_runs) > 1:
                fig_line = px.line(
                    df_runs.sort_values("timestamp"),
                    x="timestamp",
                    y="new_jobs",
                    title="New Jobs per Run",
                    markers=True,
                )
                fig_line.update_layout(height=300)
                st.plotly_chart(fig_line, use_container_width=True)

        # Per-source breakdown of latest run
        if len(df_runs):
            latest = df_runs.iloc[0]
            ps = latest["per_source"]
            if ps:
                st.subheader("Latest Run — Per Source")
                ps_df = pd.DataFrame(
                    list(ps.items()), columns=["Source", "Jobs Found"]
                ).sort_values("Jobs Found", ascending=False)
                fig_bar = px.bar(
                    ps_df, x="Source", y="Jobs Found",
                    color_discrete_sequence=["#34a853"],
                )
                fig_bar.update_layout(height=300)
                st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.info("No runs recorded yet.")

# ---------------------------------------------------------------------------
# Previous exports
# ---------------------------------------------------------------------------
with st.expander("Previous Exports", expanded=False):
    exports_path = Path(EXPORTS_DIR)
    if exports_path.exists():
        csv_files = sorted(exports_path.glob("*.csv"), reverse=True)
        if csv_files:
            for f in csv_files[:10]:
                col_f, col_d = st.columns([3, 1])
                col_f.text(f.name)
                with open(f, "rb") as fh:
                    col_d.download_button(
                        "Download", fh.read(), file_name=f.name, mime="text/csv",
                        key=f"dl_{f.name}",
                    )
        else:
            st.info("No exports yet.")
    else:
        st.info("Exports directory not found.")
