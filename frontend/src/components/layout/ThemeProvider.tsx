"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";

// ---------------------------------------------------------------------------
// Provider — wraps next-themes, defaults to dark, persists to localStorage
// ---------------------------------------------------------------------------

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem={false}
      storageKey="job360-theme"
    >
      {children}
    </NextThemesProvider>
  );
}
