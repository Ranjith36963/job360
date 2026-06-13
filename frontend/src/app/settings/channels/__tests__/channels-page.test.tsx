import {
  describe,
  it,
  expect,
  vi,
  beforeEach,
  afterEach,
} from "vitest";
import {
  render,
  screen,
  waitFor,
  act,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ChannelsSettingsPage from "../page";

// ---------------------------------------------------------------------------
// Mock next/navigation
// ---------------------------------------------------------------------------
const mockRouterReplace = vi.fn();

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: mockRouterReplace }),
}));

// ---------------------------------------------------------------------------
// Mock sonner toast
// ---------------------------------------------------------------------------
const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();

vi.mock("sonner", () => ({
  toast: {
    success: (...args: unknown[]) => mockToastSuccess(...args),
    error: (...args: unknown[]) => mockToastError(...args),
  },
}));

// ---------------------------------------------------------------------------
// Mock api functions
// ---------------------------------------------------------------------------
const mockGetProviders = vi.fn();
const mockListChannels = vi.fn();
const mockCreateChannel = vi.fn();
const mockDeleteChannel = vi.fn();
const mockTestChannel = vi.fn();
const mockConnectTelegram = vi.fn();
const mockPollTelegram = vi.fn();

vi.mock("@/lib/api", () => ({
  getProviders: (...args: unknown[]) => mockGetProviders(...args),
  listChannels: (...args: unknown[]) => mockListChannels(...args),
  createChannel: (...args: unknown[]) => mockCreateChannel(...args),
  deleteChannel: (...args: unknown[]) => mockDeleteChannel(...args),
  testChannel: (...args: unknown[]) => mockTestChannel(...args),
  connectTelegram: (...args: unknown[]) => mockConnectTelegram(...args),
  pollTelegram: (...args: unknown[]) => mockPollTelegram(...args),
}));

// ---------------------------------------------------------------------------
// Mock window.open / window.location
// ---------------------------------------------------------------------------
const mockWindowOpen = vi.fn();

// ---------------------------------------------------------------------------
// Default data
// ---------------------------------------------------------------------------

const DEFAULT_PROVIDERS = { slack: true, discord: true, telegram: true };
const EMPTY_CHANNELS: unknown[] = [];

function makeChannel(overrides = {}) {
  return {
    id: 1,
    channel_type: "email",
    display_name: "My Email",
    enabled: true,
    connection_status: "connected",
    target_label: null,
    ...overrides,
  };
}

function setupDefaultMocks() {
  mockGetProviders.mockResolvedValue(DEFAULT_PROVIDERS);
  mockListChannels.mockResolvedValue(EMPTY_CHANNELS);
  mockCreateChannel.mockResolvedValue(makeChannel());
  mockDeleteChannel.mockResolvedValue(undefined);
  mockTestChannel.mockResolvedValue({ ok: true, error: null });
}

