"""The shelf registry: every user-side shelf, who WRITES it, who READS it.

WHY THIS EXISTS, AND WHY IT IS NOT A REGEX.
``scripts/shelf_manifest.py`` answered the same question with a word-boundary
regex over file text, and was wrong in BOTH directions at once:

  * UNDER-reported writers. It scanned only ``src/services/profile/``, so the
    five upload receipts (``cv_filename`` and friends) printed ``NOWHERE`` while
    being populated on every live account — their writer is in
    ``api/routes/profile.py``. An instrument that says a live field is unwritten
    sends someone to "fix" working code.
  * OVER-reported readers. ``\\blocation\\b`` matches ``job.location`` just as
    happily as ``cv.location``, so a display-only shelf was credited to the
    scorer and the prefilter. An instrument that says a dead field is alive
    hides the actual gap.

This is the same failure the coverage canary was built for: the instrument must
count the way the CONSUMER counts. So this module parses the code with ``ast``
and only counts an access when the RECEIVER is a profile object — ``cv.skills``
counts, ``job.skills`` does not.

Three access shapes are recognised, because all three are load-bearing in this
codebase:

  1. attribute       ``cv.skills`` / ``prefs.salary_min`` / ``profile.cv_data.x``
  2. literal getattr ``getattr(cv, "linkedin_skills", [])``
  3. table-driven    ``for f in ("skills", "linkedin_skills"): getattr(cv, f)``

Shape 3 is not an exotic case — it is how ``embeddings.py`` and
``llm_matcher.py`` read most of their fields. A tool blind to it would report
the semantic engine as reading almost nothing.

Read-only. Stdlib only (rule #16 — no heavy imports).
"""
from __future__ import annotations

import ast
import dataclasses
from functools import lru_cache
from pathlib import Path
from typing import Iterable

_BACKEND = Path(__file__).resolve().parents[3]
_SRC = _BACKEND / "src"

# Names that, at a call site, refer to a UserProfile / CVData / UserPreferences.
# Anything else (``job``, ``enrichment``, ``repo``, ``pos``) is a different
# object that merely happens to share a field name — the exact confusion that
# made the regex version credit ``job.location`` to ``cv.location``.
_PROFILE_RECEIVERS = frozenset(
    {
        "cv",
        "cv_data",
        "cvdata",
        "prefs",
        "preferences",
        "user_preferences",
        "_user_preferences",
        "profile",
        "user_profile",
        "self._user_preferences",
        "self._profile",
    }
)

# Module stem -> the role it plays. A shelf is "used for matching" when at least
# one MATCHING role reads it. ``api`` means it only reaches the screen.
_ROLES: dict[str, str] = {
    "keyword_generator": "search-keywords",
    "skill_tiering": "tiering",
    "skill_matcher": "scorer",
    "scoring_dimensions": "scorer",
    "llm_matcher": "judge",
    "prefilter": "prefilter",
    "embeddings": "semantic",
    # retrieval.py is mapped to the same role deliberately: it reads ZERO
    # profile shelves today (verified: no cv./prefs./getattr access at all), so
    # it contributes nothing on its own. The "semantic" role is earned entirely
    # by embeddings.py. Keeping the mapping costs nothing and means a future
    # profile read in retrieval.py is counted automatically.
    "retrieval": "semantic",
    # "feed" was here as its own MATCHING role. services/feed.py also reads zero
    # profile shelves, so unlike "semantic" nothing else earned it — it was a
    # declared matching role with no reader, quietly inflating the headline
    # "N shelves feed matching" number with a module that contributes none.
    # Removed until feed.py actually reads a shelf. This is exactly what the
    # de-tautologised test_every_matching_role_is_actually_used caught on its
    # first real run.
    "feed": "persistence",
    # Not matching — kept so the manifest can show WHERE a shelf does surface.
    "profile": "api",
    "jobs": "api",
    "tailor": "tailor",
    "snapshot": "api",
}

# Roles whose consumer can change what a user actually sees. Every one of these
# must be READ BY AT LEAST ONE SHELF — a declared role with no reader inflates
# the count with a module that contributes nothing, which is why
# test_every_matching_role_is_actually_used exists.
#
# NOT equal reach, and the difference matters when quoting the number:
#   scorer / search-keywords / tiering / prefilter -> rank EVERY job
#   judge                                          -> the top slice only
#   semantic                                       -> only when the vector runs
MATCHING_ROLES = frozenset(
    {"search-keywords", "tiering", "scorer", "judge", "prefilter", "semantic"}
)

