from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import UniqueConstraint

from .database import db


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def beijing_iso(value: datetime | None) -> str | None:
    """Serialize UTC/SQLite datetimes as explicit Beijing time."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BEIJING_TIMEZONE).isoformat(timespec="seconds")


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    password_ciphertext = db.Column(db.Text)
    password_changed_at = db.Column(db.DateTime(timezone=True))
    role = db.Column(db.String(16), nullable=False, default="user")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    account_kind = db.Column(db.String(16), nullable=False, default="trial")
    plan = db.Column(db.String(16))
    activated_at = db.Column(db.DateTime(timezone=True))
    activation_expires_at = db.Column(db.DateTime(timezone=True))
    monitor_limit = db.Column(db.Integer)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime(timezone=True))
    subscriptions = db.relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    trial_experience = db.relationship(
        "TrialExperience",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )


class SteamTarget(db.Model):
    __tablename__ = "steam_targets"
    id = db.Column(db.Integer, primary_key=True)
    steamid = db.Column(db.String(17), unique=True, nullable=False, index=True)
    persona_name = db.Column(db.String(255))
    profile_updated_at = db.Column(db.DateTime(timezone=True))
    scan_status = db.Column(db.String(24), nullable=False, default="pending")
    last_scan_at = db.Column(db.DateTime(timezone=True))
    last_success_at = db.Column(db.DateTime(timezone=True))
    last_error = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    subscriptions = db.relationship("Subscription", back_populates="target", cascade="all, delete-orphan")
    snapshots = db.relationship("Snapshot", back_populates="target", cascade="all, delete-orphan")


class Subscription(db.Model):
    __tablename__ = "subscriptions"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    target_id = db.Column(db.Integer, db.ForeignKey("steam_targets.id", ondelete="CASCADE"), nullable=False)
    remark = db.Column(db.String(50))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship("User", back_populates="subscriptions")
    target = db.relationship("SteamTarget", back_populates="subscriptions")
    __table_args__ = (UniqueConstraint("user_id", "target_id", name="uq_subscription"),)


class TrialExperience(db.Model):
    __tablename__ = "trial_experiences"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    steamid = db.Column(db.String(17))
    current_target_id = db.Column(db.Integer, db.ForeignKey("steam_targets.id", ondelete="SET NULL"))
    current_job_id = db.Column(db.Integer, db.ForeignKey("scan_jobs.id", ondelete="SET NULL"))
    result_snapshot_id = db.Column(db.Integer, db.ForeignKey("snapshots.id", ondelete="SET NULL"))
    registration_expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    completed_at = db.Column(db.DateTime(timezone=True))
    result_expires_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship("User", back_populates="trial_experience")


class ActivationCode(db.Model):
    __tablename__ = "activation_codes"
    id = db.Column(db.Integer, primary_key=True)
    code_digest = db.Column(db.String(64), unique=True, nullable=False, index=True)
    code_prefix = db.Column(db.String(16), nullable=False)
    plan = db.Column(db.String(16), nullable=False)
    monitor_limit = db.Column(db.Integer, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    redeemed_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    redeemed_at = db.Column(db.DateTime(timezone=True))
    revoked_at = db.Column(db.DateTime(timezone=True))


class Snapshot(db.Model):
    __tablename__ = "snapshots"
    id = db.Column(db.Integer, primary_key=True)
    target_id = db.Column(db.Integer, db.ForeignKey("steam_targets.id", ondelete="CASCADE"), nullable=False, index=True)
    scanned_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    total_items = db.Column(db.Integer, nullable=False)
    item_types = db.Column(db.Integer, nullable=False)
    coverage = db.Column(db.String(32), nullable=False, default="unknown")
    elapsed_ms = db.Column(db.Integer, nullable=False, default=0)
    errors_json = db.Column(db.Text, nullable=False, default="[]")
    payload_gzip = db.Column(db.LargeBinary, nullable=False)
    target = db.relationship("SteamTarget", back_populates="snapshots")
    items = db.relationship("SnapshotItem", back_populates="snapshot", cascade="all, delete-orphan")


class SnapshotItem(db.Model):
    __tablename__ = "snapshot_items"
    id = db.Column(db.Integer, primary_key=True)
    snapshot_id = db.Column(db.Integer, db.ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    asset_key = db.Column(db.String(128), nullable=False)
    name = db.Column(db.String(512), nullable=False, index=True)
    raw_name = db.Column(db.String(512), nullable=False, default="")
    classid = db.Column(db.String(64), nullable=False, default="")
    instanceid = db.Column(db.String(64), nullable=False, default="0")
    name_localized = db.Column(db.Boolean, nullable=False, default=False)
    amount = db.Column(db.Integer, nullable=False, default=1)
    evidence_json = db.Column(db.Text, nullable=False, default="{}")
    first_seen_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    is_trade_protected = db.Column(db.Boolean, nullable=False, default=False)
    snapshot = db.relationship("Snapshot", back_populates="items")
    __table_args__ = (UniqueConstraint("snapshot_id", "asset_key", name="uq_snapshot_asset"),)


class ItemNameLocalization(db.Model):
    __tablename__ = "item_name_localizations"
    id = db.Column(db.Integer, primary_key=True)
    language = db.Column(db.String(16), nullable=False, default="schinese")
    source_name = db.Column(db.String(512), nullable=False)
    localized_name = db.Column(db.String(512), nullable=False)
    classid = db.Column(db.String(64), nullable=False, default="")
    instanceid = db.Column(db.String(64), nullable=False, default="0")
    source = db.Column(db.String(32), nullable=False, default="official")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("language", "source_name", name="uq_item_name_language_source"),)


class LocalizationJob(db.Model):
    __tablename__ = "localization_jobs"
    id = db.Column(db.Integer, primary_key=True)
    snapshot_id = db.Column(db.Integer, db.ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = db.Column(db.Integer, db.ForeignKey("steam_targets.id", ondelete="CASCADE"), nullable=False, index=True)
    language = db.Column(db.String(16), nullable=False, default="schinese")
    status = db.Column(db.String(24), nullable=False, default="queued", index=True)
    attempt = db.Column(db.Integer, nullable=False, default=0)
    next_attempt_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    unresolved_count = db.Column(db.Integer, nullable=False, default=0)
    error = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class ScanBatch(db.Model):
    __tablename__ = "scan_batches"
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(24), nullable=False, default="daily")
    status = db.Column(db.String(24), nullable=False, default="queued")
    total_jobs = db.Column(db.Integer, nullable=False, default=0)
    completed_jobs = db.Column(db.Integer, nullable=False, default=0)
    failed_jobs = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    started_at = db.Column(db.DateTime(timezone=True))
    finished_at = db.Column(db.DateTime(timezone=True))


class ScanJob(db.Model):
    __tablename__ = "scan_jobs"
    id = db.Column(db.Integer, primary_key=True)
    target_id = db.Column(db.Integer, db.ForeignKey("steam_targets.id", ondelete="CASCADE"), index=True)
    steamid = db.Column(db.String(17), nullable=False)
    requested_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    batch_id = db.Column(db.Integer, db.ForeignKey("scan_batches.id", ondelete="SET NULL"), index=True)
    kind = db.Column(db.String(24), nullable=False, default="initial")
    status = db.Column(db.String(24), nullable=False, default="queued", index=True)
    result_json = db.Column(db.Text)
    error = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    started_at = db.Column(db.DateTime(timezone=True))
    finished_at = db.Column(db.DateTime(timezone=True))
    expires_at = db.Column(db.DateTime(timezone=True))


class QuotaUsage(db.Model):
    __tablename__ = "quota_usage"
    id = db.Column(db.Integer, primary_key=True)
    endpoint = db.Column(db.String(32), nullable=False, default="inventory")
    credits = db.Column(db.Integer, nullable=False, default=1)
    source = db.Column(db.String(32), nullable=False, default="local")
    used_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class SystemState(db.Model):
    __tablename__ = "system_state"
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
