"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { updateProfileFields } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { countryName } from "@/lib/countries";
import { PRIORITY_LABEL, PRIORITY_OPTIONS, RTW } from "@/lib/memory";
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

type Status = "saving" | "saved" | "error";

/** Profile -> CV tab: where should the assistant look first. Writes the two
 *  optional priority keys on each right-to-work record, nothing else. */
export function JobPriority({
  rightToWork, onSaved,
}: { rightToWork: unknown; onSaved: (profile: ProfileResponse) => void }) {
  // Saves start from the LATEST block (not the render's copy) and run one
  // after another, so two quick changes never drop each other.
  const latest = useRef<unknown>(rightToWork);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const [status, setStatus] = useState<Record<string, { state: Status; message?: string }>>({});

  useEffect(() => { latest.current = rightToWork; }, [rightToWork]);

  const rows = priorityRows(rightToWork);

  const change = (row: PlaceRow, value: string): Promise<void> => {
    setStatus((s) => ({ ...s, [row.id]: { state: "saving" } }));
    const run = async () => {
      try {
        const block = withPriority(latest.current, row.index, row.key, value);
        const updated = await updateProfileFields([{ path: RTW, value: block }]);
        latest.current = (updated.user_info as Rec | undefined)?.right_to_work ?? block;
        onSaved(updated);
        setStatus((s) => ({ ...s, [row.id]: { state: "saved" } }));
      } catch (err: unknown) {
        setStatus((s) => ({
          ...s, [row.id]: { state: "error", message: apiErrorMessage(err, "Could not save.") },
        }));
      }
    };
    const done = queue.current.then(run, run);
    queue.current = done;
    return done;
  };

  return (
    <section data-testid="job-priority" className="rounded-xl border border-border bg-card p-6">
      <h3 className={LABEL}>Job priority</h3>
      <p className="mt-2 text-xs text-muted-foreground">
        Where should your assistant look first? It spends more of the hunt on higher priorities. Off = never search there.
      </p>
      {rows.length === 0 ? (
        <p data-testid="job-priority-empty" className="mt-4 text-sm text-muted-foreground">
          Add where you can work in the{" "}
          <Link href="/profile?tab=memory" className="text-brand underline">Memory tab</Link> first.
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
    </section>
  );
}
