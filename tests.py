#!/usr/bin/env python3
"""Regression tests for the parsing and merging traps found while building this.

Run with:  uv run python tests.py
"""
from __future__ import annotations

import sys

from leadscrape import merge as merge_mod
from leadscrape.config import load_config
from leadscrape.enrich import _is_role_email, domain_of
from leadscrape.models import Business, normalize_name, normalize_phone, parse_parish
from leadscrape.sources import bermudayp, chamber

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got != want:
        FAILURES.append(f"{label}\n     got:  {got!r}\n     want: {want!r}")


# -- phone normalisation -------------------------------------------------
check("local 7-digit gets Bermuda code", normalize_phone("295-1189"), "+14412951189")
check("dashed 441 number", normalize_phone("441-296-6021"), "+14412966021")
check("bare 441 number", normalize_phone("4412951089"), "+14412951089")
check("already prefixed", normalize_phone("+1 441 292 1882"), "+14412921882")
check("junk returns None", normalize_phone("n/a"), None)

# -- name normalisation --------------------------------------------------
check("legal suffixes stripped", normalize_name("A.F. Smith Trading Co. Ltd."), "a f smith trading")
check("Limited == Ltd", normalize_name("ABM Limited"), normalize_name("ABM Ltd."))
check("ampersand expanded", normalize_name("Smith & Jones"), "smith and jones")

# -- parish parsing ------------------------------------------------------
check("parish from address", parse_parish("2 Jubilee Rd., Devonshire Parish, DV 04"), "Devonshire")
check("no parish", parse_parish("Somewhere else"), "")
# The City of Hamilton and Hamilton Parish are different places half an island
# apart. A bare "Hamilton" is split by postcode: CR and FL are the parish.
check("city of hamilton", parse_parish("129 Front Street, City of Hamilton, HM 12"), "City of Hamilton")
check("bare Hamilton with HM code", parse_parish("65 Front Street, Hamilton, HM 12"), "City of Hamilton")
check("hamilton parish named", parse_parish("38 Fractious St., Hamilton Parish, CR 04"), "Hamilton Parish")
check("bare Hamilton with CR code", parse_parish("Some Rd, Hamilton, CR 02, Bermuda"), "Hamilton Parish")
# Three spellings of St. George's in the directories, one canonical output.
check("St. George's apostrophe", parse_parish("1 York St, St George's"), "St. George's")
check("Saint George spelled out", parse_parish("55 Mullet Bay Rd., Saint George, GE01"), "St. George's")
check("St George no punctuation", parse_parish("x, St George, Bermuda"), "St. George's")
check("Smiths without apostrophe", parse_parish("2 Rd, Smiths Parish, FL 07"), "Smith's")

# -- merge: shared switchboard numbers must not collapse firms -----------
# The Chamber's own number appeared in every member page footer and merged
# eleven unrelated businesses into one before the shared-phone guard existed.
SWITCHBOARD = "+14412954201"
crowd = [
    Business(id=f"f{i}", name=f"Firm {i} Ltd.", phones=[f"+144129200{i:02d}", SWITCHBOARD])
    for i in range(6)
]
merged = merge_mod.merge([crowd])
check("shared phone does not merge distinct firms", len(merged), 6)

# A phone unique to one business still merges it across sources.
same = [
    [Business(id="a", name="Acme Ltd.", phones=["+14412920001"], sources=[{"name": "YP", "url": "u1"}])],
    [Business(id="b", name="Acme Limited", phones=["+14412920001"], website="http://acme.bm",
              sources=[{"name": "Chamber", "url": "u2"}])],
]
merged = merge_mod.merge(same)
check("same company merges across sources", len(merged), 1)
check("merge keeps both source urls", len(merged[0].sources), 2)
check("merge fills missing website", merged[0].website, "http://acme.bm")

# Different companies that happen to share nothing stay separate.
distinct = [[Business(id="x", name="Alpha Ltd.", phones=["+14412920011"]),
             Business(id="y", name="Beta Ltd.", phones=["+14412920022"])]]
check("distinct companies stay separate", len(merge_mod.merge(distinct)), 2)

