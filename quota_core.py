"""Provider-neutral quota model used by adapters and the Qt presentation layer."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class QuotaWindow:
    identifier: str
    used_percent: float
    duration_minutes: float | None = None
    resets_at: float | None = None

    @property
    def remaining_percent(self):
        return 100 - self.used_percent


class QuotaSource(Protocol):
    """A source emits server/file supplied values and owns its freshness policy."""
    stale_after: int | None

    def start(self) -> None: ...
    def refresh(self) -> None: ...
    def stop(self) -> None: ...
