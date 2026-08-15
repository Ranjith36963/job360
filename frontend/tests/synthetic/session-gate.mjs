// How the live smoke test GETS a session — and the safety gate that decides
// whether it is allowed to use one.
//
// ── Why this file exists ────────────────────────────────────────────────────
// Until now the authed half of the smoke ran on SMOKE_SESSION: a raw
// `job360_session` cookie the owner pasted into a repo secret by hand. Sessions
// have a HARD 30-day expiry with no sliding renewal
// (backend/src/services/auth/sessions.py:25 `SESSION_MAX_AGE_DAYS = 30`,
// resolve_session refuses on `expires_at <= now` at :86), so that secret dies on
// a clock. It already did: 2026-08-09 → 2026-08-11 nine authed corners reported
// OK while the robot stared at the sign-in card.
//
// A recurring manual chore attached to a safety check is a check with a
// countdown on it. So: LOG IN AT RUN TIME instead. `POST /api/auth/login`
// (backend/src/api/routes/auth.py:215-276) takes email + password and returns the
// session cookie in one request — no mailbox, no magic link, nothing to paste
// every 30 days. The owner sets a password ONCE.
//
// ── The safety rule (enforced here, in code — not in a comment) ─────────────
// This walk must never touch a real user's account. Two independent gates:
//
//   1. BEFORE the password is sent anywhere, the address must look synthetic.
//      A real user's password is never transmitted, and no session is ever
//      minted for a real account.
//   2. AFTER a session exists (however it was obtained — login OR a pasted
//      cookie), we ask the BACKEND whose account it is (`GET /api/auth/me`) and
//      refuse unless the server's own answer is a synthetic address. This is
//      the load-bearing one: it checks the account the session actually grants,
//      not the string we were handed.
//
// Gate 2 covers the pasted-cookie path too, which was previously unguarded.

// Hard expiry of a session, mirrored from the backend so the pasted-cookie
// fallback can warn BEFORE it dies. Source of truth:
// backend/src/services/auth/sessions.py:25.
export const SESSION_MAX_AGE_DAYS = 30;

// Warn once fewer than this many days remain on a pasted cookie.
export const REFRESH_WARNING_DAYS = 7;

export const SYNTHETIC_RULE_TEXT =
  'the local part (before "@") must contain "synthetic" as a whole token, ' +
  'delimited by + . - or _ — e.g. "synthetic@job360.uk" or ' +
  '"you+synthetic@gmail.com". "you@gmail.com" and "synthetics@x.com" do NOT match.';

/**
 * The safety rule. True only for an address that was deliberately created for
 * this robot.
 *
 * Deliberately a rule in CODE, not an env-var allowlist: an allowlist that
 * lives in repo settings can be widened without review, and the whole point is
 * that a stray value can never aim this at a human's account. Widening this
 * needs a commit and a diff.
 *
 * Plus-tagging is accepted because it is the practical way to own a real,
 * deliverable mailbox for the robot (gmail `+tag` lands in the same inbox) while
 * still being a genuinely SEPARATE `users` row — `users.email` is the identity
 * (auth.py:249 matches on `LOWER(email)`), so `you+synthetic@x.com` can never
 * resolve to `you@x.com`'s account.
 *
 * @param {unknown} email
 * @returns {boolean}
 */
export function isSyntheticEmail(email) {
  if (typeof email !== "string") return false;
  const at = email.lastIndexOf("@");
  if (at <= 0 || at === email.length - 1) return false;
  const local = email.slice(0, at).toLowerCase();
  // "synthetic" as its own token: at a boundary on both sides. `synthetics`
  // and `notsynthetic` are rejected — near-misses must not squeak through.
  return /(^|[+._-])synthetic([+._-]|$)/.test(local);
}

/**
 * Read the Set-Cookie header(s) off a fetch Response as an array.
 * Node 20+ has `Headers.getSetCookie()`; everything else needs a fallback,
 * because a plain `.get("set-cookie")` folds multiple cookies into one string.
 *
 * @param {{ headers: any }} res
 * @returns {string[]}
 */
export function readSetCookie(res) {
  const h = res && res.headers;
  if (!h) return [];
  if (typeof h.getSetCookie === "function") return h.getSetCookie();
  if (typeof h.raw === "function") return h.raw()["set-cookie"] || [];
  const one = typeof h.get === "function" ? h.get("set-cookie") : null;
  return one ? [one] : [];
}

/**
 * Pull one cookie's VALUE out of a list of Set-Cookie header lines.
 *
 * @param {string[]} lines
 * @param {string} name
 * @returns {string} "" when absent
 */
export function pickCookie(lines, name) {
  for (const line of lines || []) {
    const first = String(line).split(";")[0];
    const eq = first.indexOf("=");
    if (eq < 0) continue;
    if (first.slice(0, eq).trim() !== name) continue;
    return first.slice(eq + 1).trim();
  }
  return "";
}

