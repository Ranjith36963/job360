"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { CountryCard } from "@/components/profile/CountryCard";
import { CountryPicker } from "@/components/profile/CountryPicker";
import { MemoryRow } from "@/components/profile/MemoryRow";
import { getProfile, getProfileEditHistory, updateProfileFields } from "@/lib/api";
import { ApiError, apiErrorMessage } from "@/lib/api-error";
import type { ProfileResponse } from "@/lib/types";
import {
  ANSWERS, LOGISTICS, MEMORY_PATHS, RTW, SALARY,
  contactRows, equalityRows, fieldProvenance, isEmpty, logisticsCountryRows, logisticsRows,
  provenanceText, readRow, rtwCountryRows, rtwTopRows, salaryRow, shortDate, showRow, writeRow,
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
  // The block a save starts from is the LATEST one (not the render's copy),
  // and saves run one after another: two quick edits in the same block must
  // not each start from the old block and silently drop the other's change.
  const latest = useRef<ProfileResponse | null>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());

  const loadHistory = useCallback(async (path: string): Promise<[string, HistoryRow[]]> => {
    try {
      return [path, await getProfileEditHistory(path)];
    } catch {
      return [path, []]; // no history = no provenance line, never a broken page
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
      setHistories(Object.fromEntries(lists));
    } catch (err: unknown) {
      setError(apiErrorMessage(err, "Could not load your memory."));
    } finally {
      setLoading(false);
    }
  }, [loadHistory]);

  useEffect(() => { void load(); }, [load]);

  /** One web edit: the WHOLE block, as the web (a typed row), then re-read it. */
  const save = useCallback((spec: RowSpec, next: unknown): Promise<void> => {
    const run = async () => {
      const block = writeRow(spec, blockOf(latest.current, spec.path), next);
      const updated = await updateProfileFields([{ path: spec.path, value: block }]);
      latest.current = updated;
      setProfile(updated);
      const [, rows] = await loadHistory(spec.path);
      setHistories((h) => ({ ...h, [spec.path]: rows }));
    };
    const done = queue.current.then(run, run);
    queue.current = done.catch(() => undefined);
    return done;
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
    return <MemoryRow key={spec.id} spec={spec} value={value} provenance={prov} onSave={(n) => save(spec, n)} {...opts} />;
  };

  const countrySummary = (code: string) => {
    const specs = rtwCountryRows(code);
    const filled = specs.map((s) => ({ s, ...state(s) })).filter((x) => !isEmpty(x.value));
    const first = filled[0];
    return {
      saved: filled.length,
      firstFact: first ? `${first.s.label}: ${showRow(first.s, first.value).toLowerCase()}${first.prov ? ` · ${provenanceText(first.prov).replace(/^(\w)/, (c) => c.toLowerCase())}` : ""}` : undefined,
    };
  };

  const approved = (Array.isArray(blockOf(profile, ANSWERS)) ? (blockOf(profile, ANSWERS) as Record<string, unknown>[]) : []).filter((a) => a.approved === true);
  const salaryCodes = [...new Set([...codesIn(blockOf(profile, SALARY)), ...codesIn((blockOf(profile, RTW) as Record<string, unknown> | undefined)?.countries)])];

  return (
    <div className="grid gap-8 pt-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <div className="flex min-w-0 flex-col gap-8" data-testid="memory-tab">
        <Section title="Contact & identity">{contactRows().map((s) => row(s))}</Section>

        <Section title="Right to work" hint="One card per country">
          {countries.map((code, i) => {
            const sum = countrySummary(code);
            return (
              <CountryCard key={code} code={code} total={4} saved={sum.saved} firstFact={sum.firstFact}
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
        </Section>

        <Section title="Logistics & languages">{logisticsRows().map((s) => row(s))}</Section>

        <Section title="Equality">
          <p className="mb-1.5 rounded-xl border border-border bg-muted px-3.5 py-2.5 text-[13px] text-muted-foreground">
            Voluntary. Forms ask, and you never have to answer. &quot;Prefer not to say&quot; is a full answer, not a blank. Nothing here is filled in for you.
          </p>
          {equalityRows().map((s) => row(s, { emptyNote: "Your assistant will ask" }))}
        </Section>

        <Section title="Approved answers" hint="Answers you approved once. Reused word for word.">
          {approved.length === 0 ? (
            <p className="border-t border-border py-3 text-sm text-faint">Not saved yet</p>
          ) : approved.map((a, i) => (
            <div key={`${String(a.question)}-${i}`} data-testid="approved-answer"
              className="grid grid-cols-1 gap-x-4 gap-y-1 border-t border-border py-3 text-sm md:grid-cols-[minmax(0,.9fr)_minmax(0,1.2fr)_minmax(0,1fr)]">
              <span className="text-muted-foreground">{String(a.question)}</span>
              <span className="min-w-0 font-medium">{String(a.answer)}</span>
              <span className="font-mono text-xs text-faint">
                {typeof a.recorded_at === "string" && shortDate(a.recorded_at) ? `Approved by you, ${shortDate(a.recorded_at)}` : "Approved by you"}
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
        <section>
          <h2 className={`${LABEL} mb-1`}>Job targets</h2>
          <p className="mb-1.5 text-[13px] text-muted-foreground">Salary lives in Preferences, one line per country.</p>
          {salaryCodes.map((code) => row(salaryRow(code), { compact: true, amberEmpty: true }))}
        </section>
      </aside>
    </div>
  );
}
