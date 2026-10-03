"use client";

import { usePathname } from "next/navigation";
import { Navbar } from "@/components/layout/Navbar";
import { Sidebar } from "@/components/layout/Sidebar";
import { Footer } from "@/components/layout/Footer";
import { useAuth } from "@/components/layout/AuthProvider";
import { isProtectedPath } from "@/components/layout/nav-links";

/**
 * The page frame around every route.
 *
 * - Signed in (or about to be, on a guarded route): a slim left sidebar on
 *   desktop, the top bar + drawer below md. Content fills the area to the
 *   right of the sidebar.
 * - Signed out (landing, auth, legal, oauth consent): a simple top bar and
 *   footer, as before.
 *
 * Both layouts render the same <Navbar>; it hides itself from md up when the
 * sidebar is in charge.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const pathname = usePathname();
  const withSidebar = Boolean(user) || (loading && isProtectedPath(pathname));

  return (
    <div className={withSidebar ? "flex min-h-dvh flex-col md:flex-row" : "flex min-h-dvh flex-col"}>
      {withSidebar && (
        <Sidebar className="sticky top-0 hidden h-dvh self-start md:flex" />
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <Navbar />
        <main className="flex-1">{children}</main>
        <Footer />
      </div>
    </div>
  );
}
