"use client";

import { useTheme } from "next-themes";
import { Toaster } from "sonner";

/** sonner toasts that follow the chosen theme (System / Light / Dark). */
export function ThemedToaster() {
  const { resolvedTheme } = useTheme();
  return (
    <Toaster
      position="bottom-right"
      richColors
      theme={resolvedTheme === "dark" ? "dark" : "light"}
    />
  );
}
