"""clients.start_param: the /start link tail the client first came with

Revision ID: a1f47c2d9e60
Revises: 3e8d5b1c7a42
Create Date: 2026-10-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1f47c2d9e60'
down_revision: Union[str, Sequence[str], None] = '3e8d5b1c7a42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Хвост ссылки t.me/<bot>?start=<хвост> первого захода — текстом у клиента (решение 15). NULL — без ссылки."""
    op.add_column('clients', sa.Column('start_param', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('clients', 'start_param')
