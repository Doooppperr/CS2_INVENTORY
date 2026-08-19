"""Make account deletion permanent and remove legacy seeded monitors."""

import sqlalchemy as sa
from alembic import op

revision = "20260819_05"
down_revision = "20260816_04"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM users WHERE is_active = 0 AND role != 'admin'"))
    connection.execute(sa.text("""
        DELETE FROM steam_targets
        WHERE steamid IN ('76561198441561382', '76561199771254049', '76561198413577373')
    """))
    connection.execute(sa.text("""
        DELETE FROM steam_targets
        WHERE NOT EXISTS (
            SELECT 1 FROM subscriptions WHERE subscriptions.target_id = steam_targets.id
        )
    """))
    connection.execute(sa.text("""
        INSERT INTO system_state (key, value, updated_at)
        SELECT 'bootstrap_seed_version', '1', CURRENT_TIMESTAMP
        WHERE EXISTS (SELECT 1 FROM users)
          AND NOT EXISTS (SELECT 1 FROM system_state WHERE key = 'bootstrap_seed_version')
    """))


def downgrade():
    # Deleted accounts and monitoring history can only be restored from the
    # deployment-time database backup.
    pass
