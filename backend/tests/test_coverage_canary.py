"""COVERAGE CANARY — pins `job_has_value`'s value-presence rule with known
ground truth, per matching dimension.

WHY THIS EXISTS. `scripts/shelf_xray.py` used to report salary coverage as
83% via `e.salary NOT IN ('{}', '')` — a predicate that tests JSON *shape*
against two literal strings. `{"min": null, "max": null}` is neither literal,
so it passed while carrying zero usable data; the real figure (parse the
JSON, check for an actual number) was ~5%. `job_has_value` (src/services/
coverage.py) is the fix, and this file is the guardrail: it builds rows with
a KNOWN true-positive count `k` — some genuinely covered, the rest empty in
every shape this codebase's data has actually taken (or could plausibly
take) for that dimension — and asserts `job_has_value` counts EXACTLY `k`.

THE POINT: any future predicate that admits one of these empty shapes as
"covered" breaks this test the instant it is written. This is a regression
tripwire for the exact bug class above — not a general happy-path test suite
for coverage.py.

Pure functions over plain dicts — no DB connection, no network (rule #4).
Run: `python -m pytest tests/test_coverage_canary.py -q -p no:randomly`.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from src.services.coverage import DIMENSIONS, job_has_value

# A real ad is long — JobScorer's match text is `title + description`, and
# the skill-text threshold in coverage.py is 200 chars (see its docstring).
LONG_DESCRIPTION = (
    "We are looking for a backend engineer to join our growing platform "
    "team. You will design and build scalable services, own the data "
    "layer, and collaborate closely with product on new features. "
    "Experience with Python, PostgreSQL, and distributed systems is "
    "required. Nice to have: Kubernetes, Terraform, and prior fintech "
    "domain exposure. We offer a hybrid working pattern and a strong "
    "engineering culture built on code review and mentorship."
)
SHORT_DESCRIPTION = "Great team, apply now."
assert len(LONG_DESCRIPTION) > 200  # fixture sanity: must clear coverage.py's threshold
assert len(SHORT_DESCRIPTION) <= 200


def _count_covered(dimension: str, rows: list[tuple[dict, Optional[dict]]]) -> int:
    """Run every (job_row, enrichment_row) pair through job_has_value and
    count the Trues — the one operation every test class below pins."""
    return sum(1 for job, enr in rows if job_has_value(dimension, job, enr))


class TestEveryDimensionHasACanary:
    """If `coverage.DIMENSIONS` grows a new dimension without a matching
    canary class in this file, that dimension's predicate ships unguarded.
    Fail loudly here rather than silently."""

    _CANARIED = {
        "salary", "skills", "titles", "location", "workplace", "seniority",
        "domain", "visa",
    }

    def test_no_dimension_is_untested(self) -> None:
        assert set(DIMENSIONS) == self._CANARIED

    def test_unknown_dimension_raises(self) -> None:
        with pytest.raises(ValueError):
            job_has_value("not_a_real_dimension", {}, None)


class TestSalaryCoverage:
    """job_enrichment.salary is a JSON-TEXT column (migration 0008, default
    `'{}'`). Every shape below is a real or trivially-possible value for
    that column and must NOT be read as an actual number — this is the
    exact incident the module exists to fix."""

    COVERED: list[tuple[dict, Optional[dict]]] = [
        ({"salary_min": 35000, "salary_max": 45000}, None),
        (
            {"salary_min": None, "salary_max": None},
            {"salary": '{"min": 30000, "max": 50000, "currency": "GBP", "frequency": "annual"}'},
        ),
        # Already-parsed dict input (a caller that pre-decoded) with only
        # one real bound — still a usable number.
        ({"salary_min": None, "salary_max": None}, {"salary": {"min": None, "max": 42000}}),
    ]

    EMPTY_SALARY_SHAPES: list[Any] = [
        "{}", '{"min": null}', '{"min": null, "max": null}', "", None,
        "unknown", 0, [], "[]",
    ]

    @pytest.mark.parametrize("shape", EMPTY_SALARY_SHAPES)
    def test_each_empty_shape_is_uncovered(self, shape: Any) -> None:
        job_row = {"salary_min": None, "salary_max": None}
        assert job_has_value("salary", job_row, {"salary": shape}) is False

    def test_no_enrichment_row_is_uncovered(self) -> None:
        job_row = {"salary_min": None, "salary_max": None}
        assert job_has_value("salary", job_row, None) is False

    def test_exact_count(self) -> None:
        rows = list(self.COVERED)
        rows += [
            ({"salary_min": None, "salary_max": None}, {"salary": shape})
            for shape in self.EMPTY_SALARY_SHAPES
        ]
        rows.append(({"salary_min": None, "salary_max": None}, None))
        assert _count_covered("salary", rows) == len(self.COVERED)


class TestSkillsCoverage:
    """Skill signal comes from EITHER a real description (>200 chars — what
    JobScorer's match text actually needs) OR the LLM's `required_skills`
    list. Both must be absent, in every shape `required_skills` can take,
    for a row to count as uncovered."""

    COVERED: list[tuple[dict, Optional[dict]]] = [
        ({"description": LONG_DESCRIPTION}, None),
        ({"description": SHORT_DESCRIPTION}, {"required_skills": '["Python", "SQL"]'}),
        ({"description": SHORT_DESCRIPTION}, {"required_skills": ["Kubernetes"]}),
    ]

    EMPTY_SKILLS_SHAPES: list[Any] = [
        "{}", '{"min": null}', '{"min": null, "max": null}', "", None,
        "unknown", 0, [], "[]",
    ]

    @pytest.mark.parametrize("shape", EMPTY_SKILLS_SHAPES)
    def test_each_empty_shape_is_uncovered(self, shape: Any) -> None:
        job_row = {"description": SHORT_DESCRIPTION}
        assert job_has_value("skills", job_row, {"required_skills": shape}) is False

    def test_no_description_no_enrichment_is_uncovered(self) -> None:
        assert job_has_value("skills", {"description": None}, None) is False

    def test_exact_count(self) -> None:
        rows = list(self.COVERED)
        rows += [
            ({"description": SHORT_DESCRIPTION}, {"required_skills": shape})
            for shape in self.EMPTY_SKILLS_SHAPES
        ]
        rows.append(({"description": SHORT_DESCRIPTION}, None))
        rows.append(({"description": None}, None))
        assert _count_covered("skills", rows) == len(self.COVERED)


class TestTitleCoverage:
    """Titles are read verbatim off the `jobs` row — no enrichment
    fallback. Only a real non-blank string counts; a non-string value (never
    supposed to happen on a TEXT column, but defensive) must not."""

    COVERED: list[dict] = [
        {"title": "Senior Backend Engineer"},
        {"title": "  Data Scientist  "},  # whitespace padding is still a real title
    ]
    EMPTY_TITLE_SHAPES: list[Any] = ["", None, "   ", 0, [], {}]

    @pytest.mark.parametrize("shape", EMPTY_TITLE_SHAPES)
    def test_each_empty_shape_is_uncovered(self, shape: Any) -> None:
        assert job_has_value("titles", {"title": shape}, None) is False

    def test_exact_count(self) -> None:
        rows: list[tuple[dict, Optional[dict]]] = [(j, None) for j in self.COVERED]
        rows += [({"title": shape}, None) for shape in self.EMPTY_TITLE_SHAPES]
        assert _count_covered("titles", rows) == len(self.COVERED)


class TestLocationCoverage:
    """Same rule as titles — plain non-blank string, job-row only."""

    COVERED: list[dict] = [{"location": "London, UK"}, {"location": "Remote"}]
    EMPTY_LOCATION_SHAPES: list[Any] = ["", None, "   ", 0, [], {}]

    @pytest.mark.parametrize("shape", EMPTY_LOCATION_SHAPES)
    def test_each_empty_shape_is_uncovered(self, shape: Any) -> None:
        assert job_has_value("location", {"location": shape}, None) is False

    def test_exact_count(self) -> None:
        rows: list[tuple[dict, Optional[dict]]] = [(j, None) for j in self.COVERED]
        rows += [({"location": shape}, None) for shape in self.EMPTY_LOCATION_SHAPES]
        assert _count_covered("location", rows) == len(self.COVERED)


class TestEnumEnrichmentCoverage:
    """workplace_type / seniority / category are never NULL once enrichment
    has run — the LLM contract (job_enrichment.py `_SYSTEM_PROMPT`) writes
    the literal string "unknown" instead of omitting the field. So the ONLY
    real test is "not unknown, not blank"; a bare `IS NOT NULL` check (the
    shape of the original salary bug, applied here) would count every one
    of these rows as covered."""

    FIELD_BY_DIMENSION = {
        "workplace": "workplace_type",
        "seniority": "seniority",
        "domain": "category",
    }
    EMPTY_ENUM_SHAPES: list[Any] = [
        None, "", "   ", "unknown", "UNKNOWN", "Unknown", 0, [], {},
    ]

    @pytest.mark.parametrize("dimension,field", list(FIELD_BY_DIMENSION.items()))
    @pytest.mark.parametrize("shape", EMPTY_ENUM_SHAPES)
    def test_each_empty_shape_is_uncovered(self, dimension: str, field: str, shape: Any) -> None:
        assert job_has_value(dimension, {}, {field: shape}) is False

    @pytest.mark.parametrize("dimension,field", list(FIELD_BY_DIMENSION.items()))
    def test_no_enrichment_row_is_uncovered(self, dimension: str, field: str) -> None:
        assert job_has_value(dimension, {}, None) is False

    @pytest.mark.parametrize(
        "dimension,field,real_value",
        [
            ("workplace", "workplace_type", "remote"),
            ("seniority", "seniority", "senior"),
            ("domain", "category", "software_engineering"),
        ],
    )
    def test_exact_count(self, dimension: str, field: str, real_value: str) -> None:
        covered: list[tuple[dict, Optional[dict]]] = [
            ({}, {field: real_value}),
            ({}, {field: f"  {real_value}  "}),  # incidental whitespace around a real value
        ]
        empty: list[tuple[dict, Optional[dict]]] = [
            ({}, {field: shape}) for shape in self.EMPTY_ENUM_SHAPES
        ]
        empty.append(({}, None))
        assert _count_covered(dimension, covered + empty) == len(covered)


class TestVisaCoverage:
    """Delegates to `detect_visa_status` (visa_signal.py) rather than
    reading `job_enrichment.visa_sponsorship` alone — that column alone is
    why the old script under-reported visa coverage at 3% against the ~42%
    the text detector actually resolves. Every empty shape below must fall
    through to UNKNOWN when the text contains no visa phrase."""

    NO_MENTION = {"title": "Backend Engineer", "description": "Great team, competitive salary."}
    POSITIVE_TEXT = {"title": "Backend Engineer", "description": "Visa sponsorship available for this role."}

    EMPTY_VISA_SHAPES: list[Any] = [
        "{}", '{"min": null}', '{"min": null, "max": null}', "", None,
        "unknown", 0, [], "[]",
    ]

    @pytest.mark.parametrize("shape", EMPTY_VISA_SHAPES)
    def test_each_empty_shape_is_uncovered(self, shape: Any) -> None:
        assert job_has_value("visa", self.NO_MENTION, {"visa_sponsorship": shape}) is False

    def test_exact_count(self) -> None:
        covered: list[tuple[dict, Optional[dict]]] = [
            (self.POSITIVE_TEXT, None),  # text-detected positive, no enrichment at all
            (self.NO_MENTION, {"visa_sponsorship": "yes"}),
            # "no_sponsorship" is a real, non-UNKNOWN verdict — it counts as
            # covered even though it isn't a sponsorship offer (rule #29 /
            # visa_signal.py: silence vs refusal are opposite facts, both
            # are "known", only silence is "unknown").
            (self.NO_MENTION, {"visa_sponsorship": "no"}),
        ]
        empty: list[tuple[dict, Optional[dict]]] = [
            (self.NO_MENTION, {"visa_sponsorship": shape}) for shape in self.EMPTY_VISA_SHAPES
        ]
        empty.append((self.NO_MENTION, None))
        assert _count_covered("visa", covered + empty) == len(covered)
