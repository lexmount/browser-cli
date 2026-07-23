"""Runtime telemetry models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


RuntimeActionKind = Literal[
    "match_capabilities",
    "create_browser",
    "close_browser",
    "browser_api_call",
    "page_program",
    "compact_state",
    "fallback",
]


class RuntimeActionTrace(BaseModel):
    """One runtime action trace entry."""

    kind: RuntimeActionKind
    status: Literal["ok", "error", "skipped"] = "ok"
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class RuntimeTrace(BaseModel):
    """Serializable runtime trace for benchmark result metadata."""

    actions: list[RuntimeActionTrace] = Field(default_factory=list)
    matched_domains: list[str] = Field(default_factory=list)
    matched_adapter_count: int = 0
    matched_site_notice_count: int = 0

    @property
    def used_runtime_assist(self) -> bool:
        """Return whether a runtime action beyond matching was used."""

        return any(
            action.kind not in {"match_capabilities", "create_browser", "close_browser"}
            and action.status == "ok"
            for action in self.actions
        )

    def record(self, action: RuntimeActionTrace) -> None:
        """Append an action trace."""

        self.actions.append(action)

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable runtime metadata snapshot."""

        payload = self.model_dump(mode="json")
        payload["used_runtime_assist"] = self.used_runtime_assist
        return payload
