import { useId } from "react";
import { cn } from "@/lib/utils";

/**
 * The one Job360 logo: a ring with a gap and a dot, then the wordmark.
 *
 * - The symbol is always #00FF00, in both themes.
 * - The wordmark is Geist bold: `job` in the foreground colour, `360` in
 *   #00FF00 in both themes. This is a logo, so the rule that bright green is
 *   never text on a light background does not apply to it.
 * - The SVG is decorative. Put the accessible name "job360" on the link that
 *   wraps this (aria-label), or use it where the name is not needed.
 * - `symbolOnly` drops the wordmark (used for tight spots).
 *
 * `useId` keeps the mask id unique when two logos share a page.
 */
export function Logo({
  className,
  symbolOnly = false,
  size = 24,
}: {
  className?: string;
  symbolOnly?: boolean;
  size?: number;
}) {
  const id = useId();
  const maskId = `j360-gap-${id.replace(/:/g, "")}`;

  return (
    <span data-testid="logo" className={cn("inline-flex items-center gap-2", className)}>
      <svg
        viewBox="0 0 240 240"
        width={size}
        height={size}
        aria-hidden="true"
        focusable="false"
        className="shrink-0"
      >
        <mask id={maskId}>
          <rect width="240" height="240" fill="#fff" />
          <circle cx="180" cy="68" r="40" fill="#000" />
        </mask>
        <circle
          cx="108"
          cy="128"
          r="90"
          fill="none"
          stroke="#00FF00"
          strokeWidth="36"
          mask={`url(#${maskId})`}
        />
        <circle cx="180" cy="68" r="24" fill="#00FF00" />
      </svg>
      {!symbolOnly && (
        <span
          data-testid="logo-wordmark"
          className="font-sans text-lg font-bold leading-none tracking-[-0.04em] text-foreground"
        >
          job<span className="text-[#00FF00]">360</span>
        </span>
      )}
    </span>
  );
}
