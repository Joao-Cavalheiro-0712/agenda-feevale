"""ciclo de cobrança na assinatura (mensal/anual)

Revision ID: a4d7f2c91e60
Revises: 9c2e8a4b17f3
Create Date: 2026-09-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a4d7f2c91e60'
down_revision = '9c2e8a4b17f3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('subscriptions') as batch:
        batch.add_column(sa.Column('cycle', sa.String(length=10), nullable=False,
                                   server_default='MONTHLY'))


def downgrade() -> None:
    with op.batch_alter_table('subscriptions') as batch:
        batch.drop_column('cycle')
