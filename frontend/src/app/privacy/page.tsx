import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Privacy Policy — Job360",
  description: "How Job360 handles your data.",
};

export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-3xl font-semibold">Privacy Policy</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Last updated: {new Date().getFullYear()}
      </p>

      <div className="mt-8 space-y-6 text-sm leading-relaxed text-muted-foreground">
        <section>
          <h2 className="text-lg font-medium text-foreground">What we store</h2>
          <p className="mt-2">
            Job360 stores the account details you provide (email), the profile
            data you upload (CV text, preferences, optional LinkedIn/GitHub
            data), and your activity within the app (saved jobs, applications,
            notification settings). Aggregated job listings come from public
            third-party sources.
          </p>
        </section>
        <section>
          <h2 className="text-lg font-medium text-foreground">How we use it</h2>
          <p className="mt-2">
            Your profile is used only to score and rank jobs for you and to
            deliver notifications you opt into. We do not sell your data.
          </p>
        </section>
        <section>
          <h2 className="text-lg font-medium text-foreground">Your control</h2>
          <p className="mt-2">
            You can edit your profile, manage notification channels, and delete
            your account at any time from Settings. Deleting your account removes
            your per-user data.
          </p>
        </section>
        <p className="text-xs">
          This is a starting policy and will be expanded before public launch.
        </p>
      </div>
    </main>
  );
}
