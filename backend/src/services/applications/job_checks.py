"""Job checks - deterministic FACTS about the one job the user's assistant brought.

Product rule 4: Job360 never searches, ranks, scores or recommends jobs and has
no LLM. These checks only report what the stored ad literally contains (word
count, link host, dates, a currency amount, the word "sponsor"). They are shown
on that job only and never used to sort or filter a list. Wording is facts, not
verdicts - nothing here says "ghost", "scam" or "risk".

Patterns adapted from open-source projects (MIT):
* word-count completeness bands - Gsync/jobsync src/lib/jobs/descriptionCompleteness.ts
* canonical URL key (host fold, tracking params dropped) - Gsync/jobsync src/lib/jobs/jobDedupe.ts
* official-ATS host match with look-alike defence - MadsLorentzen/ai-job-search
  .claude/commands/apply.md (exact apex or ``.<apex>`` suffix only)
* salary-present regex idea - speedyapply/JobSpy jobspy/util.py (presence only here)
* "facts, not verdicts" wording - career-ops-hq/career-ops company-history.mjs
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.core import settings

# Query keys that only track the click, never pick the job.
_TRACKING_KEYS = frozenset({"gclid", "fbclid", "ref", "trackingid", "refid", "msclkid", "mc_cid", "mc_eid"})

_CUR = r"(?:USD|EUR|GBP|CAD|AUD|CHF|INR|SGD|AED)"
# An amount of money. Every quantifier is bounded or single, so the scan is linear.
_MONEY_RE = re.compile(
    rf"(?:[$€£₹¥]\s?\d|\b{_CUR}\s?\d|\d\s?{_CUR}\b|\d\s?k\s?(?:/|per\s)?\s?(?:yr|year|annum)\b)",
    re.IGNORECASE,
)
# INR amounts that are pay by nature (a salary unit, not a company figure).
_PAY_UNIT_RE = re.compile(
    r"(?:\d[\d,.]{0,12}\s?(?:lpa|lakhs?)\b|₹\s?\d[\d,.]{0,12}\s?(?:l|lac|lakhs?)\b)",
    re.IGNORECASE,
)
# Pay-context words: a money amount counts as pay only with one of these nearby.
_PAY_WORD_RE = re.compile(
    r"(?:\b(?:salary|salaries|pay|paid|compensation|ctc|lpa|lakhs?|annum|p\.a|annual|annually|yearly"
    r"|hourly|ote|base|stipend|range|package|wage|wages)\b"
    r"|\bper\s(?:year|month|hour|week|day)\b|\ba\s(?:year|month)\b"
    r"|/\s?(?:yr|year|month|mo|hr|hour)\b|\bk\s?per\b)",
    re.IGNORECASE,
)
# Company-figure words: an amount right next to one of these is not pay.
_NOT_PAY_RE = re.compile(
    r"\b(?:revenue|transactions?|funding|funded|raised|raises|valuation|valued|billions?|arr|gmv"
    r"|markets?|customers?|series\s[a-f])\b",
    re.IGNORECASE,
)
# Pay facts (amount extent, period, currency, equity). Every quantifier is bounded.
_SYM = r"[$€£₹¥]"
_NUM = r"\d[\d,.]{0,15}(?:[ \u00a0]\d{3}\b){0,3}"
_AMOUNT_RE = re.compile(
    rf"(?:{_SYM}\s{{0,3}}|\b{_CUR}\s{{0,3}})?{_NUM}(?:\s{{0,3}}k\b)?"
    rf"(?:\s{{0,3}}(?:-|–|—|to)\s{{0,3}}(?:{_SYM}\s{{0,3}}|\b{_CUR}\s{{0,3}})?{_NUM}(?:\s{{0,3}}k\b)?)?"
    rf"(?:\s{{0,3}}{_CUR}\b|\s{{0,3}}(?:lpa|lakhs?|lacs?)\b|(?<=\d)L\b)?"
    rf"(?:\s{{0,3}}(?:/|per\s)\s{{0,3}}(?:yr|year|annum|month|mo|hour|hr)\b)?"
    rf"(?:\s{{0,3}}{_CUR}\b)?",
    re.IGNORECASE,
)
_ISO_RE = re.compile(rf"\b{_CUR}\b")  # case-SENSITIVE on purpose (salary-gap.mjs:312)
_LAKH_RE = re.compile(r"(?:\blpa\b|\blakhs?\b|\blacs?\b|(?<=\d)L\b)", re.IGNORECASE)
_ANNUAL_RE = re.compile(
    r"(?:/\s*(?:year|yr)\b|\bper\s+(?:year|annum)\b|\bannual(?:ly)?\b|\byearly\b"
    r"|\ba\s+year\b|\bp\.a\b|\blpa\b|\blakhs?\b)", re.IGNORECASE)
_MONTHLY_RE = re.compile(r"(?:/\s*(?:month|mo)\b|\bper\s+month\b|\bmonthly\b|\ba\s+month\b)", re.IGNORECASE)
_HOURLY_RE = re.compile(r"(?:/\s*(?:hour|hr)\b|\bper\s+hour\b|\bhourly\b|\ban\s+hour\b)", re.IGNORECASE)
# Whitespace runs are bounded ({0,3}), never `\s*`: an unbounded `\s*` in front of an alternation or beside an
# optional group backtracks quadratically on a long run of spaces (CodeQL py/polynomial-redos).
_PERIOD_STRIP_RE = re.compile(
    r"\s{0,3}(?:/\s{0,3}(?:year|yr|month|mo|hour|hr)|per\s{1,3}(?:year|annum|month|hour)|a\s{1,3}year|p\.a\.?"
    r"|annual(?:ly)?|yearly|monthly|hourly)\s{0,3}$", re.IGNORECASE)
_CUR_EDGE_RE = re.compile(rf"(?:^\s{{0,3}}{_CUR}\s{{0,3}}|\s{{0,3}}{_CUR}\s{{0,3}}$)", re.IGNORECASE)
_RANGE_RE = re.compile(r"^([\d.,]+)\s{0,3}(k)?\s{0,3}(?:[-–—]|to)\s{0,3}([\d.,]+)\s{0,3}(k)?$", re.IGNORECASE)
_SINGLE_RE = re.compile(r"^([\d.,]+)\s{0,3}(k)?$", re.IGNORECASE)
_EQUITY_RE = re.compile(r"\b(?:equity|stock\s+options?|rsus?|shares)\b", re.IGNORECASE)
_PLUS_PCT_RE = re.compile(r"\s?\+\s?\d+(?:\.\d+)?\s?%")
_PERIOD_WINDOW = 40  # characters after the amount read for its period
_SENTENCE_END_RE = re.compile(r"[.!?;](?=\s)|\n")
_PAY_WINDOW = 120  # characters either side of an amount
_NOT_PAY_WINDOW = 50  # how close an exclusion word must sit to veto an amount
_PAY_SCAN_CAP = 100_000  # characters of the ad ever scanned
_PAY_MATCH_CAP = 200  # amounts examined
_EVIDENCE_MAX = 160
_SPONSOR_RE = re.compile(r"\b(?:sponsor\w*|visas?)\b", re.IGNORECASE)


def word_count(text: str) -> int:
    """Whitespace-separated words."""
    return len(text.split()) if text and text.strip() else 0


def canonical_url(url: str) -> str:
    """One stable key for "the same ad link": lower-case host (``www.`` folded),
    ``utm_*`` / gclid / fbclid / ref / trackingId / refId and the fragment dropped,
    remaining query sorted, trailing slash dropped. ``''`` for a blank or
    unparseable link. Compared on read - nothing is stored."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return ""
    if not host or parts.scheme.lower() not in ("http", "https"):
        return ""
    host = host.removeprefix("www.")
    kept = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_KEYS
    )
    netloc = host + (f":{port}" if port and port not in (80, 443) else "")
    return urlunsplit(("https", netloc, parts.path.rstrip("/"), urlencode(kept), ""))


