"""The frontend label test reads frontend/src/lib/event-types.json. This pins that
file to the backend tuples, so a new event type cannot ship without a label."""
import json
from pathlib import Path

from src.core import settings

JSON_PATH = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "event-types.json"


def test_event_types_json_matches_settings():
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    assert data == {
        "status": list(settings.APPLICATION_STATUS_EVENT_TYPES),
        "note": list(settings.APPLICATION_NOTE_EVENT_TYPES),
    }, "regenerate frontend/src/lib/event-types.json from core/settings.py and add labels in event-labels.ts"
