from datetime import datetime

from scripts.seed_ecommerce_orders import build_orders


def test_build_ecommerce_orders_covers_twenty_products_and_valid_fields():
    orders = build_orders(user_id=1, now=datetime(2026, 9, 6, 12, 0, 0))

    assert len(orders) == 20
    assert len({order.order_id for order in orders}) == 20
    assert all(order.order_id.startswith("ORD202609") for order in orders)
    assert all(order.user_id == 1 for order in orders)
    assert all(order.items and order.items[0]["qty"] >= 1 for order in orders)
    assert all(order.total_amount > 0 for order in orders)
    assert {order.status_code for order in orders} >= {
        "pending_payment", "processing", "shipped", "delivered", "completed", "cancelled"
    }


def test_build_ecommerce_orders_adds_shipping_only_after_dispatch():
    orders = build_orders(user_id=1, now=datetime(2026, 9, 6, 12, 0, 0))

    for order in orders:
        if order.status_code in {"shipped", "delivered", "completed"}:
            assert order.shipping is not None
            assert order.shipping["tracking_no"]
        else:
            assert order.shipping is None
