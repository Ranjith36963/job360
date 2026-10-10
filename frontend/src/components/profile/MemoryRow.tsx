"use client";

import { useId, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CountryPicker } from "@/components/profile/CountryPicker";
import { apiErrorMessage } from "@/lib/api-error";
import {
  PNS,
  fromInputs,
  inputProblem,
  isEmpty,
  isPns,
  provenanceText,
  showRow,
  toInputs,
  type Part,
  type Provenance,
  type RowSpec,
} from "@/lib/memory";

export const CONFIRM_TEXT = "This answer goes on legal forms. Save?";

interface MemoryRowProps {
  spec: RowSpec;
  /** The value saved now (`readRow` of the live block); empty = nothing saved. */
  value: unknown;
  provenance: Provenance | null;
  /** Shown instead of provenance when nothing is saved ("Your assistant will ask"). */
  emptyNote?: string;
  /** Stacked layout for the narrow side rail. */
  compact?: boolean;
  /** Empty reads "not saved yet" in amber (a missing salary). */
  amberEmpty?: boolean;
  /** Write the new value (undefined = clear). Throws when the save fails. */
  onSave: (next: unknown) => Promise<void>;
}

const FIELD =
  "h-9 w-full min-w-0 rounded-lg border border-border bg-background px-2.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const TAP = "min-h-11 md:min-h-8";

