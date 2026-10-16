"""auth tokens and usage tracking

Revision ID: 154c449d3449
Revises: 579a2340abd4
Create Date: 2026-10-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '154c449d3449'
down_revision: Union[str, Sequence[str], None] = '579a2340abd4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'auth_tokens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('purpose', sa.String(length=20), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('expires_at', TS, nullable=False),
        sa.Column('used_at', TS, nullable=True),
        sa.Column('created_at', TS, nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index('ix_auth_tokens_user_id', 'auth_tokens', ['user_id'], unique=False)
    op.create_table(
        'usage_daily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('requests', sa.Integer(), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('day', 'kind', 'user_id', name='uq_usage_daily_day_kind_user'),
    )
    op.create_index('ix_usage_daily_day', 'usage_daily', ['day'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_usage_daily_day', table_name='usage_daily')
    op.drop_table('usage_daily')
    op.drop_index('ix_auth_tokens_user_id', table_name='auth_tokens')
    op.drop_table('auth_tokens')