/**
 * When was this session cookie signed?
 *
 * The cookie is `<session_id>.<timestamp>.<hmac>` — itsdangerous
 * `TimestampSigner` (sessions.py:29). The middle field is the signing time as
 * big-endian bytes, base64url without padding, and itsdangerous 2.x uses a
 * plain unix epoch (verified against the installed itsdangerous 2.2.0). No
 * secret is needed to READ it — only to trust it. We use it purely to say
 * "this pasted cookie is N days old", never as an auth decision.
 *
 * @param {string} cookie
 * @returns {number|null} epoch milliseconds, or null if unparseable
 */
export function cookieIssuedAtMs(cookie) {
  const parts = String(cookie || "").split(".");
  if (parts.length !== 3) return null;
  const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  if (!b64) return null;
  let buf;
  try {
    buf = Buffer.from(b64 + "=".repeat((4 - (b64.length % 4)) % 4), "base64");
  } catch {
    return null;
  }
  if (buf.length === 0 || buf.length > 8) return null;
  let secs = 0;
  for (const byte of buf) secs = secs * 256 + byte;
  // Sanity bound: 2020-01-01 .. 2100-01-01. Anything outside is not a timestamp.
  if (secs < 1577836800 || secs > 4102444800) return null;
  return secs * 1000;
}

/**
 * Days left before a pasted cookie hits the hard 30-day wall. null if unknown.
 *
 * @param {string} cookie
 * @param {number} nowMs
 * @returns {number|null}
 */
export function daysUntilSessionExpiry(cookie, nowMs) {
  const issued = cookieIssuedAtMs(cookie);
  if (issued === null) return null;
  const expires = issued + SESSION_MAX_AGE_DAYS * 86400_000;
  return (expires - nowMs) / 86400_000;
}

/**
 * Get a session for the authed walk, and decide whether we are allowed to use it.
 *
 * @param {object} o
 * @param {string} o.apiBase           backend origin (no trailing slash)
 * @param {string} [o.email]           synthetic account address
 * @param {string} [o.password]        synthetic account password
 * @param {string} [o.pastedCookie]    legacy SMOKE_SESSION fallback
 * @param {boolean} [o.requireAuthed]  scheduled runs: "nothing configured" is a FAILURE
 * @param {Function} [o.fetchFn]       injected for tests
 * @param {number} [o.nowMs]           injected for tests
 * @returns {Promise<{status:"ok"|"refused"|"absent", cookie:string, email:string,
 *                    mode:string, reason:string, warnings:string[]}>}
 *   "ok"      → walk the authed corners
 *   "refused" → a RED failure; something is wrong and must be said out loud
 *   "absent"  → nothing configured; skip the authed corners (not a failure)
 */
