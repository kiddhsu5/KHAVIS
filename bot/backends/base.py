"""Abstract ``Backend`` for the dispatch bot."""

from __future__ import annotations

import abc
from dataclasses import dataclass

from ..models import BackendResult, DispatchEnvelope


@dataclass
class HealthResult:
    ok: bool
    detail: str = ""


class Backend(abc.ABC):
    """One fan-out target. Implementations are stateless / cheap to construct."""

    name: str

    @abc.abstractmethod
    async def run(self, envelope: DispatchEnvelope) -> BackendResult:
        """Execute ``envelope.prompt`` and return a structured result."""

    @abc.abstractmethod
    async def health(self) -> HealthResult:
        """Cheap probe — used by ``/status`` and the fallback selector."""
