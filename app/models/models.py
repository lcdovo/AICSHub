from app.utils.utc_time import utc_now_db
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Enum, Float,
    Index, ForeignKey, JSON, Numeric, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, relationship
import enum


class Base(DeclarativeBase):
    pass


class UserLevel(str, enum.Enum):
    NORMAL = "normal"
    VIP = "vip"
    ENTERPRISE = "enterprise"


class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    nickname = Column(String(50), nullable=True)
    level = Column(Enum(UserLevel), default=UserLevel.NORMAL)
    role = Column(String(20), nullable=False, default=UserRole.USER.value, index=True)
    password_hash = Column(String(255), nullable=True)
    token_version = Column(Integer, nullable=False, default=0)
    disabled_at = Column(DateTime, nullable=True)
    avatar_url = Column(String(500), nullable=True)
    tags = Column(JSON, default=list)
    status = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now_db)
    updated_at = Column(DateTime, default=utc_now_db, onupdate=utc_now_db)

    sessions = relationship("Session", back_populates="user")
    tickets = relationship("Ticket", back_populates="user")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    replaced_by = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=utc_now_db, nullable=False)

    user = relationship("User", back_populates="refresh_tokens")


class SessionStatus(str, enum.Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    PENDING = "pending"


class Session(Base):
    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(Enum(SessionStatus), default=SessionStatus.ACTIVE, index=True)
    csat_score = Column(Integer, nullable=True)
    message_count = Column(Integer, default=0)
    last_intent = Column(String(50), nullable=True)
    summary = Column(Text, nullable=True)
    summary_cursor = Column(Integer, nullable=False, default=0)
    summary_version = Column(Integer, nullable=False, default=0)
    summary_policy = Column(String(40), nullable=False, default="rules-v1")
    version = Column(Integer, nullable=False, default=0)
    context_data = Column(JSON, nullable=True)
    lease_token = Column(String(36), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now_db, index=True)
    updated_at = Column(DateTime, default=utc_now_db, onupdate=utc_now_db)
    closed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="sessions")
    messages = relationship("Message", back_populates="session", cascade="all, delete-orphan")


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False, index=True)
    role = Column(Enum(MessageRole), nullable=False)
    content = Column(Text, nullable=False)
    token_count = Column(Integer, default=0)
    response_time_ms = Column(Integer, nullable=True)
    tool_calls = Column(JSON, nullable=True)
    run_id = Column(String(36), nullable=True, index=True)
    created_at = Column(DateTime, default=utc_now_db, index=True)

    session = relationship("Session", back_populates="messages")


class Order(Base):
    """持久化订单投影，供客服演示中的订单查询工具使用。"""

    __tablename__ = "orders"

    order_id = Column(String(32), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(50), nullable=False)
    status_code = Column(String(50), nullable=False)
    items = Column(JSON, nullable=False, default=list)
    total_amount = Column(Numeric(12, 2), nullable=False, default=0)
    refunded_amount = Column(Numeric(12, 2), nullable=False, default=0)
    received_at = Column(DateTime, nullable=True)
    currency = Column(String(3), nullable=False, default="CNY")
    shipping = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utc_now_db, nullable=False)
    pay_method = Column(String(50), nullable=True)
    address = Column(String(500), nullable=True)


class RefundApplication(Base):
    """模拟退换货待审核申请；不代表真实支付或退款到账。"""
    __tablename__ = "refund_applications"
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    actor_id = Column(Integer, nullable=False)
    session_id = Column(String(36), nullable=False, index=True)
    source_message_id = Column(Integer, nullable=False)
    order_id = Column(String(32), ForeignKey("orders.order_id"), nullable=False, index=True)
    type = Column(String(20), nullable=False)
    reason = Column(Text, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="CNY")
    status = Column(String(30), nullable=False, default="pending_review")
    is_simulated = Column(Boolean, nullable=False, default=True)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    request_hash = Column(String(64), nullable=False)
    action_id = Column(String(36), nullable=True, unique=True)
    business_hash = Column(String(64), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_db)


class RefundAction(Base):
    __tablename__ = "refund_actions"
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    actor_id = Column(Integer, nullable=False)
    session_id = Column(String(36), nullable=False, index=True)
    order_id = Column(String(32), nullable=False, index=True)
    type = Column(String(20), nullable=False)
    reason = Column(Text, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="pending")
    expires_at = Column(DateTime, nullable=False)
    application_id = Column(String(36), nullable=True)
    preconditions = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_db)


class RefundBusinessClaim(Base):
    __tablename__ = "refund_business_claims"
    id = Column(String(64), primary_key=True)
    revision = Column(Integer, nullable=False, default=0)


class TicketStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RESOLVED = "resolved"
    CLOSED = "closed"
    ESCALATED = "escalated"


class TicketPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)
    status = Column(Enum(TicketStatus), default=TicketStatus.PENDING, index=True)
    priority = Column(Enum(TicketPriority), default=TicketPriority.MEDIUM, index=True)
    content = Column(Text, nullable=False)
    session_id = Column(String(36), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True, index=True)
    assigned_to = Column(String(50), nullable=True)
    resolution = Column(Text, nullable=True)
    idempotency_key = Column(String(128), nullable=True, unique=True, index=True)
    sla_deadline = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now_db, index=True)
    updated_at = Column(DateTime, default=utc_now_db, onupdate=utc_now_db)
    resolved_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="tickets")


class HandoffStatus(str, enum.Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class HandoffCapacityGate(Base):
    """座席分配事务闸门；无内存锁依赖，容量仍由有效指派计算。"""
    __tablename__ = "handoff_capacity_gates"
    agent_id = Column(String(50), primary_key=True)
    revision = Column(Integer, nullable=False, default=0)


class HandoffRequestRecord(Base):
    """转人工请求持久化记录，与客服分配内存缓存解耦。"""

    __tablename__ = "handoff_requests"

    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    reason = Column(String(100), nullable=False)
    priority = Column(String(20), nullable=False, default="normal", index=True)
    context = Column(JSON, nullable=True)
    status = Column(Enum(HandoffStatus), nullable=False, default=HandoffStatus.PENDING, index=True)
    assigned_to = Column(String(50), nullable=True, index=True)
    resolution = Column(Text, nullable=True)
    sla_deadline = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now_db, nullable=False, index=True)
    assigned_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    idempotency_key = Column(String(128), nullable=True, unique=True, index=True)


class NotificationStatus(str, enum.Enum):
    SENDING = "sending"
    UNKNOWN = "unknown"
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class NotificationRecord(Base):
    """Outbox待发送事件记录；当前尚无投递适配器或消费worker。"""

    __tablename__ = "notification_records"

    id = Column(String(36), primary_key=True)
    event_type = Column(String(50), nullable=False, index=True)
    recipient = Column(String(200), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(Enum(NotificationStatus), nullable=False, default=NotificationStatus.PENDING, index=True)
    is_simulated = Column(Boolean, nullable=False, default=False)
    provider_receipt = Column(String(128), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    idempotency_key = Column(String(128), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=utc_now_db, nullable=False, index=True)
    sent_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=utc_now_db, onupdate=utc_now_db)


class OperationAudit(Base):
    """副作用操作审计与幂等记录。"""

    __tablename__ = "operation_audits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    operation = Column(String(50), nullable=False, index=True)
    idempotency_key = Column(String(128), nullable=False, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    resource_id = Column(String(100), nullable=True, index=True)
    request_payload = Column(JSON, nullable=True)
    result_payload = Column(JSON, nullable=True)
    status = Column(String(20), nullable=False, default="succeeded")
    created_at = Column(DateTime, default=utc_now_db, nullable=False, index=True)


class UserFeedback(Base):
    """用户反馈持久化记录。"""

    __tablename__ = "user_feedback"

    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    message_id = Column(Integer, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True)
    feedback_type = Column(String(20), nullable=False, index=True)
    score = Column(Integer, nullable=True)
    content = Column(Text, nullable=True)
    categories = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utc_now_db, nullable=False, index=True)


class KnowledgeDoc(Base):
    __tablename__ = "knowledge_docs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    category = Column(String(50), nullable=False, index=True)
    content = Column(Text, nullable=False)
    chunk_count = Column(Integer, default=0)
    version = Column(Integer, default=1)
    is_published = Column(Boolean, default=True)
    keywords = Column(JSON, nullable=True, default=list)
    source = Column(String(500), nullable=True, default="")
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now_db)
    updated_at = Column(DateTime, default=utc_now_db, onupdate=utc_now_db)

    stable_id = Column(String(40), nullable=True, unique=True, index=True)
    published_version = Column(Integer, nullable=True)
    lifecycle_status = Column(String(20), nullable=False, default="legacy")

    chunks = relationship("KnowledgeChunk", back_populates="document", cascade="all, delete-orphan")


