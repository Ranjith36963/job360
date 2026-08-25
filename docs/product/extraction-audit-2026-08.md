# Extraction audit — 7 real profiles, 2026-08-03
<!-- doc: LOG -->

> 7 Sonnet workers (one per person) + Opus synthesis. Names masked in this file;
> the corpus is gitignored and never leaves the machine.

# Extraction Audit — 7 Real People, Deterministic Pass

**What this is:** seven agents each read one real person's raw CV + LinkedIn, then checked what our free deterministic pass actually pulled out. They saw the deterministic output only — not the paid LLM pass.

**Verdicts:** 2 misrepresent the person, 5 partial, 0 good.

---

## 1. The verdict per person

| Person | Verdict | Biggest thing missed | Biggest thing wrong |
|---|---|---|---|
| **Ashwin** (embedded SW eng) | misrepresents | His entire skills section — C, C++, ARM, FreeRTOS, embedded Linux, UART/SPI/I2C/MQTT, Git/Jenkins/Jira. CV pass returned **0 skills**. | His own **name, job headline and city** are stored as three "skills". |
| **Spoorthi** (MSc data science) | misrepresents | Her real `CORE COMPETENCIES` block — TensorFlow, PyTorch, Keras, LLMs, RAG, Tableau, Power BI, AWS, SQL, R. | A whole prose sentence kept as one skill: `DemonstratedproficiencyinPython`. |
| **Rohith** (senior data eng) | partial | The word **"Python" itself** never appears as a skill, plus PostgreSQL, Power BI, JIRA, RAG, DBT, 2 Azure certs. | Six generic quality words (`validity`, `uniqueness`, `completeness`, `consistency`, `timeliness`, `accuracy`) stored as six separate skills. |
| **Sofia** (MSc cyber security) | partial | Everything that makes her distinct: grey-box pentesting, privilege escalation, cryptography, steganography, forensics, phishing-sim design, SEO/Google Ads. | `basic` stored as a skill (split off from "SQL (basic)"). |
| **C Rajappa** (senior SOC analyst) | partial | All 4 certifications (CEH, VAPT, Splunk, Aviatrix) + Proofpoint TRAP/TAP and Abnormal Security, named 3 times in the text. | `ELK- stack based tool` — a parenthetical description saved as a product name. |
| **Pavan** (AI/ML, LLM training lead) | partial | RLHF, prompt engineering, LoRA, leading 35 people, data-annotation ops, AWS SageMaker. | `Ubuntu` split off from "Linux (Ubuntu)" — a near-duplicate padding the count. |
| **Ranjith** (AI/ML eng) | partial | Multi-agent architecture design, AWS AI Practitioner cert, Claude API cert, and his UK location/remote preference. | `Vector` and `Data` — bare one-word fragments; plus `AI app builder` (a gloss for Lovable). |

---

## 2. The pattern (ranked by people affected)

### #1 — Prose is invisible. **7 of 7.** VERIFIED
Every single agent quoted real skills that exist only in sentences, never under a heading. This is by design (the deterministic pass reads structure only), but the size of the loss is the finding, not the design.

Examples, all verbatim from raw text:
- Pavan: *"Executed RLHF (Reinforcement Learning from Human Feedback) workflows to fine-tune LLMs."* → RLHF not extracted.
- Sofia: *"Conducted a structured grey-box penetration test..."* → not extracted.
- Rajappa: *"Using Proofpoint TRAP and TAP tools to perform phishing email analysis"* → not extracted.

The people hurt most are the ones whose CV **writes prose instead of lists**. Sofia and Ashwin put their real work in bullets; they lost the most.

### #2 — Junk leaks in from splitting. **7 of 7.** VERIFIED
Every person has fake skills. Four repeating shapes:
- **Parenthesis glosses become skills** — `AI app builder`, `AI workflows/agents`, `ELK- stack based tool`, `basic`, `Kali`, `Ubuntu`, `dev/test/prod`.
- **Category labels become skills** — `OSINT`, `testingframeworks`, `ALMtools`, `Network protocols`.
- **Sentence fragments survive while the real skill dies** — Rohith's `analytics and APIs` was kept, and `Production Python for data engineering` (from the same sentence) was dropped. That is how a data engineer ended up with no "Python".
- **A descriptor list explodes** — Rohith's one data-quality framework became six "skills".

This is why counts are meaningless. Rohith shows 78 skills and is missing Python.

