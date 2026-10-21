"""Capture the visual style of the user's uploaded CV PDF.

Product rule: Job360 has NO LLM. The style fingerprint is read deterministically
from the PDF's own characters and drawn lines (pdfplumber), so every generated
CV can follow the design of the CV the user uploaded. No uploaded PDF (or an
unreadable one) means the built-in ``DEFAULT_STYLE``.

The ``cv_style`` dict is JSON-safe and EVERY key is always present after
``normalize_style``. Heavy deps (pdfplumber) are imported lazily.
"""

from __future__ import annotations

import copy
import logging
import re
from collections import Counter
from typing import Any, Optional

logger = logging.getLogger("job360.profile.cv_style")

NAVY = "#1F3864"

DEFAULT_STYLE: dict[str, Any] = {
    "version": 1,
    "source": "default",
    "page": {"format": "A4", "margin_left": 15.0, "margin_right": 15.0, "margin_top": 15.0, "pages": 1},
    "font": {"family": "sans"},
    "name": {"size": 20.0, "bold": True, "color": NAVY, "align": "center", "upper": True},
    "headline": {"size": 11.0, "color": "#555555", "align": "center"},
    "contact": {"size": 9.0, "color": "#000000", "align": "center"},
    "heading": {
        "size": 11.5,
        "bold": True,
        "color": NAVY,
        "upper": True,
        "rule": True,
        "rule_color": NAVY,
    },
    "body": {"size": 9.5, "color": "#000000"},
    "entry": {"bold": True, "first_bold": True, "dates_align": "right"},
    "skills": {"label_bold": True},
    "project_name_color": NAVY,  # "" = project names in the body colour
    "bullet": "•",
    "link_color": NAVY,
}

# (min, max) clamps in points / millimetres.
_SIZE_RANGES: dict[str, tuple[float, float]] = {
    "name": (14.0, 28.0),
    "headline": (8.0, 13.0),
    "contact": (8.0, 13.0),
    "heading": (9.0, 16.0),
    "body": (8.5, 12.0),
}
_MARGIN_RANGE = (8.0, 30.0)
_MAX_PAGES = 4
_BULLETS = ("•", "-", "▪")
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _color(value: Any, fallback: str) -> str:
    """Return ``value`` as ``#RRGGBB`` (upper-case) or ``fallback`` when invalid."""
    if isinstance(value, str) and _HEX_RE.match(value):
        return value.upper()
    return fallback


