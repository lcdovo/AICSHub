"""管理员用途级模型账本，只暴露计量与版本，不返回prompt/凭据。"""

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import ModelCallRecord, User
from app.security import require_admin
from app.utils.database import get_db

router = APIRouter(prefix="/api/v1/model-calls", tags=["模型计量"])


@router.get("/")
async def calls(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    rows = (
        (
            await db.execute(
                select(ModelCallRecord)
                .order_by(ModelCallRecord.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "call_id": r.id,
                "user_id": r.user_id,
                "run_id": r.run_id,
                "purpose": r.purpose,
                "model": r.model,
                "model_version": r.model_version,
                "provider": r.provider,
                "route_version": r.route_version,
                "schema_version": r.schema_version,
                "prompt_version": r.prompt_version,
                "status": r.status,
                "usage_basis": r.usage_basis,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "reserved_tokens": r.reserved_tokens,
                "estimated_cost_usd": str(r.cost) if r.cost is not None else None,
                "latency_ms": r.latency_ms,
                "error_code": r.error_code,
            }
            for r in rows
        ],
    }


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    rows = (
        await db.execute(
            select(
                ModelCallRecord.purpose,
                ModelCallRecord.usage_basis,
                func.count(),
                func.sum(ModelCallRecord.input_tokens),
                func.sum(ModelCallRecord.output_tokens),
                func.sum(ModelCallRecord.cost),
            ).group_by(ModelCallRecord.purpose, ModelCallRecord.usage_basis)
        )
    ).all()
    return {
        "code": 0,
        "data": [
            {
                "purpose": r[0],
                "usage_basis": r[1],
                "calls": r[2],
                "input_tokens": r[3],
                "output_tokens": r[4],
                "estimated_cost_usd": str(r[5]) if r[5] is not None else None,
                "supplier_bill": False,
            }
            for r in rows
        ],
    }