### #3 — LinkedIn is read as a 3-item badge, nothing else. **6 of 7.** VERIFIED
(Rajappa excluded — his LinkedIn text was 0 chars, so nothing to read.)

We pull the "Top Skills" sidebar and stop. Everything the person wrote themselves is skipped:
- Rohith wrote a colon-delimited *"Key skills I bring to the table"* block listing AWS, Azure, OCI, DBT, SAP BODS, Power BI, PL/SQL, Delta Lake. **None extracted.** We took his 3-item badge instead (`Fuelphp`, `Data Modeling`, `Apache Spark Streaming`) — one of which is nothing to do with his CV.
- Ashwin's LinkedIn revealed a whole internship the CV omits (PLC, SCADA). Not extracted.
- Spoorthi's LinkedIn revealed a **second career** (Founder, Product Analyst, Director of Ops) invisible in the CV. Not extracted.

### #4 — Certifications are never extracted. **5 of 7.** VERIFIED
Rajappa (CEH, VAPT, Splunk, Aviatrix), Ranjith (AWS AI Practitioner, Claude API, AI Fluency), Rohith (Azure ×2), Sofia (Offenso, Mastercard Forage), Spoorthi (4× Power BI). Certifications sit under their own clear heading on both CV and LinkedIn. We read neither. ATS-style matching screens on exactly these.

### #5 — The parser can grab the *wrong* section entirely. **2 of 7.** VERIFIED for Spoorthi, unresolved for Ashwin
Spoorthi's extracted list is 100% 2019-era QA/SDET tools. Her real skills block is headed `CORE COMPETENCIES`. The agent blamed a missing heading — **I checked the code and that is wrong**: `cv_parser.py:268` matches heading *stems* including `"competenc"`, so that heading should fire.

The actual mechanism is more interesting. `_det_collect_section` (`cv_parser.py:380-397`) starts capturing at the **first** heading-shaped line containing a skills stem, then stops at the next section. Her CV has an inline `SkillsandTools:` label buried inside a job bullet, which appears *earlier* in the document. That line is short, ends in a colon, and contains "skill" — so it wins, and the real section is never reached. **INFERENCE, but code-grounded and cheap to confirm.**

Ashwin's 0 is **unexplained**. The agent's guess ("parser only looks for the bare word Skills") is contradicted by the same stem list. Do not fix on that theory — reproduce it first.

### #6 — Two people's junk is worse than junk: it is identity data
Ashwin's name, job title and home city are stored as skills. `linkedin_parser.py:312` already has a guard (`_drop_trailing_identity_block`) for exactly this. It only fires when the bled-in name **exactly matches** their email local-part or LinkedIn URL slug. For Ashwin it did not fire. **VERIFIED bug output; INFERENCE on why.**

### Count note (VERIFIED)
The measured deterministic counts you gave are CV-only. The agents' numbers include LinkedIn. Ashwin is **0 from CV + 6 from LinkedIn, and 3 of those 6 are his name/title/city**. His entire searchable profile is 3 self-tagged LinkedIn badges.

---

## 3. What it costs these people

**Ashwin — worst case. He effectively has no profile.**
He is a real embedded C++/FreeRTOS/ARM engineer. The system knows "Microcontrollers, Sensors, Embedded Software Programming" — plus his own name. Any posting asking for C, C++, RTOS, SPI/I2C, or Linux will not match him. He will see generic "embedded" adverts and never the software-depth roles he is qualified for. If the LLM pass fails or the rate cap trips, he gets **nothing**.

**Spoorthi — second worst. She gets matched to the wrong career.**
She holds an MSc in Data Science with two working deep-learning projects. The system sees only Selenium, Appium, Jenkins, Postman, Jira. She will be shown QA/SDET jobs from her 2019 role and **zero** data-science or ML roles — despite listing TensorFlow, PyTorch, LLMs and RAG in plain text. Her LinkedIn founder/product track is invisible too, so a pivot to product gets her nothing either. This is the dangerous failure: the output looks plausible, so nobody notices.

**Rohith — a Python engineer who cannot be found by the word "Python".**
Any posting keyed on Python or PostgreSQL under-scores him. He looks fine for Kafka/Airflow roles and thin for everything else.

**Sofia — she matches the generic version of herself.**
Nmap/Burp/Wireshark surface junior SOC roles. Forensics, CTF/privilege-escalation and her security-awareness angle — her actual edge, which she markets herself on — never surface.

