from __future__ import annotations

import gzip
import json
import re
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping

from .database import db
from .inventory_engine import SteamQueryError, fetch_localized_market_name
from .models import (
    ItemNameLocalization,
    LocalizationJob,
    Snapshot,
    SnapshotItem,
    SteamTarget,
    beijing_iso,
    utcnow,
)

_HAN_RE = re.compile(r"[\u3400-\u9fff]")
_CACHE_KEY = "__localized_market_names__"


def has_han(value: str) -> bool:
    return bool(_HAN_RE.search(value or ""))


def localization_map(language: str = "schinese") -> dict[str, str]:
    return {
        row.source_name: row.localized_name
        for row in ItemNameLocalization.query.filter_by(language=language).all()
    }


def upsert_localization(
    source_name: str,
    localized_name: str,
    *,
    language: str = "schinese",
    classid: str = "",
    instanceid: str = "0",
    source: str = "official",
) -> ItemNameLocalization | None:
    source_name = str(source_name or "").strip()
    localized_name = str(localized_name or "").strip()
    if not source_name or not localized_name:
        return None
    row = ItemNameLocalization.query.filter_by(language=language, source_name=source_name).first()
    if row is None:
        row = ItemNameLocalization(language=language, source_name=source_name)
        db.session.add(row)
    row.localized_name = localized_name
    row.classid = classid or row.classid or ""
    row.instanceid = instanceid or row.instanceid or "0"
    row.source = source
    return row


def _rebuild_unified(unified: dict[str, Any]) -> None:
    counts: Counter[str] = Counter()
    for asset in unified.get("_assets") or []:
        counts[str(asset.get("name") or "Unknown item")] += int(asset.get("amount", 1) or 1)
    unified["items"] = [{"name": name, "count": count} for name, count in sorted(counts.items())]
    unified["total_items"] = sum(counts.values())
    unified["item_types"] = len(counts)


def canonicalize_unified(target: SteamTarget, unified: dict[str, Any], *, language: str = "schinese") -> int:
    """Apply persistent and same-asset historical names before a snapshot is stored."""
    mappings = localization_map(language)
    previous = (
        Snapshot.query.filter_by(target_id=target.id)
        .order_by(Snapshot.scanned_at.desc(), Snapshot.id.desc())
        .first()
    )
    previous_assets = {row.asset_key: row for row in previous.items} if previous else {}

    for asset in unified.get("_assets") or []:
        current = str(asset.get("name") or "Unknown item")
        raw = str(asset.get("raw_name") or current)
        verified = bool(asset.get("name_localized")) or has_han(current)
        mapped = mappings.get(raw) or mappings.get(current)
        prior = previous_assets.get(str(asset.get("asset_key") or ""))
        if mapped:
            current = mapped
            verified = True
        elif prior and (prior.name_localized or has_han(prior.name)) and not verified:
            upsert_localization(
                raw,
                prior.name,
                language=language,
                classid=str(asset.get("classid") or prior.classid or ""),
                instanceid=str(asset.get("instanceid") or prior.instanceid or "0"),
                source="same_asset_history",
            )
            mappings[raw] = prior.name
            current = prior.name
            verified = True
        if verified and raw:
            upsert_localization(
                raw,
                current,
                language=language,
                classid=str(asset.get("classid") or ""),
                instanceid=str(asset.get("instanceid") or "0"),
                source="official_scan",
            )
            mappings[raw] = current
        asset["raw_name"] = raw
        asset["name"] = current
        asset["name_localized"] = verified

    _rebuild_unified(unified)
    return sum(not bool(asset.get("name_localized")) for asset in (unified.get("_assets") or []))


def queue_localization_job(snapshot: Snapshot, target: SteamTarget, unresolved_count: int, *, language: str) -> LocalizationJob | None:
    if unresolved_count <= 0:
        return None
    job = LocalizationJob(
        snapshot_id=snapshot.id,
        target_id=target.id,
        language=language,
        unresolved_count=unresolved_count,
        next_attempt_at=utcnow() + timedelta(minutes=15),
    )
    db.session.add(job)
    return job


def _snapshot_payload(snapshot: Snapshot) -> bytes:
    counts: Counter[str] = Counter()
    for row in snapshot.items:
        counts[row.name] += row.amount
    snapshot.item_types = len(counts)
    payload = {
        "items": [{"name": name, "count": count} for name, count in sorted(counts.items())],
        "total_items": snapshot.total_items,
        "item_types": snapshot.item_types,
        "coverage": snapshot.coverage,
        "scanned_at": beijing_iso(snapshot.scanned_at),
        "elapsed_ms": snapshot.elapsed_ms,
        "errors": json.loads(snapshot.errors_json or "[]"),
    }
    return gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _cache_mappings(cache_path: str | None) -> dict[str, str]:
    if not cache_path:
        return {}
    path = Path(cache_path)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    values = payload.get(_CACHE_KEY) if isinstance(payload, Mapping) else None
    return {str(key): str(value) for key, value in values.items()} if isinstance(values, Mapping) else {}


