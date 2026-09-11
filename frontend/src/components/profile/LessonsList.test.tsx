import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { LessonsList } from "./LessonsList";
import type { Lesson } from "@/lib/api";

const listLessons = vi.fn();
vi.mock("@/lib/api", () => ({
  listLessons: (...args: unknown[]) => listLessons(...args),
}));

function lesson(overrides: Partial<Lesson> = {}): Lesson {
  return {
    event_id: 1,
    application_id: 8282,
    job_title: "Platform Engineer",
    job_company: "Northwind",
    detail: "Ask about sponsorship first.",
    occurred_at: "2026-09-10T00:00:00Z",
    recorded_by: "user",
    ...overrides,
  };
}

describe("LessonsList", () => {
  beforeEach(() => listLessons.mockReset());

  it("renders two mocked lessons as two lesson-items with detail text and a lesson-link href", async () => {
    listLessons.mockResolvedValue({
      lessons: [
        lesson({ event_id: 1, application_id: 8282, detail: "Ask about sponsorship first." }),
        lesson({ event_id: 2, application_id: 9191, detail: "Mention the Kubernetes cert." }),
      ],
      total: 2,
    });

    render(<LessonsList />);

    const items = await screen.findAllByTestId("lesson-item");
    expect(items).toHaveLength(2);
    expect(screen.getByText("Ask about sponsorship first.")).toBeInTheDocument();
    expect(screen.getByText("Mention the Kubernetes cert.")).toBeInTheDocument();

    const links = screen.getAllByTestId("lesson-link");
    expect(links[0]).toHaveAttribute("href", "/applications/8282");
    expect(links[1]).toHaveAttribute("href", "/applications/9191");
  });

  it("shows the empty-state line and no lesson-item when the list is empty", async () => {
    listLessons.mockResolvedValue({ lessons: [], total: 0 });

    render(<LessonsList />);

    await waitFor(() =>
      expect(
        screen.getByText("No lessons yet. Flag one from any application's timeline.")
      ).toBeInTheDocument()
    );
    expect(screen.queryByTestId("lesson-item")).toBeNull();
  });

  it("shows the error line when the fetch rejects", async () => {
    listLessons.mockRejectedValueOnce(new Error("network error"));

    render(<LessonsList />);

    await waitFor(() =>
      expect(screen.getByText("Could not load lessons.")).toBeInTheDocument()
    );
  });
});
