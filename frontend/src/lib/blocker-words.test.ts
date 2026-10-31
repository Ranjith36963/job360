import { describe, expect, it } from "vitest";
import { actorWord, nextLine } from "./blocker-words";

describe("actorWord", () => {
  it.each([
    ["you", "You"], ["your_pc", "Your PC"], ["assistant", "Assistant"], ["job360", "Job360"], ["employer", "Employer"],
  ] as const)("%s -> %s", (a, w) => expect(actorWord(a)).toBe(w));
});

describe("nextLine", () => {
  it("adds the count of the rest", () => {
    expect(nextLine({ actor: "you", label: "solve the CAPTCHA" }, 3)).toBe("You: solve the CAPTCHA +3 more");
  });
  it("has no suffix when nothing else waits", () => {
    expect(nextLine({ actor: "assistant", label: "write the CV" }, 0)).toBe("Assistant: write the CV");
    expect(nextLine({ actor: "assistant", label: "write the CV" })).toBe("Assistant: write the CV");
  });
  it("is empty without a blocker", () => {
    expect(nextLine(null, 2)).toBe("");
    expect(nextLine(undefined)).toBe("");
  });
});
