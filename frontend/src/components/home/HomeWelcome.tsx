"use client";

import {
  AddressStepCard,
  AssistantStepsCard,
  SayHelloCard,
} from "@/components/connect/ConnectSteps";

/**
 * A brand-new user (no applications, no events): no sentence, no ledger, no
 * right pane — just the heading and the same three connect steps that live on
 * /settings/connect, shared from one component so the copy cannot drift.
 */
export function HomeWelcome() {
  return (
    <div data-testid="home-welcome" className="flex flex-col gap-6">
      <div className="max-w-3xl">
        <AddressStepCard />
      </div>
      <AssistantStepsCard />
      <div className="max-w-3xl">
        <SayHelloCard />
      </div>
    </div>
  );
}
