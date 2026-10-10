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
  APPLY_MODES,
  CHECK_EVERY,
  DAILY_CAP_MAX,
  HARD_STOPS,
  HISTORY_PATHS,
  INBOX_MODES,
  P_APPLY,
  P_CAP,
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
} from "@/lib/assistant-tab";
import type { ChangeRow, HistRow } from "@/lib/assistant-tab";
import { APPLY_MODE_LABEL, SUBMIT_MODE_LABEL, quotaText } from "@/lib/morning-check";
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

const eff = (f: { effective?: unknown; value?: unknown } | undefined) => f?.effective ?? f?.value;

/** Profile -> Assistant: when the assistant may apply, how it submits, its limits
 *  and inbox, the hard stops that cannot change, and every change with Take back.
 *  Every control is a PATCH as the web user and applies at once. */
export function AssistantTab() {
  const [view, setView] = useState<AssistantSettingsView | null>(null);
  const [rows, setRows] = useState<ChangeRow[]>([]);
  const [today, setToday] = useState<MorningCheck | null>(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [score, setScore] = useState("75");
  const [cap, setCap] = useState("");
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
    setScore(String(eff(v.apply_min_score) ?? 75));
    setCap(v.daily_cap.value == null ? "" : String(v.daily_cap.value));
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

  const apply_mode = String(eff(view.apply_mode) ?? "ask_each");
  const submit_mode = String(eff(view.submit_mode) ?? "confirm");
  const inbox = inboxKey(view.inbox_mode);
  const every = view.check_every ?? "";
  const savedScore = Number(eff(view.apply_min_score) ?? 75);
  const savedCap = view.daily_cap.value == null ? "" : String(view.daily_cap.value);

  function commitScore() {
    const n = Number(score);
    if (score.trim() === "" || !Number.isInteger(n) || n < 0 || n > 100) {
      toast.error("Score line must be a whole number from 0 to 100. Not saved.");
      setScore(String(savedScore));
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
        <Section title="Apply mode" hint="When may your assistant apply?">
          <div role="radiogroup" aria-label="Apply mode" className="grid gap-2 md:grid-cols-3">
            {APPLY_MODES.map((m) => (
              <Option
                key={m.value}
                id={`apply-mode-${m.value}`}
                title={APPLY_MODE_LABEL[m.value]}
                sub={m.sub}
                on={apply_mode === m.value}
                disabled={busy}
                onPick={() => apply_mode !== m.value && void save(P_APPLY, m.value)}
              />
            ))}
          </div>
          {apply_mode === "selective_above_score" && (
            <Row title="Score line" sub="Your assistant scores each job. Below the line, it skips.">
              <input
                type="range"
                min={0}
                max={100}
                aria-label="Score line slider"
                data-testid="score-range"
                value={Number(score) || 0}
                disabled={busy}
                onChange={(e) => setScore(e.target.value)}
                onPointerUp={commitScore}
                onKeyUp={commitScore}
                className="min-h-11 min-w-36 flex-1 accent-[var(--primary)]"
              />
              <input
                type="number"
                inputMode="numeric"
                min={0}
                max={100}
                aria-label="Score line"
                data-testid="score-input"
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
            </Row>
          )}
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
              hours
            </label>
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
                ["Apply mode", APPLY_MODE_LABEL[apply_mode] ?? apply_mode],
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
