import Link from "next/link";
import { Activity, ExternalLink } from "lucide-react";
// This strapline sits on every page. It said "47 sources" for a week after the
// registry dropped to 41. See src/lib/catalog.ts.
import { SOURCE_COUNT, SCORING_DIMENSIONS } from "@/lib/catalog";

// Legal link slots — content (privacy policy, terms) populated in Batch 4 launch readiness
const LEGAL_LINKS: { label: string; href: string }[] = [
  { label: "Privacy", href: "/privacy" },
  { label: "Terms", href: "/terms" },
  { label: "Contact", href: "/contact" },
];

export function Footer() {
  return (
    <footer className="border-t border-border/30 bg-background/40 backdrop-blur-sm">
      <div className="mx-auto flex flex-col sm:flex-row h-auto sm:h-14 max-w-7xl items-center justify-between gap-2 px-4 sm:px-6 py-3 sm:py-0">
        <Link
          href="/"
          className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <Activity className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
          <span className="font-heading">
            Job<span className="text-primary">360</span>
          </span>
        </Link>

        {/* Legal link slots — placeholder content until Batch 4 */}
        <nav aria-label="Footer links" className="flex items-center gap-4">
          {LEGAL_LINKS.map(({ label, href }) => (
            <Link
              key={label}
              href={href}
              className="text-xs text-muted-foreground/60 hover:text-muted-foreground transition-colors"
            >
              {label}
            </Link>
          ))}
          <a
            href="https://github.com/Ranjith36963/job360"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-muted-foreground/60 hover:text-muted-foreground transition-colors flex items-center gap-1"
            aria-label="Job360 on GitHub"
          >
            <ExternalLink className="h-3 w-3" aria-hidden="true" />
            GitHub
          </a>
        </nav>

        <p className="text-xs text-muted-foreground/60">
          {SOURCE_COUNT} sources. {SCORING_DIMENSIONS}D scoring. One dashboard.
        </p>
      </div>
    </footer>
  );
}
