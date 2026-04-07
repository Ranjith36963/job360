"""Tests for dashboard helper functions."""
import sys
from unittest.mock import MagicMock, PropertyMock

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

# Inject mocks before importing dashboard
sys.modules["streamlit"] = _st
sys.modules.setdefault("pandas", MagicMock())
sys.modules.setdefault("plotly", MagicMock())
sys.modules.setdefault("plotly.express", MagicMock())

from src.dashboard import _safe_url


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
