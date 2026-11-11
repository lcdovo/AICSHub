from contextlib import asynccontextmanager
import contextlib
import asyncio
import logging
import os
import time
import httpx

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.config.config import settings
from app.utils.database import init_database, close_redis
from app.utils.database import engine, get_redis
from sqlalchemy import text
from app.services.llm_service import LLMService
from app.utils.tracking import get_tracer, structured_logger, generate_trace_id
from app.utils.langfuse_tracing import shutdown_langfuse
from app.security import require_admin
from app.services.model_failure import failure_code
from app.routers.users import router as users_router
from app.routers.sessions import router as sessions_router
from app.routers.tickets import router as tickets_router
from app.routers.chat import router as chat_router
from app.routers.analytics import router as analytics_router
from app.routers.feedback import router as feedback_router
from app.routers.knowledge import router as knowledge_router
from app.routers.refunds import router as refunds_router

logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

llm_service = LLMService()


async def warmup_local_embedding() -> None:
    """用应用真实调用的 OpenAI 兼容接口预热本地 Embedding。"""
    base = (settings.EMBEDDING_API_BASE or "").rstrip("/")
    if not base or not settings.EMBEDDING_API_KEY or "embedding-local" not in base:
        return
    started = time.perf_counter()
    try:
        timeout = min(float(settings.EMBEDDING_TIMEOUT_SECONDS), 20.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                f"{base}/embeddings",
                headers={"Authorization": f"Bearer {settings.EMBEDDING_API_KEY}"},
                json={"model": settings.EMBEDDING_MODEL, "input": ["warmup"],
                      "encoding_format": "float", "dimensions": settings.EMBEDDING_DIM},
            )
            response.raise_for_status()
        logger.info("Local embedding API warmup completed in %.0fms", (time.perf_counter() - started) * 1000)
    except Exception as exc:
        # 本地向量服务是可降级依赖；预热失败不能阻止 API 启动。
        logger.warning("Local embedding API warmup unavailable: %s", type(exc).__name__)


async def keep_local_embedding_warm() -> None:
    """低频保持本地向量模型活跃，降低空闲后的首请求长尾。"""
    while True:
        await asyncio.sleep(30)
        await warmup_local_embedding()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")

    app.state.dependency_health = {}
    try:
        await init_database()
        logger.info("Database initialized successfully")
        app.state.dependency_health["database"] = {"status": "healthy", "backend": settings.DATABASE_URL.split(":", 1)[0]}
    except Exception as e:
        # 数据库是必需依赖：记录明确状态，但保留进程运行以便 /health 暴露原因。
        logger.error("Database initialization failed: %s", type(e).__name__)
        app.state.dependency_health["database"] = {
            "status": "unhealthy", "backend": settings.DATABASE_URL.split(":", 1)[0],
            "reason": type(e).__name__,
        }

    app.state.dependency_health["llm"] = {
        "status": "configured" if settings.LLM_API_KEY else "degraded",
        "backend": settings.LLM_MODEL if settings.LLM_API_KEY else "unconfigured",
        "reason": None if settings.LLM_API_KEY else "未配置 LLM_API_KEY，回复将降级为默认话术",
        "timeout_seconds": settings.HEALTHCHECK_TIMEOUT_SECONDS,
    }
    app.state.dependency_health["milvus"] = {
        "status": "disabled" if not settings.USE_MILVUS else "unknown",
        "backend": "memory" if not settings.USE_MILVUS else "milvus",
        "reason": "USE_MILVUS=false，使用内存检索" if not settings.USE_MILVUS else None,
        "timeout_seconds": settings.HEALTHCHECK_TIMEOUT_SECONDS,
    }
    app.state.dependency_health["redis"] = {
        "status": "optional", "backend": "redis", "reason": "Redis 为可选依赖，失败时使用进程内存降级"
    }

    # OpenTelemetry：配置 OTEL_EXPORTER_OTLP_ENDPOINT 后启用 OTLP 导出，否则 sdk-only。
    try:
        from app.utils.otel import enable_otlp_export

        enable_otlp_export()
    except Exception:
        pass

    try:
        from app.agent.mcp_client import warmup_mcp_client
        mcp_ready = await warmup_mcp_client()
        app.state.dependency_health["mcp"] = {
            "status": "healthy" if mcp_ready else "degraded",
            "backend": settings.MCP_TRANSPORT if mcp_ready else "direct",
        }
        await warmup_local_embedding()
        app.state.embedding_keepalive = asyncio.create_task(keep_local_embedding_warm())
        yield
    finally:
        keepalive = getattr(app.state, "embedding_keepalive", None)
        if keepalive is not None:
            keepalive.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keepalive
        await shutdown_resources()


