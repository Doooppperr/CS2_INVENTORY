"""Add idempotent twice-daily schedule slots and conservative credit accounting."""

import sqlalchemy as sa
from alembic import op

revision = "20260906_09"
down_revision = "20260903_08"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("scan_batches")}
    if "slot_key" not in columns:
        with op.batch_alter_table("scan_batches") as batch:
            batch.add_column(sa.Column("slot_key", sa.String(length=32), nullable=True))
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("scan_batches")}
    if "ix_scan_batches_slot_key" not in indexes:
        with op.batch_alter_table("scan_batches") as batch:
            batch.create_index("ix_scan_batches_slot_key", ["slot_key"], unique=True)
    # Legacy rows counted source records rather than provider credits. Three
    # credits per recorded request is intentionally conservative and reversible.
    op.execute("UPDATE quota_usage SET credits = credits * 3 WHERE endpoint = 'inventory'")


def downgrade():
    op.execute("UPDATE quota_usage SET credits = CAST(credits / 3 AS INTEGER) WHERE endpoint = 'inventory'")
    bind = op.get_bind()
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("scan_batches")}
    with op.batch_alter_table("scan_batches") as batch:
        if "ix_scan_batches_slot_key" in indexes:
            batch.drop_index("ix_scan_batches_slot_key")
        batch.drop_column("slot_key")
