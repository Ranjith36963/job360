"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { CountryCard } from "@/components/profile/CountryCard";
import { CountryPicker } from "@/components/profile/CountryPicker";
import { MemoryRow } from "@/components/profile/MemoryRow";
import { useSetupProgress } from "@/components/profile/SetupProgress";
import { doneDate } from "@/lib/assistant-tab";
import { getProfile, getProfileEditHistory, keepProfileEdit, updateProfileFields } from "@/lib/api";
import { ApiError, apiErrorMessage } from "@/lib/api-error";
import type { ProfileResponse } from "@/lib/types";
import {
  ANSWERS, LOGISTICS, MEMORY_PATHS, RTW, SALARY,
  contactRows, equalityRows, fieldProvenance, isEmpty, logisticsCountryRows, logisticsRows,
  answerApprover, approvalText, countrySaved, provenanceText, readRow, rtwCountryRows, rtwTopRows, salaryRow, showRow, writeRow,
  type HistoryRow, type RowSpec,
} from "@/lib/memory";

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
type Histories = Record<string, HistoryRow[]>;

function blockOf(profile: ProfileResponse | null, path: string): unknown {
  if (!profile) return undefined;
  if (path === SALARY) return (profile.preferences as Record<string, unknown>)?.[ "salary_by_country" ];
  return (profile.user_info as Record<string, unknown>)?.[path.replace("user_info.", "")];
}

function codesIn(list: unknown): string[] {
  return Array.isArray(list)
    ? list.map((r) => (r && typeof r === "object" ? String((r as Record<string, unknown>).country ?? "") : "")).filter(Boolean)
    : [];
}

/** One setup step: a header that opens and closes it (dot, name, the date the
 *  step was done, how many answers wait for the user's word) and, when open,
 *  its facts plus one "Confirm all" for that step. */
function Step({ id, title, doneAt, waiting, defaultOpen, confirming, onConfirmAll, children }: {
  id: string; title: string; doneAt?: string; waiting: number; defaultOpen: boolean;
  confirming: boolean; onConfirmAll: () => void; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const done = doneAt !== undefined;
  const when = done ? doneDate(doneAt) : "";
  return (
    <section data-testid={`memory-step-${id}`} data-done={done} className="border-t border-border first:border-t-0">
      <button type="button" aria-expanded={open} aria-controls={`memory-step-body-${id}`}
        data-testid={`memory-step-toggle-${id}`} onClick={() => setOpen((o) => !o)}
        className="flex min-h-11 w-full items-center gap-2.5 py-3 text-left">
        <span aria-hidden="true" className={`inline-block h-2 w-2 flex-none rounded-full ${done ? "bg-primary" : "border border-faint"}`} />
        <span className="text-[15px] font-medium">{title}</span>
        {waiting > 0 && (
          <span data-testid={`memory-step-waiting-${id}`} className="rounded-full bg-muted px-2 py-0.5 text-xs text-warning">
            {waiting} to confirm
          </span>
        )}
        <span className="ml-auto font-mono text-xs text-faint">{when || (done ? "" : "not done")}</span>
        <span aria-hidden="true" className="text-faint">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div id={`memory-step-body-${id}`} className="flex flex-col pb-4">
          {waiting > 0 && (
            <div className="mb-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl border border-border bg-muted px-3.5 py-2.5">
              <span className="text-[13px] text-muted-foreground">Your assistant saved these. Confirm them as yours, or change any first.</span>
              <Button type="button" size="sm" className="min-h-11 md:min-h-8" data-testid={`memory-confirm-step-${id}`}
                disabled={confirming} aria-busy={confirming} onClick={onConfirmAll}>
                {confirming ? "Confirming…" : `Confirm all ${waiting}`}
              </Button>
            </div>
          )}
          {children}
        </div>
      )}
    </section>
  );
}

