import { describe, expect, it } from "vitest";
import { foldPreview, looksLikePlaceholder, needsFold } from "./answer-text";

describe("looksLikePlaceholder", () => {
  it.each([
    ["Determinism and hallucinations", "drafted from profile facts"],
    ["Structured JSON outputs", "drafted"],
    ["LLM/agentic feature in production", "Calnex RAG + multi-agent assistant, metrics from CV"],
    ["Anything", "Draft"],
    ["Anything", "To be drafted"],
    ["Anything", "Will draft this later"],
    ["Anything", "see profile"],
    ["Anything", "based on profile"],
    ["Anything", "TBD"],
    ["Anything", "placeholder text"],
    ["Anything", "..."],
    ["Anything", "[...]"],
  ])("%s / %s is a placeholder", (q, a) => expect(looksLikePlaceholder(q, a)).toBe(true));

  it.each([
    ["Right to work in the UK?", "Yes"],
    ["Where are you based?", "London"],
    ["Phone", "+44 7700 900123"],
    ["How many years of Python?", "5 years"],
    ["How did you hear about us?", "LinkedIn"],
    ["Tell us your notice period", "1 month"],
    ["Anything", ""],
    ["Anything", "Drafting tools: Figma"],
    ["Why are you leaving?", "Relocating"],
    ["Describe your right to work", "UK citizen"],
  ])("%s / %s is a real answer", (q, a) => expect(looksLikePlaceholder(q, a)).toBe(false));

  it("a real 400-character essay is not a placeholder", () => {
    const essay = "I built a retrieval pipeline that cut support load by a third and shipped it to production. ".repeat(5);
    expect(essay.length).toBeGreaterThan(400);
    expect(looksLikePlaceholder("Why do you want to work here?", essay)).toBe(false);
  });
});

describe("fold", () => {
  it("folds past 280 chars or 4 lines only", () => {
    expect(needsFold("x".repeat(280))).toBe(false);
    expect(needsFold("x".repeat(281))).toBe(true);
    expect(needsFold("a\nb\nc\nd")).toBe(false);
    expect(needsFold("a\nb\nc\nd\ne")).toBe(true);
    expect(foldPreview("x".repeat(500))).toHaveLength(281);
  });
});
