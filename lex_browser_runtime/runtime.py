"""Public SDK facade for Lex browser runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lex_browser_runtime.browser import (
    BrowserBackend,
    BrowserSessionInfo,
    CreateBrowserRequest,
    LexmountBackend,
)
from lex_browser_runtime.assist import RuntimeAssist
from lex_browser_runtime.registry import AdapterRegistry, CapabilityMatch
from lex_browser_runtime.telemetry import RuntimeActionTrace, RuntimeTrace


class LexBrowserRuntime:
    """SDK facade used by agents and benchmark harnesses."""

    def __init__(
        self,
        *,
        browser_backend: BrowserBackend | None = None,
        registry: AdapterRegistry | None = None,
        trace: RuntimeTrace | None = None,
    ) -> None:
        self.browser_backend = browser_backend or LexmountBackend()
        self.registry = registry or AdapterRegistry()
        self.trace = trace or RuntimeTrace()
        self.assist = RuntimeAssist()

    @classmethod
    def from_paths(
        cls,
        *,
        adapters_dir: str | Path | None = None,
        site_hints_path: str | Path | None = None,
        browser_backend: BrowserBackend | None = None,
    ) -> "LexBrowserRuntime":
        """Create a runtime with registry files loaded from disk."""

        return cls(
            browser_backend=browser_backend,
            registry=AdapterRegistry(
                adapters_dir=adapters_dir,
                site_hints_path=site_hints_path,
            ),
        )

    async def create_browser(
        self,
        request: CreateBrowserRequest | None = None,
    ) -> BrowserSessionInfo:
        """Create a browser through the configured backend."""

        session = await self.browser_backend.create_browser(request)
        self.trace.record(
            RuntimeActionTrace(
                kind="create_browser",
                summary=f"{session.backend}:{session.id}",
                metadata={
                    "backend": session.backend,
                    "session_id": session.id,
                    "status": session.status,
                },
            )
        )
        return session

    async def close_browser(self, session_id: str | None = None) -> None:
        """Close a browser through the configured backend."""

        await self.browser_backend.close_browser(session_id)
        self.trace.record(
            RuntimeActionTrace(
                kind="close_browser",
                summary=session_id or "current",
            )
        )

    def match_capabilities(
        self, *, task: str = "", url: str | None = None
    ) -> CapabilityMatch:
        """Match runtime capabilities for the current task and URL."""

        match = self.registry.match(task=task, url=url)
        self.trace.matched_domains = match.domains
        self.trace.matched_adapter_count = len(match.adapters)
        self.trace.matched_site_notice_count = len(match.site_notices)
        self.trace.record(
            RuntimeActionTrace(
                kind="match_capabilities",
                summary=(
                    f"{len(match.adapters)} adapters, "
                    f"{len(match.site_notices)} site notices"
                ),
                metadata={
                    "domains": match.domains,
                    "adapter_count": len(match.adapters),
                    "site_notice_count": len(match.site_notices),
                },
            )
        )
        return match

    def telemetry_snapshot(self) -> dict[str, Any]:
        """Return benchmark-friendly runtime metadata."""

        return self.trace.snapshot()