def _num(value: Any, lo: float, hi: float, fallback: float) -> float:
    """Clamp ``value`` into ``[lo, hi]`` (rounded to 0.1); ``fallback`` if not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        value = fallback
    return round(max(lo, min(hi, float(value))), 1)


def _enum(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    return value if isinstance(value, str) and value in allowed else fallback


def _flag(value: Any, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def _section(d: dict[str, Any], key: str) -> dict[str, Any]:
    sub = d.get(key)
    return sub if isinstance(sub, dict) else {}


def normalize_style(d: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Fill missing keys from ``DEFAULT_STYLE``, clamp numbers, validate colours/enums.

    Always returns a fresh, fully-populated dict; never raises on odd input.
    """
    d = d if isinstance(d, dict) else {}
    base = copy.deepcopy(DEFAULT_STYLE)
    out = copy.deepcopy(base)

    out["source"] = _enum(d.get("source"), ("uploaded", "default"), "default")

    page = _section(d, "page")
    out["page"] = {
        "format": _enum(page.get("format"), ("A4", "Letter"), base["page"]["format"]),
        "margin_left": _num(page.get("margin_left"), *_MARGIN_RANGE, base["page"]["margin_left"]),
        "margin_right": _num(page.get("margin_right"), *_MARGIN_RANGE, base["page"]["margin_right"]),
        "margin_top": _num(page.get("margin_top"), *_MARGIN_RANGE, base["page"]["margin_top"]),
        "pages": int(_num(page.get("pages"), 1, _MAX_PAGES, base["page"]["pages"])),
    }
    out["font"] = {
        "family": _enum(_section(d, "font").get("family"), ("sans", "serif"), base["font"]["family"])
    }

    n = _section(d, "name")
    out["name"] = {
        "size": _num(n.get("size"), *_SIZE_RANGES["name"], base["name"]["size"]),
        "bold": _flag(n.get("bold"), base["name"]["bold"]),
        "color": _color(n.get("color"), base["name"]["color"]),
        "align": _enum(n.get("align"), ("center", "left"), base["name"]["align"]),
        "upper": _flag(n.get("upper"), base["name"]["upper"]),
    }
    for key in ("headline", "contact"):
        s = _section(d, key)
        out[key] = {
            "size": _num(s.get("size"), *_SIZE_RANGES[key], base[key]["size"]),
            "color": _color(s.get("color"), base[key]["color"]),
            "align": _enum(s.get("align"), ("center", "left"), base[key]["align"]),
        }
    h = _section(d, "heading")
    out["heading"] = {
        "size": _num(h.get("size"), *_SIZE_RANGES["heading"], base["heading"]["size"]),
        "bold": _flag(h.get("bold"), base["heading"]["bold"]),
        "color": _color(h.get("color"), base["heading"]["color"]),
        "upper": _flag(h.get("upper"), base["heading"]["upper"]),
        "rule": _flag(h.get("rule"), base["heading"]["rule"]),
        "rule_color": _color(h.get("rule_color"), base["heading"]["rule_color"]),
    }
    b = _section(d, "body")
    out["body"] = {
        "size": _num(b.get("size"), *_SIZE_RANGES["body"], base["body"]["size"]),
        "color": _color(b.get("color"), base["body"]["color"]),
    }
    e = _section(d, "entry")
    out["entry"] = {
        "bold": _flag(e.get("bold"), base["entry"]["bold"]),
        "first_bold": _flag(e.get("first_bold"), base["entry"]["first_bold"]),
        "dates_align": _enum(e.get("dates_align"), ("right", "inline"), base["entry"]["dates_align"]),
    }
    out["skills"] = {"label_bold": _flag(_section(d, "skills").get("label_bold"), True)}
    pnc = d.get("project_name_color")
    out["project_name_color"] = "" if pnc == "" else _color(pnc, out["heading"]["color"])
    out["bullet"] = _enum(d.get("bullet"), _BULLETS, base["bullet"])
    out["link_color"] = _color(d.get("link_color"), base["link_color"])
    return out


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_END_RE = re.compile(
    rf"(?:(?:{_MONTHS}\s+)?(?:19|20)\d{{2}}|present|current|now)\s*$", re.IGNORECASE
)
_CP1252_BULLET = chr(0x95)  # "•" as cp1252 core-font PDFs encode it
_BULLET_GLYPHS = "•▪◦-▫■" + _CP1252_BULLET
_LINK_HINT = ("linkedin.", "github.", "http")
_SERIF_HINT = ("times", "serif", "georgia", "garamond", "cambria")
_PT_TO_MM = 25.4 / 72.0


def _to_hex(color: Any) -> str:
    """Convert a pdfplumber colour (gray float, RGB, CMYK, None) to ``#RRGGBB``."""
    try:
        if color is None:
            return "#000000"
        if isinstance(color, (int, float)):
            color = (color,)
        vals = [float(v) for v in color]
        if len(vals) == 1:
            r = g = b = vals[0]
        elif len(vals) == 3:
            r, g, b = vals
        elif len(vals) == 4:
            c, m, y, k = vals
            r, g, b = (1 - min(1.0, c + k)), (1 - min(1.0, m + k)), (1 - min(1.0, y + k))
        else:
            return "#000000"
        r, g, b = (max(0.0, min(1.0, x)) for x in (r, g, b))
        return f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}"
    except (TypeError, ValueError):
        return "#000000"


def _is_bold(fontname: str) -> bool:
    low = (fontname or "").lower()
    return any(t in low for t in ("bold", "black", "semibold", "heavy"))


def _mode(items: list[Any], default: Any) -> Any:
    return Counter(items).most_common(1)[0][0] if items else default


