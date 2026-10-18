"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  getAssistantSettings,
  getMorningCheck,
  getProfileEditHistory,
  getSettingHistory,
  takeBackSetting,
  updateProfileFields,
} from "@/lib/api";
import type { AssistantSettingsView, MorningCheck } from "@/lib/api";
import {
  CHECK_EVERY,
  COMPANY_CAP_MAX,
  COMPANY_DAYS_MAX,
  DAILY_CAP_MAX,
  GRANT_COUNT_MAX,
  GRANT_MAX_DAYS,
  HARD_STOPS,
  HISTORY_PATHS,
  INBOX_MODES,
  LINE_SWITCHES,
  AUTO_EMAILS_HELP,
  P_ABOVE,
  P_AUTO_EMAILS,
  P_BELOW,
  MIN_GAP_MAX,
  P_CAP,
  P_COMPANY_CAP,
  P_COMPANY_DAYS,
  P_EVERY,
  P_GRANT_COUNT,
  P_GRANT_UNTIL,
  P_HOURS,
  P_INBOX,
  P_MIN_GAP,
  P_WEEKLY_CAP,
  WEEKLY_CAP_MAX,
  P_SCORE,
  P_SUBMIT,
  PREF_HISTORY_PATHS,
  SUBMIT_MODES,
  everyText,
  grantActive,
  grantText,
  historyWhen,
  hoursOf,
  isoToLocalInput,
  localInputToIso,
  numText,
  inboxKey,
  mergeHistory,
  settingAutoEmails,
  settingScore,
  settingSwitch,
} from "@/lib/assistant-tab";
import type { ChangeRow, HistRow } from "@/lib/assistant-tab";
import { LINE_SWITCH_LABEL, SUBMIT_MODE_LABEL, quotaText, scoreLineText } from "@/lib/morning-check";
import { cn } from "@/lib/utils";

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const NUMBOX =
  "min-h-11 w-24 rounded-lg border border-border bg-card px-3 py-1.5 text-center font-mono text-sm md:min-h-9";

