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
        "`wait-selector`",
        "`click`",
        "`type`",
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
    assert "inspect again before trying a different action" in normalized


def test_skill_includes_common_task_recipes() -> None:
    normalized = _normalized_skill_text()

    assert "Common task recipes" in normalized
    assert "Fill and submit a form" in normalized
    assert "`interactive-snapshot`, use `fill-label`" in normalized
    assert "`select-option` or `check`" in normalized
    assert "`click-role --role button --name <text>` or `click-text`" in normalized
    assert "Click a visible control" in normalized
    assert "`click-role`, then `click-text`, then selector `click`" in normalized
    assert "Open menus or keyboard flows" in normalized
    assert "`hover` for menus, `press` for shortcuts" in normalized
    assert "Read page results" in normalized
    assert "`get-text` for a known selector" in normalized
    assert "Capture final evidence" in normalized
    assert "use `screenshot` after the action sequence" in normalized
