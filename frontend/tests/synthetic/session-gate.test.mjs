// THE DRILL, in unit form: prove the synthetic-live session gate can go RED.
//
// A synthetic monitor that cannot fail is the original bug. These tests fire
// every wrong-way path deliberately and assert it is REFUSED — so the guard is
// exercised on every CI run, not only on the day something breaks.
//
// The other half of the drill is end-to-end and lives in
// .github/workflows/synthetic-live.yml (`workflow_dispatch` → drill: dead-session),
// which runs the real script against real production with a corrupt session and
// fails if the script exits 0.

import { describe, expect, it } from "vitest";

import {
  SESSION_MAX_AGE_DAYS,
  acquireSession,
  cookieIssuedAtMs,
  daysUntilSessionExpiry,
  isSyntheticEmail,
  pickCookie,
  readSetCookie,
} from "./session-gate.mjs";

const API = "https://backend.example";
const SYNTH = "synthetic@job360.uk";

/** Minimal fetch double. Routes by URL suffix. */
function fakeFetch({ login, me }) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    if (String(url).endsWith("/api/auth/login")) {
      if (typeof login === "function") return login(init);
      if (login instanceof Error) throw login;
      return login;
    }
    if (String(url).endsWith("/api/auth/me")) {
      if (me instanceof Error) throw me;
      return me;
    }
    throw new Error(`unexpected URL ${url}`);
  };
  fn.calls = calls;
  return fn;
}

const res = (status, { setCookie = [], json = {} } = {}) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: { getSetCookie: () => setCookie },
  json: async () => json,
});

const okLogin = (cookie = "sid.an2pww.sig") =>
  res(200, { setCookie: [`job360_session=${cookie}; Path=/; HttpOnly; Secure`] });

describe("isSyntheticEmail — the safety rule", () => {
  it.each([
    "synthetic@job360.uk",
    "SYNTHETIC@JOB360.UK",
    "ranjith+synthetic@gmail.com",
    "smoke.synthetic@job360.uk",
    "synthetic-bot@job360.uk",
    "synthetic_1@job360.uk",
  ])("accepts a dedicated robot address: %s", (e) => {
    expect(isSyntheticEmail(e)).toBe(true);
  });

  it.each([
    // The owner's real address — the account this must never touch.
    "ranjithmaligaguruprakash@gmail.com",
    "someone@job360.uk",
    // Near-misses must NOT squeak through.
    "synthetics@job360.uk",
    "notsynthetic@job360.uk",
    "resynthetic@job360.uk",
    // The domain is not the local part: this is a real user at a cute domain.
    "victim@synthetic.com",
    "",
    "@job360.uk",
    "synthetic@",
    null,
    undefined,
    123,
  ])("refuses anything else: %s", (e) => {
    expect(isSyntheticEmail(e)).toBe(false);
  });
});

describe("acquireSession — GATE 1: never send a real user's password", () => {
  it("refuses a non-synthetic SMOKE_EMAIL without making ANY network call", async () => {
    const fn = fakeFetch({ login: okLogin(), me: res(200, { json: { email: "victim@gmail.com" } }) });
    const r = await acquireSession({
      apiBase: API,
      email: "victim@gmail.com",
      password: "hunter2",
      fetchFn: fn,
    });
    expect(r.status).toBe("refused");
    // The whole point of gate 1: the password never left the process.
    expect(fn.calls).toHaveLength(0);
    expect(r.reason).toMatch(/no password was sent/i);
    expect(r.reason).toMatch(/trample their real profile/i);
  });

  it("refuses when only one of email/password is set", async () => {
    const fn = fakeFetch({ login: okLogin(), me: res(200, { json: { email: SYNTH } }) });
    const r = await acquireSession({ apiBase: API, email: SYNTH, password: "", fetchFn: fn });
    expect(r.status).toBe("refused");
    expect(fn.calls).toHaveLength(0);
    expect(r.reason).toMatch(/TOGETHER/);
  });
});

