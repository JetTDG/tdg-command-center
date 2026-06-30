"""add indexes to transactions table for query performance

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-30

Adds indexes on the columns used in every My Business query:
  - archived        (always filtered)
  - status          (summary counts, status filter, sort)
  - year            (primary year filter)
  - close_date      (Closed year fallback, date range filter)
  - signed_date     (year fallback for rows with no close_date)
  - projected_close_date  (Pending year + month filter)
  - agent_id        (agent filter)

These are read-only index additions — zero risk to existing data.
Safe to run on a live database (CREATE INDEX CONCURRENTLY not needed
on Railway's Postgres; row count ~600 so creation is instant).
"""
from alembic import op


revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_transactions_archived',             'transactions', ['archived'])
    op.create_index('ix_transactions_status',               'transactions', ['status'])
    op.create_index('ix_transactions_year',                 'transactions', ['year'])
    op.create_index('ix_transactions_close_date',           'transactions', ['close_date'])
    op.create_index('ix_transactions_signed_date',          'transactions', ['signed_date'])
    op.create_index('ix_transactions_projected_close_date', 'transactions', ['projected_close_date'])
    op.create_index('ix_transactions_agent_id',             'transactions', ['agent_id'])
    # Composite index for the most common hot path: archived=F + year=N
    op.create_index('ix_transactions_archived_year',        'transactions', ['archived', 'year'])


def downgrade():
    op.drop_index('ix_transactions_archived_year',        table_name='transactions')
    op.drop_index('ix_transactions_agent_id',             table_name='transactions')
    op.drop_index('ix_transactions_projected_close_date', table_name='transactions')
    op.drop_index('ix_transactions_signed_date',          table_name='transactions')
    op.drop_index('ix_transactions_close_date',           table_name='transactions')
    op.drop_index('ix_transactions_year',                 table_name='transactions')
    op.drop_index('ix_transactions_status',               table_name='transactions')
    op.drop_index('ix_transactions_archived',             table_name='transactions')
