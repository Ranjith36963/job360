import type { ReactNode } from "react";

/**
 * Shared page padding for every signed-in page.
 *
 * Redesign slice 1: content no longer sits in a boxed `max-w-7xl` column. It
 * fills the area to the right of the sidebar, with comfortable side padding
 * (`px-6 lg:px-10`). A readable max width belongs only on pure text pages
 * (legal pages carry their own `max-w-3xl`); forms and cards that need to stay
 * narrow put a `max-w-*` block inside this container, left-aligned.
 */
export function PageContainer({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`w-full px-6 lg:px-10 ${className}`.trim()}>
      {children}
    </div>
  );
}
