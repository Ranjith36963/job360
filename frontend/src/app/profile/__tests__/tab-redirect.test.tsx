import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";

const replace = vi.fn();
let search = "";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace }),
  useSearchParams: () => new URLSearchParams(search),
  usePathname: () => "/profile",
}));

import ProfilePage from "../page";

beforeEach(() => replace.mockClear());

describe("old /profile?tab= links", () => {
  it.each([
    ["tab=memory", "/profile/memory"],
    ["tab=setup", "/profile/memory"],
    ["tab=assistant", "/connect?tab=assistant"],
    ["tab=memory&x=1", "/profile/memory?x=1"],
    ["tab=assistant&x=1", "/connect?tab=assistant&x=1"],
    ["tab=cv", "/profile"],
    ["tab=bogus", "/profile"],
  ])("?%s goes to %s", (qs, to) => {
    search = qs;
    render(<ProfilePage />);
    expect(replace).toHaveBeenCalledWith(to);
  });
});
