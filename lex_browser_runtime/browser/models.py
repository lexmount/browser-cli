"""Browser session models shared by runtime backends."""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


LexmountBrowserMode = Literal["normal", "light"] | str


class CreateBrowserRequest(BaseModel):
    """Request used to create a browser session."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    api_key: str | None = Field(default=None, alias="lexmount_api_key")
    project_id: str | None = Field(default=None, alias="lexmount_project_id")
    base_url: str | None = Field(default=None, alias="lexmount_base_url")
    region: str | None = Field(default=None, alias="lexmount_region")
    verify_ssl: bool = Field(default=True, alias="lexmount_verify_ssl")
    timeout: float = Field(default=60.0, gt=0, alias="lexmount_timeout")
    browser_mode: LexmountBrowserMode = Field(
        default="normal",
        alias="lexmount_browser_mode",
    )
    context: dict[str, Any] | None = Field(default=None, alias="lexmount_context")
    context_id: str | None = Field(default=None, alias="lexmount_context_id")
    base_context_id: str | None = Field(
        default=None,
        alias="lexmount_base_context_id",
    )
    context_mode: str = Field(default="read_write", alias="lexmount_context_mode")
    delete_context_on_close: bool = Field(
        default=True,
        alias="lexmount_delete_context_on_close",
    )
    extension_ids: list[str] | None = Field(
        default=None,
        alias="lexmount_extension_ids",
    )
    proxy: dict[str, Any] | None = Field(default=None, alias="lexmount_proxy")
    downloads_enabled: bool = Field(
        default=False,
        alias="lexmount_downloads_enabled",
    )
    recording_persistent: bool = Field(
        default=False,
        alias="lexmount_recording_persistent",
    )
    weak_lock: bool = Field(default=False, alias="lexmount_weak_lock")
    async_create: bool = Field(default=True, alias="lexmount_async_create")
    poll_interval_sec: float = Field(
        default=1.0,
        gt=0,
        alias="lexmount_poll_interval_sec",
    )
    poll_timeout_sec: float = Field(
        default=600.0,
        gt=0,
        alias="lexmount_poll_timeout_sec",
    )

    @classmethod
    def from_env(cls) -> "CreateBrowserRequest":
        """Build a request from Lexmount environment variables."""

        return cls.model_validate(
            {
                "api_key": os.getenv("LEXMOUNT_API_KEY"),
                "project_id": os.getenv("LEXMOUNT_PROJECT_ID"),
                "base_url": os.getenv("LEXMOUNT_BASE_URL"),
                "region": os.getenv("LEXMOUNT_REGION"),
                "verify_ssl": os.getenv("LEXMOUNT_VERIFY_SSL", "true").lower()
                not in {"0", "false", "no"},
                "browser_mode": os.getenv("LEXMOUNT_BROWSER_MODE") or "normal",
                "downloads_enabled": os.getenv(
                    "LEXMOUNT_DOWNLOADS_ENABLED",
                    "false",
                ).lower()
                in {"1", "true", "yes"},
                "recording_persistent": os.getenv(
                    "LEXMOUNT_RECORDING_PERSISTENT",
                    "false",
                ).lower()
                in {"1", "true", "yes"},
            }
        )


class BrowserSessionInfo(BaseModel):
    """Normalized browser session descriptor returned by backends."""

    id: str
    status: str
    cdp_url: str
    browser_type: str = "chromium"
    inspect_url: str | None = None
    created_at: str | None = None
    backend: str = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrowserRuntimeError(Exception):
    """Raised when a browser runtime operation fails."""


class BrowserAuthError(BrowserRuntimeError):
    """Raised when browser backend authentication fails."""


class BrowserConfigError(BrowserRuntimeError):
    """Raised when browser backend configuration is invalid."""


class BrowserParallelLimitError(BrowserRuntimeError):
    """Raised when the Lexmount active browser/session quota is exhausted."""
