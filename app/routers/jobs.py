"""任务管理只暴露引用/固定错误码，重试与通知查询均执行资源权限。"""
from datetime import datetime
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import BackgroundJob, NotificationRecord, OperationAudit, User
from app.security import require_admin, get_current_user, ensure_owner_or_admin
from app.utils.database import get_db

router = APIRouter(prefix="/api/v1/jobs", tags=["后台任务"])


def job_view(job):
    return {"job_id":job.id,"kind":job.kind,"resource_id":job.resource_id,"user_id":job.user_id,
            "status":job.status,"attempts":job.attempts,"max_attempts":job.max_attempts,
            "next_run":job.next_run.isoformat()+"Z","error_code":job.last_error}


@router.get("/")
async def jobs(db: AsyncSession=Depends(get_db), _: User=Depends(require_admin)):
    rows=(await db.execute(select(BackgroundJob).order_by(BackgroundJob.created_at.desc()).limit(100))).scalars().all()
    return {"code":0,"data":[job_view(row) for row in rows]}


@router.post("/{job_id}/retry")
async def retry(job_id: str, db: AsyncSession=Depends(get_db), actor: User=Depends(require_admin)):
    job=await db.scalar(select(BackgroundJob).where(BackgroundJob.id==job_id).with_for_update())
    if job is None: raise HTTPException(404,"任务不存在")
    if job.status != "failed": raise HTTPException(409,"只有失败任务可重试")
    job.status,job.attempts,job.next_run="queued",0,datetime.utcnow()
    job.lease_token,job.lease_expires_at=None,None
    db.add(OperationAudit(operation="retry_job",idempotency_key="job-retry:"+uuid.uuid4().hex,
        user_id=job.user_id,resource_id=job.id,request_payload={"actor_id":actor.id,"owner_id":job.user_id},
        result_payload={"status":"queued","delivery_unknown_requires_reconciliation":True}))
    await db.commit()
    return {"code":0,"data":job_view(job)}


@router.get("/notifications/{event_id}")
async def notification(event_id: str, db: AsyncSession=Depends(get_db), actor: User=Depends(get_current_user)):
    event=await db.get(NotificationRecord,event_id)
    if event is None: raise HTTPException(404,"通知不存在")
    if not event.recipient.isdecimal():
        if actor.role != "admin": raise HTTPException(403,"无权访问")
    else:
        ensure_owner_or_admin(actor,int(event.recipient))
    labels={"pending":"queued","sending":"sending","sent":"sent","failed":"failed","unknown":"unknown"}
    return {"code":0,"data":{"event_id":event.id,"status":labels[event.status.value],"is_simulated":event.is_simulated,
            "provider_receipt":event.provider_receipt,"error_code":event.last_error}}
