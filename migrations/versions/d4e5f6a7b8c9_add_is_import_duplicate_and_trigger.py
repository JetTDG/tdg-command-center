"""add is_import_duplicate column + enforce-archived trigger

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-18

Adds is_import_duplicate boolean to transactions.
Adds DB trigger: if is_import_duplicate=TRUE, archived is ALWAYS forced TRUE
on INSERT or UPDATE — making confirmed import duplicates permanently invisible
to all reporting queries regardless of any app-level reset.
"""
from alembic import op
import sqlalchemy as sa


revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # Add column (idempotent)
    conn.execute(sa.text("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS is_import_duplicate BOOLEAN NOT NULL DEFAULT FALSE
    """))

    # Create the trigger function
    conn.execute(sa.text("""
        CREATE OR REPLACE FUNCTION enforce_import_duplicate_archived()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.is_import_duplicate = TRUE THEN
                NEW.archived = TRUE;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """))

    # Drop and recreate trigger (idempotent)
    conn.execute(sa.text("""
        DROP TRIGGER IF EXISTS trg_import_dup_always_archived ON transactions
    """))
    conn.execute(sa.text("""
        CREATE TRIGGER trg_import_dup_always_archived
        BEFORE INSERT OR UPDATE ON transactions
        FOR EACH ROW
        EXECUTE FUNCTION enforce_import_duplicate_archived()
    """))


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text("DROP TRIGGER IF EXISTS trg_import_dup_always_archived ON transactions"))
    conn.execute(sa.text("DROP FUNCTION IF EXISTS enforce_import_duplicate_archived()"))
    conn.execute(sa.text("ALTER TABLE transactions DROP COLUMN IF EXISTS is_import_duplicate"))
