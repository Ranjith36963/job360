import { describe, it, expect, vi } from "vitest";

vi.mock("next/navigation", () => ({
  permanentRedirect: (url: string) => {
    throw new Error(`REDIRECT:${url}`);
  },
}));

import OldSettings from "../settings/page";
import OldSettingsCatchAll from "../settings/[...rest]/page";
import OldPeople from "../people/[[...rest]]/page";

type SP = Record<string, string | string[] | undefined>;

async function target(fn: () => Promise<unknown>): Promise<string | null> {
  try {
    await fn();
    return null;
  } catch (e) {
    return (e as Error).message.replace("REDIRECT:", "");
  }
}
const sp = (s: SP = {}) => ({ searchParams: Promise.resolve(s) });

const rest = (...r: string[]) => ({ params: Promise.resolve({ rest: r }) });

describe("old settings URLs", () => {
  it("/settings goes to /account", async () => {
    expect(await target(() => OldSettings(sp()))).toBe("/account");
  });
  it("/settings/account and any other /settings/* go to /account, query kept", async () => {
    expect(await target(() => OldSettingsCatchAll({ ...rest("account"), ...sp() }))).toBe("/account");
    expect(await target(() => OldSettingsCatchAll({ ...rest("whatever", "x"), ...sp({ x: "1" }) }))).toBe("/account?x=1");
  });
  it("/settings/connect goes to /connect, keeping ?tab= and the rest", async () => {
    expect(await target(() => OldSettingsCatchAll({ ...rest("connect"), ...sp() }))).toBe("/connect");
    expect(await target(() => OldSettingsCatchAll({ ...rest("connect"), ...sp({ tab: "schedules", ref: "a" }) }))).toBe(
      "/connect?tab=schedules&ref=a"
    );
  });
  it("/settings/job-sites goes to the Job sites tab of /connect, keeping the query", async () => {
    expect(await target(() => OldSettingsCatchAll({ ...rest("job-sites"), ...sp() }))).toBe("/connect?tab=job-sites");
    expect(await target(() => OldSettingsCatchAll({ ...rest("job-sites"), ...sp({ a: ["1", "2"] }) }))).toBe(
      "/connect?a=1&a=2&tab=job-sites"
    );
  });
});

describe("old /people URLs", () => {
  it("go to /outreach/people with subpath and query kept", async () => {
    expect(await target(() => OldPeople({ params: Promise.resolve({}), ...sp() }))).toBe("/outreach/people");
    expect(
      await target(() => OldPeople({ params: Promise.resolve({ rest: ["7", "edit"] }), ...sp({ q: "x" }) }))
    ).toBe("/outreach/people/7/edit?q=x");
  });
});
