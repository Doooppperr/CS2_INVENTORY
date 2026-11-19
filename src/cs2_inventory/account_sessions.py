"""本浏览器已验证的账号。密码登录建立固定期限的授权，切换只选择已有授权。"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import timezone

from flask import abort, current_app, jsonify, request, session
from itsdangerous import BadSignature
from sqlalchemy.dialects.sqlite import insert

from .database import db
from .models import SystemState, User, utcnow

DEVICE = "account_device"
PREFIX = "authdev:"
LEGACY = "authold:"


def _digest(value):
    return (
        base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest())
        .decode()
        .rstrip("=")
    )


def _epoch(dt):
    return (
        dt.replace(tzinfo=timezone.utc).timestamp()
        if dt.tzinfo is None
        else dt.timestamp()
    )


def _version(user):
    # 账号重建、密码重置及权限变化均需要新验证。不在授权表中复制密码散列。
    changed = _epoch(user.password_changed_at) if user.password_changed_at else ""
    return _digest(
        f"{_epoch(user.created_at)}|{changed}|{user.role}|{user.password_hash}"
    )


def _key():
    return PREFIX + _digest(session[DEVICE]) if DEVICE in session else None


def _legacy_key():
    csrf = session.get("csrf_token")
    return (
        LEGACY + _digest(csrf)
        if session.permanent and csrf and session.get("user_id")
        else None
    )


def _grants():
    key = _key()
    row = db.session.get(SystemState, key) if key else None
    return json.loads(row.value) if row else {}


def _valid(user, grant):
    return bool(
        user
        and user.is_active
        and grant
        and grant[0] > utcnow().timestamp()
        and grant[1] == _version(user)
    )


def authenticated_user():
    ident = session.get("user_id")
    if ident is None:
        return None
    user = db.session.get(User, int(ident))
    if DEVICE in session:
        return user if _valid(user, _grants().get(str(ident))) else None
    old = _legacy_key()
    if old:
        return (
            user
            if not db.session.get(SystemState, old) and _legacy_grant(user)
            else None
        )
    return None


def _error(status, message):
    response = jsonify(error=message)
    response.status_code = status
    abort(response)


def _consume_device():
    key = _key()
    if (
        key
        and db.session.query(SystemState)
        .filter_by(key=key)
        .delete(synchronize_session=False)
        != 1
    ):
        _error(409, "账号状态已更新，请刷新页面")


def _consume_legacy():
    key = _legacy_key()
    if not key:
        return False
    stmt = (
        insert(SystemState)
        .values(key=key, value="1")
        .on_conflict_do_nothing(index_elements=["key"])
    )
    return db.session.execute(stmt).rowcount == 1


def _legacy_grant(user):
    # 旧版本密码登录同时签发永久 Cookie、CSRF 并写 last_login_at。两处时间
    # 确实匹配才沿用旧账号，而且到期时间仍从原密码登录计算。
    if not user or not user.is_active or not user.last_login_at or not _legacy_key():
        return None
    cookie = request.cookies.get(current_app.config["SESSION_COOKIE_NAME"])
    signer = current_app.session_interface.get_signing_serializer(current_app)
    if not cookie:
        return None
    ttl = current_app.permanent_session_lifetime.total_seconds()
    try:
        _, signed_at = signer.loads(cookie, max_age=int(ttl), return_timestamp=True)
    except BadSignature:
        return None
    login_time, issued = _epoch(user.last_login_at), signed_at.timestamp()
    if int(login_time) != issued:
        return None
    if user.password_changed_at and _epoch(user.password_changed_at) >= issued:
        return None
    if _epoch(user.created_at) >= issued or issued + ttl <= utcnow().timestamp():
        return None
    return [issued + ttl, _version(user)]


def _activate(grants, user, *, keep_csrf=False):
    token = secrets.token_urlsafe(32)
    db.session.add(
        SystemState(
            key=PREFIX + _digest(token), value=json.dumps(grants, separators=(",", ":"))
        )
    )
    db.session.commit()
    csrf = session.get("csrf_token") if keep_csrf else None
    session.clear()
    session.permanent = True
    session["user_id"] = user.id
    session[DEVICE] = token
    if csrf:
        session["csrf_token"] = csrf


def password_login(user):
    ident = session.get("user_id")
    previous = db.session.get(User, ident) if ident is not None else None
    grants = _grants()
    if DEVICE in session:
        if _valid(previous, grants.get(str(ident))):
            _consume_device()
        else:
            key = _key()
            db.session.query(SystemState).filter_by(key=key).delete(
                synchronize_session=False
            )
            grants = {}
    else:
        old = _legacy_grant(previous)
        if _consume_legacy() and old:
            grants[str(ident)] = old
    now = utcnow()
    grants = {k: v for k, v in grants.items() if v[0] > now.timestamp()}
    grants[str(user.id)] = [
        now.timestamp() + current_app.permanent_session_lifetime.total_seconds(),
        _version(user),
    ]
    user.last_login_at = now
    _activate(grants, user)


def account_list(user):
    grants = _grants()
    if not grants:
        return [{"id": user.id, "username": user.username}]
    users = (
        db.session.query(User)
        .filter(User.id.in_([int(i) for i in grants]))
        .order_by(User.username)
    )
    return [
        {"id": u.id, "username": u.username}
        for u in users
        if _valid(u, grants[str(u.id)])
    ]


def switch_account(user_id):
    grants = _grants()
    grant = grants.get(str(user_id))
    user = db.session.get(User, user_id) if grant else None
    if not _valid(user, grant):
        _error(403, "请重新登录该账号")
    _consume_device()
    _activate(grants, user)
    return user


def forget(user, user_id):
    if user_id == user.id:
        abort(400, description="请先切换到其他账号")
    grants = _grants()
    if grants.pop(str(user_id), None) is not None:
        _consume_device()
        _activate(grants, user, keep_csrf=True)


def retain_after_password_change(user):
    grants = _grants()
    if DEVICE in session:
        _consume_device()
        grants[str(user.id)][1] = _version(user)
    else:
        _consume_legacy()
        grants[str(user.id)] = [
            utcnow().timestamp()
            + current_app.permanent_session_lifetime.total_seconds(),
            _version(user),
        ]
    _activate(grants, user, keep_csrf=True)


def sign_out():
    if DEVICE in session:
        _consume_device()
    else:
        _consume_legacy()
    db.session.commit()
    session.clear()


def prune_devices(now):
    cutoff = now - current_app.permanent_session_lifetime
    SystemState.query.filter(
        SystemState.key.startswith(PREFIX) | SystemState.key.startswith(LEGACY),
        SystemState.updated_at < cutoff,
    ).delete(synchronize_session=False)
