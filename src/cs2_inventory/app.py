from __future__ import annotations

import json
from datetime import timedelta

from flask import Flask, jsonify, render_template, request, session
from sqlalchemy import func, text
from werkzeug.security import check_password_hash

from .auth import (
    USERNAME_RE,
    admin_required,
    csrf_token,
    current_user,
    login_required,
    rate_limited,
    require_csrf,
    user_json,
)
from .config import Config
from .database import db
from .models import (
    ScanJob,
    Snapshot,
    SteamTarget,
    Subscription,
    User,
    beijing_iso,
    utcnow,
)
from .password_vault import recover_user_password, set_user_password
from .services import (
    add_monitor,
    delete_monitor,
    ensure_mutation_allowed,
    maintenance_active,
    queue_job,
    quota_status,
    snapshot_diff,
    snapshot_public,
    state_get,
    target_public,
)


def bootstrap_data() -> None:
    db.create_all()
    db.session.execute(text("""
        CREATE TRIGGER IF NOT EXISTS trg_steam_target_capacity
        BEFORE INSERT ON steam_targets
        WHEN (SELECT COUNT(*) FROM steam_targets) >= 35
        BEGIN SELECT RAISE(ABORT, 'platform target limit reached'); END
    """))
    seeds = (
        (
            "cs2inventory_admin",
            "scrypt:32768:8:1$gacp7ZrQWyCZHyuv$4e898275b5534b60f7f03f26a3ae21587c524bd14efcc4e48b7f730ad2ad042595d1c3f2d665f909a3a1c145bd2f8aab376c715674e078d08c45ec34b65ab2eb",
            "admin",
        ),
        (
            "cs2inventory_user",
            "scrypt:32768:8:1$53U2u76WMrPTMSeD$b0d629cf7c57ecf06d8030cdd33ee35fd1cc4108cece129218fc0b9730ec6b8fe4af78b3a6b0bb7530e2f52c32ca56e7dbac338594714af84fa29c12dadb69b3",
            "user",
        ),
    )
    for username, password_hash, role in seeds:
        user = User.query.filter_by(username=username).first()
        if not user:
            db.session.add(User(username=username, password_hash=password_hash, role=role))
    db.session.commit()
    initial_user = User.query.filter_by(username="cs2inventory_user").one()
    for steamid in ("76561198441561382", "76561199771254049", "76561198413577373"):
        if not SteamTarget.query.filter_by(steamid=steamid).first():
            add_monitor(initial_user, steamid)


