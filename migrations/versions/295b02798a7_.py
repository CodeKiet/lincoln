"""empty message

Revision ID: 295b02798a7
Revises: 338e5ac5577
Create Date: 2015-02-09 20:55:29.645749

"""

# revision identifiers, used by Alembic.
revision = '295b02798a7'
down_revision = '338e5ac5577'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.drop_constraint('output_address_hash_fkey', 'output')
    op.drop_index('ix_address_hash', table_name='address')
    op.drop_index('address_version', table_name='address')
    op.add_column('output', sa.Column('address_version', sa.Integer(), nullable=True))
    op.create_index('address_hash', 'address', ['hash', 'version'], unique=True)
    op.create_foreign_key('output_address_hash_fkey', 'output', 'address',
                          ['address_hash', 'address_version'],
                          ['hash', 'version'])


def downgrade():
    op.drop_constraint('output_address_hash_fkey', 'output')
    op.drop_index('address_hash', table_name='address')
    op.drop_column('output', 'address_version')
    op.add_index('address_version', 'address', 'version')
    op.add_index('ix_address_hash', 'address', 'hash')
    op.create_foreign_key('output_address_hash_fkey', 'output', 'address',
                          ['address_hash'], ['hash'])
