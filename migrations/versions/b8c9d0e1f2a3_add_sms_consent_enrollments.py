"""add TDG Agent Operations SMS consent enrollments

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

revision = 'b8c9d0e1f2a3'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sms_consent_enrollments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('receipt_id', sa.String(length=36), nullable=False),
        sa.Column('submission_token', sa.String(length=64), nullable=False),
        sa.Column('full_name', sa.String(length=160), nullable=False),
        sa.Column('company_email', sa.String(length=254), nullable=False),
        sa.Column('mobile_number', sa.String(length=16), nullable=False),
        sa.Column('consent_granted', sa.Boolean(), nullable=False),
        sa.Column('consent_method', sa.String(length=30), nullable=False),
        sa.Column('policy_version', sa.String(length=20), nullable=False),
        sa.Column('consent_copy_sha256', sa.String(length=64), nullable=False),
        sa.Column('consented_at', sa.DateTime(), nullable=False),
        sa.Column('ip_address_sha256', sa.String(length=64), nullable=False),
        sa.Column('user_agent', sa.String(length=300)),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('receipt_id'),
        sa.UniqueConstraint('submission_token'),
    )
    op.create_index('ix_sms_consent_enrollments_receipt_id', 'sms_consent_enrollments', ['receipt_id'], unique=True)
    op.create_index('ix_sms_consent_enrollments_company_email', 'sms_consent_enrollments', ['company_email'])
    op.create_index('ix_sms_consent_enrollments_mobile_number', 'sms_consent_enrollments', ['mobile_number'])
    op.create_index('ix_sms_consent_enrollments_consented_at', 'sms_consent_enrollments', ['consented_at'])


def downgrade():
    op.drop_index('ix_sms_consent_enrollments_consented_at', table_name='sms_consent_enrollments')
    op.drop_index('ix_sms_consent_enrollments_mobile_number', table_name='sms_consent_enrollments')
    op.drop_index('ix_sms_consent_enrollments_company_email', table_name='sms_consent_enrollments')
    op.drop_index('ix_sms_consent_enrollments_receipt_id', table_name='sms_consent_enrollments')
    op.drop_table('sms_consent_enrollments')