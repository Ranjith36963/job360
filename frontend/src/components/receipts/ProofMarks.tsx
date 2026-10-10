import type { Proof } from "@/lib/types";

/** A confirmation value longer than this is not repeated in the mark (it is in the facts below). */
const CONFIRMATION_SHOWN_MAX = 24;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "02 Oct 18:52" in the viewer's time zone, built by hand so no locale changes it. Bad input → null. */
export function proofTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const two = (n: number) => String(n).padStart(2, "0");
  return `${two(d.getDate())} ${MONTHS[d.getMonth()]} ${two(d.getHours())}:${two(d.getMinutes())}`;
}

/**
 * The stored proof parts, strongest first, as marks. Only what Job360 holds:
 * `has_page_text` (pasted thank-you text) and `has_confirmation` (a receipt's
 * reference) are separate, so a confirmation number alone never reads as a
 * saved thank-you page. With `detail`, the sheet adds the value and the time.
 */
function proofMarks(
  proof: Proof | null | undefined,
  confirmation: string | null | undefined,
  detail: boolean,
): string[] {
  const marks: string[] = [];
  const conf = (confirmation ?? "").trim();
  if (conf || proof?.has_confirmation) {
    const value = detail && conf && conf.length <= CONFIRMATION_SHOWN_MAX ? ` · ${conf}` : "";
    marks.push(`Confirmation number saved${value}`);
  }
  if (proof?.has_email) {
    const at = detail ? proofTime(proof.email_seen_at) : null;
    marks.push(at ? `Confirmation email seen · ${at}` : "Confirmation email seen");
  }
  if (proof?.has_page_text) marks.push("Thank-you page saved");
  if (proof && proof.screenshots > 0) marks.push(`Screenshot saved (${proof.screenshots})`);
  return marks;
}

/**
 * Marks for one row of the receipts list, at the row end. Missing proof (a
 * receipt with no application) reads "No proof yet" - never a guess.
 */
export function ProofMark({ proof }: { proof?: Proof | null }) {
  const marks = proofMarks(proof, null, false);
  return (
    <span data-testid="proof-marks" className="inline-flex flex-wrap gap-1.5">
      {marks.length === 0 ? (
        <span
          data-testid="proof-mark"
          className="whitespace-nowrap rounded-full border border-dashed border-faint px-2 py-px font-mono text-[11px] font-medium text-muted-foreground"
        >
          No proof yet
        </span>
      ) : (
        marks.map((m) => (
          <span
            key={m}
            data-testid="proof-mark"
            className="whitespace-nowrap rounded-full bg-brand-soft px-2 py-px font-mono text-[11px] font-medium text-brand"
          >
            {m}
          </span>
        ))
      )}
    </span>
  );
}

/**
 * The "Proof" line on the white receipt sheet. Shows only what Job360 has
 * stored; the sheet is white in both themes, so it uses the paper tokens.
 */
export function ProofMarks({
  proof,
  confirmation,
}: {
  proof?: Proof | null;
  confirmation?: string | null;
}) {
  const marks = proofMarks(proof, confirmation, true);

  return (
    <div data-testid="proof-line" className="mt-5 flex flex-wrap items-center gap-x-2.5 gap-y-2">
      <span className="mr-1 text-paper-dim">Proof</span>
      {marks.length === 0 ? (
        <span
          data-testid="proof-none"
          className="rounded-full border border-dashed border-paper-line px-2.5 py-0.5 font-mono text-xs font-medium text-paper-dim"
        >
          No proof yet
        </span>
      ) : (
        marks.map((m) => (
          <span
            key={m}
            data-testid="proof-chip"
            className="inline-flex items-center gap-1.5 rounded-full border border-paper-green bg-paper-soft px-2.5 py-0.5 font-mono text-xs font-medium text-paper-ink [overflow-wrap:anywhere]"
          >
            <i className="inline-block h-[7px] w-[7px] shrink-0 rounded-full bg-paper-green" />
            {m}
          </span>
        ))
      )}
    </div>
  );
}