async function renderPage() {
  let container: ReturnType<typeof render>;
  await act(async () => {
    container = render(<ChannelsSettingsPage />);
  });
  // Wait for providers + channels to load
  await waitFor(() => {
    expect(screen.queryByText(/Loading…/i)).not.toBeInTheDocument();
  });
  return container!;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ChannelsSettingsPage — providers gating", () => {
  beforeEach(() => {
    setupDefaultMocks();
    vi.stubGlobal("open", mockWindowOpen);
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    mockGetProviders.mockReset();
    mockListChannels.mockReset();
    mockConnectTelegram.mockReset();
    mockPollTelegram.mockReset();
    mockToastSuccess.mockReset();
    mockToastError.mockReset();
    mockRouterReplace.mockReset();
    mockWindowOpen.mockReset();
  });

  it("renders all three Connect buttons when all providers are true", async () => {
    await renderPage();
    expect(screen.getByRole("button", { name: /connect slack/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /connect discord/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /connect telegram/i })).toBeInTheDocument();
  });

  it("renders Slack disabled when providers.slack is false", async () => {
    mockGetProviders.mockResolvedValue({
      slack: false,
      discord: true,
      telegram: true,
    });
    await renderPage();
    const slackBtn = screen.getByRole("button", { name: /connect slack/i });
    expect(slackBtn).toBeDisabled();
    // Enabled ones should not be disabled
    expect(screen.getByRole("button", { name: /connect discord/i })).not.toBeDisabled();
  });

  it("renders Discord disabled when providers.discord is false", async () => {
    mockGetProviders.mockResolvedValue({
      slack: true,
      discord: false,
      telegram: true,
    });
    await renderPage();
    const discordBtn = screen.getByRole("button", { name: /connect discord/i });
    expect(discordBtn).toBeDisabled();
  });

  it("shows 'not configured' note when all providers are false", async () => {
    mockGetProviders.mockResolvedValue({
      slack: false,
      discord: false,
      telegram: false,
    });
    await renderPage();
    expect(
      screen.getByText(
        /Chat connects \(Slack \/ Discord \/ Telegram\) aren't configured on this server yet\./i
      )
    ).toBeInTheDocument();
    // No connect buttons should appear
    expect(
      screen.queryByRole("button", { name: /connect slack/i })
    ).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Email add
// ---------------------------------------------------------------------------

describe("ChannelsSettingsPage — email add form", () => {
  beforeEach(() => {
    setupDefaultMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    mockGetProviders.mockReset();
    mockListChannels.mockReset();
    mockCreateChannel.mockReset();
    mockToastSuccess.mockReset();
    mockToastError.mockReset();
  });

  it("blocks submit with inline error when email is invalid", async () => {
    const user = userEvent.setup();
    await renderPage();

    const emailInput = screen.getByLabelText(/your email address/i);
    await user.type(emailInput, "not-an-email");
    // Submit via the form that wraps this input
    await user.keyboard("{Enter}");

    expect(
      await screen.findByText(/please enter a valid email address/i)
    ).toBeInTheDocument();
    expect(mockCreateChannel).not.toHaveBeenCalled();
  });

  it("calls createChannel with channel_type email on valid submit", async () => {
    const user = userEvent.setup();
    await renderPage();

    const emailInput = screen.getByLabelText(/your email address/i);
    await user.type(emailInput, "test@example.com");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(mockCreateChannel).toHaveBeenCalledWith(
        expect.objectContaining({
          channel_type: "email",
          credential: "test@example.com",
        })
      );
    });
  });

  it("shows error toast when createChannel returns 503", async () => {
    mockCreateChannel.mockRejectedValue(new Error("email delivery isn't configured"));
    const user = userEvent.setup();
    await renderPage();

    const emailInput = screen.getByLabelText(/your email address/i);
    await user.type(emailInput, "test@example.com");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith(
        expect.stringContaining("email delivery isn't configured")
      );
    });
  });
});

// ---------------------------------------------------------------------------
// Webhook add
// ---------------------------------------------------------------------------

