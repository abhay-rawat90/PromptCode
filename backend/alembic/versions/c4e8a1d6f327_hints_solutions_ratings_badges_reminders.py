"""hints, shared solutions, ratings, badges and streak reminders

Revision ID: c4e8a1d6f327
Revises: b7d2f4c9e810
Create Date: 2026-11-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e8a1d6f327'
down_revision: Union[str, Sequence[str], None] = 'b7d2f4c9e810'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # Plain add_column (not batch mode): a batch rebuild of `users` on SQLite would drop the
    # expression index ix_users_username_lower.
    op.add_column('users', sa.Column('reminder_emails', sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column('users', sa.Column('last_reminded', sa.Date(), nullable=True))
    op.add_column('problems', sa.Column('hints', sa.JSON(), nullable=True))
    op.add_column('submissions', sa.Column('shared', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_index('ix_submissions_shared', 'submissions', ['shared'], unique=False)
    op.create_table(
        'hint_opens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('problem_id', sa.Integer(), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('created_at', TS, nullable=False),
        sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'problem_id', name='uq_hint_opens_user_problem'),
    )
    op.create_index('ix_hint_opens_user_id', 'hint_opens', ['user_id'], unique=False)
    op.create_table(
        'solution_votes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', TS, nullable=False),
        sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('submission_id', 'user_id', name='uq_solution_votes_submission_user'),
    )
    op.create_index('ix_solution_votes_submission_id', 'solution_votes', ['submission_id'], unique=False)
    op.create_table(
        'problem_ratings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('problem_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('liked', sa.Boolean(), nullable=True),
        sa.Column('difficulty', sa.String(length=10), nullable=True),
        sa.Column('updated_at', TS, nullable=False),
        sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('problem_id', 'user_id', name='uq_problem_ratings_problem_user'),
    )
    op.create_index('ix_problem_ratings_problem_id', 'problem_ratings', ['problem_id'], unique=False)
    op.create_table(
        'user_badges',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('badge', sa.String(length=30), nullable=False),
        sa.Column('earned_at', TS, nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'badge', name='uq_user_badges_user_badge'),
    )
    op.create_index('ix_user_badges_user_id', 'user_badges', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_user_badges_user_id', table_name='user_badges')
    op.drop_table('user_badges')
    op.drop_index('ix_problem_ratings_problem_id', table_name='problem_ratings')
    op.drop_table('problem_ratings')
    op.drop_index('ix_solution_votes_submission_id', table_name='solution_votes')
    op.drop_table('solution_votes')
    op.drop_index('ix_hint_opens_user_id', table_name='hint_opens')
    op.drop_table('hint_opens')
    op.drop_index('ix_submissions_shared', table_name='submissions')
    op.drop_column('submissions', 'shared')
    op.drop_column('problems', 'hints')
    op.drop_column('users', 'last_reminded')
    op.drop_column('users', 'reminder_emails')
