"""Retire public trials and make account creation administrator-only."""

import sqlalchemy as sa
from alembic import op

revision = "20260903_08"
down_revision = "20260828_07"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    trial_users = bind.execute(
        sa.text("SELECT COUNT(*) FROM users WHERE account_kind = 'trial'")
    ).scalar_one()
    trial_rows = 0
    if "trial_experiences" in tables:
        trial_rows = bind.execute(sa.text("SELECT COUNT(*) FROM trial_experiences")).scalar_one()
    if trial_users or trial_rows:
        raise RuntimeError(
            "trial retirement requires zero trial users and trial_experiences rows "
            f"(users={trial_users}, rows={trial_rows})"
        )
    if "trial_experiences" in tables:
        op.drop_table("trial_experiences")


def downgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
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
