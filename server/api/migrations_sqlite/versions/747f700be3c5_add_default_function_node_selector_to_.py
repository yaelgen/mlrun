"""add default function node selector to project

Revision ID: 747f700be3c5
Revises: 0b224a1b4e0d
Create Date: 2024-03-18 17:58:40.946907

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "747f700be3c5"
down_revision = "0b224a1b4e0d"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(
            sa.Column("default_function_node_selector", sa.JSON(), nullable=True)
        )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("default_function_node_selector")
    # ### end Alembic commands ###
