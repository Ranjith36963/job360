"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";

// ---------------------------------------------------------------------------
// Provider — wraps next-themes. Follows the OS by default (System), and the
// user's explicit choice (Light / Dark) persists to localStorage. next-themes
// injects a tiny script that sets the class before first paint, so there is
// no flash of the wrong theme (the <html> carries suppressHydrationWarning).
// ---------------------------------------------------------------------------

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      storageKey="job360-theme"
    >
      {children}
    </NextThemesProvider>
  );
}
