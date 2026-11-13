import { getNeedsYouCount } from "@/lib/api";
import { createSharedRead } from "@/lib/shared-read";

// The one shared reader of GET /api/needs-you-count. The nav badge, Home and the batch queue card all
// read it here, so a page load (key = the pathname) or a live change (key = `live:<seq>`) makes ONE call,
// even when a consumer asks after the first answer arrived (see shared-read.ts).
// A caller that needs a fresh number after its own action passes a key nobody else uses.
export const fetchNeedsYouCountShared = createSharedRead(() => getNeedsYouCount());

let uniqueSeq = 0;
/** A key no other caller shares: a read that must be fresh (after the caller's own action). */
export const freshCountKey = (): string => `fresh:${++uniqueSeq}`;