export async function acquireSession({
  apiBase,
  email = "",
  password = "",
  pastedCookie = "",
  requireAuthed = false,
  fetchFn = fetch,
  nowMs = Date.now(),
}) {
  const warnings = [];
  let cookie = "";
  let mode = "";

  if (email || password) {
    mode = "login";
    if (!email || !password) {
      return {
        status: "refused",
        cookie: "",
        email: "",
        mode,
        reason:
          "SMOKE_EMAIL and SMOKE_PASSWORD must be set TOGETHER — one without the " +
          "other cannot log in. Set both (SMOKE_EMAIL is a repo variable, " +
          "SMOKE_PASSWORD a repo secret) or neither.",
        warnings,
      };
    }
    // GATE 1 — refuse BEFORE the password leaves this process. If the address
    // is a human's, we must not transmit their password nor mint a session.
    if (!isSyntheticEmail(email)) {
      return {
        status: "refused",
        cookie: "",
        email,
        mode,
        reason:
          `SMOKE_EMAIL (${email}) does not look like a dedicated synthetic account, ` +
          `so this run REFUSED to log in — no password was sent. This robot walks ` +
          `production every 6 hours and (with SMOKE_ALLOW_WRITES) uploads a CV; ` +
          `pointed at a human it would trample their real profile. Rule: ${SYNTHETIC_RULE_TEXT} ` +
          `FIX: register a dedicated account and point SMOKE_EMAIL at it.`,
        warnings,
      };
    }
    let res;
    try {
      res = await fetchFn(`${apiBase}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
    } catch (e) {
      return {
        status: "refused",
        cookie: "",
        email,
        mode,
        reason: `POST ${apiBase}/api/auth/login was unreachable: ${String(e && e.message ? e.message : e).slice(0, 120)}`,
        warnings,
      };
    }
    if (!res.ok) {
      const hint =
        res.status === 401
          ? "the password is wrong, or the account does not exist yet — register it once, " +
            "then set SMOKE_PASSWORD."
          : res.status === 429
            ? "locked out by the brute-force throttle (auth.py:232). It clears itself; " +
              "if it keeps happening the password is wrong."
            : "unexpected — check the backend is up.";
      return {
        status: "refused",
        cookie: "",
        email,
        mode,
        reason: `login as the synthetic account failed — POST /api/auth/login returned HTTP ${res.status}. ${hint} This is NOT a production outage for real users.`,
        warnings,
      };
    }
    cookie = pickCookie(readSetCookie(res), "job360_session");
    if (!cookie) {
      return {
        status: "refused",
        cookie: "",
        email,
        mode,
        reason:
          "login returned HTTP 200 but no `job360_session` cookie. The login route is " +
          "supposed to set one (auth.py:271 `_set_session_cookie`) — if that stopped " +
          "happening, NO real user can sign in either. Treat as a live incident.",
        warnings,
      };
    }
  } else if (pastedCookie) {
    // Legacy fallback: a hand-pasted cookie. Still supported so this change can
    // never make the check worse than it was — but it is on a countdown, so say so.
    mode = "pasted-cookie";
    cookie = pastedCookie;
    const left = daysUntilSessionExpiry(cookie, nowMs);
    if (left === null) {
      warnings.push(
        "SMOKE_SESSION is set but its age could not be read, so this check cannot warn " +
          "you before it expires. Switch to SMOKE_EMAIL + SMOKE_PASSWORD (a login has no expiry).",
      );
    } else if (left <= 0) {
      warnings.push(
        `SMOKE_SESSION was signed ${(SESSION_MAX_AGE_DAYS - left).toFixed(1)} days ago and the ` +
          `hard limit is ${SESSION_MAX_AGE_DAYS} days — it is ALREADY EXPIRED.`,
      );
    } else if (left <= REFRESH_WARNING_DAYS) {
      warnings.push(
        `SMOKE_SESSION expires in ${left.toFixed(1)} days (hard ${SESSION_MAX_AGE_DAYS}-day limit, ` +
          `no renewal). Refresh it NOW, or better: set SMOKE_EMAIL + SMOKE_PASSWORD and delete ` +
          `the secret — a login never expires on a clock.`,
      );
    } else {
      warnings.push(
        `Running on a hand-pasted SMOKE_SESSION; it dies in ${left.toFixed(1)} days and will need ` +
          `pasting again. Set SMOKE_EMAIL + SMOKE_PASSWORD to end that chore.`,
      );
    }
  } else {
    // NOTHING CONFIGURED. On a scheduled run this is the QUIETEST way for the
    // monitor to die: delete the secret and every authed corner "skips", the run
    // is green, and nine corners of production stop being watched with nobody
    // told. A monitor that silently downgrades itself is the same disease as one
    // that cannot go red — so on the schedule, say it out loud.
    return {
      status: requireAuthed ? "refused" : "absent",
      cookie: "",
      email: "",
      mode: "none",
      reason:
        "no synthetic credentials configured, so NINE authed corners of production are " +
        "not being watched at all. Set SMOKE_EMAIL (repo variable) + SMOKE_PASSWORD " +
        "(repo secret) to a dedicated synthetic account. This is NOT a production outage.",
      warnings,
    };
  }

  // ── GATE 2 — ask the SERVER whose account this session is ──────────────────
  // Also the liveness check PR #313 added: a dead cookie is a CONFIG problem
  // with a one-line fix, and letting it leak into nine corners buries the cause.
  let me;
  try {
    me = await fetchFn(`${apiBase}/api/auth/me`, {
      headers: { Cookie: `job360_session=${cookie}` },
    });
  } catch (e) {
    return {
      status: "refused",
      cookie,
      email,
      mode,
      reason: `GET ${apiBase}/api/auth/me was unreachable: ${String(e && e.message ? e.message : e).slice(0, 120)}`,
      warnings,
    };
  }
  if (!me.ok) {
    return {
      status: "refused",
      cookie,
      email,
      mode,
      reason:
        `the session is not valid — GET /api/auth/me returned ${me.status}. Every protected ` +
        `page would redirect to /login and each corner would "pass" against the sign-in card, ` +
        `so the authed walk is BLOCKED instead. ` +
        (mode === "pasted-cookie"
          ? "FIX: stop pasting cookies — set SMOKE_EMAIL + SMOKE_PASSWORD and this cannot happen again."
          : "The login just succeeded, so a session that is instantly invalid points at the backend, not at config.") +
        " This is NOT a production outage.",
      warnings,
    };
  }
  let body = {};
  try {
    body = await me.json();
  } catch {
    /* fall through — an unreadable body means an unverifiable owner */
  }
  const owner = typeof body?.email === "string" ? body.email : "";
  if (!isSyntheticEmail(owner)) {
    return {
      status: "refused",
      cookie,
      email: owner,
      mode,
      reason:
        `the session belongs to ${owner || "an account whose email could not be read"}, which is ` +
        `NOT a dedicated synthetic account — REFUSING to walk production as it. The backend ` +
        `itself named this owner (GET /api/auth/me), so this holds no matter how the session ` +
        `was obtained. Rule: ${SYNTHETIC_RULE_TEXT} ` +
        `FIX: point SMOKE_EMAIL/SMOKE_PASSWORD at a throwaway account.`,
      warnings,
    };
  }

  return { status: "ok", cookie, email: owner, mode, reason: "", warnings };
}
