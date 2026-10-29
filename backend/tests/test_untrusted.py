"""S10 - guard markers are never stored, and an old row that holds them reads clean.

``strip_markers`` peels complete fences, then cuts every lone marker of either family (case,
width and invisible characters do not matter), leaving nothing in its place. Its work is bounded
per ``<<``, so caller text cannot make it slow.
"""
from __future__ import annotations

import time

ZWSP = chr(0x200B)


def test_strip_markers_peels_complete_and_nested_fences():
    from src.services.untrusted import fence_stored, strip_markers

    one = fence_stored("first ad line")
    assert strip_markers(f"{one}\nFollowed up") == "first ad line\nFollowed up"
    nested = f"<<untrusted-text 11111111>>\n{one}\n<<end untrusted-text 11111111>>"
    assert strip_markers(nested) == "first ad line"
    assert strip_markers("plain text, no markers") == "plain text, no markers"
    assert strip_markers("a << b >> c, x<<y") == "a << b >> c, x<<y", "arrows that are not markers stay"


def test_strip_markers_cuts_lone_markers_of_both_families():
    from src.services.untrusted import strip_markers

    assert strip_markers("<<untrusted-text 1a2b3c4d>>\nOnly the start was copied") == "\nOnly the start was copied"
    assert strip_markers("Recruiter said hi<<end untrusted-text deadbeef>>") == "Recruiter said hi"
    mismatched = "<<untrusted-text aaaaaaaa>>\nkeep me\n<<end untrusted-text bbbbbbbb>>"
    assert strip_markers(mismatched) == "\nkeep me\n"
    assert strip_markers("a <<page-text 0f0f0f0f>>b<<end page-text 0f0f0f0f>> c") == "a b c"
    assert strip_markers("x <</untrusted-text>> y <<end_untrusted_text>> z") == "x  y  z"
    assert strip_markers("<<<untrusted-text 12345678>>") == "<", "only the marker itself is cut"


def test_strip_markers_sees_through_width_and_invisible_splits():
    from src.services.untrusted import strip_markers

    full_width = "café ＜＜ｅｎｄ untrusted-text 1234abcd＞＞ tail"
    assert strip_markers(full_width) == "café  tail"
    split = f"note <<end un{ZWSP}trusted-text 1234abcd>> done"
    assert strip_markers(split) == "note  done"
    assert strip_markers(f"ﬁne{ZWSP} text") == f"ﬁne{ZWSP} text", "no marker: every character is kept"


def test_strip_markers_deep_cleans_nested_values_and_keeps_the_rest():
    from src.services.untrusted import strip_markers_deep

    value = {"detail": "hi<<end untrusted-text deadbeef>>", "n": 3, "list": ["<<page-text 00000000>>ok", None]}
    assert strip_markers_deep(value) == {"detail": "hi", "n": 3, "list": ["ok", None]}
    assert value["detail"] == "hi<<end untrusted-text deadbeef>>", "the input is never changed in place"


def test_strip_markers_runs_in_linear_time_on_hostile_input():
    from src.services.untrusted import strip_markers

    for hostile in ("<<" * 100_000, "<<untrusted-text " * 12_000, "<<end page-text a" * 12_000 + ">"):
        started = time.perf_counter()
        out = strip_markers(hostile)
        assert time.perf_counter() - started < 2.0, hostile[:20]
        assert out == hostile, "nothing in these is a whole marker"
    many = "<<untrusted-text 12345678>>x" * 20_000
    started = time.perf_counter()
    assert strip_markers(many) == "x" * 20_000
    assert time.perf_counter() - started < 2.0