def build_trusted_history_mappings(*, cache_path: str | None = None) -> dict[str, str]:
    mappings = _cache_mappings(cache_path)
    histories: dict[tuple[int, str], list[SnapshotItem]] = defaultdict(list)
    rows = (
        SnapshotItem.query.join(Snapshot, Snapshot.id == SnapshotItem.snapshot_id)
        .order_by(Snapshot.scanned_at.asc(), Snapshot.id.asc())
        .all()
    )
    for row in rows:
        histories[(row.snapshot.target_id, row.asset_key)].append(row)
    for history in histories.values():
        trusted = next((row.name for row in reversed(history) if row.name_localized or has_han(row.name)), "")
        if not trusted:
            continue
        for row in history:
            source = row.raw_name or row.name
            if source and source != trusted and not has_han(source):
                mappings.setdefault(source, trusted)
    return mappings


def repair_retained_snapshots(
    *,
    dry_run: bool = True,
    language: str = "schinese",
    cache_path: str | None = None,
) -> dict[str, int | bool]:
    """Repair retained names without changing assets, amounts, times, or protection."""
    mappings = build_trusted_history_mappings(cache_path=cache_path)
    current = localization_map(language)
    combined = dict(current)
    combined.update(mappings)
    rows = SnapshotItem.query.all()
    changes: list[tuple[SnapshotItem, str]] = []
    for row in rows:
        source = row.raw_name or row.name
        localized = combined.get(source) or combined.get(row.name)
        if localized and (row.name != localized or not row.name_localized):
            changes.append((row, localized))

    affected = {row.snapshot_id for row, _name in changes}
    if not dry_run:
        for source_name, localized_name in mappings.items():
            upsert_localization(
                source_name,
                localized_name,
                language=language,
                source="cache_or_asset_history",
            )
        for row, localized in changes:
            if not row.raw_name:
                row.raw_name = row.name
            row.name = localized
            row.name_localized = True
        db.session.flush()
        for snapshot_id in affected:
            snapshot = db.session.get(Snapshot, snapshot_id)
            if snapshot:
                snapshot.payload_gzip = _snapshot_payload(snapshot)
        db.session.commit()

    unresolved = sum(not (row.name_localized or has_han(row.name) or (row.raw_name or row.name) in combined) for row in rows)
    return {
        "dry_run": dry_run,
        "trusted_mappings": len(mappings),
        "existing_mappings": len(current),
        "changed_items": len(changes),
        "affected_snapshots": len(affected),
        "unresolved_items": unresolved,
    }


def _apply_one_mapping(source_name: str, localized_name: str, *, language: str, classid: str, instanceid: str) -> int:
    upsert_localization(
        source_name,
        localized_name,
        language=language,
        classid=classid,
        instanceid=instanceid,
        source="official_hover",
    )
    rows = SnapshotItem.query.filter(
        (SnapshotItem.raw_name == source_name) | (SnapshotItem.name == source_name)
    ).all()
    snapshots: set[int] = set()
    for row in rows:
        if not row.raw_name:
            row.raw_name = row.name
        row.name = localized_name
        row.name_localized = True
        snapshots.add(row.snapshot_id)
    db.session.flush()
    for snapshot_id in snapshots:
        snapshot = db.session.get(Snapshot, snapshot_id)
        if snapshot:
            snapshot.payload_gzip = _snapshot_payload(snapshot)
    return len(rows)


def process_localization_job(job_id: int) -> None:
    job = db.session.get(LocalizationJob, job_id)
    if not job:
        return
    rows = SnapshotItem.query.filter_by(snapshot_id=job.snapshot_id, name_localized=False).all()
    candidates: dict[str, SnapshotItem] = {}
    for row in rows:
        candidates.setdefault(row.raw_name or row.name, row)
    resolved = 0
    failures: list[str] = []
    for source_name, row in candidates.items():
        if not row.classid:
            failures.append(f"{source_name}: missing classid")
            continue
        try:
            localized = fetch_localized_market_name(
                row.classid,
                row.instanceid,
                language=job.language,
                timeout=8.0,
            )
        except SteamQueryError as exc:
            failures.append(f"{source_name}: {exc}")
            continue
        if localized:
            resolved += _apply_one_mapping(
                source_name,
                localized,
                language=job.language,
                classid=row.classid,
                instanceid=row.instanceid,
            )

    remaining = SnapshotItem.query.filter_by(snapshot_id=job.snapshot_id, name_localized=False).count()
    job.unresolved_count = remaining
    job.attempt += 1
    job.error = "; ".join(failures)[:2000] or None
    if remaining == 0:
        job.status = "completed"
    elif job.attempt == 1:
        job.status = "queued"
        job.next_attempt_at = utcnow() + timedelta(minutes=45)
    elif job.attempt == 2:
        job.status = "queued"
        job.next_attempt_at = utcnow() + timedelta(hours=5)
    else:
        job.status = "failed"
    db.session.commit()

