"use client";

import { useState } from "react";
import { approveSend, clearDuplicate, declineSend, resolveBlocked, setAutofill } from "@/lib/api";
import type { ApplicationControls, DecisionMark } from "@/lib/api";
import { toast } from "@/lib/toast";
import { formatDate } from "@/lib/format-date";
import { describeMark } from "@/components/applications/CvSeenButton";

const BTN =
  "rounded-md border border-border px-2.5 py-1 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-50";
const BTN_ON = "rounded-md border border-brand px-2.5 py-1 text-xs font-semibold text-brand disabled:opacity-50";
const NOTE = "text-xs text-muted-foreground";

function stamp(mark: DecisionMark): string {
  return `${describeMark(mark)} · ${formatDate(mark.at)}`;
}

/**
 * The decisions a person makes before their assistant submits (S3, owner
 * decision 2026-10-08 - human in the loop). Each control shows its current state
 * with who / where / when, and every click is recorded as an event:
 *
 *  - Send this one / Don't send - the yes (or no) to sending THIS application
 *    with the latest CV. A CV edit voids the yes.
 *  - Autofill / Don't autofill - may the assistant type into the form at all.
 *  - Not a duplicate, go ahead - shown only when Job360 flagged a duplicate.
 *  - Your assistant got stuck (S6) - shown at the top while a block is recorded
 *    (CAPTCHA, sign-in...), with who / when; "Mark resolved" clears it.
 *
 * `newFillWaiting`: the assistant filled the form AFTER the last yes / no (the
 * Ready card is showing), so that old answer is not the current one.
 *
 * ("I've checked this CV" lives next to the CV version itself.) All of these are
 * website-only actions: an assistant cannot press them.
 */
export function ApplicationDecisions({
  applicationId,
  controls,
  onChanged,
  newFillWaiting = false,
}: {
  applicationId: number;
  controls: ApplicationControls;
  onChanged: (next: ApplicationControls) => void;
  newFillWaiting?: boolean;
}) {
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<ApplicationControls>, failure: string) {
    setBusy(true);
    try {
      onChanged(await action());
    } catch (err) {
      toast.apiError(err, failure);
    } finally {
      setBusy(false);
    }
  }

  const cv = controls.cv ?? null;
  // The newest of {fill, yes, no} is the current one: after a newer fill an old mark is history.
  const approved = newFillWaiting ? null : (cv?.approved ?? null);
  const declined = newFillWaiting ? null : (controls.declined ?? null);
  const earlier = newFillWaiting ? (controls.declined ?? cv?.approved ?? null) : null;
  const autofill = controls.autofill;
  const dup = controls.duplicate;
  const dupOpen = dup.flag !== "" && !dup.cleared;
  const blocked = controls.blocked ?? null;

  return (
    <div data-testid="app-decisions" className="flex w-full flex-col gap-3 border-t border-border pt-3">
      {blocked && (
        <div data-testid="blocked-state" className="flex flex-col gap-1.5">
          <p className="text-xs text-warning">
            {`Your assistant got stuck: ${blocked.reason_label}${blocked.step ? ` at ${blocked.step}` : ""}${blocked.page_host ? ` on ${blocked.page_host}` : ""}.`}
          </p>
          <p data-testid="blocked-who" className={NOTE}>
            {stamp({ by: blocked.by, where: blocked.by === "web" ? "web" : "chat", at: blocked.at })}
          </p>
          {blocked.detail && (
            <p data-testid="blocked-detail" className={NOTE}>
              {blocked.detail}
            </p>
          )}
          <button
            type="button"
            data-testid="blocked-resolve-button"
            disabled={busy}
            onClick={() => void run(() => resolveBlocked(applicationId), "Couldn't mark it resolved")}
            className={`${BTN} self-start`}
          >
            Mark resolved
          </button>
        </div>
      )}
      {dupOpen && (
        <div data-testid="duplicate-warning" className="flex flex-col gap-1.5">
          <p className="text-xs text-warning">
            {dup.flag === "same_job"
              ? `You already applied to this job${dup.same_job ? ` on ${formatDate(dup.same_job.applied_at)} (${dup.same_job.status})` : ""}.`
              : `${dup.same_company_30d} other application${dup.same_company_30d === 1 ? "" : "s"} at this company in the last 30 days.`}
          </p>
          <button
            type="button"
            data-testid="duplicate-clear-button"
            disabled={busy}
            onClick={() => void run(() => clearDuplicate(applicationId), "Couldn't clear the duplicate warning")}
            className={`${BTN} self-start`}
          >
            Not a duplicate, go ahead
          </button>
        </div>
      )}
      {dup.cleared && (
        <p data-testid="duplicate-cleared" className={NOTE}>
          Not a duplicate - {stamp(dup.cleared)}
        </p>
      )}

      {cv && (
        <div className="flex flex-col gap-1.5">
          {/* A newer fill is waiting: the Ready card above owns Send / Don't send (it passes the
              CV + fill ids the guard checks), so no second, unguarded pair of buttons here. */}
          {!newFillWaiting && (
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                data-testid="send-approve-button"
                disabled={busy}
                onClick={() => void run(() => approveSend(applicationId), "Couldn't save your yes")}
                className={approved && !declined ? BTN_ON : BTN}
              >
                Send this one
              </button>
              <button
                type="button"
                data-testid="send-decline-button"
                disabled={busy}
                onClick={() => void run(() => declineSend(applicationId), "Couldn't save your no")}
                className={declined ? BTN_ON : BTN}
              >
                Don&apos;t send
              </button>
            </div>
          )}
          {newFillWaiting ? (
            <p data-testid="send-state" className={NOTE}>
              New fill waiting for your yes.
              {earlier && ` Before it: ${controls.declined ? "Don't send" : `Yes to v${cv.version}`} - ${stamp(earlier)}`}
            </p>
          ) : declined ? (
            <p data-testid="send-state" className={NOTE}>
              Don&apos;t send - {stamp(declined)}
            </p>
          ) : approved ? (
            <p data-testid="send-state" className={NOTE}>
              Yes to v{cv.version} - {stamp(approved)}
            </p>
          ) : (
            <p data-testid="send-state" className={NOTE}>
              No answer yet for v{cv.version}. Your assistant asks before sending.
            </p>
          )}
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            data-testid="autofill-allow-button"
            disabled={busy}
            onClick={() => void run(() => setAutofill(applicationId, "allow"), "Couldn't save the autofill choice")}
            className={autofill.mode === "allow" ? BTN_ON : BTN}
          >
            Autofill
          </button>
          <button
            type="button"
            data-testid="autofill-deny-button"
            disabled={busy}
            onClick={() => void run(() => setAutofill(applicationId, "deny"), "Couldn't save the autofill choice")}
            className={autofill.mode === "deny" ? BTN_ON : BTN}
          >
            Don&apos;t autofill
          </button>
        </div>
        <p data-testid="autofill-state" className={NOTE}>
          {autofill.mode === "unset"
            ? "Autofill: not set - your assistant follows its own app permission."
            : `Autofill: ${autofill.mode === "allow" ? "allowed" : "not allowed"} - ${stamp({
                by: autofill.by ?? "",
                where: autofill.where ?? "web",
                at: autofill.at ?? "",
              })}`}
        </p>
      </div>
    </div>
  );
}
