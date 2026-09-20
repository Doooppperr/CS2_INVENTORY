from __future__ import annotations

import collections
import json
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from typing import Any

from flask import current_app

from . import inventory_engine
from .app import create_app
from .database import db
from .entitlements import scan_job_eligible
from .inventory_engine import (
    fetch_steamwebapi_batch,
    run_lightweight_query,
    run_max_coverage_query,
)
from .localization import process_localization_job
from .models import (
    LocalizationJob,
    QuotaUsage,
    ScanBatch,
    ScanJob,
    SteamTarget,
    beijing_iso,
    utcnow,
)
from .services import prune_expired, quota_allows_scan, state_set, store_snapshot
from .unified import public_payload, unify_inventory

_claim_lock = threading.Lock()


class SlidingWindowLimiter:
    def __init__(self, limit: int = 18, seconds: int = 60):
        self.limit = limit
        self.seconds = seconds
        self.lock = threading.Lock()
        self.events: collections.deque[float] = collections.deque()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                while self.events and self.events[0] <= now - self.seconds:
                    self.events.popleft()
                if len(self.events) < self.limit:
                    self.events.append(now)
                    return
                sleep_for = max(0.05, self.events[0] + self.seconds - now)
            time.sleep(sleep_for)


RATE_LIMITER = SlidingWindowLimiter()


def profile_refresh_due(updated_at, *, days: int) -> bool:
    """Handle SQLite datetimes, which are returned without tzinfo."""
    if updated_at is None:
        return True
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return updated_at < utcnow() - timedelta(days=days)


def recover_interrupted_jobs() -> int:
    jobs = ScanJob.query.filter_by(status="running").all()
    for job in jobs:
        job.status = "queued"
        job.started_at = None
        if job.target_id:
            target = db.session.get(SteamTarget, job.target_id)
            if target:
                target.scan_status = "queued"
    localization_jobs = LocalizationJob.query.filter_by(status="running").all()
    for job in localization_jobs:
        job.status = "queued"
    db.session.commit()
    return len(jobs) + len(localization_jobs)


def fetch_persona_name(steamid: str) -> str | None:
    key = current_app.config["STEAMWEBAPI_KEY"]
    if not key:
        return None
    url = "https://www.steamwebapi.com/steam/api/profile?" + urllib.parse.urlencode({"key": key, "steam_id": steamid})
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            payload = json.loads(response.read())
        candidates = [payload]
        if isinstance(payload, list):
            candidates = payload
        elif isinstance(payload, dict):
            for key_name in ("profile", "data", "player", "response"):
                value = payload.get(key_name)
                if isinstance(value, dict):
                    candidates.append(value)
        for row in candidates:
            if not isinstance(row, dict):
                continue
            for field in ("personaname", "persona_name", "name", "steam_name"):
                if row.get(field):
                    return str(row[field])[:255]
    except Exception:
        return None
    return None


