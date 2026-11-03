"""marketing delivery log (Agent 5: sending approved drafts)

Revision ID: 0006_marketing_deliveries
Revises: 0005_document_templates
Create Date: 2026-11-03 02:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0006_marketing_deliveries'
down_revision: Union[str, Sequence[str], None] = '0005_document_templates'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('marketing_deliveries',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('draft_id', sa.String(length=36), nullable=False),
    sa.Column('channel', sa.String(length=30), nullable=False),
    sa.Column('target', sa.String(length=200), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('requested_by', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['draft_id'], ['marketing_drafts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('marketing_deliveries', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_marketing_deliveries_draft_id'), ['draft_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_marketing_deliveries_channel'), ['channel'], unique=False)
        batch_op.create_index(batch_op.f('ix_marketing_deliveries_status'), ['status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('marketing_deliveries', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_marketing_deliveries_status'))
        batch_op.drop_index(batch_op.f('ix_marketing_deliveries_channel'))
        batch_op.drop_index(batch_op.f('ix_marketing_deliveries_draft_id'))
    op.drop_table('marketing_deliveries')
