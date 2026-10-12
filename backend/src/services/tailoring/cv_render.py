"""Styled CV PDF renderer, driven by the user's ``cv_style`` dict.

Deterministic code only (Job360 has no LLM). The CV text the assistant writes is
parsed into blocks (name / headline / contact, section headings, entry lines with
an optional date, bullets, paragraphs) and drawn the way the user's own uploaded
CV looks: coloured headings with a rule, right-aligned dates, real bullet shapes,
clickable links, and the same page count as the uploaded template.

fpdf2 is lazy-imported (heavy-dep discipline, root rules #11/#16).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src.services.tailoring.pdf import _to_latin1
from src.utils.loop_guard import cpu_bound

# Built-in fallback so this module works even when the style dict is partial.
# Mirrors services/profile/cv_style.DEFAULT_STYLE (the owner's 1-page CV).
_FALLBACK: dict[str, Any] = {
    "page": {"format": "A4", "margin_left": 15, "margin_right": 15, "margin_top": 14, "pages": 1},
    "font": {"family": "sans"},
    "name": {"size": 20, "bold": True, "color": "#1F3864", "align": "center", "upper": True},
    "headline": {"size": 11, "color": "#555555", "align": "center"},
    "contact": {"size": 9, "color": "#000000", "align": "center"},
    "heading": {"size": 11.5, "bold": True, "color": "#1F3864", "upper": True, "rule": True,
                "rule_color": "#1F3864"},
    "body": {"size": 9.5, "color": "#000000"},
    "entry": {"bold": True, "dates_align": "right"},
    "bullet": "•",
    "link_color": "#1F3864",
}

_PT = 0.3528  # mm per point
_MIN_BODY = 8.5
_MAX_STEPS = 8

_HEADING_RE = re.compile(r"^[A-Z][A-Z &/\-]{1,39}$")
_BULLET_RE = re.compile(r"^\s*(?:-|\*|•)\s+")
_MONTHS = (
    "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|"
    "june|july|august|september|october|november|december"
)
_LINK_RE = re.compile(
    r"https?://[^\s|]+"
    r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
    r"|(?:www\.)?(?:linkedin\.com|github\.com)/[^\s|]*"
    r"|[\w-]+\.github\.io[^\s|]*"
    r"|[\w-]+(?:\.[\w-]+)*\.(?:com|uk|io|dev|ai|net|org|co)(?:/[^\s|]*)?(?![\w@])"
)


def _merged(style: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Fill every missing key from the fallback (one level deep)."""
    style = style or {}
    out: dict[str, Any] = {}
    for key, default in _FALLBACK.items():
        val = style.get(key)
        if isinstance(default, dict):
            merged = dict(default)
            if isinstance(val, dict):
                merged.update({k: v for k, v in val.items() if v is not None})
            out[key] = merged
        else:
            out[key] = val if val else default
    return out


def _rgb(color: Any, fallback: tuple[int, int, int] = (0, 0, 0)) -> tuple[int, int, int]:
    s = str(color or "").lstrip("#")
    if len(s) == 6:
        try:
            return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        except ValueError:
            pass
    return fallback


def _is_date(seg: str) -> bool:
    """True if ``seg`` looks like a date range (months / years / Present)."""
    low = seg.lower().strip()
    if not low or not re.search(r"\d{4}|present|current|\bnow\b", low):
        return False
    rest = re.sub(rf"\b(?:{_MONTHS}|present|current|now|to|until)\b\.?", " ", low)
    rest = re.sub(r"[\d\s\-/,.()']", "", rest)
    return rest == ""


# ── Parsing ──────────────────────────────────────────────────────────────────


_SECTION_WORDS = frozenset({
    "summary", "professional summary", "career summary", "profile", "experience",
    "professional experience", "work experience", "employment", "projects",
    "portfolio projects", "skills", "technical skills", "education", "certifications",
    "education & certification", "languages",
})
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_DOMAIN_RE = re.compile(
    r"https?://|www\.|linkedin\.|github\.|\.io\b|\.com\b|\.uk\b|\.dev\b|\.ai\b|\.net\b|\.org\b",
    re.IGNORECASE,
)
_MAX_HEADER = 4
_LONG_RUN_RE = re.compile(r"\S{200,}")


