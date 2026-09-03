"""programa de indicação: código, atribuição e recompensas

Revision ID: b8e3a5d41c72
Revises: a4d7f2c91e60
Create Date: 2026-09-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b8e3a5d41c72'
down_revision = 'a4d7f2c91e60'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('referral_code', sa.String(length=16), nullable=True))
        batch.create_unique_constraint('uq_users_referral_code', ['referral_code'])
    op.create_index('ix_users_referral_code', 'users', ['referral_code'])

    op.create_table(
        'referrals',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('referrer_id', sa.String(length=36), nullable=False),
        sa.Column('referred_id', sa.String(length=36), nullable=False),
        sa.Column('code', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('signup_ip_hash', sa.String(length=64), nullable=False),
        sa.Column('signup_user_agent', sa.String(length=300), nullable=False),
        sa.Column('mesmo_ip_do_indicador', sa.Boolean(), nullable=False),
        sa.Column('rejection_reason', sa.String(length=60), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('qualified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rewarded_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['referrer_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['referred_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('referred_id', name='uq_referral_referred'),
    )
    op.create_index('ix_referrals_referrer_id', 'referrals', ['referrer_id'])
    op.create_index('ix_referrals_code', 'referrals', ['code'])
    op.create_index('ix_referrals_created_at', 'referrals', ['created_at'])

    op.create_table(
        'rewards',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('kind', sa.String(length=30), nullable=False),
        sa.Column('months', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(length=160), nullable=False),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_reason', sa.String(length=160), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_rewards_user_id', 'rewards', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_rewards_user_id', table_name='rewards')
    op.drop_table('rewards')
    op.drop_index('ix_referrals_created_at', table_name='referrals')
    op.drop_index('ix_referrals_code', table_name='referrals')
    op.drop_index('ix_referrals_referrer_id', table_name='referrals')
    op.drop_table('referrals')
    op.drop_index('ix_users_referral_code', table_name='users')
    with op.batch_alter_table('users') as batch:
        batch.drop_constraint('uq_users_referral_code', type_='unique')
        batch.drop_column('referral_code')
