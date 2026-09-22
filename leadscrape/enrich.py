"""Website + DNS enrichment: emails, size/scope signals, incumbent providers."""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .models import Business

log = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
STAFF_RE = re.compile(
    r"(?:(\d{2,5})\s*(?:\+\s*)?(?:employees|staff|people|professionals|team members)"
    r"|(?:team|staff|workforce)\s+of\s+(?:over\s+|more than\s+)?(\d{2,5}))",
    re.I,
)
GLOBAL_TERMS = (
    "worldwide", "global", "globally", "international offices", "offices in",
    "our offices", "multinational", "across the globe", "around the world",
    "subsidiaries", "group companies",
)
_JUNK_EMAIL_DOMAINS = ("sentry.", "example.com", "wixpress.com", "sentry.io", ".png", ".jpg")


# Department, function or place words. Matched against whole tokens of the
# local part, never as raw substrings -- "it" inside "merritt" is not a role.
_ROLE_WORDS = {
    "info", "sales", "contact", "contactus", "office", "admin", "administration",
    "enquiries", "enquiry", "inquiries", "inquiry", "hello", "support", "reception",
    "mail", "email", "general", "service", "services", "customer", "customers",
    "client", "clients", "clientservices", "customerservice", "customerservices",
    "care", "customercare", "help", "helpdesk", "team", "accounts", "accounting",
    "billing", "invoice", "invoices", "booking", "bookings", "reservation",
    "reservations", "orders", "order", "shop", "store", "frontdesk", "desk",
    "business", "corporate", "commercial", "operations", "ops", "dispatch",
    "quotes", "quote", "estimating", "hr", "recruitment", "careers", "jobs",
    "press", "media", "marketing", "finance", "payable", "payables",
    "receivable", "receivables", "legal", "privacy", "compliance", "security",
    "it", "tech", "support", "web", "webmaster", "noreply", "newsletter",
    "subscribe", "feedback", "main", "head", "office", "branch", "group",
    "hamilton", "pembroke", "paget", "warwick", "southampton", "devonshire",
    "sandys", "smiths", "stgeorges", "stgeorge", "bermuda",
}

# Given names that appear as a whole local part, e.g. john@, sarah@.
_GIVEN_NAMES = {
    "john", "james", "robert", "michael", "david", "william", "richard",
    "joseph", "thomas", "charles", "chris", "daniel", "matthew", "mark",
    "paul", "steven", "andrew", "kevin", "brian", "george", "peter", "simon",
    "sarah", "mary", "jennifer", "linda", "susan", "jessica", "karen", "nancy",
    "lisa", "anna", "emma", "laura", "kate", "katie", "rachel", "claire",
    "julie", "nicole", "amanda", "michelle", "melissa", "tracy", "tina",
}


def _tokens(local: str) -> list[str]:
    return [t for t in re.split(r"[^a-z]+", local.lower()) if t]


def _is_role_email(addr: str, prefixes: list[str]) -> bool:
    """True when the address belongs to the organisation rather than a person.

    Defaults to business. Under Bermuda's PIPA the concern is information about
    an identifiable individual, so only addresses that actually name a person
    are held back from the export.
    """
    local = addr.split("@", 1)[0].lower()
    tokens = _tokens(local)
    if not tokens:
        return True
    joined = "".join(tokens)

    if any(joined.startswith(p) for p in prefixes) or joined in _ROLE_WORDS:
        return True
    if any(t in _ROLE_WORDS for t in tokens):
        return True

    # firstname.lastname@ or f.lastname@ names a person.
    if len(tokens) == 2 and all(t.isalpha() for t in tokens):
        if len(tokens[0]) <= 15 and 2 <= len(tokens[1]) <= 20:
            return False
    # A bare given name, e.g. sarah@.
    if len(tokens) == 1 and tokens[0] in _GIVEN_NAMES:
        return False
    return True


