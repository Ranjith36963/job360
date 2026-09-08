import type { ApplicationReceiptEntry } from "@/lib/api";

/** The Receipts section on an application's record page (fix 3, agentic UX
 * audit): every "I applied" receipt, in the order the backend returns them.
 * Only rendered by the caller when there is at least one. */
export function Receipts({ receipts }: { receipts: ApplicationReceiptEntry[] }) {
  return (
    <ul className="flex flex-col gap-2">
      {receipts.map((receipt) => (
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
        </li>
      ))}
    </ul>
  );
}
