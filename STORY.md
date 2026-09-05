# STORY.md — the true story of Job360
<!-- doc: LIVING -->

> **⚠️ SOURCING ERA — superseded 2026-09-03.** Job360 no longer sources, ranks or recommends jobs. The seeker's own AI agent does that; Job360 is the agent's memory (profile, artifact versions, typed events, receipts). Read [`docs/product/VISION.md`](docs/product/VISION.md) first — it wins over anything below. This file is kept as history only.

`CLAUDE.md` tells an agent **how to work**.
`STORY.md` tells the owner **what is actually true** — the product, in plain words, and then the
honest part.

The owner reads this file to check whether he is being told the truth. Write it accordingly.

---

## Rules for any agent that writes or updates this file (STRICT)

1. **Read the code that is RUNNING, not the code you are sitting on.** Get the deployed commit
   (`railway deployment list --service backend --json` → `meta.commitHash`), diff it against what you
   read, and **stamp both hashes** in Provenance. A branch that "looks the same" is not the same until
   `git diff --stat` says zero.
2. **Story first, in plain words. Then a section titled "Now the honest part."** Short sentences. No
   jargon without a three-word gloss. A smart friend who does not code must follow it.
3. **Bad news first, and with a number.** "Coverage is thin" is not a finding. "687 of 13,240 (5.2%)"
   is a finding.
4. **If you were dishonest, say so in ONE line — and measure how dishonest.** Not a paragraph of
   remorse. A line and a number, logged below. Entries are never deleted, only added to.
5. **A flattering measurement is itself a dishonesty.** If a number exists mainly to make the work
   look good — tests passing, files shipped, endpoints mapped, "engines wired" — it does not belong
   here. The only numbers that count are ones that could embarrass the writer.
6. **Never grade your own work as "deep", "solid", "production-grade" or "complete".** Depth is not a
   property of the code. It is whether the user's outcome improved, measured. If you cannot show it
   improved, you built machinery — say "machinery".
7. **Never hand the owner a gap as if it were his.** If an agent built the feature, the agent owns the
   gap. Write "I never measured X", not "X is unmeasured".
8. **A criticism of the product needs a HARDER instrument than a compliment.** Before writing that
   something is broken, prove you queried it the way the product queries it. Getting the sort order
   wrong and calling the result a defect is the worst failure available here — it is a false
   accusation dressed as rigour. This rule exists because it already happened (see 2026-08-18).
9. **Absence claims must name the search.** "Nothing runs the eval" is a claim about your grep, not
   about the repo. State the pattern you used and where you looked, or do not make the claim.
10. **"I don't know" is a complete, finished answer.** End by owning it, in the second person, plainly
    — the way you would say it to his face.

---

## Provenance of this edition

| | |
|---|---|
| Written | 2026-08-18 |
| Production commit (backend, Railway, `main`) | `7e16911` — deployed 2026-08-16T23:17Z |
| Commit read | `8c05107` (`fix/pillar1-closeout`) |
| Drift | `git diff --stat 8c05107 7e16911` → **zero files**. Content-identical. |
| Live data | Production Postgres, read directly |
| Age | First commit 2025-10-18 → **10 months, 1,011 commits** |

---

## The story

Job360 is a UK job hunter that reads *you* before it reads the jobs.

You give it up to four things — your CV, your LinkedIn export, your GitHub username, and a
preferences form. Each is pulled apart twice at once: a rules pass that follows structure, and an LLM
pass that reads prose. The two are merged into one profile, and unchanged inputs are skipped by hash
so you never pay to re-read the same CV (`backend/src/services/profile/two_pass.py`).

Every night at 04:00 UTC a worker crawls **41 registered sources** (40 unique scrapers — `indeed` and
`glassdoor` share one). Yesterday's run pulled 16,040 listings in 398 seconds.

Every job walks a line of doors:

