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
    r = coverage("Used LLM daily", ad, _req("LLM"), _profile(raw="Worked with Large Language Models at Acme."))
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


def test_zero_supported_must_haves_blocks_with_no_must_haves():
    r = coverage("x", AD, _req(soft=("Terraform",)), _profile("Terraform"))
    assert r["ok"] is False and ("no_must_haves", "") in _codes(r)
    assert r["must_total"] == 0 and r["soft_total"] == 1
    g = coverage("x", AD, _req("Python"), _profile("Java"))  # only gaps
    assert g["ok"] is False and ("no_must_haves", "") in _codes(g)


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


# ── checker attacks (2026-11-10) ────────────────────────────────────────────


def _sup(term: str, prof: UserProfile) -> bool:
    r = coverage(f"x {term} x", f"Need {term}.", [term], prof)
    return bool(r["covered"] or r["missing"])


def test_d1_acronym_is_not_made_of_ordinary_words():
    assert not _sup("RAG", _profile(raw="I ran a great team"))
    assert not _sup("AWS", _profile(raw="built a web site"))
    assert not _sup("ETL", _profile(raw="met everyone. Then lunch"))
    assert not _sup("NLP", _profile(raw="Nursing; Leadership; Pharmacy"))
    assert not _sup("AWS", _profile("Agile", "Workshops", "Scrum"))  # across separate skills
    assert not _sup("SRE", _profile(raw="Sales Recruitment. Experience"))  # across a sentence end


def test_d1_capitalised_phrase_supports_an_all_caps_acronym_and_names_it():
    r = coverage("Used AWS", "Need AWS.", ["AWS"], _profile(raw="Led Amazon Web Services migration"))
    assert r["covered"] == ["AWS"] and r["support"] == {"AWS": "Amazon Web Services"}


def test_d1_a_lowercase_acronym_in_the_ad_is_not_expanded():
    r = coverage("x", "Need aws.", ["aws"], _profile(raw="Led Amazon Web Services migration"))
    assert r["gaps"] == ["aws"]


def test_d2_a_phrase_acronym_matches_only_capitals_in_the_profile():
    for phrase in ("Technical Hiring Experience", "Advanced Network Design", "Web Application Security"):
        assert not _sup(phrase, _profile(raw="I was the lead for the team and staff"))
    assert _sup("Technical Hiring Experience", _profile(raw="Ran THE programme"))


def test_d3_short_terms_compare_original_case():
    r = coverage("Ready to go live", "Must know Go", ["Go"], _profile("Java"))
    assert r["gaps"] == ["Go"] and "gap_claimed_in_cv" not in [c for c, _ in _codes(r)]
    assert not _sup("Go", _profile(raw="I like to go hiking"))
    assert not _sup("C", _profile(raw="Grade c in maths"))
    assert _sup("Go", _profile("Go"))
    c = coverage("Wrote Go services", "Must know Go", ["Go"], _profile("Go"))
    assert c["covered"] == ["Go"]


def test_d4_a_gap_with_attached_markup_is_still_a_claim():
    ad = "Must have Kubernetes."
    for cv in (
        "Kubernetes-based",
        "Kubernetes\u2011based",
        "Kubernetes\u2014cluster",
        "Kubernetes's",
        "Kubernetes\u2019s",
        "*Kubernetes*",
        "#Kubernetes",
        "<Kubernetes>",
        "Kuber\u200bnetes",
        "Kube\u00adrnetes",
        "\uff2bubernetes",
        "Kubernetes:cluster",
        "(Kubernetes)",
        "KUBERNETES",
        "Kubernetes\u2026",
    ):
        r = coverage(f"Ran {cv} at scale", ad, ["Kubernetes"], _profile("nothing"))
        assert ("gap_claimed_in_cv", "Kubernetes") in _codes(r), cv
        assert r["ok"] is False


def test_d4_loose_spellings_of_a_gap_are_claims_but_coverage_stays_strict():
    ad = "Must have Node.js and machine learning."
    for cv in ("Node JS", "NodeJS", "node.js", "Built Node.js,"):
        r = coverage(f"Built {cv} apps", ad, ["Node.js"], _profile("nothing"))
        assert ("gap_claimed_in_cv", "Node.js") in _codes(r), cv
    r = coverage("Did machine-learning work", ad, ["machine learning"], _profile("nothing"))
    assert ("gap_claimed_in_cv", "machine learning") in _codes(r)
    s = coverage("Built NodeJS apps", ad, ["Node.js"], _profile("Node.js"))
    assert s["missing"] == ["Node.js"] and s["covered"] == []  # supported, but only a variant: not covered
    assert coverage("Built apps", ad, ["Node.js"], _profile("Node.js"))["missing"] == ["Node.js"]


def test_d4_java_stays_a_whole_word_in_the_loose_pass():
    r = coverage("JavaScript, Javascripts", "Must have Java.", ["Java"], _profile("Python"))
    assert "gap_claimed_in_cv" not in [c for c, _ in _codes(r)]


def test_d5_threshold_is_validated():
    reqs = _req("Python", "Kubernetes", "Terraform")
    prof = _profile("Python", "Kubernetes", "Terraform")
    for bad in (float("nan"), 0, -1, 0.0, None, "0.5", float("inf"), True, 0.2):
        r = coverage("Python", AD, reqs, prof, threshold=bad)
        assert r["ok"] is False, bad  # 1/3 never passes: the default or the 0.5 floor applies
    assert coverage("Python Kubernetes", AD, reqs, prof, threshold=0.5)["ok"] is True
    assert coverage("Python Kubernetes", AD, reqs, prof, threshold=7)["ok"] is False  # clamped to 1.0


def test_d6_odd_requirement_shapes_do_not_raise_or_split_letters():
    prof = _profile("Python")
    for odd in (5, "Python", None, {"text": "Python"}, (x for x in ["Python"])):
        r = coverage("Python", AD, odd, prof)  # type: ignore[arg-type]
        assert r["problems"] == [{"code": "no_ad_terms", "term": ""}], odd
    r = coverage(
        "Python", AD, [5, None, {"text": None}, {"text": ["Python"]}, {"kind": "soft"}, ["Python"], "Python"], prof
    )
    assert r["covered"] == ["Python"] and r["problems"] == []
    assert coverage(5, 123, [{"text": "Python"}], prof)["ok"] is False  # type: ignore[arg-type]


def test_the_ads_own_spelling_is_returned_for_hyphenated_and_slashed_terms():
    r = coverage("x", "We want end-to-end, Python/Java work", ["end-to-end", "java"], _profile("end-to-end", "Java"))
    assert r["missing"] == ["end-to-end", "Java"]
