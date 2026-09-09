"""add site_messages table（站内信）

- 新表 site_messages：管理员站内信，一行一个收件人（广播=每活跃用户一行）
- user_id → users.id CASCADE；sender_id → users.id SET NULL
- batch_id 同一次发送共享，管理端按批次做已读统计；kind=user/broadcast
- read_at NULL=未读

Revision ID: c3a9e4f71b28
Revises: b71f2c3d9e50
Create Date: 2026-09-10 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3a9e4f71b28'
down_revision: Union[str, None] = 'b71f2c3d9e50'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('site_messages',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('sender_id', sa.UUID(), nullable=True),
    sa.Column('batch_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=16), server_default='user', nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_site_messages_batch_id'), 'site_messages', ['batch_id'], unique=False)
    op.create_index(op.f('ix_site_messages_user_id'), 'site_messages', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_site_messages_user_id'), table_name='site_messages')
    op.drop_index(op.f('ix_site_messages_batch_id'), table_name='site_messages')
    op.drop_table('site_messages')
