/**
 * U1 (point 3, S1) — after the magic link is consumed, the AuthProvider must
 * learn who signed in BEFORE the page navigates; otherwise the app shell keeps
 * the signed-out frame (and the live poller never starts) until a focus event.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MagicLinkPage from "../page";

const mockReplace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
  useSearchParams: () => ({ get: (k: string) => (k === "token" ? "tok" : null) }),
}));
const USER = { id: "u1", email: "u1@example.com" };
vi.mock("@/lib/api", () => ({ consumeMagicLink: vi.fn(async () => USER) }));
const mockSignedIn = vi.fn();
vi.mock("@/components/layout/AuthProvider", () => ({
  useAuth: () => ({ signedIn: mockSignedIn }),
}));

describe("magic page — tells the provider who signed in", () => {
  it("calls signedIn(user) before navigating", async () => {
    render(<MagicLinkPage />);
    await userEvent.click(screen.getByRole("button", { name: /sign in to job360/i }));
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/needs-you"));
    expect(mockSignedIn).toHaveBeenCalledWith(USER);
    expect(mockSignedIn.mock.invocationCallOrder[0]).toBeLessThan(
      mockReplace.mock.invocationCallOrder[0]
    );
  });
});
