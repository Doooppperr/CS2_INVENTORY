from __future__ import annotations

import functools
import re
import secrets
import threading
import time
from collections import defaultdict, deque

from flask import abort, jsonify, request, session

from .database import db
from .models import User

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
_attempts: dict[str, deque[float]] = defaultdict(deque)
_attempt_lock = threading.Lock()


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def require_csrf() -> None:
    supplied = request.headers.get("X-CSRF-Token", "")
    if not secrets.compare_digest(supplied, str(session.get("csrf_token") or "")):
        abort(400, description="CSRF 校验失败")


def rate_limited(key: str, *, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    with _attempt_lock:
        queue = _attempts[key]
        while queue and queue[0] <= now - window_seconds:
            queue.popleft()
        if len(queue) >= limit:
            return True
        queue.append(now)
        return False


def current_user() -> User | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = db.session.get(User, int(user_id))
    if not user or not user.is_active:
        session.clear()
        return None
    return user


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"error": "请先登录"}), 401
        return view(user, *args, **kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"error": "请先登录"}), 401
        if user.role != "admin":
            return jsonify({"error": "需要管理员权限"}), 403
        return view(user, *args, **kwargs)
    return wrapped


def user_json(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }
