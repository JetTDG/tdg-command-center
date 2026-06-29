"""add source and division columns to doc_envelopes

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-29

Adds:
  - source   VARCHAR(20)  default 'api'        — 'api' | 'personal'
  - division VARCHAR(20)  default 'Residential' — 'Residential' | 'CRE'

Backfills division=CRE for existing rows whose subject contains CRE keywords.
All other existing rows default to source='api', division='Residential'.
"""
from alembic import op
import sqlalchemy as sa


revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('doc_envelopes',
        sa.Column('source', sa.String(20), nullable=False,
                  server_default='api'))
    op.add_column('doc_envelopes',
        sa.Column('division', sa.String(20), nullable=False,
                  server_default='Residential'))

    # Backfill CRE division for existing rows based on subject keywords
    op.execute("""
        UPDATE doc_envelopes
        SET division = 'CRE'
        WHERE
            subject ILIKE '%commercial%'
            OR subject ILIKE '%CRE%'
            OR subject ILIKE '%NDA%'
            OR subject ILIKE '% LLC%'
            OR subject ILIKE '%Land Parcel%'
            OR subject ILIKE '%Industrial%'
            OR subject ILIKE '%Warehouse%'
            OR subject ILIKE '%Gratiot%'
            OR doc_type IN ('nda', 'commercial_pa', 'cre_listing')
    """)


def downgrade():
    op.drop_column('doc_envelopes', 'division')
    op.drop_column('doc_envelopes', 'source')