const Section = ({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) => (
  <section className="flex flex-col">
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 pb-2.5">
      <h2 className={LABEL}>{title}</h2>
      {hint && <span className="text-[12.5px] text-muted-foreground">{hint}</span>}
    </div>
    {children}
  </section>
);

const Row = ({ title, sub, children }: { title: string; sub: string; children: React.ReactNode }) => (
  <div className="flex flex-wrap items-center gap-x-[18px] gap-y-2.5 border-t border-border py-3">
    <div className="min-w-0 flex-1 basis-56">
      <div className="font-medium">{title}</div>
      <div className="text-[12.5px] text-muted-foreground">{sub}</div>
    </div>
    {children}
  </div>
);

function Option({
  id,
  title,
  sub,
  on,
  disabled,
  onPick,
}: {
  id: string;
  title: string;
  sub: string;
  on: boolean;
  disabled: boolean;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      data-testid={id}
      disabled={disabled}
      onClick={onPick}
      className={cn(
        "flex min-h-11 items-start gap-2.5 rounded-xl border bg-card px-3.5 py-3 text-left transition-colors hover:bg-muted disabled:opacity-60",
        on ? "border-brand shadow-[0_0_0_1px_var(--brand)]" : "border-border",
      )}
    >
      <span
        aria-hidden="true"
        className={cn("mt-1 h-3.5 w-3.5 flex-none rounded-full border-[1.5px]", on ? "border-brand bg-primary" : "border-faint")}
      />
      <span>
        <span className="block font-medium">{title}</span>
        <span className="block text-[12.5px] text-muted-foreground">{sub}</span>
      </span>
    </button>
  );
}

function LineSwitch({
  title,
  testPrefix,
  current,
  busy,
  onPick,
}: {
  title: string;
  testPrefix: string;
  current: string;
  busy: boolean;
  onPick: (v: "ask" | "auto") => void;
}) {
  return (
    <Row title={title} sub={current === "auto" ? "Your assistant applies on its own." : "Your assistant asks you first."}>
      <div role="radiogroup" aria-label={title} className="inline-flex overflow-hidden rounded-lg border border-border">
        {LINE_SWITCHES.map((m, i) => (
          <button
            key={m.value}
            type="button"
            role="radio"
            aria-checked={current === m.value}
            data-testid={`${testPrefix}-${m.value}`}
            disabled={busy}
            onClick={() => current !== m.value && onPick(m.value)}
            className={cn(
              "min-h-11 px-3.5 text-sm font-medium md:min-h-9",
              i > 0 && "border-l border-border",
              current === m.value ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted",
            )}
          >
            {m.label}
          </button>
        ))}
      </div>
    </Row>
  );
}

const eff = (f: { effective?: unknown; value?: unknown } | undefined) => f?.effective ?? f?.value;

/** Profile -> Assistant: the score line, how it submits, its limits
 *  and inbox, the hard stops that cannot change, and every change with Take back.
 *  Every control is a PATCH as the web user and applies at once. */
export function AssistantTab() {
  const [view, setView] = useState<AssistantSettingsView | null>(null);
  const [rows, setRows] = useState<ChangeRow[]>([]);
  const [today, setToday] = useState<MorningCheck | null>(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [score, setScore] = useState("");
  const [cap, setCap] = useState("");
  const [confirmAuto, setConfirmAuto] = useState(false);
  // Pace and company limits (empty = not set; nothing is ever written for the user).
  const [companyCap, setCompanyCap] = useState("");
  const [companyDays, setCompanyDays] = useState("");
  const [weeklyCap, setWeeklyCap] = useState("");
  const [minGap, setMinGap] = useState("");
  const [hoursFrom, setHoursFrom] = useState("");
  const [hoursTo, setHoursTo] = useState("");
  // "Auto for my next N applications until <time>" - saved together, after one confirm.
  const [grantCount, setGrantCount] = useState("");
  const [grantUntil, setGrantUntil] = useState("");
  const [confirmGrant, setConfirmGrant] = useState(false);
  const enterDone = useRef(false);

  const readHistory = useCallback(async () => {
    const lists = await Promise.all(
      HISTORY_PATHS.map(async (p): Promise<[string, HistRow[]]> => {
        try {
          const isPref = (PREF_HISTORY_PATHS as readonly string[]).includes(p);
          return [p, await (isPref ? getProfileEditHistory(p) : getSettingHistory(p))];
        } catch {
          return [p, []];
        }
      }),
    );
    setRows(mergeHistory(Object.fromEntries(lists)));
  }, []);

  const apply = useCallback((v: AssistantSettingsView) => {
    setView(v);
    setScore(settingScore(v.apply_min_score) == null ? "" : String(settingScore(v.apply_min_score)));
    setCap(v.daily_cap.value == null ? "" : String(v.daily_cap.value));
    setCompanyCap(numText(v.company_cap));
    setCompanyDays(numText(v.company_cap_days));
    setWeeklyCap(numText(v.weekly_cap));
    setMinGap(numText(v.min_gap_minutes));
    setHoursFrom(hoursOf(v.apply_hours).from);
    setHoursTo(hoursOf(v.apply_hours).to);
    setGrantCount(numText(v.auto_grant_count));
    setGrantUntil(isoToLocalInput(String(v.auto_grant_until?.value ?? "")));
    setConfirmGrant(false);
  }, []);

  const load = useCallback(async () => {
    try {
      apply(await getAssistantSettings());
      setError(false);
    } catch {
      setError(true);
      return;
    }
    await readHistory();
    try {
      setToday(await getMorningCheck(new Date().toISOString()));
    } catch {
      setToday(null);
    }
  }, [apply, readHistory]);

  useEffect(() => {
    void load();
  }, [load]);

  /** One change, as the web user. The page shows what the server now holds. */
  async function save(path: string, value: unknown) {
    setBusy(true);
    try {
      await updateProfileFields([{ path, value }]);
      toast.success("Saved.");
      await load();
    } catch {
      toast.error("Could not save. Try again.");
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function saveMany(changes: { path: string; value: unknown }[]) {
    setBusy(true);
    try {
      await updateProfileFields(changes);
      toast.success("Saved.");
    } catch {
      toast.error("Could not save. Try again.");
    } finally {
      await load();
      setBusy(false);
    }
  }

  async function takeBack(r: ChangeRow) {
    setBusy(true);
    try {
      if ((PREF_HISTORY_PATHS as readonly string[]).includes(r.path)) await updateProfileFields([{ path: r.path, value: r.previous }]);
      else await takeBackSetting(r.path);
      toast.success("Saved.");
      await load();
    } catch {
      toast.error("Could not take that back. Try again.");
    } finally {
      setBusy(false);
    }
  }

  if (error && !view) {
    return (
      <div role="alert" className="flex items-center gap-3 pt-6 text-sm text-destructive">
        <span>Could not load your assistant settings.</span>
        <button type="button" data-testid="assistant-retry" onClick={() => void load()} className="text-xs font-medium underline-offset-2 hover:underline">
          Try again
        </button>
      </div>
    );
  }
  if (!view) return <p className="pt-6 text-sm text-muted-foreground">Loading…</p>;

  const aboveLine = settingSwitch(view.above_line);
  const belowLine = settingSwitch(view.below_line);
  const autoEmails = settingAutoEmails(view.auto_employer_emails);
  const submit_mode = String(eff(view.submit_mode) ?? "confirm");
  const inbox = inboxKey(view.inbox_mode);
  const every = view.check_every ?? "";
  const savedScore: number | null = settingScore(view.apply_min_score);
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

  function commitScore() {
    const t = score.trim();
    if (t === "") {
      // Cleared: the line goes back to "not set" (no default is ever written).
      if (savedScore !== null) void save(P_SCORE, null);
      return;
    }
    const n = Number(t);
    if (!Number.isInteger(n) || n < 0 || n > 100) {
      toast.error("Score line must be a whole number from 0 to 100. Not saved.");
      setScore(savedScore === null ? "" : String(savedScore));
      return;
    }
    if (n !== savedScore) void save(P_SCORE, n);
  }
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
    const was = hoursOf(view?.apply_hours);
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
    <div className="grid gap-8 pt-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <div className="flex min-w-0 flex-col gap-8" data-testid="assistant-tab">
        <Section title="Your score line" hint="Your assistant scores each job. You choose what happens on each side of your line.">
          <Row title="Score line" sub="A whole number from 0 to 100. It is yours; nothing is set for you.">
            <input
              type="number"
              inputMode="numeric"
              min={0}
              max={100}
              aria-label="Score line"
              data-testid="score-input"
              placeholder="Not set"
              value={score}
              disabled={busy}
              onChange={(e) => {
                enterDone.current = false;
                setScore(e.target.value);
              }}
              onBlur={onBlurCommit(commitScore)}
              onKeyDown={onEnter(commitScore)}
              className={NUMBOX}
            />
            {savedScore === null && (
              <span data-testid="score-line-unset" className="basis-full text-[12.5px] text-muted-foreground">
                Not set — your assistant will ask you
              </span>
            )}
          </Row>
          <LineSwitch
            title="Jobs at or above your line"
            testPrefix="above-line"
            current={aboveLine}
            busy={busy}
            onPick={(v) => void save(P_ABOVE, v)}
          />
          <LineSwitch
            title="Jobs below your line"
            testPrefix="below-line"
            current={belowLine}
            busy={busy}
            onPick={(v) => void save(P_BELOW, v)}
          />
        </Section>

        <Section title="Submit mode" hint="The last click on the form">
          <div role="radiogroup" aria-label="Submit mode" className="grid gap-2 md:grid-cols-2">
            {SUBMIT_MODES.map((m) => (
              <Option
                key={m.value}
                id={`submit-mode-${m.value}`}
                title={SUBMIT_MODE_LABEL[m.value]}
                sub={m.sub}
                on={submit_mode === m.value}
                disabled={busy}
                onPick={() => submit_mode !== m.value && void save(P_SUBMIT, m.value)}
              />
            ))}
          </div>
        </Section>

        <Section title="Limits & inbox">
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
            <span className="flex flex-wrap items-center gap-2 text-[13px] text-muted-foreground">
              At most
              {numInput(
                "company-cap", "Applications per company", companyCap, setCompanyCap,
                () => commitNumber(P_COMPANY_CAP, companyCap, numText(view.company_cap), setCompanyCap, { max: COMPANY_CAP_MAX, label: "The company limit", blankMeans: "no limit" }),
                COMPANY_CAP_MAX, "No limit",
              )}
              applications per company in
              {numInput(
                "company-cap-days", "Days for the company limit", companyDays, setCompanyDays,
                () => commitNumber(P_COMPANY_DAYS, companyDays, numText(view.company_cap_days), setCompanyDays, { max: COMPANY_DAYS_MAX, label: "The number of days", blankMeans: "the standard window" }),
                COMPANY_DAYS_MAX, "Days",
              )}
              days
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
                () => undefined, GRANT_COUNT_MAX, "How many",
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
          <Row title="Gmail check" sub="Your assistant reads your inbox and records replies.">
            <div role="radiogroup" aria-label="Gmail check" className="inline-flex overflow-hidden rounded-lg border border-border">
              {INBOX_MODES.map((m, i) => (
                <button
                  key={m.value}
                  type="button"
                  role="radio"
                  aria-checked={inbox === m.value}
                  data-testid={`inbox-${m.value}`}
                  disabled={busy}
                  onClick={() => inbox !== m.value && void save(P_INBOX, m.value)}
                  className={cn(
                    "min-h-11 px-3.5 text-sm font-medium md:min-h-9",
                    i > 0 && "border-l border-border",
                    inbox === m.value ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted",
                  )}
                >
                  {m.label}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-2 text-[13px] text-muted-foreground">
              every
              <select
                aria-label="Check every"
                data-testid="check-every"
                value={every}
                disabled={busy}
                onChange={(e) => void save(P_EVERY, e.target.value === "" ? null : e.target.value)}
                className="min-h-11 rounded-lg border border-border bg-card px-2 font-mono text-sm md:min-h-9"
              >
                <option value="">-</option>
                {CHECK_EVERY.map((h) => (
                  <option key={h} value={h}>
                    {h.replace("h", "")}
                  </option>
                ))}
              </select>
              {every === "1h" ? "hour" : "hours"}
            </label>
          </Row>
        </Section>

        <Section title="Employer emails" hint="Sent in your name by your assistant">
          <Row title="Automatic employer emails" sub={AUTO_EMAILS_HELP}>
            <button
              type="button"
              role="switch"
              aria-checked={autoEmails}
              aria-label="Automatic employer emails"
              data-testid="auto-employer-emails-toggle"
              disabled={busy}
              onClick={() => {
                if (autoEmails) {
                  setConfirmAuto(false);
                  void save(P_AUTO_EMAILS, false); // the stop switch applies at once
                } else {
                  setConfirmAuto(true);
                }
              }}
              className={cn(
                "min-h-11 rounded-lg border border-border px-3.5 text-sm font-medium md:min-h-9",
                autoEmails ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted",
              )}
            >
              {autoEmails ? "On" : "Off"}
            </button>
            {confirmAuto && !autoEmails && (
              <span className="flex items-center gap-2 text-[13px]">
                Let your assistant email employers in your name?
                <button
                  type="button"
                  data-testid="auto-employer-emails-confirm"
                  disabled={busy}
                  onClick={() => {
                    setConfirmAuto(false);
                    void save(P_AUTO_EMAILS, true);
                  }}
                  className="min-h-11 rounded-md bg-primary px-3 text-xs font-semibold text-primary-foreground md:min-h-9"
                >
                  Confirm
                </button>
                <button
                  type="button"
                  data-testid="auto-employer-emails-cancel"
                  onClick={() => setConfirmAuto(false)}
                  className="min-h-11 rounded-md border border-border px-3 text-xs font-medium text-muted-foreground md:min-h-9"
                >
                  Cancel
                </button>
              </span>
            )}
          </Row>
        </Section>

        <Section title="Hard stops" hint="Always on. Cannot be switched off.">
          {HARD_STOPS.map((t) => (
            <div key={t} data-testid="hard-stop" className="flex items-baseline gap-2.5 border-t border-border py-2.5 text-[13.5px]">
              <span className="rounded-full border border-border px-2 py-px font-mono text-[11px] font-medium text-faint">ALWAYS</span>
              <span>{t}</span>
            </div>
          ))}
        </Section>

        <Section title="History" hint="Every change, newest first">
          {rows.length === 0 && <p className="border-t border-border py-3 text-sm text-faint">No changes yet</p>}
          {rows.map((r) => (
            <div
              key={r.key}
              data-testid="history-row"
              className="grid grid-cols-1 gap-x-3.5 gap-y-1 border-t border-border py-2.5 text-[13.5px] sm:grid-cols-[6.5rem_minmax(0,1fr)_auto]"
            >
              <span className="font-mono text-xs text-faint">{historyWhen(r.at)}</span>
              <span className="min-w-0">
                <span className="font-medium">{r.by}</span> {r.label}: <s className="text-faint">{r.was}</s> → {r.now}
              </span>
              {r.newest ? (
                <button
                  type="button"
                  data-testid="history-take-back"
                  disabled={busy}
                  onClick={() => void takeBack(r)}
                  className="min-h-11 text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:opacity-50 md:min-h-0"
                >
                  Take back
                </button>
              ) : (
                <span />
              )}
            </div>
          ))}
        </Section>
      </div>

      <aside className="flex flex-col gap-6" data-testid="assistant-rail">
        <section>
          <h2 className={`${LABEL} mb-2`}>Right now</h2>
          <dl className="flex flex-col gap-1.5 text-[13px]">
            {(
              [
                ["Score line", scoreLineText(savedScore).replace(/^Score line /, "")],
                ["At or above", LINE_SWITCH_LABEL[aboveLine] ?? aboveLine],
                ["Below", LINE_SWITCH_LABEL[belowLine] ?? belowLine],
                ["Submit", SUBMIT_MODE_LABEL[submit_mode] ?? submit_mode],
                ...(today ? [["Today", quotaText(today.state.applied_today, today.state.daily_cap).replace(/ today$/, "")]] : []),
                ...(inbox ? [["Gmail check", [INBOX_MODES.find((m) => m.value === inbox)?.label, everyText(every)].filter(Boolean).join(", ")]] : []),
              ] as [string, string][]
            ).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3">
                <dt className="text-muted-foreground">{k}</dt>
                <dd className="text-right font-medium">{v}</dd>
              </div>
            ))}
          </dl>
        </section>
        <p className="text-[13px] leading-relaxed text-muted-foreground">
          Changes take effect at once. Each one is listed in History with who made it. Take back adds a new row; nothing is erased.
        </p>
      </aside>
    </div>
  );
}
