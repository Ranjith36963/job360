import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { AnswerText } from "./AnswerText";

describe("AnswerText", () => {
  it("shows the full address as stored, as a new-tab link, and marks it opened after a click", () => {
    const { container } = render(<AnswerText value="linkedin.com/in/ranjith-ai-engineer" />);
    const link = screen.getByTestId("answer-link");
    expect(link).toHaveAttribute("href", "https://linkedin.com/in/ranjith-ai-engineer");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link.textContent).toBe("linkedin.com/in/ranjith-ai-engineer");
    expect(container.querySelectorAll("wbr")).toHaveLength(2); // may wrap after each "/"
    expect(screen.queryByTestId("answer-link-opened")).toBeNull();
    fireEvent.click(link);
    expect(screen.getByTestId("answer-link-opened")).toHaveTextContent("opened");
  });

  it("keeps javascript: and plain answers as text", () => {
    const { container } = render(
      <>
        <AnswerText value="javascript:alert(1)" />
        <AnswerText value="Yes" />
      </>,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toBe("javascript:alert(1)Yes");
  });
});