def _is_heading(s: str) -> bool:
    """ALL-CAPS heading, or a short Title Case / ALL CAPS known section word."""
    if _HEADING_RE.match(s):
        return True
    if len(s) > 40:
        return False
    key = s.lower().rstrip(":").strip()
    return key in _SECTION_WORDS and (s.istitle() or s.isupper())


def _is_contact(s: str) -> bool:
    """True if ``s`` looks like a contact line (email, phone, link, short ' | ' parts)."""
    if _BULLET_RE.match(s) or _is_heading(s):
        return False
    if "@" in s or _DOMAIN_RE.search(s):
        return True
    m = _PHONE_RE.search(s)
    if m and sum(c.isdigit() for c in m.group(0)) >= 9:
        return True
    parts = [p.strip() for p in s.split(" | ") if p.strip()]
    return len(parts) >= 2 and all(len(p) <= 40 for p in parts)


def _parse(text: str) -> tuple[list[str], list[tuple[str, Any]]]:
    """Return (header_lines, blocks). Block kinds: heading, entry, bullet, para, gap."""
    lines = _to_latin1(text or "").replace("\r", "").split("\n")
    header: list[str] = []
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines) and lines[i].strip() and len(header) < _MAX_HEADER:
        s = lines[i].strip()
        if header:
            if len(header) == 1:  # optional headline
                if _is_heading(s) or _BULLET_RE.match(s) or len(s) > 100:
                    break
            elif not _is_contact(s):
                break
        header.append(s)
        i += 1
    blocks: list[tuple[str, Any]] = []
    for raw in lines[i:]:
        # An unbroken run over 200 chars is junk: cut it so wrapping stays linear.
        line = _LONG_RUN_RE.sub(lambda m: m.group(0)[:200] + "...", raw.rstrip())
        s = line.strip()
        if not s:
            blocks.append(("gap", None))
        elif _is_heading(s):
            blocks.append(("heading", s))
        elif _BULLET_RE.match(line):
            blocks.append(("bullet", _BULLET_RE.sub("", line, count=1).strip()))
        elif " | " in s:
            parts = [p.strip() for p in s.split(" | ") if p.strip()]
            if len(parts) > 1 and _is_date(parts[-1]):
                blocks.append(("entry", (" | ".join(parts[:-1]), parts[-1])))
            else:
                blocks.append(("entry", (" | ".join(parts), "")))
        else:
            blocks.append(("para", s))
    return header, blocks


_MAX_LINK_TOKEN = 256


def _linkify(line: str) -> list[tuple[str, Optional[str]]]:
    """Split a contact line into (word, url-or-None) pieces.

    The link regex only runs on short whitespace-free tokens: it is quadratic on a
    long unbroken run, so anything over 256 chars is plain text.
    """
    pieces: list[tuple[str, Optional[str]]] = []
    for tok in line.split():
        if len(tok) > _MAX_LINK_TOKEN:
            pieces.append((tok, None))
        else:
            pieces.extend(_linkify_token(tok))
    return pieces


def _clip(pdf: Any, txt: str, w: float) -> str:
    """Cut ``txt`` (with an ellipsis) so it is no wider than ``w`` mm."""
    if len(txt) > 600:  # no page-wide line holds more; keeps width maths cheap
        txt = txt[:600]
    if pdf.get_string_width(txt) <= w:
        return txt
    ell = "..."
    avail = w - pdf.get_string_width(ell)
    lo, hi = 0, len(txt)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if pdf.get_string_width(txt[:mid]) <= avail:
            lo = mid
        else:
            hi = mid - 1
    return txt[:lo] + ell