def domain_of(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url if "//" in url else "https://" + url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _dns_lookup(domain: str, cfg: dict) -> tuple[str, str]:
    """Return (mx_provider, ns_provider) labels for the domain."""
    if not domain or not cfg["enrich"].get("dns_enabled", True):
        return "", ""
    try:
        import dns.resolver
    except ImportError:  # pragma: no cover
        return "", ""

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 5.0
    resolver.timeout = 5.0

    def _records(rtype: str) -> list[str]:
        try:
            return [str(r).lower() for r in resolver.resolve(domain, rtype)]
        except Exception:
            return []

    mx_label = ""
    for record in _records("MX"):
        for fragment, label in cfg["mx_providers"].items():
            if fragment in record:
                mx_label = label
                break
        if mx_label:
            break

    ns_label = ""
    for record in _records("NS"):
        for fragment, label in cfg["ns_providers"].items():
            if fragment in record:
                ns_label = label
                break
        if ns_label:
            break

    return mx_label, ns_label


def _provider_mentions(text: str, cfg: dict) -> list[dict[str, str]]:
    lowered = f" {text.lower()} "
    hits: list[dict[str, str]] = []
    for kind, entries in cfg["providers"].items():
        for entry in entries:
            for pattern in entry["patterns"]:
                idx = lowered.find(pattern.lower())
                if idx >= 0:
                    hits.append({
                        "vendor": entry["vendor"],
                        "type": kind,
                        "evidence": text[max(0, idx - 60):idx + 90].strip(),
                    })
                    break
    # one hit per vendor
    seen: set[str] = set()
    return [h for h in hits if not (h["vendor"] in seen or seen.add(h["vendor"]))]


def _crawl_site(record: Business, fetcher, cfg) -> tuple[str, list[str]]:
    """Fetch homepage plus a couple of contact/about pages. Returns (text, emails)."""
    max_pages = int(cfg["enrich"].get("max_pages_per_site", 3))
    start = record.website if record.website.startswith("http") else f"https://{record.website}"
    home = fetcher.get(start)
    if not home:
        return "", []

    pages = [home]
    soup = BeautifulSoup(home, "lxml")
    host = domain_of(start)
    wanted = cfg["enrich"].get("page_paths", [])
    candidates: list[str] = []
    for anchor in soup.select("a[href]"):
        href = anchor["href"]
        label = anchor.get_text(" ", strip=True).lower()
        target = urljoin(start, href)
        if domain_of(target) != host:
            continue
        path = urlparse(target).path.lower().rstrip("/")
        if any(path.startswith(w) for w in wanted) or label in ("contact", "contact us", "about", "about us"):
            if target not in candidates:
                candidates.append(target)

    for url in candidates[: max_pages - 1]:
        body = fetcher.get(url)
        if body:
            pages.append(body)

    texts, emails = [], []
    for body in pages:
        page = BeautifulSoup(body, "lxml")
        for tag in page(["script", "style", "noscript"]):
            tag.decompose()
        texts.append(page.get_text(" ", strip=True))
        emails.extend(a["href"][7:].split("?")[0] for a in page.select('a[href^="mailto:"]') if "@" in a.get("href", ""))
        emails.extend(EMAIL_RE.findall(texts[-1]))

    text = re.sub(r"\s+", " ", " ".join(texts))
    return text[: int(cfg["enrich"].get("max_text_chars", 6000))], emails


def enrich_one(record: Business, fetcher, cfg) -> Business:
    record.domain = domain_of(record.website)
    text, raw_emails = ("", [])
    if record.website:
        text, raw_emails = _crawl_site(record, fetcher, cfg)

    prefixes = [p.lower() for p in cfg["role_email_prefixes"]]
    role, personal = [], []
    for addr in raw_emails:
        addr = addr.strip().lower().strip(".,;:")
        if "@" not in addr or any(j in addr for j in _JUNK_EMAIL_DOMAINS):
            continue
        if len(addr) > 80:
            continue
        bucket = role if _is_role_email(addr, prefixes) else personal
        if addr not in bucket:
            bucket.append(addr)

    record.emails = list(dict.fromkeys(record.emails + role))
    record.personal_emails = list(dict.fromkeys(record.personal_emails + personal))

    record.mx_provider, record.ns_provider = _dns_lookup(record.domain, cfg)
    record.site_text_excerpt = text[:2000]

    staff_hits = [next(g for g in m if g) for m in STAFF_RE.findall(text)] if text else []
    record.signals = {
        "employee_hints": staff_hits[:5],
        "global_terms": sorted({t for t in GLOBAL_TERMS if t in text.lower()}),
        "provider_mentions": _provider_mentions(text, cfg),
        "site_reachable": bool(text),
    }
    record.enriched = True
    return record


def enrich(records: list[Business], fetcher, cfg, limit: int | None = None,
           force: bool = False, checkpoint=None) -> list[Business]:
    """Enrich records from their websites and DNS.

    `checkpoint` is called with the full record list periodically so an
    interrupted run keeps the work it has already done.
    """
    todo = [r for r in records if force or not r.enriched]
    if limit:
        todo = todo[:limit]
    every = max(1, int(cfg["enrich"].get("checkpoint_every_records", 100)))
    log.info("enrich: %d of %d records need enrichment", len(todo), len(records))
    for i, record in enumerate(todo, 1):
        try:
            enrich_one(record, fetcher, cfg)
        except Exception as exc:  # keep the run going
            log.warning("enrich failed for %s: %s", record.name, exc)
            record.enriched = True
            record.signals = {"error": str(exc)}
        if i % 50 == 0:
            log.info("enrich: %d/%d", i, len(todo))
        if checkpoint is not None and (i % every == 0 or i == len(todo)):
            try:
                checkpoint(records)
            except Exception as exc:
                log.warning("enrich: checkpoint failed: %s", exc)
    return records
