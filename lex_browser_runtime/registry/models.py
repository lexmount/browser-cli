"""Pydantic models for runtime capability knowledge."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import regex as safe_regex  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

SAFE_REGEX_TIMEOUT_SECONDS = 0.05

StrategyConfidence = Literal["auto", "suggest", "fallback"]
StrategyActionType = Literal[
    "direct_url",
    "api_call",
    "embedded_json",
    "page_program",
    "stop_rule",
]


class RuntimeStrategyAction(BaseModel):
    """One executable action family in a runtime strategy."""

    type: StrategyActionType
    description: str
    url_template: str | None = None
    method: str | None = None
    fetch_mode: Literal["browser", "http", "auto"] | None = None
    body_template: str | None = None
    max_calls: int | None = None
    output_contract: list[str] = Field(default_factory=list)


class RuntimeStrategy(BaseModel):
    """Machine-readable strategy shared by hints and adapters."""

    name: str
    site_pattern: str | None = None
    trigger: str = ""
    confidence: StrategyConfidence = "suggest"
    actions: list[RuntimeStrategyAction] = Field(default_factory=list)
    observable_validation: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SiteNotice(BaseModel):
    """Hints and runtime strategies for one site."""

    hints: list[str] = Field(default_factory=list)
    strategies: list[RuntimeStrategy] = Field(default_factory=list)


class AdapterEndpoint(BaseModel):
    """A known website API endpoint or page contract."""

    domain: str
    url_pattern: str
    method: str = "GET"
    description: str
    example_url: str
    requires_auth: bool = False
    fetch_mode: Literal["browser", "http", "auto"] = "browser"
    preferred_runtime: Literal["auto", "api", "page_program", "code_extraction"] = (
        "auto"
    )
    strategy: str | None = None
    strategy_notes: list[str] = Field(default_factory=list)
    followup_url_template: str | None = None
    max_followup_calls: int | None = None
    output_contract: list[str] = Field(default_factory=list)
    runtime_strategies: list[RuntimeStrategy] = Field(default_factory=list)
    discovered_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    usage_count: int = 1
    success_count: int = 0
    failure_count: int = 0
    last_used_at: str | None = None
    last_used_successfully_at: str | None = None
    last_used_failed_at: str | None = None
    source_version: int = 0

    def matches(self, url: str) -> bool:
        """Return whether this endpoint pattern matches a concrete URL."""

        try:
            return bool(
                safe_regex.search(
                    self.url_pattern,
                    url,
                    timeout=SAFE_REGEX_TIMEOUT_SECONDS,
                )
            )
        except TimeoutError:
            return False
        except safe_regex.error:
            return self.example_url == url


class CapabilityMatch(BaseModel):
    """Structured capabilities matched for a task or URL."""

    domains: list[str] = Field(default_factory=list)
    adapters: list[AdapterEndpoint] = Field(default_factory=list)
    site_notices: dict[str, SiteNotice] = Field(default_factory=dict)

    @property
    def has_capabilities(self) -> bool:
        """Return whether this match exposes any runtime knowledge."""

        return bool(self.adapters or self.site_notices)