describe("acquireSession — GATE 2: the SERVER names the owner", () => {
  it("refuses a session the backend says belongs to a real user", async () => {
    // The dangerous case the old code could not see: a pasted cookie that is
    // perfectly VALID but belongs to a human. /api/auth/me returns 200, so a
    // liveness-only gate waves it through.
    const fn = fakeFetch({ login: null, me: res(200, { json: { email: "ranjithmaligaguruprakash@gmail.com" } }) });
    const r = await acquireSession({ apiBase: API, pastedCookie: "sid.an2pww.sig", fetchFn: fn });
    expect(r.status).toBe("refused");
    expect(r.reason).toMatch(/ranjithmaligaguruprakash@gmail\.com/);
    expect(r.reason).toMatch(/REFUSING to walk production/);
  });

  it("refuses when /api/auth/me cannot be parsed (owner unverifiable)", async () => {
    const fn = fakeFetch({
      login: null,
      me: {
        ok: true,
        status: 200,
        headers: { getSetCookie: () => [] },
        json: async () => {
          throw new Error("not json");
        },
      },
    });
    const r = await acquireSession({ apiBase: API, pastedCookie: "sid.an2pww.sig", fetchFn: fn });
    expect(r.status).toBe("refused");
  });

  it("refuses a DEAD session (401) — the 2026-08-09 bug, kept red", async () => {
    const fn = fakeFetch({ login: null, me: res(401) });
    const r = await acquireSession({ apiBase: API, pastedCookie: "sid.an2pww.sig", fetchFn: fn });
    expect(r.status).toBe("refused");
    expect(r.reason).toMatch(/returned 401/);
    expect(r.reason).toMatch(/NOT a production outage/);
  });

  it("refuses when login succeeds but the backend then rejects the session", async () => {
    const fn = fakeFetch({ login: okLogin(), me: res(401) });
    const r = await acquireSession({ apiBase: API, email: SYNTH, password: "pw", fetchFn: fn });
    expect(r.status).toBe("refused");
  });
});

describe("acquireSession — the happy path", () => {
  it("logs in, keeps the cookie, and proceeds when the owner is synthetic", async () => {
    const fn = fakeFetch({ login: okLogin("abc.an2pww.sig"), me: res(200, { json: { email: SYNTH } }) });
    const r = await acquireSession({ apiBase: API, email: SYNTH, password: "pw", fetchFn: fn });
    expect(r.status).toBe("ok");
    expect(r.cookie).toBe("abc.an2pww.sig");
    expect(r.mode).toBe("login");
    // No countdown warning: a login has no clock on it.
    expect(r.warnings).toEqual([]);
    const body = JSON.parse(fn.calls[0].init.body);
    expect(body).toEqual({ email: SYNTH, password: "pw" });
  });

  it("refuses a login that returns 200 with no cookie (a real incident)", async () => {
    const fn = fakeFetch({ login: res(200, { setCookie: [] }), me: res(200, { json: { email: SYNTH } }) });
    const r = await acquireSession({ apiBase: API, email: SYNTH, password: "pw", fetchFn: fn });
    expect(r.status).toBe("refused");
    expect(r.reason).toMatch(/NO real user can sign in either/);
  });

  it("explains a 401 from login as config, not an outage", async () => {
    const fn = fakeFetch({ login: res(401), me: res(200, { json: { email: SYNTH } }) });
    const r = await acquireSession({ apiBase: API, email: SYNTH, password: "pw", fetchFn: fn });
    expect(r.status).toBe("refused");
    expect(r.reason).toMatch(/HTTP 401/);
    expect(r.reason).toMatch(/NOT a production outage/);
  });

  it("skips (does not fail) when nothing is configured at all", async () => {
    const fn = fakeFetch({ login: null, me: null });
    const r = await acquireSession({ apiBase: API, fetchFn: fn });
    expect(r.status).toBe("absent");
    expect(fn.calls).toHaveLength(0);
  });

  it("FAILS instead of skipping when the schedule requires an authed walk", async () => {
    // The quietest death: delete the secret, everything "skips", the run is
    // green, and nine corners of production stop being watched with nobody told.
    const fn = fakeFetch({ login: null, me: null });
    const r = await acquireSession({ apiBase: API, requireAuthed: true, fetchFn: fn });
    expect(r.status).toBe("refused");
    expect(r.reason).toMatch(/NINE authed corners of production are not being watched/);
  });
});

