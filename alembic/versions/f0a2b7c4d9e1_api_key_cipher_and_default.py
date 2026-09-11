"""api_keys.key_cipher / is_default（默认 API Key 与明文可查看）

- key_cipher：明文 Key 的 Fernet 密文（api_keys/crypto.py 派生主密钥），
  支撑「平时隐藏、点击查看明文 / 生成 MCP 链接」；鉴权仍只用 key_hash。
- is_default：默认 Key 标记。历史数据回填：每个用户最新的一把有效 Key 置为默认，
  保证老用户也能直接拿到可用的 MCP 配置。

Revision ID: f0a2b7c4d9e1
Revises: d4b8e2f1a3c5
Create Date: 2026-09-12 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f0a2b7c4d9e1'
down_revision: Union[str, None] = 'd4b8e2f1a3c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('api_keys', sa.Column('key_cipher', sa.Text(), nullable=True))
    op.add_column(
        'api_keys',
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.create_index('ix_api_keys_is_default', 'api_keys', ['is_default'])
    # 历史数据回填：每个用户最新（未吊销）的一把 Key 标记为默认
    op.execute(
        """
        UPDATE api_keys SET is_default = true
        WHERE id IN (
            SELECT DISTINCT ON (user_id) id
            FROM api_keys
            WHERE revoked_at IS NULL
            ORDER BY user_id, created_at DESC
        )
        """
    )


def downgrade() -> None:
    op.drop_index('ix_api_keys_is_default', table_name='api_keys')
    op.drop_column('api_keys', 'is_default')
    op.drop_column('api_keys', 'key_cipher')
