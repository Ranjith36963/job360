/**
 * L7 — ProfilePage used to detect "no profile yet" by string-matching
 * `err.message.includes("404")`, which is fragile (any error whose message
 * happens to contain "404" is silently swallowed as "no profile"). Fixed to
 * check the typed `err instanceof ApiError && err.isNotFound` instead.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ApiError } from "@/lib/api-error";
import ProfilePage from "../page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getProfile: vi.fn(),
}));

describe("ProfilePage — L7 typed 404 handling", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it("treats a real ApiError(404) as 'no profile yet' — no error banner", async () => {
    const api = await import("@/lib/api");
    vi.mocked(api.getProfile).mockRejectedValueOnce(new ApiError(404, "no profile"));

    render(<ProfilePage />);

    await waitFor(() => {
      expect(screen.getByText(/no profile/i)).toBeInTheDocument();
    });

    expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument();
  });

  it("shows the error banner for a genuine 500, distinct from the 404 empty case", async () => {
    const api = await import("@/lib/api");
    vi.mocked(api.getProfile).mockRejectedValueOnce(new ApiError(500, "database is down"));

    render(<ProfilePage />);

    await waitFor(() => {
      expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/database is down/i)).toBeInTheDocument();
  });

  it("does not misclassify a non-404 error whose message happens to contain '404'", async () => {
    // Old behaviour: err.message.includes("404") would have swallowed this
    // as "no profile yet" even though it's a genuine server error. A plain
    // Error (not ApiError) must always take the error-banner branch.
    const api = await import("@/lib/api");
    vi.mocked(api.getProfile).mockRejectedValueOnce(
      new Error("upstream returned HTTP 404-like payload but request actually 500'd")
    );

    render(<ProfilePage />);

    await waitFor(() => {
      expect(screen.getByText(/something went wrong/i)).toBeInTheDocument();
    });
  });
});
