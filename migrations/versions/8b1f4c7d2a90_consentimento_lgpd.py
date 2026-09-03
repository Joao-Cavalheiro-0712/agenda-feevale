"""consentimento, idade e trava de menores (LGPD art. 8º §1º e art. 14)

Revision ID: 8b1f4c7d2a90
Revises: 540cc2347c1c
Create Date: 2026-09-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '8b1f4c7d2a90'
down_revision = '540cc2347c1c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'consent_records',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('kind', sa.String(length=30), nullable=False),
        sa.Column('version', sa.String(length=20), nullable=False),
        sa.Column('granted', sa.Boolean(), nullable=False),
        sa.Column('document_hash', sa.String(length=64), nullable=False),
        sa.Column('ip_hash', sa.String(length=64), nullable=False),
        sa.Column('user_agent', sa.String(length=300), nullable=False),
        sa.Column('origin', sa.String(length=20), nullable=False),
        sa.Column('guardian_name', sa.String(length=160), nullable=False),
        sa.Column('guardian_email', sa.String(length=200), nullable=False),
        sa.Column('guardian_relationship', sa.String(length=40), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_consent_records_user_id', 'consent_records', ['user_id'])
    op.create_index('ix_consent_records_kind', 'consent_records', ['kind'])
    op.create_index('ix_consent_records_created_at', 'consent_records', ['created_at'])

    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('birth_year', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('is_minor', sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
        batch.add_column(sa.Column('guardian_consent_at', sa.DateTime(timezone=True),
                                   nullable=True))
        batch.add_column(sa.Column('accepted_terms_version', sa.String(length=20),
                                   nullable=False, server_default=''))
        batch.add_column(sa.Column('accepted_privacy_version', sa.String(length=20),
                                   nullable=False, server_default=''))
        batch.add_column(sa.Column('ai_processing_enabled', sa.Boolean(), nullable=False,
                                   server_default=sa.true()))

    # Contas já existentes ficam sem versão aceita de propósito: no próximo
    # acesso o app pede o aceite da versão vigente e o ano de nascimento. É a
    # única forma honesta de ter prova de consentimento para quem entrou antes.


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('ai_processing_enabled')
        batch.drop_column('accepted_privacy_version')
        batch.drop_column('accepted_terms_version')
        batch.drop_column('guardian_consent_at')
        batch.drop_column('is_minor')
        batch.drop_column('birth_year')
    op.drop_index('ix_consent_records_created_at', table_name='consent_records')
    op.drop_index('ix_consent_records_kind', table_name='consent_records')
    op.drop_index('ix_consent_records_user_id', table_name='consent_records')
    op.drop_table('consent_records')
