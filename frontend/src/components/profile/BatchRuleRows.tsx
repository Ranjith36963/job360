"use client";

import { useRef, useState } from "react";
import { toast } from "sonner";
import type { AssistantSettingsView } from "@/lib/api";
import {
  COMPANY_CAP_MAX,
  COMPANY_DAYS_MAX,
  DAILY_CAP_MAX,
  GRANT_COUNT_MAX,
  GRANT_MAX_DAYS,
  MIN_GAP_MAX,
  P_CAP,
  P_COMPANY_CAP,
  P_COMPANY_DAYS,
  P_GRANT_COUNT,
  P_GRANT_UNTIL,
  P_HOURS,
  P_MIN_GAP,
  P_WEEKLY_CAP,
  WEEKLY_CAP_MAX,
  grantActive,
  grantText,
  hoursOf,
  isoToLocalInput,
  localInputToIso,
  numText,
} from "@/lib/assistant-tab";
import { cn } from "@/lib/utils";

export const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
export const NUMBOX =
  "min-h-11 w-24 rounded-lg border border-border bg-card px-3 py-1.5 text-center font-mono text-sm md:min-h-9";

export const Row = ({ title, sub, children }: { title: string; sub: string; children: React.ReactNode }) => (
  <div className="flex flex-wrap items-center gap-x-[18px] gap-y-2.5 border-t border-border py-3">
    <div className="min-w-0 flex-1 basis-56">
      <div className="font-medium">{title}</div>
      <div className="text-[12.5px] text-muted-foreground">{sub}</div>
    </div>
    {children}
  </div>
);

/** The user's own batch rules: daily / weekly / per-company limits, the wait between
 *  applications, the apply hours and "auto for my next N". Shared by Profile -> Assistant
 *  and the Needs-you "Batch apply" panel. Empty = not set (rule #29): nothing is ever
 *  written for the user. Every change goes through `save` / `saveMany` (a PATCH as the
 *  web user that applies at once; the click is the confirmation). */
