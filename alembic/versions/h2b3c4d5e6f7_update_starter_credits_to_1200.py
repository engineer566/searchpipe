"""update Starter subscription plan credits from 1000 to 1200

Revision ID: h2b3c4d5e6f7
Revises: g1a2b3c4d5e6
Create Date: 2026-09-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'h2b3c4d5e6f7'
down_revision: Union[str, None] = 'g1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STARTER_PLAN_ID = '44444444-0000-0000-0000-000000000001'


def upgrade() -> None:
    op.execute(
        f"UPDATE plans SET credits = 1200 WHERE id = '{_STARTER_PLAN_ID}'"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE plans SET credits = 1000 WHERE id = '{_STARTER_PLAN_ID}'"
    )
