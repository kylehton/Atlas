from collections.abc import Mapping

from atlas.inference.contracts import InferenceRequest, InferenceResponse


class InMemoryInference:
    """Return configured responses without contacting a language model."""

    def __init__(
        self, responses: Mapping[str, str], *, default_response: str | None = None
    ) -> None:
        self._responses = dict(responses)
        self._default_response = default_response
        self.requests: list[InferenceRequest] = []

    async def complete(self, request: InferenceRequest) -> InferenceResponse:
        """Record the request and return its configured response or fail if none exists."""

        self.requests.append(request)
        text = self._responses.get(request.prompt, self._default_response)
        if text is None:
            raise LookupError(f"no in-memory inference response for task {request.task!r}")
        return InferenceResponse(text=text)
