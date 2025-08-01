"""empty message

Revision ID: 2cd4b44e5145
Revises: cf508f03a764
Create Date: 2025-08-01 08:14:26.723644

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '2cd4b44e5145'
down_revision = 'cf508f03a764'
branch_labels = None
depends_on = None

def upgrade():
    # Safe even on SQLite
  

    with op.batch_alter_table('cart_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('discount', sa.Numeric(precision=5, scale=2), nullable=True))
        batch_op.alter_column('quantity',
            existing_type=sa.INTEGER(),
            type_=sa.Numeric(precision=12, scale=3),
            existing_nullable=False)
        batch_op.alter_column('unit_price',
            existing_type=sa.FLOAT(),
            type_=sa.Numeric(precision=12, scale=2),
            nullable=False)
        batch_op.alter_column('total_price',
            existing_type=sa.FLOAT(),
            type_=sa.Numeric(precision=12, scale=2),
            nullable=False)

def downgrade():
    with op.batch_alter_table('cart_items', schema=None) as batch_op:
        batch_op.alter_column('total_price',
            existing_type=sa.Numeric(precision=12, scale=2),
            type_=sa.FLOAT(),
            nullable=True)
        batch_op.alter_column('unit_price',
            existing_type=sa.Numeric(precision=12, scale=2),
            type_=sa.FLOAT(),
            nullable=True)
        batch_op.alter_column('quantity',
            existing_type=sa.Numeric(precision=12, scale=3),
            type_=sa.INTEGER(),
            existing_nullable=False)
        batch_op.drop_column('discount')

    op.drop_constraint(None, 'businesses', type_='foreignkey')
