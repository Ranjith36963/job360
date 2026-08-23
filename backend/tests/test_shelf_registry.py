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
        """An allowlisted shelf can go stale two ways, and both must be caught:

        1. It names a shelf that HAS gained a reader — the exemption is no
           longer needed and should be deleted, or the list becomes a place
           where live fields hide.
        2. It names a shelf that no longer EXISTS on either dataclass — a
           rename or deletion left a dead entry exempting nothing.

        ALLOWED_UNREAD is empty right now, and that is the healthy state:
        every shelf currently earns its keep by being read. That also means
        BOTH checks below pass vacuously against production data today — an
        empty dict has no stale entries by definition. That is not what makes
        this test meaningful; see ``test_the_rot_check_can_actually_fail``
        immediately below, which proves the checking logic against a
        fabricated entry so the day someone adds a real exemption, this test
        is already known to bite.
        """
        known = frozenset(shelf_names())
        unknown = [name for name in ALLOWED_UNREAD if name not in known]
        stale = [name for name in ALLOWED_UNREAD if name in known and readers(name)]
        assert not unknown, (
            f"ALLOWED_UNREAD names shelves that no longer exist: {unknown}. "
            "The field was renamed or removed — delete the exemption."
        )
        assert not stale, f"ALLOWED_UNREAD is stale — these now have readers: {stale}"

    def test_the_rot_check_can_actually_fail(self) -> None:
        """Proof the assertions above are not vacuous.

        With ALLOWED_UNREAD empty, ``test_the_allowlist_does_not_rot`` passes
        no matter what the checking logic does — even a check that always
        returned ``[]`` would look green forever, which is exactly the shape
        of bug this whole file exists to catch (see the module docstring).
        This test runs the SAME two checks against a fabricated allowlist
        entry and confirms each one actually fires.
        """
        known = frozenset(shelf_names())
        real_shelf_with_a_reader = next(name for name in shelf_names() if readers(name))

        fake_allowlist = {
            "not_a_real_shelf_name": "made up for this test",
            real_shelf_with_a_reader: "pretend this used to be unread",
        }

        unknown = [name for name in fake_allowlist if name not in known]
        stale = [name for name in fake_allowlist if name in known and readers(name)]

        assert unknown == ["not_a_real_shelf_name"], (
            "the unknown-shelf check did not fire against a name that isn't "
            "a real shelf — it cannot protect against a renamed/deleted field"
        )
        assert stale == [real_shelf_with_a_reader], (
            "the has-a-reader check did not fire against a shelf known to "
            "have a reader — it cannot protect against a stale exemption"
        )


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


