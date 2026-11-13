import { Bot } from "lucide-react";
import type { Approval } from "@/lib/api";

const WHAT: Record<string, string> = {
  cv: "CV",
  outreach: "Message",
  answers: "Answers",
  submit: "Submit",
};
const REVIEWER_MAX = 30;

/** "AI reviewed" or "You approved" (owner 2026-11-13) for one approved thing. One component for every surface
 * (Sent on its own, the job timeline). It shows what the server says and nothing else: no approval, no tag.
 * `reviewer` is caller text (the name an assistant gave itself): a plain React text node, cut short, never markup. */
export function ApprovalTag({ approval }: { approval?: Approval | null }) {
  if (!approval || (approval.by !== "ai" && approval.by !== "you")) return null;
  const what = WHAT[approval.what];
  const reviewer = approval.reviewer ? approval.reviewer.slice(0, REVIEWER_MAX) : "";
  if (approval.by === "you") {
    return (
      <span
        data-testid="approval-tag"
        data-by="you"
        className="inline-flex items-center rounded-full border border-border px-2 py-px font-mono text-[10.5px] text-muted-foreground"
      >
        You approved{what ? ` · ${what}` : ""}
      </span>
    );
  }
  return (
    <span
      data-testid="approval-tag"
      data-by="ai"
      className="inline-flex items-center gap-1 rounded-full bg-brand-soft px-2 py-px font-mono text-[10.5px] text-brand"
    >
      <Bot className="h-3 w-3" />
      AI reviewed{what ? ` · ${what}` : ""}
      {reviewer ? ` · ${reviewer}` : ""}
    </span>
  );
}
