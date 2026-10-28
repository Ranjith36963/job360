"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { LessonRow } from "@/components/applications/LessonRow";
import { BookOpen } from "lucide-react";
import { listLessons, type Lesson } from "@/lib/api";
import { formatDate } from "@/lib/format-date";

/** Home page's "Lessons" section (moved from Profile 2026-10-26) (slice 9, #516) — every lesson flagged
 * from any application's timeline, newest first, linking back to its
 * application. This is the store half of the loop: the agent reads these
 * back (door 2, `ProfileResponse.lessons`) before tailoring the next CV. */
export function LessonsList() {
  const [lessons, setLessons] = useState<Lesson[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const [version, setVersion] = useState(0);
  const reload = useCallback(async () => setVersion((v) => v + 1), []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await listLessons({ limit: 50 });
        if (cancelled) return;
        setLessons(res.lessons);
        setTotal(res.total);
      } catch {
        if (cancelled) return;
        setError("Could not load lessons.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [version]);

  return (
    <div className="rounded-xl border border-border bg-card p-6">
      <h2 className="font-heading text-lg font-normal mb-4 flex items-center gap-2">
        <BookOpen className="h-4 w-4 text-muted-foreground" />
        Lessons
      </h2>

      {error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : lessons === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : lessons.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No lessons yet. Flag one from any application&apos;s timeline.
        </p>
      ) : (
        <>
          <ul className="divide-y divide-border">
            {lessons.map((l) => (
              <li key={l.event_id} data-testid="lesson-item" className="py-3 first:pt-0 last:pb-0">
                <LessonRow
                  applicationId={l.application_id}
                  eventId={l.event_id}
                  detail={l.detail}
                  recordedBy={l.recorded_by}
                  onChanged={reload}
                />
                <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                  <Link
                    href={`/applications/${l.application_id}`}
                    data-testid="lesson-link"
                    className="hover:text-foreground hover:underline"
                  >
                    {l.job_title || "Untitled role"} · {l.job_company}
                  </Link>
                  {" · "}
                  {formatDate(l.occurred_at)}
                </p>
              </li>
            ))}
          </ul>
          {total > lessons.length && (
            <p className="mt-4 text-xs text-muted-foreground">
              Showing {lessons.length} of {total}.
            </p>
          )}
        </>
      )}
    </div>
  );
}
