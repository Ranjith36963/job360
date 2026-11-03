import type { components } from "./api-types";

type Blocker = components["schemas"]["BlockerOut"];

const ACTOR_WORDS: Record<Blocker["actor"], string> = {
  you: "You",
  your_pc: "Your PC",
  assistant: "Assistant",
  job360: "Job360",
  employer: "Employer",
};

/** The word for who must act next: "You", "Your PC", "Assistant", "Job360", "Employer". */
export function actorWord(actor: Blocker["actor"]): string {
  return ACTOR_WORDS[actor] ?? "";
}

/** The one fixed line every job row shows: "You: solve the CAPTCHA +3 more". Never truncated.
 *  Returns "" when nothing blocks the job (callers then fall back to their old text). */
export function nextLine(next: Pick<Blocker, "actor" | "label"> | null | undefined, more = 0): string {
  if (!next) return "";
  return `${actorWord(next.actor)}: ${next.label}${more > 0 ? ` +${more} more` : ""}`;
}

/** The backend's label for a Send that lapsed (services/applications/blockers.py). */
export const SEND_EXPIRED_LABEL = "Send again (your yes expired)";
/** How long a Send lasts. ONE place to change it (PR #1466 made it 7 days). */
export const SEND_LASTS = "7 days";
export const SEND_EXPIRED_NOTE = `Your Send lasts ${SEND_LASTS}; your assistant didn't submit in time.`;
