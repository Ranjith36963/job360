/**
 * F-01: ?next redirect tests for LoginPage
 *
 * Verifies:
 * 1. safeNext() accepts valid internal paths and rejects external/protocol-relative URLs.
 * 2. LoginForm redirects to the ?next path when it is a valid internal path.
 * 3. LoginForm falls back to /dashboard when ?next is an external URL (open-redirect guard).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Suspense } from "react";
import LoginPage from "../page";
import { safeNext } from "@/lib/safe-next";
import { requestMagicLink } from "@/lib/api";

// ---------------------------------------------------------------------------
// Mocks — hoisted before any module evaluation via vi.mock
// ---------------------------------------------------------------------------

const mockPush = vi.fn();
const mockGet = vi.fn<(key: string) => string | null>();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
  useSearchParams: () => ({ get: mockGet }),
}));

vi.mock("@/lib/api", () => ({
  login: vi.fn().mockResolvedValue({}),
  requestMagicLink: vi.fn().mockResolvedValue(undefined),
}));

// ---------------------------------------------------------------------------
// safeNext — pure function unit tests (no rendering needed)
// ---------------------------------------------------------------------------

describe("safeNext()", () => {
  it("returns /dashboard for null", () => {
    expect(safeNext(null)).toBe("/dashboard");
  });

  it("returns /dashboard for empty string", () => {
    expect(safeNext("")).toBe("/dashboard");
  });

  it("returns /dashboard for an external URL", () => {
    expect(safeNext("https://evil.com")).toBe("/dashboard");
  });

  it("returns /dashboard for a protocol-relative URL", () => {
    expect(safeNext("//evil.com/steal")).toBe("/dashboard");
  });

  // The WHATWG URL parser treats "\" as "/" for http(s) and strips tab/CR/LF
  // before parsing — each of these resolves to https://evil.com/ in a browser.
  it.each(["/\\evil.com", "/\t/evil.com", "/\n/evil.com", "/\r/evil.com"])(
    "returns /dashboard for the URL-parser trick %j",
    (p) => {
      expect(safeNext(p)).toBe("/dashboard");
      expect(new URL(p, "https://job360.uk").origin).not.toBe("https://job360.uk");
    },
  );

  // A backslash later in the path stays same-origin, but a path is plain
  // characters or it is not a path — rejected all the same.
  it("returns /dashboard for a backslash anywhere in the path", () => {
    expect(safeNext("/ok\\evil.com")).toBe("/dashboard");
  });

  it("returns the path for a valid internal path", () => {
    expect(safeNext("/pipeline")).toBe("/pipeline");
  });

  it("returns the path for /dashboard", () => {
    expect(safeNext("/dashboard")).toBe("/dashboard");
  });

  it("returns the path for deeply-nested routes", () => {
    expect(safeNext("/jobs/123")).toBe("/jobs/123");
  });
});

// ---------------------------------------------------------------------------
// LoginPage integration — renders inside Suspense and submits the form
// ---------------------------------------------------------------------------

function renderPage() {
  return render(
    <Suspense fallback={null}>
      <LoginPage />
    </Suspense>
  );
}

async function fillAndSubmit() {
  // Magic-link is the default view; switch to the password form first.
  fireEvent.click(screen.getByRole("button", { name: /use password instead/i }));
  fireEvent.change(screen.getByLabelText(/email/i), {
    target: { value: "user@example.com" },
  });
  fireEvent.change(screen.getByLabelText(/password/i), {
    target: { value: "password123" },
  });
  const form = screen.getByRole("button", { name: /sign in/i }).closest("form")!;
  fireEvent.submit(form);
}

describe("LoginPage — ?next redirect", () => {
  beforeEach(() => {
    mockPush.mockClear();
    mockGet.mockReset();
  });

  it("redirects to ?next=/pipeline after successful login", async () => {
    mockGet.mockReturnValue("/pipeline");
    renderPage();
    await fillAndSubmit();
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/pipeline"));
  });

  it("falls back to /dashboard when ?next=https://evil.com", async () => {
    mockGet.mockReturnValue("https://evil.com");
    renderPage();
    await fillAndSubmit();
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/dashboard"));
  });

  it("falls back to /dashboard when ?next is null", async () => {
    mockGet.mockReturnValue(null);
    renderPage();
    await fillAndSubmit();
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/dashboard"));
  });
});

// ---------------------------------------------------------------------------
// Magic-link form — passes `next` through to requestMagicLink (spec R9:
// the emailed link must carry `next` so /auth/magic can send the user back
// to e.g. an OAuth consent page instead of always landing on /dashboard).
// ---------------------------------------------------------------------------

describe("MagicLinkForm — next passthrough", () => {
  beforeEach(() => {
    mockGet.mockReset();
    vi.mocked(requestMagicLink).mockClear();
  });

  async function fillAndSubmitMagic() {
    // Magic-link is the default view — no toggle needed.
    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: "user@example.com" },
    });
    const form = screen
      .getByRole("button", { name: /email me a sign-in link/i })
      .closest("form")!;
    fireEvent.submit(form);
  }

  it("passes a valid ?next to requestMagicLink", async () => {
    mockGet.mockReturnValue("/oauth/consent/abc");
    renderPage();
    await fillAndSubmitMagic();
    await waitFor(() =>
      expect(requestMagicLink).toHaveBeenCalledWith(
        "user@example.com",
        "/oauth/consent/abc"
      )
    );
  });

  it("omits next when absent", async () => {
    mockGet.mockReturnValue(null);
    renderPage();
    await fillAndSubmitMagic();
    await waitFor(() =>
      expect(requestMagicLink).toHaveBeenCalledWith("user@example.com", undefined)
    );
  });

  it("does not forward an external ?next", async () => {
    mockGet.mockReturnValue("https://evil.com");
    renderPage();
    await fillAndSubmitMagic();
    await waitFor(() =>
      expect(requestMagicLink).toHaveBeenCalledWith("user@example.com", undefined)
    );
  });
});
