"""发布停写期间回填现有证据；可重复运行，不调用上游 API。"""
from __future__ import annotations

import json
from datetime import timedelta

from .app import create_app
from .dashboard import save_daily
from .database import db
from .models import (
    BEIJING_TIMEZONE,
    DailyObservation,
    ScanBatch,
    ScanJob,
    Snapshot,
    SteamTarget,
    TargetAssetHistory,
    utcnow,
)
from .observation import evidence_observation, private_errors, reliable_id, row_evidence


def backfill():
    counts = {"assets": 0, "days": 0, "targets": 0}
    cutoff = (utcnow().astimezone(BEIJING_TIMEZONE).date() - timedelta(days=7)).isoformat()
    for target in SteamTarget.query.order_by(SteamTarget.id):
        history = {h.asset_key: h for h in TargetAssetHistory.query.filter_by(target_id=target.id)}
        snapshots = Snapshot.query.filter_by(target_id=target.id).order_by(Snapshot.scanned_at, Snapshot.id).all()
        for snapshot in snapshots:
            if snapshot.coverage in {"failed", "private"} or private_errors(json.loads(snapshot.errors_json or "[]")):
                continue
            for item in snapshot.items:
                if not reliable_id(item.asset_key) or evidence_observation(row_evidence(item)) == "cached":
                    continue
                seen = min(item.first_seen_at or snapshot.scanned_at, snapshot.scanned_at)
                row = history.get(item.asset_key)
                if row:
                    row.first_seen_at = min(row.first_seen_at, seen)
                    row.last_seen_at = max(row.last_seen_at, snapshot.scanned_at)
                else:
                    row = TargetAssetHistory(target_id=target.id, asset_key=item.asset_key,
                                             first_seen_at=seen, last_seen_at=snapshot.scanned_at)
                    history[item.asset_key] = row
                    db.session.add(row)
                    counts["assets"] += 1
        # 不重写原始快照；日报提取时使用永久历史纠正 first_seen。
        db.session.flush()
        jobs = ScanJob.query.join(ScanBatch, ScanJob.batch_id == ScanBatch.id).filter(
            ScanJob.target_id == target.id, ScanJob.kind == "daily", ScanJob.finished_at.is_not(None),
            ScanBatch.slot_key >= cutoff, ScanBatch.slot_key.like("%-R2")).order_by(ScanJob.finished_at).all()
        for job in jobs:
            if not job.started_at:
                continue
            matches = [s for s in snapshots if job.started_at <= s.scanned_at <= job.finished_at]
            if len(matches) != 1:
                continue  # 没有唯一作业关联时不猜测历史深度基线。
            snapshot = matches[0]
            day = db.session.get(ScanBatch, job.batch_id).slot_key[:10]
            if db.session.get(DailyObservation, (target.id, day)):
                continue
            errors = json.loads(snapshot.errors_json or "[]")
            status = "private" if private_errors(errors) else "failed" if snapshot.coverage == "failed" else "partial"
            save_daily(target, day=day, scanned_at=snapshot.scanned_at, status=status,
                       snapshot=snapshot if status == "partial" else None)
            counts["days"] += 1
        if snapshots and target.scan_status not in {"queued", "running", "scanning"}:
            last = snapshots[-1]
            if private_errors(json.loads(last.errors_json or "[]")):
                target.scan_status, target.last_error = "private", "已私密"
            elif last.coverage == "failed":
                target.scan_status, target.last_error = "failed", "扫描失败，保留上次有效库存"
            elif last.coverage not in {"ok", "complete"}:
                target.scan_status, target.last_error = "partial", "数据不完整，缺失物品尚未确认移除"
            valid = [s for s in snapshots if s.coverage not in {"failed", "private"} and not private_errors(json.loads(s.errors_json or "[]"))]
            target.last_success_at = valid[-1].scanned_at if valid else None
            counts["targets"] += 1
        db.session.commit()
    return counts


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        print(json.dumps(backfill(), ensure_ascii=False))
