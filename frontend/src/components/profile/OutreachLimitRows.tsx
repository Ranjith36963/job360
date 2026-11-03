"use client";

import { useRef, useState } from "react";
import { toast } from "sonner";
import type { AssistantSettingsView } from "@/lib/api";
import { LINKEDIN_LIMIT_NOTE, OUTREACH_LIMITS, settingEffective } from "@/lib/assistant-tab";
import { NUMBOX, Row } from "./BatchRuleRows";
import { cn } from "@/lib/utils";

const text = (v: unknown) => (v == null || v === "" ? "" : String(v));

/** The four outreach limits (owner 2026-11-02). Each box shows the setting's `effective` value; a change
 *  saves like the other number settings (a PATCH as the web user, applied at once). Blank = back to the standard. */
export function OutreachLimitRows({
  view,
  busy,
  save,
}: {
  view: AssistantSettingsView;
  busy: boolean;
  save: (path: string, value: unknown) => void | Promise<void>;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [seen, setSeen] = useState(view);
  const enterDone = useRef(false);
  if (seen !== view) {
    setSeen(view);
    setDrafts({}); // the server's view arrived: show what it holds
  }

  return (
    <>
      {OUTREACH_LIMITS.map((l) => {
        const effective = text(settingEffective(view[l.key]));
        const value = drafts[l.key] ?? effective;
        const commit = () => {
          const t = value.trim();
          if (t === effective) return;
          const n = Number(t);
          if (t === "") void save(l.path, null);
          else if (Number.isInteger(n) && n >= 1 && n <= l.max) void save(l.path, n);
          else {
            toast.error(`${l.title} must be a whole number from 1 to ${l.max}, or blank for the standard. Not saved.`);
            setDrafts((d) => ({ ...d, [l.key]: effective }));
          }
        };
        return (
          <Row
            key={l.key}
            title={l.title}
            sub={l.key === "linkedin_sends_per_day" ? `${l.sub} ${LINKEDIN_LIMIT_NOTE}` : l.sub}
          >
            <input
              type="number"
              inputMode="numeric"
              min={1}
              max={l.max}
              aria-label={l.title}
              data-testid={l.testid}
              value={value}
              disabled={busy}
              onChange={(e) => {
                enterDone.current = false;
                setDrafts((d) => ({ ...d, [l.key]: e.target.value }));
              }}
              onBlur={() => {
                if (enterDone.current) enterDone.current = false;
                else commit();
              }}
              onKeyDown={(e) => {
                if (e.key !== "Enter") return;
                e.preventDefault();
                enterDone.current = true;
                commit();
              }}
              className={cn(NUMBOX, "w-24")}
            />
          </Row>
        );
      })}
    </>
  );
}
