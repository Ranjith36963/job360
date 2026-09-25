import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AccountSettingsPage from "../page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const mockChangePassword = vi.fn();
const mockChangeEmail = vi.fn();
const mockDeleteAccount = vi.fn();
const mockLogout = vi.fn();
// Default: no signed-in user data needed by these unrelated password/email/
// delete tests — TimezoneCard's mount-time `me()` call just resolves to null.
const mockMe = vi.fn().mockResolvedValue(null);
const mockSetTimezone = vi.fn();
const mockResendVerificationEmail = vi.fn();

vi.mock("@/lib/api", () => ({
  changePassword: (...args: unknown[]) => mockChangePassword(...args),
  changeEmail: (...args: unknown[]) => mockChangeEmail(...args),
  deleteAccount: (...args: unknown[]) => mockDeleteAccount(...args),
  logout: (...args: unknown[]) => mockLogout(...args),
  // TimezoneCard / VerifyEmailCard also live on this page — stub them so
  // mounting the whole page in these unrelated tests never throws.
  me: (...args: unknown[]) => mockMe(...args),
  setTimezone: (...args: unknown[]) => mockSetTimezone(...args),
  resendVerificationEmail: (...args: unknown[]) => mockResendVerificationEmail(...args),
}));

/** Scope queries to the <form> containing a submit button with the given name. */
function withinForm(submitButtonName: RegExp) {
  const btn = screen.getByRole("button", { name: submitButtonName });
  return within(btn.closest("form")!);
}

function setup() {
  const user = userEvent.setup();
  render(<AccountSettingsPage />);
  return { user };
}

// ---------------------------------------------------------------------------
// Change Password
// ---------------------------------------------------------------------------

