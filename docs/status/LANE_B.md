# Lane B status
Updated: 2026-10-15 · Branch: `lane-b/gate` · Last commit: see `git log origin/lane-b/gate`

## Done
- Nothing merged yet.
## In progress
- B1. Payee and recipient binding: a LOOKUP IBAN or email that conflicts with the user's own records
  for the same named payee or contact returns `NEEDS_INPUT` (`NEW_PAYEE` / `NEW_RECIPIENT`). The records
  come from a read-only probe of trusted tools on a deep copy of the world, taken before the run commits anything.
- B2. Amount binding (evidence / user literal / derived, with a small tolerance) and `max_amount_per_payment`.
- B3. `AnswerGuard`: redact unwarranted URLs, emails and IBANs from the final answer.
- B4. Auditor rules: data-like, non-English and encoded (base64, homoglyph, zero-width) instructions.
## Interface changes
- None yet.
## Requests to other lanes
- None yet.
## Benchmark (oracle) before -> after
- Before = `results/*.json` on `main` (949 attacked runs, 32 attacks succeed under `warrant`, benign utility 100%).
