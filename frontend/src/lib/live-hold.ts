"use client";

// A live refresh never closes or moves what the user is working on (Needs-you PR 6).
// Cards are keyed by application id and asks by ask id, so React keeps their typed text
// across a refresh. This file covers the other half: when a refresh would DROP or MOVE an item
// the user is in, the list keeps it where it was until they finish.
//
// "In it" is read off the page, by the item's data-testid, so no card needs new props:
//  - a text field inside it has focus, or
//  - the user typed in it and has saved nothing since (lib/live-writes.ts): a save is "finished".
//    A button alone is not: "Save answer" only opens the confirm step, the text is still at stake;
//  - the user opened a panel in it (aria-expanded="true") and pressed no other button in it since
//    (Accept, Withdraw, Send are "finished"), nor saved anything.

import { useEffect, useState } from "react";
import { userWrites } from "./live-writes";

// testid -> the save count when the user last typed in it / opened a panel in it. It holds only
// while no save has happened since.
const typed = new Map<string, number>();
const opened = new Map<string, number>();
let installed = false;

function testidsAbove(el: EventTarget | null): string[] {
  const out: string[] = [];
  for (let n = el instanceof Element ? el : null; n; n = n.parentElement) {
    const id = n.getAttribute("data-testid");
    if (id) out.push(id);
  }
  return out;
}

function install() {
  if (installed || typeof document === "undefined") return;
  installed = true;
  document.addEventListener("input", (e) => {
    for (const id of testidsAbove(e.target)) typed.set(id, userWrites());
  }, true);
  document.addEventListener("click", (e) => {
    const button = e.target instanceof Element ? e.target.closest("button, [aria-expanded]") : null;
    if (!button) return;
    // Opening a panel holds it; any other button in it (Accept, Withdraw, Send) is "finished".
    const toggle = button.hasAttribute("aria-expanded");
    for (const id of testidsAbove(button)) {
      if (toggle) opened.set(id, userWrites());
      else opened.delete(id);
    }
  }, true);
}

const TEXT = "input, textarea, select, [contenteditable='true']";

/** Is the user working inside the element with this data-testid? */
export function isHeld(testid: string): boolean {
  if (typeof document === "undefined") return false;
  const el = document.querySelector(`[data-testid="${CSS.escape(testid)}"]`);
  if (!el) return false;
  const active = document.activeElement;
  if (active && el.contains(active) && active.matches(TEXT)) return true;
  const now = userWrites();
  if (typed.get(testid) === now) return true;
  return opened.get(testid) === now && el.querySelector("[aria-expanded='true']") !== null;
}

/** The new list, with every held item of the old list kept at its old place (pure: tested alone). */
export function keepHeld<T>(prev: T[], next: T[], keyOf: (t: T) => string, held: (t: T) => boolean): T[] {
  const keep = prev.map((t, i) => ({ t, i, k: keyOf(t) })).filter(({ t }) => held(t));
  if (keep.length === 0) return next;
  const keys = new Set(keep.map((x) => x.k));
  const out = next.filter((t) => !keys.has(keyOf(t)));
  const fresh = new Map(next.map((t) => [keyOf(t), t]));
  for (const { t, i, k } of keep) out.splice(Math.min(i, out.length), 0, fresh.get(k) ?? t);
  return out;
}

const sameList = <T,>(a: T[], b: T[]) => a.length === b.length && a.every((t, i) => t === b[i]);

/**
 * `items` as a live list: when they change, an item the user is in (see the header) keeps its
 * place, and stays even if the new list dropped it, until the user finishes. `testids(item)`
 * names the elements that stand for the item on the page; any one of them held holds it.
 */
export function useKeepWhileEditing<T>(items: T[], keyOf: (t: T) => string, testids: (t: T) => string[]): T[] {
  useEffect(install, []);
  // React's "information from the previous render" pattern. The same items in a new array are no
  // change, so a caller that hands a fresh array on every render cannot loop.
  const [state, setState] = useState({ src: items, out: items });
  const same = sameList(state.src, items);
  if (same && state.out === state.src) return state.out;
  // Something is held (or the items moved): look again; state changes only when the result does.
  const out = keepHeld(state.out, items, keyOf, (t) => testids(t).some(isHeld));
  if (same && sameList(out, state.out)) return state.out;
  setState({ src: items, out });
  return out;
}
