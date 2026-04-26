// ---------------------------------------------------------------------------
// Job360 Frontend — Typed API error class
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: string;
  readonly retryAfter: number | null;

  constructor(
    status: number,
    detail: string,
    code = "api_error",
    retryAfter: number | null = null
  ) {
    super(`API error ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.retryAfter = retryAfter;
    // Restore prototype chain for instanceof checks in transpiled ES2017.
    Object.setPrototypeOf(this, new.target.prototype);
  }

  get isUnauthorized() {
    return this.status === 401 || this.status === 403;
  }

  get isNotFound() {
    return this.status === 404;
  }

  get isRateLimited() {
    return this.status === 429;
  }

  get isServerError() {
    return this.status >= 500;
  }
}
