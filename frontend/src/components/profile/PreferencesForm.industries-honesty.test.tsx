/**
 * PreferencesForm — Industries help text honesty (Pillar 1 audit, Finding 9).
 *
 * The old copy said "Target industries for relevance scoring bonus". That is
 * false: a full read of backend/src/services/profile/{keyword_generator,
 * skill_matcher,scoring_dimensions,prefilter,embeddings,llm_matcher}.py shows
 * `preferences.industries` reaches exactly ONE consumer — the semantic
 * embedding text built in embeddings.py (`_add_all(getattr(prefs,
 * "industries", []))`). No keyword generator, no scoring dimension, no
 * prefilter, and no LLM judge ever reads it (llm_matcher.py only reads the
 * CV-extracted `cv_industries` fact, a different field). Semantic matching is
 * gated behind SEMANTIC_ENABLED/ENGINE3_ENABLED and only actively scores a
 * minority of jobs in production, so for most jobs typing an industry here
 * changes nothing. This test pins the honest replacement copy and guards
 * against the old overclaim coming back.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { PreferencesForm } from "./PreferencesForm";

describe("PreferencesForm — Industries help text", () => {
  it("tells the user most jobs are not affected, instead of promising a scoring bonus", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={{}} onSave={onSave} loading={false} />);

    // Meaningful phrase, not the whole sentence -- a later wording tweak
    // shouldn't break this as long as it still tells the truth about reach.
    expect(
      screen.getByText(/most jobs (are )?not affected/i)
    ).toBeTruthy();
  });

  it("never claims a 'relevance scoring bonus' the field does not deliver", () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<PreferencesForm preferences={{}} onSave={onSave} loading={false} />);

    // Control: this is the exact overclaim Finding 9 flagged. If a future
    // edit reintroduces it, this must fail.
    expect(screen.queryByText(/scoring bonus/i)).toBeNull();
  });
});
