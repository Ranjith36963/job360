import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Contact — Job360",
  description: "Get in touch with Job360.",
};

export default function ContactPage() {
  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-3xl font-semibold">Contact</h1>

      <div className="mt-8 space-y-6 text-sm leading-relaxed text-muted-foreground">
        <p>
          Questions, feedback, or found a bug? We&apos;d like to hear from you.
        </p>
        <section>
          <h2 className="text-lg font-medium text-foreground">Email</h2>
          <p className="mt-2">
            <a
              href="mailto:ranjithmaligaguruprakash@gmail.com"
              className="text-primary hover:underline"
            >
              ranjithmaligaguruprakash@gmail.com
            </a>
          </p>
        </section>
        <section>
          <h2 className="text-lg font-medium text-foreground">GitHub</h2>
          <p className="mt-2">
            <a
              href="https://github.com/Ranjith36963/job360"
              className="text-primary hover:underline"
              target="_blank"
              rel="noopener noreferrer"
            >
              github.com/Ranjith36963/job360
            </a>{" "}
            — open an issue for bugs or feature requests.
          </p>
        </section>
      </div>
    </main>
  );
}
