"""Cross-source dedup: one Business per real-world company."""
from __future__ import annotations

import logging
from collections import Counter

from .models import Business

log = logging.getLogger(__name__)

# A phone shared by more than this many records is a switchboard or a directory's
# own number, not an identifier. Merging on it would collapse unrelated firms.
SHARED_PHONE_THRESHOLD = 2


def _absorb(base: Business, other: Business) -> None:
    """Fold `other` into `base`, preferring existing non-empty values."""
    if base.key() == other.key() and len(other.name) > len(base.name):
        base.name = other.name
    for field_name in ("phones", "fax", "emails", "personal_emails", "directory_categories"):
        merged = list(dict.fromkeys(getattr(base, field_name) + getattr(other, field_name)))
        setattr(base, field_name, merged)
    for field_name in ("website", "address", "parish", "description"):
        if not getattr(base, field_name) and getattr(other, field_name):
            setattr(base, field_name, getattr(other, field_name))
    known = {(s.get("name"), s.get("url")) for s in base.sources}
    for src in other.sources:
        if (src.get("name"), src.get("url")) not in known:
            base.sources.append(src)


def merge(groups: list[list[Business]]) -> list[Business]:
    """Merge records matching on normalised name, or on a phone unique to one business."""
    all_records = [r for group in groups for r in group]

    phone_counts: Counter[str] = Counter()
    for record in all_records:
        for phone in set(record.phones):
            phone_counts[phone] += 1
    shared = {p for p, c in phone_counts.items() if c > SHARED_PHONE_THRESHOLD}
    if shared:
        log.info("merge: ignoring %d shared phone numbers for matching", len(shared))

    by_name: dict[str, Business] = {}
    by_phone: dict[str, Business] = {}
    merged_count = 0

    for record in all_records:
        key = record.key()
        if not key:
            continue
        target = by_name.get(key)
        if target is None:
            for phone in record.phones:
                if phone not in shared and phone in by_phone:
                    target = by_phone[phone]
                    break
        if target is None:
            by_name[key] = record
            for phone in record.phones:
                if phone not in shared:
                    by_phone.setdefault(phone, record)
        else:
            _absorb(target, record)
            merged_count += 1
            for phone in target.phones:
                if phone not in shared:
                    by_phone.setdefault(phone, target)

    out = list(by_name.values())
    log.info("merge: %d unique businesses from %d records (%d folded in)",
             len(out), len(all_records), merged_count)
    return out
