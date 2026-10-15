/**
 * One small "status -> allowed actions" table for an application's buttons.
 *
 * The pre-submit approval step ("Send this one" / "Don't send") only makes sense
 * before the application is sent. Once it is applied (or further along, or
 * closed) those buttons are hidden, so nobody can double-act. The backend refuses
 * the same calls with 409 "already applied" (defence in depth).
 *
 * Statuses are the backend's APPLICATION_STATUS_EVENT_TYPES (`brought` maps to
 * `considering`). Pure function, no React.
 */

export type ApplicationAction = "send_approve" | "send_decline";

/** Statuses where nothing has been sent yet: the approval step is still open. */
const PRE_APPLY_STATUSES: ReadonlySet<string> = new Set(["considering"]);

export interface ActionFlags {
  /** A CV is saved: the yes / no is about "the latest CV", so there is nothing to answer without one. */
  hasCv?: boolean;
  /** A newer filled form is waiting: the Ready card owns Send / Don't send instead. */
  newFillWaiting?: boolean;
}

/** Which actions are allowed for this status. Unknown statuses allow nothing. */
export function allowedActions(status: string, flags: ActionFlags = {}): ApplicationAction[] {
  if (!PRE_APPLY_STATUSES.has(status)) return [];
  const { hasCv = true, newFillWaiting = false } = flags;
  if (!hasCv || newFillWaiting) return [];
  return ["send_approve", "send_decline"];
}

/** The Ready card is the pre-send approval: only while the application is still pre-apply. */
export function readyCardAllowed(status: string): boolean {
  return PRE_APPLY_STATUSES.has(status);
}
