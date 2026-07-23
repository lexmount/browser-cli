"""Runtime-state handoff decisions shared by browser agents."""

from __future__ import annotations

from typing import Any


def has_answerable_runtime_state(metadata: dict[str, Any] | None) -> bool:
    """Return whether action metadata carries answerable compact runtime state."""

    metadata = metadata or {}
    api_metadata = metadata.get("browser_api_call")
    if isinstance(api_metadata, dict):
        status = api_metadata.get("status")
        if api_metadata.get("adapter_budget_reached") or api_metadata.get(
            "blocked_page"
        ):
            return False
        if not isinstance(status, int) or not 200 <= status < 300:
            return False
        runtime_compact_state = api_metadata.get("runtime_compact_state")
        if runtime_compact_state is False:
            return False
        if runtime_compact_state is True:
            return True
        return False

    page_program = metadata.get("page_program")
    if isinstance(page_program, dict) and page_program.get("ok") is True:
        if metadata.get("runtime_compact_state") is True:
            return True
        extracted = page_program.get("extracted")
        if extracted:
            return True
        results = page_program.get("results")
        if isinstance(results, list):
            for item in results:
                if not isinstance(item, dict) or item.get("ok") is not True:
                    continue
                if item.get("op") in {"extract", "extract_all", "validate"} and (
                    item.get("text") or item.get("value") or item.get("count")
                ):
                    return True

    return bool(metadata.get("structured_extraction"))
