"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { countryName } from "@/lib/countries";
import { getCountryPage } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { regionName } from "@/lib/regions";
import type { CountryPage } from "@/lib/types";

type Rec = Record<string, unknown>;
const isRec = (v: unknown): v is Rec => !!v && typeof v === "object" && !Array.isArray(v);

/** A place in plain words: "Germany", "Europe", "every country". */
export const placeName = (code: string) => (code === "ANY" ? "every country" : regionName(code) ?? countryName(code));

/** Where a value comes from, said once and only when it is not the country's own. */
export function fromText(from: string, code: string): string | null {
  if (!from || from === code) return null;
  return from === "ANY" ? "From your settings for every country" : `From your ${placeName(from)} settings`;
}

const FACT: Record<string, string> = {
  work_authorization: "Work authorization",
  needs_sponsorship: "Needs visa sponsorship",
  onsite: "On-site work",
  remote_ok: "Remote work",
  contract_ok: "Contract work",
};
const humanize = (k: string) => FACT[k] ?? k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

/** `right_to_work.AE.needs_sponsorship` -> "Needs visa sponsorship". */
export const confirmItem = (key: string) => humanize(key.split(".").pop() ?? key);

function say(v: unknown): string {
  if (v === true) return "Yes";
  if (v === false) return "No";
  if (Array.isArray(v)) return v.map(say).join(", ");
  if (isRec(v)) return Object.entries(v).map(([k, x]) => `${humanize(k)}: ${say(x)}`).join("; ");
  return String(v ?? "").replace(/_/g, " ");
}

/** The facts of a value as [label, text] pairs; salary reads as one line. Empty values are skipped. */
export function factLines(value: Rec, kind: "rtw" | "salary" | "logistics"): [string, string][] {
  const rest = Object.entries(value).filter(([, v]) => v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && !v.length));
  if (kind === "salary" && (value.min !== undefined || value.max !== undefined)) {
    const range = [value.min, value.max].filter((x) => x !== undefined && x !== null).map((x) => Number(x).toLocaleString("en-GB")).join(" to ");
    const per = value.period ? ` per ${say(value.period)}` : "";
    return [["Range", `${range} ${say(value.currency ?? "")}`.trim() + per]];
  }
  return rest.map(([k, v]) => [humanize(k), say(v)]);
}

/** The right-to-work block with ONE row confirmed: that row loses its `set_by: "assistant"` mark, every other
 *  row goes back exactly as stored (a drafted row stays drafted: only Confirm or an edit of the row confirms it). */
export function confirmRow(block: unknown, code: string): Rec {
  const base = isRec(block) ? block : {};
  const list = Array.isArray(base.countries) ? base.countries : [];
  const countries = list.map((r) => {
    if (!isRec(r) || String(r.country ?? "").toUpperCase() !== code) return r;
    const { set_by: _mark, ...facts } = r;
    void _mark;
    return facts;
  });
  return { ...base, countries };
}

function Block({ title, from, code, children }: { title: string; from?: string; code: string; children: React.ReactNode }) {
  const note = from ? fromText(from, code) : null;
  return (
    <div className="border-t border-border py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <h3 className="text-[13px] font-medium">{title}</h3>
        {note && <span data-testid="country-from" className="text-xs text-faint">{note}</span>}
      </div>
      <div className="mt-1 text-sm">{children}</div>
    </div>
  );
}

function Facts({ value, kind }: { value: Rec; kind: "rtw" | "salary" | "logistics" }) {
  const lines = factLines(value, kind);
  return (
    <dl className="grid grid-cols-[minmax(0,.9fr)_minmax(0,1.2fr)] gap-x-4 gap-y-1">
      {lines.map(([k, v]) => (
        <div key={k} className="contents"><dt className="text-muted-foreground">{k}</dt><dd className="font-medium">{v}</dd></div>
      ))}
    </dl>
  );
}

