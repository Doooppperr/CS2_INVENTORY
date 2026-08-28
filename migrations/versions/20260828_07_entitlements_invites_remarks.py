"""Add account entitlements, trial lifecycle, invitation codes and remarks."""

import sqlalchemy as sa
from alembic import op

revision = "20260828_07"
down_revision = "20260820_06"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    additions = (
        ("account_kind", sa.Column("account_kind", sa.String(16), nullable=True)),
        ("plan", sa.Column("plan", sa.String(16), nullable=True)),
        ("activated_at", sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True)),
        ("activation_expires_at", sa.Column("activation_expires_at", sa.DateTime(timezone=True), nullable=True)),
        ("monitor_limit", sa.Column("monitor_limit", sa.Integer(), nullable=True)),
    )
    with op.batch_alter_table("users") as batch:
        for name, column in additions:
            if name not in user_columns:
                batch.add_column(column)

    subscription_columns = {column["name"] for column in sa.inspect(bind).get_columns("subscriptions")}
    if "remark" not in subscription_columns:
        with op.batch_alter_table("subscriptions") as batch:
            batch.add_column(sa.Column("remark", sa.String(50), nullable=True))

    tables = set(sa.inspect(bind).get_table_names())
    if "trial_experiences" not in tables:
        op.create_table(
            "trial_experiences",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("steamid", sa.String(17), nullable=True),
            sa.Column("current_target_id", sa.Integer(), sa.ForeignKey("steam_targets.id", ondelete="SET NULL"), nullable=True),
            sa.Column("current_job_id", sa.Integer(), sa.ForeignKey("scan_jobs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("result_snapshot_id", sa.Integer(), sa.ForeignKey("snapshots.id", ondelete="SET NULL"), nullable=True),
            sa.Column("registration_expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("result_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "activation_codes" not in tables:
        op.create_table(
            "activation_codes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code_digest", sa.String(64), nullable=False),
            sa.Column("code_prefix", sa.String(16), nullable=False),
            sa.Column("plan", sa.String(16), nullable=False),
            sa.Column("monitor_limit", sa.Integer(), nullable=False),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("redeemed_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("code_digest", name="uq_activation_codes_digest"),
        )
        op.create_index("ix_activation_codes_code_digest", "activation_codes", ["code_digest"], unique=True)

    # Every account present at deployment keeps its current rights and becomes
    # an internal permanent account. New registrations explicitly use trial.
    op.execute(sa.text("""
        UPDATE users
        SET account_kind = 'internal',
            plan = 'permanent',
            activated_at = COALESCE(activated_at, created_at),
            activation_expires_at = NULL,
            monitor_limit = NULL
        WHERE account_kind IS NULL
    """))
    # Do not rebuild the SQLite users table merely to add NOT NULL: dropping
    # the temporary source table can cascade into subscriptions while foreign
    # keys are enabled. Application writes always set account_kind explicitly;
    # fresh databases receive the stricter metadata definition directly.


def downgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "activation_codes" in tables:
        op.drop_table("activation_codes")
    if "trial_experiences" in tables:
        op.drop_table("trial_experiences")
    subscription_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("subscriptions")}
    if "remark" in subscription_columns:
        with op.batch_alter_table("subscriptions") as batch:
            batch.drop_column("remark")
    user_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    with op.batch_alter_table("users") as batch:
        for name in ("monitor_limit", "activation_expires_at", "activated_at", "plan", "account_kind"):
            if name in user_columns:
                batch.drop_column(name)
