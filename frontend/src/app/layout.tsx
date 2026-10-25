import type { Metadata } from "next";
import localFont from "next/font/local";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppShell } from "@/components/layout/AppShell";
import { AuthProvider } from "@/components/layout/AuthProvider";
import { ThemeProvider } from "@/components/layout/ThemeProvider";
import { QueryProvider } from "@/components/providers/QueryProvider";
import { PostHogProviderWrapper } from "@/components/providers/PostHogProviderWrapper";
import { ConsentBanner } from "@/components/consent/ConsentBanner";
import { ClientErrorReporter } from "@/components/ClientErrorReporter";
import { ThemedToaster } from "@/components/layout/ThemedToaster";
import "./globals.css";

// Geist = body (--font-sans), Geist Mono = numbers, dates and IDs only
// (--font-mono), Newsreader = the serif for headings (--font-heading).
// Self-hosted from ./fonts (OFL; copied from the `geist` and
// `@fontsource/newsreader` npm packages) so `npm run build` never downloads
// from Google Fonts. Same variables, weights, latin set and display as the
// old next/font/google set-up; the fallback fonts match it too (Arial for
// Geist and Geist Mono, Times New Roman for the serif).
const geist = localFont({
  src: "./fonts/Geist-Variable.woff2",
  variable: "--font-geist",
  weight: "100 900",
  display: "swap",
  adjustFontFallback: "Arial",
});

const geistMono = localFont({
  src: "./fonts/GeistMono-Variable.woff2",
  variable: "--font-geist-mono",
  weight: "100 900",
  display: "swap",
  adjustFontFallback: "Arial",
});

const newsreader = localFont({
  src: [
    { path: "./fonts/newsreader-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "./fonts/newsreader-latin-500-normal.woff2", weight: "500", style: "normal" },
  ],
  variable: "--font-newsreader",
  display: "swap",
  adjustFontFallback: "Times New Roman",
});

// C2 (application-spine review, VISION rule 4) — Job360 never sources or
// ranks jobs; the meta/OpenGraph/Twitter copy used to advertise a source
// count and a multi-factor score on every page. Mission copy instead.
// Owner-approved CATEGORY LINE (2026-09-28) — the ONLY positioning sentence
// in use; do not invent another. Same line as the landing headline
// (Landing.tsx), the MCP server's own description/INSTRUCTIONS
// (backend/src/api/mcp_server.py) and the Connect page intro
// (settings/connect/page.tsx).
const TITLE = "Job360 — the job tracker your AI assistant fills in for you";
const TAGLINE =
  "The job tracker your AI assistant fills in for you — every CV version, every reply, every receipt.";

export const metadata: Metadata = {
  title: TITLE,
  description: TAGLINE,
  openGraph: {
    title: TITLE,
    description: TAGLINE,
    type: "website",
    siteName: "Job360",
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: TAGLINE,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geist.variable} ${geistMono.variable} ${newsreader.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <ClientErrorReporter />
        <PostHogProviderWrapper>
        <ThemeProvider>
          <QueryProvider>
            <AuthProvider>
              <TooltipProvider>
                <AppShell>{children}</AppShell>
                {/* Analytics consent gate (fable/05 C3) — PostHog stays off
                    until the user accepts here. */}
                <ConsentBanner />
              </TooltipProvider>
              <ThemedToaster />
            </AuthProvider>
          </QueryProvider>
        </ThemeProvider>
        </PostHogProviderWrapper>
      </body>
    </html>
  );
}
