/** Eyebrow, lede and one sub line. Shown above Memory & instructions;
 *  the profile page keeps its own header. */
export function ProfileHeader() {
  return (
    <header className="mb-6">
      <p className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">Memory &amp; instructions</p>
      <h1 className="font-heading text-4xl font-normal tracking-tight">
        What your assistants <em className="not-italic text-brand">know</em> about you.
      </h1>
      <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
        They read this before every form: first how you want them to work, then your facts. Nothing here is guessed. Empty rows stay empty until you or an assistant fills them.
      </p>
    </header>
  );
}
