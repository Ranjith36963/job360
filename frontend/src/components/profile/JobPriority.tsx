"use client";

import { useEffect, useRef, useState } from "react";
import { CountryPicker } from "@/components/profile/CountryPicker";
import { updateProfileFields } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { countryName } from "@/lib/countries";
import {
  PRIORITY_LABEL, PRIORITY_OPTIONS, RTW, SALARY, fromInputs, inputProblem, readRow, salaryRow, salaryText, toInputs, writeRow,
} from "@/lib/memory";
import type { ProfileResponse } from "@/lib/types";

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const FIELD =
  "h-9 w-full min-w-0 rounded-lg border border-border bg-background px-2.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60 min-h-11 md:min-h-9";

type Kind = "remote" | "onsite";
type Rec = Record<string, unknown>;

interface PlaceRow {
  /** Stable key + test id suffix: <record index>-<remote|onsite>. */
  id: string;
  index: number;
  kind: Kind;
  label: string;
  /** The key inside the record this row writes. */
  key: "remote_priority" | "onsite_priority";
  value: string;
}

const isObj = (v: unknown): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v);

function placeName(code: unknown): string {
  const c = String(code ?? "");
  if (c === "ANY") return "anywhere else";
  return c ? countryName(c) : "unknown place";
}

/** One row per place the user can work, in record order. Nothing is invented:
 *  a record with neither remote_ok nor an on-site yes gives no row. */
export function priorityRows(block: unknown): PlaceRow[] {
  const out: PlaceRow[] = [];
  // Index into the RAW countries list (not the filtered one) so a write lands on the right record.
  const list: unknown[] = isObj(block) && Array.isArray(block.countries) ? block.countries : [];
  list.forEach((rec, index) => {
    if (!isObj(rec)) return;
    const place = placeName(rec.country);
    const level = (key: string) =>
      (PRIORITY_OPTIONS as readonly string[]).includes(String(rec[key])) ? String(rec[key]) : "";
    if (rec.remote_ok === true) {
      out.push({
        id: `${index}-remote`, index, kind: "remote", label: `Remote — ${place}`,
        key: "remote_priority", value: level("remote_priority"),
      });
    }
    if (rec.onsite === "yes" || rec.onsite === "only_if_sponsored") {
      out.push({
        id: `${index}-onsite`, index, kind: "onsite",
        label: `On-site — ${place}${rec.onsite === "only_if_sponsored" ? " (visa sponsorship)" : ""}`,
        key: "onsite_priority", value: level("onsite_priority"),
      });
    }
  });
  return out;
}

/** A copy of the WHOLE right_to_work block with one key of one record changed.
 *  `""` (Not set) removes the key. */
export function withPriority(block: unknown, index: number, key: string, value: string): Rec {
  const base: Rec = isObj(block) ? block : {};
  const list: unknown[] = Array.isArray(base.countries) ? base.countries : [];
  const countries = list.map((rec, i) => {
    if (i !== index || !isObj(rec)) return rec;
    const next = { ...rec };
    if (value === "") delete next[key];
    else next[key] = value;
    return next;
  });
  return { ...base, countries };
}

// ---- Where you'll work (owner sketch, 2026-10-26) -------------------------
// One line per place: Remote / On-site / Contract ticks, then Visa need / no
// need. Each tick writes one key of that place's right-to-work record.

export type Tick = "remote" | "onsite" | "contract";

/** Is this tick on for the record? Unanswered reads as off (nothing is guessed). */
export function tickOn(rec: Rec, tick: Tick): boolean {
  if (tick === "remote") return rec.remote_ok === true;
  if (tick === "onsite") return rec.onsite === "yes" || rec.onsite === "only_if_sponsored";
  return rec.contract_ok === true;
}

/** A copy of the WHOLE block with one record changed by `edit` (index into the raw list). */
export function withRecord(block: unknown, index: number, edit: (rec: Rec) => Rec): Rec {
  const base: Rec = isObj(block) ? block : {};
  const list: unknown[] = Array.isArray(base.countries) ? base.countries : [];
  return { ...base, countries: list.map((rec, i) => (i === index && isObj(rec) ? edit({ ...rec }) : rec)) };
}

/** Flip one tick. On-site off is "no"; on is "yes", keeping an existing
 *  "only if sponsored" rather than widening it. */
export function flipTick(rec: Rec, tick: Tick): Rec {
  const on = tickOn(rec, tick);
  if (tick === "remote") return { ...rec, remote_ok: !on };
  if (tick === "contract") return { ...rec, contract_ok: !on };
  return { ...rec, onsite: on ? "no" : "yes" };
}

