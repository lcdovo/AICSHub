"""OpenTelemetry 接入 - 底层 span 标准化（GenAI Semantic Conventions）。

接入原则：
- SDK 常驻：默认 TracerProvider 不带 exporter（span 开销微秒级），业务零成本；
- 显式开启导出：设置 ``OTEL_EXPORTER_OTLP_ENDPOINT`` 后调用
  :func:`enable_otlp_export`，span 以 OTLP 协议发往任意可观测后端
  （Jaeger/Tempo/云厂商等），与 Langfuse/自研 agent_traces 形成互补；
- 属性命名对齐 OTel GenAI 语义约定（``gen_ai.*``），保证跨后端可移植性。

上层：``app.utils.tracking.Tracer`` 在 start_span/end_span 处镜像创建 OTel span，
对外（analytics、agent_traces 落库、日志）契约保持不变。
"""
import logging
import os
from typing import Any, Dict, Optional

from opentelemetry import trace as otel_trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

logger = logging.getLogger(__name__)

# ---- GenAI Semantic Conventions（OTel 官方 gen_ai.* 键）----
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_LATENCY_MS = "gen_ai.response.latency.ms"
GEN_AI_USER_ID = "ai.agent.user.id"
GEN_AI_SESSION_ID = "ai.agent.session.id"
GEN_AI_PROMPT_VERSION = "ai.agent.prompt_version"

_provider: Any = None
_otlp_enabled: bool = False


def get_tracer_provider() -> Any:
    """进程级 TracerProvider 单例（无 exporter，sdk-only）。"""
    global _provider
    if _provider is not None:
        return _provider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    _provider = TracerProvider(
        resource=Resource.create({"service.name": "ai-customer"})
    )
    otel_trace.set_tracer_provider(_provider)
    return _provider


def get_tracer(name: str = "ai-customer"):
    """获取命名 Tracer（隐式初始化 provider）。"""
    return otel_trace.get_tracer(name)


def enable_otlp_export(endpoint: Optional[str] = None) -> bool:
    """启用 OTLP HTTP 导出（幂等）。endpoint 缺省读取 OTEL_EXPORTER_OTLP_ENDPOINT。

    返回是否启用成功；未配置 endpoint 时保持 sdk-only（不报错）。
    """
    global _otlp_enabled
    if _otlp_enabled:
        return True
    provider = get_tracer_provider()
    endpoint = endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT 未配置，OTel span 仅在本进程内生成")
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        _otlp_enabled = True
        logger.info("OpenTelemetry OTLP 导出已启用: %s", endpoint)
        return True
    except Exception as exc:
        logger.warning("启用 OTLP 导出失败，保持 sdk-only: %s", exc)
        return False


def start_span(
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Optional[Dict[str, Any]] = None,
) -> Span:
    """创建 OTel span（对齐项目 Tracer.start_span 语义）。"""
    return get_tracer().start_span(name, kind=kind, attributes=attributes or {})


def end_span(span: Optional[Span], status: str = "ok", error_message: Optional[str] = None) -> None:
    """结束 OTel span：非 ok 置 ERROR 状态，保留错误描述。"""
    if span is None:
        return
    try:
        if status != "ok":
            span.set_status(Status(StatusCode.ERROR, description=error_message or status))
        else:
            span.set_status(StatusCode.OK)
    except Exception:
        pass
    try:
        span.end()
    except Exception:
        pass