import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ConsentClient } from "./ConsentClient";

// ---------------------------------------------------------------------------
// OAuth consent screen (spec R4, R9, S9).
//
// Server component shell: awaits `params` (Next.js 16 — sync access to
// `params` was removed, see frontend/AGENTS.md) and hands the plain `rid`
// string to a client child, which does the actual fetch/decision round trip.
// Same server-page + client-child split as src/app/jobs/[id]/page.tsx.
//
// This route is protected: middleware.ts bounces an anonymous visitor to
// /login?next=/oauth/consent/<rid> before this component ever renders.
// ---------------------------------------------------------------------------

export default async function OAuthConsentPage({
  params,
}: {
  params: Promise<{ rid: string }>;
}) {
  const { rid } = await params;

  return (
    <div className="mx-auto max-w-md py-16">
      <Card>
        <CardHeader>
          <CardTitle>Connect to Job360</CardTitle>
          <CardDescription>
            Review what this app can do before you allow it.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ConsentClient rid={rid} />
        </CardContent>
      </Card>
    </div>
  );
}
