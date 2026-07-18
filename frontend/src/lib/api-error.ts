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

  /**
   * A signed-in user whose email is not yet verified hit a verified-only route.
   * Distinct from a genuine 401 (not signed in at all). The low-level fetch
   * client throws this instead of navigating; a top-level handler decides the
   * redirect. Matches both the structured `code` and the legacy bare `detail`.
   */
  get isEmailNotVerified() {
    return (
      this.status === 403 &&
      (this.code === "email_not_verified" || this.detail === "email_not_verified")
    );
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

/**
 * Human-readable message for any API error. Prefers the backend's ``detail``
 * (e.g. "email delivery is not configured") over the raw ``API error 503: …``
 * string that leaks the HTTP status into the UI.
 */
export function apiErrorMessage(
  err: unknown,
  fallback = "Something went wrong. Please try again."
): string {
  if (err instanceof ApiError) {
    return err.detail || fallback;
  }
  return err instanceof Error && err.message ? err.message : fallback;
}

/**
 * Map an error to a user-friendly message for auth forms (login / register /
 * password reset). Without this, forms rendered the raw `API error 401:
 * invalid credentials` string, leaking the HTTP status to the user.
 */
export function friendlyAuthError(
  err: unknown,
  fallback = "Something went wrong. Please try again."
): string {
  if (err instanceof ApiError) {
    if (err.status === 401 || err.status === 403) {
      return "Incorrect email or password.";
    }
    if (err.status === 409) {
      return "That email is already registered — try signing in instead.";
    }
    if (err.status === 422) {
      return "Please enter a valid email and a password with at least 8 characters.";
    }
    if (err.status === 429) {
      return "Too many attempts. Please wait a moment and try again.";
    }
    if (err.status >= 500) {
      return "The server had a problem. Please try again shortly.";
    }
    return err.detail || fallback;
  }
  return err instanceof Error && err.message ? err.message : fallback;
}