describe("ChannelsSettingsPage — webhook add form", () => {
  beforeEach(() => {
    setupDefaultMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    mockGetProviders.mockReset();
    mockListChannels.mockReset();
    mockCreateChannel.mockReset();
    mockToastSuccess.mockReset();
  });

  it("calls createChannel with channel_type webhook on valid URL", async () => {
    const user = userEvent.setup();
    await renderPage();

    const webhookInput = screen.getByLabelText(/webhook url/i);
    await user.type(webhookInput, "https://example.com/hook");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(mockCreateChannel).toHaveBeenCalledWith(
        expect.objectContaining({
          channel_type: "webhook",
          credential: "https://example.com/hook",
        })
      );
    });
  });

  it("blocks submit with inline error when URL does not start with http", async () => {
    const user = userEvent.setup();
    await renderPage();

    const webhookInput = screen.getByLabelText(/webhook url/i);
    await user.type(webhookInput, "ftp://bad-url");
    await user.keyboard("{Enter}");

    expect(
      await screen.findByText(/must start with https:\/\/ or http:\/\//i)
    ).toBeInTheDocument();
    expect(mockCreateChannel).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Telegram polling
// ---------------------------------------------------------------------------

describe("ChannelsSettingsPage — Telegram connect + poll", () => {
  beforeEach(() => {
    setupDefaultMocks();
    vi.stubGlobal("open", mockWindowOpen);
    mockConnectTelegram.mockResolvedValue({
      deep_link: "https://t.me/bot?start=abc",
      state: "abc123",
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    mockGetProviders.mockReset();
    mockListChannels.mockReset();
    mockConnectTelegram.mockReset();
    mockPollTelegram.mockReset();
    mockToastSuccess.mockReset();
    mockWindowOpen.mockReset();
  });

  it("calls connectTelegram then window.open on button click", async () => {
    const user = userEvent.setup();
    // Poll never resolves connected so interval stays running
    mockPollTelegram.mockResolvedValue({ connected: false, target_label: null });

    await act(async () => {
      render(<ChannelsSettingsPage />);
    });
    await waitFor(() =>
      expect(screen.queryByText(/Loading…/i)).not.toBeInTheDocument()
    );

    await user.click(screen.getByRole("button", { name: /connect telegram/i }));

    await waitFor(() => {
      expect(mockConnectTelegram).toHaveBeenCalledTimes(1);
      expect(mockWindowOpen).toHaveBeenCalledWith(
        "https://t.me/bot?start=abc",
        "_blank"
      );
    });
  }, 10000);

  it("shows waiting state after clicking Connect Telegram", async () => {
    const user = userEvent.setup();
    mockPollTelegram.mockResolvedValue({ connected: false, target_label: null });

    await act(async () => {
      render(<ChannelsSettingsPage />);
    });
    await waitFor(() =>
      expect(screen.queryByText(/Loading…/i)).not.toBeInTheDocument()
    );

    await user.click(screen.getByRole("button", { name: /connect telegram/i }));

    // Waiting message should appear immediately after click
    await waitFor(() => {
      expect(
        screen.getByRole("button", {
          name: /waiting for you to tap start in telegram/i,
        })
      ).toBeInTheDocument();
    });
  }, 10000);

  it("fires success toast when poll eventually returns connected", async () => {
    // Approach: replace setInterval globally so we control when the poll fires.
    // The mock setInterval captures the callback, we invoke it manually,
    // then wait for the async poll promise chain to settle.
    const realSetInterval = window.setInterval.bind(window);
    const realClearInterval = window.clearInterval.bind(window);

    let capturedCallback: (() => void) | null = null;
    let fakeTimerId: ReturnType<typeof setInterval>;

    const setIntervalSpy = vi
      .spyOn(window, "setInterval")
      .mockImplementation((fn: TimerHandler, .../* _delay, _args */rest: unknown[]) => {
        void rest;
        capturedCallback = fn as () => void;
        // Use real timer so clearInterval(fakeTimerId) works
        fakeTimerId = realSetInterval(() => {/* noop */}, 999999);
        return fakeTimerId;
      });

    mockPollTelegram.mockResolvedValue({ connected: true, target_label: "@jobs" });

    const user = userEvent.setup();
    await act(async () => {
      render(<ChannelsSettingsPage />);
    });
    await waitFor(() =>
      expect(screen.queryByText(/Loading…/i)).not.toBeInTheDocument()
    );

    await user.click(screen.getByRole("button", { name: /connect telegram/i }));

    // Manually fire the poll callback and flush the resulting async chain
    await act(async () => {
      if (capturedCallback) await capturedCallback();
    });

    await waitFor(() => {
      expect(mockToastSuccess).toHaveBeenCalledWith("Telegram connected!");
    });

    // Cleanup
    realClearInterval(fakeTimerId!);
    setIntervalSpy.mockRestore();
  }, 15000);
});

// ---------------------------------------------------------------------------
// Channel list — target_label rendering
// ---------------------------------------------------------------------------

describe("ChannelsSettingsPage — channel list", () => {
  beforeEach(() => {
    setupDefaultMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    mockGetProviders.mockReset();
    mockListChannels.mockReset();
  });

  it("renders target_label when present on a channel", async () => {
    mockListChannels.mockResolvedValue([
      makeChannel({
        id: 42,
        channel_type: "slack",
        display_name: "Slack Jobs",
        target_label: "#jobs",
      }),
    ]);

    await renderPage();

    expect(screen.getByText("Slack Jobs")).toBeInTheDocument();
    // target_label is inside a <span> within the type div — use a regex or partial match
    expect(screen.getByText((content) => content.includes("#jobs"))).toBeInTheDocument();
  });

  it("does not render target_label span when null", async () => {
    mockListChannels.mockResolvedValue([
      makeChannel({ id: 7, channel_type: "email", display_name: "My Email", target_label: null }),
    ]);

    await renderPage();

    expect(screen.getByText("My Email")).toBeInTheDocument();
    // No span with a label value should appear
    expect(screen.queryByText((content) => content.includes("—"))).not.toBeInTheDocument();
  });
});
