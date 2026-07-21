"""add canonical conversion lead and assignment tables

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa

revision = 'a7b8c9d0e1f2'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'conversion_leads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fub_person_id', sa.String(length=50), nullable=False),
        sa.Column('lead_received_at', sa.DateTime(), nullable=False),
        sa.Column('fub_created_at', sa.DateTime()),
        sa.Column('fub_updated_at', sa.DateTime()),
        sa.Column('original_agent_id', sa.Integer()),
        sa.Column('current_agent_id', sa.Integer()),
        sa.Column('original_fub_user_id', sa.String(length=50)),
        sa.Column('current_fub_user_id', sa.String(length=50)),
        sa.Column('original_source', sa.String(length=200), nullable=False, server_default='Unknown'),
        sa.Column('original_source_family', sa.String(length=100), nullable=False, server_default='Unknown'),
        sa.Column('current_source', sa.String(length=200), nullable=False, server_default='Unknown'),
        sa.Column('current_source_family', sa.String(length=100), nullable=False, server_default='Unknown'),
        sa.Column('attribution_quality', sa.String(length=40), nullable=False, server_default='current_agent_backfill'),
        sa.Column('lead_type', sa.String(length=30), server_default='Team'),
        sa.Column('side', sa.String(length=30), server_default='Unknown'),
        sa.Column('stage', sa.String(length=100)),
        sa.Column('deal_status', sa.String(length=100)),
        sa.Column('is_soi', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('is_bulk', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('contacted_at', sa.DateTime()),
        sa.Column('appointment_set_at', sa.DateTime()),
        sa.Column('appointment_held_at', sa.DateTime()),
        sa.Column('signed_at', sa.DateTime()),
        sa.Column('pending_at', sa.DateTime()),
        sa.Column('closed_at', sa.DateTime()),
        sa.Column('transaction_id', sa.Integer()),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_synced_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['current_agent_id'], ['agents.id']),
        sa.ForeignKeyConstraint(['original_agent_id'], ['agents.id']),
        sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('fub_person_id'),
    )
    op.create_index('ix_conversion_leads_fub_person_id', 'conversion_leads', ['fub_person_id'], unique=True)
    op.create_index('ix_conversion_leads_received', 'conversion_leads', ['lead_received_at'])
    op.create_index('ix_conversion_leads_original_agent_source', 'conversion_leads', ['original_agent_id', 'original_source'])
    op.create_index('ix_conversion_leads_current_agent_source', 'conversion_leads', ['current_agent_id', 'current_source'])

    op.create_table(
        'conversion_assignments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversion_lead_id', sa.Integer(), nullable=False),
        sa.Column('agent_id', sa.Integer()),
        sa.Column('fub_user_id', sa.String(length=50), nullable=False),
        sa.Column('assigned_at', sa.DateTime(), nullable=False),
        sa.Column('source', sa.String(length=40), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id']),
        sa.ForeignKeyConstraint(['conversion_lead_id'], ['conversion_leads.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversion_lead_id', 'fub_user_id', 'assigned_at', name='uq_conversion_assignment_observation'),
    )
    op.create_index('ix_conversion_assignments_lead_date', 'conversion_assignments', ['conversion_lead_id', 'assigned_at'])


def downgrade():
    op.drop_index('ix_conversion_assignments_lead_date', table_name='conversion_assignments')
    op.drop_table('conversion_assignments')
    op.drop_index('ix_conversion_leads_current_agent_source', table_name='conversion_leads')
    op.drop_index('ix_conversion_leads_original_agent_source', table_name='conversion_leads')
    op.drop_index('ix_conversion_leads_received', table_name='conversion_leads')
    op.drop_index('ix_conversion_leads_fub_person_id', table_name='conversion_leads')
    op.drop_table('conversion_leads')
