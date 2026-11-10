"""ATS format checks on a CV's text (cv_format.format_findings): pure, closed codes, numbers and words only.

Fixtures are synthetic (Ada Lovelace); no real contact data. Every test names one rule and asserts the exact
finding (rule #21), not just "something was found".
"""
from __future__ import annotations

import time

import pytest

from src.services.applications.cv_format import (
    CODES,
    FORMAT_MAX_CHARS,
    FORMAT_MIN_CHARS,
    format_findings,
)

BULLET = "- Built ETL pipelines in Python and Snowflake, cutting warehouse costs by 30% across three regions."
GOOD = "\n".join([
    "Ada Lovelace",
    "Data Engineer | Analytics Platforms",
    "London, UK | +44 7700 900123 | ada@example.com",
    "linkedin.com/in/ada | github.com/ada",
    "",
    "SUMMARY",
    "Data engineer who builds reliable pipelines and owns them in production for analytics teams.",
    "",
    "EXPERIENCE",
    "Northwind Traders | Data Engineer | London | Mar 2019 - Present",
    BULLET,
    "- Moved the nightly load from four hours to forty minutes with incremental models and tests.",
    "Contoso Ltd | Analyst | Leeds | Jan 2016 - Feb 2019",
    "- Wrote the weekly revenue report used by the finance team and cut manual work by half.",
    "- Automated the data checks that used to run by hand every Monday morning before the meeting.",
    "",
    "PROJECTS",
    "Pipeline Monitor | Python, Airflow",
    "- Alerting service that reports late and empty tables to the team chat within five minutes.",
    "",
    "SKILLS",
    "Languages: Python, SQL, Bash",
    "Tools: Snowflake, Airflow, dbt, Docker",
    "",
    "EDUCATION & CERTIFICATION",
    "BSc Mathematics, University of Leeds 2015",
    "Google Cloud Professional Data Engineer, 2022",
]) + "\n"


def codes(text: str) -> list[str]:
    return [f["code"] for f in format_findings(text, None)]


def test_a_cv_without_an_experience_heading_is_not_ats_ready():
    text = GOOD.replace("EXPERIENCE\n", "")
    found = [f for f in format_findings(text, None) if f["code"] == "missing_section"]
    assert len(found) == 1
    assert found[0]["detail"] == "experience"


def test_a_complete_cv_has_no_findings():
    assert format_findings(GOOD, None) == []


@pytest.mark.parametrize("old,new", [
    ("EXPERIENCE", "Work Experience"),
    ("EXPERIENCE", "WORK HISTORY"),
    ("EXPERIENCE", "Employment"),
    ("SKILLS", "Technical Skills"),
    ("SKILLS", "Core Competencies"),
    ("EDUCATION & CERTIFICATION", "Education"),
    ("EDUCATION & CERTIFICATION", "Education and Certifications"),
    ("SUMMARY", "Professional Profile"),
])
def test_ats_standard_heading_variants_are_accepted(old, new):
    assert codes(GOOD.replace(old + "\n", new + "\n", 1)) == []


def test_a_markdown_heading_counts_as_a_section_but_leaves_markup():
    found = format_findings(GOOD.replace("EXPERIENCE\n", "## Experience\n"), None)
    assert [f["code"] for f in found] == ["markup_left"]
    assert found[0]["lines"] == [GOOD.splitlines().index("EXPERIENCE") + 1]


