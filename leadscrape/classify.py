"""Classification via the Claude Code CLI (subscription auth, no API key)."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from .models import Business

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "classify.md"

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "sector": {"type": "string"},
                    "subsector": {"type": "string"},
                    "size_tier": {"type": "string"},
                    "size_evidence": {"type": "string"},
                    "geo_scope": {"type": "string"},
                    "providers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "vendor": {"type": "string"},
                                "type": {"type": "string"},
                                "evidence": {"type": "string"},
                                "confidence": {"type": "string"},
                            },
                            "required": ["vendor", "type", "evidence", "confidence"],
                            "additionalProperties": False,
                        },
                    },
                    "confidence": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": [
                    "id", "sector", "subsector", "size_tier", "size_evidence",
                    "geo_scope", "providers", "confidence", "notes",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _record_payload(record: Business) -> dict:
    signals = record.signals or {}
    return {
        "id": record.id,
        "name": record.name,
        "directory_categories": record.directory_categories,
        "address": record.address,
        "parish": record.parish,
        "website": record.website,
        "directory_description": (record.description or "")[:600],
        "website_text": (record.site_text_excerpt or "")[:1500],
        "mx_provider": record.mx_provider,
        "ns_provider": record.ns_provider,
        "employee_hints": signals.get("employee_hints", []),
        "global_terms": signals.get("global_terms", []),
        "provider_mentions": signals.get("provider_mentions", []),
    }


def build_prompt(batch: list[Business], cfg: dict) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    tax = cfg["taxonomy"]
    payload = [_record_payload(r) for r in batch]
    return (
        template.replace("{SECTORS}", ", ".join(tax["sectors"]))
        .replace("{SIZE_TIERS}", ", ".join(tax["size_tiers"]))
        .replace("{GEO_SCOPES}", ", ".join(tax["geo_scopes"]))
        .replace("{RECORDS}", json.dumps(payload, indent=1, ensure_ascii=False))
    )


def _extract_json(text: str) -> list[dict] | None:
    """Pull the results array out of whatever the CLI returned."""
    text = (text or "").strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\[.*\]|\{.*\})", text, re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    if isinstance(data, dict):
        data = data.get("results", data.get("businesses", []))
    return data if isinstance(data, list) else None


def _run_cli(prompt: str, cfg: dict) -> list[dict] | None:
    conf = cfg["classify"]
    binary = shutil.which(conf.get("cli", "claude")) or str(Path.home() / ".local/bin/claude")
    cmd = [
        binary,
        "-p",
        "--output-format", "json",
        "--model", conf.get("model", "sonnet"),
        "--json-schema", json.dumps(SCHEMA),
        "--tools", "",
        "--permission-mode", "manual",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=int(conf.get("timeout_seconds", 600)),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        log.warning("claude CLI call failed: %s", exc)
        return None

    if proc.returncode != 0:
        log.warning("claude CLI exited %s: %s", proc.returncode, (proc.stderr or "")[:400])
        return None

    raw = proc.stdout.strip()
    try:
        envelope = json.loads(raw)
        payload = envelope.get("result", raw) if isinstance(envelope, dict) else raw
    except json.JSONDecodeError:
        payload = raw
    if isinstance(payload, (dict, list)):
        if isinstance(payload, dict):
            payload = payload.get("results", payload)
        return payload if isinstance(payload, list) else None
    return _extract_json(payload)


def _apply(record: Business, result: dict, cfg: dict) -> None:
    tax = cfg["taxonomy"]
    sector = (result.get("sector") or "").strip()
    record.sector = sector if sector in tax["sectors"] else "Other"
    record.subsector = (result.get("subsector") or "").strip()
    size = (result.get("size_tier") or "").strip().title()
    record.size_tier = size if size in tax["size_tiers"] else "Unknown"
    record.size_evidence = (result.get("size_evidence") or "").strip()
    scope = (result.get("geo_scope") or "").strip().title()
    record.geo_scope = scope if scope in tax["geo_scopes"] else "Unknown"
    providers = result.get("providers") or []
    record.providers = [p for p in providers if isinstance(p, dict) and p.get("vendor")]
    record.classification_confidence = (result.get("confidence") or "").strip().lower()
    record.classification_notes = (result.get("notes") or "").strip()
    record.classification_status = "ok"


def classify(records: list[Business], cfg: dict, limit: int | None = None,
             force: bool = False, checkpoint=None) -> list[Business]:
    """Classify records in batches.

    `checkpoint` is called with the full record list every few batches so a
    long run that is interrupted keeps the work it has already done.
    """
    todo = [r for r in records if force or r.classification_status != "ok"]
    if limit:
        todo = todo[:limit]
    size = int(cfg["classify"].get("batch_size", 15))
    max_retries = int(cfg["classify"].get("max_retries", 2))
    batches = [todo[i:i + size] for i in range(0, len(todo), size)]
    checkpoint_every = max(1, int(cfg["classify"].get("checkpoint_every_batches", 5)))
    log.info("classify: %d records in %d batches", len(todo), len(batches))

    by_id = {r.id: r for r in records}
    for n, batch in enumerate(batches, 1):
        prompt = build_prompt(batch, cfg)
        results = None
        for attempt in range(1, max_retries + 1):
            results = _run_cli(prompt, cfg)
            if results:
                break
            log.warning("classify: batch %d attempt %d returned nothing", n, attempt)

        if not results:
            for record in batch:
                record.classification_status = "failed"
            log.error("classify: batch %d failed after %d attempts", n, max_retries)
            continue

        # Match by id when present, else fall back to positional order.
        keyed = {r.get("id"): r for r in results if isinstance(r, dict) and r.get("id")}
        for idx, record in enumerate(batch):
            result = keyed.get(record.id)
            if result is None and idx < len(results) and isinstance(results[idx], dict):
                result = results[idx]
            if result is None:
                record.classification_status = "failed"
                continue
            _apply(by_id.get(record.id, record), result, cfg)

        done = sum(1 for r in batch if r.classification_status == "ok")
        log.info("classify: batch %d/%d -> %d/%d classified", n, len(batches), done, len(batch))

        if checkpoint is not None and (n % checkpoint_every == 0 or n == len(batches)):
            try:
                checkpoint(records)
                log.info("classify: checkpoint saved after batch %d", n)
            except Exception as exc:
                log.warning("classify: checkpoint failed: %s", exc)
    return records
