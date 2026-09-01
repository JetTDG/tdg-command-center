"""add sanitized Twilio webhook events

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa

revision = 'c9d0e1f2a3b4'
down_revision = 'b8c9d0e1f2a3'
branch_labels = None
depends_on = None

TABLE = 'sms_webhook_events'
REQUIRED = {
    'id', 'event_key', 'event_type', 'message_sid', 'message_status',
    'from_phone_sha256', 'to_phone_sha256', 'body_sha256', 'keyword',
    'error_code', 'received_at',
}


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        existing = {column['name'] for column in inspector.get_columns(TABLE)}
        missing = sorted(REQUIRED - existing)
        if missing:
            raise RuntimeError(TABLE + ' exists with missing columns: ' + ', '.join(missing))
        return
    op.create_table(
        TABLE,
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_key', sa.String(length=64), nullable=False),
        sa.Column('event_type', sa.String(length=20), nullable=False),
        sa.Column('message_sid', sa.String(length=34), nullable=False),
        sa.Column('message_status', sa.String(length=30)),
        sa.Column('from_phone_sha256', sa.String(length=64)),
        sa.Column('to_phone_sha256', sa.String(length=64)),
        sa.Column('body_sha256', sa.String(length=64)),
        sa.Column('keyword', sa.String(length=20)),
        sa.Column('error_code', sa.String(length=20)),
        sa.Column('received_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_key'),
    )
    op.create_index('ix_sms_webhook_events_event_type', TABLE, ['event_type'])
    op.create_index('ix_sms_webhook_events_message_sid', TABLE, ['message_sid'])
    op.create_index('ix_sms_webhook_events_received_at', TABLE, ['received_at'])


def downgrade():
    if TABLE not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index('ix_sms_webhook_events_received_at', table_name=TABLE)
    op.drop_index('ix_sms_webhook_events_message_sid', table_name=TABLE)
    op.drop_index('ix_sms_webhook_events_event_type', table_name=TABLE)
    op.drop_table(TABLE)