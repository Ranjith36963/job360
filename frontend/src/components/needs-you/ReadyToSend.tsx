"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { getReadyToSend } from "@/lib/api";
import type { ReadyToSend as ReadyData } from "@/lib/api";
import { PAUSE_CHANGED_EVENT, READY_CHANGED_EVENT } from "@/lib/assistant-state";
import { splitReady } from "@/lib/ready-to-send";
import { ReadyCard } from "./ReadyCard";
import { SendAllCard } from "./SendAllCard";
import { cn } from "@/lib/utils";

const sectionLabel = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";

/**
 * "Ready to send" on Needs you (owner decisions 2026-10-09): every application
 * whose form an assistant filled and stopped before Submit. Each card shows all
 * the answers and where they came from; "Send all unflagged" sends the clean
 * ones after one confirm. Nothing waiting = no section at all.
 */
export function ReadyToSend({ onTotal }: { onTotal?: (total: number) => void }) {
  const [data, setData] = useState<ReadyData | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(() => {
    getReadyToSend().then(
      (d) => {
        setData(d);
        setFailed(false);
        onTotal?.(d.total);
      },
      () => {
        setFailed(true);
        onTotal?.(0); // the badge counts 0 for a failed read, as useOpenAsks does
      },
    );
  }, [onTotal]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const onPause = () => load();
    window.addEventListener(PAUSE_CHANGED_EVENT, onPause);
    return () => window.removeEventListener(PAUSE_CHANGED_EVENT, onPause);
  }, [load]);

  // After any send or decline: re-read this list, and tell the morning check strip.
  const changed = useCallback(() => {
    load();
    window.dispatchEvent(new CustomEvent(READY_CHANGED_EVENT));
  }, [load]);

  const { clean, held } = useMemo(() => splitReady(data?.items ?? []), [data]);

  if (failed && !data) {
    return (
      <div role="alert" className="flex items-center gap-3 text-sm text-destructive">
        <span>Could not load Ready to send.</span>
        <button type="button" data-testid="ready-retry" onClick={load} className="text-xs font-medium underline-offset-2 hover:underline">
          Try again
        </button>
      </div>
    );
  }
  if (!data || data.items.length === 0) return null;

  return (
    <section aria-labelledby="ready-to-send" data-testid="ready-to-send" className="flex flex-col gap-3">
      <h2 id="ready-to-send" className={cn(sectionLabel, data.paused && "opacity-70")}>
        {data.paused ? "Ready to send · on hold while paused" : "Ready to send"}
      </h2>
      <ul className="flex flex-col gap-3">
        {data.items.map((c) => (
          <li key={c.application_id}>
            <ReadyCard card={c} paused={data.paused} onChanged={changed} />
          </li>
        ))}
      </ul>
      {clean.length >= 2 && <SendAllCard clean={clean} held={held} paused={data.paused} onChanged={changed} />}
    </section>
  );
}
