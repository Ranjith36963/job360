"""The `cv` recipe: the CV review scheduled run's rules live in Job360 (owner decision 2026-11-17), so the
owner's task prompt is three stable lines. Exact substring asserts on the closed lists and routing words."""
from __future__ import annotations

from src.api.routes.recipes import RECIPE_NAMES, load_recipe

LABEL_WORDS = ("Skills", "Technical", "Technologies", "Languages", "Frameworks", "Backend", "Frontend", "Data",
               "Engineering", "Cloud", "DevOps", "Databases", "Infrastructure", "Automation", "Platforms",
               "Analytics", "Programming", "APIs", "Machine learning", "Generative AI")


def _text() -> str:
    return " ".join(load_recipe("cv").text.split())


def test_cv_is_registered_and_indexed():
    assert "cv" in RECIPE_NAMES and RECIPE_NAMES[-1] == "rules"
    recipe = load_recipe("cv")
    assert recipe.name == "cv" and recipe.title
    assert len(recipe.text.splitlines()) <= 70 and "Print this checklist and tick each step:" in recipe.text


def test_cv_recipe_holds_every_allowed_label_word():
    text = _text()
    for word in LABEL_WORDS:
        assert word in text, word
    for refused in ('"Infra"', '"CI"', '"Systems"', '"Practice"'):
        assert refused in text, refused


def test_cv_recipe_holds_the_closed_tokens_and_routing_words():
    text = _text()
    for token in ("tries_left", "review.state", "strong = skip", "none", "weak", "cv_work", "never call `cv_work`",
                  "record_events", "cv_reviewed", "1-based", "evidence []", "10 NEW CVs", "in batches of 5",
                  "At most 2 re-reviews", "One rewrite per item per run", "costs no try",
                  '"LLM systems" names "LLMs"', '"prompt engineering" names "prompts"', '"Next.js" names "NextJs"',
                  "never add a qualifier", '"in evaluations"', '"3.2s → 0.8s"', "get_recipe(\"rules\")", "get_recipe(\"doctor\")",
                  "never review an unreviewed CV as it is", "written N; strong N; weak N"):
        assert token in text, token
