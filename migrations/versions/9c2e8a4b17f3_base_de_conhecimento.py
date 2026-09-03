"""memória de vocabulário por usuário

Revision ID: 9c2e8a4b17f3
Revises: 8b1f4c7d2a90
Create Date: 2026-09-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '9c2e8a4b17f3'
down_revision = '8b1f4c7d2a90'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'knowledge_entries',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('key_raw', sa.String(length=120), nullable=False),
        sa.Column('key_norm', sa.String(length=120), nullable=False),
        sa.Column('key_phonetic', sa.String(length=120), nullable=False),
        sa.Column('value', sa.String(length=60), nullable=False),
        sa.Column('hits', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'kind', 'key_norm', name='uq_knowledge_key'),
    )
    op.create_index('ix_knowledge_entries_user_id', 'knowledge_entries', ['user_id'])
    op.create_index('ix_knowledge_entries_kind', 'knowledge_entries', ['kind'])
    op.create_index('ix_knowledge_entries_key_norm', 'knowledge_entries', ['key_norm'])
    op.create_index('ix_knowledge_entries_key_phonetic', 'knowledge_entries', ['key_phonetic'])


def downgrade() -> None:
    op.drop_index('ix_knowledge_entries_key_phonetic', table_name='knowledge_entries')
    op.drop_index('ix_knowledge_entries_key_norm', table_name='knowledge_entries')
    op.drop_index('ix_knowledge_entries_kind', table_name='knowledge_entries')
    op.drop_index('ix_knowledge_entries_user_id', table_name='knowledge_entries')
    op.drop_table('knowledge_entries')