const Section = ({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) => (
  <section className="flex flex-col">
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 pb-2.5">
      <h2 className={LABEL}>{title}</h2>
      {hint && <span className="text-[12.5px] text-muted-foreground">{hint}</span>}
    </div>
    {children}
  </section>
);

/** Profile -> Memory: every fact job forms ask, who saved it, and when. */
export function MemoryTab() {
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [histories, setHistories] = useState<Histories>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [extra, setExtra] = useState<string[]>([]);
  const [adding, setAdding] = useState(false);
  const [keepingAll, setKeepingAll] = useState<string | null>(null);
  const [keepAllError, setKeepAllError] = useState<string | null>(null);
  // The block a save starts from is the LATEST one (not the render's copy),
  // and saves run one after another: two quick edits in the same block must
  // not each start from the old block and silently drop the other's change.
  const latest = useRef<ProfileResponse | null>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const { progress } = useSetupProgress();

  /** `null` means the history could NOT be read — which is not the same thing
   *  as "nothing is saved" (`[]`). Each caller decides what to do with it. */
  const loadHistory = useCallback(async (path: string): Promise<[string, HistoryRow[] | null]> => {
    try {
      return [path, await getProfileEditHistory(path)];
    } catch (err: unknown) {
      console.error(`Could not read the edit history for ${path}`, err);
      return [path, null];
    }
  }, []);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [data, ...lists] = await Promise.all([
        getProfile().catch((err: unknown) => {
          if (err instanceof ApiError && err.isNotFound) return null;
          throw err;
        }),
        ...MEMORY_PATHS.map(loadHistory),
      ]);
      latest.current = data;
      setProfile(data);
      // On first load an unreadable history reads as none: no provenance line,
      // never a broken page.
      setHistories(Object.fromEntries(lists.map(([p, rows]): [string, HistoryRow[]] => [p, rows ?? []])));
    } catch (err: unknown) {
      setError(apiErrorMessage(err, "Could not load your memory."));
    } finally {
      setLoading(false);
    }
  }, [loadHistory]);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- existing working effect; refactor tracked
  useEffect(() => { void load(); }, [load]);

  /** One web edit: the WHOLE block, as the web (a typed row), then re-read it. */
  const save = useCallback((spec: RowSpec, next: unknown): Promise<void> => {
    const run = async () => {
      const block = writeRow(spec, blockOf(latest.current, spec.path), next);
      const updated = await updateProfileFields([{ path: spec.path, value: block }]);
      latest.current = updated;
      setProfile(updated);
      // The save worked. If the re-read did not, keep the provenance this block
      // already had rather than blanking every row in it until a reload.
      const [, rows] = await loadHistory(spec.path);
      if (rows) setHistories((h) => ({ ...h, [spec.path]: rows }));
    };
    const done = queue.current.then(run, run);
    queue.current = done.catch(() => undefined);
    return done;
  }, [loadHistory]);

  /** Confirm an inferred fact: Keep writes a web row marked user_said, then re-read. */
  const confirm = useCallback(async (spec: RowSpec): Promise<void> => {
    const updated = await keepProfileEdit(spec.path);
    latest.current = updated;
    setProfile(updated);
    const [, rows] = await loadHistory(spec.path);
    if (rows) setHistories((h) => ({ ...h, [spec.path]: rows }));
  }, [loadHistory]);

  const countries = useMemo(() => {
    const prefs = (profile?.preferences ?? {}) as Record<string, unknown>;
    const rtw = blockOf(profile, RTW) as Record<string, unknown> | undefined;
    const log = blockOf(profile, LOGISTICS) as Record<string, unknown> | undefined;
    const auth = Array.isArray(prefs.work_authorization_countries) ? prefs.work_authorization_countries.map(String) : [];
    return [...new Set([...codesIn(rtw?.countries), ...codesIn(blockOf(profile, SALARY)), ...auth, ...codesIn(log?.countries), ...extra])];
  }, [profile, extra]);

  if (loading) return <div data-testid="memory-loading" className="space-y-3 pt-6"><Skeleton className="h-8 w-full" /><Skeleton className="h-8 w-full" /><Skeleton className="h-8 w-full" /></div>;
  if (error) {
    return (
      <div role="alert" className="mt-6 flex items-center gap-3 text-sm text-destructive">
        {error}
        <Button size="sm" variant="outline" onClick={() => { setLoading(true); void load(); }}>Try again</Button>
      </div>
    );
  }

  const state = (spec: RowSpec) => {
    const block = blockOf(profile, spec.path);
    const value = readRow(spec, block);
    const prov = fieldProvenance(histories[spec.path] ?? [], (b) => readRow(spec, b), value);
    return { value, prov };
  };
  const row = (spec: RowSpec, opts: { emptyNote?: string; compact?: boolean; amberEmpty?: boolean } = {}) => {
    const { value, prov } = state(spec);
    return <MemoryRow key={spec.id} spec={spec} value={value} provenance={prov} onSave={(n) => save(spec, n)} onConfirm={() => confirm(spec)} {...opts} />;
  };

  const salaryCodes = [...new Set([...codesIn(blockOf(profile, SALARY)), ...codesIn((blockOf(profile, RTW) as Record<string, unknown> | undefined)?.countries)])];

  // The five setup steps and the rows each one holds. Assistant settings, the
  // sixth setup round, lives on the Assistant tab (owner, 2026-10-26).
  const steps: { id: string; title: string; specs: RowSpec[] }[] = [
    { id: "targets", title: "Job targets", specs: salaryCodes.map(salaryRow) },
    { id: "you", title: "About you", specs: contactRows() },
    { id: "visa", title: "Right to work", specs: [...countries.flatMap((c) => [...rtwCountryRows(c), ...logisticsCountryRows(c)]), ...rtwTopRows()] },
    { id: "logistics", title: "Logistics", specs: logisticsRows() },
    { id: "equality", title: "Equality (voluntary)", specs: equalityRows() },
  ];

  /** The rows of a step an assistant saved and the user has not confirmed. */
  const unconfirmedIn = (specs: RowSpec[]) => {
    const seen = new Set<string>();
    return specs.filter((s) => {
      if (seen.has(s.id)) return false;
      seen.add(s.id);
      const { value, prov } = state(s);
      return !isEmpty(value) && !!prov?.unconfirmed;
    });
  };

  /** Confirm all in one step: the same per-row confirm, one request at a time,
   *  stop at the first failure. One confirm covers a whole block (spec.path). */
  const confirmStep = async (stepId: string, specs: RowSpec[]) => {
    setKeepingAll(stepId);
    setKeepAllError(null);
    try {
      const done = new Set<string>();
      for (const spec of unconfirmedIn(specs)) {
        if (done.has(spec.path)) continue;
        done.add(spec.path);
        await confirm(spec);
      }
    } catch (err: unknown) {
      setKeepAllError(apiErrorMessage(err, "Could not confirm everything. Try again."));
    } finally {
      await load();
      setKeepingAll(null);
    }
  };

  const countrySummary = (code: string) => {
    // The card counts the facts a form asks. The two job-priority rows are a
    // search preference (empty = "not chosen"), so they never show as "not saved yet".
    const specs = rtwCountryRows(code).filter((s) => !s.id.endsWith("_priority"));
    const filled = specs.map((s) => ({ s, ...state(s) })).filter((x) => !isEmpty(x.value));
    const first = filled[0];
    return {
      saved: countrySaved(filled.map((x) => x.value)),
      total: specs.length,
      firstFact: first ? `${first.s.label}: ${showRow(first.s, first.value).toLowerCase()}${first.prov ? ` · ${provenanceText(first.prov).replace(/^(\w)/, (c) => c.toLowerCase())}` : ""}` : undefined,
    };
  };

  const approved = (Array.isArray(blockOf(profile, ANSWERS)) ? (blockOf(profile, ANSWERS) as Record<string, unknown>[]) : []).filter((a) => a.approved === true);

  const STEP_BODY: Record<string, React.ReactNode> = {
    targets: (
      <>
              <p className="mb-1.5 text-[13px] text-muted-foreground">Salary, one line per country.</p>
              {salaryCodes.map((code) => row(salaryRow(code), { amberEmpty: true }))}
      </>
    ),
    you: (
      <>
              {contactRows().map((s) => row(s))}
      </>
    ),
    visa: (
      <>
              {countries.map((code, i) => {
                const sum = countrySummary(code);
                return (
                  <CountryCard key={code} code={code} total={sum.total} saved={sum.saved} firstFact={sum.firstFact}
                    defaultOpen={i === 0 || extra.includes(code)}>
                    {rtwCountryRows(code).map((s) => row(s))}
                    {logisticsCountryRows(code).map((s) => row(s))}
                  </CountryCard>
                );
              })}
              <div className="mt-2.5">
                {adding ? (
                  <CountryPicker label="Add a country" tags={[]} onChange={(codes) => {
                    setExtra((e) => [...new Set([...e, ...codes])]);
                    setAdding(false);
                  }} />
                ) : (
                  <Button type="button" variant="outline" size="sm" className="min-h-11 md:min-h-8" onClick={() => setAdding(true)}>
                    Add a country
                  </Button>
                )}
              </div>
              <div className="mt-2.5">{rtwTopRows().map((s) => row(s, { emptyNote: s.id.endsWith("sanctions_country_citizen") ? "Your assistant will ask" : undefined }))}</div>
      </>
    ),
    logistics: (
      <>
              {logisticsRows().map((s) => row(s))}
      </>
    ),
    equality: (
      <>
              <p className="mb-1.5 rounded-xl border border-border bg-muted px-3.5 py-2.5 text-[13px] text-muted-foreground">
                Voluntary. Forms ask, and you never have to answer. &quot;Prefer not to say&quot; is a full answer, not a blank. Nothing here is filled in for you.
              </p>
              {equalityRows().map((s) => row(s, { emptyNote: "Your assistant will ask" }))}
      </>
    ),
  };

  return (
    <div className="grid gap-8 pt-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <div className="flex min-w-0 flex-col gap-8" data-testid="memory-tab">
        {keepAllError && (
          <p role="alert" data-testid="memory-keep-all-error" className="text-[13px] text-destructive">{keepAllError}</p>
        )}
        <div data-testid="memory-steps" className="flex flex-col">
          {steps.map((st) => {
            const doneAt = progress?.rounds?.[st.id]?.done_at;
            const waiting = unconfirmedIn(st.specs).length;
            return (
              // Keyed on the done date: progress arrives after the profile, and a
              // step it marks done must then start closed.
              <Step key={`${st.id}-${doneAt ?? ""}`} id={st.id} title={st.title} doneAt={doneAt} waiting={waiting}
                defaultOpen={doneAt === undefined || waiting > 0}
                confirming={keepingAll === st.id} onConfirmAll={() => void confirmStep(st.id, st.specs)}>
                {STEP_BODY[st.id]}
              </Step>
            );
          })}
        </div>

        <Section title="Approved answers" hint="Answers you approved once. Reused word for word.">
          {approved.length === 0 ? (
            <p className="border-t border-border py-3 text-sm text-faint">Not saved yet</p>
          ) : approved.map((a, i) => (
            <div key={`${String(a.question)}-${i}`} data-testid="approved-answer"
              className="grid grid-cols-1 gap-x-4 gap-y-1 border-t border-border py-3 text-sm md:grid-cols-[minmax(0,.9fr)_minmax(0,1.2fr)_minmax(0,1fr)]">
              <span className="text-muted-foreground">{String(a.question)}</span>
              <span className="min-w-0 font-medium">{String(a.answer)}</span>
              <span className="font-mono text-xs text-faint">
                {approvalText(answerApprover(histories[ANSWERS] ?? [], a.question, a.answer), a.recorded_at)}
              </span>
            </div>
          ))}
        </Section>
      </div>

      <aside className="flex flex-col gap-6" data-testid="memory-rail">
        <section>
          <h2 className={`${LABEL} mb-2`}>How memory works</h2>
          <p className="text-[13px] leading-relaxed text-muted-foreground">
            Every change adds a new row. The old one is kept. Take back restores it as a new row, nothing is deleted.
          </p>
          <p className="mt-2 text-[13px] leading-relaxed text-muted-foreground">
            Visa, equality, salary, birth date and address ask before saving.
          </p>
        </section>
      </aside>
    </div>
  );
}