class KnowledgeChunk(Base):
    """持久化的文档分块及其 embedding。

    chunk_id 在索引后端和数据库中保持稳定，version/is_active 用于避免文档更新时
    旧向量与新内容混用。embedding 使用 JSON 保存，SQLite 本地 profile 无需额外服务。
    """

    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("knowledge_docs.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_id = Column(String(100), unique=True, nullable=False, index=True)
    content = Column(Text, nullable=False)
    embedding = Column(JSON, nullable=True)
    chunk_index = Column(Integer, nullable=False, default=0)
    version = Column(Integer, nullable=False, default=1, index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_at = Column(DateTime, default=utc_now_db, nullable=False)

    content_hash = Column(String(64), nullable=True)
    start_offset = Column(Integer, nullable=True)
    end_offset = Column(Integer, nullable=True)
    section = Column(String(200), nullable=True)
    chunk_policy = Column(String(40), nullable=True)
    source_context = Column(JSON, nullable=True)

    document = relationship("KnowledgeDoc", back_populates="chunks")


class AgentTrace(Base):
    __tablename__ = "agent_traces"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String(100), unique=True, nullable=False, index=True)
    request_trace_id = Column(String(100), nullable=True, index=True)
    span_id = Column(String(100), nullable=True, unique=True, index=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=True, index=True)
    intent = Column(String(50), nullable=True, index=True)
    node_name = Column(String(50), nullable=False)
    node_order = Column(Integer, default=0)
    input_data = Column(JSON, nullable=True)
    output_data = Column(JSON, nullable=True)
    tool_calls = Column(JSON, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    token_usage = Column(JSON, nullable=True)
    model_name = Column(String(100), nullable=True)
    prompt_version = Column(String(50), nullable=True)
    schema_version = Column(String(50), nullable=True)
    route_version = Column(String(50), nullable=True)
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now_db, index=True)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=True, index=True)
    trace_id = Column(String(100), nullable=True, index=True)
    intent = Column(String(50), nullable=True, index=True)
    sample_id = Column(String(50), nullable=True, index=True)
    accuracy_score = Column(Integer, nullable=True)
    completeness_score = Column(Integer, nullable=True)
    safety_score = Column(Integer, nullable=True)
    overall_score = Column(Integer, nullable=True, index=True)
    is_low_score = Column(Boolean, default=False, index=True)
    failure_reason = Column(String(200), nullable=True)
    evaluator = Column(String(50), nullable=True, default="heuristic")
    model_name = Column(String(100), nullable=True)
    prompt_version = Column(String(50), nullable=True)
    schema_version = Column(String(50), nullable=True)
    route_version = Column(String(50), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    token_usage = Column(JSON, nullable=True)
    user_query = Column(Text, nullable=True)
    response = Column(Text, nullable=True)
    categories = Column(JSON, nullable=True)
    processed_at = Column(DateTime, nullable=True)
    processing_action = Column(String(50), nullable=True)
    processing_note = Column(Text, nullable=True)
    processed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utc_now_db, index=True)


