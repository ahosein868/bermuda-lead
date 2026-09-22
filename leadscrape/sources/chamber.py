"""Bermuda Chamber of Commerce (ChamberMaster/GrowthZone) adapter.

Member URLs come from the alphabetical index pages (the "0" key returns the
full roster) and from the division/category pages, which also supply the
Chamber's own category labels. Detail pages use GrowthZone's gz-card-* blocks
plus schema.org microdata.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..models import Business, normalize_phone, parse_parish, slugify

log = logging.getLogger(__name__)

SOURCE = "Bermuda Chamber of Commerce"
_MEMBER_RE = re.compile(r'href="((?:https?://[^"]*?)?/list/member/[^"#?]+)"')
# Only /list/category/ pages carry real division labels; /list/ql/ are
# duplicate "QuickLink" shortcuts whose titles are shouty and redundant.
_CATEGORY_RE = re.compile(r'href="((?:https?://[^"]*?)?/list/category/[^"#?]+)"')

_SKIP_EMAIL_DOMAINS = ("bcc.bm", "chambermaster.com", "growthzone.com")
# The Chamber's own switchboard appears in the page footer of every member page.
_CHAMBER_PHONES = {"+14412954201"}
_SKIP_HOSTS = (
    "chamber", "growthzone", "chambermaster", "google", "facebook", "twitter",
    "linkedin", "instagram", "jquery", "youtube", "x.com", "blob.core.windows.net",
)


def _abs(base: str, href: str) -> str:
    return href if href.startswith("http") else base + href


def category_urls(fetcher, cfg) -> list[str]:
    base = cfg["sources"]["chamber"]["base"].rstrip("/")
    html = fetcher.get(f"{base}/list")
    if not html:
        return []
    urls = {_abs(base, h) for h in _CATEGORY_RE.findall(html)}
    return sorted(urls)


def member_urls(fetcher, cfg, with_categories: bool = True) -> tuple[list[str], dict[str, list[str]]]:
    """Collect member URLs and, from category pages, their category labels."""
    base = cfg["sources"]["chamber"]["base"].rstrip("/")
    keys = cfg["sources"]["chamber"].get("alpha_keys", "0abcdefghijklmnopqrstuvwxyz")

    urls: list[str] = []
    seen: set[str] = set()
    categories: dict[str, set[str]] = {}

    def _record(found: list[str], label: str | None) -> int:
        added = 0
        for href in found:
            url = _abs(base, href)
            if url not in seen:
                seen.add(url)
                urls.append(url)
                added += 1
            if label:
                categories.setdefault(url, set()).add(label)
        return added

    for key in keys:
        html = fetcher.get(f"{base}/list/searchalpha/{key}")
        if not html:
            continue
        added = _record(_MEMBER_RE.findall(html), None)
        log.info("Chamber: alpha '%s' -> +%d members (total %d)", key, added, len(urls))

    if with_categories:
        cat_urls = category_urls(fetcher, cfg)
        log.info("Chamber: %d category pages", len(cat_urls))
        for cat_url in cat_urls:
            html = fetcher.get(cat_url)
            if not html:
                continue
            title = BeautifulSoup(html, "lxml").find("title")
            label = ""
            if title:
                label = re.sub(r"\s*(Category)?\s*\|.*$", "", title.get_text(strip=True)).strip()
                label = re.sub(r"\s+(Division|Committee|Assoc\.?|Association)$", "", label).strip()
            if not label:
                label = cat_url.rstrip("/").rsplit("/", 1)[-1].rsplit("-", 1)[0].replace("-", " ").title()
            added = _record(_MEMBER_RE.findall(html), label)
            if added:
                log.info("Chamber: category '%s' -> +%d members (total %d)", label, added, len(urls))

    return urls, {k: sorted(v) for k, v in categories.items()}


def _microdata(soup: BeautifulSoup, prop: str) -> str:
    node = soup.select_one(f'[itemprop="{prop}"]')
    return node.get_text(" ", strip=True) if node else ""


def parse_member(html: str, url: str) -> Business | None:
    soup = BeautifulSoup(html, "lxml")

    name = _microdata(soup, "name")
    if not name:
        title = soup.select_one(".gz-pagetitle, h1")
        name = title.get_text(strip=True) if title else ""
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return None

    street = _microdata(soup, "streetAddress")
    city = _microdata(soup, "addressLocality")
    postal = _microdata(soup, "postalCode")
    address = ", ".join(p for p in (street, city, postal) if p)

    # Phones come only from the member's own detail card, never the page footer.
    phones: list[str] = []
    for node in soup.select("li.gz-card-phone a[href^='tel:'], li.gz-card-phone [itemprop='telephone']"):
        raw = node.get("href", "")[4:] if node.has_attr("href") else node.get_text(strip=True)
        num = normalize_phone(raw)
        if num and num not in phones and num not in _CHAMBER_PHONES:
            phones.append(num)
    if not phones:
        num = normalize_phone(_microdata(soup, "telephone"))
        if num and num not in _CHAMBER_PHONES:
            phones.append(num)

    faxes: list[str] = []
    for node in soup.select("li.gz-card-fax a[href^='tel:'], li.gz-card-fax [itemprop='faxNumber']"):
        raw = node.get("href", "")[4:] if node.has_attr("href") else node.get_text(strip=True)
        num = normalize_phone(raw)
        if num and num not in faxes:
            faxes.append(num)
    phones = [p for p in phones if p not in faxes]

    categories = sorted({
        c.get_text(" ", strip=True)
        for c in soup.select(".gz-details-categories .gz-cat")
        if c.get_text(strip=True)
    })

    website = ""
    for anchor in soup.select("li.gz-card-website a[href^='http'], .gz-details-links a[href^='http']"):
        href = anchor["href"].strip()
        parts = href.split("/")
        host = parts[2].lower() if len(parts) > 2 else ""
        if any(bad in host for bad in _SKIP_HOSTS):
            continue
        website = href
        break

    emails = sorted({
        a["href"][7:].split("?")[0].strip().lower()
        for a in soup.select("li.gz-card-email a[href^='mailto:'], .gz-details-links a[href^='mailto:']")
        if "@" in a.get("href", "")
    })
    emails = [e for e in emails if not any(e.endswith(d) for d in _SKIP_EMAIL_DOMAINS)]

    return Business(
        id=slugify(name),
        name=name,
        phones=phones,
        fax=faxes,
        emails=emails,
        website=website,
        address=address,
        parish=parse_parish(address),
        directory_categories=categories,
        sources=[{"name": SOURCE, "url": url}],
    )


def scrape(fetcher, cfg, limit: int | None = None, with_categories: bool = True, **_: object) -> list[Business]:
    urls, url_categories = member_urls(fetcher, cfg, with_categories=with_categories)
    if limit:
        urls = urls[:limit]
    log.info("Chamber: %d member pages to fetch", len(urls))

    out: list[Business] = []
    for i, url in enumerate(urls, 1):
        html = fetcher.get(url)
        if not html:
            continue
        record = parse_member(html, url)
        if not record:
            continue
        extra = url_categories.get(url, [])
        if extra:
            record.directory_categories = sorted(set(record.directory_categories) | set(extra))
        # Drop "X" when "X Division" is also present (same label, two sources).
        labels = set(record.directory_categories)
        record.directory_categories = sorted(
            l for l in labels
            if not any(other != l and other.startswith(l + " ") for other in labels)
        )
        out.append(record)
        if i % 100 == 0:
            log.info("Chamber: %d/%d", i, len(urls))
    return out
