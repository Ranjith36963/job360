import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@/components/connect/AssistantInstructions", () => ({
  AssistantInstructions: () => <div data-testid="assistant-instructions" />,
}));
vi.mock("@/components/profile/MemoryTab", () => ({ MemoryTab: () => <div data-testid="memory-tab" /> }));

import ProfileMemoryPage from "./page";

describe("Memory & instructions page", () => {
  it("shows the assistant's instructions first, then the facts", () => {
    render(<ProfileMemoryPage />);
    const instructions = screen.getByTestId("assistant-instructions");
    const facts = screen.getByTestId("memory-tab");
    expect(instructions.compareDocumentPosition(facts) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("Memory & instructions")).toBeInTheDocument();
  });
});
