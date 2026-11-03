import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SEND_EXPIRED_LABEL, SEND_EXPIRED_NOTE } from "@/lib/blocker-words";
import { JobRowSummary } from "./JobRowSummary";
import type { JobHeader } from "@/lib/needs-you-items";
import type { RowFacts } from "@/lib/job-row";

const h = { id: 9, company: "Acme", role: "Dev", cv: null, ready: null, asks: [] } as unknown as JobHeader;
const facts = { location: "", score: null } as unknown as RowFacts;
const show = (nextText: string) => render(<JobRowSummary h={h} card={null} facts={facts} nextText={nextText} linkTestid="t" />);

describe("JobRowSummary expired Send", () => {
  it("adds one muted line under the expired-Send label", () => {
    show(`You: ${SEND_EXPIRED_LABEL}`);
    expect(screen.getByTestId("send-expired-note-9")).toHaveTextContent(SEND_EXPIRED_NOTE);
    expect(SEND_EXPIRED_NOTE).toBe("Your Send lasts 7 days; your assistant didn't submit in time.");
  });
  it("shows no note for any other line", () => {
    show("You: Send");
    expect(screen.queryByTestId("send-expired-note-9")).toBeNull();
  });
});