# A link names ONE job when it carries a job id: a long number, a UUID-like
# token, or a known job-id query key. Board roots and careers home pages do not.
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_JOB_ID_SEGMENT = re.compile(rf"(?:^|[/_-])(?:\d{{4,}}|{_UUID})(?:$|[/_-])", re.I)
_JOB_ID_KEYS = frozenset({"jk", "vjk", "gh_jid", "currentjobid", "jobid", "job_id", "jid", "id", "req", "reqid"})


def is_job_specific_url(url: str) -> bool:
    """True when ``url`` identifies a single job (see the two patterns above)."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return False
    if _JOB_ID_SEGMENT.search(parts.path or ""):
        return True
    return any(k.lower() in _JOB_ID_KEYS and v.strip() for k, v in parse_qsl(parts.query))


def apply_host(url: str) -> str:
    """The real host of ``url`` ('' when it is not an http(s) link). ``urlsplit``
    takes everything after the LAST ``@`` as the host, so
    ``https://greenhouse.io@evil.com/`` reads as ``evil.com``."""
    try:
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host if parts.scheme.lower() in ("http", "https") else ""


def _is_or_under(host: str, apex: str) -> bool:
    return host == apex or host.endswith("." + apex)


def _is_indeed(host: str) -> bool:
    labels = host.split(".")
    if len(labels) >= 2 and labels[-2] == "indeed" and len(labels[-1]) >= 2:
        return True  # indeed.com, uk.indeed.com, indeed.de
    return len(labels) >= 3 and labels[-3] == "indeed" and labels[-2] in ("co", "com") and len(labels[-1]) == 2


