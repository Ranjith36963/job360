"use client";

import { useMemo, useState } from "react";
import { X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { allCountries, countryName } from "@/lib/countries";

interface CountryPickerProps {
  label: string;
  /** ISO alpha-2 codes — the wire value, unchanged (backend validates and
   *  stores exactly this shape; see docs/product/VISION.md decision 27). */
  tags: string[];
  onChange: (codes: string[]) => void;
  description?: string;
  placeholder?: string;
  /** Extra content after the label — e.g. an `EditedMark` (spec R11). */
  trailing?: React.ReactNode;
  /** Extra content under the field — e.g. its `FieldHistory` link. */
  footer?: React.ReactNode;
  /** `data-testid` on the field's outer wrapper. */
  testId?: string;
  /** Cap on how many matches show at once — keeps a broad search (e.g. "a")
   *  from dumping dozens of rows. */
  maxMatches?: number;
}

/** Rule #29: empty is "don't care", never a penalty — this picker starts
 * empty and stores nothing until the person actually searches and picks a
 * country. A searchable multi-select over the full ISO list (never a
 * hand-typed partial one — see lib/countries.ts) that stores alpha-2 codes
 * while showing real country names, replacing the old free-typed
 * ISO-code text box. */
export function CountryPicker({
  label,
  tags,
  onChange,
  description,
  placeholder = "Search countries…",
  trailing,
  footer,
  testId,
  maxMatches = 8,
}: CountryPickerProps) {
  const [query, setQuery] = useState("");
  const countries = useMemo(() => allCountries(), []);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return countries
      .filter((c) => !tags.includes(c.code) && c.name.toLowerCase().includes(q))
      .slice(0, maxMatches);
  }, [query, tags, countries, maxMatches]);

  function addCountry(code: string) {
    if (!tags.includes(code)) onChange([...tags, code]);
    setQuery("");
  }

  function removeCountry(code: string) {
    onChange(tags.filter((c) => c !== code));
  }

  return (
    <div className="space-y-2" data-testid={testId}>
      <Label className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
        {label}
        {trailing}
      </Label>
      {description && (
        <p className="text-xs text-muted-foreground -mt-1">{description}</p>
      )}
      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {tags.map((code) => (
            <span
              key={code}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-card px-2.5 py-1 text-xs font-medium text-foreground"
            >
              {countryName(code)}
              <button
                type="button"
                onClick={() => removeCountry(code)}
                aria-label={`Remove ${countryName(code)}`}
                className="ml-0.5 rounded-sm p-0.5 opacity-60 hover:opacity-100 transition-opacity"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <Input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={placeholder}
        aria-label={`Search countries to add to ${label}`}
        onKeyDown={(e) => {
          if (e.key === "Enter" && matches.length > 0) {
            e.preventDefault();
            addCountry(matches[0].code);
          }
        }}
      />
      {matches.length > 0 && (
        <div
          className="flex flex-wrap gap-1.5"
          role="listbox"
          aria-label={`Matching countries for ${label}`}
        >
          {matches.map((c) => (
            <button
              key={c.code}
              type="button"
              role="option"
              aria-selected={false}
              onClick={() => addCountry(c.code)}
              className="inline-flex items-center gap-1 rounded-full border border-dashed border-border px-2.5 py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground"
            >
              {c.name}
            </button>
          ))}
        </div>
      )}
      {footer}
    </div>
  );
}
