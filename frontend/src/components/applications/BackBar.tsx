import { Fragment } from "react";
import Link from "next/link";
import { ArrowLeft, ChevronRight } from "lucide-react";
import { backTarget } from "@/lib/back-link";

type Param = string | string[] | undefined;

/** The back link and the breadcrumb at the top of an application page. Where the user came from rides in
 *  the link (?from=…&show=…, allow-listed by backTarget); anything else reads "All applications".
 *  Company and title are the job's own text: plain React text only. */
export function BackBar({ applicationId, from, show, company, title }: {
  applicationId: number;
  from?: Param;
  show?: Param;
  company: string;
  title: string;
}) {
  const back = backTarget(applicationId, { from, show });
  const job = [company, title].filter(Boolean).join(" — ") || "This job";
  return (
    <div className="flex flex-col gap-1">
      <Link href={back.href} data-testid="back-link"
        className="flex items-center gap-1.5 self-start py-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft aria-hidden className="h-4 w-4" /> {back.label}
      </Link>
      <nav aria-label="Breadcrumb" data-testid="breadcrumb">
        <ol className="flex flex-wrap items-center gap-x-1 text-xs text-muted-foreground">
          {back.trail.map((t) => (
            <Fragment key={t.href + t.name}>
              <li><Link href={t.href} className="underline-offset-2 hover:text-foreground hover:underline">{t.name}</Link></li>
              <li aria-hidden><ChevronRight className="size-3" /></li>
            </Fragment>
          ))}
          <li aria-current="page" className="min-w-0 break-words text-foreground">{job}</li>
        </ol>
      </nav>
    </div>
  );
}
