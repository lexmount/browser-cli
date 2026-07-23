"""Runtime configuration for Lex browser runtime."""

from __future__ import annotations

import os

from lex_browser_runtime.browser.models import BrowserConfigError

DEFAULT_RESEARCH_CONCURRENCY = 5
RESEARCH_CONCURRENCY_ENV = "LEX_BROWSER_RESEARCH_CONCURRENCY"


def get_default_research_concurrency() -> int:
    """Return the configured default research concurrency."""

    raw_value = os.getenv(RESEARCH_CONCURRENCY_ENV)
    if raw_value is None:
        return DEFAULT_RESEARCH_CONCURRENCY

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise BrowserConfigError(
            f"{RESEARCH_CONCURRENCY_ENV} must be an integer"
        ) from exc

    if value <= 0:
        raise BrowserConfigError(f"{RESEARCH_CONCURRENCY_ENV} must be greater than 0")
    return value
