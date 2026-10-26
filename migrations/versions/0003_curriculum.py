"""curricula table (Agent 3: Curriculum Research & Accreditation Alignment)

Revision ID: 0003_curriculum
Revises: 0002
Create Date: 2026-10-25 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003_curriculum'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('curricula',
    sa.Column('id', sa.String(length=20), nullable=False),
    sa.Column('course_id', sa.String(length=20), nullable=True),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('department', sa.String(length=100), nullable=True),
    sa.Column('skill_level', sa.String(length=20), nullable=False),
    sa.Column('industry', sa.String(length=100), nullable=True),
    sa.Column('country', sa.String(length=100), nullable=True),
    sa.Column('language', sa.String(length=5), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('total_hours', sa.Float(), nullable=True),
    sa.Column('framework_ids', sa.JSON(), nullable=False),
    sa.Column('generation_mode', sa.String(length=20), nullable=False),
    sa.Column('content', sa.JSON(), nullable=False),
    sa.Column('alignment', sa.JSON(), nullable=False),
    sa.Column('review_log', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('approved_by', sa.String(length=200), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('curricula', schema=None) as batch_op:
        for col in ('course_id', 'department', 'skill_level', 'industry', 'country', 'status'):
            batch_op.create_index(batch_op.f(f'ix_curricula_{col}'), [col], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('curricula', schema=None) as batch_op:
        for col in ('status', 'country', 'industry', 'skill_level', 'department', 'course_id'):
            batch_op.drop_index(batch_op.f(f'ix_curricula_{col}'))
    op.drop_table('curricula')
