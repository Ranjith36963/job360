import { Bot } from "lucide-react";
import { whoLabel } from "@/lib/event-labels";

/** The "recorded by" chip — **You** (neutral) or **Assistant · name** (accent).
 * One component for every feed surface (Timeline, What's-new) so the two
 * never drift apart visually. `name` is attacker-supplied (an OAuth client's
 * registered name, or a personal-token name) — rendered as a plain React
 * text node only, never markup, never a URL, never a className. */
export function WhoChip({ recordedBy }: { recordedBy: string }) {
  const { who, name } = whoLabel(recordedBy);
  if (who === "you") {
    return (
      <span
        data-testid="event-who"
        className="inline-flex items-center rounded-full border border-border px-2 py-px font-mono text-[10.5px] text-muted-foreground"
      >
        {name}
      </span>
    );
  }
  return (
    <span
      data-testid="event-who"
      className="inline-flex items-center gap-1 rounded-full bg-brand-soft px-2 py-px font-mono text-[10.5px] text-brand"
    >
      <Bot className="h-3 w-3" />
      Assistant · {name}
    </span>
  );
}
