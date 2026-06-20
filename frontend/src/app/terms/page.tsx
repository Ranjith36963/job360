import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Terms of Service — Job360",
  description: "The terms for using Job360.",
};

export default function TermsPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-3xl font-semibold">Terms of Service</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Last updated: {new Date().getFullYear()}
      </p>

      <div className="mt-8 space-y-6 text-sm leading-relaxed text-muted-foreground">
        <section>
          <h2 className="text-lg font-medium text-foreground">Using Job360</h2>
          <p className="mt-2">
            Job360 aggregates and scores job listings from public sources to help
            you find roles that match your profile. The service is provided
            as-is; job listings originate from third parties and accuracy or
            availability is not guaranteed.
          </p>
        </section>
        <section>
          <h2 className="text-lg font-medium text-foreground">Your account</h2>
          <p className="mt-2">
            You are responsible for keeping your login credentials secure and for
            the content you upload. Do not misuse the service or attempt to
            disrupt it.
          </p>
        </section>
        <section>
          <h2 className="text-lg font-medium text-foreground">Changes</h2>
          <p className="mt-2">
            These terms may be updated as the product evolves. Continued use
            after changes means you accept the updated terms.
          </p>
        </section>
        <p className="text-xs">
          These are starting terms and will be expanded before public launch.
        </p>
      </div>
    </main>
  );
}
