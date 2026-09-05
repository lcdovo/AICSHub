FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# aiomysql 需要编译依赖；SQLite 本地镜像通过 build arg 额外安装 aiosqlite，
# 不把仅本地开发的依赖带入默认生产层。
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock ./
RUN pip install --requirement requirements.txt --constraint requirements.lock
ARG INSTALL_LOCAL_DEPS=false
RUN if [ "$INSTALL_LOCAL_DEPS" = "true" ]; then pip install "aiosqlite==0.20.0"; fi

# 生产容器不以 root 身份运行。挂载的数据目录由 compose 映射到 /app/data。
RUN addgroup --system --gid 10001 appuser \
    && adduser --system --uid 10001 --gid 10001 appuser
COPY --chown=appuser:appuser . .
RUN mkdir -p /app/data && chown -R appuser:appuser /app/data
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
