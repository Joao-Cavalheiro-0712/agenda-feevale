"""registro de eventos de gateway — idempotência da cobrança

Revision ID: c1f6b93e08a4
Revises: b8e3a5d41c72
Create Date: 2026-09-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'c1f6b93e08a4'
down_revision = 'b8e3a5d41c72'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'webhook_events',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('event_id', sa.String(length=120), nullable=False),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('type', sa.String(length=60), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', name='uq_webhook_event_id'),
    )
    op.create_index('ix_webhook_events_event_id', 'webhook_events', ['event_id'])
    op.create_index('ix_webhook_events_user_id', 'webhook_events', ['user_id'])
    op.create_index('ix_webhook_events_created_at', 'webhook_events', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_webhook_events_created_at', table_name='webhook_events')
    op.drop_index('ix_webhook_events_user_id', table_name='webhook_events')
    op.drop_index('ix_webhook_events_event_id', table_name='webhook_events')
    op.drop_table('webhook_events')
