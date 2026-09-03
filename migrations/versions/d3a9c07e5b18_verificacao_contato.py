"""verificação de e-mail e de telefone

Revision ID: d3a9c07e5b18
Revises: c1f6b93e08a4
Create Date: 2026-09-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'd3a9c07e5b18'
down_revision = 'c1f6b93e08a4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('email_verified_at', sa.DateTime(), nullable=True))
        batch.add_column(sa.Column('phone_verified_at', sa.DateTime(), nullable=True))
    with op.batch_alter_table('link_tokens') as batch:
        batch.add_column(sa.Column('attempts', sa.Integer(), nullable=False,
                                   server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('link_tokens') as batch:
        batch.drop_column('attempts')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('phone_verified_at')
        batch.drop_column('email_verified_at')
