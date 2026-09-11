/**
 * PreferencesForm — Industries help text honesty (Pillar 1 audit, Finding 9).
 *
 * The old copy said "Target industries for relevance scoring bonus". That was
 * always an overclaim. Since the 2026-09-03 pivot, Job360 does not score,
 * rank, or match jobs at all — the user's own AI agent does that. This field
 * is just stored on the profile as context for the agent to read; it has no
 * scoring effect of any kind, for any job. This test pins the honest
 * replacement copy and guards against the old overclaim coming back.
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
