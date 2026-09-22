# Bermuda Business Lead Scraper

Builds a sector-tagged, contactable lead list of Bermuda businesses for B2B
telecommunications sales, and detects which telecom / IT vendors each prospect
already uses.

Outputs:

- `business-leads.md` — sales-readable, grouped by sector then size tier.
- `leads.csv` — one row per business, ready to import into a CRM.

## Requirements

`uv` only. It pins Python 3.12 and installs the dependencies itself.
Classification runs through the **Claude Code CLI on your subscription**, so no
Anthropic API key is needed. Make sure `claude` is on your `PATH` and signed in.

## Running

```bash
uv sync
uv run python run.py all
```

Stages also run individually, in this order:

```bash
uv run python run.py scrape     # directories -> data/businesses.json
uv run python run.py enrich     # websites + DNS -> emails, size/scope signals
uv run python run.py classify   # claude -p -> sector, size, scope, providers
uv run python run.py export     # business-leads.md + leads.csv
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--limit N` | Process at most N records. Use for a quick test run. |
| `--force` | Redo work already recorded, instead of skipping it. |
| `--with-categories` | Also crawl BermudaYP category pages for category labels. Adds roughly 800 requests. |
| `-v` | Debug logging. |

Every stage is **resumable**. Fetched pages are cached under `data/cache/`, and
records already enriched or classified are skipped, so an interrupted run
continues where it stopped rather than starting over.

## Tests

```bash
uv run python tests.py
```

Covers phone and name normalisation, the listing and member parsers, and the
merge rules. Several tests reproduce bugs found while building this: phone
numbers leaking in from related listings, and the Chamber's own switchboard
number in every page footer collapsing unrelated firms into one record.

## Data sources

| Source | What it provides |
| --- | --- |
| BermudaYP (`bermudayp.com`) | ~3,600 listings: name, address, phone, fax, description, website |
| Bermuda Chamber of Commerce | ~380 members: name, address, phone, fax, division categories |
| Each business's own website | Contact emails, employee-count and geographic-scope wording, vendor mentions |
| DNS records (MX, NS) | The incumbent mail and hosting vendor, which often names the ISP |

Emails are not published by either directory, so they come from business
websites. Expect a contact email for roughly the share of businesses that run a
reachable website.

## How classification works

`leadscrape/classify.py` batches records into a prompt
(`leadscrape/prompts/classify.md`) and pipes it to `claude -p` with a JSON
schema, no tools, and manual permissions. The model returns sector, subsector,
size tier, geographic scope, current providers with evidence, and a confidence
rating. Failed batches are retried, then marked `failed` and skipped rather than
stopping the run.

Edit the taxonomy, vendor keyword lists and DNS mappings in `config.yaml`.

## Compliance

Bermuda's Personal Information Protection Act has applied since 1 January 2025
and covers business-to-business contact data.

- Only business-entity data is collected. Named-individual contacts are not targeted.
- Role addresses (`info@`, `sales@`, …) are treated as business contacts. Anything
  that looks personal is stored separately and **excluded from the exports** unless
  you set `export.include_personal_emails: true` in `config.yaml`.
- Add a business name or an email address to `suppress.txt`, one per line, to honour
  an opt-out. The next export drops it.
- The scraper identifies itself in its `User-Agent`, respects each directory's
  robots.txt, and rate-limits to one request per second per host.
- Every record keeps its source URLs and a collection date for provenance.

Confirm your own outreach practice against PIPA and the Electronic Transactions
Act before running a campaign on this list.