async def shutdown_resources():
    """聊天图与健康检查服务各自拥有连接池；单项失败不能跳过其余资源。"""
    logger.info("Shutting down...")
    try:
        shutdown_langfuse()
    except Exception as exc:
        logger.error("Shutdown langfuse failed: %s", type(exc).__name__)
    from app.agent.mcp_client import shutdown_mcp_client
    from app.services.chat_service import chat_service
    from app.services.reranker_service import reset_reranker

    async def close_reranker():
        await asyncio.to_thread(reset_reranker)

    closers = [("mcp", shutdown_mcp_client), ("reranker", close_reranker), ("redis", close_redis)]
    services = [llm_service, getattr(chat_service.agent_graph, "llm_service", None)]
    seen = set()
    for service in services:
        if service is not None and id(service) not in seen:
            seen.add(id(service))
            closers.insert(-1, ("model", service.close))
    for name, close in closers:
        try:
            await close()
        except Exception as exc:
            logger.error("Shutdown %s failed: %s", name, type(exc).__name__)
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="企业级智能客服与工单自动处理系统",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "用户管理", "description": "登录、Token 轮换与用户资源管理"},
        {"name": "对话管理", "description": "同步/流式聊天、会话历史与工具目录"},
        {"name": "知识库管理", "description": "文档生命周期、检索与 RAG 问答（管理员写入）"},
        {"name": "工单管理", "description": "工单状态机、转人工与通知 outbox"},
        {"name": "评测与可观测性", "description": "Trace、评测结果、反馈样本与指标"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def tracing_middleware(request: Request, call_next):
    """全链路追踪中间件"""
    trace_id = request.headers.get("X-Trace-Id", generate_trace_id())
    request.state.trace_id = trace_id
    request.state.start_time = time.time()
    tracer = get_tracer()
    tracer.start_trace(trace_id)
    request_span = tracer.start_span(trace_id, "http_request")

    try:
        response = await call_next(request)
        duration_ms = int((time.time() - request.state.start_time) * 1000)
        tracer.end_span(request_span, status="ok" if response.status_code < 400 else "error")
        tracer.end_trace(trace_id)

        response.headers["X-Trace-Id"] = trace_id
        response.headers["X-Response-Time"] = f"{duration_ms}ms"

        structured_logger.log_response(
            trace_id=trace_id,
            status_code=response.status_code,
            duration_ms=duration_ms,
            method=request.method,
            path=request.url.path,
        )

        if response.status_code < 400:
            tracer.record_response(duration_ms, True)
        else:
            tracer.record_response(duration_ms, False)
            tracer.record_error("http_error", f"Status: {response.status_code}")

        return response
    except Exception as exc:
        duration_ms = int((time.time() - request.state.start_time) * 1000)
        tracer.end_span(request_span, status="error", error_message=type(exc).__name__)
        tracer.end_trace(trace_id)
        structured_logger.log_error(
            trace_id=trace_id,
            error_type="internal_error",
            error_message=type(exc).__name__,
            method=request.method,
            path=request.url.path,
            duration_ms=duration_ms,
        )
        raise


app.include_router(users_router)
app.include_router(sessions_router)
app.include_router(tickets_router)
app.include_router(chat_router)
from app.routers.model_calls import router as model_calls_router
from app.routers.jobs import router as jobs_router
app.include_router(jobs_router)
app.include_router(model_calls_router)
app.include_router(analytics_router)
app.include_router(feedback_router)
app.include_router(knowledge_router)
app.include_router(refunds_router)

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs",
    }


