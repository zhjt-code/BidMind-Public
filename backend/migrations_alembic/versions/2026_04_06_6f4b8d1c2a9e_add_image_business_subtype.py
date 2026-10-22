"""add_image_business_subtype

Revision ID: 6f4b8d1c2a9e
Revises: 38278c537892
Create Date: 2026-04-06 20:10:00.000000+08:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6f4b8d1c2a9e"
down_revision: Union[str, None] = "38278c537892"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE writing_material_images
        ADD COLUMN IF NOT EXISTS business_subtype VARCHAR(50)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE writing_material_images
        DROP COLUMN IF EXISTS business_subtype
        """
    )
