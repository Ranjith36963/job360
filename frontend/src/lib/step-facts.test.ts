import { describe, expect, it } from "vitest";
import { factRoom, factsFor, withFact, withoutFact } from "./step-facts";

const notes = ["Never apply to agencies.", "About you: I go by Alex.", "Logistics: No travel in August.", "About you: Pronouns they/them."];

describe("step facts", () => {
  it("reads only the step's own facts, without the step name", () => {
    expect(factsFor(notes, "About you")).toEqual(["I go by Alex.", "Pronouns they/them."]);
    expect(factsFor(notes, "Equality")).toEqual([]);
  });

  it("adds a fact at the end with the step name in front; blank adds nothing", () => {
    expect(withFact(notes, "Equality", "  Ask   me first ")).toEqual([...notes, "Equality: Ask me first"]);
    expect(withFact(notes, "Equality", "   ")).toBe(notes);
  });

  it("removes the nth fact of that step only; general notes stay", () => {
    expect(withoutFact(notes, "About you", 1)).toEqual(["Never apply to agencies.", "About you: I go by Alex.", "Logistics: No travel in August."]);
  });

  it("leaves room for the step name inside the 200-character note cap", () => {
    expect(factRoom("About you")).toBe(200 - "About you: ".length);
  });
});
