"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { CountryPicker } from "@/components/profile/CountryPicker";
import { updateProfileFields } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { countryName } from "@/lib/countries";
import { REGIONS, isRegion } from "@/lib/regions";
import {
  PRIORITY_LABEL, RTW, SALARY, fromInputs, inputProblem, readRow, salaryRow, salaryText, toInputs, writeRow,
} from "@/lib/memory";
import type { ProfileResponse } from "@/lib/types";

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const FIELD =
  "h-9 w-full min-w-0 rounded-lg border border-border bg-background px-2.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60 min-h-11 md:min-h-9";

type Rec = Record<string, unknown>;


const isObj = (v: unknown): v is Rec => typeof v === "object" && v !== null && !Array.isArray(v);


/** Countries, then regions, then all other countries: the order a job's place is answered in. */
const placeRank = (code: unknown) => (code === "ANY" ? 2 : isRegion(String(code)) ? 1 : 0);



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

// Owner, 2026-10-26: a priority right next to each ticked work type, per place.
// Three choices; an older level (high / off) is kept and shown until changed.
export const PRIORITY_KEY: Record<Tick, string> = {
  remote: "remote_priority", onsite: "onsite_priority", contract: "contract_priority",
};
const LEVELS = ["highest", "medium", "low"] as const;

/** The choices for one priority box: the three levels, plus a stored older one. */
export function levelChoices(current: string): string[] {
  return current && !(LEVELS as readonly string[]).includes(current) ? [...LEVELS, current] : [...LEVELS];
}

/** The record with one work type's priority set; "" (not chosen) removes it. */
export function withLevel(rec: Rec, tick: Tick, level: string): Rec {
  const next = { ...rec };
  if (level === "") delete next[PRIORITY_KEY[tick]];
  else next[PRIORITY_KEY[tick]] = level;
  return next;
}
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
        {shown || "Add salary"}
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

/** Add a place: a country (search), all other countries, or a whole region (owner,
 *  2026-10-26). Used by Where you'll work and by Right to work: both edit the same
 *  right-to-work list, so a place added in either shows in both. */
