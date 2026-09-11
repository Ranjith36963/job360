# Slice 7 — visa / sponsorship signal, country-agnostic (#514)
<!-- doc: PLAN | written 2026-09-11 after the owner's "go" -->

## Intent (the owner, 2026-09-11)

> This is a global thing we're building, not only for the UK. Every country
> has its own restrictions. Only when you see it in the job description, you
> flag it.

**Job360 never knows anything about visas.** No country rules, no keyword
scanning, no guessing when the ad is silent. We store two facts and compare
them (VISION decision 20: the agent supplies it, we never extract it).

## The two facts

**Fact 1 — from the ad, per application** (`applications` slot, migration 0042):

| Column | Meaning |
|---|---|
| `visa_signal` | `sponsors` / `no_sponsorship` / `unknown` (closed set `APPLICATION_VISA_SIGNALS`) |
| `visa_detail` | the sentence from the ad the judgement rests on, ≤ `APPLICATION_VISA_DETAIL_MAX_CHARS` (500) |
| `visa_country` | ISO 3166-1 alpha-2 of the job's country, upper-case, or `''` |
| `visa_recorded_by`, `visa_recorded_at` | who/when |

Supplied by the agent on `bring_job` or `save_fit` (both MCP tools and their
routes), or by a human at the browser through `PUT /applications/{id}/visa`
(the web dropdown). `unknown` means "the ad said nothing" and shows nothing
(rule #29). A slot, not history: it is overwritten like the fit verdict (S7);
when set through `save_fit` the `fit_judged` event payload carries it too.

**Fact 2 — from the candidate** (`preferences.work_authorization_countries`):
a list of ISO alpha-2 codes where the candidate can work without
sponsorship. Set on the web (profile → preferences) or by the agent through
`update_profile` (new editable path). Empty list = "don't care" = no
comparison, never a penalty (rule #29). The old `needs_visa` boolean stays
untouched (other tests pin it) but is no longer what the badge reads.

## The comparison (backend, one function, no country knowledge)

```
needs_sponsorship =
  None  if signal == unknown or visa_country == '' or countries == []
  False if visa_country in countries
  True  otherwise
```

Returned as `visa: {signal, detail, country, recorded_by, recorded_at,
needs_sponsorship}` on `GET /applications/{id}`, and as `visa_signal`,
`visa_country`, `needs_sponsorship` on each `list_applications` row, so the
card and the detail page never need the profile.

## The badge (web, pure)

| signal | needs_sponsorship | shows |
|---|---|---|
| `unknown` | — | nothing |
| `sponsors` | any | green "Sponsors visas" |
| `no_sponsorship` | `True` or `None` | red "No sponsorship" |
| `no_sponsorship` | `False` | muted "No sponsorship · not needed for you" |

`visa_detail` under the badge on the detail page as a quote (text node).

## Security guardrails

- `visa_signal` validated server-side against the closed set (422 otherwise);
  `visa_country` must match `^[A-Za-z]{2}$` and is upper-cased (422 otherwise);
  `visa_detail` length-capped (422). Free text rendered as text only.
- Every route `Depends(require_user)`; the slot update goes through
  `get_owned_application` (rule #12/#25). No new MCP tool: `bring_job` and
  `save_fit` gain optional inputs; the human door `PUT …/visa` has no tool
  (a human at a browser has no agent — same reasoning as `fetch-url`).
- Nothing writes history except the existing `fit_judged` event (M3).

## Out of scope

- Dropping the dead `jobs.visa_flag` column: eight source files and four test
  files still name it; that is its own cleanup PR.
- Any per-country rule, any extraction from the ad by us.

## Done when

- `tests/test_visa_signal.py`: `bring_job(visa_signal="no_sponsorship",
  visa_country="DE")` shows on detail and list; `save_fit` changes it and the
  `fit_judged` payload carries it; `needs_sponsorship` is `None` / `False` /
  `True` across the three cases; junk signal / country / over-cap detail are
  422; the profile list round-trips through `POST /profile` and
  `update_profile`; a second user cannot set it.
- Hermetic e2e: red badge on a card and on the detail page; nothing for
  `unknown`; the dropdown PUTs the right body.
- Types regenerated; VISION build-order 7 + roadmap row 7 + docs/README +
  ARCHITECTURE env rows.
