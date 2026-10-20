"use client";

import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

import {
  browserLive,
  forgetBrowserSignIn,
  getBrowserSignIns,
  type BrowserLiveAction,
  type BrowserLiveShot,
  type BrowserSignIns,
} from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format-date";

const KEYS = ["Enter", "Tab", "Backspace", "Escape"] as const;
const REFRESH_MS = 1500;

/**
 * Job sites — sign in once for Job360's cloud browser (docs/product/BROWSER_AGENT.md, slice 2).
 *
 * The user drives a live picture of their own cloud browser: click on the picture, type, press
 * keys. They type their password into the SITE themselves; "Keep me signed in" saves only what the
 * site remembers (its cookies), encrypted. Job360 never stores or shows what was typed, and an
 * assistant can never reach this page's endpoints (a browser session only, never a token).
 */
export default function JobSitesPage() {
  const [data, setData] = useState<BrowserSignIns | null>(null);
  const [url, setUrl] = useState("");
  const [shot, setShot] = useState<BrowserLiveShot | null>(null);
  const [text, setText] = useState("");
  const [hide, setHide] = useState(true);
  const [busy, setBusy] = useState(false);
  const [kept, setKept] = useState(false);
  const busyRef = useRef(false);

  const load = useCallback(async () => {
    try {
      setData(await getBrowserSignIns());
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not load your job sites."));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const step = useCallback(async (s: BrowserLiveAction, quiet = false): Promise<BrowserLiveShot | null> => {
    if (busyRef.current) return null;
    busyRef.current = true;
    if (!quiet) setBusy(true);
    try {
      const res = await browserLive(s);
      if (res.status === "ok" && res.image) setShot(res);
      else if (quiet && res.reason === "no_browser") {
        // The view closed (idle or past its lifetime): stop refreshing a picture that is gone.
        setShot(null);
        toast.error("The sign-in window closed; press Start again.");
      } else if (res.status !== "ok" && !quiet) toast.error(res.detail || "The browser did not answer.");
      return res;
    } catch (err) {
      if (!quiet) toast.error(apiErrorMessage(err, "The browser did not answer."));
      return null;
    } finally {
      busyRef.current = false;
      if (!quiet) setBusy(false);
    }
  }, []);

  // Keep the picture fresh while a sign-in is open (pages change on their own: redirects, codes):
  // not once it is kept, and not while this tab is hidden.
  useEffect(() => {
    if (!shot || kept) return;
    const id = window.setInterval(() => {
      if (document.visibilityState !== "hidden") void step({ action: "shot" }, true);
    }, REFRESH_MS);
    return () => window.clearInterval(id);
  }, [shot, kept, step]);

  async function start() {
    const target = url.trim();
    if (!target) return;
    setKept(false);
    const res = await step({ action: "start", url: /^https?:\/\//i.test(target) ? target : `https://${target}` });
    if (res && res.status !== "ok") setShot(null);
  }

  function clickPicture(e: MouseEvent<HTMLButtonElement>) {
    // A keyboard "click" has no position (detail 0): it would click the page's top-left corner.
    if (!shot?.width || !shot?.height || e.detail === 0) return;
    const box = e.currentTarget.getBoundingClientRect();
    const x = ((e.clientX - box.left) / box.width) * shot.width;
    const y = ((e.clientY - box.top) / box.height) * shot.height;
    void step({ action: "click", x: Math.round(x), y: Math.round(y) });
  }

  async function send() {
    if (!text) return;
    const typed = text;
    setText("");
    await step({ action: "type", text: typed });
  }

  async function keep() {
    const res = await step({ action: "keep" });
    if (res?.status === "ok" && res.kept) {
      setKept(true);
      const sites = Object.keys(res.kept);
      toast.success(`Kept you signed in to ${sites.join(", ")}.`);
      await load();
    }
  }

  async function cancel() {
    await step({ action: "cancel" });
    setShot(null);
  }

  async function forget(host: string) {
    try {
      await forgetBrowserSignIn(host);
      toast.success(`Forgot ${host}.`);
      await load();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not forget that site."));
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Sign in once for Job360&apos;s browser</CardTitle>
          <CardDescription>
            Job360&apos;s cloud browser can apply for you while your computer is off. Some job sites need you
            signed in. Sign in here once: you type your password into the site yourself. Job360 keeps only the
            site&apos;s &quot;stay signed in&quot; cookie, encrypted, and never sees or stores your password.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {data && !data.enabled && (
            <p className="text-sm text-muted-foreground" data-testid="browser-off">
              Job360&apos;s browser is not turned on yet. Until it is, your assistant uses its own browser.
            </p>
          )}
          {data?.enabled && !shot && (
            <form
              className="flex flex-col gap-2 sm:flex-row sm:items-end"
              onSubmit={(e) => {
                e.preventDefault();
                void start();
              }}
            >
              <div className="flex-1 space-y-1">
                <Label htmlFor="site-url">The job site&apos;s sign-in page</Label>
                <Input
                  id="site-url"
                  placeholder="https://acme.wd5.myworkdayjobs.com/en-GB/careers/login"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                />
              </div>
              <Button type="submit" disabled={busy || !url.trim()}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : "Start"}
              </Button>
            </form>
          )}
          {shot && (
            <div className="space-y-3" data-testid="live-view">
              <p className="truncate text-xs text-muted-foreground">{shot.url}</p>
              <button
                type="button"
                className="block w-full cursor-pointer p-0"
                aria-label={`The cloud browser showing ${shot.title || "the job site"}. Click where you would click.`}
                onClick={clickPicture}
              >
                {/* eslint-disable-next-line @next/next/no-img-element -- a live data: URL, not a static asset */}
                <img src={shot.image} alt="" className="w-full rounded-md border border-border" />
              </button>
              <form
                className="flex flex-col gap-2 sm:flex-row sm:items-end"
                onSubmit={(e) => {
                  e.preventDefault();
                  void send();
                }}
              >
                <div className="flex-1 space-y-1">
                  <Label htmlFor="live-type">Type into the box you clicked</Label>
                  <Input
                    id="live-type"
                    type={hide ? "password" : "text"}
                    autoComplete="off"
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                  />
                </div>
                <Button type="submit" disabled={busy || !text}>
                  Send
                </Button>
                <Button type="button" variant="outline" onClick={() => setHide((h) => !h)}>
                  {hide ? "Show" : "Hide"}
                </Button>
              </form>
              <div className="flex flex-wrap gap-2">
                {KEYS.map((k) => (
                  <Button key={k} type="button" variant="outline" size="sm" disabled={busy}
                    onClick={() => void step({ action: "key", key: k })}>
                    {k}
                  </Button>
                ))}
                <Button type="button" variant="outline" size="sm" disabled={busy}
                  onClick={() => void step({ action: "scroll", dy: -500 })}>
                  Scroll up
                </Button>
                <Button type="button" variant="outline" size="sm" disabled={busy}
                  onClick={() => void step({ action: "scroll", dy: 500 })}>
                  Scroll down
                </Button>
              </div>
              <div className="flex gap-2">
                <Button type="button" onClick={() => void keep()} disabled={busy}>
                  I&apos;m signed in: keep me signed in
                </Button>
                <Button type="button" variant="ghost" onClick={() => void cancel()} disabled={busy}>
                  Cancel
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Sites you are signed in to</CardTitle>
          <CardDescription>Job360&apos;s browser starts already signed in to these.</CardDescription>
        </CardHeader>
        <CardContent>
          {data && data.sites.length === 0 && (
            <p className="text-sm text-muted-foreground">None yet.</p>
          )}
          <ul className="divide-y divide-border">
            {data?.sites.map((s) => (
              <li key={s.site_host} className="flex items-center justify-between py-2">
                <div>
                  <p className="text-sm font-medium">{s.site_host}</p>
                  <p className="text-xs text-muted-foreground">Kept {formatDateTime(s.updated_at)}</p>
                </div>
                <Button type="button" variant="outline" size="sm" onClick={() => void forget(s.site_host)}>
                  Forget
                </Button>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
