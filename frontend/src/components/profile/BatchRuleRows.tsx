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
  P_SUBMIT,
  P_WEEKLY_CAP,
  WEEKLY_CAP_MAX,
  grantActive,
  grantText,
  historyWhen,
  hoursOf,
  isoToLocalInput,
  localInputToIso,
  numText,
  scopeText,
  settingEffective,
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

/** A blue "No limit" tick (owner ask 2026-10-18: "a blue tick, like verified"). */
export function NoLimitTick({
  testid, on, busy, onToggle,
}: { testid: string; on: boolean; busy: boolean; onToggle: (next: boolean) => void }) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={on}
      data-testid={testid}
      disabled={busy}
      onClick={() => onToggle(!on)}
      className="inline-flex min-h-11 items-center gap-2 rounded-md px-1 text-[13px] font-medium text-foreground md:min-h-9"
    >
      <span
        aria-hidden
        className={cn(
          "inline-flex size-5 items-center justify-center rounded-full border-2 transition-colors",
          on ? "border-[#1d9bf0] bg-[#1d9bf0] text-white" : "border-border bg-card text-transparent",
        )}
      >
        <svg viewBox="0 0 16 16" className="size-3" fill="none" stroke="currentColor" strokeWidth={2.5}>
          <path d="M3.5 8.5l3 3 6-7" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      No limit
    </button>
  );
}

/** Saved minutes (whole seconds, 1.5 = 1 min 30 s) as the two boxes; "" = not set. */
export function gapParts(v: unknown): { min: string; sec: string } {
  if (v == null || v === "") return { min: "", sec: "" };
  const total = Math.round(Number(v) * 60);
  if (!Number.isFinite(total)) return { min: "", sec: "" };
  return { min: String(Math.floor(total / 60)), sec: total % 60 ? String(total % 60) : "" };
}

export type ApplyMode = "ask" | "straight" | "while";
export const APPLY_MODES: { value: ApplyMode; title: string; sub: string }[] = [
  { value: "straight", title: "Straight away", sub: "Your assistant applies on its own, at any time." },
  { value: "while", title: "Auto for a while", sub: "Your assistant applies on its own for your next N jobs, then asks you again." },
  { value: "ask", title: "Ask me each time", sub: "Your assistant fills the form and waits on Needs you for your Submit." },
];

/** Which of the modes the saved settings are in: a live grant, else sending on its own, else asking. */
export function modeOf(view: AssistantSettingsView): ApplyMode {
  if (grantActive(view.auto_grant_count?.value, String(view.auto_grant_until?.value ?? ""))) return "while";
  if (settingEffective(view.submit_mode) !== "auto_when_sure") return "ask";
  return "straight";
}

