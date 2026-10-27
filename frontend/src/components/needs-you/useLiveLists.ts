"use client";

// Needs you, live (PR 6): which page elements stand for an ask or a setting request, so a
// refresh keeps the one the user is in (lib/live-hold.ts), and the page's quiet refetch.

import { useEffect, useRef } from "react";
import type { Ask } from "@/lib/api";
import { onChanges, type ChangesDetail } from "@/lib/live-changes";
import { useKeepWhileEditing } from "@/lib/live-hold";
import type { WaitingRequest } from "./SettingRequestCard";

const NO_ASKS: Ask[] = [];
const askKey = (a: Ask) => String(a.id);
// The ask's own card, its Other row, and the job card it sits in.
const askTestids = (a: Ask) =>
  [`ask-${a.id}`, `other-row-question-${a.id}`, ...(a.application_id != null ? [`job-card-${a.application_id}`] : [])];
const requestKey = (r: WaitingRequest) => String(r.id);
const requestTestids = (r: WaitingRequest) => [`other-row-setting-${r.id}`];

/** Open asks as shown: an ask the user is answering survives a refresh that dropped it. */
export function useLiveAsks(open: Ask[] | null): Ask[] {
  return useKeepWhileEditing(open ?? NO_ASKS, askKey, askTestids);
}

/** Waiting setting requests as shown, same rule. */
export function useLiveRequests(waiting: WaitingRequest[]): WaitingRequest[] {
  return useKeepWhileEditing(waiting, requestKey, requestTestids);
}

/** Run `refetch` on every live change: no toast, no spinner (the caller swallows its own errors). */
export function useLiveRefresh(refetch: (d: ChangesDetail) => void): void {
  const ref = useRef(refetch);
  useEffect(() => {
    ref.current = refetch;
  });
  useEffect(() => onChanges((d) => ref.current(d)), []);
}

const jobKey = (h: { id: number }) => String(h.id);
const jobTestids = (h: { id: number }) => [`job-card-${h.id}`, `job-header-${h.id}`, `ready-section-${h.id}`];

/** The job cards as shown: the card the user is in keeps its place and stays until they finish. */
export function useLiveJobs<T extends { id: number }>(jobs: T[]): T[] {
  return useKeepWhileEditing(jobs, jobKey, jobTestids);
}

/** An Other row's items as shown (outcomes, mail): `kind` and `id` name its row on the page. */
export function useLiveRows<T>(items: T[], kind: "outcome" | "mail", id: (t: T) => number): T[] {
  return useKeepWhileEditing(items, (t) => String(id(t)), (t) => [`other-row-${kind}-${id(t)}`]);
}
