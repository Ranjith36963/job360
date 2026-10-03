/**
 * The System / Light / Dark switch (redesign slice 1). It sits at the bottom
 * of the sidebar and in the signed-out top bar. next-themes is mocked: this
 * checks OUR part — three labelled buttons, the stored choice announced with
 * aria-pressed, and a click handing the choice to setTheme.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ThemeToggle } from "@/components/layout/ThemeToggle";

const setTheme = vi.fn();
let mockTheme: string | undefined = "system";

vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: mockTheme, setTheme }),
}));

beforeEach(() => {
  setTheme.mockClear();
  mockTheme = "system";
});

describe("ThemeToggle", () => {
  it("is a labelled group with System, Light and Dark buttons", () => {
    render(<ThemeToggle />);

    expect(screen.getByRole("group", { name: "Theme" })).toBeInTheDocument();
    for (const name of ["System", "Light", "Dark"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("announces the current choice with aria-pressed", () => {
    mockTheme = "dark";
    render(<ThemeToggle />);

    expect(screen.getByRole("button", { name: "Dark" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Light" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "System" })).toHaveAttribute("aria-pressed", "false");
  });

  it("treats an unset theme as System", () => {
    mockTheme = undefined;
    render(<ThemeToggle />);

    expect(screen.getByRole("button", { name: "System" })).toHaveAttribute("aria-pressed", "true");
  });

  it("hands the clicked choice to setTheme", () => {
    render(<ThemeToggle />);

    fireEvent.click(screen.getByRole("button", { name: "Light" }));
    expect(setTheme).toHaveBeenLastCalledWith("light");

    fireEvent.click(screen.getByRole("button", { name: "Dark" }));
    expect(setTheme).toHaveBeenLastCalledWith("dark");

    fireEvent.click(screen.getByRole("button", { name: "System" }));
    expect(setTheme).toHaveBeenLastCalledWith("system");
  });
});
