"""
统计分析与监控 API - Phase 4 实现
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import PlainTextResponse
from typing import Optional
from datetime import datetime, timedelta

from app.utils.tracking import get_tracer, alert_manager, build_alert_input
from app.services.evaluation import get_answer_evaluator, get_low_score_pool, get_ab_test_framework
from app.models.models import User
from app.security import require_admin
from app.models.models import EvaluationResult, AgentTrace
from app.utils.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

router = APIRouter(prefix="/api/v1/analytics", tags=["统计分析"])


def _group_evaluations(rows, field: str) -> dict:
    grouped = {}
    for row in rows:
        key = getattr(row, field, None) or "unknown"
        grouped.setdefault(key, []).append(row.overall_score or 0)
    return {key: {"count": len(scores), "avg_score": round(sum(scores) / len(scores), 2), "low_score": sum(score < 60 for score in scores)} for key, scores in grouped.items()}


@router.get("/metrics/prometheus", response_class=PlainTextResponse)
async def get_prometheus_metrics(_: User = Depends(require_admin)):
    """输出无用户/消息等敏感标签的结构化 Prometheus 指标。"""
    return get_tracer().to_prometheus()


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str, db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)):
    """按请求级 Trace ID 聚合数据库 Span，避免把每个节点误当成独立 Trace。"""
    rows = (await db.execute(select(AgentTrace).where(AgentTrace.request_trace_id == trace_id).order_by(AgentTrace.node_order))).scalars().all()
    spans = [{"trace_id": trace_id, "span_id": row.span_id, "node": row.node_name, "node_order": row.node_order, "duration_ms": row.duration_ms, "success": row.success, "input": row.input_data, "output": row.output_data} for row in rows]
    return {"code": 0, "message": "获取成功", "data": {"trace_id": trace_id, "spans": spans, "span_count": len(spans)}}


@router.get("/metrics")
async def get_system_metrics(_: User = Depends(require_admin)):
    """获取系统指标"""
    tracer = get_tracer()
    metrics = tracer.get_metrics()

    alerts = alert_manager.check_alerts(build_alert_input(metrics))
    active_alerts = alert_manager.get_active_alerts()

    return {
        "code": 0,
        "message": "获取成功",
        "data": {
            "metrics": metrics,
            "alerts": active_alerts,
            "alert_rules": alert_manager.get_alert_rules(),
        },
    }


@router.get("/metrics/summary")
async def get_metrics_summary(_: User = Depends(require_admin)):
    """获取指标摘要。

    注意：MetricsCollector 为进程级聚合计数器/分位统计，不保留按小时的
    采样明细，因此不再提供 hours 参数（此前参数仅回显不生效，已移除）。
    """
    tracer = get_tracer()
    metrics = tracer.get_metrics()

    response_time = metrics.get("response_time", {})
    tool_stats = metrics.get("tool_statistics", {})

    summary = {
        "total_requests": metrics.get("counters", {}).get("response.total", 0),
        "success_rate": metrics.get("success_rate", 0),
        "avg_response_time_ms": response_time.get("avg_ms", 0),
        "p95_response_time_ms": response_time.get("p95_ms", 0),
        "intent_distribution": metrics.get("intent_distribution", {}),
        "tool_statistics": tool_stats,
        "session_count": metrics.get("session_count", 0),
    }

    return {
        "code": 0,
        "message": "获取成功",
        "data": summary,
    }


@router.get("/evaluation/stats")
async def get_evaluation_stats(
    hours: int = Query(default=0, ge=0, le=8760),
    model: Optional[str] = Query(default=None),
    intent: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db), _: User = Depends(require_admin),
):
    """获取评测统计"""
    evaluator = get_answer_evaluator()
    low_score_pool = get_low_score_pool()

    query = select(EvaluationResult)
    if hours:
        query = query.where(EvaluationResult.created_at >= datetime.utcnow() - timedelta(hours=hours))
    if model:
        query = query.where(EvaluationResult.model_name == model)
    if intent:
        query = query.where(EvaluationResult.intent == intent)
    rows = (await db.execute(query)).scalars().all()
    if rows:
        scores = [r.overall_score or 0 for r in rows]
        stats = {
            "total": len(rows), "avg_score": round(sum(scores) / len(scores), 2),
            "min_score": min(scores), "max_score": max(scores),
            "low_score": sum(1 for r in rows if r.is_low_score),
            "filters": {"hours": hours or None, "model": model, "intent": intent},
            "by_model": _group_evaluations(rows, "model_name"),
            "by_intent": _group_evaluations(rows, "intent"),
        }
    else:
        stats = evaluator.get_evaluation_stats()
    failure_patterns = low_score_pool.get_failure_patterns()

    return {
        "code": 0,
        "message": "获取成功",
        "data": {
            "evaluation_stats": stats,
            "failure_patterns": failure_patterns,
            "pending_low_score_samples": sum(1 for r in rows if r.is_low_score and not r.processed) if rows else len(low_score_pool.get_pending_samples()),
        },
    }


@router.get("/evaluation/low-scores")
async def get_low_score_samples(
    limit: int = Query(default=50, ge=1, le=200),
    failure_type: Optional[str] = Query(default=None, description="失败类型过滤"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """获取低分样本"""
    low_score_pool = get_low_score_pool()

    db_query = select(EvaluationResult).where(EvaluationResult.is_low_score.is_(True), EvaluationResult.processed.is_(False)).order_by(EvaluationResult.created_at.desc()).limit(limit)
    db_rows = (await db.execute(db_query)).scalars().all()
    if db_rows:
        samples = [{"sample_id": r.sample_id, "session_id": r.session_id, "trace_id": r.trace_id, "user_query": r.user_query or "", "response": r.response or "", "score": r.overall_score or 0, "failure_reason": r.failure_reason or "", "status": "pending", "created_at": r.created_at.isoformat() if r.created_at else None} for r in db_rows]
        if failure_type:
            samples = [s for s in samples if failure_type in s["failure_reason"]]
    elif failure_type:
        samples = low_score_pool.get_samples_by_failure_type(failure_type)
    else:
        samples = low_score_pool.get_pending_samples(limit)

    return {
        "code": 0,
        "message": "获取成功",
        "data": {
            "total": len(samples),
            "samples": samples[:limit],
        },
    }


@router.post("/evaluation/low-scores/{sample_id}/resolve")
async def resolve_low_score_sample(
    sample_id: str,
    action: str = Query(..., description="处理动作: knowledge_update/prompt_optimization/tool_fix"),
    note: str = Query(default="", description="处理备注"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    """处理低分样本"""
    low_score_pool = get_low_score_pool()
    row = await db.scalar(select(EvaluationResult).where(EvaluationResult.sample_id == sample_id))
    success = False
    if row:
        row.processed = True
        row.processed_at = datetime.utcnow()
        row.processing_action = action
        row.processing_note = note
        await db.commit()
        success = True
    else:
        success = low_score_pool.mark_processed(sample_id, action, note)

    if not success:
        raise HTTPException(status_code=404, detail="样本不存在")

    return {
        "code": 0,
        "message": "处理成功",
        "data": {"sample_id": sample_id, "action": action},
    }


@router.get("/ab-test/experiments")
async def get_ab_experiments(_: User = Depends(require_admin)):
    """获取A/B测试实验列表"""
    framework = get_ab_test_framework()
    experiments = framework.get_active_experiments()

    return {
        "code": 0,
        "message": "获取成功",
        "data": experiments,
    }


@router.post("/ab-test/experiments")
async def create_ab_experiment(
    experiment_id: str = Query(..., description="实验ID"),
    name: str = Query(..., description="实验名称"),
    description: str = Query(default="", description="实验描述"),
    variant_a: str = Query(..., description="变体A ID"),
    variant_b: str = Query(..., description="变体B ID"),
    traffic_split: float = Query(default=0.5, ge=0.1, le=0.9, description="流量分配比例"),
    _: User = Depends(require_admin),
):
    """创建A/B测试实验"""
    framework = get_ab_test_framework()

    variants = [
        {"id": variant_a, "name": f"对照组-{variant_a}"},
        {"id": variant_b, "name": f"实验组-{variant_b}"},
    ]

    experiment = framework.create_experiment(
        experiment_id=experiment_id,
        name=name,
        description=description,
        variants=variants,
        traffic_split=traffic_split,
    )

    return {
        "code": 0,
        "message": "创建成功",
        "data": {
            "experiment_id": experiment_id,
            "name": name,
            "status": "active",
        },
    }


@router.get("/ab-test/experiments/{experiment_id}/results")
async def get_ab_experiment_results(experiment_id: str, _: User = Depends(require_admin)):
    """获取A/B测试结果"""
    framework = get_ab_test_framework()
    results = framework.get_experiment_results(experiment_id)

    if not results:
        raise HTTPException(status_code=404, detail="实验不存在")

    return {
        "code": 0,
        "message": "获取成功",
        "data": results,
    }