class _Line:
    """One text line with its dominant size/colour/weight."""

    def __init__(self, raw: dict[str, Any], page_no: int) -> None:
        chars = [c for c in raw.get("chars", []) if (c.get("text") or "").strip()]
        self.page_no = page_no
        self.text: str = (raw.get("text") or "").strip()
        self.x0: float = float(raw["x0"])
        self.x1: float = float(raw["x1"])
        self.top: float = float(raw["top"])
        self.bottom: float = float(raw["bottom"])
        self.chars = chars
        self.size: float = round(_mode([round(float(c["size"]) * 2) / 2 for c in chars], 0.0), 1)
        self.color: str = _mode([_to_hex(c.get("non_stroking_color")) for c in chars], "#000000")
        self.bold: bool = _mode([_is_bold(c.get("fontname", "")) for c in chars], False)

    def max_gap(self) -> float:
        """Widest horizontal gap between neighbouring characters (right-aligned dates leave a big one)."""
        xs = sorted(self.chars, key=lambda c: c["x0"])
        gaps = [xs[i + 1]["x0"] - xs[i]["x1"] for i in range(len(xs) - 1)]
        return max(gaps) if gaps else 0.0


def _align(line: _Line, page_w: float, left: float, right: float) -> str:
    """``center`` when the line sits on the page centre and is not full-width."""
    text_w = max(right - left, 1.0)
    if (line.x1 - line.x0) > 0.85 * text_w:
        return "left"
    centre = (line.x0 + line.x1) / 2
    return "center" if abs(centre - page_w / 2) <= 0.08 * page_w else "left"


def _is_contact(text: str) -> bool:
    low = text.lower()
    return "@" in text or any(h in low for h in _LINK_HINT) or len(re.findall(r"\d", text)) >= 7


def _find_rule(page: Any, line: _Line, text_w: float) -> Optional[str]:
    """Colour of a thin horizontal line/rect just under ``line``, or None."""
    candidates: list[tuple[dict[str, Any], str]] = []
    for ln in getattr(page, "lines", None) or []:
        candidates.append((ln, "stroking_color"))
    for rc in getattr(page, "rects", None) or []:
        candidates.append((rc, "non_stroking_color"))
    for obj, key in candidates:
        try:
            height = abs(float(obj["bottom"]) - float(obj["top"]))
            width = float(obj["x1"]) - float(obj["x0"])
            gap = float(obj["top"]) - line.bottom
        except (KeyError, TypeError, ValueError):
            continue
        if height <= 2.0 and width > 0.4 * text_w and -3.0 <= gap <= 12.0:
            col = obj.get(key)
            if col is None:
                col = obj.get("non_stroking_color")
            return _to_hex(col)
    return None


def _split_chars(ln: _Line, sep: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Visible chars of ``ln`` before / after the first ``sep`` char (sep itself dropped)."""
    for i, c in enumerate(ln.chars):
        if c["text"] == sep:
            return ln.chars[:i], ln.chars[i + 1 :]
    return ln.chars, []


def _share_bold(chars: list[dict[str, Any]]) -> float:
    return sum(1 for c in chars if _is_bold(c.get("fontname", ""))) / len(chars) if chars else 0.0


def _entry_weight(lines: list[_Line], body_size: float) -> tuple[bool, bool]:
    """(entry.bold, entry.first_bold) from ``Company | Role`` lines.

    Bold first segment + regular rest -> (True, True); whole line bold -> (True, False);
    nothing bold -> (False, False). No entry lines at all -> the default (True, True).
    """
    votes: list[str] = []
    for ln in lines:
        # Headline / contact rows also hold "|" but are not body-sized: skip them.
        if "|" not in ln.text or abs(ln.size - body_size) > 0.3:
            continue
        head, rest = _split_chars(ln, "|")
        # Right-aligned dates are never bold; judge the role part only up to its last char.
        if not head or not rest:
            continue
        # The first glyph decides the first segment ("Job360 (job360.uk)" is only part-bold).
        first = _is_bold(head[0].get("fontname", ""))
        rest_bold = _share_bold(rest)
        if first and rest_bold < 0.5:
            votes.append("first")
        elif first and rest_bold >= 0.5:
            votes.append("all")
        elif _share_bold(head) < 0.5 and rest_bold < 0.5:
            votes.append("none")
    if not votes:
        return True, True
    top = _mode(votes, "first")
    return top != "none", top == "first"


def _section_styles(
    style: dict[str, Any], lines: list[_Line], heading: Optional[dict[str, Any]], body_color: str
) -> None:
    """Project-name colour and skills-label weight, read from the PROJECTS / SKILLS sections."""
    if not heading:
        return
    key = (heading["size"], heading["color"], heading["bold"])
    section = ""
    proj_cols: list[str] = []
    label_votes: list[bool] = []
    for ln in lines:
        if (ln.size, ln.color, ln.bold) == key and 2 <= len(ln.text) <= 40 and " | " not in ln.text:
            section = ln.text.lower()
            continue
        if "project" in section and "|" in ln.text and ln.text[0] not in _BULLET_GLYPHS:
            head, _ = _split_chars(ln, "|")
            if head:
                proj_cols.append(_mode([_to_hex(c.get("non_stroking_color")) for c in head], body_color))
        elif "skill" in section and ":" in ln.text[:31]:
            head, rest = _split_chars(ln, ":")
            if head and rest:
                label_votes.append(_share_bold(head) >= 0.5 and _share_bold(rest) < 0.5)
    if proj_cols:
        col = _mode(proj_cols, body_color)
        style["project_name_color"] = "" if col == body_color else col
    if label_votes:
        style["skills"] = {"label_bold": _mode(label_votes, True)}


def _extract(pdf_path: str) -> Optional[dict[str, Any]]:
    """Read the raw (un-normalised) style dict, or None when there is no text."""
    import pdfplumber  # lazy: heavy

    with pdfplumber.open(pdf_path) as pdf:
        n_pages = len(pdf.pages)
        if n_pages < 1:
            return None
        page_w = float(pdf.pages[0].width)
        lines: list[_Line] = []
        pages_lines: dict[int, list[_Line]] = {}
        all_chars: list[dict[str, Any]] = []
        for i, page in enumerate(pdf.pages[: _MAX_PAGES + 2]):
            raw_lines = page.extract_text_lines(return_chars=True) or []
            pl = [_Line(r, i) for r in raw_lines if (r.get("text") or "").strip()]
            pl = [ln for ln in pl if ln.chars]
            pages_lines[i] = pl
            lines.extend(pl)
            all_chars.extend(c for ln in pl for c in ln.chars)
        if not lines:
            return None

        # Page geometry from the first page.
        p1 = pages_lines.get(0) or lines
        p1_chars = [c for ln in p1 for c in ln.chars]
        left = min(c["x0"] for c in p1_chars)
        right = max(c["x1"] for c in p1_chars)
        top = min(c["top"] for c in p1_chars)
        text_w = max(right - left, 1.0)
        fmt = "Letter" if abs(page_w - 612) < abs(page_w - 595) else "A4"

        # Body = most common size+colour over every character.
        body_size, body_color = _mode(
            [(round(float(c["size"]) * 2) / 2, _to_hex(c.get("non_stroking_color"))) for c in all_chars],
            (9.5, "#000000"),
        )
        top_font = str(_mode([c.get("fontname", "") for c in all_chars], "")).lower()
        family = "serif" if any(h in top_font for h in _SERIF_HINT) else "sans"

        style: dict[str, Any] = {
            "source": "uploaded",
            "page": {
                "format": fmt,
                "margin_left": left * _PT_TO_MM,
                "margin_right": (page_w - right) * _PT_TO_MM,
                "margin_top": top * _PT_TO_MM,
                "pages": n_pages,
            },
            "font": {"family": family},
            "body": {"size": body_size, "color": body_color},
        }

        # Name = the largest-font line on page 1 (top-most on ties).
        name_line = max(p1, key=lambda ln: (ln.size, -ln.top))
        style["name"] = {
            "size": name_line.size,
            "bold": name_line.bold,
            "color": name_line.color,
            "align": _align(name_line, page_w, left, right),
            "upper": name_line.text.isupper(),
        }

        # Headline / contact = the next 1-3 lines under the name.
        under = [ln for ln in p1 if ln.top > name_line.top][:3]
        for ln in under:
            if ln.text.isupper() and len(ln.text) <= 40 and (ln.bold or ln.color != body_color):
                break  # reached the first section heading
            kind = "contact" if _is_contact(ln.text) else "headline"
            if kind not in style:
                style[kind] = {
                    "size": ln.size,
                    "color": ln.color,
                    "align": _align(ln, page_w, left, right),
                }

        # Headings = short, emphasised lines that repeat with the same look.
        def emphasised(ln: _Line) -> bool:
            return ln.bold or ln.color != body_color or ln.size > body_size + 0.4

        cands = [
            ln
            for ln in lines
            if ln is not name_line
            and 2 <= len(ln.text) <= 40
            and " | " not in ln.text
            and ln.text[0] not in _BULLET_GLYPHS
            and emphasised(ln)
        ]
        groups: dict[tuple[float, str, bool], list[_Line]] = {}
        for ln in cands:
            groups.setdefault((ln.size, ln.color, ln.bold), []).append(ln)
        repeating = {k: v for k, v in groups.items() if len(v) >= 2}
        if repeating:
            key = max(
                repeating,
                key=lambda k: (sum(1 for ln in repeating[k] if ln.text.isupper()), len(repeating[k])),
            )
            heads = repeating[key]
            ruled = [_find_rule(pdf.pages[ln.page_no], ln, text_w) for ln in heads]
            found = [c for c in ruled if c]
            style["heading"] = {
                "size": key[0],
                "bold": key[2],
                "color": key[1],
                "upper": sum(1 for ln in heads if ln.text.isupper()) * 2 >= len(heads),
                "rule": len(found) * 2 >= len(heads),
                "rule_color": _mode(found, key[1]),
            }

        # Entry lines: dates flush right on a line that starts at the left edge.
        right_dates = sum(
            1
            for ln in lines
            if _DATE_END_RE.search(ln.text)
            and ln.x1 >= right - 0.05 * text_w
            and ln.x0 <= left + 0.05 * text_w
            and ln.max_gap() > 2.5 * max(ln.size, 1.0)
        )
        entry_bold, first_bold = _entry_weight(lines, body_size)
        style["entry"] = {
            "bold": entry_bold,
            "first_bold": first_bold,
            "dates_align": "right" if right_dates >= 2 else "inline",
        }
        _section_styles(style, lines, style.get("heading"), body_color)

        # Bullet glyph most common at line starts.
        glyph = _mode([ln.text[0] for ln in lines if ln.text[0] in _BULLET_GLYPHS], "•")
        style["bullet"] = {"◦": "•", _CP1252_BULLET: "•", "▫": "▪", "■": "▪"}.get(glyph, glyph)

        # Link colour: chars of lines mentioning a link, if distinct from body.
        link_cols = [
            _to_hex(c.get("non_stroking_color"))
            for ln in lines
            if any(h in ln.text.lower() for h in _LINK_HINT)
            for c in ln.chars
        ]
        link = _mode(link_cols, body_color)
        heading_color = style.get("heading", {}).get("color", DEFAULT_STYLE["heading"]["color"])
        style["link_color"] = link if link != body_color else heading_color
        return style


def extract_cv_style(pdf_path: str) -> Optional[dict[str, Any]]:
    """Read the visual style of the PDF at ``pdf_path`` into a normalised ``cv_style``.

    Deterministic (pdfplumber only, no LLM). Returns ``None`` -- never raises --
    when the file is not a readable PDF, so the caller keeps the default style.
    """
    try:
        raw = _extract(pdf_path)
        return normalize_style(raw) if raw else None
    except Exception as exc:  # noqa: BLE001 - odd PDFs raise many pdfminer/plumber types
        logger.info("cv_style extraction skipped: %s", type(exc).__name__)
        return None