class TestReceiverIsTypeAware:
    """Finding 10 (2026-08-16): the visitor used to trust ANY parameter named
    ``profile``/``cv``/``prefs`` as a real profile receiver, with no check on
    what the parameter actually was. ``prefilter.py`` proved that false:

        def skill_overlap_ok(profile: FilterProfile, job: Job, ...) -> bool:
            if not profile.skills: ...

    ``FilterProfile`` is a dataclass the module docstring calls "decoupled
    from UserProfile" on purpose — this access never touches a real CV. The
    old bare-name check credited it anyway, so ``matching_tiers()`` reported
    the prefilter as narrowing every job on skill overlap when it never does.
    """

    def _found(self, source: str) -> set[str]:
        visitor = _ShelfVisitor(frozenset(shelf_names()))
        visitor.visit(ast.parse(source))
        return visitor.found

    def test_a_parameter_typed_as_a_different_dataclass_is_not_counted(self) -> None:
        # The exact shape of the bug: the bare name matches, the annotation
        # does not.
        src = (
            "def skill_overlap_ok(profile: FilterProfile, job: Job) -> bool:\n"
            "    return bool(profile.skills)\n"
        )
        assert "skills" not in self._found(src)

    def test_a_parameter_typed_as_the_real_profile_is_still_counted(self) -> None:
        # CONTROL — without this, "credit nothing" would also pass the test
        # above. A genuine, correctly-annotated receiver must keep its credit.
        src = "def f(cv: CVData) -> list:\n    return cv.skills\n"
        assert "skills" in self._found(src)

    def test_an_unannotated_parameter_keeps_the_old_bare_name_behaviour(self) -> None:
        # Most of this codebase's real reads go through a duck-typed `Any`
        # parameter on purpose (avoids an import cycle, rule #16) — e.g.
        # `llm_matcher.profile_to_matcher_text(profile: Any)`. Requiring an
        # annotation to match would silently un-credit all of those and make
        # the "judge" role look unread. Unannotated / Any-typed parameters
        # must still be trusted by bare name.
        src = "def f(cv) -> list:\n    return cv.skills\n"
        assert "skills" in self._found(src)
        src_any = "def f(cv: Any) -> list:\n    return cv.skills\n"
        assert "skills" in self._found(src_any)

    def test_a_non_parameter_local_alias_keeps_the_old_bare_name_behaviour(self) -> None:
        # `prefs = self._user_preferences; prefs.salary_min` (skill_matcher.py)
        # and `cv = getattr(profile, "cv_data", None)` (llm_matcher.py) are how
        # most of the scorer/judge reads are actually written — a LOCAL
        # variable, never a parameter. Only a parameter's own annotation can
        # override the bare-name match; an alias must not lose its credit.
        src = (
            "def f(profile):\n"
            "    cv = getattr(profile, 'cv_data', None)\n"
            "    return cv.skills\n"
        )
        assert "skills" in self._found(src)

    def test_the_production_prefilter_role_no_longer_claims_skills(self) -> None:
        # The regression the finding asked for, run against the REAL
        # codebase, not a fabricated snippet — proves the fix landed, not
        # just that the fabricated case above is well-formed.
        assert "prefilter" not in readers("skills"), (
            "the prefilter role is credited with reading 'skills' again — "
            "check whether a FOREIGN-typed parameter (like prefilter.py's "
            "FilterProfile) started sharing a profile receiver's bare name"
        )

    def test_the_prefilter_role_still_reads_what_it_actually_reads(self) -> None:
        # CONTROL for the test above at production scale: dropping the false
        # 'skills' credit must not silently zero the WHOLE role. The real
        # read lives in workers/tasks.py::_filter_profile_for, which builds
        # the FilterProfile prefilter.py runs on from the user's actual
        # UserPreferences — see the "tasks" -> "prefilter" mapping in
        # shelf_audit._ROLES.
        assert "prefilter" in readers("preferred_locations")
        assert "prefilter" in readers("work_arrangement")
        assert "prefilter" in readers("experience_level")


class TestMatchingCoverageDoesNotRegress:
    """A ratchet. Wiring more shelves into matching is the current goal, so this
    may only ever move UP. If a change drops a shelf out of matching, that is
    either a mistake or a decision that should be made deliberately here."""

    # 35 measured against origin/main d8ec43e (cross-checked by hand against all
    # nine matching modules); raised to 46 by the shelf-completeness batch, which
    # wired the LinkedIn evidence sections, cv_industries, cv_languages,
    # career_domain and linkedin_summary into the judge and the vector.
    #
    # 50 -> 49 on 2026-08-13, and this is the ONE legitimate reason to lower a
    # ratchet that is otherwise up-only. No engine lost an input. Two shelf NAMES
    # collapsed into one: ``preferred_workplace`` was a stored copy of
    # ``work_arrangement``, written by a bridge in the profile route on every
    # save. It is now a read-only property, so the pair is one shelf that every
    # consumer reads, and the audit counts it once instead of twice.
    #
    # The proof that nothing went dark is in the READER SET, not the count:
    # ``work_arrangement`` now lists ``scorer`` among its readers, which it never
    # did before, because a read of a derived view is folded into its source (see
    # ``shelf_audit.derived_shelves``). Deduplication should lower this number.
    # A shelf falling out of matching should not, and still fails the test below.
    BASELINE = 49

    def test_the_headline_number_is_broken_down_by_reach(self) -> None:
        """A single "N shelves feed matching" figure counts a shelf the LLM
        judge reads on a capped window the same as one the keyword engine reads
        on every row. Those are not the same claim, and the flattering one is
        the easy one to repeat. This pins the honest breakdown as something the
        code computes rather than something a summary asserts."""
        from src.services.profile.shelf_audit import matching_shelves, matching_tiers

        tiers = matching_tiers()
        assert set(tiers) == {"every_job", "judge_only", "semantic_only"}
        total = sum(len(v) for v in tiers.values())
        assert total == len(matching_shelves()), "a shelf fell out of every tier"
        # The every-job tier is the one worth quoting: it changes the rank of
        # every job in the feed. If it hits zero the engine is nominal.
        assert tiers["every_job"], "no shelf reaches the engine that ranks every job"

    def test_matching_shelf_count_never_falls(self) -> None:
        current = sum(1 for n in shelf_names() if is_matching_shelf(n))
        assert current >= self.BASELINE, (
            f"Matching shelves fell from {self.BASELINE} to {current}. A shelf "
            "stopped feeding the engines — find which consumer stopped reading it."
        )

    def test_every_matching_role_is_actually_used(self) -> None:
        """This test used to read ``used & MATCHING_ROLES - MATCHING_ROLES``.

        ``&`` and ``-`` bind left to right at equal precedence, so that is
        ``(used & M) - M`` — the empty set for EVERY possible value of ``used``.
        It could not fail. It sat inside a regression class and read like
        coverage, which is worse than no test: it is the exact assertion that
        should have caught a declared matching role reading zero shelves.
        """
        used: set[str] = set()
        for name in shelf_names():
            used |= readers(name)

        # A role we CLAIM is a matching role must earn it by being read by at
        # least one shelf. A role nobody reads inflates the headline "N shelves
        # feed matching" with a module that contributes none of them.
        idle = sorted(MATCHING_ROLES - used)
        assert not idle, (
            f"These roles are declared matching but read zero shelves: {idle}. "
            "Wire them or remove them from MATCHING_ROLES — a role with no "
            "reader makes the matching count claim more than it delivers."
        )

        from src.services.profile.shelf_audit import _ROLES

        undefined = sorted(used - set(_ROLES.values()))
        assert not undefined, f"readers() returned undefined roles: {undefined}"


