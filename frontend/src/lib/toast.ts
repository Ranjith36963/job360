// ---------------------------------------------------------------------------
// Job360 Frontend — Sonner toast helper with project-styled defaults
// ---------------------------------------------------------------------------

import { toast as sonnerToast } from "sonner";
import type { ApiError } from "./api-error";

export const toast = {
  success: (message: string) =>
    sonnerToast.success(message, { duration: 3000 }),

  error: (message: string) =>
    sonnerToast.error(message, { duration: 5000 }),

  info: (message: string) =>
    sonnerToast(message, { duration: 3000 }),

  apiError: (err: ApiError | unknown, fallback = "Something went wrong") => {
    if (err && typeof err === "object" && "status" in err) {
      const e = err as ApiError;
      if (e.isRateLimited) {
        const wait = e.retryAfter ?? 60;
        sonnerToast.warning(`Rate limited — please wait ${wait}s`, {
          duration: Math.min(wait * 1000, 10000),
        });
        return;
      }
      if (e.isUnauthorized) {
        sonnerToast.error("Session expired — please log in again", {
          duration: 5000,
        });
        return;
      }
      sonnerToast.error(e.detail || fallback, { duration: 5000 });
      return;
    }
    sonnerToast.error(fallback, { duration: 5000 });
  },
};
