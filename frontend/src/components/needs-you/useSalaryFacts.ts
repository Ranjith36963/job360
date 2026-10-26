import { useEffect, useState } from "react";
import { getProfile } from "@/lib/api";
import { NO_SALARY_FACTS, salaryFactsFromProfile } from "@/lib/salary-suggest";
import type { SalaryFacts } from "@/lib/salary-suggest";

/** The user's saved salary facts, read from the profile ONCE per page and only when `enabled`
 * (some answer is waiting for the user). A failed read means no suggestions; it never breaks the card. */
export function useSalaryFacts(enabled: boolean): SalaryFacts {
  const [facts, setFacts] = useState<SalaryFacts>(NO_SALARY_FACTS);
  const [asked, setAsked] = useState(false);
  useEffect(() => {
    if (!enabled || asked) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- read once, then never again
    setAsked(true);
    (async () => {
      try {
        setFacts(salaryFactsFromProfile(await getProfile()));
      } catch {
        /* no suggestions */
      }
    })();
  }, [enabled, asked]);
  return facts;
}
