/**
 * ONE table: application status -> the buttons the application page may show.
 *
 * Every button on the page (Send / Don't send, the Ready card, Mark Applied)
 * asks this table; no component decides visibility from `status` on its own.
 * The backend refuses the same calls with 409 "already applied" (defence in depth).
 *
 * Statuses are the backend's APPLICATION_STATUS_EVENT_TYPES (`brought` maps to
 * `considering`). Pure functions, no React.
 */

export type ApplicationAction = "send_approve" | "send_decline" | "ready_card" | "mark_applied";

const PRE_APPLY: readonly ApplicationAction[] = ["send_approve", "send_decline", "ready_card", "mark_applied"];
// After the send the approval step is closed; only recording the apply by hand can still be needed.
const POST_APPLY: readonly ApplicationAction[] = ["mark_applied"];

/** status -> allowed actions. A status not listed allows nothing. */
export const ACTIONS_BY_STATUS: Readonly<Record<string, readonly ApplicationAction[]>> = {
  considering: PRE_APPLY,
  applied: POST_APPLY,
  replied: POST_APPLY,
  interview_requested: POST_APPLY,
  interview_scheduled: POST_APPLY,
  interview_done: POST_APPLY,
  offer: POST_APPLY,
  rejected: POST_APPLY,
  withdrawn: POST_APPLY,
  ghosted: POST_APPLY,
};

export interface ActionFlags {
  /** A CV is saved: the yes / no is about "the latest CV", so there is nothing to answer without one. */
  hasCv?: boolean;
  /** A newer filled form is waiting: the Ready card owns Send / Don't send instead. */
  newFillWaiting?: boolean;
  /** A receipt exists: "Mark Applied" would only create a second one. */
  hasReceipt?: boolean;
}

/** Which actions are allowed for this status (and flags). Unknown statuses allow nothing. */
export function allowedActions(status: string, flags: ActionFlags = {}): ApplicationAction[] {
  const { hasCv = true, newFillWaiting = false, hasReceipt = false } = flags;
  return (ACTIONS_BY_STATUS[status] ?? []).filter((a) => {
    if (a === "send_approve" || a === "send_decline") return hasCv && !newFillWaiting;
    if (a === "mark_applied") return !hasReceipt;
    return true;
  });
}

/** The Ready card is the pre-send approval: only while the application is still pre-apply. */
export function readyCardAllowed(status: string): boolean {
  return allowedActions(status).includes("ready_card");
}

/** Nothing has been sent while the approval step is still open (same table row as the Ready card). */
export function nothingSent(status: string): boolean {
  return readyCardAllowed(status);
}

interface EventLike {
  id: number;
  event_type: string;
  payload: Record<string, unknown>;
}
interface ArtifactLike {
  id: number;
  kind: string;
  version_no: number;
}

/**
 * How many CV versions were saved after the CV the user last approved
 * (`submit_approved`). `null` when there was no approval, or its CV is not in
 * the list. Read off the existing application payload (events + artifacts).
 */
export function changesSinceApprovedCv(
  events: readonly EventLike[],
  artifacts: readonly ArtifactLike[],
): number | null {
  let approval: EventLike | null = null;
  for (const e of events) {
    if (e.event_type === "submit_approved" && typeof e.payload?.artifact_id === "number") {
      if (!approval || e.id > approval.id) approval = e;
    }
  }
  if (!approval) return null;
  const approvedId = approval.payload.artifact_id;
  const approved = artifacts.find((a) => a.id === approvedId && a.kind === "cv");
  if (!approved) return null;
  return artifacts.filter((a) => a.kind === "cv" && a.version_no > approved.version_no).length;
}