export function BatchRuleRows({
  view,
  busy,
  save,
  saveMany,
}: {
  view: AssistantSettingsView;
  busy: boolean;
  save: (path: string, value: unknown) => void | Promise<void>;
  saveMany: (changes: { path: string; value: unknown }[]) => void | Promise<void>;
}) {
  const [cap, setCap] = useState("");
  const [companyCap, setCompanyCap] = useState("");
  const [companyDays, setCompanyDays] = useState("");
  const [weeklyCap, setWeeklyCap] = useState("");
  const [minGap, setMinGap] = useState("");
  const [hoursFrom, setHoursFrom] = useState("");
  const [hoursTo, setHoursTo] = useState("");
  const [grantCount, setGrantCount] = useState("");
  const [grantUntil, setGrantUntil] = useState("");
  const [confirmGrant, setConfirmGrant] = useState(false);
  const enterDone = useRef(false);

  // Show what the server now holds whenever a new view arrives (state adjusted
  // during render, the React-recommended way to reset state from a prop).
  const [seen, setSeen] = useState<AssistantSettingsView | null>(null);
  if (seen !== view) {
    setSeen(view);
    setCap(view.daily_cap.value == null ? "" : String(view.daily_cap.value));
    setCompanyCap(numText(view.company_cap));
    setCompanyDays(numText(view.company_cap_days));
    setWeeklyCap(numText(view.weekly_cap));
    setMinGap(numText(view.min_gap_minutes));
    setHoursFrom(hoursOf(view.apply_hours).from);
    setHoursTo(hoursOf(view.apply_hours).to);
    setGrantCount(numText(view.auto_grant_count));
    setGrantUntil(isoToLocalInput(String(view.auto_grant_until?.value ?? "")));
    setConfirmGrant(false);
  }

  const savedCap = view.daily_cap.value == null ? "" : String(view.daily_cap.value);
  const savedGrantCount = view.auto_grant_count?.value;
  const savedGrantUntil = String(view.auto_grant_until?.value ?? "");
  const grantOn = grantActive(savedGrantCount, savedGrantUntil);
  const numInput = (
    testid: string,
    label: string,
    value: string,
    setValue: (v: string) => void,
    commit: () => void,
    max: number,
    placeholder = "Not set",
  ) => (
    <input
      type="number"
      inputMode="numeric"
      min={1}
      max={max}
      aria-label={label}
      data-testid={testid}
      placeholder={placeholder}
      value={value}
      disabled={busy}
      onChange={(e) => {
        enterDone.current = false;
        setValue(e.target.value);
      }}
      onBlur={onBlurCommit(commit)}
      onKeyDown={onEnter(commit)}
      className={cn(NUMBOX, "w-24")}
    />
  );

  function commitCap() {
    const t = cap.trim();
    if (t === savedCap) return;
    const n = Number(t);
    if (t === "") void save(P_CAP, null);
    else if (Number.isInteger(n) && n >= 1 && n <= DAILY_CAP_MAX) void save(P_CAP, n);
    else {
      toast.error(`Daily limit must be a whole number from 1 to ${DAILY_CAP_MAX}, or blank for no limit. Not saved.`);
      setCap(savedCap);
    }
  }
  /** A whole-number setting: blank clears it (back to "not set"), 1..max saves, anything else is refused. */
  function commitNumber(
    path: string,
    text: string,
    saved: string,
    setText: (v: string) => void,
    opts: { max: number; label: string; blankMeans: string; min?: number },
  ) {
    const t = text.trim();
    if (t === saved) return;
    const n = Number(t);
    const min = opts.min ?? 1;
    if (t === "" || (min === 0 && n === 0 && Number.isInteger(n))) void save(path, null);
    else if (Number.isInteger(n) && n >= min && n <= opts.max) void save(path, n);
    else {
      toast.error(`${opts.label} must be a whole number from ${min === 0 ? 1 : min} to ${opts.max}, or blank for ${opts.blankMeans}. Not saved.`);
      setText(saved);
    }
  }
  function commitHours(from: string, to: string) {
    const was = hoursOf(view.apply_hours);
    if (from === was.from && to === was.to) return;
    if (from === "" && to === "") return void save(P_HOURS, null);
    if (from === "" || to === "" || from === to) {
      toast.error("Pick two different times, or clear both. Not saved.");
      setHoursFrom(was.from);
      setHoursTo(was.to);
      return;
    }
    void save(P_HOURS, { from, to });
  }
  function saveGrant() {
    const n = Number(grantCount.trim());
    const until = localInputToIso(grantUntil);
    const ok = Number.isInteger(n) && n >= 1 && n <= GRANT_COUNT_MAX && until !== "" && new Date(until).getTime() > Date.now();
    if (!ok) {
      toast.error(`Pick a number from 1 to ${GRANT_COUNT_MAX} and a time up to ${GRANT_MAX_DAYS} days from now. Not saved.`);
      setConfirmGrant(false);
      return;
    }
    setConfirmGrant(false);
    void saveMany([
      { path: P_GRANT_COUNT, value: n },
      { path: P_GRANT_UNTIL, value: until },
    ]);
  }
  /** Enter commits exactly like blur; the blur that may follow must not commit twice. */
  const onEnter = (commit: () => void) => (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    enterDone.current = true;
    commit();
  };
  const onBlurCommit = (commit: () => void) => () => {
    if (enterDone.current) {
      enterDone.current = false;
      return;
    }
    commit();
  };

  return (
    <>
          <Row title="Daily limit" sub="Most applications per day. Leave blank for no limit.">
            <input
              type="number"
              inputMode="numeric"
              min={1}
              max={DAILY_CAP_MAX}
              aria-label="Daily limit"
              data-testid="daily-cap"
              placeholder="No limit"
              value={cap}
              disabled={busy}
              onChange={(e) => {
                enterDone.current = false;
                setCap(e.target.value);
              }}
              onBlur={onBlurCommit(commitCap)}
              onKeyDown={onEnter(commitCap)}
              className={cn(NUMBOX, "w-32 min-w-[11ch] max-w-full")}
            />
          </Row>
          <Row title="Applications per company" sub="At most N applications per company in D days. Leave blank for no limit.">
            <span className="flex flex-wrap items-center gap-x-2 gap-y-2 text-[13px] text-muted-foreground">
              <span className="inline-flex items-center gap-2 whitespace-nowrap">
              At most
              {numInput(
                "company-cap", "Applications per company", companyCap, setCompanyCap,
                () => commitNumber(P_COMPANY_CAP, companyCap, numText(view.company_cap), setCompanyCap, { max: COMPANY_CAP_MAX, label: "The company limit", blankMeans: "no limit" }),
                COMPANY_CAP_MAX, "No limit",
              )}
              per company
              </span>
              <span className="inline-flex items-center gap-2 whitespace-nowrap">
              in
              {numInput(
                "company-cap-days", "Days for the company limit", companyDays, setCompanyDays,
                () => commitNumber(P_COMPANY_DAYS, companyDays, numText(view.company_cap_days), setCompanyDays, { max: COMPANY_DAYS_MAX, label: "The number of days", blankMeans: "the standard window" }),
                COMPANY_DAYS_MAX, "Days",
              )}
              days
              </span>
            </span>
          </Row>
          <Row title="Weekly limit" sub="Most applications per week (Monday to Sunday). Leave blank for no limit.">
            {numInput(
              "weekly-cap", "Weekly limit", weeklyCap, setWeeklyCap,
              () => commitNumber(P_WEEKLY_CAP, weeklyCap, numText(view.weekly_cap), setWeeklyCap, { max: WEEKLY_CAP_MAX, label: "Weekly limit", blankMeans: "no limit" }),
              WEEKLY_CAP_MAX, "No limit",
            )}
          </Row>
          <Row title="Wait between applications" sub="Wait at least N minutes between applications. Leave blank for no wait.">
            <span className="flex items-center gap-2 text-[13px] text-muted-foreground">
              Wait at least
              {numInput(
                "min-gap", "Minutes to wait between applications", minGap, setMinGap,
                () => commitNumber(P_MIN_GAP, minGap, numText(view.min_gap_minutes), setMinGap, { max: MIN_GAP_MAX, label: "The wait", blankMeans: "no wait", min: 0 }),
                MIN_GAP_MAX, "No wait",
              )}
              minutes
            </span>
          </Row>
          <Row title="Hours to apply" sub="Your own clock. Leave both blank to apply at any time. A later start than end means overnight.">
            <span className="flex flex-wrap items-center gap-2 text-[13px] text-muted-foreground">
              Only apply between
              <input
                type="time"
                aria-label="Apply from"
                data-testid="apply-hours-from"
                value={hoursFrom}
                disabled={busy}
                onChange={(e) => setHoursFrom(e.target.value)}
                onBlur={() => commitHours(hoursFrom, hoursTo)}
                className={cn(NUMBOX, "w-32")}
              />
              and
              <input
                type="time"
                aria-label="Apply until"
                data-testid="apply-hours-to"
                value={hoursTo}
                disabled={busy}
                onChange={(e) => setHoursTo(e.target.value)}
                onBlur={() => commitHours(hoursFrom, hoursTo)}
                className={cn(NUMBOX, "w-32")}
              />
              {(hoursFrom !== "" || hoursTo !== "") && (
                <button
                  type="button"
                  data-testid="apply-hours-clear"
                  disabled={busy}
                  onClick={() => {
                    setHoursFrom("");
                    setHoursTo("");
                    if (hoursOf(view.apply_hours).from !== "") void save(P_HOURS, null);
                  }}
                  className="min-h-11 rounded-md border border-border px-3 text-xs font-medium text-muted-foreground md:min-h-9"
                >
                  Clear
                </button>
              )}
            </span>
          </Row>
          <Row
            title="Auto for a while"
            sub={
              grantOn
                ? `${grantText(savedGrantCount, savedGrantUntil)}.`
                : "Let your assistant apply on its own for a set number of applications, then it goes back to asking you."
            }
          >
            <span className="flex flex-wrap items-center gap-2 text-[13px] text-muted-foreground">
              Auto for my next
              {numInput(
                "auto-grant-count", "How many applications", grantCount, setGrantCount,
                () => undefined, GRANT_COUNT_MAX, "N",
              )}
              applications until
              <input
                type="datetime-local"
                aria-label="Auto until"
                data-testid="auto-grant-until"
                value={grantUntil}
                disabled={busy}
                onChange={(e) => setGrantUntil(e.target.value)}
                className={cn(NUMBOX, "w-56")}
              />
              , then back to asking
              {!confirmGrant && (
                <button
                  type="button"
                  data-testid="auto-grant-save"
                  disabled={busy || grantCount.trim() === "" || grantUntil === ""}
                  onClick={() => setConfirmGrant(true)}
                  className="min-h-11 rounded-md bg-primary px-3 text-xs font-semibold text-primary-foreground disabled:opacity-50 md:min-h-9"
                >
                  Start
                </button>
              )}
              {grantOn && (
                <button
                  type="button"
                  data-testid="auto-grant-stop"
                  disabled={busy}
                  onClick={() => void saveMany([{ path: P_GRANT_COUNT, value: null }, { path: P_GRANT_UNTIL, value: null }])}
                  className="min-h-11 rounded-md border border-border px-3 text-xs font-medium text-muted-foreground md:min-h-9"
                >
                  Stop now
                </button>
              )}
            </span>
            {confirmGrant && (
              <span className="flex flex-wrap items-center gap-2 text-[13px]">
                Let your assistant apply on its own for your next {grantCount} applications?
                <button
                  type="button"
                  data-testid="auto-grant-confirm"
                  disabled={busy}
                  onClick={saveGrant}
                  className="min-h-11 rounded-md bg-primary px-3 text-xs font-semibold text-primary-foreground md:min-h-9"
                >
                  Confirm
                </button>
                <button
                  type="button"
                  data-testid="auto-grant-cancel"
                  onClick={() => setConfirmGrant(false)}
                  className="min-h-11 rounded-md border border-border px-3 text-xs font-medium text-muted-foreground md:min-h-9"
                >
                  Cancel
                </button>
              </span>
            )}
          </Row>
    </>
  );
}
