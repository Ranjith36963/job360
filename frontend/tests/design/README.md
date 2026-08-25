# Design pass — the tooling, and the measurements behind the fixes

Three scripts, one job: stop guessing what the page looks like and measure it.

| File | What it does |
|---|---|
| `audit-fn.mjs` | **The audit.** Runs inside the browser via `page.evaluate`. Side-effect free so anything may import it. |
| `layout-audit.mjs` | Drives Chromium over `routes.mjs` at three widths and writes the findings JSON. |
| `self-check.mjs` | Breaks the page on purpose and asserts `audit()` notices. Run it after touching `audit-fn.mjs`. |
| `design-pass.mjs` | The screenshot pass. |
| `mock-data.mjs` · `routes.mjs` | Fixtures and the route list. |

```bash
DESIGN_SESSION=<cookie> node tests/design/layout-audit.mjs
DESIGN_SESSION=<cookie> node tests/design/self-check.mjs
```

## Why the self-check imports the audit

`layout-audit.mjs` launches Chromium at import time, so `self-check.mjs` could
not import it and re-implemented a simplified version of the measurement
inline. That is not a check of the checker — a regression inside `audit()`
passes an independent re-implementation without a murmur, and this audit has
silently broken twice: the on-screen filter that excluded the very elements
`CLIPPED_BY_CONTAINER` looks for, and the rgb colour parsing that invented **189
FALSE contrast failures** — the palette is authored in oklch, Chrome returns
`lab(...)`, and reading that as rgb collapsed every ratio, including
white-on-black nav text reported at "1.48:1" when it is really about 19:1
(`audit-fn.mjs`, the `describe`/contrast section). A parser that wrong in one
direction hides real failures just as easily. The audit now lives alone in
`audit-fn.mjs` and both callers run the same copy.

Note the honest gap: the audit has **no ragged-height finding**, so the
self-check's height assertion measures the PAGE, not the checker. It is
labelled as such in the output.

## The measurements behind the JobCard and TimeBuckets fixes

Kept here rather than in the components — a component file is not where
viewport numbers belong, and a number in a comment rots silently.

**`h-full` on the card (JobCard).** The cards sit in a 2/3-column grid, but each
one is wrapped in a plain block div, so the CARD was never the grid item and
never got the row's stretched height. At 1440px one row of three measured
**189 / 231 / 191px** tall — ragged bottoms, three different gaps before the
next row.

**`flex-wrap` on the action row (JobCard).** That row holds Apply, Tailor my CV,
Like, Skip, Details and the source tag. Without wrapping it ran past the card,
and the card is `overflow:hidden`, so the overspill was CLIPPED AWAY rather than
scrolled. At 1440px the Details button sat **79px** and the source label
**116px** beyond the padding box — both invisible and unclickable.
`mt-auto` then pins the row to the bottom so buttons line up across a row.

**`scrollIntoView` on the active bucket (TimeBuckets).** The row is
`overflow-x-auto scrollbar-none`: it scrolls but shows no scrollbar to say so.
The default bucket is the LAST one (7d), so a **390px** screen opened on
"All 24h 48h 3d 5d…" with the active filter off the right edge and no hint it
existed. Centring the active chip makes the selection visible and cuts chips off
on BOTH sides, which reads as "scroll me". It is feature-detected because jsdom
implements no layout and has no `Element.scrollIntoView` — calling it unguarded
threw during mount and failed **9 dashboard tests** on a purely cosmetic change.
