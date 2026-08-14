"""Initial monitoring schema."""
from alembic import op

from cs2_inventory import models  # noqa: F401
from cs2_inventory.database import db

revision = "20260814_01"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    db.metadata.create_all(bind=op.get_bind())

def downgrade():
    db.metadata.drop_all(bind=op.get_bind())
