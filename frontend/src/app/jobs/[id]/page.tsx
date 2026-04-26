import type { Metadata } from "next";
import type { JobResponse } from "@/lib/types";
import { safeUrl } from "@/lib/utils";
import { JobDetailClient } from "./JobDetailClient";

// ---------------------------------------------------------------------------
// Server-side data fetch (used by both generateMetadata and the page)
// ---------------------------------------------------------------------------

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetchJob(id: string): Promise<JobResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/api/jobs/${id}`, {
      // Revalidate every 5 minutes — job data changes infrequently
      next: { revalidate: 300 },
    });
    if (!res.ok) return null;
    return res.json() as Promise<JobResponse>;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// generateMetadata — runs server-side before the page renders
// ---------------------------------------------------------------------------

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  const job = await fetchJob(id);

  if (!job) {
    return {
      title: "Job not found — Job360",
      description: "This job listing could not be found.",
    };
  }

  const title = `${job.title} at ${job.company} — Job360`;
  const description = [
    `${job.title} at ${job.company}`,
    job.location ? `in ${job.location}` : null,
    job.salary ? `· ${job.salary}` : null,
    `· Match score ${job.match_score}/100`,
  ]
    .filter(Boolean)
    .join(" ");

  return {
    title,
    description,
    openGraph: {
      title,
      description,
      type: "website",
      siteName: "Job360",
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
    },
  };
}

// ---------------------------------------------------------------------------
// Page — server component shell
// ---------------------------------------------------------------------------

export default async function JobDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const jobId = Number(id);

  // Fetch job server-side so we can embed the JSON-LD schema for Google
  const job = await fetchJob(id);

  const jsonLd = job
    ? {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        title: job.title,
        description: `${job.title} at ${job.company}`,
        hiringOrganization: {
          "@type": "Organization",
          name: job.company,
        },
        jobLocation: {
          "@type": "Place",
          address: { addressLocality: job.location },
        },
        datePosted: job.posted_at ?? job.date_found,
        employmentType: job.employment_type ?? "FULL_TIME",
        url: safeUrl(job.apply_url),
      }
    : null;

  return (
    <>
      {jsonLd && (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            // R-1: escape < to prevent </script> breakout from scraper-derived strings
            __html: JSON.stringify(jsonLd).replace(/</g, "\\u003c"),
          }}
        />
      )}
      <JobDetailClient jobId={jobId} />
    </>
  );
}
