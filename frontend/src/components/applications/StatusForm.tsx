"use client";

import { useId, useState, type FormEvent, type ReactNode } from "react";
import { STATUS_LABEL } from "@/lib/event-labels";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** The statuses that open a small form instead of a bare "Confirm". */
export const STATUS_FORM_TYPES = ["interview_requested", "interview_scheduled", "offer", "rejected"] as const;

export function hasStatusForm(eventType: string): boolean {
  return (STATUS_FORM_TYPES as readonly string[]).includes(eventType);
}

/** What the form hands the menu: the fields `recordApplicationEvent` takes. */
export interface StatusFormBody {
  event_type: string;
  scheduled_at?: string;
  payload?: Record<string, unknown>;
}

// Backend closed set (interview_payloads.ROUNDS) and OFFER_PERIODS.
const ROUND_OPTIONS: [string, string][] = [
  ["screen", "Screening call"],
  ["hiring-manager", "Hiring manager"],
  ["technical", "Technical"],
  ["system-design", "System design"],
  ["behavioral", "Behavioural"],
  ["onsite", "On-site"],
  ["final", "Final"],
];
const PERIOD_OPTIONS = ["year", "month", "week", "day", "hour"] as const;
const SELECT_CLASS =
  "h-8 w-full rounded-lg border border-border bg-card px-2.5 text-xs text-foreground outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50";
const LINK_PATTERN = /^https?:\/\/\S+$/i;
const REASONS_MAX_ITEMS = 10; // spine.REASONS_MAX_ITEMS
const REASON_MAX_CHARS = 120; // spine.REASON_MAX_CHARS

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className="flex min-w-[9rem] flex-1 flex-col gap-1">
      <Label htmlFor={htmlFor} className="text-xs font-normal text-muted-foreground">
        {label}
      </Label>
      {children}
    </div>
  );
}

/** "What happened?" details for an interview, an offer or a rejection. Every
 * field is optional except the date and time of a scheduled interview. */
