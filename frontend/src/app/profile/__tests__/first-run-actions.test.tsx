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

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getProfile: vi.fn().mockRejectedValue(
    Object.assign(new Error("Not Found"), { status: 404 }),
  ),
}));

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
  });
});