@app.get("/login.html")
async def login_page():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/user.html")
async def user_page():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/admin.html")
async def admin_page():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


async def _probe_dependencies() -> dict:
    """以短超时探测依赖，并返回可解释的降级原因。"""
    timeout = settings.HEALTHCHECK_TIMEOUT_SECONDS
    result = dict(getattr(app.state, "dependency_health", {}))

    try:
        started = time.perf_counter()
        async with engine.connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=timeout)
        result["database"] = {"status": "healthy", "backend": settings.DATABASE_URL.split(":", 1)[0], "latency_ms": round((time.perf_counter() - started) * 1000, 2), "timeout_seconds": timeout}
    except Exception as exc:
        result["database"] = {"status": "unhealthy", "backend": settings.DATABASE_URL.split(":", 1)[0], "reason": failure_code(exc), "timeout_seconds": timeout}

    redis = await get_redis()
    if redis is None:
        result["redis"] = {"status": "degraded", "backend": "memory", "reason": "Redis 客户端未初始化，使用进程内存降级", "timeout_seconds": timeout}
    else:
        try:
            started = time.perf_counter()
            await asyncio.wait_for(redis.ping(), timeout=timeout)
            result["redis"] = {"status": "healthy", "backend": "redis", "latency_ms": round((time.perf_counter() - started) * 1000, 2), "timeout_seconds": timeout}
        except Exception as exc:
            result["redis"] = {"status": "degraded", "backend": "memory", "reason": failure_code(exc), "timeout_seconds": timeout}

    if settings.USE_MILVUS:
        try:
            from app.services.knowledge_base import get_knowledge_base
            knowledge_base = await asyncio.to_thread(get_knowledge_base)
            milvus = await asyncio.to_thread(knowledge_base.health_check)
            backend = milvus.get("backend", "memory")
            result["milvus"] = {
                "status": "healthy" if milvus.get("healthy") and backend == "milvus" else "degraded",
                **milvus,
                "reason": milvus.get("degraded_reason") or (milvus.get("message") if backend != "milvus" else None),
                "timeout_seconds": timeout,
            }
        except Exception as exc:
            result["milvus"] = {"status": "degraded", "backend": "memory", "reason": failure_code(exc), "timeout_seconds": timeout}
    return result


@app.get("/health", response_model=dict)
async def health_check():
    dependencies = await _probe_dependencies()
    required_ok = dependencies.get("database", {}).get("status") == "healthy"
    degraded = any(item.get("status") in {"degraded", "disabled", "optional"} for item in dependencies.values())
    return {
        "status": "healthy" if required_ok else "unhealthy",
        "mode": "local" if not settings.LLM_API_KEY and not settings.USE_MILVUS else "integration",
        "degraded": degraded,
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "dependencies": dependencies,
    }


@app.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics(_: object = Depends(require_admin)):
    """受管理员保护的 Prometheus 指标出口。"""
    return get_tracer().to_prometheus()


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error("Unhandled exception: error_type=%s", type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "message": "Internal Server Error",
            "detail": "An unexpected error occurred",
        },
    )


if __name__ == "__main__":
    import uvicorn

    # DEBUG（--reload）模式下必须单 worker；生产按 WORKERS 多进程扩容，
    # 各 worker 无状态（会话/锁/checkpoint 均在 Redis），天然支持横扩。
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        workers=1 if settings.DEBUG else settings.WORKERS,
    )
