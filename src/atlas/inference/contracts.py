from typing import Protocol

from pydantic.dataclasses import dataclass

from atlas.shared.field_types import LongText, ShortText


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    task: ShortText
    prompt: LongText


@dataclass(frozen=True, slots=True)
class InferenceResponse:
    text: LongText


class InferenceCapability(Protocol):
    async def complete(self, request: InferenceRequest) -> InferenceResponse:
        """Return a model response for a request."""
