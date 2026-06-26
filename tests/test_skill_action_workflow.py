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