/** Visa need (true) / no need (false); choosing the one already chosen clears it. */
export function setVisa(rec: Rec, need: boolean): Rec {
  const next = { ...rec };
  if (rec.needs_sponsorship === need) delete next.needs_sponsorship;
  else next.needs_sponsorship = need;
  return next;
}

/** Why "Need" cannot be chosen for this place, or null: a citizen or permanent
 *  resident needs no visa (the server refuses the pair). */
export function visaNeedBlocked(rec: Rec, name: string): string | null {
  const auth = rec.work_authorization;
  if (auth !== "citizen" && auth !== "permanent_resident") return null;
  const who = auth === "citizen" ? "a citizen" : "a permanent resident";
  return `You saved that you're ${who} of ${name}, so no visa is needed there. Change that in Memory, Right to work, first.`;
}

/** The block with one more place (a new empty record); an existing one is left alone. */
export function withPlace(block: unknown, code: string): Rec {
  const base: Rec = isObj(block) ? block : {};
  const list: unknown[] = Array.isArray(base.countries) ? base.countries : [];
  if (list.some((r) => isObj(r) && r.country === code)) return base;
  return { ...base, countries: [...list, { country: code }] };
}

// FIELD without w-full, so the four salary boxes sit side by side.
const SMALL_FIELD = FIELD.replace("w-full ", "");
const TICK_LABEL: Record<Tick, string> = { remote: "Remote", onsite: "On-site", contract: "Contract" };
const CHIP =
  "inline-flex min-h-11 items-center gap-1.5 rounded-lg border px-3 text-sm md:min-h-8 disabled:opacity-60 " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const chipTone = (on: boolean) => (on ? "border-primary bg-primary/10 text-foreground" : "border-border text-muted-foreground");

/** Salary for one country: the saved range, or an inline From / To / Currency / Per
 *  editor. Same rules as Memory (a figure above 0, a 3-letter currency, a period). */
function SalaryCell({ code, salaries, busy, onSave }: {
  code: string; salaries: unknown; busy: boolean; onSave: (value: unknown) => void;
}) {
  const spec = salaryRow(code);
  const value = readRow(spec, salaries);
  const [inputs, setInputs] = useState<Record<string, string> | null>(null);
  const shown = value && typeof value === "object" ? salaryText(value as Rec) : "";
  if (!inputs) {
    return (
      <button type="button" data-testid={`work-grid-salary-${code}`} disabled={busy}
        className={`${CHIP} ${chipTone(!!shown)}`} onClick={() => setInputs(toInputs(spec, value))}>
        {shown ? `Salary ${shown}` : "Add salary"}
      </button>
    );
  }
  const problem = inputProblem(spec, inputs);
  const field = (key: string, label: string, width: string, extra: Record<string, string> = {}) => (
    <input aria-label={label} placeholder={label} value={inputs[key] ?? ""} {...extra}
      onChange={(e) => setInputs({ ...inputs, [key]: e.target.value })}
      className={`${SMALL_FIELD} ${width}`} />
  );
  return (
    <form data-testid={`work-grid-salary-form-${code}`} className="flex w-full flex-wrap items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (problem) return;
        onSave(fromInputs(spec, inputs));
        setInputs(null);
      }}>
      {field("min", "From", "w-24", { inputMode: "numeric" })}
      {field("max", "Up to", "w-24", { inputMode: "numeric" })}
      {field("currency", "Currency", "w-24", { maxLength: "3" })}
      <select aria-label="Per" value={inputs.period ?? ""} className={`${SMALL_FIELD} w-24`}
        onChange={(e) => setInputs({ ...inputs, period: e.target.value })}>
        <option value="">Per…</option>
        <option value="year">year</option>
        <option value="month">month</option>
      </select>
      <button type="submit" disabled={!!problem} className={`${CHIP} border-border`}>Save</button>
      <button type="button" className={`${CHIP} border-border`} onClick={() => setInputs(null)}>Cancel</button>
      {value !== undefined && (
        <button type="button" className={`${CHIP} border-border`} onClick={() => { onSave(undefined); setInputs(null); }}>
          Remove
        </button>
      )}
      {problem && <p role="alert" className="w-full text-xs text-destructive">{problem}</p>}
    </form>
  );
}

type Status = "saving" | "saved" | "error";

/** Profile -> CV tab: where should the assistant look first. Writes the two
 *  optional priority keys on each right-to-work record, nothing else. */