/** The user's own batch rules: daily / weekly / per-company limits, the wait between
 *  applications and "auto for my next N". Shared by Profile -> Assistant
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
  const [gapSec, setGapSec] = useState("");
  const [mode, setMode] = useState<ApplyMode>("ask");
  const [confirmStraight, setConfirmStraight] = useState(false);
  const [companyOpen, setCompanyOpen] = useState(false);
  const [weeklyOpen, setWeeklyOpen] = useState(false);
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
    setMinGap(gapParts(view.min_gap_minutes?.value).min);
    setGapSec(gapParts(view.min_gap_minutes?.value).sec);
    setMode(modeOf(view));
    setConfirmStraight(false);
    setCompanyOpen(false);
    setWeeklyOpen(false);
    setGrantCount(numText(view.auto_grant_count));
    setGrantUntil(isoToLocalInput(String(view.auto_grant_until?.value ?? "")));
    setConfirmGrant(false);
  }

  const savedCap = view.daily_cap.value == null ? "" : String(view.daily_cap.value);
  const savedGrantCount = view.auto_grant_count?.value;
  const savedGrantUntil = String(view.auto_grant_until?.value ?? "");
  const grantOn = grantActive(savedGrantCount, savedGrantUntil);
  // A window saved by the removed "Hours to apply" card still limits sending; show it so it can be cleared.
  const oldHours = hoursOf(view.apply_hours);
  // Slice 30: a grant covers only the queue the user sees here when they confirm it.
  const scope = view.auto_grant_scope;
  const queueNow = scope?.queue;
  const covered = scopeText(scope?.granted);
  const numInput = (
    testid: string,
    label: string,
    value: string,
    setValue: (v: string) => void,
    commit: () => void,
    max: number,
    placeholder = "Not set",
    min = 1,
  ) => (
    <input
      type="number"
      inputMode="numeric"
      min={min}
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
  /** Days for the company limit: blank = the standard window, 0 = all time, 1..365. */
  function commitDays() {
    const t = companyDays.trim();
    const saved = numText(view.company_cap_days);
    if (t === saved) return;
    const n = Number(t);
    if (t === "") void save(P_COMPANY_DAYS, null);
    else if (Number.isInteger(n) && n >= 0 && n <= COMPANY_DAYS_MAX) void save(P_COMPANY_DAYS, n);
    else {
      toast.error(`Days must be a whole number from 0 (all time) to ${COMPANY_DAYS_MAX}. Not saved.`);
      setCompanyDays(saved);
    }
  }
  /** Minutes + seconds -> minutes in whole seconds (both blank = no wait; 0 and 0 = no wait). */
  function commitGap(minText: string, secText: string) {
    const was = gapParts(view.min_gap_minutes?.value);
    if (minText.trim() === was.min && secText.trim() === was.sec) return;
    if (minText.trim() === "" && secText.trim() === "") return void save(P_MIN_GAP, null);
    const m = Number(minText.trim() || "0");
    const sec = Number(secText.trim() || "0");
    const ok = Number.isInteger(m) && Number.isInteger(sec) && m >= 0 && sec >= 0 && sec <= 59 && m * 60 + sec <= MIN_GAP_MAX * 60;
    if (!ok) {
      toast.error(`The wait must be 0 to ${MIN_GAP_MAX} minutes and 0 to 59 seconds. Not saved.`);
      setMinGap(was.min);
      setGapSec(was.sec);
      return;
    }
    const total = m * 60 + sec;
    void save(P_MIN_GAP, total === 0 ? null : total % 60 === 0 ? total / 60 : Math.round((total / 60) * 10000) / 10000);
  }
  /** The other two modes' settings, cleared, so exactly one mode is on. */
  const clearGrant = [{ path: P_GRANT_COUNT, value: null }, { path: P_GRANT_UNTIL, value: null }];
  function pickMode(next: ApplyMode) {
    setConfirmStraight(false);
    setConfirmGrant(false);
    setMode(next);
    if (next === "ask") {
      void saveMany([{ path: P_SUBMIT, value: "confirm" }, { path: P_HOURS, value: null }, ...clearGrant]);
    } else if (next === "straight") {
      setConfirmStraight(true);
    }
    // "while" saves on Confirm. Ask, straight and while each clear any old apply-hours window.
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
    // "Auto for a while" alone: back to asking afterwards, and any old hours window cleared.
    void saveMany([
      { path: P_GRANT_COUNT, value: n },
      { path: P_GRANT_UNTIL, value: until },
      { path: P_SUBMIT, value: "confirm" },
      { path: P_HOURS, value: null },
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
          <Row
            title="Applications per company"
            sub={companyCap === "" && !companyOpen ? "No limit." : "At most N applications per company in D days (0 days = all time)."}
          >
            <span className="flex flex-wrap items-center gap-x-3 gap-y-2 text-[13px] text-muted-foreground">
              <NoLimitTick
                testid="company-cap-none"
                on={companyCap === "" && !companyOpen}
                busy={busy}
                onToggle={(none) => {
                  if (none) {
                    setCompanyOpen(false);
                    setCompanyCap("");
                    if (numText(view.company_cap) !== "") void save(P_COMPANY_CAP, null);
                  } else setCompanyOpen(true);
                }}
              />
              {(companyCap !== "" || companyOpen) && (
                <>
                  <span className="inline-flex items-center gap-2 whitespace-nowrap">
                    At most
                    {numInput(
                      "company-cap", "Applications per company", companyCap, setCompanyCap,
                      () => commitNumber(P_COMPANY_CAP, companyCap, numText(view.company_cap), setCompanyCap, { max: COMPANY_CAP_MAX, label: "The company limit", blankMeans: "no limit", min: 0 }),
                      COMPANY_CAP_MAX, "N",
                    )}
                    per company
                  </span>
                  <span className="inline-flex items-center gap-2 whitespace-nowrap">
                    in
                    {numInput(
                      "company-cap-days", "Days for the company limit", companyDays, setCompanyDays,
                      () => commitDays(),
                      COMPANY_DAYS_MAX, "180",
                    )}
                    days
                  </span>
                </>
              )}
            </span>
          </Row>
          <Row title="Weekly limit" sub="Most applications per week (Monday to Sunday).">
            <span className="flex flex-wrap items-center gap-x-3 gap-y-2 text-[13px] text-muted-foreground">
              <NoLimitTick
                testid="weekly-cap-none"
                on={weeklyCap === "" && !weeklyOpen}
                busy={busy}
                onToggle={(none) => {
                  if (none) {
                    setWeeklyOpen(false);
                    setWeeklyCap("");
                    if (numText(view.weekly_cap) !== "") void save(P_WEEKLY_CAP, null);
                  } else setWeeklyOpen(true);
                }}
              />
              {(weeklyCap !== "" || weeklyOpen) &&
                numInput(
                  "weekly-cap", "Weekly limit", weeklyCap, setWeeklyCap,
                  () => commitNumber(P_WEEKLY_CAP, weeklyCap, numText(view.weekly_cap), setWeeklyCap, { max: WEEKLY_CAP_MAX, label: "Weekly limit", blankMeans: "no limit", min: 0 }),
                  WEEKLY_CAP_MAX, "N",
                )}
            </span>
          </Row>
          <Row title="Wait between applications" sub="Minutes and seconds. 0 or blank = no wait.">
            <span className="flex flex-wrap items-center gap-2 text-[13px] text-muted-foreground">
              Wait at least
              {numInput(
                "min-gap", "Minutes to wait between applications", minGap, setMinGap,
                () => commitGap(minGap, gapSec), MIN_GAP_MAX, "0", 0,
              )}
              min
              {numInput(
                "min-gap-seconds", "Seconds to wait between applications", gapSec, setGapSec,
                () => commitGap(minGap, gapSec), 59, "0", 0,
              )}
              s
            </span>
          </Row>
          <div className="border-t border-border py-3">
            <div className="font-medium">How your assistant applies</div>
            <div className="text-[12.5px] text-muted-foreground">
              Pick one. Anything it cannot do (a login, a CAPTCHA, a question only you can answer) waits for you on Needs you.
            </div>
            <div role="radiogroup" aria-label="How your assistant applies" className="mt-2 grid gap-2 sm:grid-cols-2">
              {APPLY_MODES.map((m) => (
                <button
                  key={m.value}
                  type="button"
                  role="radio"
                  aria-checked={mode === m.value}
                  data-testid={`mode-${m.value}`}
                  disabled={busy}
                  onClick={() => mode !== m.value && pickMode(m.value)}
                  className={cn(
                    "flex min-h-11 flex-col items-start rounded-lg border px-3 py-2 text-left",
                    mode === m.value ? "border-[#1d9bf0] bg-[#1d9bf0]/10" : "border-border bg-card",
                  )}
                >
                  <span className="flex items-center gap-2 text-sm font-medium">
                    <span
                      aria-hidden
                      className={cn(
                        "size-3.5 rounded-full border-2",
                        mode === m.value ? "border-[#1d9bf0] bg-[#1d9bf0]" : "border-border",
                      )}
                    />
                    {m.title}
                  </span>
                  <span className="text-[12px] text-muted-foreground">{m.sub}</span>
                </button>
              ))}
            </div>
            {oldHours.from !== "" && (
              <span className="mt-2 flex flex-wrap items-center gap-2 text-[13px] text-muted-foreground" data-testid="old-apply-hours">
                Your assistant still only applies between {oldHours.from} and {oldHours.to} (a window you saved earlier).
                <button
                  type="button"
                  data-testid="old-apply-hours-clear"
                  disabled={busy}
                  onClick={() => void save(P_HOURS, null)}
                  className="min-h-11 rounded-md border border-border px-3 text-xs font-medium text-muted-foreground md:min-h-9"
                >
                  Clear
                </button>
              </span>
            )}
            {confirmStraight && (
              <span className="mt-2 flex flex-wrap items-center gap-2 text-[13px]" data-testid="mode-straight-ask">
                Let your assistant apply on its own, at any time? Indeed, LinkedIn and the first practice run still wait for you.
                <button
                  type="button"
                  data-testid="mode-straight-confirm"
                  disabled={busy}
                  onClick={() => {
                    setConfirmStraight(false);
                    void saveMany([{ path: P_SUBMIT, value: "auto_when_sure" }, { path: P_HOURS, value: null }, ...clearGrant]);
                  }}
                  className="min-h-11 rounded-md bg-primary px-3 text-xs font-semibold text-primary-foreground md:min-h-9"
                >
                  Confirm
                </button>
                <button
                  type="button"
                  data-testid="mode-straight-cancel"
                  onClick={() => {
                    setConfirmStraight(false);
                    setMode(modeOf(view));
                  }}
                  className="min-h-11 rounded-md border border-border px-3 text-xs font-medium text-muted-foreground md:min-h-9"
                >
                  Cancel
                </button>
              </span>
            )}
            {mode === "while" && (
              <div className="mt-2 flex flex-col gap-2">
                {grantOn && (
                  <span data-testid="auto-grant-on" className="text-[12.5px] text-muted-foreground">
                    {grantText(savedGrantCount, savedGrantUntil)}.
                    {covered && ` It covers the ${covered} that were in your queue when you said yes.`}
                  </span>
                )}
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
                      onClick={() => void saveMany(clearGrant)}
                      className="min-h-11 rounded-md border border-border px-3 text-xs font-medium text-muted-foreground md:min-h-9"
                    >
                      Stop now
                    </button>
                  )}
                </span>
                {confirmGrant && (
                  <span className="flex flex-wrap items-center gap-2 text-[13px]">
                    <span data-testid="auto-grant-scope">
                      {queueNow && queueNow.count === 0
                        ? "Your queue is empty, so nothing would be sent on its own. Queue the jobs first."
                        : `Auto-submit the ${scopeText(queueNow) || "applications"} in your queue now, at most ${grantCount}, ` +
                          `until ${historyWhen(localInputToIso(grantUntil))}? Jobs you queue later, or whose form ` +
                          "moves to another site, will still ask you."}
                    </span>
                    <button
                      type="button"
                      data-testid="auto-grant-confirm"
                      disabled={busy || queueNow?.count === 0}
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
              </div>
            )}
          </div>
    </>
  );
}