# -- email classification ------------------------------------------------
cfg = load_config()
prefixes = [p.lower() for p in cfg["role_email_prefixes"]]
check("info@ is a business address", _is_role_email("info@abs.bm", prefixes), True)
check("sales@ is a business address", _is_role_email("sales@abs.bm", prefixes), True)
# Departmental addresses that the first, allowlist-only filter wrongly excluded.
check("clientservices@ is a business address", _is_role_email("clientservices@bcb.bm", prefixes), True)
check("customer.care@ is a business address", _is_role_email("customer.care@hsbc.bm", prefixes), True)
check("branch location is a business address", _is_role_email("hamilton@electronics.bm", prefixes), True)
check("product line is a business address", _is_role_email("purewater@bwl.bm", prefixes), True)
# "it" must match as a token, not inside a surname like merritt.
check("surname containing 'it' is not a role", _is_role_email("cmerritt@ami.bm", prefixes), True)
check("first.last is personal", _is_role_email("jane.doe@abs.bm", prefixes), False)
check("initial.surname is personal", _is_role_email("j.smith@abs.bm", prefixes), False)
check("bare given name is personal", _is_role_email("sarah@abs.bm", prefixes), False)

check("domain strips www", domain_of("http://www.ami.bm"), "ami.bm")
check("domain from bare host", domain_of("aclero.com"), "aclero.com")
check("empty website", domain_of(""), "")

# -- sitemap CDATA parsing ----------------------------------------------
SITEMAP = (
    '<?xml version="1.0"?><urlset><url>'
    "<loc><![CDATA[https://www.bermudayp.com/listing/view/29627/auto-express]]></loc>"
    "</url><url><loc>https://www.bermudayp.com/listing/view/1/plain</loc></url></urlset>"
)
check("CDATA and plain locs both parse", len(bermudayp._locs(SITEMAP)), 2)

# -- BermudaYP listing: phones must not leak from related listings -------
LISTING = """
<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"LocalBusiness","name":"Atlantic Medical International",
 "address":[{"@type":"Place","address":{"@type":"PostalAddress","addressCountry":"Bermuda",
 "addressLocality":"Devonshire Parish","postalCode":"DV 04","streetAddress":"2 Jubilee Rd.",
 "telephone":["4412366810"]}}]}
</script></head><body>
<div class="main-info"><div class="block-title"><h3>Atlantic Medical International</h3></div>
  <a href="tel:441-296-6021">441-296-6021</a><span class="phone-label">Fax</span>
</div>
<a href="http://www.ami.bm" data-listing-web="1">Website</a>
<div class="listing-related-results">
  <a href="tel:4412931162">Someone Else</a><a href="tel:4412963324">Another Firm</a>
</div></body></html>
"""
rec = bermudayp.parse_listing(LISTING, "https://www.bermudayp.com/listing/view/29597/x")
check("listing name", rec.name, "Atlantic Medical International")
check("only the listing's own phone", rec.phones, ["+14412366810"])
check("fax separated from phones", rec.fax, ["+14412966021"])
check("website from data-listing-web", rec.website, "http://www.ami.bm")
check("parish parsed", rec.parish, "Devonshire")

# -- Chamber member: footer switchboard must be ignored ------------------
MEMBER = """
<html><body>
<h1 class="gz-pagetitle" itemprop="name">A.F. Smith Trading Co. Ltd.</h1>
<div class="gz-details-categories"><p>
  <span class="gz-cat">Business Technology Division</span>
  <span class="gz-cat">Computer Service/Sales</span></p></div>
<ul class="gz-list-group">
  <li class="gz-card-address"><span itemprop="streetAddress">7 Tumkins Ln</span>
    <span itemprop="addressLocality">Hamilton</span><span itemprop="postalCode">HM 09</span></li>
  <li class="gz-card-phone"><a href="tel:4412921882"><span itemprop="telephone">(441) 292-1882</span></a></li>
  <li class="gz-card-fax"><a href="tel:4412954062"><span itemprop="faxNumber">(441) 295-4062</span></a></li>
</ul>
<footer><a href="tel:441-295-4201">(441) 295-4201</a>
  <a href="mailto:info@bcc.bm">Chamber</a></footer>
</body></html>
"""
rec = chamber.parse_member(MEMBER, "https://www.bermudachamber.bm/list/member/a-f-smith-2")
check("member name", rec.name, "A.F. Smith Trading Co. Ltd.")
check("footer switchboard excluded", rec.phones, ["+14412921882"])
check("member fax", rec.fax, ["+14412954062"])
check("chamber's own email excluded", rec.emails, [])
check("categories captured", rec.directory_categories,
      ["Business Technology Division", "Computer Service/Sales"])

# -- summary label -------------------------------------------------------
lead = Business(name="X", sector="Hospitality & Tourism", size_tier="Medium", geo_scope="Global")
check("sales label", lead.summary_label(), "Hospitality & Tourism, Medium, Global operations")
unknown = Business(name="Y", sector="Retail", size_tier="Unknown", geo_scope="Unknown")
check("unknowns omitted from label", unknown.summary_label(), "Retail")


if FAILURES:
    print(f"FAILED ({len(FAILURES)}):")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("all tests passed")
