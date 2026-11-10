"""JD coverage (pure core): the ad's exact words, profile support, literal presence in the CV."""

from __future__ import annotations

import time

from src.services.applications.cv_coverage import coverage
from src.services.profile.models import CVData, UserPreferences, UserProfile

AD = "We need Python, Kubernetes and large language models. Nice: Terraform. Experience with Java and Node.js, C++ or C#."


def _profile(*skills: str, raw: str = "") -> UserProfile:
    return UserProfile(cv_data=CVData(raw_text=raw, skills=list(skills)), preferences=UserPreferences())


def _req(*texts: str, soft: tuple[str, ...] = ()) -> list[dict]:
    return [{"text": t, "kind": None} for t in texts] + [{"text": t, "kind": "soft"} for t in soft]


def _codes(r: dict) -> list[tuple[str, str]]:
    return [(p["code"], p["term"]) for p in r["problems"]]


def test_a_covered_claim_whose_term_is_absent_from_the_cv_is_weak():
    r = coverage("Built services in Go.", AD, _req("Python", "Kubernetes"), _profile("Python", "Kubernetes"))
    assert r["covered"] == []
    assert r["missing"] == ["Python", "Kubernetes"]
    assert r["ratio"] == 0.0
    assert r["ok"] is False
    assert ("jd_coverage_low", "") in _codes(r)


def test_all_supported_terms_literally_in_the_cv_is_ok():
    r = coverage("Python and Kubernetes work.", AD, _req("Python", "Kubernetes"), _profile("Python", "Kubernetes"))
    assert r["covered"] == ["Python", "Kubernetes"] and r["ratio"] == 1.0 and r["ok"] is True
    assert r["problems"] == []


def test_a_term_not_in_the_ad_is_reported_and_left_out_of_the_ratio():
    r = coverage("Python", AD, _req("Python", "Rust"), _profile("Python", "Rust"))
    assert ("term_not_in_ad", "Rust") in _codes(r)
    assert r["ratio"] == 1.0 and r["ok"] is True
    assert "Rust" not in r["covered"] + r["missing"] + r["gaps"]


def test_a_gap_skill_in_the_cv_is_a_problem_and_blocks():
    r = coverage("Python and Terraform.", AD, _req("Python", "Terraform"), _profile("Python"))
    assert r["gaps"] == ["Terraform"]
    assert ("gap_claimed_in_cv", "Terraform") in _codes(r)
    assert r["ok"] is False


def test_a_gap_absent_from_the_cv_is_listed_in_the_ads_words_and_ok():
    r = coverage("Python.", AD, _req("python", "terraform"), _profile("Python"))
    assert r["gaps"] == ["Terraform"]  # the ad's casing, not the requirement's
    assert r["ok"] is True


def test_java_is_not_found_inside_javascript():
    r = coverage("JavaScript developer", AD, _req("Java"), _profile("Java"))
    assert r["covered"] == [] and r["missing"] == ["Java"]
    g = coverage("JavaScript developer", AD, _req("Java"), _profile("JavaScript"))
    assert "gap_claimed_in_cv" not in [c for c, _ in _codes(g)]


def test_single_letter_and_symbol_terms_match_whole_tokens_only():
    ad = "Skills: R, C++, C#, .NET and Node.js."
    cv = "Used R. Wrote C++ and C# plus .NET, Node.js."
    r = coverage(cv, ad, _req("R", "C++", "C#", ".NET", "Node.js"), _profile("R", "C++", "C#", ".NET", "Node.js"))
    assert r["covered"] == ["R", "C++", "C#", ".NET", "Node.js"] and r["ok"] is True
    # none of them is inside a longer word / name
    cv2 = "Rust, React, C, Net, Node and JS, C++11"
    r2 = coverage(cv2, ad, _req("R", "C++", "C#", ".NET", "Node.js"), _profile("R", "C++", "C#", ".NET", "Node.js"))
    assert r2["covered"] == []


def test_case_variants_match_and_the_ad_casing_is_returned():
    r = coverage("PYTHON, kubernetes", AD, _req("python", "KUBERNETES"), _profile("Python", "Kubernetes"))
    assert r["covered"] == ["Python", "Kubernetes"]


def test_a_slash_in_the_ad_still_finds_each_word():
    ad = "Python/Java required"
    r = coverage("Java and Python", ad, _req("Java", "Python"), _profile("Java", "Python"))
    assert r["covered"] == ["Java", "Python"]


def test_acronym_in_the_profile_supports_the_long_form_in_the_ad():
    prof = _profile("LLMs")
    r = coverage("Built LLM pipelines. large language models", AD, _req("large language models"), prof)
    assert r["gaps"] == [] and r["covered"] == ["large language models"]


def test_long_form_in_the_profile_supports_the_acronym_in_the_ad():
    ad = "Experience with LLM tooling."
    r = coverage("Used LLM daily", ad, _req("LLM"), _profile(raw="Worked with large language models at Acme."))
    assert r["gaps"] == [] and r["covered"] == ["LLM"]


