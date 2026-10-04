import Link from "next/link";
import { ExternalLink } from "lucide-react";
import { Logo } from "@/components/brand/Logo";

// Legal link slots — content (privacy policy, terms) populated in Batch 4 launch readiness
const LEGAL_LINKS: { label: string; href: string }[] = [
  { label: "Privacy", href: "/privacy" },
  { label: "Terms", href: "/terms" },
  { label: "Contact", href: "/contact" },
];

export function Footer() {
  return (
    <footer data-print-hide className="border-t border-border">
      <div className="mx-auto flex h-auto flex-col items-center justify-between gap-2 px-6 py-3 sm:h-14 sm:flex-row sm:py-0 lg:px-10">
        <Link
          href="/"
          aria-label="job360"
          className="flex items-center text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          <Logo size={18} className="[&_[data-testid=logo-wordmark]]:text-sm" />
        </Link>

        {/* Legal link slots — placeholder content until Batch 4 */}
        <nav aria-label="Footer links" className="flex items-center gap-4">
          {LEGAL_LINKS.map(({ label, href }) => (
            <Link
              key={label}
              href={href}
              className="text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              {label}
            </Link>
          ))}
          <a
            href="https://github.com/Ranjith36963/job360"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
            aria-label="Job360 on GitHub"
          >
            <ExternalLink className="h-3 w-3" aria-hidden="true" />
            GitHub
          </a>
        </nav>

        <p className="text-xs text-muted-foreground">
          {/* C2 (application-spine review) — VISION rule 4: Job360 never
              sources or ranks jobs. This footer advertised a source count on
              every page; replaced with the mission line. */}
          The shared job-hunt record for your AI assistant.
        </p>
      </div>
    </footer>
  );
}
