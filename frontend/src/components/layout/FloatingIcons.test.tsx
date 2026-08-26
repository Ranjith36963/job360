/**
 * The decorative icon layer must paint BEHIND page content.
 *
 * WHAT WENT WRONG. FloatingIcons is `fixed inset-0` — which makes it a
 * POSITIONED element — and it carried `z-0`. Positioned elements paint above
 * non-positioned ones even at z-index 0, so on any page whose content sits in
 * a plain static container the decoration landed on top of the content. On
 * /register at 3x, background icons were clearly legible INSIDE the email and
 * password inputs. The landing and profile pages escaped only because their
 * wrappers happen to carry `relative` — a coincidence, not a guard.
 *
 * This test pins the negative z-index so the layer cannot drift back in front
 * of forms. It is deliberately a class assertion: jsdom does not resolve
 * Tailwind utilities into computed styles, and the real proof is a screenshot
 * (tests/design/design-pass.mjs), not this file. What this catches is somebody
 * "tidying" -z-10 back to z-0.
 */

import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { FloatingIcons } from "./FloatingIcons";

describe("FloatingIcons — decoration stays behind content", () => {
  it("renders the layer with a negative z-index and no z-0", () => {
    const { container } = render(<FloatingIcons />);
    const layer = container.firstElementChild as HTMLElement;

    expect(layer).toBeTruthy();
    expect(layer.className).toContain("-z-10");
    // `z-0` would put a positioned layer back in front of static content.
    expect(layer.className).not.toMatch(/(^|\s)z-0(\s|$)/);
  });

  it("stays invisible to assistive tech and to pointer input", () => {
    const { container } = render(<FloatingIcons />);
    const layer = container.firstElementChild as HTMLElement;

    expect(layer.getAttribute("aria-hidden")).toBe("true");
    expect(layer.className).toContain("pointer-events-none");
  });
});
