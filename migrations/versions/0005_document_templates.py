"""academy document templates (Agent 4)

Revision ID: 0005_document_templates
Revises: 0004_documents
Create Date: 2026-11-01 20:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0005_document_templates'
down_revision: Union[str, Sequence[str], None] = '0004_documents'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('document_templates',
    sa.Column('id', sa.String(length=20), nullable=False),
    sa.Column('doc_type', sa.String(length=40), nullable=False),
    sa.Column('language', sa.String(length=5), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('filename', sa.String(length=300), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('headings', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('uploaded_by', sa.String(length=200), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reviewed_by', sa.String(length=200), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('document_templates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_document_templates_doc_type'), ['doc_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_document_templates_sha256'), ['sha256'], unique=True)
        batch_op.create_index(batch_op.f('ix_document_templates_status'), ['status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('document_templates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_document_templates_status'))
        batch_op.drop_index(batch_op.f('ix_document_templates_sha256'))
        batch_op.drop_index(batch_op.f('ix_document_templates_doc_type'))
    op.drop_table('document_templates')
