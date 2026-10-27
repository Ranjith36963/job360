// Counts the user's own saves (every successful non-GET call of the API client). A live refresh
// keeps a card the user is working in (lib/live-hold.ts) only until their next save: that save is
// "finished". No "use client": the API client imports this on the server too, where it is a no-op.

let writes = 0;

export function noteUserWrite(): void {
  writes++;
}

export function userWrites(): number {
  return writes;
}
