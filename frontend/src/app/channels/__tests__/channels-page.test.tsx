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
const mockListChannels = vi.fn();
const mockCreateChannel = vi.fn();
const mockDeleteChannel = vi.fn();
const mockTestChannel = vi.fn();

vi.mock("@/lib/api", () => ({
  listChannels: (...args: unknown[]) => mockListChannels(...args),
  createChannel: (...args: unknown[]) => mockCreateChannel(...args),
  deleteChannel: (...args: unknown[]) => mockDeleteChannel(...args),
  testChannel: (...args: unknown[]) => mockTestChannel(...args),
}));

// ---------------------------------------------------------------------------
// Default data
// ---------------------------------------------------------------------------

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
  // Wait for channels to load
  await waitFor(() => {
    expect(screen.queryByText(/Loading…/i)).not.toBeInTheDocument();
  });
  return container!;
}

// ---------------------------------------------------------------------------
// Channel offering — only email + webhook, email primary
// ---------------------------------------------------------------------------

describe("ChannelsSettingsPage — channel offering", () => {
  beforeEach(() => {
    setupDefaultMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    mockListChannels.mockReset();
    mockCreateChannel.mockReset();
    mockDeleteChannel.mockReset();
    mockTestChannel.mockReset();
    mockToastSuccess.mockReset();
    mockToastError.mockReset();
  });

  it("offers exactly two channel types: email and webhook", async () => {
    await renderPage();

    // The two add-channel cards (CardTitle renders as plain text, not a
    // semantic heading element, so match on exact text instead of role).
    expect(screen.getByText(/^email$/i)).toBeInTheDocument();
    expect(screen.getByText(/^webhook$/i)).toBeInTheDocument();

    // Nothing referencing the removed chat channels remains on the page.
    expect(screen.queryByText(/slack/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/discord/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/telegram/i)).not.toBeInTheDocument();
  });

  it("presents email as the primary, recommended option", async () => {
    await renderPage();

    // Email is called out as "Recommended"; webhook is called out as "Advanced".
    expect(screen.getByText(/recommended/i)).toBeInTheDocument();
    expect(screen.getByText(/advanced/i)).toBeInTheDocument();
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
    mockListChannels.mockReset();
    mockCreateChannel.mockReset();
    mockToastSuccess.mockReset();
  });

  it("calls createChannel with channel_type webhook on a valid https URL", async () => {
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

  it("calls createChannel with channel_type webhook on a valid http URL", async () => {
    const user = userEvent.setup();
    await renderPage();

    const webhookInput = screen.getByLabelText(/webhook url/i);
    await user.type(webhookInput, "http://internal-host/hook");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(mockCreateChannel).toHaveBeenCalledWith(
        expect.objectContaining({
          channel_type: "webhook",
          credential: "http://internal-host/hook",
        })
      );
    });
  });

  it("blocks submit with inline error when URL does not start with http(s)", async () => {
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
// Channel list — target_label rendering
// ---------------------------------------------------------------------------

describe("ChannelsSettingsPage — channel list", () => {
  beforeEach(() => {
    setupDefaultMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    mockListChannels.mockReset();
  });

  it("renders target_label when present on a channel", async () => {
    mockListChannels.mockResolvedValue([
      makeChannel({
        id: 42,
        channel_type: "webhook",
        display_name: "My Webhook",
        target_label: "https://example.com/hook",
      }),
    ]);

    await renderPage();

    expect(screen.getByText("My Webhook")).toBeInTheDocument();
    // target_label is inside a <span> within the type div — use a regex or partial match
    expect(
      screen.getByText((content) => content.includes("https://example.com/hook"))
    ).toBeInTheDocument();
  });

  it("does not render target_label span when null", async () => {
    mockListChannels.mockResolvedValue([
      makeChannel({ id: 7, channel_type: "email", display_name: "My Email", target_label: null }),
    ]);

    const { container } = await renderPage();

    expect(screen.getByText("My Email")).toBeInTheDocument();
    // The target_label span (class "normal-case") is only rendered when
    // target_label is set. Scoped to the DOM (not a global text search) so
    // this doesn't false-positive on unrelated em-dashes elsewhere on the
    // page (e.g. the webhook card's "advanced — not officially supported"
    // copy).
    expect(container.querySelector(".normal-case")).not.toBeInTheDocument();
  });
});
