from __future__ import annotations

import json
from datetime import timedelta

from flask import Flask, jsonify, render_template, request, session
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
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
from .entitlements import (
    VALID_PLANS,
    activation_code_public,
    add_natural_period,
    create_activation_code,
    entitlement_public,
    entitlement_state,
    redeem_activation_code,
    revoke_activation_code,
    snapshot_query_for_user,
    state_allows_monitor_write,
)
from .models import (
    ActivationCode,
    ItemNameLocalization,
    LocalizationJob,
    ScanJob,
    Snapshot,
    SteamTarget,
    Subscription,
    SystemState,
    User,
    beijing_iso,
    utcnow,
)
from .password_vault import recover_user_password, set_user_password
from .services import (
    add_monitor,
    delete_monitor,
    delete_user_account,
    ensure_mutation_allowed,
    maintenance_active,
    monitor_public,
    queue_job,
    quota_status,
    snapshot_diff,
    snapshot_public,
    state_get,
    state_set,
    target_public,
)

BOOTSTRAP_SEED_VERSION = "1"


def bootstrap_data() -> None:
    db.create_all()
    # Capacity is an observability benchmark, not an insertion constraint.
    # Drop the legacy trigger as a second line of defense for installations
    # that initialized the database before the soft-limit migration existed.
    db.session.execute(text("DROP TRIGGER IF EXISTS trg_steam_target_capacity"))
    if db.session.get(SystemState, "bootstrap_seed_version"):
        db.session.commit()
        return
    if User.query.first() or SteamTarget.query.first():
        state_set("bootstrap_seed_version", BOOTSTRAP_SEED_VERSION)
        db.session.commit()
        return

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
        db.session.add(User(
            username=username,
            password_hash=password_hash,
            role=role,
            account_kind="internal",
            plan="permanent",
            monitor_limit=None,
        ))
    state_set("bootstrap_seed_version", BOOTSTRAP_SEED_VERSION)
    db.session.commit()


