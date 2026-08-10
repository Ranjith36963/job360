"""THE SHELF LAW, enforced.

Job360's user side is 66 field declarations across two dataclasses. The rules
the owner set for them are simple and, until this file, unenforced:

  1. A shelf the user's input can fill MUST be fillable — something has to write it.
  2. A shelf nobody reads is dead weight, not a gap. Either wire it or delete it.
  3. Nothing may read a field that does not exist.
  4. Two shelves must not share a name; a name-keyed tool cannot tell them apart.

``scripts/shelf_manifest.py`` already printed most of this — and that was the
problem. A script you must remember to run is not a guard; it is a document that
happens to execute. Every one of these rules was broken in production while that
script sat in the repo saying so, because nothing failed when they broke. This
file is the notifier that script never had.

Pure static analysis: no DB, no network, no LLM (rule #4). Runs in well under a
second.
"""
from __future__ import annotations

import ast

import pytest

from src.services.profile.shelf_audit import (
    MATCHING_ROLES,
    _ShelfVisitor,
    colliding_names,
    is_matching_shelf,
    phantom_reads,
    readers,
    shelf_names,
    shelf_owners,
    writers,
)

# Shelves that legitimately have NO consumer beyond storage. Each entry must
# carry a reason, and the reason must be about the PRODUCT, not about it being
# inconvenient to wire. An empty allowlist is the goal state.
ALLOWED_UNREAD: dict[str, str] = {}


class TestEveryShelfCanBeFilled:
    """Rule 1. The owner's sharpest line: 'if they give us inputs and we don't
    populate, that is OUR fault.' A shelf with no writer can never be filled by
    any input, so it is a permanent, guaranteed hole."""

    def test_no_shelf_is_unfillable(self) -> None:
        orphans = {name: writers(name) for name in shelf_names() if not writers(name)}
        assert not orphans, (
            "These shelves have no writer anywhere — no user input can ever fill "
            f"them: {sorted(orphans)}"
        )


class TestEveryShelfIsRead:
    """Rule 2. 'An artifact with no consumer dies.' A shelf that is extracted,
    stored, versioned and shown to nobody costs a paid LLM call per profile and
    returns nothing."""

    def test_no_shelf_is_unread(self) -> None:
        orphans = [
            name
            for name in shelf_names()
            if not readers(name) and name not in ALLOWED_UNREAD
        ]
        assert not orphans, (
            f"These shelves are written but read by nothing: {sorted(orphans)}. "
            "Wire each to a consumer, delete it, or add it to ALLOWED_UNREAD "
            "with a product reason."
        )

    def test_the_allowlist_does_not_rot(self) -> None:
        """An allowlisted shelf that HAS gained a reader must leave the list, or
        the list slowly becomes a place where live fields hide."""
        stale = [name for name in ALLOWED_UNREAD if readers(name)]
        assert not stale, f"ALLOWED_UNREAD is stale — these now have readers: {stale}"


class TestNoPhantomReads:
    """Rule 3. ``getattr(cv, "linkedin_summary", "")`` returned "" on every
    profile for the life of the field, because ``CVData`` has never had a
    ``linkedin_summary``. getattr-with-a-default cannot fail, so no test could
    have caught it by running the code — only by reading it."""

    def test_nothing_reads_a_field_that_does_not_exist(self) -> None:
        ghosts = phantom_reads()
        assert not ghosts, (
            "These modules getattr a profile field that does not exist, and so "
            f"silently receive the default forever: {ghosts}"
        )


class TestNoAmbiguousNames:
    """Rule 4. ``industries`` was declared on BOTH dataclasses. They mean
    opposite things — one is a fact extracted FROM the CV, the other a
    preference stated BY the user — and every name-keyed tool in the project
    (this registry, the manifest, the API serialiser) silently merged them."""

    def test_no_name_is_declared_on_two_dataclasses(self) -> None:
        collisions = {n: shelf_owners()[n] for n in colliding_names()}
        assert not collisions, (
            f"These names are declared on more than one profile dataclass: "
            f"{collisions}. Rename one — a shared name cannot be told apart by "
            "any tool that keys on it."
        )


