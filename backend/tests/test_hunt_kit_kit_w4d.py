"""The applypilot boards with no recorded endpoint are reported as unsupported, never read."""
import sys
from pathlib import Path

import pytest

_KIT = Path(__file__).resolve().parent.parent / "src" / "hunt_kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))
import hunt_kit as hk  # noqa: E402


@pytest.mark.parametrize("url", [
    "https://www.randstad.ca/jobs/search/123",
    "https://www.careerjet.ca/jobad/ca1",
    "https://justremote.co/remote-jobs/x",
    "https://remote.co/job/x/",
])
def test_unrecorded_boards_are_unsupported(url):
    assert hk.board_from_job_url(url)["kind"] == "unsupported"


def test_remote_com_is_not_caught_by_the_remote_co_rule():
    assert not hk._UNSUPPORTED_HOSTS.search("remote.com")
    assert not hk._UNSUPPORTED_HOSTS.search("notremote.co")
    assert hk._UNSUPPORTED_HOSTS.search("www.remote.co")


def test_dice_stays_an_aggregator():
    assert hk.board_from_job_url("https://www.dice.com/job-detail/abc")["kind"] == "aggregator"