describe("pasted-cookie fallback still warns before the 30-day wall", () => {
  // A real itsdangerous TimestampSigner cookie shape, minted with the installed
  // itsdangerous 2.2.0: `<sid>.<base64url unix seconds>.<hmac>`.
  const at = (epochSeconds) => {
    const b = Buffer.alloc(4);
    b.writeUInt32BE(epochSeconds);
    return `deadbeef.${b.toString("base64url")}.sig`;
  };

  it("reads the signing time out of the cookie without any secret", () => {
    // 1786620355 → "an2pww", verified against python itsdangerous 2.2.0.
    expect(cookieIssuedAtMs("0123456789abcdef.an2pww.O5ob24")).toBe(1786620355 * 1000);
  });

  it.each(["", "no-dots", "a.b", "a.b.c.d", "a.!!!.c", "a.AAAAAAAAAAAAAAAA.c"])(
    "returns null for an unparseable cookie: %s",
    (c) => {
      expect(cookieIssuedAtMs(c)).toBeNull();
    },
  );

  it("warns loudly when a pasted cookie is nearly dead", async () => {
    const now = 1_800_000_000_000;
    const issued = Math.floor(now / 1000) - (SESSION_MAX_AGE_DAYS - 3) * 86400; // 3 days left
    const fn = fakeFetch({ login: null, me: res(200, { json: { email: SYNTH } }) });
    const r = await acquireSession({ apiBase: API, pastedCookie: at(issued), fetchFn: fn, nowMs: now });
    expect(r.status).toBe("ok");
    expect(r.warnings.join(" ")).toMatch(/expires in 3\.0 days/);
    expect(r.warnings.join(" ")).toMatch(/Refresh it NOW/);
  });

  it("still nags on a fresh pasted cookie — the chore is the defect", async () => {
    const now = 1_800_000_000_000;
    const fn = fakeFetch({ login: null, me: res(200, { json: { email: SYNTH } }) });
    const r = await acquireSession({
      apiBase: API,
      pastedCookie: at(Math.floor(now / 1000)),
      fetchFn: fn,
      nowMs: now,
    });
    expect(r.status).toBe("ok");
    expect(r.warnings.join(" ")).toMatch(/end that chore/);
  });

  it("computes days remaining from the 30-day hard limit", () => {
    const now = 1_800_000_000_000;
    const issued = Math.floor(now / 1000) - 10 * 86400;
    expect(daysUntilSessionExpiry(at(issued), now)).toBeCloseTo(20, 5);
  });
});

describe("Set-Cookie parsing", () => {
  it("picks the right cookie out of several", () => {
    const lines = [
      "other=1; Path=/",
      "job360_session=abc.def.ghi; Path=/; HttpOnly; SameSite=lax; Secure",
      "another=2",
    ];
    expect(pickCookie(lines, "job360_session")).toBe("abc.def.ghi");
    expect(pickCookie(lines, "missing")).toBe("");
    expect(pickCookie([], "job360_session")).toBe("");
  });

  it("reads Set-Cookie via getSetCookie, raw(), or a single header", () => {
    expect(readSetCookie({ headers: { getSetCookie: () => ["a=1"] } })).toEqual(["a=1"]);
    expect(readSetCookie({ headers: { raw: () => ({ "set-cookie": ["b=2"] }) } })).toEqual(["b=2"]);
    expect(readSetCookie({ headers: { get: () => "c=3" } })).toEqual(["c=3"]);
    expect(readSetCookie({ headers: { get: () => null } })).toEqual([]);
    expect(readSetCookie({})).toEqual([]);
  });
});
