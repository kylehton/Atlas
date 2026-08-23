from collections.abc import Iterable
from dataclasses import replace
from uuid import UUID

from atlas.integrations.tasks.contracts import TaskChange, TaskItem, TaskRequest
from atlas.shared.field_types import AtlasId


class InMemoryTasks:
    """Store task state in memory with stable IDs for local use and tests."""

    def __init__(self, tasks: Iterable[TaskItem] = ()) -> None:
        self._tasks = {task.id: task for task in tasks}
        self._next_id = 1

    async def list_tasks(self, *, include_completed: bool = False) -> tuple[TaskItem, ...]:
        """Return tasks in creation order, hiding completed tasks by default."""

        return tuple(
            task for task in self._tasks.values() if include_completed or not task.completed
        )

    async def create_task(self, request: TaskRequest) -> TaskItem:
        task = TaskItem(id=self._take_id(), title=request.title, due_at=request.due_at)
        self._tasks[task.id] = task
        return task

    async def update_task(self, task_id: AtlasId, change: TaskChange) -> TaskItem:
        """Apply supplied fields to an existing task."""

        current = self._tasks[task_id]
        task = replace(
            current,
            title=change.title if change.title is not None else current.title,
            due_at=change.due_at if change.due_at is not None else current.due_at,
            completed=change.completed if change.completed is not None else current.completed,
        )
        self._tasks[task_id] = task
        return task

    async def delete_task(self, task_id: AtlasId) -> None:
        del self._tasks[task_id]

    def _take_id(self) -> AtlasId:
        """Return the next deterministic ID without colliding with seeded tasks."""

        while (task_id := UUID(int=self._next_id)) in self._tasks:
            self._next_id += 1
        self._next_id += 1
        return task_id
