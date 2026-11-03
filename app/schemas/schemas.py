from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=128)
    nickname: Optional[str] = None
    level: str = "normal"


class UserResponse(BaseModel):
    id: int
    username: str
    nickname: Optional[str]
    level: str
    role: str = "user"
    avatar_url: Optional[str]
    tags: List[str]
    status: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    nickname: Optional[str] = None
    level: Optional[str] = None
    tags: Optional[List[str]] = None


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=8, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=20, max_length=2048)


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = Field(default=None, min_length=20, max_length=2048)


class ChatRequest(BaseModel):
    user_id: int
    session_id: Optional[str] = None
    message: str = Field(..., min_length=1, max_length=5000)
    action_id: Optional[str] = Field(default=None, min_length=1, max_length=36, strict=True)
    action_version: Optional[int] = Field(default=None, gt=0, strict=True)


class ChatResponse(BaseModel):
    message_id: int
    session_id: str
    reply: str
    intent: Optional[str] = None
    response_time_ms: int = 0
    trace_id: Optional[str] = None


class SessionCreate(BaseModel):
    user_id: int


class SessionResponse(BaseModel):
    id: str
    user_id: int
    status: str
    csat_score: Optional[int]
    message_count: int
    last_intent: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: int
    session_id: str
    role: str
    content: str
    token_count: int
    response_time_ms: Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketCreate(BaseModel):
    user_id: int
    category: str
    content: str
    priority: str = "medium"


class TicketResponse(BaseModel):
    id: str
    user_id: int
    category: str
    status: str
    priority: str
    content: str
    assigned_to: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class TicketUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None


class APIResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: Any = None


class ErrorResponse(BaseModel):
    code: int
    message: str
    detail: Optional[str] = None
