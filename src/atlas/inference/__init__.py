"""Language-model inference capability."""

from atlas.inference.contracts import InferenceCapability, InferenceRequest, InferenceResponse
from atlas.inference.memory import InMemoryInference

__all__ = [
    "InferenceCapability",
    "InferenceRequest",
    "InferenceResponse",
    "InMemoryInference",
]
