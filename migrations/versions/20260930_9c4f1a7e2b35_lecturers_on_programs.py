"""lecturers belong to programs, not sessions

Revision ID: 9c4f1a7e2b35
Revises: 7b1e2c9a4d10
Create Date: 2026-09-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9c4f1a7e2b35'
down_revision: Union[str, Sequence[str], None] = '7b1e2c9a4d10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _link_table(name: str, owner: str, owner_table: str) -> None:
    op.create_table(
        name,
        sa.Column(owner, sa.BigInteger(), nullable=False),
        sa.Column('lecturer_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint([owner], [f'{owner_table}.id'], name=op.f(f'fk_{name}_{owner}_{owner_table}'),
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['lecturer_id'], ['lecturers.id'], name=op.f(f'fk_{name}_lecturer_id_lecturers'),
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint(owner, 'lecturer_id', name=op.f(f'pk_{name}')),
    )
    op.create_index(f'ix_{name}_lecturer_id', name, ['lecturer_id'], unique=False)


def upgrade() -> None:
    """Курс авторский: его читают одни и те же лекторы, поэтому связь переносится с потока на курс."""
    _link_table('program_lecturers', 'program_id', 'programs')
    op.execute("""
        INSERT INTO program_lecturers (program_id, lecturer_id)
        SELECT DISTINCT s.program_id, sl.lecturer_id
        FROM session_lecturers sl JOIN sessions s ON s.id = sl.session_id
    """)
    op.drop_index('ix_session_lecturers_lecturer_id', table_name='session_lecturers')
    op.drop_table('session_lecturers')


def downgrade() -> None:
    """Обратно: каждый поток получает лекторов своего курса."""
    _link_table('session_lecturers', 'session_id', 'sessions')
    op.execute("""
        INSERT INTO session_lecturers (session_id, lecturer_id)
        SELECT s.id, pl.lecturer_id
        FROM program_lecturers pl JOIN sessions s ON s.program_id = pl.program_id
    """)
    op.drop_index('ix_program_lecturers_lecturer_id', table_name='program_lecturers')
    op.drop_table('program_lecturers')
