"""BermudaYP (bermudayp.com) adapter.

Listing URLs come from the XML sitemap. Each listing page carries schema.org
JSON-LD with name, address and phones; the outbound website link is tagged
data-listing-web. Email addresses sit behind a JS form and are not exposed,
so they come from website enrichment instead.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Iterable

from bs4 import BeautifulSoup

from ..models import Business, normalize_phone, parse_parish, slugify

log = logging.getLogger(__name__)

SOURCE = "BermudaYP"
_LOC_RE = re.compile(r"<loc>\s*(?:<!\[CDATA\[(.*?)\]\]>|([^<]+))\s*</loc>", re.S)
_LISTING_RE = re.compile(r"/listing/view/(\d+)/")


def _locs(xml: str) -> list[str]:
    out = []
    for cdata, plain in _LOC_RE.findall(xml or ""):
        url = (cdata or plain or "").strip()
        if url:
            out.append(url)
    return out


def listing_urls(fetcher, cfg) -> list[str]:
    """All /listing/view/ URLs advertised by the sitemap index."""
    index = fetcher.get(cfg["sources"]["bermudayp"]["sitemap_index"])
    if not index:
        log.error("BermudaYP sitemap index unavailable")
        return []
    urls: list[str] = []
    for child in _locs(index):
        if "sitemap_listing_" not in child or "listing_search" in child:
            continue
        body = fetcher.get(child)
        if body:
            urls.extend(u for u in _locs(body) if "/listing/view/" in u)
    # de-dup, preserve order
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


def category_search_urls(fetcher, cfg) -> list[str]:
    index = fetcher.get(cfg["sources"]["bermudayp"]["sitemap_index"])
    if not index:
        return []
    for child in _locs(index):
        if "sitemap_listing_search_" in child:
            body = fetcher.get(child)
            if body:
                return [u for u in _locs(body) if "/businesses/search/" in u]
    return []


def scrape_categories(fetcher, cfg) -> dict[str, list[str]]:
    """Map listing id -> category labels by walking category search pages."""
    max_pages = int(cfg["sources"]["bermudayp"].get("category_max_pages", 3))
    mapping: dict[str, set[str]] = {}
    urls = category_search_urls(fetcher, cfg)
    log.info("BermudaYP: %d category pages (up to %d pages each)", len(urls), max_pages)
    for base in urls:
        m = re.search(r"/businesses/search/\d+/(.+)$", base)
        if not m:
            continue
        label = re.sub(r"[+_]", " ", m.group(1))
        try:
            from urllib.parse import unquote

            label = unquote(label)
        except Exception:  # pragma: no cover
            pass
        label = label.strip().title()
        for page in range(1, max_pages + 1):
            url = re.sub(r"/businesses/search/\d+/", f"/businesses/search/{page}/", base)
            html = fetcher.get(url)
            if not html:
                break
            ids = set(_LISTING_RE.findall(html))
            if not ids:
                break
            for lid in ids:
                mapping.setdefault(lid, set()).add(label)
    return {k: sorted(v) for k, v in mapping.items()}


def _json_ld(soup: BeautifulSoup) -> dict:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, list):
            data = next((d for d in data if isinstance(d, dict)), {})
        if isinstance(data, dict) and data.get("@type") in ("LocalBusiness", "Organization"):
            return data
    return {}


def _address_from_ld(ld: dict) -> tuple[str, list[str]]:
    blocks = ld.get("address") or []
    if isinstance(blocks, dict):
        blocks = [blocks]
    street, phones = "", []
    for block in blocks:
        addr = block.get("address", block) if isinstance(block, dict) else {}
        if not isinstance(addr, dict):
            continue
        parts = [
            addr.get("streetAddress", ""),
            addr.get("addressLocality", "") or addr.get("addressRegion", ""),
            addr.get("postalCode", ""),
            addr.get("addressCountry", ""),
        ]
        candidate = ", ".join(p for p in parts if p)
        if candidate and not street:
            street = candidate
        tel = addr.get("telephone") or []
        if isinstance(tel, str):
            tel = [tel]
        phones.extend(tel)
    return street, phones


def parse_listing(html: str, url: str) -> Business | None:
    soup = BeautifulSoup(html, "lxml")
    ld = _json_ld(soup)

    name = (ld.get("name") or ld.get("legalName") or "").strip()
    if not name:
        h3 = soup.select_one(".main-info .block-title h3")
        name = h3.get_text(strip=True) if h3 else ""
    if not name:
        return None

    address, ld_phones = _address_from_ld(ld)
    if not address:
        node = soup.select_one('[itemtype*="PostalAddress"]')
        address = node.get_text(" ", strip=True) if node else ""

    # Phones: JSON-LD is authoritative. Fall back to tel: links inside the
    # listing's own detail block only -- the page also renders phone numbers
    # for nearby/related businesses, which must not be attributed here.
    phones: list[str] = []
    faxes: list[str] = []
    for raw in ld_phones:
        num = normalize_phone(raw)
        if num and num not in phones:
            phones.append(num)

    scope = soup.select_one(".main-info") or soup.select_one(".listing-details")
    if scope is not None:
        for anchor in scope.select('a[href^="tel:"]'):
            num = normalize_phone(anchor.get("href", "")[4:])
            if not num:
                continue
            parent = anchor.find_parent()
            label = parent.get_text(" ", strip=True).lower() if parent else ""
            target = faxes if "fax" in label else phones
            if num not in target:
                target.append(num)
    phones = [p for p in phones if p not in faxes]

    website = ""
    web_link = soup.select_one("a[data-listing-web]")
    if web_link and web_link.get("href", "").startswith("http"):
        website = web_link["href"].strip()
    if not website and ld.get("url", "").startswith("http") and "bermudayp.com" not in ld["url"]:
        website = ld["url"]

    description = ""
    if ld.get("description"):
        description = BeautifulSoup(ld["description"], "lxml").get_text(" ", strip=True)[:1500]

    emails = sorted({
        a["href"][7:].split("?")[0].strip()
        for a in soup.select('a[href^="mailto:"]')
        if "@" in a.get("href", "")
    })

    return Business(
        id=slugify(name),
        name=name,
        phones=phones,
        fax=faxes,
        emails=[e for e in emails if not e.endswith("yabsta.net")],
        website=website,
        address=address,
        parish=parse_parish(address),
        description=description,
        sources=[{"name": SOURCE, "url": url}],
    )


def scrape(fetcher, cfg, limit: int | None = None, with_categories: bool = False) -> list[Business]:
    urls = listing_urls(fetcher, cfg)
    if limit:
        urls = urls[:limit]
    log.info("BermudaYP: %d listings to fetch", len(urls))

    categories = scrape_categories(fetcher, cfg) if with_categories else {}

    out: list[Business] = []
    for i, url in enumerate(urls, 1):
        html = fetcher.get(url)
        if not html:
            continue
        record = parse_listing(html, url)
        if not record:
            continue
        m = _LISTING_RE.search(url)
        if m and categories.get(m.group(1)):
            record.directory_categories = categories[m.group(1)]
        out.append(record)
        if i % 100 == 0:
            log.info("BermudaYP: %d/%d", i, len(urls))
    return out
