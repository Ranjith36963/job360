/**
 * Sentry Session Replay, only with the signed-in user's consent (gap roadmap
 * slice 4).
 *
 * Replay used to start for every visitor at init, with text unmasked — so it
 * recorded CV text and the Art. 9 equality answers with no consent at all. Now
 * instrumentation-client.ts never adds it; AuthProvider calls this with the
 * stored "Help improve Job360" choice (`analytics_consent` on /api/auth/me,
 * set in Settings > Account). Error capture without replay is unaffected.
 *
 * Lazy add, per Sentry's Next.js docs ("Lazy-Loading Replay"):
 * `Sentry.addIntegration(replayIntegration())` after init; the integration then
 * reads `replaysSessionSampleRate` / `replaysOnErrorSampleRate` from the client
 * options set in instrumentation-client.ts. Withdrawal calls
 * `replay.stop({ flush: false })` (SDK >= 10.67) so the pending segment is
 * dropped, not uploaded.
 */

import * as Sentry from "@sentry/nextjs";

/** Masking is not optional: every piece of text and media is hidden. */
export const REPLAY_PRIVACY = { maskAllText: true, blockAllMedia: true } as const;

export async function syncReplayConsent(consented: boolean): Promise<void> {
  if (!process.env.NEXT_PUBLIC_SENTRY_DSN) return;
  const replay = Sentry.getReplay();
  if (!consented) {
    // Recording stops at once; the buffered, unsent segment is discarded.
    if (replay) await replay.stop({ flush: false });
    return;
  }
  if (!replay) {
    Sentry.addIntegration(Sentry.replayIntegration(REPLAY_PRIVACY));
    return;
  }
  // Consent given again after a withdrawal in this page's lifetime: the
  // integration is already installed (only one may exist), so resume in
  // buffer mode — a recording is kept only when an error happens.
  if (!replay.getReplayId()) replay.startBuffering();
}
