"""Boards E: the four applypilot sites with no documented endpoint are reported as unsupported."""
import importlib.util
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

_KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))
_spec = importlib.util.spec_from_file_location("hunt_kit_w4e_check", _KIT / "hunt_kit.py")
hk = importlib.util.module_from_spec(_spec)
sys.modules["hunt_kit_w4e_check"] = hk
_spec.loader.exec_module(hk)  # type: ignore[union-attr]

URLS = [
    "https://dynamitejobs.com/category/remote-development-jobs",
    "https://arc.dev/remote-jobs/developer",
    "https://jobgether.com/remote-jobs/software-engineer",
    "https://topstartups.io/jobs/?job_location=Remote",
]


def test_hosts_are_exactly_the_four():
    assert {urlsplit(u).hostname for u in URLS} == {"dynamitejobs.com", "arc.dev", "jobgether.com", "topstartups.io"}


@pytest.mark.parametrize("url", URLS)
def test_reported_as_unsupported(url):
    assert hk.board_from_job_url(url)["kind"] == "unsupported"


def test_lookalike_host_is_not_unsupported():
    assert hk.board_from_job_url("https://notarc.dev.example.com/x")["kind"] != "unsupported"
