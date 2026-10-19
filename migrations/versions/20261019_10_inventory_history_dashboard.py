"""新增长期资产历史及八天日报，不改写或删除原有库存。"""
import sqlalchemy as sa
from alembic import op

revision = "20261019_10"
down_revision = "20260906_09"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "target_asset_history" not in tables:
        op.create_table("target_asset_history",
            sa.Column("target_id", sa.Integer, sa.ForeignKey("steam_targets.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("asset_key", sa.String(128), primary_key=True),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False))
    if "daily_observations" not in tables:
        op.create_table("daily_observations",
            sa.Column("target_id", sa.Integer, sa.ForeignKey("steam_targets.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("day", sa.String(10), primary_key=True),
            sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("assets_json", sa.Text, nullable=False),
            sa.Column("report_json", sa.Text, nullable=False))


def downgrade():
    op.drop_table("daily_observations")
    op.drop_table("target_asset_history")
