"""generated_documents table (Agent 4: Document Generation)

Revision ID: 0004_documents
Revises: 0003_curriculum
Create Date: 2026-10-25 14:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004_documents'
down_revision: Union[str, Sequence[str], None] = '0003_curriculum'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('generated_documents',
    sa.Column('id', sa.String(length=20), nullable=False),
    sa.Column('doc_type', sa.String(length=40), nullable=False),
    sa.Column('course_id', sa.String(length=20), nullable=True),
    sa.Column('curriculum_id', sa.String(length=20), nullable=True),
    sa.Column('curriculum_revision', sa.Integer(), nullable=True),
    sa.Column('language', sa.String(length=5), nullable=False),
    sa.Column('title', sa.String(length=400), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('basis', sa.String(length=300), nullable=False),
    sa.Column('template_version', sa.String(length=20), nullable=False),
    sa.Column('generation_mode', sa.String(length=20), nullable=False),
    sa.Column('content', sa.JSON(), nullable=False),
    sa.Column('notices', sa.JSON(), nullable=False),
    sa.Column('log', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('approved_by', sa.String(length=200), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('generated_documents', schema=None) as batch_op:
        for col in ('doc_type', 'course_id', 'curriculum_id', 'status'):
            batch_op.create_index(batch_op.f(f'ix_generated_documents_{col}'), [col], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('generated_documents', schema=None) as batch_op:
        for col in ('status', 'curriculum_id', 'course_id', 'doc_type'):
            batch_op.drop_index(batch_op.f(f'ix_generated_documents_{col}'))
    op.drop_table('generated_documents')
