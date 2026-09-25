import type { ReactNode } from "react";

/**
 * Shared page width for every signed-in page below the nav bar.
 *
 * Owner decision (2026-09-25): pages left big empty space because widths
 * differed page to page — navbar `max-w-7xl` (Navbar.tsx), profile
 * `max-w-6xl`, applications/application/bring/connect all `max-w-3xl`. This
 * uses EXACTLY the navbar's max width and horizontal padding
 * (`max-w-7xl mx-auto px-4 sm:px-6` — see Navbar.tsx) so a page's left and
 * right edges line up with the menu above it.
 *
 * Forms and other content that should stay narrow for readability (Bring a
 * job, the Connect token/OAuth forms) sit inside this container in their own
 * `max-w-3xl` block, left-aligned to the container — not a second, narrower
 * centred wrapper.
 */
export function PageContainer({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`mx-auto max-w-7xl px-4 sm:px-6 ${className}`.trim()}>
      {children}
    </div>
  );
}