function PartField({
  part, value, disabled, onChange,
}: { part: Part; value: string; disabled: boolean; onChange: (v: string) => void }) {
  const id = useId();
  const label = <label htmlFor={id} className="text-xs text-muted-foreground">{part.label}</label>;
  if (part.input === "countries") {
    return (
      <CountryPicker
        label={part.label}
        tags={value ? value.split(",") : []}
        onChange={(codes) => onChange(codes.join(","))}
      />
    );
  }
  if (part.input === "yesno" || part.input === "select") {
    const opts: [string, string][] =
      part.input === "yesno"
        ? [...(part.pns ? [["pns", PNS] as [string, string]] : []), ["yes", "Yes"], ["no", "No"]]
        : (part.options ?? []).map((o) => [o, o.replace(/_/g, " ")]);
    return (
      <div className="flex flex-col gap-1">
        {label}
        <select id={id} className={FIELD} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
          <option value="">Choose…</option>
          {opts.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
        </select>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1">
      {label}
      {part.pns && (
        <Button
          type="button" variant="outline" size="sm" className={`self-start ${TAP}`}
          disabled={disabled} onClick={() => onChange(PNS)}
        >
          {PNS}
        </Button>
      )}
      {part.input === "textarea" ? (
        <textarea id={id} rows={3} className={`${FIELD} h-auto py-2`} value={value} disabled={disabled}
          onChange={(e) => onChange(e.target.value)} />
      ) : (
        <Input id={id} value={value} disabled={disabled} placeholder={part.placeholder}
          type={part.input === "date" ? "date" : part.input === "number" ? "number" : "text"}
          onChange={(e) => onChange(e.target.value)} />
      )}
    </div>
  );
}

type Mode = "view" | "edit" | "confirm-edit" | "confirm-back";

/** One remembered fact: key · value · who saved it · Edit. Editing asks first
 *  for the sensitive ones; a changed row offers "was: …" and Take back. */
export function MemoryRow({ spec, value, provenance, emptyNote, compact, amberEmpty, onSave }: MemoryRowProps) {
  const [mode, setMode] = useState<Mode>("view");
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const empty = isEmpty(value);
  const shown = showRow(spec, value);
  const previous = provenance?.previous;
  const wasText = !isEmpty(previous) ? showRow(spec, previous) : "";

  async function save(next: unknown, failure: string) {
    setBusy(true);
    try {
      await onSave(next);
      toast.success("Saved.");
      setMode("view");
    } catch (err: unknown) {
      toast.error(apiErrorMessage(err, failure));
    } finally {
      setBusy(false);
    }
  }

  function startEdit() {
    setInputs(toInputs(spec, value));
    setProblem(null);
    setMode("edit");
  }

  function submit() {
    const bad = inputProblem(spec, inputs);
    setProblem(bad);
    if (bad) return;
    if (spec.sensitive) setMode("confirm-edit");
    else void save(fromInputs(spec, inputs), "Could not save.");
  }

  const confirm = (go: () => void) => (
    <div
      data-testid="memory-confirm"
      className="flex flex-wrap items-center gap-2 rounded-lg bg-warning-soft px-3 py-2 text-sm font-medium text-warning"
    >
      {CONFIRM_TEXT}
      <span className="ml-auto flex gap-2">
        <Button type="button" size="sm" className={TAP} disabled={busy} onClick={go}>Save</Button>
        <Button type="button" size="sm" variant="outline" className={TAP} disabled={busy} onClick={() => setMode("view")}>
          Cancel
        </Button>
      </span>
    </div>
  );

  const grid = compact
    ? "grid grid-cols-1 gap-1"
    : "grid grid-cols-1 gap-x-4 gap-y-1 md:grid-cols-[minmax(0,.9fr)_minmax(0,1.2fr)_minmax(0,1fr)_auto]";

  return (
    <div data-testid={`memory-row-${spec.id}`} className={`${grid} items-baseline border-t border-border py-3 text-sm`}>
      <span className="text-muted-foreground">{spec.label}</span>
      <span className="min-w-0 font-medium">
        {empty ? (
          <span className={amberEmpty ? "font-normal text-warning" : "font-normal text-faint"}>
            {amberEmpty ? "not saved yet" : "Not saved yet"}
          </span>
        ) : isPns(value) ? (
          <span className="inline-flex rounded-full bg-brand-soft px-2.5 py-0.5 text-[13px] text-brand">{PNS}</span>
        ) : (
          shown
        )}
        {wasText && mode === "view" && (
          <span className="mt-0.5 block font-mono text-xs font-normal text-faint">
            was: {wasText}
            <button
              type="button" data-testid="memory-take-back"
              className={`ml-2 underline underline-offset-2 hover:text-foreground ${TAP}`}
              onClick={() => (spec.sensitive ? setMode("confirm-back") : void save(previous, "Could not take that back."))}
            >
              Take back
            </button>
          </span>
        )}
      </span>
      <span className="font-mono text-xs text-faint">
        {provenance ? provenanceText(provenance) : empty ? (emptyNote ?? "") : ""}
      </span>
      {mode === "view" && (
        <button
          type="button" onClick={startEdit} aria-label={`Edit ${spec.label}`}
          className={`justify-self-start text-sm text-muted-foreground underline-offset-2 hover:text-foreground hover:underline md:justify-self-end ${TAP}`}
        >
          Edit
        </button>
      )}

      {mode === "confirm-back" && <div className="col-span-full">{confirm(() => void save(previous, "Could not take that back."))}</div>}

      {(mode === "edit" || mode === "confirm-edit") && (
        <div className="col-span-full mt-2 flex flex-col gap-3 rounded-xl border border-brand p-3.5">
          <p className="font-mono text-[11px] uppercase tracking-[0.09em] text-faint">Editing · {spec.label}</p>
          {spec.parts.map((part) => (
            <PartField
              key={part.key} part={part} value={inputs[part.key] ?? ""} disabled={busy || mode === "confirm-edit"}
              onChange={(v) => setInputs((cur) => ({ ...cur, [part.key]: v }))}
            />
          ))}
          {problem && <p role="alert" className="text-xs text-destructive">{problem}</p>}
          {mode === "confirm-edit" ? (
            confirm(() => void save(fromInputs(spec, inputs), "Could not save."))
          ) : (
            <div className="flex gap-2">
              <Button type="button" size="sm" className={TAP} disabled={busy} onClick={submit}>Save</Button>
              <Button type="button" size="sm" variant="outline" className={TAP} onClick={() => setMode("view")}>Cancel</Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
