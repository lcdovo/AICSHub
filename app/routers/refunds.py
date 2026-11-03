"""模拟退换货申请结果查询；不提供支付或自动审核。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import RefundApplication, User
from app.security import ensure_owner_or_admin, get_current_user
from app.services.refund_application import receipt
from app.utils.database import get_db

router = APIRouter(prefix="/api/v1/refunds", tags=["模拟退换货申请"])


@router.get("/{refund_id}")
async def get_refund(refund_id: str, db: AsyncSession = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    item = await db.get(RefundApplication, refund_id)
    if item is None:
        raise HTTPException(status_code=404, detail="申请不存在")
    ensure_owner_or_admin(current_user, item.user_id)
    return {"code": 0, "message": "获取成功", "data": receipt(item)}
