import Link from "next/link";

export default function NotFound() {
  return (
    <div className="min-h-[60vh] flex items-center justify-center">
      <div className="text-center max-w-md mx-auto px-4">
        <div className="font-heading text-8xl font-normal text-faint mb-4">404</div>
        <h1 className="text-balance font-heading text-3xl font-normal tracking-tight mb-2">Page not found</h1>
        <p className="text-muted-foreground mb-6">
          The page you&apos;re looking for doesn&apos;t exist or has been moved.
        </p>
        <Link
          href="/"
          className="inline-flex items-center justify-center rounded-lg bg-foreground px-6 py-2.5 text-sm font-medium text-background hover:opacity-90 transition-colors"
        >
          Back to home
        </Link>
      </div>
    </div>
  );
}
