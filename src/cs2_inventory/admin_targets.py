"""管理员全部监控：按目标分页，备注与库存仅批量读取当前页。"""
from __future__ import annotations

import json
from collections import defaultdict

from sqlalchemy import func, or_
from sqlalchemy.orm import load_only, selectinload

from .auth import can_inventory_diagnostics
from .database import db
from .inventory_views import inventory_sources, inventory_view
from .models import (
    ScanBatch,
    ScanJob,
    Snapshot,
    SteamTarget,
    Subscription,
    User,
    beijing_iso,
)
from .services import target_card_fields


def search_targets(query, term):
    if not term:
        return query
    return query.filter(or_(
        SteamTarget.persona_name.icontains(term, autoescape=True),
        SteamTarget.steamid.contains(term, autoescape=True),
        SteamTarget.subscriptions.any(Subscription.remark.icontains(term, autoescape=True)),
    ))


def _scope_id(args, key):
    value = args.get(key, type=int)
    if value is None or not 1 <= value <= 2**63-1:
        raise ValueError("筛选编号无效")
    return value


def detail_subscriber(args, target_id):
    if "subscriber_id" not in args:
        return None
    value = _scope_id(args, "subscriber_id")
    if not db.session.query(Subscription.id).filter_by(user_id=value, target_id=target_id).first():
        raise LookupError("该账号未订阅此监控对象")
    return value


def scoped_targets(args, user):
    query = search_targets(SteamTarget.query, args.get("q", "").strip())
    scope = {"failed_batch": None, "subscriber": None}
    if "failed_batch" in args:
        batch = db.session.get(ScanBatch, _scope_id(args, "failed_batch"))
        if batch is None:
            raise LookupError("这次扫描记录不存在")
        # 目标后来恢复也要保留；目标后来的失败不属于这次批次。IN 不重复计数。
        failed = db.select(ScanJob.target_id).where(ScanJob.batch_id == batch.id, ScanJob.status == "failed")
        query = query.filter(SteamTarget.id.in_(failed))
        created = beijing_iso(batch.created_at)
        scope["failed_batch"] = {"id": batch.id, "label": created[:16].replace("T", " ") + " 扫描"}
    if "subscriber_id" in args:
        customer = db.session.get(User, _scope_id(args, "subscriber_id"))
        if customer is None:
            raise LookupError("该账号不存在")
        query = query.filter(SteamTarget.subscriptions.any(Subscription.user_id == customer.id))
        scope["subscriber"] = {"id": customer.id, "username": customer.username}
    order = args.get("order", "newest")
    clock = SteamTarget.last_scan_at if can_inventory_diagnostics(user) else SteamTarget.last_success_at
    if order == "recent_scan":
        query = query.order_by(clock.desc().nulls_last())
    elif order == "subscribers":
        counts = (db.select(Subscription.target_id, func.count().label("n"))
                  .group_by(Subscription.target_id).subquery())
        query = query.outerjoin(counts, counts.c.target_id == SteamTarget.id).order_by(func.coalesce(counts.c.n, 0).desc())
    elif order != "newest":
        raise ValueError("未知的监控排序方式")
    return query.order_by(SteamTarget.created_at.desc(), SteamTarget.id.desc()), scope, order, clock.key


def target_names(targets, user, subscriber_id=None):
    """全站优先管理员备注；选中订阅者时只用其备注，否则显示原昵称或 ID。"""
    ids = [target.id for target in targets]
    by_target = defaultdict(list)
    if ids:
        records = (db.session.query(Subscription.target_id, User.id, User.username, Subscription.remark)
                   .join(User, User.id == Subscription.user_id)
                   .filter(Subscription.target_id.in_(ids))
                   .order_by((Subscription.user_id == (subscriber_id if subscriber_id is not None else user.id)).desc(), Subscription.id))
        for target_id, uid, username, remark in records:
            by_target[target_id].append((uid, username, (remark or "").strip()))
    result = {}
    for target in targets:
        subs = by_target[target.id]
        remarks = [{"username": username, "remark": name} for _, username, name in subs if name]
        if subscriber_id is None:
            primary = remarks[0]["remark"] if remarks else None
        else:
            primary = next((name for uid, _, name in subs if uid == subscriber_id and name), None)
        result[target.id] = {"label": primary or (target.persona_name or "").strip() or target.steamid,
                             "remark": primary, "remarks": remarks, "subscriber_count": len(subs)}
    return result


def admin_target_cards(targets, user, subscriber_id=None):
    if not targets:
        return []
    names = target_names(targets, user, subscriber_id)
    ids = list(names)
    histories = defaultdict(list)
    snapshots = (Snapshot.query.filter(Snapshot.target_id.in_(ids), Snapshot.coverage.notin_(["failed", "private"]))
                 .order_by(Snapshot.target_id, Snapshot.scanned_at.desc(), Snapshot.id.desc()).all())
    for row in snapshots:
        histories[row.target_id].append(row)
    plans = {key: inventory_sources(histories[key]) for key in ids}
    item_ids = set()
    for key, plan in plans.items():
        item_ids.update(plan.item_ids())
        # latest 保留原接口的单轮摘要；只在其为 API 快照时需要分区物品。
        if histories[key] and histories[key][0].id in plan.quality:
            item_ids.add(histories[key][0].id)
    if item_ids:
        Snapshot.query.options(load_only(Snapshot.id), selectinload(Snapshot.items)).filter(Snapshot.id.in_(item_ids)).all()

    diagnostics = can_inventory_diagnostics(user)
    last_jobs = {}
    if diagnostics:
        latest = db.select(func.max(ScanJob.id)).where(ScanJob.target_id.in_(ids)).group_by(ScanJob.target_id)
        for key, raw in db.session.query(ScanJob.target_id, ScanJob.result_json).filter(ScanJob.id.in_(latest)):
            if raw:
                last_jobs[key] = json.loads(raw).get("diagnostics", {})
    result = []
    for target in targets:
        key = target.id
        history = histories[key]
        card = target_card_fields(target, history[0] if history else None, names[key]["subscriber_count"])
        card.update(names[key])
        card.update(inventory_view(key, None, include_items=False, sources=plans[key]))
        if diagnostics:
            card["diagnostics"] = dict(plans[key].last_quality)
            if key in last_jobs:
                card["diagnostics"]["last_job"] = last_jobs[key]
        result.append(card)
    return result
