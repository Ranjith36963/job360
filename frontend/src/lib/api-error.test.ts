import { describe, it, expect } from "vitest";
import { ApiError, friendlyAuthError, apiErrorMessage } from "./api-error";

describe("ApiError", () => {
  it("extends Error with correct name", () => {
    const err = new ApiError(404, "Not found");
    expect(err).toBeInstanceOf(Error);
    expect(err.name).toBe("ApiError");
  });

  it("stores status, detail, code", () => {
    const err = new ApiError(429, "Rate limited", "rate_limit", 30);
    expect(err.status).toBe(429);
    expect(err.detail).toBe("Rate limited");
    expect(err.code).toBe("rate_limit");
    expect(err.retryAfter).toBe(30);
  });

  it("instanceof check works after transpilation", () => {
    const err = new ApiError(500, "Server error");
    expect(err instanceof ApiError).toBe(true);
  });

  it("isUnauthorized for 401", () => {
    expect(new ApiError(401, "").isUnauthorized).toBe(true);
  });

  it("isUnauthorized for 403", () => {
    expect(new ApiError(403, "").isUnauthorized).toBe(true);
  });

  it("isNotFound for 404", () => {
    expect(new ApiError(404, "").isNotFound).toBe(true);
  });

  it("isRateLimited for 429", () => {
    expect(new ApiError(429, "").isRateLimited).toBe(true);
  });

  it("isServerError for 500+", () => {
    expect(new ApiError(500, "").isServerError).toBe(true);
    expect(new ApiError(503, "").isServerError).toBe(true);
  });

  it("isServerError is false for 4xx", () => {
    expect(new ApiError(404, "").isServerError).toBe(false);
  });

  it("isEmailNotVerified for a 403 with the structured code", () => {
    expect(
      new ApiError(403, "not verified", "email_not_verified").isEmailNotVerified
    ).toBe(true);
  });

  it("isEmailNotVerified for a 403 with the legacy bare detail", () => {
    expect(new ApiError(403, "email_not_verified").isEmailNotVerified).toBe(true);
  });

  it("isEmailNotVerified is false for an ordinary 403", () => {
    expect(new ApiError(403, "forbidden").isEmailNotVerified).toBe(false);
  });

  it("isEmailNotVerified is false when the code appears on a non-403", () => {
    expect(
      new ApiError(401, "email_not_verified", "email_not_verified").isEmailNotVerified
    ).toBe(false);
  });

  it("default code is api_error", () => {
    const err = new ApiError(400, "Bad request");
    expect(err.code).toBe("api_error");
  });

  it("default retryAfter is null", () => {
    const err = new ApiError(400, "Bad request");
    expect(err.retryAfter).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// H11 — friendlyAuthError must never leak the raw "API error NNN: …" string
// ---------------------------------------------------------------------------

describe("friendlyAuthError", () => {
  it("never contains the raw 'API error' prefix for an ApiError", () => {
    const err = new ApiError(400, "some backend token/expiry detail");
    const msg = friendlyAuthError(err, "fallback");
    expect(msg).not.toMatch(/API error \d+/);
  });

  it("maps 401/403 to a generic credentials message", () => {
    expect(friendlyAuthError(new ApiError(401, "invalid credentials"))).toBe(
      "Incorrect email or password."
    );
    expect(friendlyAuthError(new ApiError(403, "forbidden"))).toBe(
      "Incorrect email or password."
    );
  });

  it("maps 429 to a rate-limit message", () => {
    expect(friendlyAuthError(new ApiError(429, "too many"))).toMatch(/too many attempts/i);
  });

  it("maps 500+ to a generic server message", () => {
    expect(friendlyAuthError(new ApiError(503, "db down"))).toMatch(/server had a problem/i);
  });

  it("falls back to the provided fallback for a non-ApiError, non-Error value", () => {
    expect(friendlyAuthError("boom", "custom fallback")).toBe("custom fallback");
  });

  it("uses the provided fallback message by default", () => {
    expect(friendlyAuthError(null)).toBe("Something went wrong. Please try again.");
  });
});

describe("apiErrorMessage", () => {
  it("prefers the ApiError detail over the raw 'API error NNN' string", () => {
    const err = new ApiError(400, "token is expired");
    expect(apiErrorMessage(err)).toBe("token is expired");
  });

  it("falls back for a non-Error value", () => {
    expect(apiErrorMessage(42, "fallback text")).toBe("fallback text");
  });
});
