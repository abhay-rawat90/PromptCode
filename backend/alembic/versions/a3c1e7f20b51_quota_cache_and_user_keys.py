"""llm cache, per-model usage and users' own API keys

Revision ID: a3c1e7f20b51
Revises: 154c449d3449
Create Date: 2026-11-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c1e7f20b51'
down_revision: Union[str, Sequence[str], None] = '154c449d3449'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'model_usage_daily',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('requests', sa.Integer(), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('cache_hits', sa.Integer(), nullable=False),
        sa.Column('failures', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('day', 'model', name='uq_model_usage_daily_day_model'),
    )
    op.create_index('ix_model_usage_daily_day', 'model_usage_daily', ['day'], unique=False)
    op.create_table(
        'llm_cache',
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', TS, nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )
    op.create_index('ix_llm_cache_created_at', 'llm_cache', ['created_at'], unique=False)
    # Plain add_column (not batch mode): a batch rebuild of `users` on SQLite would drop the
    # expression index ix_users_username_lower.
    op.add_column('users', sa.Column('gemini_key_enc', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('gemini_key_hint', sa.String(length=8), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'gemini_key_hint')
    op.drop_column('users', 'gemini_key_enc')
    op.drop_index('ix_llm_cache_created_at', table_name='llm_cache')
    op.drop_table('llm_cache')
    op.drop_index('ix_model_usage_daily_day', table_name='model_usage_daily')
    op.drop_table('model_usage_daily')
