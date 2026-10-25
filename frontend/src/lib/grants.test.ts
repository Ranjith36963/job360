import { describe, it, expect } from "vitest";
import { groupGrants } from "./grants";

const g = (id: number, name: string, created_at: string) => ({
  id,
  client_name: name,
  created_at,
});

describe("groupGrants", () => {
  it("groups the same client, newest first, with an older list", () => {
    const out = groupGrants([
      g(1, "Claude", "2026-01-01T00:00:00Z"),
      g(3, "Claude", "2026-03-01T00:00:00Z"),
      g(2, "Claude", "2026-02-01T00:00:00Z"),
    ]);
    expect(out).toHaveLength(1);
    expect(out[0].newest.id).toBe(3);
    expect(out[0].older.map((x) => x.id)).toEqual([2, 1]);
    expect(out[0].key).toBe("claude");
  });

  it("sorts apps by their newest grant and keeps names as given", () => {
    const out = groupGrants([
      g(1, "ChatGPT", "2026-01-01T00:00:00Z"),
      g(2, "Other App", "2026-05-01T00:00:00Z"),
      g(3, "chatgpt ", "2026-02-01T00:00:00Z"),
    ]);
    expect(out.map((x) => x.name)).toEqual(["Other App", "chatgpt "]);
    expect(out[1].older.map((x) => x.id)).toEqual([1]);
  });

  it("returns an empty list for no grants", () => {
    expect(groupGrants([])).toEqual([]);
  });
});