def refresh_official_usage() -> dict:
    key = current_app.config["STEAMWEBAPI_KEY"]
    if current_app.config.get("TESTING"):
        result = {"available": False, "error": "testing"}
    elif not key:
        result = {"available": False, "error": "API Key 未配置"}
    else:
        url = "https://www.steamwebapi.com/account/me?" + urllib.parse.urlencode({"key": key})
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                payload = json.loads(response.read())
            result = {"available": True, "payload": payload, "checked_at": beijing_iso(utcnow())}
        except Exception as exc:
            result = {"available": False, "error": str(exc)[:500], "checked_at": beijing_iso(utcnow())}
    state_set("official_quota_json", json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    db.session.commit()
    return result


def estimate_inventory_credits(result: dict) -> int:
    requests = 0
    for value in (result.get("sources") or {}).values():
        if isinstance(value, dict):
            requests += int(value.get("provider_requests", 0) or 0)
    return (requests * current_app.config["INVENTORY_CREDITS_PER_REQUEST"]) or (
        current_app.config["REQUESTS_PER_SCAN"]
        * current_app.config["INVENTORY_CREDITS_PER_REQUEST"]
    )


def claim_next_job() -> int | None:
    with _claim_lock:
        job = ScanJob.query.filter_by(status="queued").order_by(ScanJob.created_at.asc(), ScanJob.id.asc()).first()
        if not job:
            return None
        job.status = "running"
        job.started_at = utcnow()
        if job.target_id:
            target = db.session.get(SteamTarget, job.target_id)
            if target:
                target.scan_status = "scanning"
        db.session.commit()
        return job.id


def claim_next_localization_job() -> int | None:
    with _claim_lock:
        job = (
            LocalizationJob.query.filter(
                LocalizationJob.status == "queued",
                LocalizationJob.next_attempt_at <= utcnow(),
            )
            .order_by(LocalizationJob.next_attempt_at.asc(), LocalizationJob.id.asc())
            .first()
        )
        if not job:
            return None
        job.status = "running"
        db.session.commit()
        return job.id


def claim_batch_group(max_ids: int = 20) -> list[int] | None:
    """Claim up to max_ids queued daily_light jobs for one batched run."""
    with _claim_lock:
        jobs = (
            ScanJob.query.filter_by(status="queued", kind="daily_light")
            .order_by(ScanJob.created_at.asc(), ScanJob.id.asc())
            .limit(max_ids)
            .all()
        )
        if not jobs:
            return None
        job_ids = []
        for job in jobs:
            job.status = "running"
            job.started_at = utcnow()
            job_ids.append(job.id)
            if job.target_id:
                target = db.session.get(SteamTarget, job.target_id)
                if target:
                    target.scan_status = "scanning"
        db.session.commit()
        return job_ids


def claim_next_work() -> tuple[str, Any] | None:
    scan_id = claim_next_job()
    if scan_id is not None:
        return ("scan", scan_id)
    group_ids = claim_batch_group()
    if group_ids is not None:
        return ("scan_group", group_ids)
    localization_id = claim_next_localization_job()
    if localization_id is not None:
        return ("localization", localization_id)
    return None


def finish_batch(batch_id: int | None) -> None:
    if not batch_id:
        return
    batch = db.session.get(ScanBatch, batch_id)
    if not batch:
        return
    jobs = ScanJob.query.filter_by(batch_id=batch.id).all()
    batch.completed_jobs = sum(job.status == "completed" for job in jobs)
    batch.failed_jobs = sum(job.status == "failed" for job in jobs)
    if jobs and all(job.status in {"completed", "failed", "cancelled"} for job in jobs):
        batch.status = "completed" if batch.failed_jobs == 0 else "completed_with_errors"
        batch.finished_at = utcnow()
        other_active = ScanBatch.query.filter(
            ScanBatch.id != batch.id,
            ScanBatch.kind == "daily",
            ScanBatch.status.in_(["queued", "running"]),
        ).first()
        if other_active is None:
            state_set("maintenance", "0")
            state_set("maintenance_message", "")
        prune_expired()
    db.session.commit()


def process_job(job_id: int) -> None:
    job = db.session.get(ScanJob, job_id)
    if not job:
        return
    if not scan_job_eligible(job):
        job.status = "cancelled"
        job.error = "监控账号权益已到期"
        job.finished_at = utcnow()
        db.session.commit()
        finish_batch(job.batch_id)
        return
    is_admin_job = job.kind in {"manual", "instant"}
    if not quota_allows_scan(admin=is_admin_job):
        job.status = "failed"
        job.error = "Inventory API 工作预算不足"
        job.finished_at = utcnow()
        if job.target_id:
            target = db.session.get(SteamTarget, job.target_id)
            if target:
                target.scan_status = "failed"
                target.last_error = job.error
                target.last_scan_at = utcnow()
        db.session.commit()
        finish_batch(job.batch_id)
        return

    try:
        inventory_engine.REQUEST_THROTTLE = RATE_LIMITER.acquire
        result = run_max_coverage_query(
            job.steamid,
            key=current_app.config["STEAMWEBAPI_KEY"],
            language=current_app.config["ITEM_LANGUAGE"],
            timeout=120,
            trading_samples=3,
            normal_samples=1,
            include_mode1=True,
            include_parse1=True,
            parse1_samples=2,
            include_public=True,
            observation_cache_path=current_app.config["OBSERVATION_CACHE"],
        )
        unified = unify_inventory(result)
        credits = estimate_inventory_credits(result)
        db.session.add(QuotaUsage(endpoint="inventory", credits=credits, source=job.kind))
        if job.target_id:
            target = db.session.get(SteamTarget, job.target_id)
            if not target:
                raise RuntimeError("监控目标已删除")
            if not target.persona_name or profile_refresh_due(
                target.profile_updated_at, days=current_app.config["PROFILE_REFRESH_DAYS"]
            ):
                target.persona_name = fetch_persona_name(target.steamid) or target.persona_name
                target.profile_updated_at = utcnow()
            store_snapshot(target, unified)
            job.result_json = json.dumps({"target_id": target.id}, ensure_ascii=False)
        else:
            job.result_json = json.dumps(
                public_payload(unified, scanned_at=beijing_iso(utcnow())),
                ensure_ascii=False,
            )
        job.status = "completed"
        job.finished_at = utcnow()
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        job = db.session.get(ScanJob, job_id)
        if not job:
            return
        job.status = "failed"
        job.error = str(exc)[:2000]
        job.finished_at = utcnow()
        if job.target_id:
            target = db.session.get(SteamTarget, job.target_id)
            if target:
                target.scan_status = "failed"
                target.last_error = job.error
                target.last_scan_at = utcnow()
        db.session.commit()
    finally:
        fresh = db.session.get(ScanJob, job_id)
        finish_batch(fresh.batch_id if fresh else None)


def _finish_job(job_id: int, *, status: str, error: str | None = None) -> None:
    """Mark a claimed job as failed without losing the target state update."""
    job = db.session.get(ScanJob, job_id)
    if not job:
        return
    job.status = status
    if error:
        job.error = error[:2000]
    job.finished_at = utcnow()
    if job.target_id:
        target = db.session.get(SteamTarget, job.target_id)
        if target:
            target.scan_status = "failed" if status == "failed" else target.scan_status
            if status == "failed":
                target.last_error = job.error
            target.last_scan_at = utcnow()
    db.session.commit()


def process_batch_group(job_ids: list[int]) -> None:
    """Run one batched lightweight scan group: one batch call per 20 SteamIDs."""
    jobs = [db.session.get(ScanJob, job_id) for job_id in job_ids]
    jobs = [job for job in jobs if job is not None and job.status == "running"]
    if not jobs:
        return

    steamids: list[str] = []
    for job in jobs:
        if job.steamid not in steamids:
            steamids.append(job.steamid)

    try:
        inventory_engine.REQUEST_THROTTLE = RATE_LIMITER.acquire
        items_by_steamid: dict[str, list] = {}
        provider_requests = 0
        for chunk_start in range(0, len(steamids), 20):
            chunk = steamids[chunk_start:chunk_start + 20]
            chunk_items, chunk_errors, chunk_attempts = fetch_steamwebapi_batch(
                chunk,
                key=current_app.config["STEAMWEBAPI_KEY"],
                language=current_app.config["ITEM_LANGUAGE"],
                timeout=120,
            )
            provider_requests += chunk_attempts
            for error in chunk_errors:
                print(f"[worker] batch error: {error}")
            items_by_steamid.update(chunk_items)
            if chunk_attempts:
                db.session.add(
                    QuotaUsage(
                        endpoint="inventory",
                        credits=chunk_attempts * current_app.config["INVENTORY_CREDITS_PER_REQUEST"],
                        source="daily_light",
                    )
                )
                db.session.commit()
    except Exception as exc:
        db.session.rollback()
        for job_id in job_ids:
            _finish_job(job_id, status="failed", error=f"batch 请求失败: {exc}")
        for job in jobs:
            finish_batch(job.batch_id)
        return

    completed = []
    for job in jobs:
        job = db.session.get(ScanJob, job.id)
        if job is None or job.status != "running":
            continue
        batch_items = items_by_steamid.get(job.steamid)
        if batch_items is None:
            _finish_job(job.id, status="failed", error="batch 响应缺少该 SteamID")
            finish_batch(job.batch_id)
            continue
        try:
            result = run_lightweight_query(
                job.steamid,
                batch_items,
                key=current_app.config["STEAMWEBAPI_KEY"],
                language=current_app.config["ITEM_LANGUAGE"],
                timeout=120,
                observation_cache_path=current_app.config["OBSERVATION_CACHE"],
                batch_provider_requests=0,  # QuotaUsage per chunk is recorded by the group executor.
            )
            unified = unify_inventory(result)
            if job.target_id:
                target = db.session.get(SteamTarget, job.target_id)
                if not target:
                    raise RuntimeError("监控目标已删除")
                if not target.persona_name or profile_refresh_due(
                    target.profile_updated_at, days=current_app.config["PROFILE_REFRESH_DAYS"]
                ):
                    target.persona_name = fetch_persona_name(target.steamid) or target.persona_name
                    target.profile_updated_at = utcnow()
                store_snapshot(target, unified)
                job.result_json = json.dumps({"target_id": target.id}, ensure_ascii=False)
            else:
                job.result_json = json.dumps(
                    public_payload(unified, scanned_at=beijing_iso(utcnow())),
                    ensure_ascii=False,
                )
            job.status = "completed"
            job.finished_at = utcnow()
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            _finish_job(job.id, status="failed", error=str(exc))
        completed.append(job.id)

    for job_id in completed:
        fresh = db.session.get(ScanJob, job_id)
        finish_batch(fresh.batch_id if fresh else None)


def worker_loop(*, once: bool = False) -> None:
    app = create_app()
    with app.app_context():
        recover_interrupted_jobs()
        if once:
            while True:
                work = claim_next_work()
                if work is None:
                    return
                _process_work(*work)
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = set()
            while True:
                futures = {future for future in futures if not future.done()}
                while len(futures) < 2:
                    work = claim_next_work()
                    if work is None:
                        break
                    futures.add(executor.submit(_process_with_context, app, *work))
                time.sleep(1 if futures else 3)


def _process_work(kind: str, job_id: Any) -> None:
    if kind == "scan":
        process_job(job_id)
    elif kind == "scan_group":
        process_batch_group(job_id)
    else:
        process_localization_job(job_id)


def _process_with_context(app, kind: str, job_id: Any) -> None:
    with app.app_context():
        _process_work(kind, job_id)


if __name__ == "__main__":
    worker_loop()
