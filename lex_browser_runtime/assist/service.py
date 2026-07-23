"""Agent-agnostic runtime assist facade."""

from __future__ import annotations

from typing import Any

from lex_browser_runtime.assist.api import (
    ApiObservation,
    YouTubeJsonWriter,
    YouTubePageFetcher,
    YouTubeSearchBatchAction,
    build_api_observation,
    compact_api_data,
    collect_youtube_search_results,
    fetch_api_via_http,
    normalize_request_body,
    prune_api_data,
    summarize_api_data,
)
from lex_browser_runtime.assist.douban import (
    RuntimeCompletion,
    build_douban_completion_async,
    detect_douban_batch_completion_intent,
)
from lex_browser_runtime.assist.state import has_answerable_runtime_state


class RuntimeAssist:
    """Reusable runtime capability layer for browser agents."""

    async def fetch_api_via_http(
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        body: str | None,
    ) -> dict[str, Any]:
        """Fetch an adapter-approved public API outside browser context."""

        return await fetch_api_via_http(url, method, headers, body)

    def normalize_request_body(self, url: str, body: str | None) -> str | None:
        """Normalize known runtime API request bodies."""

        return normalize_request_body(url, body)

    def compact_api_data(self, url: str, data: Any) -> Any:
        """Return compact known API data when a contract matches."""

        return compact_api_data(url, data)

    def prune_api_data(self, data: Any) -> Any:
        """Prune data for display without losing high-value fields."""

        return prune_api_data(data)

    def summarize_api_data(self, url: str, data: Any, method: str = "GET") -> str:
        """Summarize compact API data for agent memory."""

        return summarize_api_data(url, data, method=method)

    def build_api_observation(
        self,
        url: str,
        data: Any,
        *,
        method: str = "GET",
        ok: bool = True,
        status: int | None = None,
    ) -> ApiObservation:
        """Build a compact observation for LLM handoff."""

        return build_api_observation(url, data, method=method, ok=ok, status=status)

    async def collect_youtube_search_results(
        self,
        params: YouTubeSearchBatchAction,
        fetch_page: YouTubePageFetcher,
        write_json_file: YouTubeJsonWriter | None = None,
    ) -> dict[str, Any]:
        """Collect YouTube search pages with caller-provided browser/file callbacks."""

        return await collect_youtube_search_results(params, fetch_page, write_json_file)

    def has_answerable_runtime_state(self, metadata: dict[str, Any] | None) -> bool:
        """Return whether action metadata can skip a fresh DOM observe."""

        return has_answerable_runtime_state(metadata)

    def detect_completion_intent(self, task: str) -> str | None:
        """Return supported deterministic completion category for a task."""

        return detect_douban_batch_completion_intent(task)

    async def build_completion(self, task: str) -> RuntimeCompletion | None:
        """Build a deterministic completion payload when supported."""

        return await build_douban_completion_async(task)