@pytest.mark.parametrize("name", list(shelf_names()))
def test_every_shelf_has_both_a_writer_and_a_reader(name: str) -> None:
    """Per-shelf so a failure names the offending shelf, not just a count."""
    assert writers(name), f"{name}: nothing can write it"
    if name not in ALLOWED_UNREAD:
        assert readers(name), f"{name}: nothing reads it"


class TestDerivedViewsAreFoldedIntoTheirSource:
    """Rule 5, added 2026-08-13 with the workplace collapse.

    A ``@property`` on a profile dataclass is a VIEW: no storage, no writer, so
    it is correctly absent from ``shelf_names()`` — rule 1 would fail it, and
    should. But consumers read it BY NAME, and that read is a real read of the
    shelf underneath.

    Miss that and the audit lies in the direction that flatters it: the stored
    shelf looks less used than it is. ``work_arrangement`` would have reported
    as judge-and-vector-only while in fact it ranks every job in the feed
    through ``preferred_workplace``.
    """

    def test_the_mapping_is_parsed_from_the_source_not_hand_listed(self) -> None:
        """The point of deriving it: a property renamed or repointed tomorrow
        updates this map for free. A hand-typed dict would be one more copy to
        drift — the exact bug class this module exists to catch."""
        from src.services.profile.shelf_audit import derived_shelves

        assert derived_shelves().get("preferred_workplace") == ("work_arrangement",)

    def test_a_derived_view_is_not_itself_a_shelf(self) -> None:
        """It has no writer and never can have one. Counting it as a shelf would
        force a permanent exemption in rule 1 — the honest answer is that it is
        not a shelf at all."""
        from src.services.profile.shelf_audit import derived_shelves

        for view in derived_shelves():
            assert view not in shelf_names(), (
                f"{view} is a property AND a stored field — one of the two is a "
                "copy that can disagree with the other"
            )

    def test_reading_the_view_credits_the_source(self) -> None:
        """The assertion that actually protects the audit's honesty. The keyword
        scorer reads ``prefs.preferred_workplace``; ``work_arrangement`` is what
        holds that answer, so ``work_arrangement`` must show the scorer."""
        assert "scorer" in readers("work_arrangement"), (
            "work_arrangement lost its every-job reader — either the scorer "
            "stopped reading the workplace preference, or the derived-view fold "
            "broke and the audit is now under-reporting this shelf's reach"
        )

    def test_every_derived_view_points_at_real_shelves(self) -> None:
        from src.services.profile.shelf_audit import derived_shelves

        names = set(shelf_names())
        for view, sources in derived_shelves().items():
            assert sources, f"{view}: derived from nothing"
            unknown = sorted(set(sources) - names)
            assert not unknown, f"{view}: derived from non-shelves {unknown}"
