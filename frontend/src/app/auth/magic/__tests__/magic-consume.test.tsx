/**
 * Magic-link consume page — token handling states.
 *
 * Verifies:
 * 1. Missing ?token → error shown, API never called (no pointless request).
 * 2. Successful consume → router.replace("/dashboard").
 * 3. Failed consume → error message + "Back to login" escape hatch.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import MagicLinkPage from "../page";

const mockReplace = vi.fn();
const mockGet = vi.fn<(key: string) => string | null>();
const mockConsume = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
  useSearchParams: () => ({ get: mockGet }),
}));

vi.mock("@/lib/api", () => ({
  consumeMagicLink: (...args: unknown[]) => mockConsume(...args),
}));

describe("MagicLinkPage — token consume", () => {
  beforeEach(() => {
    mockReplace.mockClear();
    mockGet.mockReset();
    mockConsume.mockReset();
  });

  it("shows an error and never calls the API when the token is missing", async () => {
    mockGet.mockReturnValue(null);
    render(<MagicLinkPage />);
    expect(await screen.findByText(/token missing/i)).toBeTruthy();
    expect(mockConsume).not.toHaveBeenCalled();
  });

  it("redirects to /dashboard on a successful consume", async () => {
    mockGet.mockReturnValue("good-token");
    mockConsume.mockResolvedValue({});
    render(<MagicLinkPage />);
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/dashboard"));
    expect(mockConsume).toHaveBeenCalledWith("good-token");
  });

  it("shows the error message and a back-to-login link when consume fails", async () => {
    mockGet.mockReturnValue("bad-token");
    mockConsume.mockRejectedValue(new Error("invalid or expired link"));
    render(<MagicLinkPage />);
    expect(await screen.findByText(/invalid or expired link/i)).toBeTruthy();
    expect(screen.getByText(/back to login/i)).toBeTruthy();
    expect(mockReplace).not.toHaveBeenCalled();
  });
});
