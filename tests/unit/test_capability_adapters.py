from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from atlas.inference import (
    InferenceCapability,
    InferenceRequest,
    InMemoryInference,
)
from atlas.integrations.calendar import (
    CalendarCapability,
    CalendarEventChange,
    CalendarEventRequest,
    InMemoryCalendar,
)
from atlas.integrations.gmail import (
    EmailDraftRequest,
    EmailMessage,
    EmailMutation,
    GmailCapability,
    InMemoryGmail,
)
from atlas.integrations.tasks import (
    InMemoryTasks,
    TaskCapability,
    TaskChange,
    TaskRequest,
)
from atlas.notifications import (
    InMemoryNotifications,
    NotificationCapability,
    NotificationRequest,
)
from atlas.shared import CapabilityName, ProviderName
from atlas.telegram import InMemoryTelegram, TelegramButton, TelegramCapability


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_calendar_request_crosses_capability_boundary() -> None:
    calendar: CalendarCapability = InMemoryCalendar()
    starts_at = datetime(2026, 8, 24, 9, tzinfo=UTC)
    created = await calendar.create_event(
        CalendarEventRequest(
            title="Dentist",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
        )
    )

    events = await calendar.list_events(
        starts_at=starts_at - timedelta(days=1),
        ends_at=starts_at + timedelta(days=1),
    )
    updated = await calendar.update_event(
        created.id,
        CalendarEventChange(title="Dentist appointment"),
    )

    assert [event.id for event in events] == [created.id]
    assert updated.title == "Dentist appointment"

    await calendar.delete_event(created.id)
    assert (
        await calendar.list_events(
            starts_at=starts_at - timedelta(days=1),
            ends_at=starts_at + timedelta(days=1),
        )
        == ()
    )


@pytest.mark.anyio
async def test_gmail_and_task_adapters_keep_deterministic_state() -> None:
    received_at = datetime(2026, 8, 23, 8, tzinfo=UTC)
    gmail: GmailCapability = InMemoryGmail(
        [
            EmailMessage(
                id="message-1",
                thread_id="thread-1",
                sender="alex@example.com",
                subject="Project update",
                snippet="The project is ready.",
                received_at=received_at,
                labels=frozenset({"INBOX"}),
            )
        ]
    )
    tasks: TaskCapability = InMemoryTasks()

    messages = await gmail.list_messages(query="project")
    archived = await gmail.modify_message(
        messages[0].id,
        EmailMutation(is_read=True, archive=True),
    )
    draft = await gmail.create_draft(
        EmailDraftRequest(
            recipients=("alex@example.com",),
            subject="Re: Project update",
            body="Thanks for the update.",
            thread_id="thread-1",
        )
    )
    task = await tasks.create_task(TaskRequest(title="Review project update"))
    completed = await tasks.update_task(task.id, TaskChange(completed=True))

    assert archived.is_read is True
    assert "INBOX" not in archived.labels
    assert draft.id == "draft-1"
    assert completed.completed is True
    assert await tasks.list_tasks() == ()
    assert await tasks.list_tasks(include_completed=True) == (completed,)


@pytest.mark.anyio
async def test_message_notification_and_inference_adapters_record_results() -> None:
    telegram_adapter = InMemoryTelegram()
    telegram: TelegramCapability = telegram_adapter
    notification_adapter = InMemoryNotifications()
    notifications: NotificationCapability = notification_adapter
    inference_adapter = InMemoryInference({"calendar intent": "calendar.list"})
    inference: InferenceCapability = inference_adapter

    response = await inference.complete(InferenceRequest(task="intent", prompt="calendar intent"))
    message = await telegram.send_message(
        chat_id=1001,
        text="Create this event?",
        buttons=(TelegramButton(text="Confirm", callback_data="confirm:event-1"),),
    )
    delivery = await notifications.deliver(
        NotificationRequest(user_id=UUID(int=1001), text="Event starts soon", priority="urgent")
    )

    assert response.text == "calendar.list"
    assert inference_adapter.requests[0].task == "intent"
    assert telegram_adapter.sent_messages == [message]
    assert notification_adapter.deliveries == [delivery]


def test_shared_field_types_enforce_runtime_limits() -> None:
    starts_at = datetime(2026, 8, 24, 9, tzinfo=UTC)

    with pytest.raises(ValidationError):
        CalendarEventRequest(
            title="x" * 257,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
        )

    with pytest.raises(ValidationError):
        NotificationRequest(user_id="not-a-uuid", text="Example")

    with pytest.raises(ValidationError):
        NotificationRequest(user_id=UUID(int=1001), text="x" * 2_001)

    with pytest.raises(ValidationError):
        TypeAdapter(ProviderName).validate_python("")

    with pytest.raises(ValidationError):
        TypeAdapter(CapabilityName).validate_python("x" * 33)