def test_acronym_is_not_supported_when_the_profile_says_neither():
    r = coverage("LLM", "We use LLM here.", _req("LLM"), _profile("Python"))
    assert r["gaps"] == ["LLM"] and ("gap_claimed_in_cv", "LLM") in _codes(r)


def test_acronym_letters_must_be_consecutive_words_initials():
    ad = "We use LLM here."
    r = coverage("x", ad, _req("LLM"), _profile(raw="large old language models"))
    assert r["gaps"] == ["LLM"]


def test_threshold_edge_exactly_point_eight_is_ok_and_below_is_not():
    ad = "A1 B2 C3 D4 E5"
    prof = _profile("A1", "B2", "C3", "D4", "E5")
    reqs = _req("A1", "B2", "C3", "D4", "E5")
    r = coverage("A1 B2 C3 D4", ad, reqs, prof)
    assert r["ratio"] == 0.8 and r["ok"] is True
    r2 = coverage("A1 B2 C3", ad, reqs, prof)
    assert r2["ok"] is False and r2["ratio"] == 0.6
    r3 = coverage("A1 B2 C3", ad, reqs, prof, threshold=0.6)
    assert r3["ok"] is True


def test_only_must_have_supported_terms_form_the_denominator():
    r = coverage("Python", AD, _req("Python", "Kubernetes", soft=("Terraform",)), _profile("Python", "Terraform"))
    # Kubernetes is a gap (not in profile): out of the ratio; Terraform soft: out of the ratio
    assert r["ratio"] == 1.0 and r["ok"] is True
    assert r["gaps"] == ["Kubernetes"]
    assert r["missing"] == ["Terraform"]


def test_zero_must_haves_is_vacuously_ok_but_not_a_no_ad_terms_failure():
    r = coverage("x", AD, _req(soft=("Terraform",)), _profile("Terraform"))
    assert r["ratio"] == 1.0 and r["ok"] is True and r["problems"] == []


def test_no_ad_text_or_no_term_in_the_ad_is_not_ok():
    a = coverage("Python", "", _req("Python"), _profile("Python"))
    assert a["ok"] is False and ("no_ad_terms", "") in _codes(a)
    b = coverage("Rust", AD, _req("Rust"), _profile("Rust"))
    assert b["ok"] is False and ("no_ad_terms", "") in _codes(b)
    c = coverage("Python", AD, [], _profile("Python"))
    assert c["ok"] is False and ("no_ad_terms", "") in _codes(c)


def test_duplicate_requirements_count_once():
    r = coverage("Python", AD, _req("Python", "python", " PYTHON "), _profile("Python"))
    assert r["covered"] == ["Python"]


def test_a_requirement_saved_as_a_plain_string_is_a_must_have():
    r = coverage("x", AD, ["Python"], _profile("Python"))
    assert r["missing"] == ["Python"]


def test_a_requirement_longer_than_four_words_is_left_out_and_reported():
    sentence = "experience with large language models today"
    r = coverage("x", "We want " + sentence, _req(sentence), _profile(raw=sentence))
    assert ("term_too_long", sentence) in _codes(r)


def test_a_phrase_must_occur_in_order_in_the_ad_and_the_cv():
    ad = "Machine learning at scale"
    r = coverage("learning machine", ad, _req("machine learning"), _profile("Machine Learning"))
    assert r["missing"] == ["Machine learning"]
    r2 = coverage("learning about Machine Learning", ad, _req("machine learning"), _profile("Machine Learning"))
    assert r2["covered"] == ["Machine learning"]


def test_huge_inputs_are_bounded_and_fast():
    reqs = [{"text": f"tool{i}", "kind": None} for i in range(5000)]
    ad = " ".join(f"tool{i}" for i in range(5000)) * 20
    cv = ("python " * 200000) + "tool1"
    prof = _profile(raw=("java " * 200000))
    t0 = time.perf_counter()
    r = coverage(cv, ad, reqs, prof)
    assert time.perf_counter() - t0 < 1.0
    assert len(r["covered"]) + len(r["missing"]) + len(r["gaps"]) + len(r["problems"]) <= 200


def test_hostile_punctuation_runs_are_linear():
    junk = ",-(" * 100000
    t0 = time.perf_counter()
    coverage(junk, junk + " Python", _req("Python"), _profile(raw=junk))
    assert time.perf_counter() - t0 < 1.0


def test_no_profile_text_means_every_term_is_a_gap():
    r = coverage("Python", AD, _req("Python"), None)
    assert r["gaps"] == ["Python"] and ("gap_claimed_in_cv", "Python") in _codes(r)


def test_a_two_letter_acronym_is_not_supported_by_two_unrelated_words():
    r = coverage("AI", "We use AI.", _req("AI"), _profile(raw="analysis and improvements, about it"))
    assert r["gaps"] == ["AI"]


def test_an_acronym_does_not_span_a_non_word_token():
    r = coverage("x", "We use LLM here.", _req("LLM"), _profile(raw="large 2024 language models"))
    assert r["gaps"] == ["LLM"]
