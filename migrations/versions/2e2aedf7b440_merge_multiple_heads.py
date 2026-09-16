"""merge multiple heads

Revision ID: 2e2aedf7b440
Revises: 1cbe17df222c, 4a4f16d5c139
Create Date: 2026-09-16 13:11:33.852280

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2e2aedf7b440'
down_revision = ('1cbe17df222c', '4a4f16d5c139')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
