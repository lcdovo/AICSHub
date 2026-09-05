"""数据库仓储层。"""

from .persistence import TicketRepository, HandoffRepository, OutboxRepository

__all__ = ["TicketRepository", "HandoffRepository", "OutboxRepository"]