describe("ChangePasswordCard", () => {
  beforeEach(() => {
    mockChangePassword.mockReset().mockResolvedValue(undefined);
  });

  it("blocks submit and shows errors when all fields empty", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: /update password/i }));
    const form = withinForm(/update password/i);

    expect(await form.findByText(/current password is required/i)).toBeInTheDocument();
    expect(form.getByText(/password must be at least 8/i)).toBeInTheDocument();
    expect(mockChangePassword).not.toHaveBeenCalled();
  });

  it("shows error when new password is too short", async () => {
    const { user } = setup();
    const form = withinForm(/update password/i);
    await user.type(form.getByLabelText(/current password/i), "secret");
    await user.type(form.getByLabelText(/^new password/i), "short");
    await user.type(form.getByLabelText(/confirm new password/i), "short");
    await user.click(form.getByRole("button", { name: /update password/i }));

    expect(await form.findByText(/password must be at least 8/i)).toBeInTheDocument();
    expect(mockChangePassword).not.toHaveBeenCalled();
  });

  it("shows mismatch error when passwords differ", async () => {
    const { user } = setup();
    const form = withinForm(/update password/i);
    await user.type(form.getByLabelText(/current password/i), "secret");
    await user.type(form.getByLabelText(/^new password/i), "newpassword1");
    await user.type(form.getByLabelText(/confirm new password/i), "different");
    await user.click(form.getByRole("button", { name: /update password/i }));

    expect(await form.findByText(/passwords do not match/i)).toBeInTheDocument();
    expect(mockChangePassword).not.toHaveBeenCalled();
  });

  it("calls changePassword and shows success on valid submit", async () => {
    const { user } = setup();
    const form = withinForm(/update password/i);
    await user.type(form.getByLabelText(/current password/i), "oldpass");
    await user.type(form.getByLabelText(/^new password/i), "newpassword1");
    await user.type(form.getByLabelText(/confirm new password/i), "newpassword1");
    await user.click(form.getByRole("button", { name: /update password/i }));

    await waitFor(() =>
      expect(mockChangePassword).toHaveBeenCalledWith("oldpass", "newpassword1")
    );
    expect(await form.findByText(/password updated successfully/i)).toBeInTheDocument();
  });

  it("shows server error when API rejects", async () => {
    mockChangePassword.mockRejectedValue(new Error("Incorrect current password."));
    const { user } = setup();
    const form = withinForm(/update password/i);
    await user.type(form.getByLabelText(/current password/i), "wrong");
    await user.type(form.getByLabelText(/^new password/i), "newpassword1");
    await user.type(form.getByLabelText(/confirm new password/i), "newpassword1");
    await user.click(form.getByRole("button", { name: /update password/i }));

    expect(await form.findByText(/incorrect current password/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Change Email
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Time zone (owner decision, 2026-09-25)
// ---------------------------------------------------------------------------

describe("TimezoneCard", () => {
  const originalDateTimeFormat = Intl.DateTimeFormat;

  beforeEach(() => {
    mockMe.mockReset();
    mockSetTimezone.mockReset().mockImplementation((tz: string) =>
      Promise.resolve({ timezone: tz })
    );
  });

  afterEach(() => {
    Intl.DateTimeFormat = originalDateTimeFormat;
  });

  function mockBrowserTimezone(zone: string) {
    // @ts-expect-error -- test-only stub of a subset of Intl.DateTimeFormat
    Intl.DateTimeFormat = () => ({ resolvedOptions: () => ({ timeZone: zone }) });
  }

  it("prefills the browser zone when the saved value is the untouched UTC default, but does not save it", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@b.com", timezone: "UTC" });
    mockBrowserTimezone("Europe/London");

    render(<AccountSettingsPage />);

    await waitFor(() =>
      expect(screen.getByTestId("timezone-input")).toHaveValue("Europe/London")
    );
    expect(screen.getByTestId("timezone-detected")).toBeInTheDocument();
    expect(mockSetTimezone).not.toHaveBeenCalled();
  });

  it("shows the saved value untouched when it is not the default", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@b.com", timezone: "America/New_York" });
    mockBrowserTimezone("Europe/London");

    render(<AccountSettingsPage />);

    await waitFor(() =>
      expect(screen.getByTestId("timezone-input")).toHaveValue("America/New_York")
    );
    expect(screen.queryByTestId("timezone-detected")).toBeNull();
  });

  it("only calls the API when Save is pressed", async () => {
    mockMe.mockResolvedValue({ id: "u1", email: "a@b.com", timezone: "UTC" });
    mockBrowserTimezone("Europe/London");
    const { user } = setup();

    await waitFor(() =>
      expect(screen.getByTestId("timezone-input")).toHaveValue("Europe/London")
    );
    expect(mockSetTimezone).not.toHaveBeenCalled();

    await user.click(screen.getByTestId("timezone-save"));

    await waitFor(() => expect(mockSetTimezone).toHaveBeenCalledWith("Europe/London"));
  });
});

describe("ChangeEmailCard", () => {
  beforeEach(() => {
    mockChangeEmail.mockReset().mockResolvedValue(undefined);
    mockLogout.mockReset().mockResolvedValue(undefined);
  });

  it("blocks submit when fields empty", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: /update email/i }));
    const form = withinForm(/update email/i);

    expect(await form.findByText(/current password is required/i)).toBeInTheDocument();
    expect(mockChangeEmail).not.toHaveBeenCalled();
  });

  it("shows error for invalid email format", async () => {
    const { user } = setup();
    const form = withinForm(/update email/i);
    await user.type(form.getByLabelText(/current password/i), "secret");
    await user.type(form.getByLabelText(/new email address/i), "not-an-email");
    await user.click(form.getByRole("button", { name: /update email/i }));

    expect(await form.findByText(/enter a valid email address/i)).toBeInTheDocument();
    expect(mockChangeEmail).not.toHaveBeenCalled();
  });

  it("calls changeEmail then logout on valid submit", async () => {
    const { user } = setup();
    const form = withinForm(/update email/i);
    await user.type(form.getByLabelText(/current password/i), "secret");
    await user.type(form.getByLabelText(/new email address/i), "new@example.com");
    await user.click(form.getByRole("button", { name: /update email/i }));

    await waitFor(() =>
      expect(mockChangeEmail).toHaveBeenCalledWith("secret", "new@example.com")
    );
    expect(mockLogout).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Change Password card copy — must be truthful about sign-out (rule #26)
// ---------------------------------------------------------------------------

describe("ChangePasswordCard copy", () => {
  it('does NOT say "remain logged in"', () => {
    render(<AccountSettingsPage />);
    expect(screen.queryByText(/remain logged in/i)).not.toBeInTheDocument();
  });

  it('tells the user they will be signed out after changing password', () => {
    render(<AccountSettingsPage />);
    expect(screen.getByText(/signed out on all devices/i)).toBeInTheDocument();
    expect(screen.getByText(/sign in again/i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Delete Account
// ---------------------------------------------------------------------------

describe("DeleteAccountCard", () => {
  beforeEach(() => {
    mockDeleteAccount.mockReset().mockResolvedValue(undefined);
  });

  it("opens dialog on button click", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: /delete my account/i }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("shows validation error when submitted without typing DELETE", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: /delete my account/i }));

    const dialog = await screen.findByRole("dialog");
    // Fill the password field but leave the confirm text wrong
    await user.type(within(dialog).getByLabelText(/current password/i), "mypassword");
    const input = within(dialog).getByLabelText(/type DELETE to confirm/i);
    await user.type(input, "wrong");
    await user.keyboard("{Enter}");

    // Error appears as role=alert — find the one with "delete" text
    await waitFor(() => {
      const alerts = screen.getAllByRole("alert");
      expect(alerts.some((el) => /type DELETE to confirm/i.test(el.textContent ?? ""))).toBe(true);
    });
    expect(mockDeleteAccount).not.toHaveBeenCalled();
  });

  it("shows validation error when password is missing", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: /delete my account/i }));

    const dialog = await screen.findByRole("dialog");
    // Type DELETE in the confirm field but leave the password blank
    await user.type(within(dialog).getByLabelText(/type DELETE to confirm/i), "DELETE");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      const alerts = screen.getAllByRole("alert");
      expect(alerts.some((el) => /enter your password/i.test(el.textContent ?? ""))).toBe(true);
    });
    expect(mockDeleteAccount).not.toHaveBeenCalled();
  });

  it("calls deleteAccount with the password when DELETE typed and form submitted", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: /delete my account/i }));

    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/current password/i), "mypassword");
    await user.type(within(dialog).getByLabelText(/type DELETE to confirm/i), "DELETE");

    const submitBtn = within(dialog).getByRole("button", { name: /delete my account/i });
    await user.click(submitBtn);

    await waitFor(() => expect(mockDeleteAccount).toHaveBeenCalledWith("mypassword"));
  });
});
