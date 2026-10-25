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
  HARD_STOPS,
  HUNT_EVERY_MAX,
  HISTORY_PATHS,
  INBOX_MODES,
  AUTO_EMAILS_HELP,
  OUTREACH_SEND_HELP,
  P_AUTO_EMAILS,
  P_OUTREACH_SEND,
  P_EVERY,
  P_HUNT_EVERY,
  P_INBOX,
  P_PEOPLE_HUNT,
  PEOPLE_HUNT_HELP,
  P_SCORE,
  PREF_HISTORY_PATHS,
  everyText,
  historyWhen,
  huntText,
  inboxKey,
  mergeHistory,
  peopleHuntLastText,
  settingAutoEmails,
  settingOutreachSend,
  settingScore,
  settingSwitch,
} from "@/lib/assistant-tab";
import type { ChangeRow, HistRow } from "@/lib/assistant-tab";
import {
  ALWAYS_ASKS,
  LINE_SWITCH_LABEL,
  batchEdits,
  batchOn,
  belowText,
  quotaText,
  scoreLineText,
} from "@/lib/morning-check";
import { cn } from "@/lib/utils";
import { BatchRuleRows, LABEL, NUMBOX, Row } from "./BatchRuleRows";

const Section = ({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) => (
  <section className="flex flex-col">
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 pb-2.5">
      <h2 className={LABEL}>{title}</h2>
      {hint && <span className="text-[12.5px] text-muted-foreground">{hint}</span>}
    </div>
    {children}
  </section>
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
  const [hunt, setHunt] = useState("");
  const [confirmAuto, setConfirmAuto] = useState(false);
  const [confirmOutreach, setConfirmOutreach] = useState(false);
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
    setHunt(settingScore(v.hunt_every_hours) == null ? "" : String(settingScore(v.hunt_every_hours)));
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
    // eslint-disable-next-line react-hooks/set-state-in-effect -- existing working effect; refactor tracked
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
  const belowLine = String(eff(view.below_line) || "ask");
  const autoEmails = settingAutoEmails(view.auto_employer_emails);
  const outreachSend = settingOutreachSend(view.outreach_send);
  const submit_mode = String(eff(view.submit_mode) ?? "confirm");
  const batch = batchOn(aboveLine, submit_mode);
  const inbox = inboxKey(view.inbox_mode);
  const every = view.check_every ?? "";
  const savedScore: number | null = settingScore(view.apply_min_score);
  const savedHunt: number | null = settingScore(view.hunt_every_hours);
  const peopleHunt = settingAutoEmails(view.people_hunt);
  const liveState = { apply_min_score: savedScore, above_line: aboveLine, below_line: belowLine, submit_mode };
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
  function commitHunt() {
    const t = hunt.trim();
    if (t === "" || t === "0") {
      // Off: nothing is written unless something was saved (rule #29).
      setHunt("");
      if (savedHunt !== null) void save(P_HUNT_EVERY, null);
      return;
    }
    const n = Number(t);
    if (!Number.isInteger(n) || n < 1 || n > HUNT_EVERY_MAX) {
      toast.error(`The job hunt runs every 1 to ${HUNT_EVERY_MAX} hours, or blank for off. Not saved.`);
      setHunt(savedHunt === null ? "" : String(savedHunt));
      return;
    }
    if (n !== savedHunt) void save(P_HUNT_EVERY, n);
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
          {/* Owner decision 2026-10-19: ONE switch. On = apply on its own to every job at or
              above the line; off = ask first. Jobs below the line are dropped. */}
          <div role="radiogroup" aria-label="Batch auto apply" className="grid gap-2 pt-3 md:grid-cols-2">
            {([false, true] as const).map((on) => (
              <Option
                key={String(on)}
                id={`batch-${on ? "on" : "off"}`}
                title={on ? "Batch auto apply: on" : "Batch auto apply: off"}
                sub={
                  on
                    ? "Your assistant applies on its own to every job at or above your score line."
                    : "Your assistant asks you before each application."
                }
                on={batch === on}
                disabled={busy}
                onPick={() => {
                  const edits = batchEdits(liveState, { scoreValue: savedScore, batch: on });
                  if (edits.length) void saveMany(edits);
                }}
              />
            ))}
          </div>
          <p data-testid="below-line-rule" className="pt-2 text-[12.5px] text-muted-foreground">
            {belowLine === "skip"
              ? "Jobs below your score line are dropped — never applied to."
              : `${belowText(belowLine)}. Choosing either option above drops them from now on.`}
          </p>
          <p data-testid="always-asks" className="mt-3 rounded-lg bg-warning-soft px-3 py-2 text-[13px] text-warning">
            {ALWAYS_ASKS}
          </p>
        </Section>

        <Section title="Limits & inbox">
          <BatchRuleRows view={view} busy={busy} save={save} saveMany={saveMany} />
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
          <Row
            title="Job hunt"
            sub="Your assistant looks for new jobs on a schedule you set, from its own task. Blank = off."
          >
            <label className="flex items-center gap-2 text-[13px] text-muted-foreground">
              every
              <input
                type="number"
                inputMode="numeric"
                min={1}
                max={HUNT_EVERY_MAX}
                aria-label="Hunt every"
                data-testid="hunt-every"
                placeholder="Off"
                value={hunt}
                disabled={busy}
                onChange={(e) => {
                  enterDone.current = false;
                  setHunt(e.target.value);
                }}
                onBlur={onBlurCommit(commitHunt)}
                onKeyDown={onEnter(commitHunt)}
                className={NUMBOX}
              />
              {savedHunt === 1 ? "hour" : "hours"}
            </label>
          </Row>
          <Row title="People hunt" sub={PEOPLE_HUNT_HELP}>
            <button
              type="button"
              role="switch"
              aria-checked={peopleHunt}
              aria-label="People hunt"
              data-testid="people-hunt-toggle"
              disabled={busy}
              onClick={() => void save(P_PEOPLE_HUNT, !peopleHunt)}
              className={cn(
                "min-h-11 rounded-lg border border-border px-3.5 text-sm font-medium md:min-h-9",
                peopleHunt ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted",
              )}
            >
              {peopleHunt ? "On" : "Off"}
            </button>
            {peopleHunt && (
              <span className="text-[13px] text-muted-foreground" data-testid="people-hunt-last">
                {peopleHuntLastText(view.people_hunt_last_at)}
              </span>
            )}
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
          <Row title="Automatic outreach emails" sub={OUTREACH_SEND_HELP}>
            <button
              type="button"
              role="switch"
              aria-checked={outreachSend}
              aria-label="Automatic outreach emails"
              data-testid="outreach-send-toggle"
              disabled={busy}
              onClick={() => {
                if (outreachSend) {
                  setConfirmOutreach(false);
                  void save(P_OUTREACH_SEND, "draft_only"); // the stop switch applies at once
                } else {
                  setConfirmOutreach(true);
                }
              }}
              className={cn(
                "min-h-11 rounded-lg border border-border px-3.5 text-sm font-medium md:min-h-9",
                outreachSend ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted",
              )}
            >
              {outreachSend ? "On" : "Off"}
            </button>
            {confirmOutreach && !outreachSend && (
              <span className="flex items-center gap-2 text-[13px]">
                Let your assistant send outreach emails from your Gmail in your name?
                <button
                  type="button"
                  data-testid="outreach-send-confirm"
                  disabled={busy}
                  onClick={() => {
                    setConfirmOutreach(false);
                    void save(P_OUTREACH_SEND, "auto");
                  }}
                  className="min-h-11 rounded-md bg-primary px-3 text-xs font-semibold text-primary-foreground md:min-h-9"
                >
                  Confirm
                </button>
                <button
                  type="button"
                  data-testid="outreach-send-cancel"
                  onClick={() => setConfirmOutreach(false)}
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
                ["Batch auto apply", batch ? "On" : "Off"],
                ["Below the line", LINE_SWITCH_LABEL[belowLine] ?? belowLine],
                ...(today ? [["Today", quotaText(today.state.applied_today, today.state.daily_cap).replace(/ today$/, "")]] : []),
                ...(inbox ? [["Gmail check", [INBOX_MODES.find((m) => m.value === inbox)?.label, everyText(every)].filter(Boolean).join(", ")]] : []),
                ...(savedHunt !== null ? [["Job hunt", huntText(savedHunt)]] : []),
                ...(peopleHunt ? [["People hunt", "On"]] : []),
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
