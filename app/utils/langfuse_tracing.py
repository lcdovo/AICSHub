"""Langfuse 外部观测接入（可选，默认关闭）。

与自研 tracking（Tracer / MetricsCollector / AlertManager）互补：
- 自研：业务指标、P0-P2 告警、span 落库 agent_traces、Prometheus 出口；
- Langfuse：通过 LangGraph CallbackHandler 记录每次请求的完整执行轨迹、
  token 成本与 prompt 版本，用于生产环境的回放与调试。

接入原则：
- LANGFUSE_ENABLED=false 或缺少密钥时零成本（懒加载，不提前 import langfuse）；
- 采样率在请求入口按概率判定（见 app/agent/graph.py 的 _with_langfuse）；
- 上报内容含 prompt 与工具入参，接入前需确认数据合规与脱敏策略。
"""

import logging
import os
import threading
from typing import Any, Optional

from app.config.config import settings

logger = logging.getLogger(__name__)

_handler: Optional[Any] = None
_lock = threading.Lock()


def get_langfuse_handler() -> Optional[Any]:
    """返回 Langfuse CallbackHandler 单例；未启用或未配置时返回 None。

    该 handler 会随 LangGraph 调用自动构建完整 Trace 树，
    并在并发下复用同一个客户端实例。
    """
    global _handler
    if _handler is not None:
        return _handler
    if not settings.LANGFUSE_ENABLED:
        return None
    if not (settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY):
        logger.warning("LANGFUSE_ENABLED=true 但缺少 LANGFUSE_PUBLIC_KEY/SECRET_KEY，跳过 Langfuse 上报")
        return None

    with _lock:
        if _handler is not None:
            return _handler
        try:
            from langfuse.callback import CallbackHandler

            # CallbackHandler 优先从环境变量读取密钥与地址；这里透传 settings 值，
            # 保证与 .env / 配置文件来源一致，且不依赖 SDK 内部参数名差异。
            os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.LANGFUSE_PUBLIC_KEY)
            os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.LANGFUSE_SECRET_KEY)
            if settings.LANGFUSE_HOST:
                os.environ.setdefault("LANGFUSE_HOST", settings.LANGFUSE_HOST)

            _handler = CallbackHandler()
            logger.info("Langfuse CallbackHandler 已初始化，开始上报 Agent 执行轨迹")
        except ImportError:
            logger.warning("langfuse 未安装，跳过 Langfuse 上报（pip install langfuse）")
            return None
        except Exception as exc:  # 外部平台故障不得影响主链路
            logger.warning(f"Langfuse 初始化失败，继续使用自研 tracking: {exc}")
            return None
    return _handler


def shutdown_langfuse() -> None:
    """应用关闭时刷出剩余的待上报数据。

    Langfuse 客户端是进程级单例，flush() 会将队列中所有 Trace 写入远端，
    避免进程退出时丢失最后一批数据。
    """
    if _handler is None:
        return
    try:
        from langfuse import Langfuse

        Langfuse().flush()
        logger.info("Langfuse 已刷出剩余 Trace 数据")
    except Exception as exc:
        logger.warning(f"Langfuse flush 失败（不影响主流程）: {exc}")