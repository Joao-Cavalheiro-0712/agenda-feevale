"""tour de primeiro acesso

Revision ID: a9d15c30e6b2
Revises: f7c04e2b9d51
Create Date: 2026-09-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'a9d15c30e6b2'
down_revision = 'f7c04e2b9d51'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('tour_done_at', sa.DateTime(), nullable=True))
    # Quem JÁ terminou o onboarding não pode ganhar um tour de boas-vindas na
    # próxima vez que abrir o app: para essas contas, o tour já é passado.
    op.execute(
        "UPDATE users SET tour_done_at = CURRENT_TIMESTAMP "
        "WHERE onboarding_done = true AND tour_done_at IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('tour_done_at')
