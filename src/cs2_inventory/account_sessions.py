"""本浏览器验证过的账号持续可用，退出、移除和重新验证时轮换设备凭据。"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import timedelta, timezone

from flask import abort, current_app, jsonify, request, session
from flask.sessions import SecureCookieSessionInterface
from itsdangerous import BadSignature
from sqlalchemy.dialects.sqlite import insert

from .database import db
from .models import SystemState, User, utcnow

DEVICE = "account_device"
REMEMBERED = "remembered"
PREFIX = "authsaved:"
OLD_PREFIX = "authdev:"
LEGACY = "authold:"
BROWSER_LIFETIME = timedelta(days=400)
RENEW_AFTER = timedelta(days=30)


def _digest(value):
    return (
        base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest())
        .decode()
        .rstrip("=")
    )


class RememberedSessionInterface(SecureCookieSessionInterface):
    """旧 session 及匿名 CSRF 沿用 Flask；持久账号使用受签名的独立代际 Cookie。"""

    def open_session(self, app, req):
        base = self.get_cookie_name(app)
        prefix = base + ".remember."
        signer = self.get_signing_serializer(app)
        if not signer:
            return None
        old = super().open_session(app, req)
        if old.get(REMEMBERED):  # 持久凭据只从其代际名读取。
            old = self.session_class()
        browser = None
        visible, retired = {}, set()
        for name, value in req.cookies.items():
            if not name.startswith(prefix):
                continue
            try:
                data, issued = signer.loads(value, return_timestamp=True)
            except BadSignature:
                continue
            if not data.get(REMEMBERED) or not data.get(DEVICE):
                continue
            digest = _digest(data[DEVICE])
            if name != prefix + digest:
                continue
            key = PREFIX + digest
            if db.session.get(SystemState, key) is None:
                retired.add(name)
                continue
            # 两个匿名标签页同时首次登录时可能留下两个独立有效代际。
            # 保留本次请求实际出示的集合，以便显式退出时一起撤销。
            visible[name] = key
            if browser is None:
                browser = self.session_class(data)
                browser.cookie_name, browser.issued_at = name, issued
        if browser is None:
            browser = old
        browser.old_state = dict(old)
        browser.visible, browser.retired = visible, retired
        return browser

    def save_session(self, app, browser, response):
        base = self.get_cookie_name(app)
        old_name = getattr(browser, "cookie_name", None)
        name = (
            base + ".remember." + _digest(browser[DEVICE])
            if browser.get(REMEMBERED)
            else None
        )
        attrs = dict(
            path=self.get_cookie_path(app),
            domain=self.get_cookie_domain(app),
            httponly=self.get_cookie_httponly(app),
            secure=self.get_cookie_secure(app),
            samesite=self.get_cookie_samesite(app),
            partitioned=self.get_cookie_partitioned(app),
        )
        retired = set(getattr(browser, "retired", ()))
        if old_name and old_name != name:
            retired.add(old_name)
        if name and getattr(browser, "drop_base", False):
            retired.add(base)
        for old in retired:
            response.delete_cookie(old, **attrs)
            response.vary.add("Cookie")
        if not name:
            return super().save_session(app, browser, response)
        if browser.accessed:
            response.vary.add("Cookie")
        # 普通读取不重发。即便恰好到续期日，慢响应也只写回旧代际名，
        # 不会把用户刚切换到的账号覆盖掉；被轮换的旧名在服务器已失效。
        now = utcnow()
        due = (
            getattr(browser, "verified", False)
            and now - browser.issued_at >= RENEW_AFTER
            if old_name
            else False
        )
        if browser.modified or due:
            value = self.get_signing_serializer(app).dumps(dict(browser))
            response.set_cookie(name, value, expires=now + BROWSER_LIFETIME, **attrs)
            response.vary.add("Cookie")


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


def _key(prefix=PREFIX):
    return prefix + _digest(session[DEVICE]) if DEVICE in session else None


def _legacy_key(state=None):
    state = session if state is None else state
    csrf = state.get("csrf_token")
    return (
        LEGACY + _digest(csrf)
        if state.get("_permanent") and csrf and state.get("user_id")
        else None
    )


def _grants(prefix=PREFIX):
    key = _key(prefix)
    row = db.session.get(SystemState, key) if key else None
    return json.loads(row.value) if row else {}


def _valid(user, version):
    return bool(user and user.is_active and version and version == _version(user))


def _error(status, message):
    response = jsonify(error=message)
    response.status_code = status
    abort(response)


def _retire(prefix=PREFIX):
    return (
        db.session.query(SystemState)
        .filter_by(key=_key(prefix))
        .delete(synchronize_session=False)
        == 1
    )


def _consume_device():
    if not _retire():
        _error(409, "账号状态已更新，请刷新页面")


def _consume_legacy(state=None):
    key = _legacy_key(state)
    if not key:
        return False
    stmt = (
        insert(SystemState)
        .values(key=key, value="1")
        .on_conflict_do_nothing(index_elements=["key"])
    )
    return db.session.execute(stmt).rowcount == 1


def _legacy_grant(user):
    # 单账号旧格式没有设备记录；严格按原密码登录与签名的 24 小时窗口迁移一次。
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
    return _version(user)


def _activate(grants, user, *, keep_csrf=False):
    token = secrets.token_urlsafe(32)
    db.session.add(
        SystemState(
            key=PREFIX + _digest(token), value=json.dumps(grants, separators=(",", ":"))
        )
    )
    db.session.commit()
    csrf = session.get("csrf_token") if keep_csrf else None
    if not session.get(REMEMBERED):
        session.drop_base = True
    session.clear()
    session.permanent = True
    session["user_id"] = user.id
    session[DEVICE] = token
    session[REMEMBERED] = True
    if csrf:
        session["csrf_token"] = csrf


def authenticated_user():
    ident = session.get("user_id")
    if ident is None:
        return None
    user = db.session.get(User, ident)
    if session.get(REMEMBERED):
        session.verified = _valid(user, _grants().get(str(ident)))
        return user if session.verified else None
    if DEVICE in session:
        old = _grants(OLD_PREFIX)
        now = utcnow().timestamp()
        grants = {
            key: version
            for key, (expiry, version) in old.items()
            if expiry > now and _valid(db.session.get(User, int(key)), version)
        }
        if str(ident) not in grants or not _retire(OLD_PREFIX):
            return None
    else:
        version = _legacy_grant(user)
        if not version or not _consume_legacy():
            return None
        grants = {str(ident): version}
    _activate(grants, user, keep_csrf=True)
    return user


def password_login(user):
    previous = authenticated_user()
    grants = _grants() if previous else {}
    if session.get(REMEMBERED):
        if previous:
            _consume_device()
        else:
            _retire()
    elif DEVICE in session:
        _retire(OLD_PREFIX)
    elif _legacy_key():
        _consume_legacy()
    grants[str(user.id)] = _version(user)
    user.last_login_at = utcnow()
    _activate(grants, user)


def account_list(user):
    grants = _grants()
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
    version = grants.get(str(user_id))
    user = db.session.get(User, user_id) if version else None
    if not _valid(user, version):
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
    _consume_device()
    grants[str(user.id)] = _version(user)
    _activate(grants, user, keep_csrf=True)


def sign_out():
    _consume_device()
    # 只撤销此浏览器出示的代际，不影响同一用户的其他设备。
    keys = set(session.visible.values())
    old = session.old_state
    if old.get(DEVICE):
        keys.add(OLD_PREFIX + _digest(old[DEVICE]))
    elif _legacy_key(old):
        _consume_legacy(old)
    if keys:
        SystemState.query.filter(SystemState.key.in_(keys)).delete(
            synchronize_session=False
        )
    db.session.commit()
    session.retired.update(session.visible)
    session.clear()


def prune_devices(now):
    # 只有旧 24 小时凭据及其一次性迁移标记按时间清理。
    cutoff = now - current_app.permanent_session_lifetime
    SystemState.query.filter(
        SystemState.key.startswith(OLD_PREFIX) | SystemState.key.startswith(LEGACY),
        SystemState.updated_at < cutoff,
    ).delete(synchronize_session=False)
