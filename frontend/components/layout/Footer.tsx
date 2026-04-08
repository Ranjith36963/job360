import Link from "next/link";
import { Activity } from "lucide-react";

export function Footer() {
  return (
    <footer className="border-t border-border/30 bg-background/40 backdrop-blur-sm">
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors">
          <Activity className="h-3.5 w-3.5 text-primary" />
          <span className="font-heading">
            Job<span className="text-primary">360</span>
          </span>
        </Link>
        <p className="text-xs text-muted-foreground/60">
          50 sources. 8D scoring. One dashboard.
        </p>
      </div>
    </footer>
  );
}
