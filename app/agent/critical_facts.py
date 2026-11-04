"""固定业务论断核验；缺失字段不以复述率判错，未知语义仍需独立Judge。"""
import re
from decimal import Decimal, InvalidOperation


def critical_fact_issues(response: str, tool_results: list[dict]) -> list[dict]:
    issues = []

    def reject(code):
        if not any(i["type"] == code for i in issues):
            issues.append({"type": code, "severity": "critical"})

    rows = [r["data"] for r in tool_results if isinstance(r, dict) and r.get("success") is True
            and isinstance(r.get("data"), dict)]
    # 本产品仅模拟退款申请/审核，任何工具字典不能证明实际支付。
    for clause in re.split(r"[。；;\n]", response):
        if re.search(r"退款成功|(?:已|已经)(?:到账|打款|支付|退还)|退款(?:已)?到账", clause):
            if not re.search(r"(?:尚未|未|没有|不能|无法|不代表|不表示).{0,8}(?:到账|打款|支付|退款成功)", clause):
                reject("fact_payment_not_supported")
        if re.search(r"(?:退换货|退款|退货)申请(?:已经|已)?(?:提交|登记)|已(?:提交|登记).{0,6}(?:退款|退换货)申请", clause):
            if not re.search(r"(?:尚未|未|没有|不代表|无法).{0,8}(?:提交|登记)", clause):
                if not any(isinstance(r.get("refund_id"), str) and r["refund_id"].strip()
                           and r.get("status") in {"pending_review", "approved", "rejected"} for r in rows):
                    reject("fact_application_receipt_missing")
        if re.search(r"(?:赔偿|补偿|终身换新|终身保修)", clause) and not re.search(r"(?:不|未|无|不能|无法|没有|不承诺).{0,5}(?:赔偿|补偿|终身)", clause):
            reject("fact_unverified_commitment")
    orders = [r for r in rows if isinstance(r.get("order_id"), str) and "total_amount" in r]
    ids = {r["order_id"] for r in orders}
    mentioned = {oid for oid in ids if oid in response}
    selected = [r for r in orders if r["order_id"] in mentioned] if mentioned else orders if len(ids) == 1 else []
    for oid in {r["order_id"] for r in selected}:
        receipts = [r for r in selected if r["order_id"] == oid]
        signatures = {(str(r.get("status_code") or r.get("status")), str(r["total_amount"]), r.get("currency", "CNY")) for r in receipts}
        if len(signatures) > 1:
            reject("fact_conflicting_receipts")
            continue
        row = receipts[0]
        # 多订单回答逐句绑定；无订单号的多对象论断无法确定归属，保守拒绝。
        text = response
        if len(mentioned) > 1:
            clauses = [c for c in re.split(r"[。；;\n]", response) if oid in c]
            text = "；".join(clauses)
        status = str(row.get("status_code") or row.get("status", ""))
        shipped = status in {"shipped", "delivered", "completed", "已发货", "已签收", "已完成"}
        unshipped = status in {"pending", "paid", "unshipped", "待付款", "待发货", "已付款"}
        if shipped and re.search(r"还没发货|(?:尚未|没有|未)发货|状态[：:]?\s*(?:已取消|待发货|待付款)", text):
            reject("fact_order_status_contradiction")
        if unshipped and re.search(r"已发货|已签收|已完成", text):
            reject("fact_order_status_contradiction")
        if status == "cancelled" and re.search(r"已发货|已签收|待发货", text):
            reject("fact_order_status_contradiction")
        try:
            expected = Decimal(str(row["total_amount"]))
            if not expected.is_finite():
                raise ValueError("non_finite")
        except (InvalidOperation, ValueError):
            reject("fact_invalid_amount_evidence")
            continue
        for amount, currency in re.findall(r"(\d+(?:\.\d+)?)\s*(元|人民币|CNY|美元|USD|欧元|EUR)", text, flags=re.I):
            if Decimal(amount) != expected:
                reject("fact_amount_contradiction")
            actual_currency = {"元": "CNY", "人民币": "CNY", "美元": "USD", "欧元": "EUR"}.get(currency, currency.upper())
            if actual_currency != row.get("currency", "CNY"):
                reject("fact_currency_contradiction")
    return issues
