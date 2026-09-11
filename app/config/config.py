from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator


class Settings(BaseSettings):
    APP_NAME: str = Field(default="智能客服系统")
    APP_VERSION: str = Field(default="1.0.0")
    DEBUG: bool = Field(default=True)
    ENVIRONMENT: str = Field(default="local")
    HEALTHCHECK_TIMEOUT_SECONDS: float = Field(default=2.0, gt=0)
    CORS_ALLOW_ORIGINS: str = Field(default="http://localhost:8000,http://127.0.0.1:8000")

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
    PORT: int = Field(default=8000)

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
    LLM_REQUEST_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)
    LLM_MAX_TOKENS: int = Field(default=1024, ge=64, le=8192)
    LLM_MAX_RETRIES: int = Field(default=2, ge=0, le=5)
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
    # 规则改写未命中时，是否尝试 LLM 做多轮语义改写以增强 RAG 召回。
    LLM_QUERY_REWRITE_ENABLED: bool = Field(default=True)
    # 是否启用 LLM-as-judge 对 RAG/工具回答做事实与引用一致性校验（防幻觉）。
    LLM_JUDGE_ENABLED: bool = Field(default=True)
    # 是否启用长期记忆 / 用户画像（聚合 SQLite 历史，可选向量召回）。
    USER_MEMORY_ENABLED: bool = Field(default=True)
    # 用户画像注入 LLM 提示词的最大历史条数。
    USER_MEMORY_MAX_ITEMS: int = Field(default=5, ge=0, le=50)

    # Milvus 向量数据库
    MILVUS_HOST: str = Field(default="localhost")
    MILVUS_PORT: int = Field(default=19530)
    USE_MILVUS: bool = Field(default=False)

    # Embedding 服务
    EMBEDDING_API_BASE: str = Field(default="")
    EMBEDDING_API_KEY: str = Field(default="")
    EMBEDDING_MODEL: str = Field(default="")
    EMBEDDING_DIM: int = Field(default=1024)

    # RAG 检索增强生成
    COLLECTION_NAME: str = Field(default="customer_service_knowledge")
    RAG_TOP_K: int = Field(default=5)
    RAG_SIMILARITY_THRESHOLD: float = Field(default=0.2)
    # 检索融合已改为 RRF（Rank 融合，不依赖权重），历史上的
    # RAG_BM25_WEIGHT / RAG_VECTOR_WEIGHT / RAG_SEARCH_TOP_K_MULTIPLIER 已移除。
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
