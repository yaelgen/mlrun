"""add_created_time_to_function

Revision ID: 1bbd8b6105b0
Revises: 4acd9430b093
Create Date: 2023-05-23 11:14:23.507921

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision = '1bbd8b6105b0'
down_revision = '4acd9430b093'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('marketplace_sources',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=True),
    sa.Column('index', sa.Integer(), nullable=True),
    sa.Column('created', sa.TIMESTAMP(), nullable=True),
    sa.Column('updated', sa.TIMESTAMP(), nullable=True),
    sa.Column('object', sa.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', name='_marketplace_sources_uc')
    )
    op.drop_table('notifications')
    op.drop_table('hub_sources')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('hub_sources',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('name', sa.VARCHAR(length=255), nullable=True),
    sa.Column('index', sa.INTEGER(), nullable=True),
    sa.Column('created', sa.TIMESTAMP(), nullable=True),
    sa.Column('updated', sa.TIMESTAMP(), nullable=True),
    sa.Column('object', sqlite.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', name='_hub_sources_uc')
    )
    op.create_table('notifications',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('project', sa.VARCHAR(length=255), nullable=True),
    sa.Column('name', sa.VARCHAR(length=255), nullable=False),
    sa.Column('kind', sa.VARCHAR(length=255), nullable=False),
    sa.Column('message', sa.VARCHAR(length=255), nullable=False),
    sa.Column('severity', sa.VARCHAR(length=255), nullable=False),
    sa.Column('when', sa.VARCHAR(length=255), nullable=False),
    sa.Column('condition', sa.VARCHAR(length=255), nullable=False),
    sa.Column('params', sqlite.JSON(), nullable=True),
    sa.Column('run', sa.INTEGER(), nullable=True),
    sa.Column('sent_time', sa.TIMESTAMP(), nullable=True),
    sa.Column('status', sa.VARCHAR(length=255), nullable=False),
    sa.ForeignKeyConstraint(['run'], ['runs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', 'run', name='_notifications_uc')
    )
    op.drop_table('marketplace_sources')
    # ### end Alembic commands ###
