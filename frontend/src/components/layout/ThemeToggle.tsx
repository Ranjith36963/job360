"use client";

import { useSyncExternalStore } from "react";
import { useTheme } from "next-themes";
import { Monitor, Moon, Sun, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

const OPTIONS: { value: "system" | "light" | "dark"; label: string; icon: LucideIcon }[] = [
  { value: "system", label: "System", icon: Monitor },
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
];

const subscribe = () => () => {};

/**
 * System / Light / Dark switch. A small segmented group of three buttons;
 * the pressed one is announced with `aria-pressed`.
 *
 * next-themes only knows the stored choice on the client, so nothing is shown
 * as pressed until after hydration — otherwise the server HTML (no choice) and
 * the first client render (a choice) would disagree.
 */
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  const mounted = useSyncExternalStore(
    subscribe,
    () => true,
    () => false
  );
  const current = mounted ? (theme ?? "system") : null;

  return (
    <div
      role="group"
      aria-label="Theme"
      className={cn(
        "inline-flex items-center rounded-lg border border-border bg-card p-0.5",
        className
      )}
    >
      {OPTIONS.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          aria-label={label}
          aria-pressed={current === value}
          title={label}
          onClick={() => setTheme(value)}
          className={cn(
            "inline-flex h-7 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors",
            "hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
            current === value && "bg-foreground text-background hover:text-background"
          )}
        >
          <Icon className="h-3.5 w-3.5" aria-hidden="true" />
        </button>
      ))}
    </div>
  );
}
