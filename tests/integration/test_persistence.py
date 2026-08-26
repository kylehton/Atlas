from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from atlas.db.models import (
    ExternalIdentity,
    Integration,
    User,
    UserPreference,
    Workflow,
)
from atlas.db.repositories import AtlasRepository
from atlas.db.session import session_scope


def test_user_owned_records_are_scoped_to_their_owner(
    postgres_session_factory: sessionmaker[Session],
) -> None:
    provider_suffix = uuid4().hex

    with session_scope(postgres_session_factory) as session:
        repository = AtlasRepository(session)
        owner = repository.add(User(display_name="Owner"))
        other_user = repository.add(User(display_name="Other user"))
        repository.add(UserPreference(user_id=owner.id, timezone="America/Los_Angeles"))
        identity = repository.add(
            ExternalIdentity(
                user_id=owner.id,
                provider="telegram",
                provider_user_id=f"user-{provider_suffix}",
                provider_chat_id=f"chat-{provider_suffix}",
            )
        )
        integration = repository.add(
            Integration(
                user_id=owner.id,
                provider="google",
                capability="calendar",
                account_identifier=f"account-{provider_suffix}",
                encrypted_refresh_token=b"encrypted-test-token",
            )
        )
        workflow = repository.add(
            Workflow(
                user_id=owner.id,
                kind="calendar.create",
                idempotency_key=f"workflow-{provider_suffix}",
            )
        )
        owner_id = owner.id
        other_user_id = other_user.id
        identity_id = identity.id
        integration_id = integration.id
        workflow_id = workflow.id

    with session_scope(postgres_session_factory) as session:
        repository = AtlasRepository(session)
        assert repository.get_preferences(owner_id) is not None
        assert (
            repository.get_external_identity(user_id=owner_id, identity_id=identity_id) is not None
        )
        assert (
            repository.get_external_identity(user_id=other_user_id, identity_id=identity_id) is None
        )
        assert (
            repository.get_integration(user_id=owner_id, integration_id=integration_id) is not None
        )
        assert (
            repository.get_integration(user_id=other_user_id, integration_id=integration_id) is None
        )
        assert repository.get_workflow(user_id=owner_id, workflow_id=workflow_id) is not None
        assert repository.get_workflow(user_id=other_user_id, workflow_id=workflow_id) is None


def test_session_scope_rolls_back_failed_work(
    postgres_session_factory: sessionmaker[Session],
) -> None:
    user_id = uuid4()

    with (
        pytest.raises(RuntimeError, match="test rollback"),
        session_scope(postgres_session_factory) as session,
    ):
        AtlasRepository(session).add(User(id=user_id, display_name="Rolled back"))
        raise RuntimeError("test rollback")

    with session_scope(postgres_session_factory) as session:
        assert AtlasRepository(session).get_user(user_id) is None


def test_json_fields_persist_in_place_mutations(
    postgres_session_factory: sessionmaker[Session],
) -> None:
    with session_scope(postgres_session_factory) as session:
        repository = AtlasRepository(session)
        user = repository.add(User(display_name="Mutable JSON test"))
        integration = repository.add(
            Integration(
                user_id=user.id,
                provider="google",
                capability="gmail",
                account_identifier=f"test-{uuid4().hex}@example.com",
                scopes=["gmail.readonly"],
                token_metadata={"version": 1},
            )
        )
        workflow = repository.add(
            Workflow(
                user_id=user.id,
                kind="gmail.draft",
                state={"step": "drafting"},
                idempotency_key=f"workflow-{uuid4().hex}",
            )
        )
        user_id = user.id
        integration_id = integration.id
        workflow_id = workflow.id

    with session_scope(postgres_session_factory) as session:
        repository = AtlasRepository(session)
        integration = repository.get_integration(
            user_id=user_id,
            integration_id=integration_id,
        )
        workflow = repository.get_workflow(user_id=user_id, workflow_id=workflow_id)
        assert integration is not None
        assert workflow is not None

        integration.scopes.append("gmail.compose")
        integration.token_metadata["version"] = 2
        workflow.state["step"] = "ready"

    with session_scope(postgres_session_factory) as session:
        repository = AtlasRepository(session)
        integration = repository.get_integration(
            user_id=user_id,
            integration_id=integration_id,
        )
        workflow = repository.get_workflow(user_id=user_id, workflow_id=workflow_id)
        assert integration is not None
        assert workflow is not None

        assert integration.scopes == ["gmail.readonly", "gmail.compose"]
        assert integration.token_metadata == {"version": 2}
        assert workflow.state == {"step": "ready"}
