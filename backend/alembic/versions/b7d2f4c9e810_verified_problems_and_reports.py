"""verified problems, hiding and problem reports

Revision ID: b7d2f4c9e810
Revises: a3c1e7f20b51
Create Date: 2026-11-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d2f4c9e810'
down_revision: Union[str, Sequence[str], None] = 'a3c1e7f20b51'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column('problems', sa.Column('reference_prompt', sa.Text(), nullable=True))
    op.add_column('problems', sa.Column('verified_at', TS, nullable=True))
    op.add_column('problems', sa.Column('hidden_at', TS, nullable=True))
    op.add_column('problems', sa.Column('hidden_reason', sa.String(length=300), nullable=True))
    op.create_table(
        'problem_reports',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('problem_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=20), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('resolved_at', TS, nullable=True),
        sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('problem_id', 'user_id', name='uq_problem_reports_problem_user'),
    )
    op.create_index('ix_problem_reports_problem_id', 'problem_reports', ['problem_id'], unique=False)
    op.create_index('ix_problem_reports_status', 'problem_reports', ['status'], unique=False)
    # Daily problems were verified when they were generated.
    op.execute(
        "UPDATE problems SET verified_at = created_at "
        "WHERE id IN (SELECT problem_id FROM daily_challenges WHERE source = 'ai')"
    )


def downgrade() -> None:
    op.drop_index('ix_problem_reports_status', table_name='problem_reports')
    op.drop_index('ix_problem_reports_problem_id', table_name='problem_reports')
    op.drop_table('problem_reports')
    op.drop_column('problems', 'hidden_reason')
    op.drop_column('problems', 'hidden_at')
    op.drop_column('problems', 'verified_at')
    op.drop_column('problems', 'reference_prompt')
