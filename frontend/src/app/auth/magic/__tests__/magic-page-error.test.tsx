/**
 * Two invariants for the magic-link landing page:
 *
 * 1. SCANNER SAFETY — rendering the page must NEVER consume the token.
 *    Corporate email scanners (Outlook SafeLinks etc.) open links before the
 *    human does; the old auto-consume-on-mount burned the single-use token
 *    and locked the real user out with "link expired". Consumption must only
 *    happen on an explicit button click.
 *
 * 2. H11 — auth pages must never render a raw ApiError string like
 *    "API error 429: too many requests" (the `Error.message` format, which
 *    bakes the HTTP status into the text) — they must go through
 *    friendlyAuthError, which strips that prefix. Note: friendlyAuthError
 *    still shows the backend's own `detail` verbatim for status codes it
 *    doesn't specially map (e.g. 400) — that's intentional (mirrors
 *    apiErrorMessage), since a backend detail is meant to be human-readable.
 *    What must never leak is the raw "API error NNN: …" wrapper string.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "@/lib/api-error";
import MagicLinkPage from "../page";

const mockReplace = vi.fn();
const mockGet = vi.fn<(key: string) => string | null>();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
  useSearchParams: () => ({ get: mockGet }),
}));

const consumeMagicLinkMock = vi.fn();

vi.mock("@/lib/api", () => ({
  consumeMagicLink: (...args: unknown[]) => consumeMagicLinkMock(...args),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

describe("MagicLinkPage — scanner safety (no consume without a click)", () => {
  it("does NOT consume the token on render", async () => {
    mockGet.mockReturnValue("good-token");

    render(<MagicLinkPage />);

    // The sign-in button must be offered instead of auto-consuming.
    expect(
      await screen.findByRole("button", { name: /sign in/i })
    ).toBeInTheDocument();
    expect(consumeMagicLinkMock).not.toHaveBeenCalled();
  });

  it("consumes the token and redirects to /dashboard only after the click", async () => {
    mockGet.mockReturnValue("good-token");
    consumeMagicLinkMock.mockResolvedValue(undefined);

    render(<MagicLinkPage />);
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/dashboard"));
    expect(consumeMagicLinkMock).toHaveBeenCalledTimes(1);
    expect(consumeMagicLinkMock).toHaveBeenCalledWith("good-token");
  });

  it("shows the missing-token error without a sign-in button when the URL has no token", () => {
    mockGet.mockReturnValue(null);

    render(<MagicLinkPage />);

    expect(screen.getByText(/token missing/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /sign in/i })
    ).not.toBeInTheDocument();
    expect(consumeMagicLinkMock).not.toHaveBeenCalled();
  });
});

describe("MagicLinkPage — H11 friendly error rendering", () => {
  it("never renders the raw 'API error NNN:' prefix for a mapped status (429)", async () => {
    mockGet.mockReturnValue("bad-token");
    consumeMagicLinkMock.mockRejectedValue(new ApiError(429, "rate limited"));

    render(<MagicLinkPage />);
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    // 429 is one of friendlyAuthError's specially-mapped statuses.
    await waitFor(() => {
      expect(screen.getByText(/too many attempts/i)).toBeInTheDocument();
    });

    expect(screen.queryByText(/API error 429/i)).not.toBeInTheDocument();
  });

  it("shows the backend detail (not the raw prefix) for an unmapped status (400)", async () => {
    mockGet.mockReturnValue("bad-token");
    consumeMagicLinkMock.mockRejectedValue(
      new ApiError(400, "token row not found in magic_link_tokens table")
    );

    render(<MagicLinkPage />);
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/token row not found in magic_link_tokens table/i)
      ).toBeInTheDocument();
    });

    // The raw "API error 400: …" wrapper string must never reach the DOM,
    // even though the clean detail text is shown.
    expect(screen.queryByText(/API error 400/i)).not.toBeInTheDocument();
  });
});