export function JobPriority({
  rightToWork, salaries, onSaved, embedded = false,
}: {
  rightToWork: unknown; salaries?: unknown; onSaved: (profile: ProfileResponse) => void;
  /** Inside Memory's Job targets step: no card frame of its own. */
  embedded?: boolean;
}) {
  // Saves start from the LATEST block (not the render's copy) and run one
  // after another, so two quick changes never drop each other.
  const latest = useRef<unknown>(rightToWork);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const [status, setStatus] = useState<Record<string, { state: Status; message?: string }>>({});

  useEffect(() => { latest.current = rightToWork; }, [rightToWork]);
  const latestSalaries = useRef<unknown>(salaries);
  useEffect(() => { latestSalaries.current = salaries; }, [salaries]);

  const rows = priorityRows(rightToWork);

  /** Save `next(latest block)` in turn; `key` is the status slot it reports on. */
  const write = (key: string, next: (block: unknown) => unknown): Promise<void> => {
    setStatus((s) => ({ ...s, [key]: { state: "saving" } }));
    const run = async () => {
      try {
        const block = next(latest.current);
        const updated = await updateProfileFields([{ path: RTW, value: block }]);
        latest.current = (updated.user_info as Rec | undefined)?.right_to_work ?? block;
        onSaved(updated);
        setStatus((s) => ({ ...s, [key]: { state: "saved" } }));
      } catch (err: unknown) {
        setStatus((s) => ({
          ...s, [key]: { state: "error", message: apiErrorMessage(err, "Could not save.") },
        }));
      }
    };
    const done = queue.current.then(run, run);
    queue.current = done;
    return done;
  };

  /** Save one country's salary: the whole list, built from the last saved one, in the same queue. */
  const saveSalary = (code: string, value: unknown): Promise<void> => {
    const key = `grid-${code}`;
    setStatus((s) => ({ ...s, [key]: { state: "saving" } }));
    const run = async () => {
      try {
        const list = writeRow(salaryRow(code), latestSalaries.current, value);
        const updated = await updateProfileFields([{ path: SALARY, value: list }]);
        latestSalaries.current = (updated.preferences as Rec | undefined)?.salary_by_country ?? list;
        onSaved(updated);
        setStatus((s) => ({ ...s, [key]: { state: "saved" } }));
      } catch (err: unknown) {
        setStatus((s) => ({ ...s, [key]: { state: "error", message: apiErrorMessage(err, "Could not save.") } }));
      }
    };
    const done = queue.current.then(run, run);
    queue.current = done;
    return done;
  };

  const change = (row: PlaceRow, value: string): Promise<void> =>
    write(row.id, (b) => withPriority(b, row.index, row.key, value));

  // Grid lines: every record, by raw index, "every other country" last.
  const records: { rec: Rec; index: number }[] = (
    isObj(rightToWork) && Array.isArray(rightToWork.countries) ? rightToWork.countries : []
  ).flatMap((rec: unknown, index: number) => (isObj(rec) && typeof rec.country === "string" ? [{ rec, index }] : []));
  records.sort((a, b) => Number(a.rec.country === "ANY") - Number(b.rec.country === "ANY"));
  const hasAny = records.some((r) => r.rec.country === "ANY");
  const [adding, setAdding] = useState(false);
  // A grid write is keyed by country, not index: the index can move when a place is added.
  const editPlace = (code: string, edit: (rec: Rec) => Rec) =>
    write(`grid-${code}`, (b) => {
      const list: unknown[] = isObj(b) && Array.isArray(b.countries) ? b.countries : [];
      const at = list.findIndex((r) => isObj(r) && r.country === code);
      return at < 0 ? b : withRecord(b, at, edit);
    });

  return (
    <section data-testid="job-priority" className={embedded ? "" : "rounded-xl border border-border bg-card p-6"}>
      <h3 className={LABEL}>Job priority</h3>
      <p className="mt-2 text-xs text-muted-foreground">
        Where should your assistant look first? It spends more of the hunt on higher priorities. Off = never search there.
      </p>
      {rows.length === 0 ? (
        <p data-testid="job-priority-empty" className="mt-4 text-sm text-muted-foreground">
          Tick Remote or On-site for a place below first.
        </p>
      ) : (
        <ul className="mt-4 flex flex-col gap-3">
          {rows.map((row) => {
            const st = status[row.id];
            const selectId = `job-priority-${row.id}`;
            return (
              <li key={row.id} data-testid={`job-priority-row-${row.id}`} className="flex flex-col gap-1">
                <label htmlFor={selectId} className="text-sm">{row.label}</label>
                <div className="flex items-center gap-3">
                  <select
                    id={selectId}
                    data-testid={`job-priority-select-${row.id}`}
                    className={FIELD}
                    value={row.value}
                    disabled={st?.state === "saving"}
                    onChange={(e) => { void change(row, e.target.value); }}
                  >
                    <option value="">Not set</option>
                    {PRIORITY_OPTIONS.map((o) => <option key={o} value={o}>{PRIORITY_LABEL[o]}</option>)}
                  </select>
                  <span
                    role="status"
                    data-testid={`job-priority-status-${row.id}`}
                    className="w-16 shrink-0 text-xs text-muted-foreground"
                  >
                    {st?.state === "saving" ? "Saving…" : st?.state === "saved" ? "Saved" : ""}
                  </span>
                </div>
                {st?.state === "error" && (
                  <p role="alert" className="text-xs text-destructive">{st.message}</p>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <div data-testid="work-grid" className="mt-6 border-t border-border pt-5">
        <h4 className={LABEL}>Where you&apos;ll work</h4>
        <p className="mt-2 text-xs text-muted-foreground">
          For each place: the kinds of work you&apos;ll take, whether you need a visa there, and your salary range.
        </p>
        <ul className="mt-3 flex flex-col">
          {records.map(({ rec }) => {
            const code = String(rec.country);
            const st = status[`grid-${code}`];
            const busy = st?.state === "saving";
            const name = code === "ANY" ? "All other countries" : countryName(code);
            return (
              <li key={code} data-testid={`work-grid-row-${code}`} className="flex flex-col gap-2 border-t border-border py-3 first:border-t-0">
                <span className="text-sm font-medium">{name}</span>
                <div className="flex flex-wrap items-center gap-2">
                  {(["remote", "onsite", "contract"] as const).map((tick) => {
                    const on = tickOn(rec, tick);
                    return (
                      <button key={tick} type="button" aria-pressed={on} disabled={busy}
                        data-testid={`work-grid-${tick}-${code}`} aria-label={`${TICK_LABEL[tick]} in ${name}`}
                        className={`${CHIP} ${chipTone(on)}`}
                        onClick={() => void editPlace(code, (r) => flipTick(r, tick))}>
                        <span aria-hidden="true">{on ? "☑" : "☐"}</span>{TICK_LABEL[tick]}
                      </button>
                    );
                  })}
                  <span className="ml-1 text-xs text-muted-foreground">Visa</span>
                  <div role="group" aria-label={`Visa in ${name}`} className="inline-flex">
                    {([true, false] as const).map((need) => {
                      const on = rec.needs_sponsorship === need;
                      return (
                        <button key={String(need)} type="button" aria-pressed={on} disabled={busy}
                          data-testid={`work-grid-visa-${need ? "need" : "noneed"}-${code}`}
                          className={`${CHIP} ${chipTone(on)} ${need ? "rounded-r-none" : "-ml-px rounded-l-none"}`}
                          onClick={() => {
                            const why = need ? visaNeedBlocked(rec, name) : null;
                            if (why) setStatus((s) => ({ ...s, [`grid-${code}`]: { state: "error", message: why } }));
                            else void editPlace(code, (r) => setVisa(r, need));
                          }}>
                          {need ? "Need" : "No need"}
                        </button>
                      );
                    })}
                  </div>
                  <span role="status" className="text-xs text-muted-foreground">{busy ? "Saving…" : st?.state === "saved" ? "Saved" : ""}</span>
                </div>
                {code === "ANY" ? (
                  <p className="text-xs text-muted-foreground">Salary is set per country.</p>
                ) : (
                  <div className="flex flex-wrap items-center gap-2">
                    <SalaryCell code={code} salaries={salaries} busy={busy}
                      onSave={(v) => void saveSalary(code, v)} />
                  </div>
                )}
                {st?.state === "error" && <p role="alert" className="break-words text-xs text-destructive">{st.message}</p>}
              </li>
            );
          })}
        </ul>
        <div className="mt-3 flex flex-wrap gap-2">
          {adding ? (
            <CountryPicker label="Add a country" tags={[]} testId="work-grid-picker" onChange={(codes) => {
              setAdding(false);
              for (const c of codes) void write(`grid-${c}`, (b) => withPlace(b, c));
            }} />
          ) : (
            <button type="button" data-testid="work-grid-add" className={`${CHIP} border-border`} onClick={() => setAdding(true)}>
              Add a country
            </button>
          )}
          {!hasAny && (
            <button type="button" data-testid="work-grid-add-any" className={`${CHIP} border-border`}
              onClick={() => void write("grid-ANY", (b) => withPlace(b, "ANY"))}>
              Add all other countries
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
