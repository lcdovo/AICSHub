from pydantic_settings import BaseSettings
from pydantic import Field, model_validator


class Settings(BaseSettings):
    APP_NAME: str = Field(default="智能客服系统")
    APP_VERSION: str = Field(default="1.0.0")
    DEBUG: bool = Field(default=True)
    ENVIRONMENT: str = Field(default="local")
    # 本地 profile 默认开启 Mock，避免启动时依赖外部模型服务。
    MOCK_LLM_ENABLED: bool = Field(default=True)
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
    RAG_BM25_WEIGHT: float = Field(default=0.5)
    RAG_VECTOR_WEIGHT: float = Field(default=0.5)
    RAG_USE_RERANKER: bool = Field(default=True)
    RAG_SEARCH_TOP_K_MULTIPLIER: int = Field(default=3)

    # 文档分块配置
    CHUNK_SIZE: int = Field(default=500)
    CHUNK_OVERLAP: int = Field(default=50)
    CHUNK_SPLIT_PATTERN: str = Field(default="sentence")

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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
