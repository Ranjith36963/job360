"""RULES 8 (2026-11-15): #1610 fixed the skills-label false-weaks ("Infra:", "CI:", "Systems:"), but production apps
455, 460, 461 and 462 had each spent all MAX_WEAK tries on them under rules 7. A rules bump is the documented remedy:
weak reviews judged under older rules never count, so each of those apps gets one fresh budget; a STRONG review
stays valid (``review_valid`` never reads ``rules``), so approved CVs are untouched."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.services.applications import cv_review
from tests.test_cv_review import _save_cv, _setup, _stored
from tests.test_cv_review_held_lines import OWNER_SHAPES, _profile, _seed, _self_rows, _sha

OLD_RULES = 7  # the rules the four production apps' false-weak reviews were stored under


async def _store(db, user_id, app_id, art, text, confidence):
    from src.services.applications import kit, spine

    await spine.append_event(
        db, user_id=user_id, application_id=app_id, event_type=cv_review.EVENT_TYPE,
        payload={"artifact_id": art, "sha256": kit.sha256_text(text), "claimed": "strong", "confidence": confidence,
                 "downgraded": [] if confidence == "strong" else ["skills_label_unknown"], "lines": 30,
                 "content_lines": 30, "covered": 30, "reviewer": "fresh helper", "by": "token:claude-code",
                 "rules": OLD_RULES},
        occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by="token:claude-code")


@pytest.mark.asyncio
async def test_rules_seven_weak_reviews_get_a_fresh_budget_and_strong_stay_valid(
        authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps

    assert cv_review.RULES == 8
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        texts = [OWNER_SHAPES[351].replace("- Built", p) for p in ("- Built", "* Built", "▪ Built", "• Built")]
        app_id, cv_id = await _setup(client, cv=texts[0])
        ids = [cv_id] + [await _save_cv(client, app_id, t) for t in texts[1:3]]
        strong_text = OWNER_SHAPES[351].replace("- Built", "– Built")
        strong_id = await _save_cv(client, app_id, strong_text)
        db = await api_deps.get_db()
        for art, text in zip(ids, texts):  # three weak reviews under rules 7, each on its own CV version
            await _store(db, fixture_user_id, app_id, art, text, "weak")
        await _store(db, fixture_user_id, app_id, strong_id, strong_text, "strong")  # a strong one, rules 7
        v4_id = await _save_cv(client, app_id, texts[3])  # the newest CV, not yet reviewed
        view = (await client.get(f"/api/applications/{app_id}", params={"view": "review"})).json()["review"]
        assert view["artifact_id"] == v4_id
        assert (view["reviews_used"], view["tries_left"]) == (0, cv_review.MAX_WEAK), view
        got = await _stored(client, app_id, {"artifact_id": v4_id, "sha256": _sha(texts[3]), "confidence": "weak",
                                             "lines": _self_rows(texts[3], _profile()), "reviewer": "fresh helper"})
        assert "retry_limit" not in got["downgraded"], got
        assert got["rules"] == cv_review.RULES
        # the strong review stored under rules 7 still answers for that exact cv id + sha
        assert await cv_review.review_state(db, fixture_user_id, app_id, strong_id, _sha(strong_text)) == "strong"
