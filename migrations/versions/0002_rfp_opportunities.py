"""rfp opportunities table (Agent 2: RFP Research)

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('rfp_opportunities',
    sa.Column('id', sa.String(length=20), nullable=False),
    sa.Column('dedup_key', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('title_norm', sa.String(length=500), nullable=False),
    sa.Column('issuing_organization', sa.String(length=300), nullable=True),
    sa.Column('country', sa.String(length=10), nullable=False),
    sa.Column('deadline', sa.Date(), nullable=True),
    sa.Column('publication_date', sa.Date(), nullable=True),
    sa.Column('categories', sa.JSON(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('eligibility', sa.Text(), nullable=True),
    sa.Column('submission_link', sa.String(length=1000), nullable=True),
    sa.Column('documents', sa.JSON(), nullable=False),
    sa.Column('source_name', sa.String(length=200), nullable=False),
    sa.Column('language', sa.String(length=5), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('summary_mode', sa.String(length=20), nullable=False),
    sa.Column('fit', sa.String(length=20), nullable=True),
    sa.Column('fit_reasons', sa.JSON(), nullable=False),
    sa.Column('matched_course_ids', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('synthetic', sa.Boolean(), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('alerted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('rfp_opportunities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_rfp_opportunities_dedup_key'), ['dedup_key'], unique=True)
        batch_op.create_index(batch_op.f('ix_rfp_opportunities_country'), ['country'], unique=False)
        batch_op.create_index(batch_op.f('ix_rfp_opportunities_deadline'), ['deadline'], unique=False)
        batch_op.create_index(batch_op.f('ix_rfp_opportunities_status'), ['status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('rfp_opportunities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rfp_opportunities_status'))
        batch_op.drop_index(batch_op.f('ix_rfp_opportunities_deadline'))
        batch_op.drop_index(batch_op.f('ix_rfp_opportunities_country'))
        batch_op.drop_index(batch_op.f('ix_rfp_opportunities_dedup_key'))
    op.drop_table('rfp_opportunities')