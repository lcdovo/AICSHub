"""向当前数据库写入一组可重复执行的电子产品订单测试数据。

用法（Docker 环境）：
    docker compose exec app python scripts/seed_ecommerce_orders.py

脚本只创建不存在的订单，重复执行不会产生重复数据。订单统一归属演示用户
``user_id=1``，这样可以直接通过客服的订单查询工具验证权限和查询结果。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.models.models import Base, Order, User  # noqa: E402
from app.utils.database import async_session, engine  # noqa: E402


PRODUCTS = [
    ("降噪无线耳机", 399.0),
    ("智能手表", 899.0),
    ("机械键盘", 569.0),
    ("27英寸4K显示器", 2199.0),
    ("无线鼠标", 159.0),
    ("平板电脑", 2499.0),
    ("USB-C扩展坞", 299.0),
    ("便携式固态硬盘", 699.0),
    ("智能手机", 3299.0),
    ("游戏手柄", 459.0),
    ("蓝牙音箱", 299.0),
    ("笔记本电脑", 5999.0),
    ("手机快充套装", 189.0),
    ("家庭路由器", 399.0),
    ("电子书阅读器", 1099.0),
    ("网络摄像头", 269.0),
    ("打印机", 1299.0),
    ("移动电源", 129.0),
    ("智能门锁", 1599.0),
    ("显示器支架", 349.0),
]

ORDER_STATES = [
    ("待付款", "pending_payment"),
    ("待发货", "processing"),
    ("已发货", "shipped"),
    ("运输中", "shipped"),
    ("已送达", "delivered"),
    ("已完成", "completed"),
    ("已取消", "cancelled"),
]


def build_orders(user_id: int, now: datetime | None = None) -> list[Order]:
    """构造 20 条覆盖常见订单状态的电子产品订单。"""
    base_time = now or datetime.now(timezone.utc).replace(tzinfo=None)
    orders: list[Order] = []
    for index, (product, price) in enumerate(PRODUCTS, start=1):
        status, status_code = ORDER_STATES[(index - 1) % len(ORDER_STATES)]
        quantity = 1 if index % 4 else 2
        amount = price * quantity
        created_at = base_time - timedelta(days= index)
        is_shipped = status_code in {"shipped", "delivered", "completed"}
        shipping = (
            {
                "carrier": ("顺丰速运", "京东物流", "中通快递")[index % 3],
                "tracking_no": f"SF202609{index:02d}CN",
                "status": "已签收" if status_code in {"delivered", "completed"} else "运输中",
                "estimated_delivery": (created_at + timedelta(days=3)).date().isoformat(),
            }
            if is_shipped
            else None
        )
        orders.append(
            Order(
                order_id=f"ORD202609{index:02d}",
                user_id=user_id,
                status=status,
                status_code=status_code,
                items=[{"name": product, "qty": quantity, "price": price}],
                total_amount=amount,
                shipping=shipping,
                created_at=created_at,
                pay_method=("微信支付", "支付宝", "银行卡支付")[index % 3],
                address=(
                    "北京市朝阳区建国路88号"
                    if index % 2
                    else "上海市浦东新区世纪大道100号"
                ),
            )
        )
    return orders


async def seed_orders(user_id: int = 1) -> tuple[int, int]:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            raise RuntimeError(f"用户 {user_id} 不存在，请先初始化演示用户")

        candidates = build_orders(user_id)
        order_ids = [order.order_id for order in candidates]
        existing_rows = (
            await session.execute(select(Order.order_id).where(Order.order_id.in_(order_ids)))
        ).scalars().all()
        existing = set(existing_rows)
        new_orders = [order for order in candidates if order.order_id not in existing]
        session.add_all(new_orders)
        await session.commit()
        return len(new_orders), len(existing)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成电子产品订单测试数据")
    parser.add_argument("--user-id", type=int, default=1, help="订单归属用户 ID，默认 1")
    args = parser.parse_args()
    async def run() -> tuple[int, int]:
        try:
            return await seed_orders(args.user_id)
        finally:
            await engine.dispose()

    created, skipped = asyncio.run(run())
    print(f"订单种子完成：新增 {created} 条，已存在 {skipped} 条，共覆盖 20 个订单号")


if __name__ == "__main__":
    main()
