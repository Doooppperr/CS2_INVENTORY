"""主动查询校验与持久队列复用；调用方必须校验超级管理员和 CSRF。"""
import json
import re
from datetime import timedelta

from .database import db
from .entitlements import lock_user
from .models import ScanJob, SteamTarget, utcnow
from .services import queue_job


def validate_entries(body):
    if not isinstance(body, dict):
        raise ValueError("请求内容必须为对象")
    entries = body.get("items", [body])
    if not isinstance(entries, list) or not entries or len(entries) > 200:
        raise ValueError("请输入查询列表，每批最多 20 个不同目标")
    unique = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("每条查询必须包含 SteamID64")
        steamid = str(entry.get("steamid") or "").strip()
        alias = str(entry.get("alias") or "").strip()
        if not re.fullmatch(r"7656119\d{10}", steamid):
            raise ValueError("请输入有效的 SteamID64")
        if len(alias) > 50 or "\n" in alias or "\r" in alias:
            raise ValueError("别称最多 50 个字符且不能换行")
        unique.setdefault(steamid, alias)
    if len(unique) > 20:
        raise ValueError("每批最多 20 个不同目标")
    return list(unique.items())


def enqueue_queries(user, entries):
    # 写锁先于活动任务检查；同一 SQLite 中的所有提交进程共享此锁。
    lock_user(user.id)
    result = []
    for steamid, alias in entries:
        target = SteamTarget.query.filter_by(steamid=steamid).first()
        query = ScanJob.query.filter(ScanJob.steamid == steamid, ScanJob.status.in_(["queued", "running"]))
        if target:
            query = query.filter(ScanJob.target_id == target.id, ScanJob.kind != "instant")
        else:
            query = query.filter(ScanJob.target_id.is_(None), ScanJob.kind == "instant",
                                 ScanJob.expires_at > utcnow())
        job = query.order_by(ScanJob.id).first()
        reused = job is not None
        if not job:
            if target:
                job = queue_job(target, kind="manual", requested_by=user.id)
            else:
                job = ScanJob(steamid=steamid, requested_by=user.id, kind="instant",
                              expires_at=utcnow() + timedelta(hours=1))
                db.session.add(job)
            job.result_json = json.dumps({"query_alias": alias}, ensure_ascii=False)
            db.session.flush()
        result.append({"steamid": steamid, "alias": alias, "job_id": job.id,
                       "target_id": job.target_id, "reused": reused})
    return result
