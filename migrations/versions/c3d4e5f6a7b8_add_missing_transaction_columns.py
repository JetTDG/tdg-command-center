"""add missing transaction columns (columns added to model without migrations)

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-06-18

Adds all columns present in models.py Transaction that are not in the
previous two migrations (bdd2897860e3 + a1b2c3d4e5f6).

Missing columns:
  adj_list_price, referral_pct, net_income,
  member3_name/pct/gci, member4_name/pct/gci,
  units, eo_fee, donation, other_fee, old_list_price,
  list_date, paid, link_to_file, admin_name,
  inspection_date, appraisal_date, amt_paid,
  archived, division, fub_id, docusign_id,
  created_at, updated_at
"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime


revision = 'c3d4e5f6a7b8'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def _col_exists(table, column):
    """Return True if column already exists (idempotent guard)."""
    from sqlalchemy import inspect
    conn = op.get_bind()
    insp = inspect(conn)
    cols = [c['name'] for c in insp.get_columns(table)]
    return column in cols


def upgrade():
    with op.batch_alter_table('transactions', schema=None) as batch_op:
        # Financials
        if not _col_exists('transactions', 'adj_list_price'):
            batch_op.add_column(sa.Column('adj_list_price', sa.Float(), nullable=True))
        if not _col_exists('transactions', 'referral_pct'):
            batch_op.add_column(sa.Column('referral_pct', sa.Float(), nullable=True))
        if not _col_exists('transactions', 'net_income'):
            batch_op.add_column(sa.Column('net_income', sa.Float(), nullable=True))
        if not _col_exists('transactions', 'eo_fee'):
            batch_op.add_column(sa.Column('eo_fee', sa.Float(), nullable=True))
        if not _col_exists('transactions', 'donation'):
            batch_op.add_column(sa.Column('donation', sa.Float(), nullable=True))
        if not _col_exists('transactions', 'other_fee'):
            batch_op.add_column(sa.Column('other_fee', sa.Float(), nullable=True))
        if not _col_exists('transactions', 'old_list_price'):
            batch_op.add_column(sa.Column('old_list_price', sa.Float(), nullable=True))
        if not _col_exists('transactions', 'units'):
            batch_op.add_column(sa.Column('units', sa.Float(), nullable=True))
        if not _col_exists('transactions', 'amt_paid'):
            batch_op.add_column(sa.Column('amt_paid', sa.Float(), nullable=True))

        # Agent splits (member3 & 4)
        if not _col_exists('transactions', 'member3_name'):
            batch_op.add_column(sa.Column('member3_name', sa.String(length=100), nullable=True))
        if not _col_exists('transactions', 'member3_pct'):
            batch_op.add_column(sa.Column('member3_pct', sa.Float(), nullable=True))
        if not _col_exists('transactions', 'member3_gci'):
            batch_op.add_column(sa.Column('member3_gci', sa.Float(), nullable=True))
        if not _col_exists('transactions', 'member4_name'):
            batch_op.add_column(sa.Column('member4_name', sa.String(length=100), nullable=True))
        if not _col_exists('transactions', 'member4_pct'):
            batch_op.add_column(sa.Column('member4_pct', sa.Float(), nullable=True))
        if not _col_exists('transactions', 'member4_gci'):
            batch_op.add_column(sa.Column('member4_gci', sa.Float(), nullable=True))

        # Dates
        if not _col_exists('transactions', 'list_date'):
            batch_op.add_column(sa.Column('list_date', sa.Date(), nullable=True))
        if not _col_exists('transactions', 'inspection_date'):
            batch_op.add_column(sa.Column('inspection_date', sa.Date(), nullable=True))
        if not _col_exists('transactions', 'appraisal_date'):
            batch_op.add_column(sa.Column('appraisal_date', sa.Date(), nullable=True))

        # Flags / status
        if not _col_exists('transactions', 'paid'):
            batch_op.add_column(sa.Column('paid', sa.Boolean(), nullable=True, server_default='false'))
        if not _col_exists('transactions', 'archived'):
            batch_op.add_column(sa.Column('archived', sa.Boolean(), nullable=True, server_default='false'))

        # String fields
        if not _col_exists('transactions', 'link_to_file'):
            batch_op.add_column(sa.Column('link_to_file', sa.String(length=500), nullable=True))
        if not _col_exists('transactions', 'admin_name'):
            batch_op.add_column(sa.Column('admin_name', sa.String(length=100), nullable=True))
        if not _col_exists('transactions', 'division'):
            batch_op.add_column(sa.Column('division', sa.String(length=50), nullable=True))
        if not _col_exists('transactions', 'fub_id'):
            batch_op.add_column(sa.Column('fub_id', sa.String(length=50), nullable=True))
        if not _col_exists('transactions', 'docusign_id'):
            batch_op.add_column(sa.Column('docusign_id', sa.String(length=50), nullable=True))

        # Timestamps
        if not _col_exists('transactions', 'created_at'):
            batch_op.add_column(sa.Column('created_at', sa.DateTime(), nullable=True))
        if not _col_exists('transactions', 'updated_at'):
            batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True))


def downgrade():
    cols_to_drop = [
        'adj_list_price', 'referral_pct', 'net_income', 'eo_fee', 'donation',
        'other_fee', 'old_list_price', 'units', 'amt_paid',
        'member3_name', 'member3_pct', 'member3_gci',
        'member4_name', 'member4_pct', 'member4_gci',
        'list_date', 'inspection_date', 'appraisal_date',
        'paid', 'archived', 'link_to_file', 'admin_name',
        'division', 'fub_id', 'docusign_id',
        'created_at', 'updated_at',
    ]
    with op.batch_alter_table('transactions', schema=None) as batch_op:
        for col in cols_to_drop:
            if _col_exists('transactions', col):
                batch_op.drop_column(col)
