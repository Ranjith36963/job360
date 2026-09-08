import { Bot } from "lucide-react";
import { whoLabel } from "@/lib/event-labels";

/** The "recorded by" chip — **You** (neutral) or **Agent · name** (accent).
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
        className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
      >
        {name}
      </span>
    );
  }
  return (
    <span
      data-testid="event-who"
      className="inline-flex items-center gap-1 rounded-full bg-accent/20 px-2 py-0.5 text-[11px] font-medium text-accent-foreground"
    >
      <Bot className="h-3 w-3" />
      Agent · {name}
    </span>
  );
}