export function AddPlace({ have, onAdd, testIdPrefix }: {
  have: string[]; onAdd: (code: string) => void; testIdPrefix: string;
}) {
  const [adding, setAdding] = useState(false);
  return (
    <>
      <div className="mt-3 flex flex-wrap gap-2">
        {adding ? (
          <CountryPicker label="Add a country" tags={[]} testId={`${testIdPrefix}-picker`} onChange={(codes) => {
            setAdding(false);
            for (const c of codes) if (!have.includes(c)) onAdd(c);
          }} />
        ) : (
          <button type="button" data-testid={`${testIdPrefix}-add`} className={`${CHIP} border-border`} onClick={() => setAdding(true)}>
            Add a country
          </button>
        )}
        {!have.includes("ANY") && (
          <button type="button" data-testid={`${testIdPrefix}-add-any`} className={`${CHIP} border-border`}
            onClick={() => onAdd("ANY")}>
            Add all other countries
          </button>
        )}
      </div>
      {/* One line for a whole region. A country's own line wins over it. */}
      <div data-testid={`${testIdPrefix}-regions`} className="mt-3">
        <p className="text-xs text-muted-foreground">Or add a whole region (a country&apos;s own line wins over it):</p>
        <div className="mt-1.5 flex flex-wrap gap-2">
          {REGIONS.filter((r) => !have.includes(r.code)).map((r) => (
            <button key={r.code} type="button" data-testid={`${testIdPrefix}-add-region-${r.code}`} title={r.hint}
              className={`${CHIP} border-border`} onClick={() => onAdd(r.code)}>
              + {r.name}
            </button>
          ))}
        </div>
      </div>
    </>
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


  // Grid lines: every record, by raw index, "every other country" last.
  const records: { rec: Rec; index: number }[] = (
    isObj(rightToWork) && Array.isArray(rightToWork.countries) ? rightToWork.countries : []
  ).flatMap((rec: unknown, index: number) => (isObj(rec) && typeof rec.country === "string" ? [{ rec, index }] : []));
  records.sort((a, b) => placeRank(a.rec.country) - placeRank(b.rec.country));
  // A grid write is keyed by country, not index: the index can move when a place is added.
  const editPlace = (code: string, edit: (rec: Rec) => Rec) =>
    write(`grid-${code}`, (b) => {
      const list: unknown[] = isObj(b) && Array.isArray(b.countries) ? b.countries : [];
      const at = list.findIndex((r) => isObj(r) && r.country === code);
      return at < 0 ? b : withRecord(b, at, edit);
    });

  return (
    <section data-testid="job-priority" className={embedded ? "" : "rounded-xl border border-border bg-card p-6"}>
      <div data-testid="work-grid">
        <h4 className={LABEL}>Where you&apos;ll work</h4>
        <p className="mt-2 text-xs text-muted-foreground">
          For each place: the kinds of work you&apos;ll take and how much each matters (Highest, Medium, Low; your
          assistant spends more of the hunt on higher ones), whether you need a visa there, and your salary range.
        </p>
        {/* Owner, 2026-10-26: one line per country, four columns. On a narrow
            screen the table scrolls inside its own box; the page never does. */}
        <p className="mt-2 text-xs text-muted-foreground md:hidden">Swipe the table sideways to see Visa and Salary.</p>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[40rem] border-collapse text-sm">
            <thead>
              <tr className={`${LABEL} text-left`}>
                <th scope="col" className="py-2 pr-3 font-medium">Country</th>
                <th scope="col" className="py-2 pr-3 font-medium">Work type</th>
                <th scope="col" className="py-2 pr-3 font-medium">Visa</th>
                <th scope="col" className="py-2 font-medium">Salary range</th>
              </tr>
            </thead>
            <tbody>
              {records.map(({ rec }) => {
                const code = String(rec.country);
                const st = status[`grid-${code}`];
                const busy = st?.state === "saving";
                const name = code === "ANY" ? "All other countries" : countryName(code);
                return (
                  <Fragment key={code}>
                    <tr data-testid={`work-grid-row-${code}`} className="border-t border-border align-top">
                      <th scope="row" className="py-3 pr-3 text-left font-medium">
                        {name}
                        <span role="status" className="block text-xs font-normal text-muted-foreground">
                          {busy ? "Saving…" : st?.state === "saved" ? "Saved" : ""}
                        </span>
                      </th>
                      <td className="py-3 pr-3">
                        <div className="flex flex-wrap gap-x-2 gap-y-1.5">
                          {(["remote", "onsite", "contract"] as const).map((tick) => {
                            const on = tickOn(rec, tick);
                            const level = typeof rec[PRIORITY_KEY[tick]] === "string" ? String(rec[PRIORITY_KEY[tick]]) : "";
                            return (
                              <span key={tick} className="inline-flex items-center whitespace-nowrap">
                                <button type="button" aria-pressed={on} disabled={busy}
                                  data-testid={`work-grid-${tick}-${code}`} aria-label={`${TICK_LABEL[tick]} in ${name}`}
                                  className={`${CHIP} ${chipTone(on)} px-2 ${on ? "rounded-r-none" : ""}`}
                                  onClick={() => void editPlace(code, (r) => flipTick(r, tick))}>
                                  <span aria-hidden="true">{on ? "☑" : "☐"}</span>{TICK_LABEL[tick]}
                                </button>
                                {on && (
                                  <select data-testid={`work-grid-priority-${tick}-${code}`} disabled={busy}
                                    aria-label={`Priority for ${TICK_LABEL[tick]} in ${name}`} value={level}
                                    className="-ml-px min-h-11 rounded-l-none rounded-r-lg border border-primary bg-background px-1.5 text-sm md:min-h-8"
                                    onChange={(e) => {
                                      // Read now: the save runs later, when this controlled box shows the old value again.
                                      const level = e.target.value;
                                      void editPlace(code, (r) => withLevel(r, tick, level));
                                    }}>
                                    <option value="">Priority…</option>
                                    {levelChoices(level).map((l) => <option key={l} value={l}>{PRIORITY_LABEL[l] ?? l}</option>)}
                                  </select>
                                )}
                              </span>
                            );
                          })}
                        </div>
                      </td>
                      <td className="py-3 pr-3">
                        <div role="group" aria-label={`Visa in ${name}`} className="inline-flex whitespace-nowrap">
                          {([true, false] as const).map((need) => {
                            const on = rec.needs_sponsorship === need;
                            return (
                              <button key={String(need)} type="button" aria-pressed={on} disabled={busy}
                                data-testid={`work-grid-visa-${need ? "need" : "noneed"}-${code}`}
                                className={`${CHIP} ${chipTone(on)} px-2 ${need ? "rounded-r-none" : "-ml-px rounded-l-none"}`}
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
                      </td>
                      <td className="py-3">
                        {code === "ANY" ? (
                          <span className="text-xs text-muted-foreground">Set per country</span>
                        ) : (
                          <SalaryCell code={code} salaries={salaries} busy={busy}
                            onSave={(v) => void saveSalary(code, v)} />
                        )}
                      </td>
                    </tr>
                    {st?.state === "error" && (
                      <tr>
                        <td colSpan={4} className="pb-3">
                          <p role="alert" className="break-words text-xs text-destructive">{st.message}</p>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        <AddPlace testIdPrefix="work-grid" have={records.map(({ rec }) => String(rec.country))}
          onAdd={(c) => void write(`grid-${c}`, (b) => withPlace(b, c))} />
      </div>
    </section>
  );
}
