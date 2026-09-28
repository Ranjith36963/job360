import type { Metadata } from "next";
import { Sora, Plus_Jakarta_Sans, JetBrains_Mono } from "next/font/google";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Navbar } from "@/components/layout/Navbar";
import { Footer } from "@/components/layout/Footer";
import { FloatingIcons } from "@/components/layout/FloatingIcons";
import { AuthProvider } from "@/components/layout/AuthProvider";
import { ThemeProvider } from "@/components/layout/ThemeProvider";
import { QueryProvider } from "@/components/providers/QueryProvider";
import { PostHogProviderWrapper } from "@/components/providers/PostHogProviderWrapper";
import { ConsentBanner } from "@/components/consent/ConsentBanner";
import { ClientErrorReporter } from "@/components/ClientErrorReporter";
import { Toaster } from "sonner";
import "./globals.css";

const sora = Sora({
  variable: "--font-sora",
  subsets: ["latin"],
  display: "swap",
});

const plusJakarta = Plus_Jakarta_Sans({
  variable: "--font-plus-jakarta",
  subsets: ["latin"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains",
  subsets: ["latin"],
  display: "swap",
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
      className={`dark ${sora.variable} ${plusJakarta.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <ClientErrorReporter />
        <PostHogProviderWrapper>
        <ThemeProvider>
          <QueryProvider>
            <AuthProvider>
              <TooltipProvider>
                <FloatingIcons />
                <Navbar />
                <main className="flex-1">{children}</main>
                <Footer />
                {/* Analytics consent gate (fable/05 C3) — PostHog stays off
                    until the user accepts here. */}
                <ConsentBanner />
              </TooltipProvider>
              <Toaster position="bottom-right" richColors />
            </AuthProvider>
          </QueryProvider>
        </ThemeProvider>
        </PostHogProviderWrapper>
      </body>
    </html>
  );
}