def classify_host(host: str) -> str:
    """``official_ats`` | ``job_board`` | ``unknown``. STRICT: the host must BE
    the vendor domain or end with ``.<domain>``. ``evil-greenhouse.io`` and
    ``greenhouse.io.evil.com`` are ``unknown``."""
    if not host:
        return "unknown"
    if any(_is_or_under(host, a) for a in settings.OFFICIAL_ATS_HOSTS):
        return "official_ats"
    if any(_is_or_under(host, b) for b in settings.JOB_BOARD_HOSTS) or _is_indeed(host):
        return "job_board"
    return "unknown"


def parse_day(value: Optional[str]) -> Optional[date]:
    """``YYYY-MM-DD`` (a longer ISO stamp is cut to its date) or None."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _canon_separators(num: str) -> str:
    """Port of career-ops salary-gap.mjs:169-196 ``canonicalizeSeparators``: with
    both ``,`` and ``.`` the LAST is the decimal point; with one, exactly three
    digits after it read as grouping ("35.000", "123,684"), else a decimal."""
    last_comma, last_dot = num.rfind(","), num.rfind(".")
    if last_comma != -1 and last_dot != -1:
        decimal = "," if last_comma > last_dot else "."
        grouping = "." if decimal == "," else ","
        return num.replace(grouping, "").replace(decimal, ".", 1)
    sep = "," if last_comma != -1 else "." if last_dot != -1 else None
    if sep is None:
        return num
    return re.sub(rf"(\d)\{sep}(?=\d{{3}}(?!\d))", r"\1", num).replace(sep, ".", 1)


def _to_num(num: str, k: bool) -> Optional[float]:
    try:
        n = float(_canon_separators(num))
    except ValueError:
        return None
    return n * 1000 if k else n


def parse_amount(span: str) -> Optional[tuple[int, int]]:
    """Port of career-ops salary-gap.mjs:204-235 ``parseAmount``: ``(min, max)``
    of an amount span, or None when it is not a plain number or range. Period
    words, currency symbols and a currency token are stripped first; NOTHING is
    annualised (the reference leaves other periods to the caller, :207-208).
    Lakh / LPA spans and "M" amounts return None: the reference has no such unit
    (NOT IN REFERENCE), so we never scale them."""
    s = (span or "").strip()
    if not s or _LAKH_RE.search(s):
        return None
    s = _PERIOD_STRIP_RE.sub("", s).strip()
    s = re.sub(r"[€$£¥₹]", "", s)
    s = re.sub(r"(?<=\d)[  ](?=\d{3}\b)", "", s)  # "70 000" -> 70000 (NOT IN REFERENCE)
    s = _CUR_EDGE_RE.sub("", s).strip()
    s = _PERIOD_STRIP_RE.sub("", s).strip()
    m = _RANGE_RE.match(s)
    if m:
        lo = _to_num(m.group(1), bool(m.group(2) or m.group(4)))
        hi = _to_num(m.group(3), bool(m.group(4) or m.group(2)))
        if lo is None or hi is None:
            return None
        return int(round(min(lo, hi))), int(round(max(lo, hi)))
    m = _SINGLE_RE.match(s)
    if m:
        v = _to_num(m.group(1), bool(m.group(2)))
        return None if v is None else (int(round(v)), int(round(v)))
    return None


def compensation_period(text: str, nearest: bool = False) -> Optional[str]:
    """Port of career-ops salary-gap.mjs:239-245 ``compensationPeriod``: annual /
    monthly / hourly, or None - an unknown period is never guessed (:244). Added
    (NOT IN REFERENCE): "a year", "p.a.", "LPA", "lakh" (annual), "a month",
    "an hour"."""
    hits = [(rx.search(text), name) for rx, name in
            ((_ANNUAL_RE, "annual"), (_MONTHLY_RE, "monthly"), (_HOURLY_RE, "hourly"))]
    found = [(m.start(), name) for m, name in hits if m]
    if not found:
        return None
    # `nearest`: the period word closest to the amount; else the reference's
    # annual > monthly > hourly order (:241-243).
    return min(found)[1] if nearest else found[0][1]


def _currency_of(span: str) -> str:
    """ISO code beside the amount. The reference takes the first standalone
    UPPERCASE 3-letter token, case-sensitive on purpose (salary-gap.mjs:312-317);
    we limit it to our ISO set and fall back to the symbols GBP / EUR / INR
    (NOT IN REFERENCE; the reference drops symbols, :217). "$" and "¥" are never
    mapped (many currencies): ``"UNKNOWN"`` as :317."""
    iso = _ISO_RE.search(span)
    if iso:
        return iso.group(0)
    for sym, code in (("£", "GBP"), ("€", "EUR"), ("₹", "INR")):
        if sym in span:
            return code
    return "UNKNOWN"


def _amount_bounds(text: str, ms: int) -> tuple[int, int]:
    """Char offsets of the whole amount (range, symbol / ISO token, k, lakh) for a
    money match starting at ``ms``. Bounded scans only, so it stays linear."""
    start = ms
    if text[start:start + 1].isdigit():  # the `\d CUR` / `\dk yr` forms start mid-number
        back = 0
        while start > 0 and back < 16 and text[start - 1] in "0123456789,.":
            start -= 1
            back += 1
        while start < ms and text[start] in ",.":
            start += 1
    m = _AMOUNT_RE.match(text, start)
    end = m.end() if m else ms + 1
    while end > start and text[end - 1] in " ,.":
        end -= 1
    return start, end


def _facts(text: str, evidence: str, s0: int, s1: int, period: Optional[str], sentence: str) -> dict[str, Any]:
    amount = text[s0:s1]
    bounds = parse_amount(amount)
    equity = bool(_EQUITY_RE.search(sentence)) or bool(_PLUS_PCT_RE.match(text, s1))
    return {
        "evidence": evidence, "amount": amount, "amount_span": [s0, s1],
        "min": bounds[0] if bounds else None, "max": bounds[1] if bounds else None,
        "period": period, "currency": _currency_of(amount), "equity": equity,
    }


def pay_facts(description: str) -> Optional[dict[str, Any]]:
    """The advertised pay in the ad, or None. ``evidence`` is the sentence/line
    (whitespace-collapsed, <=160 chars). An amount counts only with a pay word in
    its own sentence or line (<=120 chars each side), or as an INR pay unit (LPA,
    lakh, ``₹12L``); an amount beside revenue / funding / billion words never
    counts. Next to it, as stated and never annualised: ``amount`` (the matched
    text), ``amount_span`` ([start, end) offsets in ``description``), ``min`` /
    ``max`` (ints, or None when not a plain number), ``period`` (annual / monthly /
    hourly / None), ``currency`` (ISO code or ``"UNKNOWN"``) and ``equity``."""
    text = (description or "")[:_PAY_SCAN_CAP]
    for i, m in enumerate(_MONEY_RE.finditer(text)):
        if i >= _PAY_MATCH_CAP:
            break
        ms, me = m.start(), m.end()
        left = text[max(0, ms - _PAY_WINDOW):ms]
        right = text[me:me + _PAY_WINDOW]
        cut = 0
        for prev_end in _SENTENCE_END_RE.finditer(left):
            cut = prev_end.end()
        left = left[cut:]
        next_end = _SENTENCE_END_RE.search(right)
        if next_end:
            right = right[:next_end.start()]
        sentence = left + m.group(0) + right
        pos = len(left)
        near = sentence[max(0, pos - _NOT_PAY_WINDOW):pos + len(m.group(0)) + _NOT_PAY_WINDOW]
        if _NOT_PAY_RE.search(near):
            continue
        unit = _PAY_UNIT_RE.search(sentence)
        if not (unit or _PAY_WORD_RE.search(sentence)):
            continue
        anchor = unit.start() if unit and not _PAY_WORD_RE.search(sentence) else pos
        flat = " ".join(sentence.split())
        if len(flat) > _EVIDENCE_MAX:
            # centre the cut on the amount so the figure stays in the evidence
            anchor_flat = len(" ".join(sentence[:anchor].split()))
            start = max(0, min(anchor_flat - _EVIDENCE_MAX // 2, len(flat) - _EVIDENCE_MAX))
            flat = flat[start:start + _EVIDENCE_MAX]
        s0, s1 = _amount_bounds(text, ms)
        # the period word usually follows the amount: read that stretch first
        after = text[s0:s1 + _PERIOD_WINDOW]
        stop = _SENTENCE_END_RE.search(after)
        period = compensation_period(after[:stop.start()] if stop else after, nearest=True)
        return _facts(text, flat, s0, s1, period or compensation_period(sentence), sentence)
    # an INR pay unit on its own (no currency symbol the money regex saw)
    u = _PAY_UNIT_RE.search(text)
    if u:
        flat = " ".join(text[max(0, u.start() - 60):u.end() + 60].split())[:_EVIDENCE_MAX]
        return _facts(text, flat, u.start(), u.end(), "annual", flat)
    return None


def pay_evidence(description: str) -> str:
    """The evidence sentence of ``pay_facts``, or ``''``."""
    facts = pay_facts(description)
    return facts["evidence"] if facts else ""


def job_checks(
    description: str,
    apply_url: str,
    posted_at: Optional[str],
    employer_published_at: Optional[str],
    today: date,
) -> list[dict[str, Any]]:
    """The checks, each ``{"code", "ok" (bool|None), "reason"}`` plus a ``value``
    (and ``host``) where one helps. ``ok`` None = nothing to say. Never raises."""
    out: list[dict[str, Any]] = []

    words = word_count(description)
    if words < settings.JOB_CHECK_PARTIAL_MIN_WORDS:
        band, why = "title_only", f"{words} words - this is a title or a teaser; fetch the full ad before tailoring."
    elif words < settings.JOB_CHECK_FULL_MIN_WORDS:
        band, why = "partial", f"{words} words - the ad looks cut short; fetch the full ad before tailoring."
    else:
        band, why = "full", f"{words} words - the ad text looks complete."
    out.append({"code": "description_completeness", "ok": band == "full", "reason": why, "value": band})

    host = apply_host(apply_url)
    kind = classify_host(host)
    if not host:
        out.append({"code": "apply_host", "ok": None, "value": "unknown", "host": "",
                    "reason": "No apply link was given."})
    elif kind == "official_ats":
        out.append({"code": "apply_host", "ok": True, "value": kind, "host": host,
                    "reason": f"{host} is a known employer ATS host."})
    elif kind == "job_board":
        out.append({"code": "apply_host", "ok": False, "value": kind, "host": host,
                    "reason": f"{host} is a job board; the employer's own posting is the better source."})
    else:
        out.append({"code": "apply_host", "ok": None, "value": kind, "host": host,
                    "reason": f"{host} is not a known ATS or job board; tell the user which site the link goes to."})

    posted, employer = parse_day(posted_at), parse_day(employer_published_at)
    newest = max((d for d in (posted, employer) if d), default=None)
    if newest is None:
        out.append({"code": "ad_age_days", "ok": None, "value": None, "reason": "No posting date was given."})
    else:
        age = (today - newest).days
        out.append({"code": "ad_age_days", "ok": None, "value": age,
                    "reason": f"Newest date given is {newest.isoformat()}, {age} days ago."})
    if posted and employer:
        gap = abs((posted - employer).days)
        reposted = gap > settings.JOB_CHECK_REPOST_GAP_DAYS
        if reposted:
            why = (
                f"The listing says {posted.isoformat()} but the employer's posting says "
                f"{employer.isoformat()} ({gap} days apart): reposted."
            )
        elif gap == 0:
            why = f"Listing and employer dates match ({posted.isoformat()})."
        else:
            side = "after" if posted > employer else "before"
            why = (
                f"Listing {posted.isoformat()} is {gap} days {side} the employer's "
                f"{employer.isoformat()} (normal indexing lag)."
            )
        out.append({"code": "reposted", "ok": not reposted, "value": reposted, "reason": why})

    facts = pay_facts(description)
    if facts:
        per = facts["period"] or "not stated"
        cur = facts["currency"] if facts["currency"] != "UNKNOWN" else "not stated"
        out.append({"code": "pay_advertised", "ok": True, "value": True, **facts,
                    "reason": f'The ad advertises pay: "{facts["evidence"]}" '
                              f"(period: {per}, currency: {cur}; as advertised, not annualised)"})
    else:
        out.append({"code": "pay_advertised", "ok": False, "value": False, "evidence": "", "amount": None,
                    "amount_span": None, "min": None, "max": None, "period": None, "currency": None,
                    "equity": None,
                    "reason": "No pay (an amount next to a salary or pay word) appears in the ad text."})
    spon = bool(_SPONSOR_RE.search(description or ""))
    out.append({"code": "sponsorship_mentioned", "ok": spon, "value": spon,
                "reason": "The ad text mentions sponsorship or visas - read what it says." if spon
                else "The ad text does not mention sponsorship or visas."})
    return out


def checks_for_application(app_row: Mapping[str, Any], description: Optional[str] = None) -> list[dict[str, Any]]:
    """Checks for one stored application row (``SELECT *``). ``description``
    overrides the application's snapshot when the caller holds the catalog text."""
    text = description if description is not None else str(app_row.get("job_description_snapshot") or "")
    return job_checks(
        text, str(app_row.get("job_url") or ""),
        app_row.get("ad_posted_at"), app_row.get("employer_published_at"), date.today(),
    )
