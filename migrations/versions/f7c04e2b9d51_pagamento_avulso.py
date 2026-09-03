"""forma de pagamento e assinatura que não renova (Pix)

Revision ID: f7c04e2b9d51
Revises: e5b2d81a4f37
Create Date: 2026-09-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'f7c04e2b9d51'
down_revision = 'e5b2d81a4f37'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('subscriptions') as batch:
        # Assinaturas existentes são de cartão e renovam: o padrão preserva o
        # comportamento de quem já está pagando.
        batch.add_column(sa.Column('renews', sa.Boolean(), nullable=False,
                                   server_default=sa.true()))
        batch.add_column(sa.Column('payment_method', sa.String(length=20),
                                   nullable=False, server_default='card'))
        batch.add_column(sa.Column('renewal_notice_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('subscriptions') as batch:
        batch.drop_column('renewal_notice_at')
        batch.drop_column('payment_method')
        batch.drop_column('renews')
