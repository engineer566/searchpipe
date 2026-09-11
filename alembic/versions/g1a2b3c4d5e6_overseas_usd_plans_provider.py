"""overseas: USD 套餐种子 + provider product 映射 + 订阅 provider 列

出海重构（docs/overseas-migration-analysis.md）：
1. plans 加 provider_products JSONB：{"creem": "prod_...", "dodo": "prod_..."}，
   套餐与 MoR 平台 product 的映射（部署后按支付商后台填入）。
2. subscriptions 加 provider / provider_subscription_id：原生自动续订由平台
   托管扣款，webhook 按 provider_subscription_id 定位本地订阅。
3. 套餐切换：旧 7 个 CNY 档 is_active=false 下架，新 USD 档入库
   （充值 $5/$10/$20；订阅 Starter $4.99 / Pro $9.99 / Max $19.99）。

Revision ID: g1a2b3c4d5e6
Revises: f0a2b7c4d9e1
Create Date: 2026-09-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'g1a2b3c4d5e6'
down_revision: Union[str, None] = 'f0a2b7c4d9e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 旧 CNY 种子套餐（b71f2c3d9e50 种入），本次下架
_OLD_PLAN_IDS = tuple(
    f'11111111-0000-0000-0000-00000000000{i}' for i in range(1, 5)
) + tuple(
    f'22222222-0000-0000-0000-00000000000{i}' for i in range(1, 4)
)

# 新 USD 种子套餐固定 UUID（测试与前端联调可依赖）
_RECHARGE_PLANS = [
    # (id, name, credits, price_cents)
    ('33333333-0000-0000-0000-000000000001', 'Recharge $5', 1000, 500),
    ('33333333-0000-0000-0000-000000000002', 'Recharge $10', 2100, 1000),
    ('33333333-0000-0000-0000-000000000003', 'Recharge $20', 4400, 2000),
]
_SUBSCRIPTION_PLANS = [
    # (id, name, level, credits, price_cents 现价, original_price_cents 标价)
    ('44444444-0000-0000-0000-000000000001', 'Starter', 1, 1000, 499, 999),
    ('44444444-0000-0000-0000-000000000002', 'Pro', 2, 3000, 999, 1999),
    ('44444444-0000-0000-0000-000000000003', 'Max', 3, 10000, 1999, 3999),
]
_NEW_PLAN_IDS = tuple(p[0] for p in _RECHARGE_PLANS + _SUBSCRIPTION_PLANS)


def upgrade() -> None:
    # --- plans：provider product 映射 ---
    op.add_column('plans', sa.Column(
        'provider_products', postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text("'{}'::jsonb"), nullable=False,
    ))

    # --- subscriptions：平台托管订阅定位 ---
    op.add_column('subscriptions', sa.Column('provider', sa.String(length=16), nullable=True))
    op.add_column('subscriptions', sa.Column('provider_subscription_id', sa.String(length=128), nullable=True))
    op.create_index('ix_subscriptions_provider_sub', 'subscriptions',
                    ['provider', 'provider_subscription_id'], unique=True)

    # --- 套餐切换：下架旧 CNY 档，入库新 USD 档 ---
    op.execute(
        sa.text("UPDATE plans SET is_active = false WHERE id IN :ids")
        .bindparams(sa.bindparam("ids", expanding=True)),
        {"ids": list(_OLD_PLAN_IDS)},
    )
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


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM plans WHERE id IN :ids")
        .bindparams(sa.bindparam("ids", expanding=True)),
        {"ids": list(_NEW_PLAN_IDS)},
    )
    op.execute(
        sa.text("UPDATE plans SET is_active = true WHERE id IN :ids")
        .bindparams(sa.bindparam("ids", expanding=True)),
        {"ids": list(_OLD_PLAN_IDS)},
    )
    op.drop_index('ix_subscriptions_provider_sub', table_name='subscriptions')
    op.drop_column('subscriptions', 'provider_subscription_id')
    op.drop_column('subscriptions', 'provider')
    op.drop_column('plans', 'provider_products')
