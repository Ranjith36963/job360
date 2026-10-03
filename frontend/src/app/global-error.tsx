"use client";

// global-error.tsx replaces the entire root layout when layout.tsx itself throws.
// It must render its own <html>/<body> since the layout shell is gone.

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen flex items-center justify-center bg-background text-foreground antialiased">
        <div className="text-center max-w-md mx-auto px-4">
          <p className="text-6xl mb-4">⚠️</p>
          <h2 className="text-2xl font-bold mb-2">Something went wrong</h2>
          <p className="text-sm text-muted-foreground mb-6">
            {error.message || "An unexpected error occurred. Please try again."}
          </p>
          <button
            onClick={reset}
            className="inline-flex items-center justify-center rounded-md bg-primary px-6 py-2.5 text-sm font-medium text-primary-foreground hover:opacity-90 transition-opacity"
          >
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
