import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// The Job360 browser agent, slice 2 (docs/product/BROWSER_AGENT.md): the user signs in to a job
// site themselves in a live picture of their cloud browser; Job360 keeps only the site's cookie.

const getBrowserSignIns = vi.fn();
const forgetBrowserSignIn = vi.fn();
const browserLive = vi.fn();
vi.mock("@/lib/api", () => ({
  getBrowserSignIns: (...a: unknown[]) => getBrowserSignIns(...a),
  forgetBrowserSignIn: (...a: unknown[]) => forgetBrowserSignIn(...a),
  browserLive: (...a: unknown[]) => browserLive(...a),
}));
const success = vi.fn();
const error = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (m: string) => success(m), error: (m: string) => error(m) } }));

import JobSitesPage from "../page";

const SHOT = {
  status: "ok", url: "https://jobs.acme.com/login", title: "Sign in - Acme",
  width: 1280, height: 900, image: "data:image/jpeg;base64,AAAA",
};

beforeEach(() => {
  getBrowserSignIns.mockReset().mockResolvedValue({ enabled: true, sites: [] });
  forgetBrowserSignIn.mockReset().mockResolvedValue({ forgotten: "jobs.acme.com" });
  browserLive.mockReset().mockResolvedValue(SHOT);
  success.mockReset();
  error.mockReset();
});

describe("Job sites", () => {
  it("says the browser is off when the server has not turned it on", async () => {
    getBrowserSignIns.mockResolvedValue({ enabled: false, sites: [] });
    render(<JobSitesPage />);
    expect(await screen.findByTestId("browser-off")).toHaveTextContent(/not turned on/i);
    expect(screen.queryByLabelText(/sign-in page/i)).toBeNull();
  });

  it("starts a sign-in, sends what is typed to the page and clears the box, then keeps it", async () => {
    render(<JobSitesPage />);
    fireEvent.change(await screen.findByLabelText(/sign-in page/i), { target: { value: "jobs.acme.com/login" } });
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await screen.findByTestId("live-view");
    expect(browserLive).toHaveBeenCalledWith({ action: "start", url: "https://jobs.acme.com/login" });

    const box = screen.getByLabelText(/type into the box/i);
    expect(box).toHaveAttribute("type", "password"); // hidden by default: it may be a password
    fireEvent.change(box, { target: { value: "s3cret" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(browserLive).toHaveBeenCalledWith({ action: "type", text: "s3cret" }));
    expect(box).toHaveValue("");

    browserLive.mockResolvedValue({ ...SHOT, kept: { "jobs.acme.com": 2 } });
    getBrowserSignIns.mockResolvedValue({
      enabled: true, sites: [{ site_host: "jobs.acme.com", cookie_count: 2, updated_at: "2026-10-19T10:00:00Z" }],
    });
    fireEvent.click(screen.getByRole("button", { name: /keep me signed in/i }));
    await waitFor(() => expect(success).toHaveBeenCalledWith("Kept you signed in to jobs.acme.com."));
    expect(await screen.findByText("jobs.acme.com")).toBeInTheDocument();
  });

  it("a click on the picture is sent in the browser's own coordinates", async () => {
    render(<JobSitesPage />);
    fireEvent.change(await screen.findByLabelText(/sign-in page/i), { target: { value: "https://jobs.acme.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    const picture = await screen.findByRole("button", { name: /cloud browser showing/i });
    picture.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 640, height: 450, right: 640, bottom: 450, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
    fireEvent.click(picture, { clientX: 320, clientY: 225, detail: 1 });
    await waitFor(() => expect(browserLive).toHaveBeenCalledWith({ action: "click", x: 640, y: 450 }));
  });

  it("refreshes the picture only while the tab is shown, and stops after Keep", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<JobSitesPage />);
      fireEvent.change(await screen.findByLabelText(/sign-in page/i), { target: { value: "https://jobs.acme.com" } });
      fireEvent.click(screen.getByRole("button", { name: "Start" }));
      await screen.findByTestId("live-view");
      const shots = () => browserLive.mock.calls.filter(([a]) => a.action === "shot").length;

      await vi.advanceTimersByTimeAsync(1600);
      expect(shots()).toBe(1);

      const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
      await vi.advanceTimersByTimeAsync(4600);
      expect(shots()).toBe(1); // a hidden tab does not keep the browser busy
      visibility.mockRestore();

      browserLive.mockResolvedValue({ ...SHOT, kept: { "jobs.acme.com": 2 } });
      fireEvent.click(screen.getByRole("button", { name: /keep me signed in/i }));
      await waitFor(() => expect(success).toHaveBeenCalled());
      const afterKeep = shots();
      await vi.advanceTimersByTimeAsync(4600);
      expect(shots()).toBe(afterKeep);
    } finally {
      vi.useRealTimers();
    }
  });

  it("forgets a saved site", async () => {
    getBrowserSignIns.mockResolvedValue({
      enabled: true, sites: [{ site_host: "jobs.acme.com", cookie_count: 2, updated_at: "2026-10-19T10:00:00Z" }],
    });
    render(<JobSitesPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Forget" }));
    await waitFor(() => expect(forgetBrowserSignIn).toHaveBeenCalledWith("jobs.acme.com"));
  });

  it("shows the server's refusal in words", async () => {
    browserLive.mockResolvedValue({ status: "refused", reason: "blocked_address", detail: "Only public web pages." });
    render(<JobSitesPage />);
    fireEvent.change(await screen.findByLabelText(/sign-in page/i), { target: { value: "http://10.0.0.1" } });
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    await waitFor(() => expect(error).toHaveBeenCalledWith("Only public web pages."));
    expect(screen.queryByTestId("live-view")).toBeNull();
  });
});