def canonical_pagination(query, *, per_page: int):
    requested_page = max(1, request.args.get("page", 1, type=int))
    pagination = query.paginate(page=requested_page, per_page=per_page, error_out=False)
    page = min(requested_page, max(1, pagination.pages))
    if page != requested_page:
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return page, pagination


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
        return render_template("landing.html")

    @app.get("/app", strict_slashes=False)
    def application():
        csrf_token()
        return render_template("index.html")

    @app.get("/app/monitors/<int:_target_id>", strict_slashes=False)
    def application_monitor_page(_target_id):
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
        return jsonify({"error": "账号仅由管理员创建"}), 403

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

    @app.post("/api/activation/redeem")
    @login_required
    def activation_redeem(user):
        require_csrf()
        data = request.get_json(silent=True) or {}
        try:
            row = redeem_activation_code(user, str(data.get("code") or ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"ok": True, "plan": row.plan, "user": user_json(user)})

    @app.get("/api/monitors")
    @login_required
    def monitors(user):
        user_state = entitlement_state(user)
        if user_state == "expired":
            return jsonify({
                "items": [], "page": 1, "pages": 0, "total": 0,
                "per_page": app.config["PAGE_SIZE"],
                "platform_targets": SteamTarget.query.count(),
                "platform_limit": app.config["MAX_TARGETS"],
                "platform_limit_enforced": False,
                "latest_scan_at": None,
                "entitlement": entitlement_public(user),
            })
        query = Subscription.query.filter_by(user_id=user.id).join(SteamTarget).order_by(SteamTarget.created_at.desc())
        page, pagination = canonical_pagination(query, per_page=app.config["PAGE_SIZE"])
        latest_scan_at = (
            db.session.query(func.max(SteamTarget.last_scan_at))
            .join(Subscription, Subscription.target_id == SteamTarget.id)
            .filter(Subscription.user_id == user.id)
            .scalar()
        )
        if user_state == "grace":
            visible_times = [
                row.scanned_at
                for sub in user.subscriptions
                if (row := snapshot_query_for_user(user, sub.target_id).order_by(
                    Snapshot.scanned_at.desc(), Snapshot.id.desc()
                ).first())
            ]
            latest_scan_at = max(visible_times) if visible_times else None
        return jsonify({
            "items": [monitor_public(subscription, user) for subscription in pagination.items],
            "page": page,
            "pages": pagination.pages,
            "total": pagination.total,
            "per_page": app.config["PAGE_SIZE"],
            "platform_targets": SteamTarget.query.count(),
            "platform_limit": app.config["MAX_TARGETS"],
            "platform_limit_enforced": False,
            "latest_scan_at": beijing_iso(latest_scan_at),
            "entitlement": entitlement_public(user),
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
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        subscription = Subscription.query.filter_by(user_id=user.id, target_id=target.id).one()
        return jsonify({"monitor": monitor_public(subscription, user), "job_id": job.id if job else None, "created_target": created}), 201

    def accessible_target(user: User, target_id: int) -> tuple[SteamTarget, Subscription | None] | None:
        target = db.session.get(SteamTarget, target_id)
        if not target:
            return None
        if user.role == "admin":
            return target, None
        if entitlement_state(user) == "expired":
            return None
        subscription = Subscription.query.filter_by(user_id=user.id, target_id=target.id).first()
        if subscription:
            return target, subscription
        return None

    @app.get("/api/monitors/<int:target_id>")
    @login_required
    def monitor_detail(user, target_id):
        access = accessible_target(user, target_id)
        if not access:
            return jsonify({"error": "监控不存在"}), 404
        target, subscription = access
        history = snapshot_query_for_user(user, target.id).order_by(Snapshot.scanned_at.desc()).all()
        latest = history[0] if history else None
        return jsonify({
            "monitor": target_public(target, include_latest=False) if subscription is None else monitor_public(subscription, user, include_latest=False),
            "snapshot": snapshot_public(latest) if latest else None,
            "history": [snapshot_public(row, include_items=False) for row in history],
        })

    @app.get("/api/monitors/<int:target_id>/snapshots/<int:snapshot_id>")
    @login_required
    def monitor_snapshot(user, target_id, snapshot_id):
        access = accessible_target(user, target_id)
        snapshot = snapshot_query_for_user(user, target_id).filter(Snapshot.id == snapshot_id).first() if access else None
        if not access or not snapshot:
            return jsonify({"error": "快照不存在"}), 404
        target, _subscription = access
        previous = snapshot_query_for_user(user, target.id).filter(
            Snapshot.scanned_at < snapshot.scanned_at
        ).order_by(Snapshot.scanned_at.desc()).first()
        return jsonify({"snapshot": snapshot_public(snapshot), "diff": snapshot_diff(snapshot, previous)})

    @app.get("/api/monitors/<int:target_id>/compare")
    @login_required
    def monitor_compare(user, target_id):
        access = accessible_target(user, target_id)
        if not access:
            return jsonify({"error": "监控不存在"}), 404
        target, _subscription = access
        days = request.args.get("days", type=int)
        if days not in {1, 3, 7}:
            return jsonify({"error": "对比天数只支持 1、3、7"}), 400
        current = (
            snapshot_query_for_user(user, target.id)
            .order_by(Snapshot.scanned_at.desc(), Snapshot.id.desc())
            .first()
        )
        if not current:
            return jsonify({"days": days, "current": None, "baseline": None, "diff": None})
        cutoff = current.scanned_at - timedelta(days=days)
        baseline = (
            snapshot_query_for_user(user, target.id).filter(
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
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 403
        return jsonify({"ok": True, "target_deleted": deleted})

    @app.patch("/api/monitors/<int:target_id>")
    @login_required
    def monitor_remark(user, target_id):
        require_csrf()
        if not state_allows_monitor_write(user):
            return jsonify({"error": "当前账号处于只读状态，不能修改备注"}), 403
        subscription = Subscription.query.filter_by(user_id=user.id, target_id=target_id).first()
        if not subscription:
            return jsonify({"error": "监控不存在"}), 404
        remark = str((request.get_json(silent=True) or {}).get("remark") or "").strip()
        if "\n" in remark or "\r" in remark:
            return jsonify({"error": "备注不能包含换行"}), 400
        if len(remark) > 50:
            return jsonify({"error": "备注最多 50 个字符"}), 400
        subscription.remark = remark or None
        db.session.commit()
        return jsonify({"monitor": monitor_public(subscription, user)})

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
        page, pagination = canonical_pagination(User.query.order_by(User.created_at.desc()), per_page=20)
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

    @app.post("/api/admin/users")
    @admin_required
    def admin_user_create(_admin):
        require_csrf()
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "请求内容必须为对象"}), 400
        allowed = {"username", "password", "plan", "monitor_limit"}
        if set(data) - allowed:
            return jsonify({"error": "创建账户只允许用户名 密码 套餐和监控限额"}), 400
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        plan = str(data.get("plan") or "").strip()
        if not USERNAME_RE.fullmatch(username):
            return jsonify({"error": "用户名只能包含字母 数字和下划线 长度 3-32"}), 400
        if len(password) < 8 or len(password) > 72:
            return jsonify({"error": "密码长度必须为 8-72"}), 400
        if plan not in VALID_PLANS:
            return jsonify({"error": "套餐必须为月度 年度或永久"}), 400
        raw_monitor_limit = data.get("monitor_limit")
        if isinstance(raw_monitor_limit, bool) or not isinstance(raw_monitor_limit, int):
            return jsonify({"error": "监控限额必须为整数"}), 400
        monitor_limit = raw_monitor_limit
        if monitor_limit < 1 or monitor_limit > 10000:
            return jsonify({"error": "创建账户监控限额必须为 1-10000"}), 400
        normalized_username = username.lower()
        if User.query.filter(func.lower(User.username) == normalized_username).first():
            return jsonify({"error": "用户名已存在"}), 409
        now = utcnow()
        user = User(
            username=normalized_username,
            password_hash="",
            role="user",
            is_active=True,
            account_kind="customer",
            plan=plan,
            activated_at=now,
            activation_expires_at=None if plan == "permanent" else add_natural_period(now, plan),
            monitor_limit=monitor_limit,
        )
        set_user_password(user, password)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return jsonify({"error": "用户名已存在"}), 409
        response = jsonify({"user": user_json(user)})
        response.status_code = 201
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
            return jsonify({"error": "账号停用已改为永久删除，请使用删除账号"}), 400
        if data.get("new_password") is not None:
            password = str(data["new_password"])
            if len(password) < 8 or len(password) > 72:
                return jsonify({"error": "重置密码长度必须为 8-72"}), 400
            set_user_password(user, password)
        if "monitor_limit" in data:
            if user.role == "admin" or user.account_kind == "internal":
                return jsonify({"error": "管理员和内部账号固定为无限监控"}), 400
            try:
                monitor_limit = int(data["monitor_limit"])
            except (TypeError, ValueError):
                return jsonify({"error": "监控限额必须为整数"}), 400
            if monitor_limit < 0 or monitor_limit > 10000:
                return jsonify({"error": "监控限额必须为 0-10000"}), 400
            user.monitor_limit = monitor_limit
        db.session.commit()
        return jsonify({"user": user_json(user)})

    @app.get("/api/admin/activation-codes")
    @admin_required
    def admin_activation_codes(_user):
        page, pagination = canonical_pagination(
            ActivationCode.query.order_by(ActivationCode.created_at.desc()),
            per_page=20,
        )
        return jsonify({
            "items": [activation_code_public(row) for row in pagination.items],
            "page": page,
            "pages": pagination.pages,
            "total": pagination.total,
        })

    @app.post("/api/admin/activation-codes")
    @admin_required
    def admin_activation_code_create(admin):
        require_csrf()
        data = request.get_json(silent=True) or {}
        try:
            monitor_limit = int(data.get("monitor_limit"))
            row, raw_code = create_activation_code(admin, str(data.get("plan") or ""), monitor_limit)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc) or "邀请码参数无效"}), 400
        return jsonify({"item": activation_code_public(row), "code": raw_code}), 201

    @app.delete("/api/admin/activation-codes/<int:code_id>")
    @admin_required
    def admin_activation_code_revoke(_admin, code_id):
        require_csrf()
        row = db.session.get(ActivationCode, code_id)
        if not row:
            return jsonify({"error": "邀请码不存在"}), 404
        try:
            revoke_activation_code(row)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "item": activation_code_public(row)})

    @app.delete("/api/admin/users/<int:user_id>")
    @admin_required
    def admin_user_delete(admin, user_id):
        require_csrf()
        user = db.session.get(User, user_id)
        if not user:
            return jsonify({"error": "用户不存在"}), 404
        if user.id == admin.id:
            return jsonify({"error": "不能删除当前管理员"}), 400
        deleted_targets = delete_user_account(user)
        return jsonify({"ok": True, "deleted_user_id": user_id, "deleted_targets": deleted_targets})

    @app.get("/api/admin/targets")
    @admin_required
    def admin_targets(_user):
        page, pagination = canonical_pagination(SteamTarget.query.order_by(SteamTarget.created_at.desc()), per_page=20)
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
        users = User.query.all()
        entitlement_counts: dict[str, int] = {}
        for row in users:
            status = entitlement_state(row)
            key = row.plan or status
            entitlement_counts[key] = entitlement_counts.get(key, 0) + 1
        return jsonify({
            "maintenance": maintenance_active(),
            "maintenance_message": state_get("maintenance_message", ""),
            "targets": SteamTarget.query.count(),
            "target_limit": app.config["MAX_TARGETS"],
            "target_limit_enforced": False,
            "pending_jobs": running,
            "quota": quota_status(),
            "official_quota": official,
            "entitlements": entitlement_counts,
            "activation_codes": {
                "unused": ActivationCode.query.filter_by(redeemed_at=None, revoked_at=None).count(),
                "redeemed": ActivationCode.query.filter(ActivationCode.redeemed_at.is_not(None)).count(),
                "revoked": ActivationCode.query.filter(ActivationCode.revoked_at.is_not(None)).count(),
            },
            "localization": {
                "mappings": ItemNameLocalization.query.count(),
                "pending_jobs": LocalizationJob.query.filter(LocalizationJob.status.in_(["queued", "running"])).count(),
                "pending_items": db.session.query(func.coalesce(func.sum(LocalizationJob.unresolved_count), 0)).filter(
                    LocalizationJob.status.in_(["queued", "running"])
                ).scalar(),
                "failed_jobs": LocalizationJob.query.filter_by(status="failed").count(),
            },
        })

    return app