@pytest.mark.parametrize("heading,kind", [
    ("Skills Summary", "SKILLS"), ("Technical Expertise", "SKILLS"), ("Tools & Technologies", "SKILLS"),
    ("Skills & Expertise", "SKILLS"), ("Professional Skills", "SKILLS"), ("Areas of Expertise", "SKILLS"),
    ("Technical Proficiencies", "SKILLS"), ("Core Skills & Tools", "SKILLS"), ("SKILLS:", "SKILLS"),
    ("Key Skills:", "SKILLS"), ("Industry Experience", "EXPERIENCE"), ("Research Experience", "EXPERIENCE"),
    ("Work Experience & Internships", "EXPERIENCE"), ("Employment Experience", "EXPERIENCE"),
    ("Relevant Work Experience", "EXPERIENCE"), ("Internships", "EXPERIENCE"), ("WORK EXPERIENCE:", "EXPERIENCE"),
    ("Career Experience", "EXPERIENCE"), ("Education Background", "EDUCATION & CERTIFICATION"),
    ("Educational Background", "EDUCATION & CERTIFICATION"), ("Academic History", "EDUCATION & CERTIFICATION"),
    ("Qualifications & Education", "EDUCATION & CERTIFICATION"), ("E X P E R I E N C E", "EXPERIENCE"),
])
def test_any_section_keyword_in_a_short_heading_names_the_section(heading, kind):
    assert codes(GOOD.replace(kind + "\n", heading + "\n", 1)) == []


def test_one_heading_with_two_keywords_counts_for_each():
    text = GOOD.replace("EXPERIENCE\n", "Experience & Projects\n").replace("PROJECTS\n", "")
    assert codes(text) == []
    text = GOOD.replace("EDUCATION & CERTIFICATION\n", "Certifications & Education\n")
    assert codes(text) == []


def test_a_cv_with_no_skills_heading_stays_flagged():
    text = GOOD.replace("SKILLS\n", "")
    assert [f["detail"] for f in format_findings(text, None) if f["code"] == "missing_section"] == ["skills"]


def test_missing_skills_and_education_are_both_named_in_order():
    text = GOOD.replace("SKILLS\n", "").replace("EDUCATION & CERTIFICATION\n", "")
    found = [f for f in format_findings(text, None) if f["code"] == "missing_section"]
    assert len(found) == 1
    assert found[0]["detail"] == "skills,education"


def test_a_creative_heading_does_not_count_as_a_section():
    text = GOOD.replace("EXPERIENCE\n", "Where I have been\n")
    assert [f["detail"] for f in format_findings(text, None) if f["code"] == "missing_section"] == ["experience"]


def test_a_bullet_that_says_experience_is_not_a_heading():
    text = GOOD.replace("EXPERIENCE\n", "- Experience with Snowflake and dbt at scale in production\n")
    assert "missing_section" in codes(text)


def test_a_cv_with_no_headline_and_no_summary_is_flagged():
    text = GOOD.replace("Data Engineer | Analytics Platforms\n", "").replace(
        "SUMMARY\nData engineer who builds reliable pipelines and owns them in production for analytics teams.\n", "")
    assert [f["code"] for f in format_findings(text, None)] == ["no_headline"]


def test_a_summary_section_stands_in_for_a_headline():
    text = GOOD.replace("Data Engineer | Analytics Platforms\n", "")
    assert codes(text) == []


def test_a_headline_stands_in_for_a_summary_section():
    text = GOOD.replace(
        "SUMMARY\nData engineer who builds reliable pipelines and owns them in production for analytics teams.\n", "")
    assert codes(text) == []


def test_a_missing_phone_is_named():
    text = GOOD.replace("+44 7700 900123 | ", "")
    found = [f for f in format_findings(text, None) if f["code"] == "contact_incomplete"]
    assert [f["detail"] for f in found] == ["phone"]


def test_a_missing_email_is_named():
    text = GOOD.replace(" | ada@example.com", "")
    found = [f for f in format_findings(text, None) if f["code"] == "contact_incomplete"]
    assert [f["detail"] for f in found] == ["email"]


def test_neither_email_nor_phone_names_both():
    text = GOOD.replace("London, UK | +44 7700 900123 | ada@example.com\n", "London, UK\n")
    found = [f for f in format_findings(text, None) if f["code"] == "contact_incomplete"]
    assert [f["detail"] for f in found] == ["email,phone"]


