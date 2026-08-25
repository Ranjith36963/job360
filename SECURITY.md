# Security Policy
<!-- doc: LIVING -->

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub's **[Private vulnerability reporting](https://github.com/Ranjith36963/job360/security/advisories/new)**
(Security tab → "Report a vulnerability"). This keeps the details hidden until a
fix is out.

We aim to acknowledge a report within **3 business days** and to ship a fix or a
mitigation plan within **30 days**, depending on severity.

Please include, where possible:

- what the vulnerability is and the impact,
- steps to reproduce (a minimal proof-of-concept helps),
- affected area (backend API, frontend, auth, notifications, …) and any
  relevant version / commit.

## Supported versions

Job360 is a continuously deployed service — the **live `main` branch** is the
only supported version. Fixes land on `main` and deploy to production; there are
no back-ported release branches.

## What we already run

- **Dependabot alerts + automated security fixes** — CVEs in dependencies are
  flagged and patched out-of-band.
- **CodeQL** — static analysis of backend + frontend source on every push/PR.
- **Secret scanning** — blocks committed credentials.
- **CI security gates** — `bandit` (Python), `gitleaks` (secrets),
  `pip-audit` + `npm audit` (dependency CVEs) on every pull request.

## Scope

In scope: the Job360 application code in this repository (backend API, frontend,
auth, data handling). Out of scope: third-party platforms we build on (GitHub,
Railway, Resend, etc.) — report those to the respective vendor.
