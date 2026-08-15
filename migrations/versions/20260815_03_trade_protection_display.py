"""Persist explicit live trade-protection state for snapshot assets."""

import sqlalchemy as sa
from alembic import op

revision = "20260815_03"
down_revision = "20260815_02"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    item_columns = {column["name"] for column in inspector.get_columns("snapshot_items")}
    if "is_trade_protected" not in item_columns:
        with op.batch_alter_table("snapshot_items") as batch:
            batch.add_column(
                sa.Column(
                    "is_trade_protected",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    item_columns = {column["name"] for column in inspector.get_columns("snapshot_items")}
    if "is_trade_protected" in item_columns:
        with op.batch_alter_table("snapshot_items") as batch:
            batch.drop_column("is_trade_protected")