def create_app(config: type[Config] | dict | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)
    if config:
        app.config.from_mapping(config) if isinstance(config, dict) else app.config.from_object(config)
    app.config["STATE_DIR"].mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    with app.app_context():
        bootstrap_data()

    @app.errorhandler(400)
    def bad_request(exc):
        return jsonify({"error": getattr(exc, "description", "请求错误")}), 400

    @app.get("/")
    def index():
        csrf_token()
        return render_template("index.html")

    @app.get("/monitors/<int:_target_id>")
    def monitor_page(_target_id):
        csrf_token()
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/ready")
    def ready():
        db.session.execute(text("SELECT 1"))
        return jsonify({"ok": True, "database": "ready"})

    @app.get("/api/bootstrap")
    def bootstrap():
        user = current_user()
        return jsonify({
            "csrf_token": csrf_token(),
            "user": user_json(user) if user else None,
            "maintenance": maintenance_active(),
            "maintenance_message": state_get("maintenance_message", "每日库存维护进行中"),
        })

    @app.post("/api/auth/register")
    def register():
        require_csrf()
        blocked = ensure_mutation_allowed(None)
        if blocked:
            return blocked
        ip = request.remote_addr or "unknown"
        if rate_limited(f"register:{ip}", limit=5, window_seconds=3600):
            return jsonify({"error": "注册过于频繁，请稍后重试"}), 429
        data = request.get_json(silent=True) or {}
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        if not USERNAME_RE.fullmatch(username):
            return jsonify({"error": "用户名只能包含字母、数字和下划线，长度 3-32"}), 400
        if len(password) < 8 or len(password) > 72:
            return jsonify({"error": "密码长度必须为 8-72"}), 400
        if User.query.filter(func.lower(User.username) == username.lower()).first():
            return jsonify({"error": "用户名已存在"}), 409
        user = User(username=username, password_hash="", role="user")
        set_user_password(user, password)
        db.session.add(user)
        db.session.commit()
        return jsonify({"user": user_json(user)}), 201

    @app.post("/api/auth/login")
    def login():
        require_csrf()
        data = request.get_json(silent=True) or {}
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        ip = request.remote_addr or "unknown"
        if rate_limited(f"login:{ip}:{username.lower()}", limit=10, window_seconds=600):
            return jsonify({"error": "登录尝试过多，请稍后重试"}), 429
        user = User.query.filter(func.lower(User.username) == username.lower()).first()
        if not user or not user.is_active or not check_password_hash(user.password_hash, password):
            return jsonify({"error": "用户名或密码错误"}), 401
        session.clear()
        session.permanent = True
        session["user_id"] = user.id
        user.last_login_at = utcnow()
        db.session.commit()
        return jsonify({"user": user_json(user), "csrf_token": csrf_token()})

    @app.post("/api/auth/logout")
    @login_required
    def logout(_user):
        require_csrf()
        session.clear()
        return jsonify({"ok": True})

    @app.post("/api/auth/password")
    @login_required
    def change_password(user):
        require_csrf()
        blocked = ensure_mutation_allowed(user)
        if blocked:
            return blocked
        data = request.get_json(silent=True) or {}
        old_password = str(data.get("old_password") or "")
        new_password = str(data.get("new_password") or "")
        if not check_password_hash(user.password_hash, old_password):
            return jsonify({"error": "当前密码错误"}), 400
        if len(new_password) < 8 or len(new_password) > 72:
            return jsonify({"error": "新密码长度必须为 8-72"}), 400
        set_user_password(user, new_password)
        db.session.commit()
        return jsonify({"ok": True})

    @app.get("/api/monitors")
    @login_required
    def monitors(user):
        page = max(1, request.args.get("page", 1, type=int))
        query = SteamTarget.query.join(Subscription).filter(Subscription.user_id == user.id).order_by(SteamTarget.created_at.desc())
        pagination = query.paginate(page=page, per_page=app.config["PAGE_SIZE"], error_out=False)
        latest_scan_at = (
            db.session.query(func.max(SteamTarget.last_scan_at))
            .join(Subscription, Subscription.target_id == SteamTarget.id)
            .filter(Subscription.user_id == user.id)
            .scalar()
        )
        return jsonify({
            "items": [target_public(target) for target in pagination.items],
            "page": page,
            "pages": pagination.pages,
            "total": pagination.total,
            "per_page": app.config["PAGE_SIZE"],
            "platform_targets": SteamTarget.query.count(),
            "platform_limit": app.config["MAX_TARGETS"],
            "latest_scan_at": beijing_iso(latest_scan_at),
        })

    @app.post("/api/monitors")
    @login_required
    def monitor_add(user):
        require_csrf()
        blocked = ensure_mutation_allowed(user)
        if blocked:
            return blocked
        steamid = str((request.get_json(silent=True) or {}).get("steamid") or "")
        try:
            target, job, created = add_monitor(user, steamid)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except OverflowError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"monitor": target_public(target), "job_id": job.id if job else None, "created_target": created}), 201

    def accessible_target(user: User, target_id: int) -> SteamTarget | None:
        target = db.session.get(SteamTarget, target_id)
        if not target:
            return None
        if user.role == "admin" or Subscription.query.filter_by(user_id=user.id, target_id=target.id).first():
            return target
        return None

    @app.get("/api/monitors/<int:target_id>")
    @login_required
    def monitor_detail(user, target_id):
        target = accessible_target(user, target_id)
        if not target:
            return jsonify({"error": "监控不存在"}), 404
        history = Snapshot.query.filter_by(target_id=target.id).order_by(Snapshot.scanned_at.desc()).all()
        latest = history[0] if history else None
        return jsonify({
            "monitor": target_public(target, include_latest=False),
            "snapshot": snapshot_public(latest) if latest else None,
            "history": [snapshot_public(row, include_items=False) for row in history],
        })

    @app.get("/api/monitors/<int:target_id>/snapshots/<int:snapshot_id>")
    @login_required
    def monitor_snapshot(user, target_id, snapshot_id):
        target = accessible_target(user, target_id)
        snapshot = db.session.get(Snapshot, snapshot_id)
        if not target or not snapshot or snapshot.target_id != target.id:
            return jsonify({"error": "快照不存在"}), 404
        previous = Snapshot.query.filter(Snapshot.target_id == target.id, Snapshot.scanned_at < snapshot.scanned_at).order_by(Snapshot.scanned_at.desc()).first()
        return jsonify({"snapshot": snapshot_public(snapshot), "diff": snapshot_diff(snapshot, previous)})

    @app.get("/api/monitors/<int:target_id>/compare")
    @login_required
    def monitor_compare(user, target_id):
        target = accessible_target(user, target_id)
        if not target:
            return jsonify({"error": "监控不存在"}), 404
        days = request.args.get("days", type=int)
        if days not in {1, 3, 7}:
            return jsonify({"error": "对比天数只支持 1、3、7"}), 400
        current = (
            Snapshot.query.filter_by(target_id=target.id)
            .order_by(Snapshot.scanned_at.desc(), Snapshot.id.desc())
            .first()
        )
        if not current:
            return jsonify({"days": days, "current": None, "baseline": None, "diff": None})
        cutoff = current.scanned_at - timedelta(days=days)
        baseline = (
            Snapshot.query.filter(
                Snapshot.target_id == target.id,
                Snapshot.scanned_at <= cutoff,
            )
            .order_by(Snapshot.scanned_at.desc(), Snapshot.id.desc())
            .first()
        )
        return jsonify({
            "days": days,
            "current": snapshot_public(current),
            "baseline": snapshot_public(baseline, include_items=False) if baseline else None,
            "diff": snapshot_diff(current, baseline) if baseline else None,
        })

    @app.delete("/api/monitors/<int:target_id>")
    @login_required
    def monitor_delete(user, target_id):
        require_csrf()
        blocked = ensure_mutation_allowed(user)
        if blocked:
            return blocked
        target = db.session.get(SteamTarget, target_id)
        if not target:
            return jsonify({"error": "监控不存在"}), 404
        try:
            deleted = delete_monitor(user, target)
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        return jsonify({"ok": True, "target_deleted": deleted})

    @app.get("/api/jobs/<int:job_id>")
    @login_required
    def job_status(user, job_id):
        job = db.session.get(ScanJob, job_id)
        if not job or (user.role != "admin" and job.requested_by != user.id):
            return jsonify({"error": "任务不存在"}), 404
        result = json.loads(job.result_json) if job.result_json else None
        return jsonify({"id": job.id, "status": job.status, "kind": job.kind, "result": result, "error": job.error})

    @app.get("/api/admin/users")
    @admin_required
    def admin_users(_user):
        page = max(1, request.args.get("page", 1, type=int))
        pagination = User.query.order_by(User.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
        items = []
        for row in pagination.items:
            data = user_json(row)
            data["monitor_count"] = len(row.subscriptions)
            data["steamids"] = [sub.target.steamid for sub in row.subscriptions]
            data["password"] = recover_user_password(row)
            data["password_available"] = data["password"] is not None
            data["password_changed_at"] = beijing_iso(row.password_changed_at)
            items.append(data)
        response = jsonify({"items": items, "page": page, "pages": pagination.pages, "total": pagination.total})
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.patch("/api/admin/users/<int:user_id>")
    @admin_required
    def admin_user_update(admin, user_id):
        require_csrf()
        user = db.session.get(User, user_id)
        if not user:
            return jsonify({"error": "用户不存在"}), 404
        data = request.get_json(silent=True) or {}
        if "is_active" in data:
            if user.id == admin.id and not bool(data["is_active"]):
                return jsonify({"error": "不能停用当前管理员"}), 400
            user.is_active = bool(data["is_active"])
        if data.get("new_password") is not None:
            password = str(data["new_password"])
            if len(password) < 8 or len(password) > 72:
                return jsonify({"error": "重置密码长度必须为 8-72"}), 400
            set_user_password(user, password)
        db.session.commit()
        return jsonify({"user": user_json(user)})

    @app.get("/api/admin/targets")
    @admin_required
    def admin_targets(_user):
        page = max(1, request.args.get("page", 1, type=int))
        pagination = SteamTarget.query.order_by(SteamTarget.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
        return jsonify({"items": [target_public(row) for row in pagination.items], "page": page, "pages": pagination.pages, "total": pagination.total})

    @app.delete("/api/admin/targets/<int:target_id>")
    @admin_required
    def admin_target_delete(_user, target_id):
        require_csrf()
        target = db.session.get(SteamTarget, target_id)
        if not target:
            return jsonify({"error": "监控不存在"}), 404
        db.session.delete(target)
        db.session.commit()
        return jsonify({"ok": True, "target_deleted": True})

    @app.post("/api/admin/targets/<int:target_id>/scan")
    @admin_required
    def admin_scan(user, target_id):
        require_csrf()
        target = db.session.get(SteamTarget, target_id)
        if not target:
            return jsonify({"error": "监控不存在"}), 404
        job = queue_job(target, kind="manual", requested_by=user.id)
        db.session.commit()
        return jsonify({"job_id": job.id}), 202

    @app.post("/api/admin/query")
    @admin_required
    def admin_query(user):
        require_csrf()
        steamid = str((request.get_json(silent=True) or {}).get("steamid") or "").strip()
        if not steamid.startswith("7656119") or len(steamid) != 17 or not steamid.isdigit():
            return jsonify({"error": "请输入有效的 SteamID64"}), 400
        job = ScanJob(steamid=steamid, requested_by=user.id, kind="instant", expires_at=utcnow() + timedelta(hours=1))
        db.session.add(job)
        db.session.commit()
        return jsonify({"job_id": job.id}), 202

    @app.get("/api/admin/status")
    @admin_required
    def admin_status(_user):
        running = ScanJob.query.filter(ScanJob.status.in_(["queued", "running"])).count()
        official_raw = state_get("official_quota_json", "")
        try:
            official = json.loads(official_raw) if official_raw else None
        except ValueError:
            official = None
        return jsonify({
            "maintenance": maintenance_active(),
            "maintenance_message": state_get("maintenance_message", ""),
            "targets": SteamTarget.query.count(),
            "target_limit": app.config["MAX_TARGETS"],
            "pending_jobs": running,
            "quota": quota_status(),
            "official_quota": official,
        })

    return app