@pytest.mark.parametrize("contact", [
    "Mobile:07700 900123 | Email:ada@example.com",
    "Phone: 07700 900123 | Email: ada@example.com",
    "+44 (0) 7700 900123 · ada@example.com",
    "+447700900123 | ada@example.com",
])
def test_labelled_and_compact_contacts_are_read(contact):
    assert codes(GOOD.replace("London, UK | +44 7700 900123 | ada@example.com", contact)) == []


def test_a_contact_block_under_a_contact_heading_counts():
    text = GOOD.replace("London, UK | +44 7700 900123 | ada@example.com\n", "")
    text = text.replace("SUMMARY\n", "CONTACT\nada@example.com\n+44 7700 900123\nSUMMARY\n", 1)
    assert codes(text) == []


def test_a_contact_in_the_body_does_not_count_as_the_header():
    text = GOOD.replace("London, UK | +44 7700 900123 | ada@example.com\n", "London, UK\n")
    text += "Reach me: ada@example.com +44 7700 900123\n"
    assert "contact_incomplete" in codes(text)


def test_mixed_month_styles_are_flagged_with_their_lines():
    text = GOOD.replace("Jan 2016 - Feb 2019", "01/2016 - Feb 2019")
    found = [f for f in format_findings(text, None) if f["code"] == "mixed_date_format"]
    assert len(found) == 1
    line_no = text.splitlines().index("Contoso Ltd | Analyst | Leeds | 01/2016 - Feb 2019") + 1
    assert found[0]["lines"] == [line_no]


def test_full_and_short_month_names_are_mixed():
    text = GOOD.replace("Mar 2019 - Present", "October 2019 - Present")
    assert "mixed_date_format" in codes(text)


@pytest.mark.parametrize("month", ["March", "April", "June", "July"])
def test_months_spelled_the_same_short_and_full_carry_no_style(month):
    assert codes(GOOD.replace("Mar 2019 - Present", f"{month} 2019 - Present")) == []


def test_the_majority_style_wins_not_the_first_seen():
    text = GOOD.replace("Mar 2019 - Present", "03/2019 - Present")
    got = [f for f in format_findings(text, None) if f["code"] == "mixed_date_format"]
    assert got[0]["lines"] == [text.splitlines().index("Northwind Traders | Data Engineer | London | 03/2019 - Present") + 1]


def test_a_year_range_like_2010_12_is_not_a_month_date():
    assert codes(GOOD.replace("University of Leeds 2015", "University of Leeds 2010-12")) == []


def test_a_long_prose_line_with_a_date_is_not_an_entry_line():
    text = GOOD.replace(BULLET, "Since January 2020 I have run the platform for the analytics teams and kept it up daily, with no gaps.")
    assert codes(text) == []


def test_one_consistent_numeric_style_and_bare_years_are_fine():
    text = GOOD.replace("Mar 2019 - Present", "03/2019 - Present").replace("Jan 2016 - Feb 2019", "01/2016 - 02/2019")
    assert codes(text) == []


def test_a_date_inside_a_bullet_is_prose_not_an_entry_date():
    text = GOOD.replace(BULLET, "- Live since January 2020 and still running in production for the analytics teams")
    assert codes(text) == []


def test_a_may_date_does_not_make_the_style_mixed():
    text = GOOD.replace("Mar 2019 - Present", "May 2019 - Present")
    assert codes(text) == []


@pytest.mark.parametrize("bad", [
    "- Built **fast** pipelines with Python and SQL for the analytics team every day",
    "# Experience heading left in markdown form after the export step ran",
    "| Company | Role | Dates |",
    "|---|---|---|",
    "See [my site](https://ada.example) for the full portfolio of data work",
    "Led the <b>migration</b> of the warehouse to the new platform for the team",
    "- Used `dbt` models to build the revenue marts for the finance team daily",
])
def test_leftover_markup_and_tables_are_flagged_with_the_line(bad):
    text = GOOD.replace(BULLET, bad)
    found = [f for f in format_findings(text, None) if f["code"] == "markup_left"]
    assert len(found) == 1
    assert found[0]["lines"] == [text.splitlines().index(bad) + 1]