# Modules that WRITE shelves. Extraction lives under services/profile/, but the
# upload receipts are stamped by the API route — the blind spot that made the
# old manifest report five live fields as unwritten.
_WRITER_ROLES: dict[str, str] = {
    "cv_parser": "cv-parse",
    "schemas": "cv-parse",
    "linkedin_parser": "linkedin-parse",
    "github_enricher": "github-fetch",
    "preferences": "prefs",
    "two_pass": "merge",
    "seniority": "derived",
    "llm_curate": "derived",
    "skill_tiering": "derived",
    "storage": "storage",
    "profile": "api-route",  # api/routes/profile.py — stamps receipts
}


def _receiver_name(node: ast.AST) -> str:
    """Flatten a receiver expression to a comparable name.

    ``cv`` -> "cv";  ``self._user_preferences`` -> "self._user_preferences";
    ``profile.cv_data`` -> "cv_data" (the tail is what identifies the object).
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _receiver_name(node.value)
        if base == "self":
            return f"self.{node.attr}"
        # ``profile.cv_data.skills`` — the meaningful receiver is ``cv_data``.
        return node.attr
    return ""


def _is_profile_receiver(node: ast.AST) -> bool:
    return _receiver_name(node) in _PROFILE_RECEIVERS


def _string_constants(node: ast.AST) -> list[str]:
    """Every string literal inside a (possibly nested) tuple/list literal."""
    out: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            out.append(child.value)
    return out


def _loop_targets(target: ast.AST) -> set[str]:
    """The names bound by a ``for`` target, including tuple unpacking."""
    names: set[str] = set()
    for child in ast.walk(target):
        if isinstance(child, ast.Name):
            names.add(child.id)
    return names


def _loop_field_literals(node: ast.For) -> list[str]:
    """For ``for ... in (<table>): ... getattr(cv, <var>)``, the field names only.

    POSITIONAL, not "every string in the table". A row often carries more than
    the field name::

        for label, field, keys in (
            ("Recommendations", "linkedin_recommendations", ("author", "text")),
        ):
            rows = getattr(cv, field, [])

    Only ``field`` is passed to getattr, so only index 1 names a shelf. A visitor
    that swept up every literal would report ``author`` and ``text`` — dict keys
    for the ROW, not fields on the profile — as phantom shelves. Reading the
    position the getattr actually uses is what makes this instrument honest
    about its own output.
    """
    target = node.target
    if isinstance(target, ast.Name):
        order = [target.id]
    elif isinstance(target, (ast.Tuple, ast.List)):
        order = [e.id if isinstance(e, ast.Name) else "" for e in target.elts]
    else:
        return []

    used: set[int] = set()
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "getattr"
            and len(child.args) >= 2
            and _is_profile_receiver(child.args[0])
            and isinstance(child.args[1], ast.Name)
            and child.args[1].id in order
        ):
            used.add(order.index(child.args[1].id))
    if not used:
        return []

    out: list[str] = []
    for element in getattr(node.iter, "elts", []):
        if isinstance(element, (ast.Tuple, ast.List)):
            for idx in used:
                if idx < len(element.elts):
                    item = element.elts[idx]
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        out.append(item.value)
        elif isinstance(element, ast.Constant) and isinstance(element.value, str):
            # Flat table: ``for f in ("skills", "linkedin_skills")``.
            if 0 in used:
                out.append(element.value)
    return out


class _ShelfVisitor(ast.NodeVisitor):
    """Collect shelf names accessed on a profile object, in all four shapes.

    Pass ``shelves=None`` to collect EVERY attribute name touched on a profile
    object rather than only the known ones — that is how phantom reads (a field
    that has never existed) are found.
    """

    def __init__(self, shelves: frozenset[str] | None) -> None:
        self.shelves = shelves
        self.found: set[str] = set()

    def _known(self, name: str) -> bool:
        return True if self.shelves is None else name in self.shelves

    # Shape 1 — cv.skills
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self._known(node.attr) and _is_profile_receiver(node.value):
            self.found.add(node.attr)
        self.generic_visit(node)

    # Shape 2 — getattr(cv, "skills", [])
    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and _is_profile_receiver(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
            and self._known(node.args[1].value)
        ):
            self.found.add(node.args[1].value)
        self._visit_constructor(node)
        self.generic_visit(node)

    # Shape 4 — UserPreferences(salary_min=..., needs_visa=...)
    #
    # The preference form does not assign attribute-by-attribute; it builds the
    # dataclass in one call. Missing this shape made SEVEN live, user-typed
    # preferences report "nothing can ever write this" — the precise false
    # alarm this module was written to stop.
    def _visit_constructor(self, node: ast.Call) -> None:
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in ("CVData", "UserPreferences", "UserProfile"):
            return
        for kw in node.keywords:
            if kw.arg and self._known(kw.arg):
                self.found.add(kw.arg)

    # Shape 3 — for f in ("skills", "linkedin_skills"): getattr(cv, f)
    def visit_For(self, node: ast.For) -> None:
        self.found.update(s for s in _loop_field_literals(node) if self._known(s))
        self.generic_visit(node)


@lru_cache(maxsize=1)
def shelf_names() -> tuple[str, ...]:
    """Every DISTINCT shelf name, read off the dataclasses so it cannot drift.

    Distinct, not total: ``industries`` is declared on BOTH ``CVData`` and
    ``UserPreferences``, so 51 + 15 declarations are only 65 names. That
    collision is not cosmetic — every name-keyed tool (this one, the manifest,
    the API serialiser) silently merges the two, and they mean different things:
    one is extracted FROM the CV, the other is stated BY the user. See
    ``colliding_names()``.
    """
    from src.services.profile.models import CVData, UserPreferences

    names: list[str] = []
    for cls in (CVData, UserPreferences):
        for f in dataclasses.fields(cls):
            if f.name not in names:
                names.append(f.name)
    return tuple(names)


@lru_cache(maxsize=1)
def shelf_owners() -> dict[str, tuple[str, ...]]:
    """shelf name -> the dataclass(es) that declare it."""
    from src.services.profile.models import CVData, UserPreferences

    owners: dict[str, list[str]] = {}
    for cls in (CVData, UserPreferences):
        for f in dataclasses.fields(cls):
            owners.setdefault(f.name, []).append(cls.__name__)
    return {k: tuple(v) for k, v in owners.items()}


def colliding_names() -> tuple[str, ...]:
    """Names declared on more than one profile dataclass — ambiguous by construction."""
    return tuple(n for n, owners in shelf_owners().items() if len(owners) > 1)


def _scan(paths: Iterable[Path], shelves: frozenset[str]) -> dict[str, set[str]]:
    """Map file stem -> shelf names accessed on a profile object in that file."""
    out: dict[str, set[str]] = {}
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        visitor = _ShelfVisitor(shelves)
        visitor.visit(tree)
        if visitor.found:
            out.setdefault(path.stem, set()).update(visitor.found)
    return out


@lru_cache(maxsize=1)
def _reader_index() -> dict[str, set[str]]:
    """shelf -> set of roles that read it."""
    shelves = frozenset(shelf_names())
    index: dict[str, set[str]] = {name: set() for name in shelves}
    for stem, found in _scan(_SRC.rglob("*.py"), shelves).items():
        role = _ROLES.get(stem)
        if not role:
            continue
        for name in found:
            index[name].add(role)
    return index


@lru_cache(maxsize=1)
def _writer_index() -> dict[str, set[str]]:
    """shelf -> set of roles that write it.

    Writes are assignments and in-place mutations, which are far harder to tell
    apart from reads than the reverse; rather than guess, this counts any
    profile-object access inside a WRITER module. A module that only reads a
    shelf while writing others is a rare and harmless over-count here, and the
    question this index answers ("can anything fill this shelf at all?") is not
    sensitive to it.
    """
    shelves = frozenset(shelf_names())
    index: dict[str, set[str]] = {name: set() for name in shelves}
    scanned = _scan(_SRC.rglob("*.py"), shelves)
    for stem, found in scanned.items():
        role = _WRITER_ROLES.get(stem)
        if not role:
            continue
        for name in found:
            index[name].add(role)
    return index


@lru_cache(maxsize=1)
def _real_attributes() -> frozenset[str]:
    """Every attribute a profile object legitimately has: shelves, properties, methods.

    ``dir()`` alone is not enough: a dataclass field declared with
    ``default_factory`` never becomes a class attribute, so ``cv_data`` and
    ``preferences`` are invisible to it. Reading ``dataclasses.fields`` too is
    what stops the detector calling the two most-used fields in the codebase
    phantoms.
    """
    from src.services.profile.models import CVData, UserPreferences, UserProfile

    names: set[str] = set(shelf_names())
    for cls in (CVData, UserPreferences, UserProfile):
        names.update(n for n in dir(cls) if not n.startswith("__"))
        names.update(f.name for f in dataclasses.fields(cls))
    return frozenset(names)


class _PhantomVisitor(ast.NodeVisitor):
    """Find getattr-style reads of a profile field, whatever the name.

    Deliberately IGNORES plain ``cv.something`` attribute access. A wrong name
    there raises AttributeError the first time it runs — loud, immediate, and
    impossible to ship. Only ``getattr(cv, "name", default)`` can be wrong and
    stay silent forever, so only those shapes can hide a phantom.
    """

    def __init__(self) -> None:
        self.found: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and _is_profile_receiver(node.args[0])
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            self.found.add(node.args[1].value)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.found.update(_loop_field_literals(node))
        self.generic_visit(node)


def phantom_reads() -> dict[str, set[str]]:
    """Code that reads a profile field WHICH DOES NOT EXIST.

    ``embeddings.py`` looped over ``("summary", "experience_text",
    "linkedin_summary")`` and called ``getattr(cv, field, "")`` on each. There
    has never been a ``linkedin_summary`` on ``CVData``, so that read returned
    the empty-string default on every profile, forever — a silent hole in the
    semantic engine that no test could catch, because ``getattr`` with a default
    cannot fail.

    That is a whole BUG CLASS, not one typo: any refactor that renames a shelf
    leaves every string-literal read behind, still running, still silent. This
    finds them.

    Returns ``{module_stem: {phantom names}}``; empty dict means clean.
    """
    real = _real_attributes()
    out: dict[str, set[str]] = {}
    for path in _SRC.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        visitor = _PhantomVisitor()
        visitor.visit(tree)
        # A field name here is always lower-case snake_case. This drops the
        # display labels that share a tuple with the field name, e.g. the
        # "Skills (CV)" and "Education" in ``("Education", "education")``.
        ghosts = {
            n for n in visitor.found
            if n.isidentifier() and n.islower() and not n.startswith("_") and n not in real
        }
        if ghosts:
            out[path.stem] = ghosts
    return out


def readers(shelf: str) -> set[str]:
    """Roles that read this shelf. Empty set = nothing consumes it."""
    return set(_reader_index().get(shelf, set()))


def writers(shelf: str) -> set[str]:
    """Roles that can fill this shelf. Empty set = nothing can ever fill it."""
    return set(_writer_index().get(shelf, set()))


def is_matching_shelf(shelf: str) -> bool:
    """True when at least one MATCHING consumer reads it (api/display alone is not)."""
    return bool(readers(shelf) & MATCHING_ROLES)


def matching_shelves() -> tuple[str, ...]:
    return tuple(n for n in shelf_names() if is_matching_shelf(n))


# Roles whose consumer ranks EVERY job in the feed, as opposed to a slice of it.
EVERY_JOB_ROLES = frozenset({"scorer", "search-keywords", "tiering", "prefilter"})


def matching_tiers() -> dict[str, tuple[str, ...]]:
    """The matching count broken down by REACH, because one number misleads.

    "50 shelves feed matching" is true as wiring and misleading as impact: it
    counts a shelf read only by the LLM judge the same as one the keyword scorer
    reads. They are not comparable —

        every_job  the keyword engine ranks all ~7,000 rows in a feed
        judge      the LLM judge sees a capped window (tens, not thousands)
        semantic   the vector, and only when the index actually covers the job

    This exists so the honest figure is COMPUTED rather than asserted in prose.
    A headline number that cannot be recomputed from the code is exactly the
    kind of claim this whole batch has been correcting.
    """
    every, judge, semantic = [], [], []
    for name in matching_shelves():
        roles = readers(name)
        if roles & EVERY_JOB_ROLES:
            every.append(name)
        elif "judge" in roles:
            judge.append(name)
        else:
            semantic.append(name)
    return {
        "every_job": tuple(every),
        "judge_only": tuple(judge),
        "semantic_only": tuple(semantic),
    }
