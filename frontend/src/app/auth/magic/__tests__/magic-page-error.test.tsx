/**
 * H11 — auth pages must never render a raw ApiError string like
 * "API error 429: too many requests" (the `Error.message` format, which
 * bakes the HTTP status into the text) — they must go through
 * friendlyAuthError, which strips that prefix. Note: friendlyAuthError still
 * shows the backend's own `detail` verbatim for status codes it doesn't
 * specially map (e.g. 400) — that's intentional (mirrors apiErrorMessage),
 * since a backend detail is meant to be human-readable. What must never
 * leak is the raw "API error NNN: …" wrapper string.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

describe("MagicLinkPage — H11 friendly error rendering", () => {
  it("never renders the raw 'API error NNN:' prefix for a mapped status (429)", async () => {
    mockGet.mockReturnValue("bad-token");
    consumeMagicLinkMock.mockRejectedValue(new ApiError(429, "rate limited"));

    render(<MagicLinkPage />);

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

    await waitFor(() => {
      expect(
        screen.getByText(/token row not found in magic_link_tokens table/i)
      ).toBeInTheDocument();
    });

    // The raw "API error 400: …" wrapper string must never reach the DOM,
    // even though the clean detail text is shown.
    expect(screen.queryByText(/API error 400/i)).not.toBeInTheDocument();
  });

  it("redirects to /dashboard on success", async () => {
    mockGet.mockReturnValue("good-token");
    consumeMagicLinkMock.mockResolvedValue(undefined);

    render(<MagicLinkPage />);

    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/dashboard"));
  });
});
