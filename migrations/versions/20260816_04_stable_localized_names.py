"""Persist stable localized names and lightweight retry jobs."""

import sqlalchemy as sa
from alembic import op

revision = "20260816_04"
down_revision = "20260815_03"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("snapshot_items")}
    additions = (
        sa.Column("raw_name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("classid", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("instanceid", sa.String(length=64), nullable=False, server_default="0"),
        sa.Column("name_localized", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    with op.batch_alter_table("snapshot_items") as batch:
        for column in additions:
            if column.name not in columns:
                batch.add_column(column)
    op.execute(sa.text("UPDATE snapshot_items SET raw_name = name WHERE raw_name = ''"))

    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "item_name_localizations" not in tables:
        op.create_table(
            "item_name_localizations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("language", sa.String(length=16), nullable=False, server_default="schinese"),
            sa.Column("source_name", sa.String(length=512), nullable=False),
            sa.Column("localized_name", sa.String(length=512), nullable=False),
            sa.Column("classid", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("instanceid", sa.String(length=64), nullable=False, server_default="0"),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="official"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("language", "source_name", name="uq_item_name_language_source"),
        )
    if "localization_jobs" not in tables:
        op.create_table(
            "localization_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("snapshots.id", ondelete="CASCADE"), nullable=False),
            sa.Column("target_id", sa.Integer(), sa.ForeignKey("steam_targets.id", ondelete="CASCADE"), nullable=False),
            sa.Column("language", sa.String(length=16), nullable=False, server_default="schinese"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("unresolved_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_localization_jobs_snapshot_id", "localization_jobs", ["snapshot_id"])
        op.create_index("ix_localization_jobs_target_id", "localization_jobs", ["target_id"])
        op.create_index("ix_localization_jobs_status", "localization_jobs", ["status"])
        op.create_index("ix_localization_jobs_next_attempt_at", "localization_jobs", ["next_attempt_at"])


def downgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "localization_jobs" in tables:
        op.drop_table("localization_jobs")
    if "item_name_localizations" in tables:
        op.drop_table("item_name_localizations")
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("snapshot_items")}
    with op.batch_alter_table("snapshot_items") as batch:
        for name in ("name_localized", "instanceid", "classid", "raw_name"):
            if name in columns:
                batch.drop_column(name)
