import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const getReplay = vi.fn();
const addIntegration = vi.fn();
const replayIntegration = vi.fn((opts: unknown) => ({ name: "Replay", opts }));

vi.mock("@sentry/nextjs", () => ({
  getReplay: () => getReplay(),
  addIntegration: (i: unknown) => addIntegration(i),
  replayIntegration: (o: unknown) => replayIntegration(o),
}));

import { syncReplayConsent } from "../sentry-replay";

function fakeReplay(replayId: string | undefined) {
  return {
    stop: vi.fn().mockResolvedValue(undefined),
    startBuffering: vi.fn(),
    getReplayId: vi.fn(() => replayId),
  };
}

describe("syncReplayConsent (gap roadmap slice 4)", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_SENTRY_DSN", "https://key@o0.ingest.de.sentry.io/1");
    getReplay.mockReset();
    addIntegration.mockReset();
    replayIntegration.mockClear();
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("adds replay lazily, fully masked, once the user consents", async () => {
    getReplay.mockReturnValue(undefined);
    await syncReplayConsent(true);
    expect(replayIntegration).toHaveBeenCalledWith({ maskAllText: true, blockAllMedia: true });
    expect(addIntegration).toHaveBeenCalledWith({
      name: "Replay",
      opts: { maskAllText: true, blockAllMedia: true },
    });
  });

  it("never adds replay without consent", async () => {
    getReplay.mockReturnValue(undefined);
    await syncReplayConsent(false);
    expect(addIntegration).not.toHaveBeenCalled();
    expect(replayIntegration).not.toHaveBeenCalled();
  });

  it("stops a running replay on withdrawal and drops the unsent segment", async () => {
    const replay = fakeReplay("abc");
    getReplay.mockReturnValue(replay);
    await syncReplayConsent(false);
    expect(replay.stop).toHaveBeenCalledWith({ flush: false });
    expect(addIntegration).not.toHaveBeenCalled();
  });

  it("resumes in buffer mode when consent comes back after a withdrawal", async () => {
    const replay = fakeReplay(undefined); // stopped: no active replay id
    getReplay.mockReturnValue(replay);
    await syncReplayConsent(true);
    expect(replay.startBuffering).toHaveBeenCalledTimes(1);
    expect(addIntegration).not.toHaveBeenCalled(); // only one replay instance may exist
  });

  it("does nothing when Sentry is not configured", async () => {
    vi.stubEnv("NEXT_PUBLIC_SENTRY_DSN", "");
    getReplay.mockReturnValue(undefined);
    await syncReplayConsent(true);
    expect(addIntegration).not.toHaveBeenCalled();
  });
});
