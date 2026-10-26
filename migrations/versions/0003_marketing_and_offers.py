"""marketing agent tables and shared course offers

Adds course_offers (shared by Agent 1 and Agent 5), marketing_campaigns and marketing_drafts.
Existing tables are not changed, so existing data is preserved.

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "course_offers",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(20), nullable=False),
        sa.Column("title_en", sa.String(200), nullable=False),
        sa.Column("title_ar", sa.String(200), nullable=True),
        sa.Column("discount_percent", sa.Float(), nullable=True),
        sa.Column("price_amount", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_status", sa.String(20), nullable=False),
        sa.Column("proposed_by", sa.String(100), nullable=True),
        sa.Column("approved_by", sa.String(100), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("course_offers") as b:
        b.create_index("ix_course_offers_course_id", ["course_id"])
        b.create_index("ix_course_offers_approval_status", ["approval_status"])

    op.create_table(
        "marketing_campaigns",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("course_id", sa.String(20), nullable=False),
        sa.Column("brief", sa.JSON(), nullable=False),
        sa.Column("strategy", sa.JSON(), nullable=False),
        sa.Column("facts_snapshot", sa.JSON(), nullable=False),
        sa.Column("generator", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("marketing_campaigns") as b:
        b.create_index("ix_marketing_campaigns_course_id", ["course_id"])

    op.create_table(
        "marketing_drafts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("campaign_id", sa.String(36), nullable=False),
        sa.Column("variation_index", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("language", sa.String(5), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("claim_issues", sa.JSON(), nullable=False),
        sa.Column("evaluation", sa.JSON(), nullable=False),
        sa.Column("approval_status", sa.String(20), nullable=False),
        sa.Column("edited", sa.Boolean(), nullable=False),
        sa.Column("reviewed_by", sa.String(100), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["campaign_id"], ["marketing_campaigns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("marketing_drafts") as b:
        b.create_index("ix_marketing_drafts_campaign_id", ["campaign_id"])
        b.create_index("ix_marketing_drafts_platform", ["platform"])
        b.create_index("ix_marketing_drafts_approval_status", ["approval_status"])


def downgrade() -> None:
    op.drop_table("marketing_drafts")
    op.drop_table("marketing_campaigns")
    op.drop_table("course_offers")