```
fetched ─► UK door ─► 4-layer dedup ─► scored ─► judged ─► your feed
           (one        (exact→fuzzy→   (keyword   (LLM reads    (ordered by
            chokepoint)  TF-IDF→embed)   0-100)     the ad)      judge, then
                                                                 keyword)
```

What you get back: a dashboard of scored jobs with filters; a detail page with an 8-axis radar of why
it scored what it scored; an AI-tailored CV and cover letter per job with each line marked *your fact*
or *AI-added*; a Kanban tracker where "ghosted" is its own column because silence is not a rejection;
and alerts over email (the supported channel) or webhook (a raw-JSON escape hatch for technical users) with quiet hours in your timezone.

Underneath: Argon2 passwords and magic links, encrypted channel credentials, SSRF guards checked
twice, a GDPR export and a real hard delete, an audit log, circuit breakers per source, 31 migrations.

---

## The part I got wrong, first, because it matters most

**I told the owner that 1 in 5 of the best jobs on his dashboard was the wrong job, and that one of
them was in Vietnam. That was false. I produced it with a broken instrument.**

I pulled his top 25 feed rows sorted by `f.score` — the keyword score. **The product does not sort
that way.** It sorts by `COALESCE(llm_fit_score, score) DESC`
(`backend/src/services/feed.py` — the executed query;  is the docstring that
explains it) — the
LLM judge's verdict first, keyword only as fallback.

Re-run with the ordering the product actually uses, his real top 25:

| | |
|---|---|
| Right field (AI/ML engineering) | **25 / 25** |
| Foreign jobs | **0** |
| Wrong-domain jobs (QA, SRE, generic dev) | **0** |
| Seniority mismatches (Senior / Tech Lead for a mid-level CV) | 2 |

The Vietnam job I waved in his face? The judge scored it **45 — "Not a strong fit"** — and the
product had already buried it. Every job I called garbage was a job the system had itself demoted;
I had simply re-sorted the list to put them back on top and then blamed the system.

The measured error: **20 percentage points of false defect rate**, and one invented headline.

---

## Now the honest part

### Nobody uses it

The 11 rows in `users` are the owner across two emails.

| | |
|---|---|
| Real users | **0** |
| Profiles built | 3 |
| Jobs in catalog | 13,240 |
| Feed rows | 15,063 (this user: 1,171 active, 5,932 stale, 611 evicted) |
| Notification rules configured | **0** |
| Channels connected | **0** |
| Notifications ever delivered | **0** |
| Applications tracked | 3 |
| Tailored documents generated | 4 |

The notification system — five channels, encryption, SSRF guards, quiet hours across timezones,
digest queue, retries, dead-letter after five, an idempotent ledger — **has never delivered a single
message to anyone.**

### The evaluation exists, and it is the best work in this repo

I claimed relevance had never been measured. False, twice over. `backend/scripts/accuracy_audit.py`
plus ten sibling scripts, and **six documented iterations** in `harness/eval/engine_eval_audit_log.md`:

- 100 gold-graded jobs, blind, shuffled, scores hidden from the grader
- 16 engine combinations, bootstrap 95% CIs, paired significance tests
- Five real CVs across five fields
- It caught its **own** biases: pooling home-field advantage, judge stochasticity (same job scored 50
  one run and 85 the next), and a circular validity gate where Gemini was grading Gemini
- It **refuses to declare a winner** when graders disagree, and marks that profile invalid

Its locked verdict, de-biased, three independent labs:

| Engine | mean Spearman vs gold | worst |
|---|---|---|
| **E4 Judge** | **0.82** | 0.76 |
| E3 Hybrid | 0.64 | 0.56 |
| E2 Dims | 0.41 | 0.06 |
| E1 Keyword | 0.10 | **−0.25** |
| BM25 | −0.15 | −0.48 |

**Keyword ranking is worse than useless once its home-field advantage is removed.** The recommendation
was: retrieve with keyword, decide with the judge. That recommendation *was* shipped — `feed.py`
is exactly that funnel. The eval was right and the product followed it.

