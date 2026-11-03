from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator


class Settings(BaseSettings):
    REFUND_ACTION_TTL_SECONDS: int = Field(default=900, ge=60, le=86400)
    APP_NAME: str = Field(default="智能客服系统")
    APP_VERSION: str = Field(default="1.0.0")
    DEBUG: bool = Field(default=True)
    ENVIRONMENT: str = Field(default="local")
    HEALTHCHECK_TIMEOUT_SECONDS: float = Field(default=2.0, gt=0)
    CORS_ALLOW_ORIGINS: str = Field(default="http://localhost:8100,http://127.0.0.1:8100")

    # 认证与限流。生产环境必须通过环境变量替换密钥，并关闭演示账号初始化。
    AUTH_SECRET_KEY: str = Field(default="local-dev-change-me-at-least-32-chars")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=1, le=1440)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, ge=1, le=90)
    AUTH_DEMO_ACCOUNTS_ENABLED: bool = Field(default=True)
    DEMO_USER_PASSWORD: str = Field(default="user12345", min_length=8)
    DEMO_ADMIN_PASSWORD: str = Field(default="admin12345", min_length=8)
    LOGIN_RATE_LIMIT_PER_MINUTE: int = Field(default=10, ge=1)
    CHAT_RATE_LIMIT_PER_MINUTE: int = Field(default=60, ge=1)
    CHAT_MESSAGE_MAX_LENGTH: int = Field(default=5000, ge=1, le=20000)
    MAX_UPLOAD_FILE_BYTES: int = Field(default=5 * 1024 * 1024, ge=1024)
    MAX_BATCH_UPLOAD_FILES: int = Field(default=10, ge=1, le=100)

    HOST: str = Field(default="0.0.0.0")
    PORT: int = Field(default=8100)
    # 生产部署时用多 worker 支撑并发（进程无状态，状态在 Redis）；DEBUG 下强制单进程。
    WORKERS: int = Field(default=1, ge=1, le=32)

    MYSQL_HOST: str = Field(default="localhost")
    MYSQL_PORT: int = Field(default=3306)
    MYSQL_USER: str = Field(default="root")
    MYSQL_PASSWORD: str = Field(default="")
    MYSQL_DATABASE: str = Field(default="customer_service")

    # 如果设置了 DATABASE_URL，则优先使用
    DATABASE_URL_OVERRIDE: str = Field(default="")

    REDIS_HOST: str = Field(default="localhost")
    REDIS_PORT: int = Field(default=6379)
    REDIS_PASSWORD: str = Field(default="")
    REDIS_DB: int = Field(default=0)
    REDIS_ENABLED: bool = Field(default=True)

    LLM_API_BASE: str = Field(default="http://localhost:8001")
    LLM_API_KEY: str = Field(default="")
    LLM_MODEL: str = Field(default="gpt-4o-mini")
    # 主模型的 DeepSeek 兼容 thinking 参数；空值沿用供应商默认，不传给备用/本地模型。
    LLM_THINKING_MODE: Literal["", "enabled", "disabled"] = ""
    LLM_STRUCTURED_OUTPUT_METHOD: Literal["json_schema", "function_calling"] = "json_schema"
    LLM_REQUEST_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)
    LLM_MAX_TOKENS: int = Field(default=1024, ge=64, le=8192)
    LLM_MAX_RETRIES: int = Field(default=2, ge=0, le=5)
    # 备用模型（provider 容灾）：主模型不可用时自动切换。三者都留空（默认）= 不启用
    # 备份链路，主模型故障时整体降级；配置 LLM_BACKUP_BASE 即启用（见 llm_service._init_models）。
    LLM_BACKUP_BASE: str = Field(default="")
    LLM_BACKUP_KEY: str = Field(default="")
    LLM_BACKUP_MODEL: str = Field(default="")
    # 解析型任务本地模型（意图识别 + 问题重写专用）：base+model 同时配置才启用。
    # 启用后 classify_intent / 查询改写优先走本地（如 vLLM 部署的 Qwen3-4B AWQ），
    # 失败自动回退主链（LLM_API_*，外接）；judge 与回复生成始终走主链，不受影响。
    LLM_LOCAL_BASE: str = Field(default="")
    LLM_LOCAL_KEY: str = Field(default="")
    LLM_LOCAL_MODEL: str = Field(default="")
    # 全局并发闸门：限制同一进程内同时进行的 LLM 调用数，防止多 worker 汇聚
    # 后打爆外部供应商的 RPM/TPM 配额（429）与连接池。
    LLM_MAX_CONCURRENCY: int = Field(default=8, ge=1, le=128)
    # LLM governance: limits are deliberately conservative in local/demo mode.
    LLM_PROMPT_VERSION: str = Field(default="v1", min_length=1, max_length=50)
    LLM_MODEL_VERSION: str = Field(default="gpt-4o-mini", min_length=1, max_length=100)
    LLM_SCHEMA_VERSION: str = Field(default="response.v1", min_length=1, max_length=50)
    LLM_ROUTE_VERSION: str = Field(default="rules-first.v1", min_length=1, max_length=50)
    LLM_MAX_COST_USD: float = Field(default=0.05, ge=0)
    LLM_DAILY_USER_TOKEN_BUDGET: int = Field(default=100000, ge=1)
    LLM_DAILY_USER_COST_USD: float = Field(default=5.0, ge=0)
    LLM_INPUT_PRICE_PER_1K_USD: float = Field(default=0.00015, ge=0)
    LLM_OUTPUT_PRICE_PER_1K_USD: float = Field(default=0.00060, ge=0)

    # ---- LLM 可观测性（Langfuse，外部平台，默认关闭）----
    # 通过 LangGraph CallbackHandler 上报完整执行轨迹，为自研 tracking 的补充观测。
    # 需同时配置 public/secret key，且 LANGFUSE_ENABLED=true 才会上报；云端或自托管均支持。
    LANGFUSE_ENABLED: bool = Field(default=False)
    LANGFUSE_PUBLIC_KEY: str = Field(default="")
    LANGFUSE_SECRET_KEY: str = Field(default="")
    # 自托管地址示例 http://localhost:3000；留空使用 Langfuse Cloud 默认地址。
    LANGFUSE_HOST: str = Field(default="")
    # 采样率 0.0-1.0：1.0 全量上报；高并发可调低以控制外部平台成本。
    LANGFUSE_SAMPLING_RATE: float = Field(default=1.0, ge=0.0, le=1.0)

    # ---- LLM 参与决策增强（Phase 3+）----
    # 意图识别主路径：True = LLM 结构化分类为主判，规则词表仅作硬性优先级
    # （转人工/投诉强情绪/退款动作/订单号识别/寒暄模板）；False（当前） = 规则优先、
    # LLM 仅低置信兜底（旧行为，性价比高：规则挡下约 74% 确定性意图，省一次模型调用）。
    # 开启后部分类型请求会多一次分类模型调用。
    LLM_INTENT_PRIMARY: bool = Field(default=False)
    # 规则改写未命中时，是否尝试 LLM 做多轮语义改写以增强 RAG 召回。
    LLM_QUERY_REWRITE_ENABLED: bool = Field(default=True)
    # 是否启用 LLM-as-judge 对 RAG/工具回答做事实与引用一致性校验（防幻觉）。
    LLM_JUDGE_ENABLED: bool = Field(default=True)
    # 是否启用长期记忆 / 用户画像（聚合 SQLite 历史，可选向量召回）。
    USER_MEMORY_ENABLED: bool = Field(default=True)
    # 用户画像注入 LLM 提示词的最大历史条数。
    USER_MEMORY_MAX_ITEMS: int = Field(default=5, ge=0, le=50)

    # ---- MCP 工具协议（可选，默认开启）----
    # 工具层通过 FastMCP server 暴露为标准 MCP 协议：agent 内经 MCP client 调用，
    # 亦可被任意 MCP Host（Claude Desktop / IDE / 其他 Agent）复用；
    # MCP client 不可用时自动回退到进程内直调，不影响主链路。
    MCP_ENABLED: bool = Field(default=True)
    # MCP server 的 Python 模块入口，stdio 传输下以 `python -m <entry>` 拉起子进程。
    MCP_SERVER_ENTRY: str = Field(default="app.agent.mcp_server")
    # MCP 传输模式：inprocess（同进程内存传输，默认）| stdio（子进程隔离）。
    MCP_TRANSPORT: str = Field(default="inprocess", pattern="^(inprocess|stdio)$")

    # Milvus 向量数据库
    MILVUS_HOST: str = Field(default="localhost")
    MILVUS_PORT: int = Field(default=19530)
    USE_MILVUS: bool = Field(default=False)

    # Embedding 服务
    EMBEDDING_API_BASE: str = Field(default="")
    EMBEDDING_API_KEY: str = Field(default="")
    EMBEDDING_MODEL: str = Field(default="")
    EMBEDDING_DIM: int = Field(default=1024)
    # 单次 Embedding 请求的文本条数上限。外接 API（如 DashScope）单次上限 20，
    # 本地 Ollama 无此限制可调大（如 64）降低批量入库的 HTTP 往返次数。
    EMBEDDING_BATCH_SIZE: int = Field(default=20)
    # 查询侧指令前缀（Qwen3-Embedding 等指令感知模型建议启用）：
    # 检索时自动加此前缀，文档入库侧不加；留空保持原行为。
    EMBEDDING_QUERY_INSTRUCTION: str = Field(default="")
    EMBEDDING_REVISION: str = Field(default="unspecified")

    # Reranker 重排服务（模型 reranker，优先外部 API，本地 bge-reranker-large 兜底）
    # 外部 API：RERANKER_API_BASE/KEY/MODEL 配置后启用；style=openai 走
    #   POST {base}/rerank（Cohere/Infinity 兼容），style=dashscope 走 DashScope
    #   text-rerank 端点。本地：RERANKER_LOCAL_BASE 指向 Infinity 部署的
    #   bge-reranker-large（docker-compose.reranker.yml），Cohere 兼容格式。
    # 未配置任何 provider 时 RAG_USE_RERANKER 回退启发式重排（_heuristic_rescore）。
    RERANKER_API_BASE: str = Field(default="")
    RERANKER_API_KEY: str = Field(default="")
    RERANKER_MODEL: str = Field(default="")
    RERANKER_API_STYLE: str = Field(default="openai", pattern="^(openai|dashscope)$")
    RERANKER_TIMEOUT_SECONDS: float = Field(default=3.0, gt=0)
    RERANKER_LOCAL_BASE: str = Field(default="")
    RERANKER_LOCAL_MODEL: str = Field(default="BAAI/bge-reranker-large")

    # RAG 检索增强生成
    COLLECTION_NAME: str = Field(default="customer_service_knowledge")
    RAG_TOP_K: int = Field(default=5)
    RAG_RECALL_K: int = Field(default=20)
    RAG_RERANK_K: int = Field(default=20)
    RAG_SIMILARITY_THRESHOLD: float = Field(default=0.2)
    # 检索融合已改为 RRF（Rank 融合，不依赖权重），历史上的
    # RAG_BM25_WEIGHT / RAG_VECTOR_WEIGHT / RAG_SEARCH_TOP_K_MULTIPLIER 已移除。
    # RAG_USE_RERANKER 是重排总开关：开启后优先模型 reranker（外部 API →
    # 本地 bge-reranker-large，见 RERANKER_* 配置），未配置/失败回退启发式
    # 重排（_heuristic_rescore：标题/关键词/类目命中加权）。
    RAG_USE_RERANKER: bool = Field(default=True)

    # 文档分块配置
    CHUNK_SIZE: int = Field(default=500)
    CHUNK_OVERLAP: int = Field(default=50)
    CHUNK_SPLIT_PATTERN: str = Field(default="heading")

    @property
    def DATABASE_URL(self) -> str:
        if self.DATABASE_URL_OVERRIDE:
            return self.DATABASE_URL_OVERRIDE
        return f"mysql+aiomysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"

    @property
    def REDIS_URL(self) -> str:
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def cors_origins(self) -> list[str]:
        """将逗号分隔的白名单转换为 FastAPI 可用格式。"""
        return [origin.strip() for origin in self.CORS_ALLOW_ORIGINS.split(",") if origin.strip()]

    @model_validator(mode="before")
    @classmethod
    def validate_cross_field_sanity(cls, values):
        """跨字段启动校验：配置自相矛盾时在进程启动即失败，而不是运行时静默失效。

        before 模式在字段解析前执行，需要容忍环境变量的字符串原始值。
        """
        def _to_int(v, name):
            if v is None or v == "":
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                raise ValueError(
                    f"环境变量 {name}={v!r} 不是有效整数，请在 .env 中检查"
                )

        def _to_float(v, name):
            if v is None or v == "":
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                raise ValueError(
                    f"环境变量 {name}={v!r} 不是有效数字，请在 .env 中检查"
                )

        # 服务端口必须落在合法区间，避免拼接出畸形连接串。
        for port_name in ("MYSQL_PORT", "REDIS_PORT", "MILVUS_PORT"):
            port = _to_int(values.get(port_name), port_name)
            if port is not None and not 1 <= port <= 65535:
                raise ValueError(f"环境变量 {port_name}={port} 必须在 1-65535 之间")

        chunk_size = _to_int(values.get("CHUNK_SIZE"), "CHUNK_SIZE")
        chunk_overlap = _to_int(values.get("CHUNK_OVERLAP"), "CHUNK_OVERLAP")
        if chunk_size is not None and chunk_overlap is not None and chunk_overlap >= chunk_size:
            raise ValueError(
                f"环境变量 CHUNK_OVERLAP={chunk_overlap} 必须小于 CHUNK_SIZE={chunk_size}"
            )

        max_tokens = _to_int(values.get("LLM_MAX_TOKENS"), "LLM_MAX_TOKENS")
        daily_budget = _to_int(values.get("LLM_DAILY_USER_TOKEN_BUDGET"), "LLM_DAILY_USER_TOKEN_BUDGET")
        if max_tokens is not None and daily_budget is not None and max_tokens > daily_budget:
            raise ValueError(
                f"环境变量 LLM_MAX_TOKENS={max_tokens} 不能大于单用户日预算 "
                f"LLM_DAILY_USER_TOKEN_BUDGET={daily_budget}"
            )

        sim_threshold = _to_float(values.get("RAG_SIMILARITY_THRESHOLD"), "RAG_SIMILARITY_THRESHOLD")
        if sim_threshold is not None and not 0.0 <= sim_threshold <= 1.0:
            raise ValueError(
                f"环境变量 RAG_SIMILARITY_THRESHOLD={sim_threshold} 必须在 0-1 之间"
            )

        embedding_dim = _to_int(values.get("EMBEDDING_DIM"), "EMBEDDING_DIM")
        if embedding_dim is not None and not 1 <= embedding_dim <= 8192:
            raise ValueError(
                f"环境变量 EMBEDDING_DIM={embedding_dim} 必须在 1-8192 之间"
            )
        return values

    @model_validator(mode="after")
    def validate_production_security(self):
        if self.ENVIRONMENT.lower() == "production":
            if len(self.AUTH_SECRET_KEY) < 32 or self.AUTH_SECRET_KEY == "local-dev-change-me-at-least-32-chars":
                raise ValueError("生产模式必须配置 AUTH_SECRET_KEY")
            if self.AUTH_DEMO_ACCOUNTS_ENABLED:
                raise ValueError("生产模式必须关闭 AUTH_DEMO_ACCOUNTS_ENABLED")
            if "*" in self.cors_origins:
                raise ValueError("生产模式禁止使用通配 CORS 来源")
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # 同一份 .env 同时供 Compose 和应用使用；忽略仅由 Compose 消费的额外变量。
        extra="ignore",
    )


settings = Settings()
