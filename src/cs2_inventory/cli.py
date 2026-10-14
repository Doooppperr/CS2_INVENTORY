from __future__ import annotations

import argparse
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from .app import create_app
from .database import db
from .entitlements import cleanup_lifecycle, target_daily_eligible
from .localization import repair_retained_snapshots
from .models import ScanBatch, ScanJob, SteamTarget, utcnow
from .scheduling import SCHEDULED_ROUNDS, scheduled_slot_key
from .services import prune_expired, state_set
from .worker import refresh_official_usage, worker_loop


def enqueue_daily(*, now: datetime | None = None) -> dict:
    cleanup_lifecycle()
    slot_key = scheduled_slot_key(now)
    if slot_key is None:
        return {"jobs": 0, "skipped": True, "slot_key": None, "reason": "北京时间 08:00 前不补扫"}
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
        # 08:00 深度多源扫描，20:00 单库存轻量扫描。
        # （parse=1 + try_first_seven_days_blocked_items=1，可直接发现交易保护中的新物品）。
        job_kind = next(kind for _, suffix, kind in SCHEDULED_ROUNDS if slot_key.endswith(f"-{suffix}"))
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
