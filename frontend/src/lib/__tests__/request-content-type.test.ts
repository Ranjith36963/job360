/**
 * TDD regression guard: request() must auto-set Content-Type: application/json
 * when the body is a JSON string, but must NOT touch FormData bodies (uploads).
 *
 * Tests run THROUGH exported callers so we don't need to export request() itself.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a minimal fake Response that satisfies the request() helper. */
function fakeResponse(status = 204): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue({}),
    text: vi.fn().mockResolvedValue(""),
    headers: new Headers(),
  } as unknown as Response;
}

/** Pull the Headers instance out of whatever fetch was called with. */
function capturedHeaders(fetchMock: ReturnType<typeof vi.fn>, callIndex = 0): Headers {
  const init: RequestInit = fetchMock.mock.calls[callIndex][1] ?? {};
  return new Headers(init.headers as HeadersInit);
}

/** Pull the raw RequestInit body out of the fetch call. */
function capturedBody(fetchMock: ReturnType<typeof vi.fn>, callIndex = 0): RequestInit["body"] {
  const init: RequestInit = fetchMock.mock.calls[callIndex][1] ?? {};
  return init.body;
}

// ---------------------------------------------------------------------------
// Module setup
// ---------------------------------------------------------------------------

// api.ts reads process.env.NEXT_PUBLIC_API_URL at module load time.
// Set it before importing so the constant is stable.
process.env.NEXT_PUBLIC_API_URL = "http://localhost:8000";

// We import AFTER setting the env var.
import {
  confirmEmailVerification,
  confirmPasswordReset,
  login,
  requestPasswordReset,
  uploadGithub,
  uploadLinkedin,
} from "../api";

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("request() Content-Type header injection", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(fakeResponse(204));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // -------------------------------------------------------------------------
  // 1. requestPasswordReset — JSON body, no explicit header in caller
  // -------------------------------------------------------------------------
  it("requestPasswordReset sends Content-Type: application/json", async () => {
    await requestPasswordReset("a@b.com");

    const headers = capturedHeaders(fetchMock);
    expect(headers.get("Content-Type")).toBe("application/json");

    const body = capturedBody(fetchMock);
    expect(body).toBe('{"email":"a@b.com"}');
  });

  // -------------------------------------------------------------------------
  // 2. confirmPasswordReset — JSON body, no explicit header in caller
  // -------------------------------------------------------------------------
  it("confirmPasswordReset sends Content-Type: application/json", async () => {
    await confirmPasswordReset("tok", "newpassword12");

    const headers = capturedHeaders(fetchMock);
    expect(headers.get("Content-Type")).toBe("application/json");

    const body = capturedBody(fetchMock);
    expect(body).toBe('{"token":"tok","new_password":"newpassword12"}');
  });

  // -------------------------------------------------------------------------
  // 3. confirmEmailVerification — JSON body, no explicit header in caller
  // -------------------------------------------------------------------------
  it("confirmEmailVerification sends Content-Type: application/json", async () => {
    await confirmEmailVerification("tok");

    const headers = capturedHeaders(fetchMock);
    expect(headers.get("Content-Type")).toBe("application/json");

    const body = capturedBody(fetchMock);
    expect(body).toBe('{"token":"tok"}');
  });

  // -------------------------------------------------------------------------
  // 4. FormData guard — uploadLinkedin must NOT set Content-Type to application/json
  //    (the browser must set multipart/form-data; boundary=... itself)
  // -------------------------------------------------------------------------
  it("uploadLinkedin (FormData) does NOT get Content-Type: application/json", async () => {
    fetchMock.mockResolvedValue(fakeResponse(200));
    // Mock the response for the JSON parse path
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ ok: true, merged: false }),
      text: vi.fn().mockResolvedValue(""),
      headers: new Headers(),
    } as unknown as Response);

    const file = new File(["x"], "x.pdf", { type: "application/pdf" });
    await uploadLinkedin(file);

    const headers = capturedHeaders(fetchMock);
    // Must NOT have application/json — browser sets multipart/form-data
    expect(headers.get("Content-Type")).not.toBe("application/json");

    const body = capturedBody(fetchMock);
    expect(body).toBeInstanceOf(FormData);
  });

  // -------------------------------------------------------------------------
  // 5. FormData guard — uploadGithub (FormData with string field) must NOT set
  //    Content-Type to application/json
  // -------------------------------------------------------------------------
  it("uploadGithub (FormData) does NOT get Content-Type: application/json", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ ok: true, merged: false }),
      text: vi.fn().mockResolvedValue(""),
      headers: new Headers(),
    } as unknown as Response);

    await uploadGithub("myuser");

    const headers = capturedHeaders(fetchMock);
    expect(headers.get("Content-Type")).not.toBe("application/json");

    const body = capturedBody(fetchMock);
    expect(body).toBeInstanceOf(FormData);
  });

  // -------------------------------------------------------------------------
  // 6. Explicit header is respected — login already passes Content-Type in
  //    its own init.headers; the merged result must still be application/json
  // -------------------------------------------------------------------------
  it("login (already has explicit Content-Type) still sends application/json", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ id: "u1", email: "a@b.com" }),
      text: vi.fn().mockResolvedValue(""),
      headers: new Headers(),
    } as unknown as Response);

    await login("a@b.com", "password");

    const headers = capturedHeaders(fetchMock);
    expect(headers.get("Content-Type")).toBe("application/json");
  });
});
