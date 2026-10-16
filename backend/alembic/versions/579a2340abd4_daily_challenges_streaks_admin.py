"""daily challenges, streaks, admin

Revision ID: 579a2340abd4
Revises: 901edd364ffa
Create Date: 2026-10-16 14:15:31.916800

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '579a2340abd4'
down_revision: Union[str, Sequence[str], None] = '901edd364ffa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'daily_queue',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', TS, nullable=False),
        sa.Column('used_on', sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'daily_solves',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('created_at', TS, nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'day', name='uq_daily_solves_user_day'),
    )
    op.create_index('ix_daily_solves_user_id', 'daily_solves', ['user_id'], unique=False)
    op.create_table(
        'daily_challenges',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('problem_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=10), nullable=False),
        sa.Column('created_at', TS, nullable=False),
        sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('problem_id'),
    )
    op.create_index('ix_daily_challenges_day', 'daily_challenges', ['day'], unique=True)

    # Plain ADD COLUMNs (no batch mode): SQLite supports them natively, and a batch rebuild of
    # `users` would silently drop the expression index ix_users_username_lower.
    op.add_column('problems', sa.Column('available_from', TS, nullable=True))
    op.create_index('ix_problems_available_from', 'problems', ['available_from'], unique=False)
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column('users', sa.Column('current_streak', sa.Integer(), server_default='0', nullable=False))
    op.add_column('users', sa.Column('best_streak', sa.Integer(), server_default='0', nullable=False))
    op.add_column('users', sa.Column('last_daily_solved', sa.Date(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('last_daily_solved')
        batch_op.drop_column('best_streak')
        batch_op.drop_column('current_streak')
        batch_op.drop_column('is_admin')
    # The batch rebuild above drops expression indexes on SQLite; restore it.
    op.execute(sa.text('CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username_lower ON users (lower(username))'))

    op.drop_index('ix_problems_available_from', table_name='problems')
    with op.batch_alter_table('problems', schema=None) as batch_op:
        batch_op.drop_column('available_from')

    op.drop_index('ix_daily_challenges_day', table_name='daily_challenges')
    op.drop_table('daily_challenges')
    op.drop_index('ix_daily_solves_user_id', table_name='daily_solves')
    op.drop_table('daily_solves')
    op.drop_table('daily_queue')
