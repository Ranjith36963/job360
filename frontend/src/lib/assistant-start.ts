import { toast } from "sonner";
import { fireAssistant, type AssistantFire } from "@/lib/api";

/**
 * Called once after Send or Send all saved the user's yes. If the user saved a start link
 * (Settings → Connect your assistant), Job360 starts their assistant now instead of waiting for
 * "run 360". Quiet when nothing is set up, nothing is waiting, or it was started a moment ago;
 * a real problem is shown, but the yes itself is already saved either way.
 */
export async function startAssistantAfterSend(): Promise<AssistantFire | null> {
  let out: AssistantFire;
  try {
    out = await fireAssistant();
  } catch {
    toast.error("Your yes is saved, but your assistant could not be started. Try “run 360” in its chat.");
    return null;
  }
  if (out.fired) {
    const n = out.count ?? 0;
    toast.success(`Your assistant started on ${n} job${n === 1 ? "" : "s"}.`, {
      ...(out.session_url ? { action: { label: "Watch", onClick: () => window.open(out.session_url!, "_blank") } } : {}),
    });
  } else if (out.reason && !["not_set", "nothing_approved", "cooldown"].includes(out.reason)) {
    toast.error(`Your yes is saved, but your assistant did not start: ${out.detail || out.reason}`);
  }
  return out;
}
