import { CONTACT_FOUND_VIA_VALUES, closedSetLabel } from "@/lib/closed-sets";

/** Optional "where you found this person" select. "Not set" is the empty
 * value — never a default (rule #29). A stored value outside the closed set
 * is kept as an extra option so opening the form never erases it. */
export function FoundViaSelect({
  id,
  value,
  onChange,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const known = (CONTACT_FOUND_VIA_VALUES as readonly string[]).includes(value);
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-9 rounded-lg border border-border bg-background px-2.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <option value="">Not set</option>
      {CONTACT_FOUND_VIA_VALUES.map((v) => (
        <option key={v} value={v}>
          {closedSetLabel(v)}
        </option>
      ))}
      {value && !known && <option value={value}>{closedSetLabel(value)}</option>}
    </select>
  );
}