export function StatusForm({
  eventType,
  saving,
  onSubmit,
  onCancel,
}: {
  eventType: string;
  saving: boolean;
  onSubmit: (body: StatusFormBody) => void;
  onCancel: () => void;
}) {
  const uid = useId();
  const isInterview = eventType === "interview_requested" || eventType === "interview_scheduled";
  const isOffer = eventType === "offer";
  const [when, setWhen] = useState("");
  const [round, setRound] = useState("");
  const [link, setLink] = useState("");
  const [duration, setDuration] = useState("60");
  const [location, setLocation] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("GBP");
  const [period, setPeriod] = useState<string>("year");
  const [stage, setStage] = useState("");
  const [reasonsText, setReasonsText] = useState("");

  const reasons = reasonsText
    .split(",")
    .map((r) => r.trim())
    .filter(Boolean);
  const minutes = duration.trim() === "" ? null : Number(duration);
  const figure = amount.trim() === "" ? null : Number(amount);

  let problem: string | null = null;
  if (isInterview) {
    if (eventType === "interview_scheduled" && !when) problem = "Pick the date and time of the interview.";
    else if (link.trim() && !LINK_PATTERN.test(link.trim())) problem = "The link must start with http:// or https://.";
    else if (minutes !== null && (!Number.isInteger(minutes) || minutes < 5 || minutes > 480)) {
      problem = "Length must be a whole number of minutes from 5 to 480.";
    }
  } else if (isOffer) {
    if (figure !== null && !(Number.isFinite(figure) && figure > 0)) problem = "The amount must be more than 0.";
    else if (figure !== null && !/^[A-Z]{3}$/.test(currency)) problem = "Currency is three letters, like GBP.";
  } else if (reasons.length > REASONS_MAX_ITEMS || reasons.some((r) => r.length > REASON_MAX_CHARS)) {
    problem = `Give up to ${REASONS_MAX_ITEMS} short reasons (${REASON_MAX_CHARS} characters each).`;
  }

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (problem || saving) return;
    const payload: Record<string, unknown> = {};
    const body: StatusFormBody = { event_type: eventType };
    if (isInterview) {
      if (when) body.scheduled_at = new Date(when).toISOString();
      if (round) payload.round = round;
      if (link.trim()) payload.link = link.trim();
      if (minutes !== null) payload.duration_minutes = minutes;
      if (location.trim()) payload.location = location.trim();
    } else if (isOffer) {
      // The backend wants the three offer figures together, so the defaults
      // for currency and period are only sent along with an amount.
      if (figure !== null) {
        payload.offer_amount = figure;
        payload.offer_currency = currency;
        payload.offer_period = period;
      }
    } else {
      if (stage.trim()) payload.stage = stage.trim();
      if (reasons.length) payload.reasons = reasons;
    }
    if (Object.keys(payload).length) body.payload = payload;
    onSubmit(body);
  };

  const kind = isInterview ? "interview" : isOffer ? "offer" : "rejected";
  return (
    <form
      data-testid={`status-form-${kind}`}
      onSubmit={submit}
      className="flex w-full flex-col gap-2 border-t border-border pt-2 text-xs text-muted-foreground"
    >
      <p>
        Add details for &quot;{STATUS_LABEL[eventType] ?? eventType}&quot;. This is added to the history.
        {eventType === "interview_scheduled" ? " Only the date and time is needed." : " Everything here is optional."}
      </p>
      <div className="flex flex-wrap gap-2">
        {isInterview && (
          <>
            <Field label={eventType === "interview_scheduled" ? "Date and time" : "Date and time (if known)"} htmlFor={`${uid}-when`}>
              <Input id={`${uid}-when`} data-testid="status-form-when" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
            </Field>
            <Field label="Round" htmlFor={`${uid}-round`}>
              <select id={`${uid}-round`} data-testid="status-form-round" className={SELECT_CLASS} value={round} onChange={(e) => setRound(e.target.value)}>
                <option value="">Not sure</option>
                {ROUND_OPTIONS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Length (minutes)" htmlFor={`${uid}-duration`}>
              <Input id={`${uid}-duration`} data-testid="status-form-duration" type="number" min={5} max={480} value={duration} onChange={(e) => setDuration(e.target.value)} />
            </Field>
            <Field label="Video link" htmlFor={`${uid}-link`}>
              <Input id={`${uid}-link`} data-testid="status-form-link" type="url" placeholder="https://" maxLength={500} value={link} onChange={(e) => setLink(e.target.value)} />
            </Field>
            <Field label="Place" htmlFor={`${uid}-location`}>
              <Input id={`${uid}-location`} data-testid="status-form-location" maxLength={200} placeholder="Office address, or Phone" value={location} onChange={(e) => setLocation(e.target.value)} />
            </Field>
          </>
        )}
        {isOffer && (
          <>
            <Field label="Amount" htmlFor={`${uid}-amount`}>
              <Input id={`${uid}-amount`} data-testid="status-form-amount" type="number" min={0} step="any" value={amount} onChange={(e) => setAmount(e.target.value)} />
            </Field>
            <Field label="Currency" htmlFor={`${uid}-currency`}>
              <Input id={`${uid}-currency`} data-testid="status-form-currency" maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} />
            </Field>
            <Field label="Paid per" htmlFor={`${uid}-period`}>
              <select id={`${uid}-period`} data-testid="status-form-period" className={SELECT_CLASS} value={period} onChange={(e) => setPeriod(e.target.value)}>
                {PERIOD_OPTIONS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </Field>
          </>
        )}
        {kind === "rejected" && (
          <>
            <Field label="Stage it ended at" htmlFor={`${uid}-stage`}>
              <Input id={`${uid}-stage`} data-testid="status-form-stage" maxLength={60} placeholder="After the first interview" value={stage} onChange={(e) => setStage(e.target.value)} />
            </Field>
            <Field label="Reasons they gave (separate with commas)" htmlFor={`${uid}-reasons`}>
              <Input id={`${uid}-reasons`} data-testid="status-form-reasons" placeholder="Too junior, no visa" value={reasonsText} onChange={(e) => setReasonsText(e.target.value)} />
            </Field>
          </>
        )}
      </div>
      {problem && (
        <p data-testid="status-form-problem" role="alert" className="text-destructive">
          {problem}
        </p>
      )}
      <div className="flex gap-2">
        <Button
          type="submit"
          size="sm"
          data-testid="status-form-submit"
          className="bg-foreground text-background hover:bg-foreground/90"
          disabled={saving || problem !== null}
        >
          {saving ? "Recording…" : "Record"}
        </Button>
        <Button type="button" size="sm" variant="outline" className="border-border bg-card" data-testid="status-cancel" disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
