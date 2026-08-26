from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from atlas.db.base import Base
from atlas.db.models import ExternalIdentity, Integration, User, UserPreference, Workflow


class AtlasRepository:
    """Persist core records and scope every user-owned lookup to its owner."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add[T: Base](self, record: T) -> T:
        """Stage a record and flush so generated IDs are immediately available."""

        self._session.add(record)
        self._session.flush()
        return record

    def get_user(self, user_id: UUID) -> User | None:
        return self._session.get(User, user_id)

    def get_preferences(self, user_id: UUID) -> UserPreference | None:
        return self._session.get(UserPreference, user_id)

    def get_external_identity(
        self,
        *,
        user_id: UUID,
        identity_id: UUID,
    ) -> ExternalIdentity | None:
        return self._session.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.id == identity_id,
                ExternalIdentity.user_id == user_id,
            )
        )

    def get_integration(self, *, user_id: UUID, integration_id: UUID) -> Integration | None:
        return self._session.scalar(
            select(Integration).where(
                Integration.id == integration_id,
                Integration.user_id == user_id,
            )
        )

    def get_workflow(self, *, user_id: UUID, workflow_id: UUID) -> Workflow | None:
        return self._session.scalar(
            select(Workflow).where(
                Workflow.id == workflow_id,
                Workflow.user_id == user_id,
            )
        )
