#!/usr/bin/env python3
"""Bermuda B2B lead scraper.

Stages run in order and are resumable; each skips work already recorded.

    uv run python run.py scrape  [--limit N] [--with-categories]
    uv run python run.py enrich  [--limit N] [--force]
    uv run python run.py classify [--limit N] [--force]
    uv run python run.py export
    uv run python run.py all
"""
from __future__ import annotations

import argparse
import logging
import sys

from leadscrape import classify as classify_mod
from leadscrape import enrich as enrich_mod
from leadscrape import export as export_mod
from leadscrape import merge as merge_mod
from leadscrape import models
from leadscrape.config import data_path, load_config
from leadscrape.http import Fetcher
from leadscrape.sources import bermudayp, chamber

BUSINESSES = "businesses.json"

log = logging.getLogger("leadscrape")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _state_path():
    return data_path(BUSINESSES)


def cmd_scrape(args, cfg) -> list[models.Business]:
    with Fetcher(cfg) as fetcher:
        groups = []
        if cfg["sources"]["bermudayp"].get("enabled", True):
            records = bermudayp.scrape(fetcher, cfg, limit=args.limit,
                                       with_categories=args.with_categories)
            models.save(records, data_path("raw", "bermudayp.json"))
            log.info("BermudaYP: %d records", len(records))
            groups.append(records)
        if cfg["sources"]["chamber"].get("enabled", True):
            records = chamber.scrape(fetcher, cfg, limit=args.limit)
            models.save(records, data_path("raw", "chamber.json"))
            log.info("Chamber: %d records", len(records))
            groups.append(records)
        log.info("HTTP stats: %s", fetcher.stats)

    merged = merge_mod.merge(groups)

    # Preserve enrichment/classification already done in earlier runs.
    previous = {r.id: r for r in models.load(_state_path())}
    for record in merged:
        old = previous.get(record.id)
        if not old:
            continue
        for field_name in ("enriched", "domain", "mx_provider", "ns_provider",
                           "site_text_excerpt", "signals", "sector", "subsector",
                           "size_tier", "size_evidence", "geo_scope", "providers",
                           "classification_confidence", "classification_notes",
                           "classification_status", "personal_emails"):
            value = getattr(old, field_name)
            if value:
                setattr(record, field_name, value)

    models.save(merged, _state_path())
    log.info("scrape: %d unique businesses -> %s", len(merged), _state_path())
    return merged


def cmd_enrich(args, cfg) -> list[models.Business]:
    records = models.load(_state_path())
    if not records:
        log.error("no businesses.json; run `scrape` first")
        return []
    with Fetcher(cfg) as fetcher:
        records = enrich_mod.enrich(
            records, fetcher, cfg, limit=args.limit, force=args.force,
            checkpoint=lambda rs: models.save(rs, _state_path()),
        )
        log.info("HTTP stats: %s", fetcher.stats)
    models.save(records, _state_path())
    with_email = sum(1 for r in records if r.emails)
    log.info("enrich: %d/%d records now have a contact email", with_email, len(records))
    return records


def cmd_classify(args, cfg) -> list[models.Business]:
    records = models.load(_state_path())
    if not records:
        log.error("no businesses.json; run `scrape` first")
        return []
    records = classify_mod.classify(
        records, cfg, limit=args.limit, force=args.force,
        checkpoint=lambda rs: models.save(rs, _state_path()),
    )
    models.save(records, _state_path())
    ok = sum(1 for r in records if r.classification_status == "ok")
    log.info("classify: %d/%d records classified", ok, len(records))
    return records


def cmd_export(args, cfg) -> None:
    records = models.load(_state_path())
    if not records:
        log.error("no businesses.json; run `scrape` first")
        return
    md, csv_path = export_mod.export(records, cfg)
    log.info("export: %s and %s written", md, csv_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["scrape", "enrich", "classify", "export", "all"])
    parser.add_argument("--limit", type=int, default=None,
                        help="process at most N records (useful for a test run)")
    parser.add_argument("--force", action="store_true",
                        help="redo work already recorded for a record")
    parser.add_argument("--with-categories", action="store_true",
                        help="also crawl BermudaYP category pages for category labels (slow)")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    cfg = load_config(args.config)

    if args.stage in ("scrape", "all"):
        cmd_scrape(args, cfg)
    if args.stage in ("enrich", "all"):
        cmd_enrich(args, cfg)
    if args.stage in ("classify", "all"):
        cmd_classify(args, cfg)
    if args.stage in ("export", "all"):
        cmd_export(args, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
