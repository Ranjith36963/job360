import { describe, it, expect } from "vitest";
import { ApiError } from "./api-error";

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

  it("default code is api_error", () => {
    const err = new ApiError(400, "Bad request");
    expect(err.code).toBe("api_error");
  });

  it("default retryAfter is null", () => {
    const err = new ApiError(400, "Bad request");
    expect(err.retryAfter).toBeNull();
  });
});