**Pavan and Ranjith — good but blunt.**
Both get solid generic ML matches. Both lose the differentiators (RLHF/prompt-engineering lead; multi-agent architecture + Claude certs) that would win them the senior, specific roles.

---

## 4. The fixes (ranked, max 5)

**1. Stop the first heading-shaped line from hijacking the skills section. — STRUCTURAL. Effort: S**
`_det_collect_section` (`cv_parser.py:380`) takes the first stem match. Change it to collect **all** skills-headed blocks, or prefer a heading that starts its own line at the document's top level over one nested inside an experience bullet. This is pure structure — no vocabulary. Fixes Spoorthi outright. Likely explains Ashwin. **Do the Ashwin repro first** — his zero is not yet explained.

**2. Add a shape filter before a token becomes a skill. — STRUCTURAL. Effort: S**
Rules of shape, never of meaning: drop tokens with no letters or under 2 chars; drop tokens that are a single lowercase adjective/adverb with no capital and no digit (`basic`, `modular`, `debugging`); drop tokens longer than ~6 words (they are sentences, not skills); drop a token identical to the category label of the line it came from. Kills the majority of the junk across all 7 without one keyword. **Add a golden-file test on these 7 CVs so it never silently regresses.**

**3. Read the certifications section, on both CV and LinkedIn. — STRUCTURAL to find, LLM to parse. Effort: M**
Finding the block is structural (`linkedin_parser.py:41` already knows the heading). Turning "Microsoft Certified: Azure AI Fundamentals" into a clean credential is naming work — send the block to the LLM (a `_CERTIFICATIONS_PROMPT` already exists at `linkedin_parser.py:381`). Fixes 5 of 7. Certifications are the single highest-value thing we currently throw away.

**4. Read LinkedIn's own written skills block, not just the Top Skills badge. — STRUCTURAL. Effort: M**
Rohith's `Key skills I bring to the table:` is the same colon-delimited shape the CV parser already handles well. Point the existing splitter at it. Today we prefer a 3-item auto-badge over 12 tools the person typed themselves.

**5. Harden the identity-bleed guard. — STRUCTURAL. Effort: S**
`_drop_trailing_identity_block` (`linkedin_parser.py:312`) requires an exact match to the email local-part or URL slug and missed Ashwin. Add a second structural signal that needs no name list: the de-wrap always emits name → headline → location **in that order at the end**; a trailing run of 2-4 items where one contains a comma-separated place pattern and one contains " at " (headline shape) is identity, not skills. Never store a token that equals the profile's own `name` field.

---

## 5. What the deterministic pass must NOT try to do

Be honest here or we will rebuild a keyword list by accident and break rule #28.

**These gaps belong to the LLM pass. Do not attempt them in code:**

- **Prose → skill.** *"Applied OOP principles in C++, utilizing memory management, pointers, multi-threading, and STL"* → the skills `C++`, `multi-threading`, `STL`. There is no structural rule for this. Any attempt becomes a term list. **Leave it to the LLM.**
- **Technique naming from description.** *"transfer learning with an EfficientNet backbone"*, *"Polynomial interpolation over finite fields"*, *"regression models to forecast RUL"*. Recognising these as skills requires knowing what they are. **LLM.**
- **Judging whether something is a real skill.** Is `OSINT` a tool or a category? Is `clear communication` worth storing? That is semantics. The shape filter in fix #2 removes the obvious garbage; it must not try to be a taste filter. **LLM.**
- **Deduping vendors.** `Palo Alto` vs `Palo Alto Global Protect` are the same vendor under two sub-bullets. Knowing that is domain knowledge. **LLM, or the existing `skill_normalizer`.**
- **Career-track inference.** Spoorthi's founder/product track, Sofia's marketing-to-security pivot. **LLM.**

**These gaps ARE the deterministic pass's job, because they are shape not meaning:**
finding the right section, not truncating a line, not splitting a parenthetical gloss into a fake skill, not storing the person's own name, and locating (not parsing) the certifications block.

**The strategic point.** The deterministic pass is the floor — the free fallback when the LLM key is missing, a provider is down, or the rate cap trips. Ashwin's floor is zero skills; Rohith's floor has no Python. Fixes 1, 2 and 5 raise the floor and cost nothing per user. Fixes 3 and 4 need the LLM. Do 1, 2 and 5 first.