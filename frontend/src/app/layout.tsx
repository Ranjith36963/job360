import type { Metadata } from "next";
// Catalog numbers shown to users live in one place; the copy said 47 for a
// week after the registry dropped to 41. See src/lib/catalog.ts.
import { SOURCE_COUNT, SCORING_DIMENSIONS } from "@/lib/catalog";
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

export const metadata: Metadata = {
  title: "Job360 — Your Career Command Center",
  description:
    `${SOURCE_COUNT} sources. ${SCORING_DIMENSIONS}D scoring. One dashboard. Upload your CV and let Job360 find your perfect match.`,
  openGraph: {
    title: "Job360 — Your Career Command Center",
    description: `${SOURCE_COUNT} sources. ${SCORING_DIMENSIONS}D scoring. One dashboard.`,
    type: "website",
    siteName: "Job360",
  },
  twitter: {
    card: "summary_large_image",
    title: "Job360 — Your Career Command Center",
    description: `${SOURCE_COUNT} sources. ${SCORING_DIMENSIONS}D scoring. One dashboard.`,
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