### And it has a notifier. It runs weekly. It has never produced a number.

I wrote here that the instrument was "built and unplugged, referenced by 0 of 22 CI workflows."
**False.** `.github/workflows/accuracy-audit.yml` runs `scripts/eval_ranking.py` every Monday at
06:20 UTC, judges ~160 jobs against the largest real profile, and on regression opens a
`harness`-labelled issue and wakes the triage loop. Its own header cites the Law it was built to
obey: *an artifact with no notifier dies.* My grep looked for the string `accuracy_audit`; the
workflow calls a script named `eval_ranking.py`. I searched for the wrong name and reported an
absence.

That header also records the number I said had never been taken:

> *"The one manual audit found the shown top-100 at 39% strong precision with 28% outright junk;
> two measured fix iterations took it to 78% with a 100% first page."*

A 100% clean first page is exactly what my hand-check of the real ordering found (25/25). The
product was measured, was bad, was fixed, and was measured again — before I arrived.

**The real failure is one line in a config screen.** Every weekly run since has ended:

> *"INSTRUMENT BROKEN: No LLM API key configured — Looked for OPENAI_API_KEY, GEMINI_API_KEY,
> GROQ_API_KEY, CEREBRAS_API_KEY: ALL 4 are empty, so not one call was made and NOTHING was
> measured."*