def _linkify_token(line: str) -> list[tuple[str, Optional[str]]]:
    """Split one whitespace-free token into (word, url-or-None) pieces."""
    pieces: list[tuple[str, Optional[str]]] = []
    pos = 0

    def add_plain(chunk: str) -> None:
        for w in chunk.split():
            pieces.append((w, None))

    for m in _LINK_RE.finditer(line):
        tok = m.group(0)
        stripped = tok.rstrip(".,;:)")
        add_plain(line[pos:m.start()])
        if "@" in stripped and not stripped.lower().startswith("http"):
            url = "mailto:" + stripped
        elif stripped.lower().startswith("http"):
            url = stripped
        else:
            url = "https://" + stripped
        pieces.append((stripped, url))
        pos = m.start() + len(stripped)
    add_plain(line[pos:])
    return pieces


# ── Drawing ──────────────────────────────────────────────────────────────────


def _render_once(text: str, st: dict[str, Any], step: int) -> tuple[bytes, int]:
    from fpdf import FPDF  # lazy import
    from fpdf.enums import XPos, YPos

    d = 0.25 * step  # pt shrink per step
    g = max(0.5, 1.0 - 0.07 * step)  # gap factor
    page = st["page"]
    fmt = "Letter" if str(page.get("format")) == "Letter" else "A4"
    ml = max(min(float(page["margin_left"]), 10.0), float(page["margin_left"]) - 0.8 * step)
    mr = max(min(float(page["margin_right"]), 10.0), float(page["margin_right"]) - 0.8 * step)
    mt = max(min(float(page["margin_top"]), 10.0), float(page["margin_top"]) - 0.8 * step)
    mb = 12.0

    fam = "Times" if st["font"]["family"] == "serif" else "Helvetica"
    body_pt = max(_MIN_BODY, float(st["body"]["size"]) - d)

    def sz(v: Any, floor: float) -> float:
        return max(floor, float(v) - d)

    pdf = FPDF(format=fmt)
    pdf.set_auto_page_break(auto=False)
    pdf.set_margins(left=ml, top=mt, right=mr)
    pdf.add_page()
    tw = pdf.w - ml - mr
    right_edge = pdf.w - mr
    limit = pdf.h - mb

    def font(style: str, size: float, color: Any) -> None:
        pdf.set_font(fam, style=style, size=size)
        pdf.set_text_color(*_rgb(color))

    def lh(size: float, k: float = 1.32) -> float:
        return size * _PT * k

    def ensure(h: float) -> None:
        if pdf.get_y() + h > limit:
            pdf.add_page()

    def nlines(w: float, h: float, txt: str) -> int:
        return max(1, len(pdf.multi_cell(w, h, txt, dry_run=True, output="LINES")))

    def flow(w: float, h: float, txt: str, **kw: Any) -> None:
        """multi_cell that splits across pages when the block is taller than a page."""
        total = nlines(w, h, txt) * h
        if total > limit - mt:
            ensure(h)
            pdf.set_auto_page_break(auto=True, margin=mb)
            try:
                pdf.multi_cell(w, h, txt, new_x=XPos.LMARGIN, new_y=YPos.NEXT, **kw)
            finally:
                pdf.set_auto_page_break(auto=False)
        else:
            ensure(total)
            pdf.multi_cell(w, h, txt, new_x=XPos.LMARGIN, new_y=YPos.NEXT, **kw)

    def line_cell(txt: str, size: float, style: str, color: Any, align: str, upper: bool) -> None:
        txt = _to_latin1(txt.upper()) if upper else txt
        txt = txt[:600]  # a longer line can never fit; keeps the shrink loop cheap
        s = size
        font(style, s, color)
        while pdf.get_string_width(txt) > tw and s > 9:
            s -= 0.5
            font(style, s, color)
        txt = _clip(pdf, txt, tw)
        h = lh(s)
        ensure(h)
        pdf.set_x(ml)
        pdf.cell(tw, h, txt, align="C" if align == "center" else "L",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    header, blocks = _parse(text)

    # Header: name, headline, contact lines.
    nm, hl, ct = st["name"], st["headline"], st["contact"]
    link_rgb = _rgb(st["link_color"])
    for idx, hline in enumerate(header):
        if idx == 0:
            line_cell(hline, sz(nm["size"], 14), "B" if nm["bold"] else "", nm["color"],
                      nm["align"], bool(nm["upper"]))
            pdf.ln(0.8 * g)
        elif idx == 1:
            line_cell(hline, sz(hl["size"], 8), "", hl["color"], hl["align"], False)
        else:
            _contact_line(pdf, hline, sz(ct["size"], 8), fam, ct, link_rgb, ml, tw, lh, ensure)
    pdf.ln(2.2 * g)

    hd, en = st["heading"], st["entry"]
    entry_style = "B" if en["bold"] else ""
    bullet = str(st["bullet"])
    first_in_section = False
    prev_gap = False

    for kind, val in blocks:
        if kind == "gap":
            prev_gap = True
            continue
        if kind == "heading":
            hs = sz(hd["size"], 9)
            h = lh(hs)
            ensure(h + 3 * g + lh(body_pt) * 2)
            pdf.ln(2.6 * g)
            font("B" if hd["bold"] else "", hs, hd["color"])
            pdf.set_x(ml)
            pdf.cell(tw, h, _to_latin1(val.upper()) if hd["upper"] else val, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            if hd["rule"]:
                y = pdf.get_y() + 0.2
                pdf.set_draw_color(*_rgb(hd["rule_color"]))
                pdf.set_line_width(0.4 * _PT)
                pdf.line(ml, y, right_edge, y)
                pdf.set_y(y + 1.0 * g)
            first_in_section = True
            prev_gap = False
            continue
        if kind == "entry":
            left, date = val
            h = lh(body_pt)
            if not first_in_section:
                pdf.ln(1.3 * g)
            first_in_section = False
            font("", body_pt, st["body"]["color"])
            dw = pdf.get_string_width(date) + 1.0 if date else 0.0
            font(entry_style, body_pt, st["body"]["color"])
            if date and en["dates_align"] == "right" and dw <= tw / 2:
                lw = max(20.0, tw - dw - 2.0)
                ensure(nlines(lw, h, left) * h)
                y0 = pdf.get_y()
                font("", body_pt, st["body"]["color"])
                pdf.set_xy(right_edge - dw, y0)
                pdf.cell(dw, h, date, align="R")
                font(entry_style, body_pt, st["body"]["color"])
                pdf.set_xy(ml, y0)
                flow(lw, h, left)
            else:
                full = f"{left} | {date}" if date else left
                pdf.set_x(ml)
                flow(tw, h, full)
            prev_gap = False
            continue
        if kind == "bullet":
            h = lh(body_pt, 1.28)
            indent = 4.6
            font("", body_pt, st["body"]["color"])
            bw = tw - indent
            ensure(min(nlines(bw, h, val) * h, limit - mt))
            y0 = pdf.get_y()
            cy = y0 + h / 2
            pdf.set_fill_color(*_rgb(st["body"]["color"]))
            pdf.set_draw_color(*_rgb(st["body"]["color"]))
            bx = ml + 1.4
            if bullet == "-":
                pdf.rect(bx, cy - 0.15, 1.5, 0.3, style="F")
            elif bullet == "▪":
                pdf.rect(bx, cy - 0.5, 1.0, 1.0, style="F")
            else:
                pdf.ellipse(bx, cy - 0.55, 1.1, 1.1, style="F")
            pdf.set_xy(ml + indent, y0)
            flow(bw, h, val)
            first_in_section = False
            prev_gap = False
            continue
        # paragraph
        h = lh(body_pt, 1.3)
        font("", body_pt, st["body"]["color"])
        if prev_gap and not first_in_section:
            pdf.ln(1.0 * g)
        pdf.set_x(ml)
        flow(tw, h, val, align="L")
        first_in_section = False
        prev_gap = False

    pages = pdf.page
    return bytes(pdf.output()), pages


def _contact_line(
    pdf: Any, line: str, size: float, fam: str, ct: dict[str, Any],
    link_rgb: tuple[int, int, int], ml: float, tw: float, lh: Any, ensure: Any,
) -> None:
    """Draw one contact line (wrapping at word boundaries) with clickable links."""
    pdf.set_font(fam, size=size)
    sp = pdf.get_string_width(" ")
    words = _linkify(line)
    rows: list[list[tuple[str, Optional[str], float]]] = [[]]
    used = 0.0
    for w, url in words:
        w = _clip(pdf, w, tw)  # a token wider than the text area is cut, never off-page
        ww = pdf.get_string_width(w)
        add = ww + (sp if rows[-1] else 0)
        if rows[-1] and used + add > tw:
            rows.append([])
            used = 0.0
            add = ww
        rows[-1].append((w, url, ww))
        used += add
    h = lh(size)
    for row in rows:
        if not row:
            continue
        # One text run per colour; plain words are joined with real spaces.
        segs: list[list[Any]] = []  # [text, url, word_width]
        for w, url, ww in row:
            if url or not segs or segs[-1][1]:
                segs.append([w, url, ww])
            else:
                segs[-1][0] += " " + w
        for seg in segs[:-1]:
            seg[0] += " "
        widths = [pdf.get_string_width(seg[0]) for seg in segs]
        total = sum(widths)
        ensure(h)
        x = max(ml, ml + (tw - total) / 2) if ct["align"] == "center" else ml
        y = pdf.get_y()
        for seg, sw in zip(segs, widths):
            pdf.set_xy(x, y)
            if seg[1]:
                pdf.set_text_color(*link_rgb)
            else:
                pdf.set_text_color(*_rgb(ct["color"]))
            pdf.cell(sw, h, seg[0])
            if seg[1]:
                pdf.link(x, y, seg[2], h, seg[1])
            x += sw
        pdf.set_y(y + h)


@cpu_bound
def render_cv_pdf(text: str, style: Optional[dict[str, Any]] = None) -> bytes:
    """Render CV ``text`` as a styled PDF (bytes) following the ``cv_style`` dict.

    Fits ``style.page.pages``: if the first render overflows, retries with slightly
    smaller type, gaps and margins (up to 8 steps, body never below 8.5pt) and keeps
    the smallest render if it still overflows. Fewer pages than the target is fine.
    """
    st = _merged(style)
    try:
        target = max(1, int(st["page"].get("pages") or 1))
    except (TypeError, ValueError):
        target = 1
    out, pages = _render_once(text, st, 0)
    step = 0
    while pages > target and step < _MAX_STEPS:
        step += 1
        out, pages = _render_once(text, st, step)
    return out


# ── Callers' one-stop helpers ────────────────────────────────────────────────


def style_for_profile(profile: Any) -> dict[str, Any]:
    """The normalised ``cv_style`` of a loaded profile (``{}`` -> DEFAULT)."""
    raw = getattr(getattr(profile, "cv_data", None), "cv_style", None)
    raw = raw if isinstance(raw, dict) else {}
    try:
        from src.services.profile.cv_style import normalize_style  # noqa: PLC0415

        return normalize_style(raw)
    except ImportError:
        return _merged(raw)


async def load_user_style(user_id: str) -> dict[str, Any]:
    """Load the user's profile and return its normalised CV style (never raises)."""
    import asyncio  # noqa: PLC0415

    try:
        from src.services.profile.storage import load_profile  # noqa: PLC0415

        # load_profile is a SYNC database read - keep it off the event loop.
        return style_for_profile(await asyncio.to_thread(load_profile, user_id))
    except Exception:  # noqa: BLE001 - a styling lookup must never break a download
        return _merged({})


def render_letter_pdf(text: str, style: Optional[dict[str, Any]], title: str = "") -> bytes:
    """Cover letter PDF: plain letter layout in the CV's font family / size / colour."""
    from src.services.tailoring.pdf import render_pdf  # noqa: PLC0415

    st = _merged(style)
    return render_pdf(
        text,
        title=title,
        font_family="Times" if st["font"]["family"] == "serif" else "Helvetica",
        body_size=max(10.0, float(st["body"]["size"])),
        color=_rgb(st["body"]["color"]),
    )


async def render_document_pdf(user_id: str, kind: str, text: str) -> bytes:
    """Render a stored CV (styled) or cover letter (no title) as PDF, off the event loop."""
    import asyncio  # noqa: PLC0415

    style = await load_user_style(user_id)
    if kind == "cv":
        return await asyncio.to_thread(render_cv_pdf, text, style)
    return await asyncio.to_thread(render_letter_pdf, text, style, "")
