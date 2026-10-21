import { toast } from "sonner";
import { fireAssistant, type AssistantFire } from "@/lib/api";

/**
 * Called once after Send or Send all saved the user's yes. If the user saved a start link
 * (Settings → Connect your assistant), Job360 starts their assistant now instead of waiting for
 * "run 360". Quiet when nothing is set up, nothing is waiting, or it was started a moment ago;
 * a real problem is shown, but the yes itself is already saved either way.
 */
export async function startAssistantAfterSend(opts: { manual?: boolean } = {}): Promise<AssistantFire | null> {
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
      // noopener: the page at the start link's answer must never reach back into this tab.
      ...(out.session_url
        ? { action: { label: "Watch", onClick: () => window.open(out.session_url!, "_blank", "noopener,noreferrer") } }
        : {}),
    });
  } else if (out.reason === "cooldown") {
    // Never silent: this yes is not in the session that just started.
    const wait = out.retry_after ?? 60;
    toast.info(
      `Your yes is saved. Your assistant was started a moment ago, so this job is not in that start: in ${wait} ` +
        "seconds press Start now on Settings → Connect your assistant (or say “run 360”).",
    );
  } else if (opts.manual && out.reason === "nothing_approved") {
    toast.info("Nothing is waiting on your yes right now.");
  } else if (opts.manual && out.reason === "not_set") {
    toast.info("Save a start link first.");
  } else if (out.reason && !["not_set", "nothing_approved"].includes(out.reason)) {
    toast.error(`Your yes is saved, but your assistant did not start: ${out.detail || out.reason}`);
  }
  return out;
}
