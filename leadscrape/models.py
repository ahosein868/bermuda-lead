"""Business record model plus name/phone normalisation helpers."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

# Legal suffixes stripped when building the dedup key.
_SUFFIXES = (
    "limited", "ltd", "llc", "llp", "lp", "inc", "incorporated", "co",
    "company", "corp", "corporation", "holdings", "group", "plc", "sac",
)
_SUFFIX_RE = re.compile(r"\b(" + "|".join(_SUFFIXES) + r")\b\.?", re.I)
_NONWORD_RE = re.compile(r"[^a-z0-9]+")

# Each entry maps a spelling found in the wild to the canonical parish name.
# Order matters: the most specific form is tested first. Bermuda has both a
# City of Hamilton (the downtown business district, postcode HM, which sits in
# Pembroke) and a separate Hamilton Parish on the north shore (postcodes CR and
# FL). Treating them as one place misplaces a business by half the island.
def normalize_name(name: str) -> str:
    """Casefolded, suffix-stripped key used for cross-source dedup."""
    s = unicodedata.normalize("NFKD", name or "")
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = s.replace("&", " and ")
    s = _SUFFIX_RE.sub(" ", s)
    s = _NONWORD_RE.sub(" ", s).strip()
    return re.sub(r"\s+", " ", s)


def slugify(name: str) -> str:
    return _NONWORD_RE.sub("-", normalize_name(name)).strip("-") or "unknown"


def normalize_phone(raw: str) -> str | None:
    """Return a Bermuda number as +1441XXXXXXX, or None if unusable."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if digits.startswith("1441") and len(digits) == 11:
        return f"+{digits}"
    if digits.startswith("441") and len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 7:  # local number without area code
        return f"+1441{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+1{digits}"
    return None


_PARISH_FORMS = (
    ("hamilton parish", "Hamilton Parish"),
    ("city of hamilton", "City of Hamilton"),
    ("st. george", "St. George's"),
    ("st george", "St. George's"),
    ("st.george", "St. George's"),
    ("saint george", "St. George's"),
    ("smith's", "Smith's"),
    ("smiths", "Smith's"),
    ("devonshire", "Devonshire"),
    ("pembroke", "Pembroke"),
    ("paget", "Paget"),
    ("warwick", "Warwick"),
    ("southampton", "Southampton"),
    ("sandys", "Sandys"),
)

# Postcode prefixes that decide a bare "Hamilton" between the two.
_HAMILTON_PARISH_CODES = ("cr", "fl")

BERMUDA_PARISHES = tuple(sorted({canon for _, canon in _PARISH_FORMS}))

_POSTCODE_RE = re.compile(r"\b([A-Za-z]{2})\s?(?:[0-9]{2}|BX)\b")


def parse_parish(address: str) -> str:
    """Return the canonical parish name found in an address, or "".

    A bare "Hamilton" is ambiguous, so the postcode prefix decides: CR and FL
    belong to Hamilton Parish, anything else to the City of Hamilton.
    """
    text = (address or "").lower()
    for form, canonical in _PARISH_FORMS:
        if form in text:
            return canonical
    if "hamilton" in text:
        match = _POSTCODE_RE.search(text)
        if match and match.group(1).lower() in _HAMILTON_PARISH_CODES:
            return "Hamilton Parish"
        return "City of Hamilton"
    return ""


@dataclass
class Business:
    id: str = ""
    name: str = ""
    phones: list[str] = field(default_factory=list)
    fax: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    personal_emails: list[str] = field(default_factory=list)
    website: str = ""
    address: str = ""
    parish: str = ""
    description: str = ""
    directory_categories: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)

    # enrichment
    domain: str = ""
    mx_provider: str = ""
    ns_provider: str = ""
    site_text_excerpt: str = ""
    signals: dict[str, Any] = field(default_factory=dict)
    enriched: bool = False

    # classification
    sector: str = ""
    subsector: str = ""
    size_tier: str = ""
    size_evidence: str = ""
    geo_scope: str = ""
    providers: list[dict[str, str]] = field(default_factory=list)
    classification_confidence: str = ""
    classification_notes: str = ""
    classification_status: str = ""

    collected_at: str = field(default_factory=lambda: date.today().isoformat())

    def key(self) -> str:
        return normalize_name(self.name)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Business":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def summary_label(self) -> str:
        """e.g. 'Hospitality & Tourism, Medium, Global operations'."""
        bits = [b for b in (self.sector, self.size_tier) if b and b != "Unknown"]
        if self.geo_scope and self.geo_scope != "Unknown":
            bits.append(f"{self.geo_scope} operations")
        return ", ".join(bits)


def save(records: list[Business], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [r.to_dict() for r in records]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load(path: Path) -> list[Business]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Business.from_dict(d) for d in raw]
