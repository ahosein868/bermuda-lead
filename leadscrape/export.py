"""Writes business-leads.md (sales-readable) and leads.csv (CRM import)."""
from __future__ import annotations

import csv
import logging
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from .models import Business, normalize_name

log = logging.getLogger(__name__)

CSV_COLUMNS = [
    "name", "sector", "subsector", "size_tier", "geo_scope", "lead_label",
    "phone", "additional_phones", "email", "additional_emails",
    "website", "address", "parish",
    "current_providers", "provider_types", "provider_evidence",
    "mx_provider", "ns_provider",
    "size_evidence", "confidence", "notes", "sources", "collected_at",
]

SIZE_ORDER = ["Large", "Medium", "Small", "Micro", "Unknown"]


def _load_suppression(cfg: dict) -> set[str]:
    path = Path(cfg["export"].get("suppress_file", "suppress.txt"))
    if not path.exists():
        return set()
    entries = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            entries.add(normalize_name(line))
            entries.add(line.lower())
    return entries


def _filter(records: list[Business], cfg: dict) -> list[Business]:
    suppressed = _load_suppression(cfg)
    if not suppressed:
        return records
    kept = [
        r for r in records
        if normalize_name(r.name) not in suppressed
        and not any(e.lower() in suppressed for e in r.emails)
    ]
    log.info("export: %d records suppressed", len(records) - len(kept))
    return kept


def _emails_for(record: Business, cfg: dict) -> list[str]:
    emails = list(record.emails)
    if cfg["export"].get("include_personal_emails", False):
        emails += record.personal_emails
    return emails


def _row(record: Business, cfg: dict) -> dict[str, str]:
    emails = _emails_for(record, cfg)
    providers = record.providers or []
    return {
        "name": record.name,
        "sector": record.sector or "Unclassified",
        "subsector": record.subsector,
        "size_tier": record.size_tier or "Unknown",
        "geo_scope": record.geo_scope or "Unknown",
        "lead_label": record.summary_label(),
        "phone": record.phones[0] if record.phones else "",
        "additional_phones": "; ".join(record.phones[1:]),
        "email": emails[0] if emails else "",
        "additional_emails": "; ".join(emails[1:]),
        "website": record.website,
        "address": record.address,
        "parish": record.parish,
        "current_providers": "; ".join(p.get("vendor", "") for p in providers),
        "provider_types": "; ".join(p.get("type", "") for p in providers),
        "provider_evidence": " | ".join(p.get("evidence", "")[:160] for p in providers),
        "mx_provider": record.mx_provider,
        "ns_provider": record.ns_provider,
        "size_evidence": record.size_evidence,
        "confidence": record.classification_confidence,
        "notes": record.classification_notes,
        "sources": "; ".join(s.get("url", "") for s in record.sources),
        "collected_at": record.collected_at,
    }


def write_csv(records: list[Business], cfg: dict) -> Path:
    path = Path(cfg["export"].get("csv_path", "leads.csv"))
    rows = [_row(r, cfg) for r in _filter(records, cfg)]
    rows.sort(key=lambda r: (r["sector"], SIZE_ORDER.index(r["size_tier"])
                             if r["size_tier"] in SIZE_ORDER else 9, r["name"]))
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)
    log.info("export: wrote %d rows to %s", len(rows), path)
    return path


def _md_escape(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ").strip()


def write_markdown(records: list[Business], cfg: dict) -> Path:
    path = Path(cfg["export"].get("markdown_path", "business-leads.md"))
    kept = _filter(records, cfg)

    by_sector: dict[str, dict[str, list[Business]]] = defaultdict(lambda: defaultdict(list))
    for record in kept:
        sector = record.sector or "Unclassified"
        size = record.size_tier if record.size_tier in SIZE_ORDER else "Unknown"
        by_sector[sector][size].append(record)

    total = len(kept)
    with_email = sum(1 for r in kept if _emails_for(r, cfg))
    with_phone = sum(1 for r in kept if r.phones)
    with_site = sum(1 for r in kept if r.website)
    with_provider = sum(1 for r in kept if r.providers)
    provider_counts = Counter(
        p.get("vendor", "") for r in kept for p in (r.providers or []) if p.get("vendor")
    )

    lines: list[str] = []
    lines.append("# Bermuda Business Leads")
    lines.append("")
    lines.append(f"Generated {date.today().isoformat()} for B2B telecommunications sales.")
    lines.append("")
    lines.append("## Coverage")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("| --- | --- |")
    lines.append(f"| Businesses | {total} |")
    lines.append(f"| With a phone number | {with_phone} |")
    lines.append(f"| With a contact email | {with_email} |")
    lines.append(f"| With a website | {with_site} |")
    lines.append(f"| With an identified current provider | {with_provider} |")
    lines.append("")

    if provider_counts:
        lines.append("### Incumbent providers detected")
        lines.append("")
        lines.append("| Vendor | Businesses |")
        lines.append("| --- | --- |")
        for vendor, count in provider_counts.most_common():
            lines.append(f"| {_md_escape(vendor)} | {count} |")
        lines.append("")

    lines.append("### Businesses by sector")
    lines.append("")
    lines.append("| Sector | Businesses |")
    lines.append("| --- | --- |")
    for sector in sorted(by_sector, key=lambda s: (s == "Unclassified", s)):
        lines.append(f"| {_md_escape(sector)} | {sum(len(v) for v in by_sector[sector].values())} |")
    lines.append("")

    for sector in sorted(by_sector, key=lambda s: (s == "Unclassified", s)):
        lines.append(f"## {sector}")
        lines.append("")
        sizes = by_sector[sector]
        for size in SIZE_ORDER:
            group = sizes.get(size)
            if not group:
                continue
            lines.append(f"### {size} ({len(group)})")
            lines.append("")
            lines.append("| Business | Phone | Email | Website | Scope | Current providers | Confidence |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- |")
            for record in sorted(group, key=lambda r: r.name.lower()):
                emails = _emails_for(record, cfg)
                providers = "; ".join(p.get("vendor", "") for p in (record.providers or [])) or "—"
                lines.append(
                    "| {name} | {phone} | {email} | {site} | {scope} | {prov} | {conf} |".format(
                        name=_md_escape(record.name),
                        phone=record.phones[0] if record.phones else "—",
                        email=_md_escape(emails[0]) if emails else "—",
                        site=_md_escape(record.website) or "—",
                        scope=record.geo_scope or "Unknown",
                        prov=_md_escape(providers),
                        conf=record.classification_confidence or "—",
                    )
                )
            lines.append("")

    lines.append("## Notes on data collection")
    lines.append("")
    lines.append("- Sources: BermudaYP business directory and the Bermuda Chamber of Commerce "
                 "member directory, plus each business's own website where one was found.")
    lines.append("- Sector, size and scope are model classifications from public signals. "
                 "Treat `Unknown` as missing data, not as a small business.")
    lines.append("- `Current providers` lists vendors with supporting evidence in the source "
                 "data, including mail-exchange records. An empty cell means no evidence was found.")
    lines.append("- Personal-looking email addresses are excluded by default under Bermuda's "
                 "Personal Information Protection Act. Add names or addresses to `suppress.txt` "
                 "to honour opt-out requests; the next export drops them.")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    log.info("export: wrote %s (%d businesses)", path, total)
    return path


def export(records: list[Business], cfg: dict) -> tuple[Path, Path]:
    return write_markdown(records, cfg), write_csv(records, cfg)
