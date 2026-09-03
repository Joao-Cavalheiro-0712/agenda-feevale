"""chaves de acesso (passkeys / Face ID)

Revision ID: e5b2d81a4f37
Revises: d3a9c07e5b18
Create Date: 2026-09-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'e5b2d81a4f37'
down_revision = 'd3a9c07e5b18'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'passkeys',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('credential_id', sa.String(length=500), nullable=False),
        sa.Column('public_key', sa.LargeBinary(), nullable=False),
        sa.Column('sign_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('label', sa.String(length=80), nullable=False, server_default=''),
        sa.Column('transports', sa.String(length=120), nullable=False, server_default=''),
        sa.Column('backed_up', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_passkeys_user_id', 'passkeys', ['user_id'])
    op.create_index('ix_passkeys_credential_id', 'passkeys', ['credential_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_passkeys_credential_id', table_name='passkeys')
    op.drop_index('ix_passkeys_user_id', table_name='passkeys')
    op.drop_table('passkeys')
