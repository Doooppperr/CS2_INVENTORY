from __future__ import annotations

import argparse

from .app import create_app
from .database import db
from .entitlements import cleanup_lifecycle, target_daily_eligible
from .localization import repair_retained_snapshots
from .models import ScanBatch, ScanJob, SteamTarget, utcnow
from .services import prune_expired, state_set
from .worker import refresh_official_usage, worker_loop


def enqueue_daily() -> dict:
    cleanup_lifecycle()
    active = ScanBatch.query.filter(ScanBatch.kind == "daily", ScanBatch.status.in_(["queued", "running"])).first()
    if active:
        return {"batch_id": active.id, "jobs": active.total_jobs, "existing": True}
    refresh_official_usage()
    targets = [
        target
        for target in SteamTarget.query.order_by(SteamTarget.id.asc()).all()
        if target_daily_eligible(target)
    ]
    batch = ScanBatch(kind="daily", status="running", total_jobs=len(targets), started_at=utcnow())
    db.session.add(batch)
    db.session.flush()
    for target in targets:
        db.session.add(ScanJob(target_id=target.id, steamid=target.steamid, batch_id=batch.id, kind="daily"))
        target.scan_status = "queued"
    if targets:
        state_set("maintenance", "1")
        state_set("maintenance_message", "每日库存快照更新进行中")
    else:
        batch.status = "completed"
        batch.finished_at = utcnow()
    db.session.commit()
    return {"batch_id": batch.id, "jobs": len(targets), "existing": False}


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
