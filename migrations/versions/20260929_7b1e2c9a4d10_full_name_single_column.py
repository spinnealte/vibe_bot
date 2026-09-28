"""full name as a single column (clients, lecturers)

Revision ID: 7b1e2c9a4d10
Revises: 4cd3f432915c
Create Date: 2026-09-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7b1e2c9a4d10'
down_revision: Union[str, Sequence[str], None] = '4cd3f432915c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# concat_ws пропускает NULL: «Иванов Иван» без отчества — без хвостового пробела.
_JOIN = "NULLIF(concat_ws(' ', last_name, first_name, middle_name), '')"


def upgrade() -> None:
    """ФИО одной строкой: как написал клиент, так и храним (и так пойдёт в сертификат)."""
    op.add_column('clients', sa.Column('full_name', sa.String(length=300), nullable=True))
    op.execute(f"UPDATE clients SET full_name = {_JOIN}")
    for column in ('last_name', 'first_name', 'middle_name'):
        op.drop_column('clients', column)

    op.add_column('lecturers', sa.Column('full_name', sa.String(length=300), nullable=True))
    op.execute(f"UPDATE lecturers SET full_name = {_JOIN}")
    op.alter_column('lecturers', 'full_name', nullable=False)
    for column in ('last_name', 'first_name', 'middle_name'):
        op.drop_column('lecturers', column)


def downgrade() -> None:
    """Обратно на три колонки: первое слово — фамилия, второе — имя, остальное — отчество."""
    for table, required in (('clients', False), ('lecturers', True)):
        op.add_column(table, sa.Column('last_name', sa.String(length=100), nullable=True))
        op.add_column(table, sa.Column('first_name', sa.String(length=100), nullable=True))
        op.add_column(table, sa.Column('middle_name', sa.String(length=100), nullable=True))
        op.execute(f"""
            UPDATE {table} SET
                last_name = left(split_part(full_name, ' ', 1), 100),
                first_name = left(NULLIF(split_part(full_name, ' ', 2), ''), 100),
                middle_name = left(NULLIF(array_to_string((string_to_array(full_name, ' '))[3:], ' '), ''), 100)
            WHERE full_name IS NOT NULL
        """)
        if required:
            op.execute(f"UPDATE {table} SET first_name = '' WHERE first_name IS NULL")
            op.alter_column(table, 'last_name', nullable=False)
            op.alter_column(table, 'first_name', nullable=False)
        op.drop_column(table, 'full_name')
