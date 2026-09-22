You are a B2B market analyst building a sales lead list for a telecommunications
company selling connectivity, mobile and managed IT services to businesses in Bermuda.

For EACH business below, decide:

1. `sector` - exactly one of: {SECTORS}
2. `subsector` - a short free-text specialisation (e.g. "boutique hotel", "captive insurance manager"). Empty string if unclear.
3. `size_tier` - one of: {SIZE_TIERS}
   Micro = under 10 staff, Small = 10-49, Medium = 50-249, Large = 250+.
   Base this on stated employee counts, number of locations, the breadth of
   services described, and how well known the business is. Use "Unknown" when
   there is genuinely no signal. Do not guess "Medium" as a default.
4. `size_evidence` - the specific phrase or fact that drove the size call. Empty if Unknown.
5. `geo_scope` - one of: {GEO_SCOPES}
   Local = operates only in Bermuda. Regional = also Caribbean/North America.
   Global = offices, clients or operations on multiple continents.
6. `providers` - the telecom / internet / managed-IT vendors this business
   CURRENTLY USES. Include an entry only when the input data supports it:
   an explicit mention in the website text, or an mx_provider / ns_provider
   value that names a vendor. Each entry: {"vendor", "type", "evidence", "confidence"}.
   `type` is one of "telecom", "hosting", "email", "it_managed".
   Return an empty list when there is no evidence. NEVER invent a vendor.
7. `confidence` - "high", "medium" or "low" for the overall classification.
8. `notes` - at most one short sentence a salesperson would find useful. Empty if nothing to add.

Rules:
- Work only from the data given. Do not use outside knowledge to fill contact details.
- Prefer "Unknown" over a guess. A wrong size tier is worse than an absent one.
- `evidence` must quote or closely paraphrase the input, never your own inference.
- Return one object per input business, in the same order, with the same `id`.

Businesses:

{RECORDS}

Return ONLY a JSON array. No prose, no markdown fences.
