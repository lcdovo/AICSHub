"""人工请求回执的归属检查；推荐/尝试标记都不证明请求已登记。"""

from typing import Any


def matching_handoff_receipt(data: Any, user_id: int, session_id: str) -> bool:
    return bool(isinstance(data, dict) and isinstance(data.get("request_id"), str)
                and data["request_id"].strip() and type(data.get("user_id")) is int
                and data["user_id"] == user_id and data.get("session_id") == session_id
                and isinstance(data.get("status"), str)
                and data.get("status") in {"pending", "assigned", "resolved", "cancelled"})
