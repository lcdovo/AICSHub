"""
用户反馈 API - Phase 4 实现
"""
import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from app.services.evaluation import get_answer_evaluator, get_low_score_pool
from app.utils.tracking import get_tracer
from app.models.models import User, UserFeedback, Session
from app.utils.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.security import get_current_user, ensure_owner_or_admin, require_admin

router = APIRouter(prefix="/api/v1/feedback", tags=["用户反馈"])


class FeedbackRequest(BaseModel):
    user_id: int
    session_id: str
    message_id: Optional[int] = None
    feedback_type: str  # like/dislike/csat/comment
    score: Optional[int] = None  # for CSAT: 1-5
    content: Optional[str] = None
    categories: Optional[list] = None  # for dislike: ["inaccurate", "incomplete", "irrelevant"]


class FeedbackResponse(BaseModel):
    feedback_id: str
    status: str
    message: str


_feedback_records = []


@router.post("/submit", response_model=FeedbackResponse)
async def submit_feedback(feedback: FeedbackRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """提交用户反馈"""
    ensure_owner_or_admin(current_user, feedback.user_id)
    feedback_id = str(uuid.uuid4())
    if feedback.feedback_type not in {"like", "dislike", "csat", "comment"}:
        raise HTTPException(status_code=400, detail="反馈类型无效")
    if feedback.feedback_type == "csat" and (feedback.score is None or not 1 <= feedback.score <= 5):
        raise HTTPException(status_code=400, detail="CSAT 评分必须在1-5之间")
    session = await db.get(Session, feedback.session_id)
    if not session or session.user_id != feedback.user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    item = UserFeedback(id=feedback_id, user_id=feedback.user_id, session_id=feedback.session_id,
                        message_id=feedback.message_id, feedback_type=feedback.feedback_type,
                        score=feedback.score, content=feedback.content, categories=feedback.categories or [])
    db.add(item)
    await db.commit()
    record = {"feedback_id": item.id, "user_id": item.user_id, "session_id": item.session_id,
              "message_id": item.message_id, "feedback_type": item.feedback_type, "score": item.score,
              "content": item.content, "categories": item.categories or [],
              "created_at": item.created_at.isoformat() if item.created_at else None}

    if feedback.feedback_type == "dislike":
        evaluator = get_answer_evaluator()
        low_score_pool = get_low_score_pool()

        if feedback.content:
            evaluation = evaluator.evaluate(
                response=feedback.content,
                user_query="",
            )

            low_score_pool.add_sample(
                sample_id=feedback_id,
                session_id=feedback.session_id,
                user_query="用户反馈",
                response=feedback.content,
                score=evaluation.overall_score,
                failure_reason="用户点踩反馈",
                metadata={"categories": feedback.categories},
            )

    return FeedbackResponse(
        feedback_id=feedback_id,
        status="received",
        message="反馈已收到，感谢您的评价！",
    )


@router.get("/history/{user_id}")
async def get_feedback_history(
    user_id: int,
    feedback_type: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user),
):
    """获取用户反馈历史"""
    ensure_owner_or_admin(current_user, user_id)
    query = select(UserFeedback).where(UserFeedback.user_id == user_id)
    if feedback_type: query = query.where(UserFeedback.feedback_type == feedback_type)
    items = (await db.execute(query.order_by(UserFeedback.created_at.desc()).limit(limit))).scalars().all()
    records = [{"feedback_id": i.id, "user_id": i.user_id, "session_id": i.session_id, "message_id": i.message_id,
                "feedback_type": i.feedback_type, "score": i.score, "content": i.content,
                "categories": i.categories or [], "created_at": i.created_at.isoformat() if i.created_at else None} for i in items]

    return {
        "code": 0,
        "message": "获取成功",
        "data": {
            "total": len(records),
            "records": records,
        },
    }


@router.get("/stats")
async def get_feedback_stats(
    days: int = Query(default=7, ge=1, le=30),
    db: AsyncSession = Depends(get_db), _: User = Depends(require_admin),
):
    """获取反馈统计"""
    cutoff_date = datetime.utcnow()
    records = (await db.execute(select(UserFeedback))).scalars().all()
    total_feedbacks = len(records)

    type_distribution = {}
    score_distribution = {}

    for record in records:
        fb_type = record.feedback_type
        type_distribution[fb_type] = type_distribution.get(fb_type, 0) + 1

        if fb_type == "csat" and record.score:
            score = record.score
            score_distribution[score] = score_distribution.get(score, 0) + 1

    csat_scores = [r.score for r in records if r.feedback_type == "csat" and r.score]
    avg_csat = sum(csat_scores) / max(len(csat_scores), 1) if csat_scores else 0

    return {
        "code": 0,
        "message": "获取成功",
        "data": {
            "period_days": days,
            "total_feedbacks": total_feedbacks,
            "type_distribution": type_distribution,
            "csat": {
                "count": len(csat_scores),
                "avg_score": round(avg_csat, 2),
                "distribution": score_distribution,
            },
            "like_rate": round(
                type_distribution.get("like", 0) / max(total_feedbacks, 1) * 100, 2
            ),
        },
    }
