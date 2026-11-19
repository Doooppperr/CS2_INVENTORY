"""管理员用户清单与待续期范围。清单只拿展示所需的字段、一次汇总本页订阅数。"""
from datetime import timedelta

from sqlalchemy import case, func, or_
from sqlalchemy.orm import load_only

from .database import db
from .entitlements import GRACE_LIFETIME, entitlement_state, grace_ends_at
from .models import Subscription, User, beijing_iso

RENEWAL_WINDOW = timedelta(days=7)


def renewal_list(args, now):
    state = args.get("user_state") or "all"
    term = args.get("q", "").strip()
    query = User.query
    if term:
        query = query.filter(User.username.icontains(term, autoescape=True))
    eligible = ((User.role != "admin") & (User.account_kind != "internal")
                & or_(User.plan.is_(None), User.plan != "permanent"))
    end = User.activation_expires_at
    states = {
        "expiring": eligible & (end > now) & (end <= now + RENEWAL_WINDOW),
        "grace": eligible & (end <= now) & (end > now - GRACE_LIFETIME),
        "expired": eligible & or_(end.is_(None), end <= now - GRACE_LIFETIME),
    }
    if state != "all" and state not in states:
        raise ValueError("未知的用户权益筛选项")
    counts = query.with_entities(
        func.count(User.id).label("all"),
        *(func.count(case((criterion, 1))).label(key) for key, criterion in states.items()),
    ).one()._mapping
    if state != "all":
        query = query.filter(states[state])
    if state in {"expiring", "grace"}:
        query = query.order_by(end, User.id)
    else:
        query = query.order_by(User.created_at.desc(), User.id.desc())
    # 不加载其他账户密码哈希、密文或每笔订阅的 Steam 目标。
    query = query.options(load_only(User.id, User.username, User.role, User.is_active,
                                    User.account_kind, User.plan, User.activated_at, User.activation_expires_at,
                                    User.monitor_limit, User.created_at, User.last_login_at))
    total = counts[state]
    per_page = 20
    pages = (total + per_page - 1) // per_page
    page = min(max(1, args.get("page", 1, type=int)), max(1, pages))
    users = query.limit(per_page).offset((page - 1) * per_page).all()
    ids = [u.id for u in users]
    subscriptions = dict(db.session.query(Subscription.user_id, func.count())
                         .filter(Subscription.user_id.in_(ids)).group_by(Subscription.user_id).all()) if ids else {}
    items = [{
        "id": u.id, "username": u.username, "role": u.role, "is_active": u.is_active,
        "created_at": beijing_iso(u.created_at), "last_login_at": beijing_iso(u.last_login_at),
        "monitor_count": subscriptions.get(u.id, 0),
        "entitlement": {"kind": u.account_kind, "plan": u.plan, "status": entitlement_state(u, now=now),
                        "activated_at": beijing_iso(u.activated_at),
                        "expires_at": beijing_iso(u.activation_expires_at), "grace_ends_at": beijing_iso(grace_ends_at(u)),
                        "monitor_limit": u.monitor_limit},
    } for u in users]
    return {"items": items, "user_state": state, "state_counts": dict(counts),
            "page": page, "pages": pages, "total": total}