def test_three_consecutive_tab_column_lines_are_a_table():
    rows = "Role\t\tCompany\t\tDates\nAnalyst\t\tContoso\t\t2016\nEngineer\t\tNorthwind\t\t2019"
    text = GOOD.replace(BULLET, rows)
    start = text.splitlines().index("Role\t\tCompany\t\tDates") + 1
    found = [f for f in format_findings(text, None) if f["code"] == "markup_left"]
    assert found[0]["lines"] == [start, start + 1, start + 2]


def test_one_tabbed_line_is_not_a_table():
    assert "markup_left" not in codes(GOOD.replace(BULLET, "Role\t\tCompany\t\tDates"))


@pytest.mark.parametrize("fine", [
    "Latency <5ms, p < 0.05, a<b",
    "Java (Spring, List<String> generics), C++ templates <typename T>",
    "Jane Doe <jane.doe@example.com> and the team",
    "Python (asyncio, __init__ hooks, dataclasses)",
    "#1 ranked team in the hackathon; C#, F#",
])
def test_code_like_and_email_text_is_not_markup(fine):
    assert "markup_left" not in codes(GOOD.replace(BULLET, "- " + fine))


def test_a_wrapped_double_underscore_word_is_markdown():
    assert "markup_left" in codes(GOOD.replace(BULLET, "__Python__, SQL and dbt for the analytics team"))


def test_pipes_inside_entry_and_contact_lines_are_not_a_table():
    assert "markup_left" not in codes(GOOD)


def test_a_very_short_cv_is_too_short():
    text = "Ada Lovelace\nada@example.com +44 7700 900123\nEXPERIENCE\nSkills\nEducation\n"
    assert len(text) < FORMAT_MIN_CHARS
    found = [f for f in format_findings(text, None) if f["code"] == "too_short"]
    assert len(found) == 1 and found[0]["detail"] == f"{len(text)}<{FORMAT_MIN_CHARS}"


def test_a_very_long_cv_is_too_long():
    text = GOOD + (BULLET + "\n") * (FORMAT_MAX_CHARS // len(BULLET) + 2)
    found = [f for f in format_findings(text, None) if f["code"] == "too_long"]
    assert len(found) == 1 and found[0]["detail"] == f"{len(text)}>{FORMAT_MAX_CHARS}"


def test_findings_use_the_closed_codes_and_hold_no_cv_text():
    text = GOOD.replace("EXPERIENCE\n", "").replace(" | ada@example.com", "").replace(BULLET, "- Built **x** things")
    found = format_findings(text, None)
    assert {f["code"] for f in found} <= set(CODES)
    blob = repr(found)
    for secret in ("Ada", "Northwind", "Snowflake", "+44", "example.com", "Built"):
        assert secret not in blob
    assert all(set(f) <= {"code", "lines", "detail"} for f in found)


def test_findings_come_in_the_order_of_the_closed_codes():
    text = GOOD.replace("EXPERIENCE\n", "").replace(" | ada@example.com", "").replace(BULLET, "- Built **x** things")
    got = [f["code"] for f in format_findings(text, None)]
    assert got == sorted(got, key=CODES.index)


def test_empty_and_non_text_input_do_not_raise():
    assert "missing_section" in codes("")
    assert format_findings(None, None)  # type: ignore[arg-type]


def test_a_hostile_cv_is_scanned_in_bounded_time():
    junk_line = ("(,-" * 100) + "\n"
    cases = [
        "x" * 300_000,
        junk_line * 5_000,
        "**" * 100_000,
        "|" * 200_000 + "\n",
        ("Jan 2020 - " * 40 + "\n") * 2_000,
        "+" + "0" * 200_000,
    ]
    for text in cases:
        start = time.perf_counter()
        found = format_findings(text, None)
        assert time.perf_counter() - start < 0.5
        assert all(len(f.get("lines", [])) <= 20 for f in found)
