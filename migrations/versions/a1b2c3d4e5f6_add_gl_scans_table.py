"""add gl_scans table

Revision ID: a1b2c3d4e5f6
Revises: bdd2897860e3
Create Date: 2026-06-09
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = 'bdd2897860e3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'gl_scans',
        sa.Column('id',          sa.Integer(),     nullable=False),
        sa.Column('slug',        sa.String(80),    nullable=False),
        sa.Column('city',        sa.String(80),    nullable=True),
        sa.Column('vertical',    sa.String(80),    nullable=True),
        sa.Column('event_type',  sa.String(20),    nullable=False),
        sa.Column('name',        sa.String(120),   nullable=True),
        sa.Column('phone',       sa.String(30),    nullable=True),
        sa.Column('address',     sa.String(200),   nullable=True),
        sa.Column('fub_id',      sa.String(50),    nullable=True),
        sa.Column('fub_status',  sa.String(30),    nullable=True),
        sa.Column('ip',          sa.String(60),    nullable=True),
        sa.Column('user_agent',  sa.String(300),   nullable=True),
        sa.Column('created_at',  sa.DateTime(),    nullable=True),
        sa.PrimaryKeyConstraint('id', name='pk_gl_scans'),
    )
    op.create_index('ix_gl_scans_slug',       'gl_scans', ['slug'])
    op.create_index('ix_gl_scans_created_at', 'gl_scans', ['created_at'])


def downgrade():
    op.drop_index('ix_gl_scans_created_at', table_name='gl_scans')
    op.drop_index('ix_gl_scans_slug',       table_name='gl_scans')
    op.drop_table('gl_scans')
