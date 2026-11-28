from datetime import datetime
from typing import Literal

from pydantic import BaseModel

NotificationCategory = Literal["approval", "promotion", "job", "refund"]


class NotificationItem(BaseModel):
    id: int
    category: NotificationCategory
    title: str
    content: str
    resource_type: str | None = None
    resource_id: int | None = None
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationItem]
    unread_count: int


class ReadAllResponse(BaseModel):
    updated_count: int
