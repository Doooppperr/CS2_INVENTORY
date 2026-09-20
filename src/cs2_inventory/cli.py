from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from .app import create_app
from .database import db
from .entitlements import cleanup_lifecycle, target_daily_eligible
from .localization import repair_retained_snapshots
from .models import BEIJING_TIMEZONE, ScanBatch, ScanJob, SteamTarget, utcnow
from .services import prune_expired, state_set
from .worker import refresh_official_usage, worker_loop


def scheduled_slot_key(now: datetime | None = None) -> str:
    """北京时间六窗口槽键：00/04/08/12/16/20 点起各 4 小时一轮（R0-R5），无跨日回退。"""
    value = now or utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(BEIJING_TIMEZONE)
    round_index = local.hour // 4
    return f"{local.date().isoformat()}-R{round_index}"


def enqueue_daily(*, now: datetime | None = None) -> dict:
    cleanup_lifecycle()
    slot_key = scheduled_slot_key(now)
    existing = ScanBatch.query.filter_by(slot_key=slot_key).first()
    if existing:
        return {"batch_id": existing.id, "jobs": existing.total_jobs, "existing": True, "slot_key": slot_key}
    refresh_official_usage()
    targets = [
        target
        for target in SteamTarget.query.order_by(SteamTarget.id.asc()).all()
        if target_daily_eligible(target)
    ]
    batch = ScanBatch(
        kind="daily",
        slot_key=slot_key,
        status="running",
        total_jobs=len(targets),
        started_at=now or utcnow(),
    )
    db.session.add(batch)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        existing = ScanBatch.query.filter_by(slot_key=slot_key).one()
        return {"batch_id": existing.id, "jobs": existing.total_jobs, "existing": True, "slot_key": slot_key}
    for target in targets:
        # R2 窗口（08:00-11:59）走深度多源扫描，其余槽位走 batch 轻量路径。
        job_kind = "daily" if slot_key.endswith("-R2") else "daily_light"
        db.session.add(ScanJob(target_id=target.id, steamid=target.steamid, batch_id=batch.id, kind=job_kind))
        target.scan_status = "queued"
    if targets:
        state_set("maintenance", "1")
        state_set("maintenance_message", "每日库存快照更新进行中")
    else:
        batch.status = "completed"
        batch.finished_at = utcnow()
    db.session.commit()
    return {"batch_id": batch.id, "jobs": len(targets), "existing": False, "slot_key": slot_key}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=["init-db", "enqueue-daily", "prune", "cleanup-accounts", "worker-once", "localization-report", "repair-localized-names"],
    )
    parser.add_argument("--apply", action="store_true", help="commit trusted localization repairs")
    args = parser.parse_args(argv)
    if args.command == "worker-once":
        worker_loop(once=True)
        return 0
    app = create_app()
    with app.app_context():
        if args.command == "init-db":
            db.create_all()
            print("database initialized")
        elif args.command == "enqueue-daily":
            print(enqueue_daily())
        elif args.command == "prune":
            print(prune_expired())
        elif args.command == "cleanup-accounts":
            print(cleanup_lifecycle())
        elif args.command in {"localization-report", "repair-localized-names"}:
            result = repair_retained_snapshots(
                dry_run=not args.apply,
                language=app.config["ITEM_LANGUAGE"],
                cache_path=str(app.config["OBSERVATION_CACHE"]),
            )
            print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
