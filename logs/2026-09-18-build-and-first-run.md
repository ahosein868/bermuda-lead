# 2026-09-18 — Built the Bermuda lead scraper and ran it end to end

## Outcome

3,303 unique Bermuda businesses scraped, enriched, classified and exported to
`business-leads.md` and `leads.csv`. 3,302 of 3,303 classified successfully.

| Field | Count |
| --- | --- |
| Phone | 3,093 |
| Business email | 249 |
| Website | 577 |
| Incumbent provider identified | 439 |
| Large/Medium with Global/Regional scope | 91 |

## Decisions

- Classification runs through `claude -p` on the user's subscription, not the
  Anthropic API. The user asked for this explicitly.
- Sources are BermudaYP and the Chamber of Commerce. Dropped the BDA directory
  (JavaScript, curated), the Registrar of Companies (login-gated),
  `local.bermuda.com` (403 to bots) and LinkedIn (terms of service).
- Size is reported as `Unknown` rather than guessed. 2,390 records carry it,
  because public signals rarely reveal headcount for a small firm with no site.

## Bugs found and fixed

1. **Phone leakage.** BermudaYP listing pages render phone numbers for nearby
   businesses. Unscoped `tel:` selection gave every record 6-7 wrong numbers.
   Fixed by preferring JSON-LD and scoping DOM selection to `.main-info`.
2. **False merges.** The Chamber's switchboard appears in every member page
   footer, collapsing 11 unrelated firms into one record. Fixed by scoping
   phone selection to `li.gz-card-phone` and refusing to merge on any phone
   shared by more than two records.
3. **Email filter too strict.** An allowlist of prefixes discarded obvious
   business addresses (`clientservices@`, `customer.care@`, `hamilton@`),
   costing 87 leads their only contact. Rewritten to default to business and
   hold back only addresses that name a person. Recovered 82 emails.
4. **Substring matching.** The role word "it" matched inside "merritt", marking
   a personal address as departmental. Now matches whole tokens.
5. **No checkpointing.** An external kill at batch 31 of 221 lost 465 classified
   records, because the stage only saved at the end. Both `classify` and
   `enrich` now checkpoint to disk periodically.

## Known limits

- Only one business showed a detectable telecom vendor. DNS reveals the mail and
  hosting stack (225 on Microsoft 365) but not the circuit supplier. Getting
  that needs Regulatory Authority licensee filings or qualification on the call.
- `bda.tire@worlddistributors.bm` is still classified as personal. It is
  structurally identical to `jane.doe@`. Fails in the safe direction.
