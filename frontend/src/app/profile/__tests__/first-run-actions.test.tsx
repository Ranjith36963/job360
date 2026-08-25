/**
 * A first visit must not lead with actions that operate on a profile you do
 * not have yet.
 *
 * WHAT IT LOOKED LIKE. Screenshotting /profile on a brand-new verified account
 * (tests/design/design-pass.mjs) showed the two most prominent controls above
 * the fold were "Export JSON Resume" — of a resume that does not exist — and
 * "History" — of a profile that has never been saved. The one thing a new user
 * should do, "Upload CV", sat below both. ClearButton, right next to them, was
 * ALREADY gated on `profile`; Export and History were simply missed, so this
 * pins all three together.
 *
 * getProfile rejects with a 404 here because that is exactly what the backend
 * returns for a user with no profile yet (documented as benign in
 * .claude/skills/verify-job360).
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import ProfilePage from "../page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

// Reject with a real ApiError, not a plain Error carrying a status field.
// ProfilePage only treats a failure as "first visit" when it is
// `err instanceof ApiError && err.isNotFound` (page.tsx:178); a plain Error
// takes the GENERIC failure branch instead, so the test could have passed
// while the page was actually showing an error banner rather than the
// first-run state it claims to describe.
// ApiError is imported INSIDE the factory on purpose. vi.mock is hoisted above
// every import in the file, so a top-level `import { ApiError }` is still in
// its temporal dead zone when the factory runs — vitest reports "Cannot access
// '__vi_import_3__' before initialization" and the whole suite fails to load.
vi.mock("@/lib/api", async (importOriginal) => {
  const { ApiError } = await import("@/lib/api-error");
  return {
    ...(await importOriginal<typeof import("@/lib/api")>()),
    getProfile: vi.fn().mockRejectedValue(new ApiError(404, "Not Found")),
  };
});

describe("ProfilePage — first run, no profile yet", () => {
  it("offers the CV uploader and none of the profile-only actions", async () => {
    render(<ProfilePage />);

    // The uploader is the point of the page on a first visit.
    await waitFor(() =>
      expect(screen.getByText(/drop your cv here/i)).toBeInTheDocument(),
    );

    expect(screen.queryByRole("button", { name: /export json resume/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /history/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /clear profile/i })).toBeNull();

    // The meter still reports the empty state honestly (rule #29: an empty
    // shelf says "empty", it does not hide itself).
    expect(screen.getByText("No profile")).toBeInTheDocument();
    expect(screen.getByText("0%")).toBeInTheDocument();

    // ...and this is the FIRST-VISIT state, not the generic failure banner.
    // Without this the test would still pass if the 404 fell through to the
    // error path, which shows an entirely different page.
    expect(screen.queryByText(/something went wrong/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /try again/i })).toBeNull();
  });
});
