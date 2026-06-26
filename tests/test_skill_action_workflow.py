from __future__ import annotations

from pathlib import Path


SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def _normalized_skill_text() -> str:
    return " ".join(SKILL_MD.read_text().split())


def test_skill_prefers_semantic_actions_before_eval() -> None:
    normalized = _normalized_skill_text()

    assert "Inspect with `snapshot`, then `interactive-snapshot`" in normalized
    assert "Prefer semantic actions" in normalized
    assert "`click-role` for known roles/names" in normalized
    assert "`click-text` for visible text" in normalized
    assert "`fill-label` for labeled form fields" in normalized
    assert (
        "Use `eval` only for page-local work not covered by a first-class action"
        in normalized
    )


def test_skill_lists_selector_and_input_actions() -> None:
    normalized = _normalized_skill_text()

    for action in (
        "`exists`",
        "`get-text`",
        "`count`",
        "`query`",
        "`get-attribute`",
        "`wait-selector`",
        "`wait-load-state`",
        "`wait-network-idle`",
        "`wait-text`",
        "`click`",
        "`type`",
        "`focus`",
        "`get-value`",
        "`wait-value`",
        "`blur`",
        "`storage-get`",
        "`storage-set`",
        "`storage-remove`",
        "`storage-clear`",
        "`wait-storage`",
        "`cookie-get`",
        "`cookie-set`",
        "`cookie-delete`",
        "`cookie-clear`",
        "`wait-cookie`",
        "`clear`",
        "`submit`",
        "`select-option`",
        "`check`",
        "`uncheck`",
    ):
        assert action in normalized


def test_skill_reinspects_after_failed_structured_results() -> None:
    normalized = _normalized_skill_text()

    assert "`result.found`" in normalized
    assert "`result.exists`" in normalized
    assert "`result.clicked`" in normalized
    assert "`result.filled`" in normalized
    assert "`result.value`" in normalized
    assert "`result.readable`" in normalized
    assert "`removed`" in normalized
    assert "`deleted`" in normalized
    assert "`cleared_count`" in normalized
    assert "`network_idle`" in normalized
    assert "`quiet_ms`" in normalized
    assert "`requested_value`" in normalized
    assert "inspect again before trying a different action" in normalized


def test_skill_includes_common_task_recipes() -> None:
    normalized = _normalized_skill_text()

    assert "Common task recipes" in normalized
    assert "Fill and submit a form" in normalized
    assert "`interactive-snapshot`, use `fill-label`" in normalized
    assert "`clear` before replacement text" in normalized
    assert "`get-value` or `wait-value` to confirm form state" in normalized
    assert "use `blur` for focus-driven validation" in normalized
    assert "`select-option` or `check`" in normalized
    assert "then use `submit`" in normalized
    assert "`click-role --role button --name <text>` or `click-text`" in normalized
    assert "Click a visible control" in normalized
    assert "`click-role`, then `click-text`, then selector `click`" in normalized
    assert "Navigate page history or async refresh" in normalized
    assert "use `reload`, `go-back`, or `go-forward`" in normalized
    assert (
        "confirm with `wait-url`, `wait-load-state`, `wait-network-idle`" in normalized
    )
    assert "Debug selectors" in normalized
    assert "use `count`, `query`, and `get-attribute` before `eval`" in normalized
    assert "Open menus or keyboard flows" in normalized
    assert "use `focus`, `hover` for menus, `press` for shortcuts" in normalized
    assert "Read page results" in normalized
    assert "`get-text` for a known selector" in normalized
    assert "use `wait-text` before reading dynamic results" in normalized
    assert "Adjust browser state" in normalized
    assert "use `storage-get` for local/session storage" in normalized
    assert "`storage-clear --prefix <prefix>` for targeted cleanup" in normalized
    assert "use `wait-storage` when the page updates keys asynchronously" in normalized
    assert (
        "Use `cookie-get`, `cookie-set`, `cookie-delete`, or `cookie-clear`"
        in normalized
    )
    assert "`wait-cookie` when cookie changes are async" in normalized
    assert "document.cookie-visible cookies" in normalized
    assert "HttpOnly cookies" in normalized
    assert "Capture final evidence" in normalized
    assert "use `screenshot` after the action sequence" in normalized