export function CountryPageView({ page, confirming, onConfirm }: { page: CountryPage; confirming: boolean; onConfirm: () => void }) {
  const code = page.country;
  const draft = page.assistant_drafted === true && page.confirmed === false;
  const answers = (page.answers ?? []) as Rec[];
  const empty = !page.instructions && !page.right_to_work && !page.salary && !page.logistics && !answers.length;
  return (
    <div data-testid={`country-page-${code}`}>
      {draft && (
        <div data-testid="country-draft-banner" className="mb-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl border border-border bg-muted px-3.5 py-2.5">
          <div className="min-w-0 text-[13px]">
            <p className="font-medium text-warning">Your assistant&apos;s draft — confirm</p>
            <p className="text-muted-foreground">
              Your assistant guessed this page. Check it, then confirm.
              {(page.to_confirm ?? []).length > 0 && " To confirm:"}
            </p>
            {(page.to_confirm ?? []).length > 0 && (
              <ul className="list-disc pl-5 text-muted-foreground">
                {(page.to_confirm ?? []).map((k) => <li key={k}>{confirmItem(k)}</li>)}
              </ul>
            )}
          </div>
          <Button type="button" size="sm" className="min-h-11 md:min-h-8" data-testid={`country-confirm-${code}`}
            disabled={confirming} aria-busy={confirming} onClick={onConfirm}>
            {confirming ? "Saving…" : "Confirm"}
          </Button>
        </div>
      )}
      {!draft && page.confirmed === true && (
        <p data-testid="country-yours" className="mb-1 text-xs text-faint">Yours: you saved or confirmed this.</p>
      )}
      {page.instructions && (
        <Block title="Instructions" from={page.instructions.from} code={code}>
          <ul className="list-disc pl-5">{page.instructions.lines.map((l, i) => <li key={i}>{l}</li>)}</ul>
        </Block>
      )}
      {page.right_to_work && <Block title="Right to work" from={page.right_to_work.from} code={code}><Facts value={page.right_to_work.value} kind="rtw" /></Block>}
      {page.salary && <Block title="Salary" from={page.salary.from} code={code}><Facts value={page.salary.value} kind="salary" /></Block>}
      {page.logistics && <Block title="Logistics" from={page.logistics.from} code={code}><Facts value={page.logistics.value} kind="logistics" /></Block>}
      {answers.length > 0 && (
        <Block title="Saved answers" code={code}>
          {answers.map((a, i) => (
            <div key={i} data-testid="country-answer" className="grid grid-cols-1 gap-x-4 py-1 md:grid-cols-[minmax(0,.9fr)_minmax(0,1.2fr)]">
              <span className="text-muted-foreground">{String(a.question ?? "")}</span>
              <span className="font-medium">{String(a.answer ?? "")}</span>
            </div>
          ))}
        </Block>
      )}
      {empty && <p className="border-t border-border py-3 text-sm text-faint">Nothing saved for this place yet</p>}
    </div>
  );
}

/** One section per place the user has, picked in-page. The places come from the saved profile (never a fixed list);
 *  each page is read from the server, so "From …" and the draft state are the server's word. */
export function CountryPages({ codes, version, onConfirm }: { codes: string[]; version: unknown; onConfirm: (code: string) => Promise<void> }) {
  const [pages, setPages] = useState<Record<string, CountryPage>>({});
  const [picked, setPicked] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = codes.join(",");

  useEffect(() => {
    let live = true;
    void Promise.all(codes.map(async (c): Promise<[string, CountryPage | null]> => {
      try { return [c, await getCountryPage(c)]; } catch { return [c, null]; } // one unreadable page hides itself only
    }))
      .then((rows) => {
        if (!live) return;
        setPages(Object.fromEntries(rows.filter((r): r is [string, CountryPage] => !!r[1])));
      });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `key` stands for `codes`
  }, [key, version]);

  const shown = codes.filter((c) => pages[c]);
  if (!shown.length) return null;
  const isDraft = (c: string) => pages[c]?.assistant_drafted === true && pages[c]?.confirmed === false;
  const current = picked && shown.includes(picked) ? picked : shown.find(isDraft) ?? shown[0];

  const confirm = async (code: string) => {
    setConfirming(code);
    setPicked(code); // stay on the page just confirmed
    setError(null);
    try { await onConfirm(code); } catch (err: unknown) { setError(apiErrorMessage(err, "Could not confirm. Try again.")); }
    finally { setConfirming(null); }
  };

  return (
    <section data-testid="country-pages" className="flex flex-col">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 pb-2.5">
        <h2 className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">Countries</h2>
        <span className="text-[12.5px] text-muted-foreground">Everything saved for one place, and where each part comes from.</span>
      </div>
      <div role="tablist" aria-label="Countries" className="mb-2 flex flex-wrap gap-2">
        {shown.map((c) => (
          <button key={c} type="button" role="tab" aria-selected={c === current} data-testid={`country-tab-${c}`}
            onClick={() => setPicked(c)}
            className={`min-h-11 rounded-full border px-3 text-sm md:min-h-8 ${c === current ? "border-primary bg-muted font-medium" : "border-border text-muted-foreground"}`}>
            {placeName(c) === "every country" ? "All other countries" : placeName(c)}
            {isDraft(c) && <span className="ml-1.5 text-xs text-warning">draft</span>}
          </button>
        ))}
      </div>
      {error && <p role="alert" className="mb-2 text-[13px] text-destructive">{error}</p>}
      <CountryPageView page={pages[current]} confirming={confirming === current} onConfirm={() => void confirm(current)} />
    </section>
  );
}
