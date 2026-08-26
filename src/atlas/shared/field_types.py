from typing import Annotated, Final
from uuid import UUID

from pydantic import StringConstraints

EXTERNAL_ID_MAX_LENGTH: Final = 256
PROVIDER_NAME_MAX_LENGTH: Final = 32
CAPABILITY_NAME_MAX_LENGTH: Final = 32
SHORT_TEXT_MAX_LENGTH: Final = 256
LONG_TEXT_MAX_LENGTH: Final = 2_000

# Atlas-owned records use native UUID values rather than loosely validated ID strings.
type AtlasId = UUID

# Provider IDs are opaque, so enforce only presence and a defensive maximum length.
type ExternalId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=EXTERNAL_ID_MAX_LENGTH),
]

# Integration names are short identifiers shared by persistence and capability boundaries.
type ProviderName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=PROVIDER_NAME_MAX_LENGTH),
]
type CapabilityName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=CAPABILITY_NAME_MAX_LENGTH),
]

# Shared text sizes keep capability models bounded before data reaches a provider or database.
type ShortText = Annotated[str, StringConstraints(max_length=SHORT_TEXT_MAX_LENGTH)]
type LongText = Annotated[str, StringConstraints(max_length=LONG_TEXT_MAX_LENGTH)]