class TestTheInstrumentCountsLikeAConsumer:
    """The registry is only worth having if it is not itself lying.

    These pin the two directions the previous regex-based manifest got wrong.
    Without them, a future 'simplification' back to a text search would pass
    every other test in this file.
    """

    def _found(self, source: str) -> set[str]:
        visitor = _ShelfVisitor(frozenset(shelf_names()))
        visitor.visit(ast.parse(source))
        return visitor.found

    def test_a_field_read_off_a_job_is_not_credited_to_the_user_side(self) -> None:
        # The exact false positive that credited display-only `cv.location` to
        # the scorer and the prefilter: both objects have a `location`.
        assert self._found("x = job.location") == set()
        assert self._found("x = enrichment.skills") == set()
        assert self._found('n = repo.get("name")') == set()

    def test_a_field_read_off_the_profile_is_counted(self) -> None:
        assert "location" in self._found("x = cv.location")
        assert "skills" in self._found("x = profile.cv_data.skills")
        assert "salary_min" in self._found("x = prefs.salary_min")

    def test_a_literal_getattr_is_counted(self) -> None:
        assert "linkedin_skills" in self._found('x = getattr(cv, "linkedin_skills", [])')

    def test_a_table_driven_getattr_is_counted(self) -> None:
        # embeddings.py and llm_matcher.py read MOST of their fields this way.
        # A tool blind to this shape reports the semantic engine as reading
        # almost nothing, which is how these shelves looked unused for months.
        src = (
            'for f in ("skills", "linkedin_skills"):\n'
            '    parts.append(getattr(cv, f, []))\n'
        )
        assert {"skills", "linkedin_skills"} <= self._found(src)

    def test_a_label_sharing_a_tuple_with_a_field_is_not_counted(self) -> None:
        src = (
            'for label, f in (("Education", "education"),):\n'
            '    parts.append(getattr(cv, f, []))\n'
        )
        found = self._found(src)
        assert "education" in found
        assert "Education" not in found

    def test_a_constructor_keyword_is_counted(self) -> None:
        # The preference form builds the dataclass in one call rather than
        # assigning field by field. Missing this made seven live, user-typed
        # preferences report 'nothing can ever write this'.
        assert "salary_min" in self._found("p = UserPreferences(salary_min=1)")


class TestMatchingCoverageDoesNotRegress:
    """A ratchet. Wiring more shelves into matching is the current goal, so this
    may only ever move UP. If a change drops a shelf out of matching, that is
    either a mistake or a decision that should be made deliberately here."""

    # 35 measured against origin/main d8ec43e (cross-checked by hand against all
    # nine matching modules); raised to 46 by the shelf-completeness batch, which
    # wired the LinkedIn evidence sections, cv_industries, cv_languages,
    # career_domain and linkedin_summary into the judge and the vector.
    BASELINE = 46

    def test_matching_shelf_count_never_falls(self) -> None:
        current = sum(1 for n in shelf_names() if is_matching_shelf(n))
        assert current >= self.BASELINE, (
            f"Matching shelves fell from {self.BASELINE} to {current}. A shelf "
            "stopped feeding the engines — find which consumer stopped reading it."
        )

    def test_every_matching_role_is_a_real_role(self) -> None:
        used = set()
        for name in shelf_names():
            used |= readers(name)
        unknown = used & MATCHING_ROLES - MATCHING_ROLES
        assert not unknown


@pytest.mark.parametrize("name", list(shelf_names()))
def test_every_shelf_has_both_a_writer_and_a_reader(name: str) -> None:
    """Per-shelf so a failure names the offending shelf, not just a count."""
    assert writers(name), f"{name}: nothing can write it"
    if name not in ALLOWED_UNREAD:
        assert readers(name), f"{name}: nothing reads it"
