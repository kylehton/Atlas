"""Database models and their shared metadata."""

from atlas.db.base import Base
from atlas.db.models.integration import Integration, IntegrationStatus
from atlas.db.models.onboarding import SettingsBrowserSession, SettingsLoginRequest
from atlas.db.models.telegram import ProcessedTelegramUpdate
from atlas.db.models.user import ExternalIdentity, User, UserPreference
from atlas.db.models.workflow import Workflow, WorkflowStatus

metadata = Base.metadata

__all__ = [
    "ExternalIdentity",
    "Integration",
    "IntegrationStatus",
    "ProcessedTelegramUpdate",
    "SettingsBrowserSession",
    "SettingsLoginRequest",
    "User",
    "UserPreference",
    "Workflow",
    "WorkflowStatus",
    "metadata",
]
