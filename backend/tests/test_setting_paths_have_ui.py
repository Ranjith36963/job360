"""Every assistant setting the backend lets a user own needs a switch in the web app.

2026-11-09: ``ask_before_signed_in_submit`` shipped with no control and no request label, so the owner was
told to switch it off on Connect your assistant -> Permissions and could not. This pins every path in
``SETTING_PATHS`` to a literal in ``frontend/src/lib/assistant-tab.ts`` (the control's path constant) and in
``SettingRequestCard.tsx`` (the label of an assistant's request). A new setting needs both, or an entry in
the explicit allowlists below with the reason.
"""
import re
from pathlib import Path

from src.services.profile import assistant_settings

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
LIB = "".join((FRONTEND / "lib" / f).read_text(encoding="utf-8") for f in ("assistant-tab.ts", "assistant-state.ts"))
# Component code with comments stripped: a control must be REFERENCED here, not just declared in lib.
COMPONENTS = "\n".join(
    re.sub(r"//[^\n]*|/\*.*?\*/", "", f.read_text(encoding="utf-8"), flags=re.S)
    for f in (FRONTEND / "components").rglob("*.tsx")
    if ".test." not in f.name and f.name != "SettingRequestCard.tsx"  # the label file is not a control
)
CARD = (FRONTEND / "components" / "needs-you" / "SettingRequestCard.tsx").read_text(encoding="utf-8")


def _constants(path: str) -> list[str]:
    """Names of the exported string constants in lib that hold this path."""
    return re.findall(rf'export const (\w+)\s*=\s*"{re.escape(path)}"', LIB)


def _has_control(path: str) -> bool:
    key = path.split(".")[-1]
    if f'"{path}"' in COMPONENTS or f'"{key}"' in COMPONENTS or f"'{key}'" in COMPONENTS:
        return True
    names = _constants(path)
    if any(re.search(rf"\b{n}\b", COMPONENTS) for n in names):
        return True
    # Declared inside an exported lib table a component renders (OUTREACH_LIMITS); the history/label lists are not controls.
    for table, body in re.findall(r"export const (\w+)\s*(?::[^=]+)?=\s*\[((?:(?!export const).)*?)\n\](?: as const)?;", LIB, flags=re.S):
        if table in {"SETTING_HISTORY_PATHS", "HISTORY_PATHS", "PREF_HISTORY_PATHS"}:
            continue
        if any(re.search(rf"\b{n}\b", body) for n in names) and re.search(rf"\b{table}\b", COMPONENTS):
            return True
    return False


# path -> why it has no entry in that file. Keep short; an empty reason is a bug.
NO_TAB_CONTROL: dict[str, str] = {
    "assistant_settings.above_line": "set by the mode pick in BatchRuleRows.tsx through lib batch-rule edits, not by path",
    "assistant_settings.paused_until": "written by the pause hook in assistant-state.ts (Pause button, PauseUntil)",
    "assistant_settings.pause_reason": "written by the pause hook in assistant-state.ts with the Pause button",
}
NO_REQUEST_LABEL: dict[str, str] = {
    "assistant_settings.pause_reason": "a note on the pause; the paused_until card carries the change",
    "assistant_settings.pc_on_hours": "never riskier, always applies at once: no request is raised",
    # Known gap (2026-11-09): a request to raise one of these shows the generic "A setting" card, never a raw path.
    "assistant_settings.outreach_per_company": "generic fallback card; add a label when touched",
    "assistant_settings.network_per_hour": "generic fallback card; add a label when touched",
    "assistant_settings.linkedin_sends_per_day": "generic fallback card; add a label when touched",
    "assistant_settings.email_sends_per_day": "generic fallback card; add a label when touched",
}


def _missing(source: str, allow: dict[str, str]) -> list[str]:
    return [p for p in assistant_settings.SETTING_PATHS if f'"{p}"' not in source and p not in allow]


def _missing_control() -> list[str]:
    return [p for p in assistant_settings.SETTING_PATHS if not _has_control(p) and p not in NO_TAB_CONTROL]


def test_every_setting_path_has_a_web_control():
    assert _missing_control() == [], "add a switch in a component that uses the path constant (assistant-tab.ts)"


def test_every_setting_path_has_a_request_label():
    assert _missing(CARD, NO_REQUEST_LABEL) == [], "add a case in SettingRequestCard.tsx describeSettingRequest"
