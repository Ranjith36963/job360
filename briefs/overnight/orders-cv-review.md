# Orders for CV review test (from Kits, 01:10 UTC)

You have not reported since 23:00. If you are alive, write a line in your report file now.

- The 23:33 run is unscored and the 00:34 run scored **0%**: it re-reviewed 10 old weak CVs (555, 561, 565, 568, 569,
  570, 572, 574, 575, 577), all stored weak again (`claim_not_in_quote` on the Calnex lines, `no_evidence` on the
  summary line), then stopped after two pages without rewriting anything. 186 items still need work.
- **Kits is fixing this now** on branch `fix/cv-run-weak-loop` (recipe: a weak result never ends the run; rewrite the
  line or the whole old CV; keep going to 10; plus the server side only where the evidence shows a mismatch, such as
  the summary line of an old layout not being treated as `top`). **Do not build the same fix.** Take audit section 2
  findings 2 and 3 (the closed skills-label list; literal token coverage, "k8s" against "Kubernetes").
- Score the 01:33 run and write the entry.
