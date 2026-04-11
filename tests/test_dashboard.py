"""Tests for dashboard helper functions."""
import sys
from unittest.mock import MagicMock, PropertyMock

# Force-import real pandas and plotly BEFORE we touch sys.modules. If we don't,
# the `sys.modules.setdefault("pandas", MagicMock())` call below would install
# a MagicMock as "pandas" when this file is collected first, and OTHER test
# files that later do `import pandas as pd` would get the MagicMock instead of
# real pandas — silently breaking downstream tests like test_jobspy_parses_dataframe
# (df.iterrows() on a MagicMock yields nothing). Importing here guarantees the
# real modules are cached in sys.modules before any mocking happens.
import pandas  # noqa: F401
try:
    import plotly  # noqa: F401
    import plotly.express  # noqa: F401
except ImportError:
    pass

# Build a streamlit mock that survives src/dashboard.py module-level execution.
# The dashboard calls st.file_uploader() which returns an object whose .size is
# compared with int, st.number_input() whose return is compared with 0, etc.
# We make all widgets return None/0/"" so conditionals short-circuit.
_st = MagicMock()

# file_uploader returns None (no file uploaded) so `if uploaded_cv` is False
_st.file_uploader.return_value = None
# number_input returns 0
_st.number_input.return_value = 0
# text_input / text_area return empty string
_st.text_input.return_value = ""
_st.text_area.return_value = ""
# multiselect returns empty list
_st.multiselect.return_value = []
# selectbox returns empty string
_st.selectbox.return_value = ""
# button returns False
_st.button.return_value = False
# slider returns 30
_st.slider.return_value = 30
# radio returns "All"
_st.radio.return_value = "All"
# columns returns list of mocks with __enter__/__exit__
_col_mock = MagicMock()
_col_mock.__enter__ = MagicMock(return_value=_col_mock)
_col_mock.__exit__ = MagicMock(return_value=False)


def _fake_columns(n, **kwargs):
    """Return n column mocks."""
    if isinstance(n, int):
        return [MagicMock() for _ in range(n)]
    if isinstance(n, (list, tuple)):
        return [MagicMock() for _ in n]
    return [MagicMock(), MagicMock()]


_st.columns.side_effect = _fake_columns
# sidebar is a context manager too
_st.sidebar = MagicMock()
_st.sidebar.__enter__ = MagicMock(return_value=_st.sidebar)
_st.sidebar.__exit__ = MagicMock(return_value=False)
_st.sidebar.file_uploader.return_value = None
_st.sidebar.number_input.return_value = 0
_st.sidebar.text_input.return_value = ""
_st.sidebar.text_area.return_value = ""
_st.sidebar.multiselect.return_value = []
_st.sidebar.selectbox.return_value = ""
_st.sidebar.button.return_value = False
_st.sidebar.slider.return_value = 30
_st.sidebar.radio.return_value = "All"
_sidebar_col = MagicMock()
_sidebar_col.__enter__ = MagicMock(return_value=_sidebar_col)
_sidebar_col.__exit__ = MagicMock(return_value=False)
_st.sidebar.columns.return_value = [_sidebar_col, _sidebar_col]
_expander = MagicMock()
_expander.__enter__ = MagicMock(return_value=_expander)
_expander.__exit__ = MagicMock(return_value=False)
# Make expander widgets also return safe values
_expander.file_uploader.return_value = None
_expander.number_input.return_value = 0
_expander.text_input.return_value = ""
_expander.text_area.return_value = ""
_expander.multiselect.return_value = []
_expander.selectbox.return_value = ""
_expander.button.return_value = False
_expander.slider.return_value = 30
_expander.radio.return_value = "All"
_expander.columns.return_value = [_sidebar_col, _sidebar_col]
_st.sidebar.expander.return_value = _expander
# cache_data decorator should be a pass-through
_st.cache_data = lambda **kwargs: (lambda fn: fn)

# Inject streamlit mock before importing dashboard.
# NOTE: do NOT mock pandas/plotly here — we force-imported the real ones above
# to keep sys.modules clean for downstream tests. Only streamlit is mocked
# because the dashboard's top-level Streamlit calls would error otherwise.
sys.modules["streamlit"] = _st

from src.dashboard import _safe_url, render_job_table


def test_safe_url_blocks_javascript():
    assert _safe_url("javascript:alert(1)") == "#"
    assert _safe_url("javascript:alert(document.cookie)") == "#"


def test_safe_url_blocks_data_uri():
    assert _safe_url("data:text/html,<script>alert(1)</script>") == "#"


def test_safe_url_allows_https():
    url = "https://example.com/job/123"
    result = _safe_url(url)
    assert "example.com/job/123" in result


def test_safe_url_allows_http():
    url = "http://example.com/job/123"
    result = _safe_url(url)
    assert "example.com/job/123" in result


def test_safe_url_handles_empty():
    assert _safe_url("") == "#"
    assert _safe_url(None) == "#"


def test_safe_url_handles_relative():
    """Relative URLs (no scheme) should be allowed."""
    result = _safe_url("/jobs/123")
    assert "/jobs/123" in result


# ---------------------------------------------------------------------------
# render_job_table skill-tier tooltip (B3 regression fix)
# ---------------------------------------------------------------------------

_SAMPLE_JOB = {
    "match_score": 75,
    "visa_flag": False,
    "apply_url": "https://example.com/job/1",
    "title": "AI Engineer",
    "company": "DeepMind",
    "location": "London, UK",
    "salary_display": "",
    "date_found": "2026-04-10T12:00:00+00:00",
    "source": "greenhouse",
    "description": "We need a Python and PyTorch expert. RAG and LangChain experience required.",
}


def test_render_job_table_empty_returns_placeholder():
    html_out = render_job_table([])
    assert "No jobs in this period" in html_out


def test_render_job_table_tooltip_empty_without_skills():
    """Without a profile's skill lists, the tooltip should silently render nothing.

    This is the B3 regression: when no profile is loaded, PRIMARY_SKILLS etc.
    are empty, so the tooltip should omit the 'N skills' badge entirely —
    rather than rendering a misleading '0 skills' bubble.
    """
    html_out = render_job_table([_SAMPLE_JOB])
    assert "skills</span>" not in html_out
    assert "jrow-skills-tip" not in html_out


def test_render_job_table_tooltip_uses_passed_skills():
    """When profile skills are passed, the tooltip should render and list matches."""
    html_out = render_job_table(
        [_SAMPLE_JOB],
        primary_skills=["python", "pytorch"],
        secondary_skills=["langchain"],
        tertiary_skills=["rag"],
    )
    # Tooltip badge is present
    assert "jrow-skills-tip" in html_out
    # All four matches are rendered (case-insensitive match in the description)
    assert "4 skills" in html_out
    # Tier labels appear in the tooltip
    assert "Primary:" in html_out
    assert "Secondary:" in html_out
    assert "Tertiary:" in html_out


def test_render_job_table_tooltip_skips_unmatched_skills():
    """Passed skills that don't appear in the description should not render."""
    html_out = render_job_table(
        [_SAMPLE_JOB],
        primary_skills=["python", "rust", "golang"],  # only "python" matches
    )
    assert "1 skills" in html_out
    # Rust and Golang should NOT appear in the tooltip text
    assert "Rust" not in html_out
    assert "Golang" not in html_out
