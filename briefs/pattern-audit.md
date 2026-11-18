# Pattern audit: Claude judges, patterns only hint

From Kits, 2026-11-18. Audit of origin/main @ 063fabe, read-only.

## Owner rule (applies to every task)

Claude is the intelligence. A fixed pattern (a regex, a word list, an exact-phrase match) may give a
**hint or a confidence signal**. It must never **judge or select**: it must not drop, refuse or pick a
job, email, form question, person or CV line on its own.

Keep it simple: tell Claude what to judge in the recipe, and hand it the evidence. Add a check only
where a real run shows it going wrong. Do not add new refusals.

Out of scope, leave as they are: authentication, tenancy, rate limits, payload shape, the password and
CAPTCHA hard lines, `check_submit` / `check_outreach`, and the protected categories (identity, health,
legal history).

## How to work

- Read-only diagnosis first, then one PR per slice, under 400 lines, with a failing test first.
- Never write to the owner's live Job360 account.
- Severity: **A** = silently drops or refuses real work. **B** = a wrong label that costs a retry.
  **C** = harmless.
- "Verified" = the audit ran it. "Plausible" = read from the code, not run.
- Report the PR number and proof in your own session. Kits reads it there.

## 1. Bulk Hunt test

Kits is already building the title change (finding 2) in this session: the kit hands over every title
and Claude decides. Do not duplicate it. Take the rest.

1. **A, verified.** `hunt_kit/hunt_kit.py:676-677` (with `:331-335`, `:1661`): when
   `--exclude-title-words` is not passed, the kit falls back to a built-in exclude list (senior, lead,
   manager, scientist, ii, support and others). The help text at `:3563` says "default: none". "Machine
   Learning Engineer II" is dropped. Fix: default to no exclusions.
2. **A, verified.** `hunt_kit.py:676,686`: custom titles match only as exact phrases. Kits owns this.
3. **A, verified.** `hunt_kit.py:775-792` → `:846`: `eligibility_locked` drops the whole job when any
   one sentence is a country lock. "We hire anywhere in Europe. US applicants must be authorized to
   work in the US." is dropped. Fix: attach `lock_lines` and keep the ad.
4. **A.** `hunt_kit.py:1781` (default `--max-years 5` at `:3581`): a "5+ years" ad is dropped whatever
   the user's experience. Fix: emit `years_min` as a hint only.
5. **A, verified.** `hunt_kit.py:382-396,822`: any negation word in a visa sentence makes it a refusal.
   "We sponsor visas for candidates who do not yet live in the UK" reads as refused. Fix: return the
   visa sentences as quotes for Claude to read.
6. **A.** `hunt.md:18` and `reposts.py:45-60,137-164` (via `bring.py:604`): duplicate keys strip a
   trailing "(…)" and " - City", so "Software Engineer (Backend)" and "(Frontend)" are skipped as
   known. Fix: report `possible_duplicate` and let Claude compare the ads. Check this against #1665.

Owner decisions that also apply here: the hunt covers every plan country and region, for on-site,
remote and contract roles, and keeps every ad whatever it says about sponsorship.

## 2. CV review test

1. **B, plausible.** `review_rows.py:32-42,50-55,123-146`: ROLE_FAMILIES and NUMBER_WORDS override the
   reviewer's "supported". Likely misses: "hiring pipeline", "responsible AI", "requests per second",
   "first release", "March". Each costs a weak verdict and a try. Fix: return them as `check_words` for
   the reviewer to confirm.
2. **B.** `cv_review.py:254` with `cv.md` step 3: the closed skills-label vocabulary weakens labels such
   as "Practice:" or "Product:" (apps 455, 460, 461). Fix: flag an unknown label, do not downgrade.
3. **B, plausible.** `review_jd.py` with `CV_REVIEW_JD_BAR` 0.8 (`settings.py:1212`): coverage is a
   literal token match. A profile that says "k8s" can never cover an ad that says "Kubernetes". Fix:
   accept the reviewer's `n` lines as covered.

Also check coverage: does every queued job get a CV, and do the old weak CVs (479-534, 3 tries left)
ever get fixed, or do runs keep skipping them?

## 3. People hunt test

1. **A, plausible.** `outreach_review.py:214-219`: `hard_facts` checks facts about the recipient too.
   "Congrats on leading the PyCon launch in March" fails on "leading", "PyCon" and "March". After 2
   weak reviews, `retry_limit` blocks auto-send to that person for good. This is likely why every
   outreach review stores weak. Fix: limit fact checks to claims about the user.
2. **B, plausible.** `contacts.py:244-259`: `_company_owns_host` refuses `company_team_page` when the
   host does not contain the company's name (Alphabet on abc.xyz). Fix: warn, do not return 422.

## 4. Gmail check test

1. **B.** `inbox.md:18` with `email_match.py`: the server lists candidates only from the sender domain
   or the company in the subject. A Greenhouse or Ashby mail with a generic subject matches every
   application on that ATS, or none, and the recipe then forces "park, never guess" even when the body
   names the company. Fix: let Claude pick a candidate from the body and quote the line.
2. **C.** `inbox_items.py:216` and `inbox_senders.py:34`: older parked mail is hidden when the sender's
   local part contains "alert", "digest" or "newsletter". Low risk; note it only.

## 5. Bulk apply test

1. **B, plausible.** `kit.py:3296-3317`: the sensitive-topic regex marks a question as needing
   confirmation (`ready.py:622`, `blockers.py:172`), which holds the row for the user. Likely misses:
   "notice period", "experience with background check APIs", "political science". Fix: keep it as a
   hint and let Claude classify. The protected categories stay hard.
2. **B, plausible.** `kit.py:3320-3327` with `apply.md:31`: knock-outs `stop_for_user`. `\bdegree\b`
   catches "To what degree…", and "years…experience" catches routine skill questions. Fix: list them as
   hints; Claude decides which are true knock-outs.
3. **B.** `kit.py:3665-3685`: sponsorship questions are answered only from an exact-wording allow-list,
   so any new phrasing becomes a user ask. Saved answers use the same exact match
   (`answer_match.py`). Fix: Claude proposes the polarity; the server checks only the country.

Owner decisions that also apply here: apply to every queued job in every country, whatever the ad says
about sponsorship. Work-authorisation answers stay truthful per country from the profile. Availability
is immediate and relocation is yes, once the owner saves them to the profile.

## 6. Browser agents test

1. **B, plausible.** `sites.py:231-243` → `agent.py:1068`: any page containing an
   `ALREADY_APPLIED_PHRASES` phrase pulls the job off the queue. A dashboard headed "Jobs you have
   already applied for this month" would trigger it. Fix: report it as a signal; Claude confirms before
   blocking.
2. **B, plausible.** `safety.py:281-287`: CAPTCHA detection from page text. An ad for a bot-detection
   firm triggers a hand-off to the PC. Fix: treat a text hit as a hint unless a CAPTCHA frame is
   present. Never solving a CAPTCHA stays the hard line.
3. **C.** `own_browser.md:36`: the "junior: forward deployed, agentic, AI-native" ranking is hard-coded
   for every user. Fix: read it from the profile.
