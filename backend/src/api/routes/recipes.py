"""Recipes — the `run 360` playbooks a connected assistant follows.

Owner plan (2026-10-01): one command sets up the whole hunt inside the user's
own assistant. A recipe is plain text the ASSISTANT reads and acts on — Job360
runs nothing itself (decision 28: no LLM, no agent loop here). The text lives
in ``src/recipes/<name>.md`` so changing a recipe is editing a file, not code.

Served three ways from this one module: these routes (the web Connect page),
the ``get_recipe`` MCP tool (calls the route function, like every tool), and
MCP prompts (``360-<name>``) for clients that show prompts as commands.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.auth_deps import CurrentUser, require_user
from src.services.applications.authorship import actor_for
from src.utils.logger import get_audit_logger, safe_log_value

router = APIRouter(tags=["recipes"])

RECIPES_DIR = Path(__file__).resolve().parents[2] / "recipes"

# Order is the order a new user runs them in. Adding a recipe = add the file
# AND a row here; tests/test_recipes.py pins that the two agree.
RECIPE_NAMES: tuple[str, ...] = (
    "setup", "hunt", "research", "apply", "reach", "daily", "prep", "review", "rules",
)


class RecipeSummary(BaseModel):
    name: str
    title: str


class Recipe(BaseModel):
    name: str
    title: str
    text: str


def _log_read(user: CurrentUser, recipe: str, result: str) -> None:
    """One audit line per recipe read: who, which recipe, when (the log's own
    timestamp). Never a personal value - only the closed recipe name or "list"."""
    get_audit_logger().info(
        "recipe_read",
        extra={
            "event": "recipe_read", "user_id": safe_log_value(user.id),
            "actor": safe_log_value(actor_for(user)), "recipe": safe_log_value(recipe, max_len=40),
            "result": result,
        },
    )


@cache
def load_recipe(name: str) -> Recipe:
    """Read one recipe file. Its first line (`# ...`) is the title."""
    text = (RECIPES_DIR / f"{name}.md").read_text(encoding="utf-8")
    first = text.splitlines()[0] if text else ""
    return Recipe(name=name, title=first.lstrip("# ").strip(), text=text)


@router.get("/recipes", response_model=list[RecipeSummary])
async def list_recipes(user: CurrentUser = Depends(require_user)) -> list[RecipeSummary]:
    """Every recipe, in the order a new user runs them."""
    _log_read(user, "list", "ok")
    return [RecipeSummary(name=n, title=load_recipe(n).title) for n in RECIPE_NAMES]


@router.get("/recipes/{name}", response_model=Recipe)
async def get_recipe(name: str, user: CurrentUser = Depends(require_user)) -> Recipe:
    """One recipe's full text. Only names in ``RECIPE_NAMES`` resolve — the
    name never reaches the filesystem otherwise (no path traversal)."""
    if name not in RECIPE_NAMES:
        _log_read(user, name, "not_found")
        raise HTTPException(status_code=404, detail=f"No recipe {name!r}. Recipes: {', '.join(RECIPE_NAMES)}")
    _log_read(user, name, "ok")
    return load_recipe(name)
