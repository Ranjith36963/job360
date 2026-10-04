"use client";

import { useEffect } from "react";
import { reportClientLog } from "@/lib/clientLog";
import { toast } from "@/lib/toast";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    toast.error(error.message || "An unexpected error occurred");
    reportClientLog({
      message: error.message || "render error boundary",
      stack: error.stack,
      context: error.digest ? `error-boundary digest=${error.digest}` : "error-boundary",
    });
  }, [error]);

  return (
    <div className="min-h-[60vh] flex items-center justify-center">
      <div className="text-center max-w-md mx-auto px-4">
        <div className="text-6xl mb-4">⚠️</div>
        <h2 className="text-balance font-heading text-3xl font-normal tracking-tight mb-2">Something went wrong</h2>
        <p className="text-muted-foreground mb-6">
          {error.message || "An unexpected error occurred. Please try again."}
        </p>
        <button
          onClick={reset}
          className="inline-flex items-center justify-center rounded-lg bg-foreground px-6 py-2.5 text-sm font-medium text-background hover:opacity-90 transition-colors"
        >
          Try again
        </button>
      </div>
    </div>
  );
}
