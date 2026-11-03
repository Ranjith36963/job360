import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { BackBar } from "./BackBar";

describe("BackBar", () => {
  it("from Needs you: back link, chip label and breadcrumb", () => {
    render(<BackBar applicationId={90} from="needs-you" show="site_step" company="Trackmind" title="Junior AI Engineer" />);
    const back = screen.getByTestId("back-link");
    expect(back).toHaveTextContent("Back to Needs you · On the site");
    expect(back).toHaveAttribute("href", "/needs-you?show=site_step&job=90");
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(crumbs).toHaveTextContent("Needs you");
    expect(within(crumbs).getByRole("link", { name: "On the site" })).toHaveAttribute("href", "/needs-you?show=site_step&job=90");
    expect(within(crumbs).getByText("Trackmind — Junior AI Engineer")).toHaveAttribute("aria-current", "page");
  });
  it("opened directly or with a bad from: All applications", () => {
    render(<BackBar applicationId={1} from="https://evil.example" show="x" company="A<b>" title="T" />);
    expect(screen.getByTestId("back-link")).toHaveTextContent("All applications");
    expect(screen.getByTestId("back-link")).toHaveAttribute("href", "/applications");
    expect(within(screen.getByRole("navigation", { name: "Breadcrumb" })).getByRole("link", { name: "Applications" })).toBeInTheDocument();
    expect(screen.getByText("A<b> — T")).toBeInTheDocument(); // text, not markup
  });
});
