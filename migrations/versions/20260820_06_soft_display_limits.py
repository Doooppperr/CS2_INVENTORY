"""Make target capacity and daily budget display-only references."""

from alembic import op

revision = "20260820_06"
down_revision = "20260819_05"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_steam_target_capacity")


def downgrade():
    op.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_steam_target_capacity
        BEFORE INSERT ON steam_targets
        WHEN (SELECT COUNT(*) FROM steam_targets) >= 35
        BEGIN SELECT RAISE(ABORT, 'platform target limit reached'); END
    """)