class ChatRun(Base):
    """轮次事实；最终消息和最终事件原子提交，独立于Redis。"""
    __tablename__ = "chat_runs"
    __table_args__ = (UniqueConstraint("user_id", "session_id", "client_message_id", name="uq_run_client_message"),)
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    actor_id = Column(Integer, nullable=False)
    session_id = Column(String(36), nullable=False, index=True)
    client_message_id = Column(String(128), nullable=False)
    request_hash = Column(String(64), nullable=False)
    request_data = Column(JSON, nullable=False)
    action_id = Column(String(36), nullable=True)
    status = Column(String(30), nullable=False, default="accepted", index=True)
    lease_token = Column(String(36), nullable=True)
    redis_lock_token = Column(String(36), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    user_message_id = Column(Integer, nullable=True)
    assistant_message_id = Column(Integer, nullable=True)
    sequence = Column(Integer, nullable=False, default=0)
    result = Column(JSON, nullable=True)
    error_code = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_db)
    updated_at = Column(DateTime, nullable=False, default=utc_now_db)


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_run_event_sequence"),)
    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), nullable=False, index=True)
    session_id = Column(String(36), nullable=False)
    sequence = Column(Integer, nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    event_type = Column(String(40), nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_db)


class BackgroundJob(Base):
    __tablename__ = "background_jobs"
    id = Column(String(36), primary_key=True)
    event_key = Column(String(128), nullable=False, unique=True)
    kind = Column(String(40), nullable=False, index=True)
    resource_id = Column(String(100), nullable=False)
    user_id = Column(Integer, nullable=True, index=True)
    status = Column(String(30), nullable=False, default="queued", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    next_run = Column(DateTime, nullable=False, default=utc_now_db, index=True)
    lease_token = Column(String(36), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    last_error = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_db)
    updated_at = Column(DateTime, nullable=False, default=utc_now_db)


class SimulatedDelivery(Base):
    """模拟供应商幂等回执，与业务事务分离，绝不声明真实发送。"""
    __tablename__ = "simulated_deliveries"
    event_id = Column(String(36), primary_key=True)
    receipt_id = Column(String(128), nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_db)


class KnowledgeVersion(Base):
    __tablename__ = "knowledge_versions"
    __table_args__ = (UniqueConstraint("document_id", "number", name="uq_knowledge_version"),)
    id = Column(String(36), primary_key=True)
    document_id = Column(Integer, ForeignKey("knowledge_docs.id"), nullable=False, index=True)
    number = Column(Integer, nullable=False)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    category = Column(String(50), nullable=False, default="")
    keywords = Column(JSON, nullable=True)
    source = Column(String(500), nullable=False, default="")
    status = Column(String(20), nullable=False, default="draft")
    publish_requested = Column(Boolean, nullable=False, default=False)
    embedding_fingerprint = Column(String(64), nullable=True)
    embedding_metadata = Column(JSON, nullable=True)
    chunk_policy = Column(String(40), nullable=False, default="bounded-source-v1")
    error_code = Column(String(80), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_db)


class KnowledgeEpoch(Base):
    __tablename__ = "knowledge_epoch"
    id = Column(Integer, primary_key=True)
    value = Column(Integer, nullable=False, default=0)


class ModelBudgetDay(Base):
    __tablename__="model_budget_days"
    key=Column(String(100),primary_key=True)
    tokens=Column(Integer,nullable=False,default=0)
    cost=Column(Numeric(18,8),nullable=False,default=0)


class ModelCallRecord(Base):
    __tablename__="model_calls"
    id=Column(String(36),primary_key=True)
    user_id=Column(Integer,nullable=False,index=True)
    run_id=Column(String(100),nullable=False,index=True)
    purpose=Column(String(50),nullable=False,index=True)
    provider=Column(String(100),nullable=False)
    model=Column(String(160),nullable=False)
    model_version=Column(String(160),nullable=True)
    price_snapshot=Column(JSON,nullable=True)
    route_version=Column(String(80),nullable=False)
    schema_version=Column(String(100),nullable=False)
    prompt_version=Column(String(80),nullable=False)
    status=Column(String(30),nullable=False,index=True)
    reserved_tokens=Column(Integer,nullable=False)
    input_tokens=Column(Integer,nullable=True)
    output_tokens=Column(Integer,nullable=True)
    reserved_cost=Column(Numeric(18,8),nullable=False)
    cost=Column(Numeric(18,8),nullable=True)
    usage_basis=Column(String(30),nullable=False,default="reserved_estimate")
    latency_ms=Column(Integer,nullable=True)
    error_code=Column(String(80),nullable=True)
    created_at=Column(DateTime,nullable=False,default=utc_now_db)
    expires_at=Column(DateTime,nullable=False,index=True)
    request_hash=Column(String(64),nullable=False)


class ModelConcurrencySlot(Base):
    __tablename__="model_concurrency_slots"
    id=Column(Integer,primary_key=True)
    call_id=Column(String(36),nullable=True)
    expires_at=Column(DateTime,nullable=True)


class AgentTask(Base):
    """跨轮任务进度；不保存可作为下一轮执行许可的旧事实或确认。"""
    __tablename__ = "agent_tasks"
    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    actor_id = Column(Integer, nullable=False)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False, index=True)
    last_run_id = Column(String(36), ForeignKey("chat_runs.id"), nullable=True, index=True)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(30), nullable=False, index=True)
    progress = Column(JSON, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now_db)
    updated_at = Column(DateTime, nullable=False, default=utc_now_db)


class AgentTaskStep(Base):
    """只记录能力执行审计和摘要hash；真实证据仍留在所属Run内。"""
    __tablename__ = "agent_task_steps"
    __table_args__ = (UniqueConstraint("task_id", "run_key", "sequence", name="uq_task_run_step"),)
    id = Column(String(36), primary_key=True)
    task_id = Column(String(36), ForeignKey("agent_tasks.id"), nullable=False, index=True)
    run_key = Column(String(80), nullable=False)
    sequence = Column(Integer, nullable=False)
    action = Column(String(40), nullable=False)
    capability = Column(String(80), nullable=False)
    status = Column(String(20), nullable=False)
    input_digest = Column(String(64), nullable=False)
    error_code = Column(String(80), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_db)


class AgentCapabilityCall(Base):
    """派发前后独立提交的执行账本；不保存凭据、原始工具结果或可复用证据。"""
    __tablename__ = "agent_capability_calls"
    __table_args__ = (UniqueConstraint("run_id", "call_key", name="uq_run_capability_call"),)
    id = Column(String(36), primary_key=True)
    run_id = Column(String(36), ForeignKey("chat_runs.id"), nullable=False, index=True)
    task_id = Column(String(36), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    actor_id = Column(Integer, nullable=False)
    session_id = Column(String(36), ForeignKey("sessions.id"), nullable=False)
    capability = Column(String(80), nullable=False)
    effect = Column(String(10), nullable=False)
    call_key = Column(String(64), nullable=False)
    input_digest = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="running")
    resource_id = Column(String(100), nullable=True)
    result_digest = Column(String(64), nullable=True)
    error_code = Column(String(80), nullable=True)
    task_progress = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_db)
    finished_at = Column(DateTime, nullable=True)
