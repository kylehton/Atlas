from datetime import datetime
from typing import Protocol

from pydantic.dataclasses import dataclass

from atlas.shared.field_types import AtlasId, ShortText


@dataclass(frozen=True, slots=True)
class TaskItem:
    id: AtlasId
    title: ShortText
    due_at: datetime | None = None
    completed: bool = False


@dataclass(frozen=True, slots=True)
class TaskRequest:
    title: ShortText
    due_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TaskChange:
    title: ShortText | None = None
    due_at: datetime | None = None
    completed: bool | None = None


class TaskCapability(Protocol):
    async def list_tasks(self, *, include_completed: bool = False) -> tuple[TaskItem, ...]:
        """Return tasks in stable creation order."""

    async def create_task(self, request: TaskRequest) -> TaskItem:
        """Create and return a task."""

    async def update_task(self, task_id: AtlasId, change: TaskChange) -> TaskItem:
        """Update and return a task."""

    async def delete_task(self, task_id: AtlasId) -> None:
        """Delete a task."""
