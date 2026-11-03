from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Enum, Float,
    Index, ForeignKey, JSON
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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

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
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
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
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    session = relationship("Session", back_populates="messages")


class Order(Base):
    """持久化订单投影，供客服演示中的订单查询工具使用。"""

    __tablename__ = "orders"

    order_id = Column(String(32), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(50), nullable=False)
    status_code = Column(String(50), nullable=False)
    items = Column(JSON, nullable=False, default=list)
    total_amount = Column(Float, nullable=False, default=0)
    shipping = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    pay_method = Column(String(50), nullable=True)
    address = Column(String(500), nullable=True)


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
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="tickets")


class HandoffStatus(str, enum.Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    assigned_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    idempotency_key = Column(String(128), nullable=True, unique=True, index=True)


class NotificationStatus(str, enum.Enum):
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
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    idempotency_key = Column(String(128), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    sent_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


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
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

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
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


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
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
