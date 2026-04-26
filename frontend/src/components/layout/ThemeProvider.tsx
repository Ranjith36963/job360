"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";
import { useTheme } from "next-themes";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

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

// ---------------------------------------------------------------------------
// Toggle button — sun in dark mode, moon in light mode
// ---------------------------------------------------------------------------

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();

  const toggle = () => setTheme(theme === "dark" ? "light" : "dark");

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      aria-label="Toggle theme"
      className="h-9 w-9 text-muted-foreground hover:text-foreground hover:bg-muted/50"
    >
      <Sun className="h-4 w-4 rotate-0 scale-100 transition-transform dark:-rotate-90 dark:scale-0" />
      <Moon className="absolute h-4 w-4 rotate-90 scale-0 transition-transform dark:rotate-0 dark:scale-100" />
    </Button>
  );
}
