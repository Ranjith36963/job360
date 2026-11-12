"""The literal term matcher the CV review uses for the ad's requirements (cv_coverage.Tokens).

The reviewer judges meaning; this only answers "is this term, word for word, in this text" (strict, ``find``) and
"could a reader see this gap in the CV" (loose, ``claims``). No acronym or synonym inference (owner 2026-11-10).
"""

from __future__ import annotations

import time

from src.services.applications.cv_coverage import Tokens

AD = "We need Python, Kubernetes and large language models. Nice: Terraform. Experience with Java and Node.js, C++ or C#."


def test_find_returns_the_texts_own_spelling_or_nothing():
    ad = Tokens(AD)
    assert (ad.find("python"), ad.find("KUBERNETES"), ad.find("Large Language Models")) == (
        "Python", "Kubernetes", "large language models")
    assert ad.find("Rust") == "" and ad.find("") == "" and ad.find("   ") == ""


def test_java_is_not_found_inside_javascript():
    cv = Tokens("JavaScript developer, Javascripts")
    assert cv.find("Java") == "" and cv.claims("Java") is False


def test_single_letter_and_symbol_terms_match_whole_tokens_only():
    cv = Tokens("Used R. Wrote C++ and C# plus .NET, Node.js.")
    assert [cv.find(t) for t in ("R", "C++", "C#", ".NET", "Node.js")] == ["R", "C++", "C#", ".NET", "Node.js"]
    other = Tokens("Rust, React, C, Net, Node and JS, C++11")
    assert [other.find(t) for t in ("R", "C++", "C#", ".NET", "Node.js")] == ["", "", "", "", ""]


def test_short_terms_compare_original_case():
    assert Tokens("Ready to go live").find("Go") == "" and Tokens("Ready to go live").claims("Go") is False
    assert Tokens("Grade c in maths").find("C") == ""
    assert Tokens("Wrote Go services").find("Go") == "Go"


def test_a_slash_or_hyphen_still_finds_each_word():
    assert Tokens("Python/Java required").find("Java") == "Java"
    assert Tokens("We want end-to-end work").find("end-to-end") == "end-to-end"


def test_a_phrase_must_occur_in_order():
    assert Tokens("learning machine").find("machine learning") == ""
    assert Tokens("learning about Machine Learning").find("machine learning") == "Machine Learning"


def test_a_gap_with_attached_markup_is_still_a_claim():
    for cv in ("Kubernetes-based", "Kubernetes‑based", "Kubernetes—cluster", "Kubernetes's",
               "Kubernetes’s", "*Kubernetes*", "#Kubernetes", "<Kubernetes>", "Kuber​netes",
               "Kube­rnetes", "Ｋubernetes", "Kubernetes:cluster", "(Kubernetes)", "KUBERNETES",
               "Kubernetes…"):
        assert Tokens(f"Ran {cv} at scale").claims("Kubernetes"), cv


def test_loose_spellings_are_claims_but_find_stays_strict():
    for cv in ("Node JS", "NodeJS", "node.js", "Built Node.js,"):
        assert Tokens(f"Built {cv} apps").claims("Node.js"), cv
    assert Tokens("Did machine-learning work").claims("machine learning")
    assert Tokens("Built NodeJS apps").find("Node.js") == ""


def test_huge_and_hostile_inputs_are_bounded_and_fast():
    big = Tokens(("python " * 200000) + "tool1")
    junk = Tokens(",-(" * 100000 + " Python")
    t0 = time.perf_counter()
    for i in range(40):
        big.find(f"tool{i}")
        big.claims(f"tool{i} x")
        junk.claims("Python")
    assert time.perf_counter() - t0 < 1.0
    assert Tokens(5).find("x") == "" and Tokens("x").find(5) == ""  # type: ignore[arg-type]
