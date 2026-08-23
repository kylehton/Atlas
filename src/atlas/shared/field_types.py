from typing import Annotated
from uuid import UUID

from pydantic import StringConstraints

# Atlas-owned records use native UUID values rather than loosely validated ID strings.
type AtlasId = UUID

# Provider IDs are opaque, so enforce only presence and a defensive maximum length.
type ExternalId = Annotated[str, StringConstraints(min_length=1, max_length=256)]

# Shared text sizes keep capability models bounded before data reaches a provider or database.
type ShortText = Annotated[str, StringConstraints(max_length=256)]
type LongText = Annotated[str, StringConstraints(max_length=2_000)]
