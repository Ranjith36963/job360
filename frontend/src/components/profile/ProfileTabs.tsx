"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";

export const PROFILE_TABS = [
  { id: "cv", label: "CV" },
  { id: "memory", label: "Memory" },
  { id: "assistant", label: "Assistant" },
  { id: "setup", label: "Setup" },
] as const;
export type ProfileTab = (typeof PROFILE_TABS)[number]["id"];

/** The tab named by `?tab=`; anything else (or nothing) is the CV tab. */
export function useProfileTab(): ProfileTab {
  const params = useSearchParams();
  const raw = params?.get("tab");
  return PROFILE_TABS.find((t) => t.id === raw)?.id ?? "cv";
}

/** Eyebrow, lede and one sub line. Shown above the Memory, Assistant and Setup
 *  tabs; the CV tab keeps the page's own header. */
export function ProfileHeader() {
  return (
    <header className="mb-6">
      <p className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">Profile</p>
      <h1 className="font-heading text-4xl font-normal tracking-tight">
        What your assistants <em className="not-italic text-brand">know</em> about you.
      </h1>
      <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
        They read this before every form. Nothing here is guessed. Empty rows stay empty until you or an assistant fills them.
      </p>
    </header>
  );
}

/** CV · Memory · Assistant · Setup, as links so a tab can be deep-linked. */
export function ProfileTabs() {
  const active = useProfileTab();
  return (
    <nav aria-label="Profile sections" className="flex flex-wrap gap-0.5 border-b border-border">
      {PROFILE_TABS.map((t) => {
        const on = t.id === active;
        return (
          <Link
            key={t.id}
            href={t.id === "cv" ? "/profile" : `/profile?tab=${t.id}`}
            data-testid={`profile-tab-${t.id}`}
            aria-current={on ? "page" : undefined}
            className={`-mb-px inline-flex min-h-11 items-center border-b-2 px-3.5 text-sm font-medium md:min-h-9 ${
              on ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
