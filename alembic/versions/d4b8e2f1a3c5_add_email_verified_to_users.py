"""add email_verified to users（邮箱验证）

- users 表新增 email_verified 布尔字段，默认 false
- 注册时设为 false，用户点击验证链接后设为 true
- 用于防止虚假注册骗积分

Revision ID: d4b8e2f1a3c5
Revises: c3a9e4f71b28
Create Date: 2026-09-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4b8e2f1a3c5'
down_revision: Union[str, None] = 'c3a9e4f71b28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('email_verified', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('users', 'email_verified')