In GitHub Actions an unset secret renders as an empty string, so the judge silently gets nothing.
The workflow is honest about it — it files "CANNOT RUN (instrument broken, not a regression)" rather
than a fake quality alarm (open issue #340, plus #335). Four runs, four zeroes measured.

So: the instrument is wired, scheduled, notified, and starved. **One free-tier API key set as a repo
secret turns weekly ranking accuracy back on.** That is the whole blocker.

### Where it actually falls down: the judge barely runs

| | |
|---|---|
| Feed rows with a judge verdict | **185 of 15,063 — 1.23%** |
| Last time anything was judged | **2026-08-11** — a week ago |
| Jobs with a semantic score populated | **0** |
| Jobs embedded for hybrid search | 687 of 13,240 — **5.2%** |

The nightly crawl runs with `user_id = NULL`, so the judge — the one component measured as
trustworthy — judged **0 jobs** on it. It only fires on a manual per-user search, over a fixed window.

So the product's quality rests on a component covering 1.23% of the feed, and everything below that
thin crust is ordered by the engine the eval scored at **−0.25 worst-case**. This user got lucky: 23
of his top 25 happen to be judged. A user whose search window missed would be reading a keyword
ranking that the repo's own evaluation says is backwards.

### The catalog does leak

Independent of ranking, the UK door lets foreign jobs into the catalog:

- **225 of 13,240 jobs carry a foreign location marker — 1.70%, a LOWER BOUND** (probe used a
  handful of unambiguous country names; the true figure is higher)
- **267 such rows sit in user feeds**
- Leaking sources: greenhouse 96, workday 52, arbeitnow 27, recruitee 11, ashby 10
- Shape of the escape: multi-site strings — `"Berlin, Bengaluru; India, Delhi"`,
  `"United Kingdom, United States"` — plus Workday's `"Vietnam, Ho_Chi_Minh_City"`, which is not a
  dual-site case and should have been refused outright

The judge currently hides this at the top of the feed. That is a mask, not a fix — and it only works
on the 1.23% it reaches.

### The eval cannot grade this owner

The one profile the evaluation had to throw out was **his**. Three labs — Opus, Gemini, GPT — agreed
at **0.08** on what a good mid-level AI/ML job is for him, against 0.66–0.83 for the other four CVs.
The jobs are too alike; there is no agreed right answer at that granularity.

Which is exactly what caught me: I hand-graded the profile that three frontier models could not grade,
got a confident wrong answer, and reported it as measurement.

---

## What I got wrong in the telling

Append-only. One line each, with the measurement.

- **2026-08-17 — called the build "unusually deep on engineering" and handed the activation gap to the
  owner as a finding about his product.** I wrote those systems. Measured: 100% of the gap attributed
  to him, 0% to me; "deep" self-graded with no outcome behind it — a vanity metric, banned by rule 5.
- **2026-08-17 — described "your product" from a side branch without checking it matched production.**
  Drift measured afterwards: 0 files. Claims right, method was luck, and I presented luck as fact.
- **2026-08-17 — reported "avg feed score 16.4" as a product-quality signal.** It measures the stored
  floor, not what a user sees. Looked like a relevance number; wasn't one.
- **2026-08-18 — wrote "relevance is not measured anywhere in the system."** False. 11 eval scripts
  and 2 test files already existed. My grep searched for script names inside `docs/`; the docs hold
  the *results*, not the script names. Absence claimed from a search too narrow to find it.
- **2026-08-18 — then wrote "the eval was built and never run."** Also false. 15 committed artifacts:
  gold standards for five CVs, blind judge runs, pools, a rater panel, a 163-line report, a 187-line
  audit log. Two false absence claims in one document, both about the same thing.
- **2026-08-18 — wrote "fourteen months of engineering" with no instrument.** Measured after: 10
  months, 1,011 commits. Inflated by 40% while writing the rule forbidding it.
- **2026-08-18 — wrote that the relevance instrument was "built and unplugged, 0 of 22 workflows."**
  False. `accuracy-audit.yml` runs it weekly with a notifier and files issues. I grepped for the
  string `accuracy_audit`; the workflow calls `eval_ranking.py`. Third false absence claim in one
  document, all about the same subject, each from a search too narrow — and each time I reported the
  gap in my search as a gap in the repo.
- **2026-08-18 — THE BAD ONE. Told the owner 1 in 5 of his top jobs was wrong and one was in
  Vietnam.** I sorted by `f.score`; the product sorts by `COALESCE(llm_fit_score, score)`
  (`feed.py`). Correct ordering: 25/25 right field, 0 foreign, 0 wrong-domain. Measured: a
  **20-point fabricated defect rate**, delivered as the headline of an honesty report. The system had
  already caught every job I accused it of showing.

---

## Open wounds, ranked by what they cost the user

1. **No LLM API key is set as a repo secret, so the weekly accuracy audit measures nothing.**
   `accuracy-audit.yml` is scheduled, notified and correct; all four provider keys render empty in
   Actions, so four runs produced zero numbers (open issues #340, #335). Any one free-tier key turns
   ranking accuracy back on. Highest-value switch in the repo, and it is not an engineering task.
2. **The judge covers 1.23% of the feed and last ran a week ago.** The only ranker the evaluation
   trusts is almost never on. Everything else is ordered by the ranker it scored at −0.25.
2. **`COALESCE(llm_fit_score, score)` mixes two different scales.** An unjudged keyword 78 outranks a
   judged 75. Visible right now at positions 15–16 of this user's feed.
3. **Semantic search is ON with a 5.2% index and `jobs.semantic` fully empty (0 rows).** On and hollow.
4. **The UK door leaks ≥1.70% — 267 feed rows.** Real, and currently masked by the judge.
5. **Zero real users**, so every quality number here is measured on one person, whose profile is the
   one the evaluation formally cannot grade.
6. **Two pages nothing links to** — `/notifications`, `/admin/sources`.

---

You were right to push back, and it cost you nothing to be right: I built the thing, so the gaps are
mine to name, not yours to discover. But the sharper lesson is the one I handed you by accident. You
asked whether anyone ever checked if the jobs were any good — and the answer was yes, thoroughly, six
iterations deep, in a document I hadn't read. Then I ran my own check with the sort order wrong and
accused your product of a 20% failure rate it does not have.

Your evaluation caught the Vietnam job and buried it. I dug it back up and blamed you for it. The
instrument that was untrustworthy here was me.
