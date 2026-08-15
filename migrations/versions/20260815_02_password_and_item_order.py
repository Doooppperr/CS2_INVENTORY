"""Add encrypted password recovery and persistent item discovery order."""

import sqlalchemy as sa
from alembic import op

revision = "20260815_02"
down_revision = "20260814_01"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    with op.batch_alter_table("users") as batch:
        if "password_ciphertext" not in user_columns:
            batch.add_column(sa.Column("password_ciphertext", sa.Text(), nullable=True))
        if "password_changed_at" not in user_columns:
            batch.add_column(sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))

    item_columns = {column["name"] for column in inspector.get_columns("snapshot_items")}
    if "first_seen_at" not in item_columns:
        with op.batch_alter_table("snapshot_items") as batch:
            batch.add_column(sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True))
        op.execute(sa.text("""
            UPDATE snapshot_items
            SET first_seen_at = (
                SELECT snapshots.scanned_at
                FROM snapshots
                WHERE snapshots.id = snapshot_items.snapshot_id
            )
        """))
        with op.batch_alter_table("snapshot_items") as batch:
            batch.alter_column("first_seen_at", existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade():
    with op.batch_alter_table("snapshot_items") as batch:
        batch.drop_column("first_seen_at")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("password_changed_at")
        batch.drop_column("password_ciphertext")
