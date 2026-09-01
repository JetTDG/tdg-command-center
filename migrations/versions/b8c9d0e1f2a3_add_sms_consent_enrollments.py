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
    # This application currently calls db.create_all() while Flask imports,
    # before the Railway start command invokes `flask db upgrade`.  Reconcile
    # that precreated table instead of attempting duplicate DDL, but fail closed
    # if the precreated shape is incomplete.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'sms_consent_enrollments' in inspector.get_table_names():
        existing = {column['name'] for column in inspector.get_columns('sms_consent_enrollments')}
        required = {
            'id', 'receipt_id', 'submission_token', 'full_name', 'company_email',
            'mobile_number', 'consent_granted', 'consent_method', 'policy_version',
            'consent_copy_sha256', 'consented_at', 'ip_address_sha256', 'user_agent',
        }
        missing = sorted(required - existing)
        if missing:
            raise RuntimeError(
                'sms_consent_enrollments exists with missing columns: ' + ', '.join(missing)
            )
        return

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
    if 'sms_consent_enrollments' not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index('ix_sms_consent_enrollments_consented_at', table_name='sms_consent_enrollments')
    op.drop_index('ix_sms_consent_enrollments_mobile_number', table_name='sms_consent_enrollments')
    op.drop_index('ix_sms_consent_enrollments_company_email', table_name='sms_consent_enrollments')
    op.drop_index('ix_sms_consent_enrollments_receipt_id', table_name='sms_consent_enrollments')
    op.drop_table('sms_consent_enrollments')