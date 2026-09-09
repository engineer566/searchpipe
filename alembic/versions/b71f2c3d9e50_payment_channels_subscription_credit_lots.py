"""payment channels: 充值档位 + 包月订阅 + 积分批次化（2 位小数）

- plans: +kind/level/original_price_cents；种子 4 充值档 + 3 订阅档（限时折扣）
- orders: +kind/pay_channel；credits → NUMERIC(20,2)
- credit_accounts.balance / credit_transactions.delta,balance_after → NUMERIC(20,2)
- credit_transactions: +lot_usage JSONB（consume 批次消耗明细）
- 新表 credit_lots（积分批次，expires_at NULL=永久）/ subscriptions（包月订阅）
- 存量余额回填为永久批次

Revision ID: b71f2c3d9e50
Revises: 6f04c661a041
Create Date: 2026-09-09 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b71f2c3d9e50'
down_revision: Union[str, None] = '6f04c661a041'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 种子套餐固定 UUID（测试与前端联调可依赖）
_RECHARGE_PLANS = [
    # (id, name, credits, price_cents)
    ('11111111-0000-0000-0000-000000000001', '充值 ¥10', 350, 1000),
    ('11111111-0000-0000-0000-000000000002', '充值 ¥20', 700, 2000),
    ('11111111-0000-0000-0000-000000000003', '充值 ¥50', 1800, 5000),
    ('11111111-0000-0000-0000-000000000004', '充值 ¥100', 5000, 10000),
]
_SUBSCRIPTION_PLANS = [
    # (id, name, level, credits, price_cents 现价, original_price_cents 标价)
    ('22222222-0000-0000-0000-000000000001', '包月·基础', 1, 1000, 999, 1999),
    ('22222222-0000-0000-0000-000000000002', '包月·进阶', 2, 3000, 2499, 4999),
    ('22222222-0000-0000-0000-000000000003', '包月·旗舰', 3, 10000, 4999, 9999),
]


def upgrade() -> None:
    # --- plans 扩展 ---
    op.add_column('plans', sa.Column('kind', sa.String(length=16), server_default='recharge', nullable=False))
    op.add_column('plans', sa.Column('level', sa.Integer(), nullable=True))
    op.add_column('plans', sa.Column('original_price_cents', sa.BigInteger(), nullable=True))

    # --- orders 扩展 + credits 小数化 ---
    op.add_column('orders', sa.Column('kind', sa.String(length=16), server_default='recharge', nullable=False))
    op.add_column('orders', sa.Column('pay_channel', sa.String(length=16), nullable=True))
    op.alter_column('orders', 'credits', type_=sa.Numeric(20, 2), existing_type=sa.BigInteger())

    # --- 积分账户/流水小数化 ---
    op.alter_column('credit_accounts', 'balance', type_=sa.Numeric(20, 2), existing_type=sa.BigInteger())
    op.alter_column('credit_transactions', 'delta', type_=sa.Numeric(20, 2), existing_type=sa.BigInteger())
    op.alter_column('credit_transactions', 'balance_after', type_=sa.Numeric(20, 2), existing_type=sa.BigInteger())
    op.add_column('credit_transactions', sa.Column('lot_usage', postgresql.JSONB(astext_type=sa.Text()), nullable=True))

    # --- 积分批次表 ---
    op.create_table('credit_lots',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('order_id', sa.Uuid(), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('amount', sa.Numeric(20, 2), nullable=False),
    sa.Column('remaining', sa.Numeric(20, 2), nullable=False),
    sa.Column('effective_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_credit_lots_user_id'), 'credit_lots', ['user_id'], unique=False)
    op.create_index(op.f('ix_credit_lots_expires_at'), 'credit_lots', ['expires_at'], unique=False)

    # --- 订阅表 ---
    op.create_table('subscriptions',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('plan_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('current_period_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_subscriptions_user_id'), 'subscriptions', ['user_id'], unique=False)
    # 同一用户至多一条 active 订阅（部分唯一索引）
    op.create_index('ix_subscriptions_user_active', 'subscriptions', ['user_id'], unique=True,
                    postgresql_where=sa.text("status = 'active'"))

    # --- 种子套餐 ---
    plans = sa.table('plans',
        sa.column('id', sa.Uuid), sa.column('name', sa.String),
        sa.column('kind', sa.String), sa.column('level', sa.Integer),
        sa.column('credits', sa.BigInteger), sa.column('price_cents', sa.BigInteger),
        sa.column('original_price_cents', sa.BigInteger), sa.column('period', sa.String),
        sa.column('is_active', sa.Boolean),
    )
    op.bulk_insert(plans, [
        {'id': pid, 'name': name, 'kind': 'recharge', 'level': None,
         'credits': credits, 'price_cents': price, 'original_price_cents': None,
         'period': None, 'is_active': True}
        for pid, name, credits, price in _RECHARGE_PLANS
    ] + [
        {'id': pid, 'name': name, 'kind': 'subscription', 'level': level,
         'credits': credits, 'price_cents': price, 'original_price_cents': original,
         'period': 'month', 'is_active': True}
        for pid, name, level, credits, price, original in _SUBSCRIPTION_PLANS
    ])

    # --- 存量余额回填为永久批次 ---
    op.execute(sa.text(
        """
        INSERT INTO credit_lots (user_id, order_id, source, amount, remaining, effective_at, expires_at)
        SELECT user_id, NULL, 'grant', balance, balance, now(), NULL
        FROM credit_accounts
        WHERE balance > 0
        """
    ))


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM plans WHERE id IN :ids").bindparams(
        ids=tuple(p[0] for p in _RECHARGE_PLANS + _SUBSCRIPTION_PLANS)))
    op.drop_index('ix_subscriptions_user_active', table_name='subscriptions')
    op.drop_index(op.f('ix_subscriptions_user_id'), table_name='subscriptions')
    op.drop_table('subscriptions')
    op.drop_index(op.f('ix_credit_lots_expires_at'), table_name='credit_lots')
    op.drop_index(op.f('ix_credit_lots_user_id'), table_name='credit_lots')
    op.drop_table('credit_lots')
    op.drop_column('credit_transactions', 'lot_usage')
    op.alter_column('credit_transactions', 'balance_after', type_=sa.BigInteger(), existing_type=sa.Numeric(20, 2))
    op.alter_column('credit_transactions', 'delta', type_=sa.BigInteger(), existing_type=sa.Numeric(20, 2))
    op.alter_column('credit_accounts', 'balance', type_=sa.BigInteger(), existing_type=sa.Numeric(20, 2))
    op.alter_column('orders', 'credits', type_=sa.BigInteger(), existing_type=sa.Numeric(20, 2))
    op.drop_column('orders', 'pay_channel')
    op.drop_column('orders', 'kind')
    op.drop_column('plans', 'original_price_cents')
    op.drop_column('plans', 'level')
    op.drop_column('plans', 'kind')
