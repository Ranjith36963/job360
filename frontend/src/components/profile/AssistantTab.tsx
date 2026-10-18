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
  HISTORY_PATHS,
  INBOX_MODES,
  LINE_SWITCHES,
  AUTO_EMAILS_HELP,
  P_ABOVE,
  P_AUTO_EMAILS,
  P_BELOW,
  P_EVERY,
  P_INBOX,
  P_SCORE,
  P_SUBMIT,
  PREF_HISTORY_PATHS,
  SUBMIT_MODES,
  everyText,
  historyWhen,
  inboxKey,
  mergeHistory,
  settingAutoEmails,
  settingScore,
  settingSwitch,
} from "@/lib/assistant-tab";
import type { ChangeRow, HistRow } from "@/lib/assistant-tab";
import { LINE_SWITCH_LABEL, SUBMIT_MODE_LABEL, quotaText, scoreLineText } from "@/lib/morning-check";
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
  const [confirmAuto, setConfirmAuto] = useState(false);
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
