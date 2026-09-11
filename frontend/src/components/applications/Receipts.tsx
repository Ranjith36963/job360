import type { ApplicationReceiptEntry } from "@/lib/api";

function formatFieldValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** The Receipts section on an application's record page (fix 3, agentic UX
 * audit): every "I applied" receipt, in the order the backend returns them.
 * Only rendered by the caller when there is at least one. Since 2026-09-11
 * it also shows what was actually sent — the answers given and the form
 * fields filled (R8) — which the backend stored since 0037 but never
 * returned. The `?? []` guards keep a cached pre-2026-09-11 payload
 * (no keys) rendering rather than crashing. */
export function Receipts({ receipts }: { receipts: ApplicationReceiptEntry[] }) {
  return (
    <ul className="flex flex-col gap-2">
      {receipts.map((receipt) => {
        const answers = receipt.answers ?? [];
        const fieldEntries = Object.entries(receipt.fields_filled ?? {}).sort(([a], [b]) =>
          a.localeCompare(b)
        );

        return (
          <li key={receipt.id} data-testid="receipt-row" className="glass-card rounded-lg p-3 text-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">{receipt.channel || "Applied"}</span>
              <span className="text-xs text-muted-foreground">
                {new Date(receipt.sent_at).toLocaleDateString()}
              </span>
            </div>
            {(receipt.cv_artifact_id != null || receipt.cover_letter_artifact_id != null) && (
              <p className="mt-1 text-xs text-muted-foreground">
                {receipt.cv_artifact_id != null && `CV #${receipt.cv_artifact_id}`}
                {receipt.cv_artifact_id != null && receipt.cover_letter_artifact_id != null && " · "}
                {receipt.cover_letter_artifact_id != null &&
                  `Cover letter #${receipt.cover_letter_artifact_id}`}
              </p>
            )}
            {receipt.confirmation && (
              <p className="mt-1 text-xs text-muted-foreground">Confirmation: {receipt.confirmation}</p>
            )}
            {receipt.note && <p className="mt-1 text-muted-foreground">{receipt.note}</p>}
            {answers.length > 0 && (
              <div data-testid="receipt-answers" className="mt-2">
                <p className="text-xs font-medium uppercase text-muted-foreground">Answers given</p>
                <dl className="mt-1 flex flex-col gap-2">
                  {answers.map((qa, i) => (
                    <div key={i} data-testid="receipt-answer">
                      <dt className="text-xs text-muted-foreground">{qa.question}</dt>
                      <dd className="whitespace-pre-wrap">{qa.answer}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
            {fieldEntries.length > 0 && (
              <div data-testid="receipt-fields" className="mt-2">
                <p className="text-xs font-medium uppercase text-muted-foreground">Fields filled</p>
                <dl className="mt-1 flex flex-col gap-1">
                  {fieldEntries.map(([key, value]) => (
                    <div key={key} data-testid="receipt-field" className="flex items-baseline gap-1">
                      <dt className="text-xs text-muted-foreground">{key}:</dt>
                      <dd className="whitespace-pre-wrap">{formatFieldValue(value)}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
