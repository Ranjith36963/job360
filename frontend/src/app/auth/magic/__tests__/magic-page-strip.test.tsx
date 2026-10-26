import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MagicLinkPage from "../page";

const params: Record<string, string> = { token: "fake-tok", next: "/applications" };
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => ({ get: (k: string) => params[k] ?? null }),
}));
const consume = vi.fn();
vi.mock("@/lib/api", () => ({ consumeMagicLink: (...a: unknown[]) => consume(...a) }));

beforeEach(() => {
  consume.mockReset().mockResolvedValue(undefined);
  window.history.replaceState(null, "", "/auth/magic?token=fake-tok&next=%2Fapplications");
});

describe("magic page", () => {
  it("removes the token from the address bar but still consumes it on click", async () => {
    render(<MagicLinkPage />);
    expect(window.location.search).toBe("");
    expect(window.location.pathname).toBe("/auth/magic");
    await userEvent.click(screen.getByRole("button", { name: /sign in to job360/i }));
    expect(consume).toHaveBeenCalledWith("fake-tok");
  });
});
