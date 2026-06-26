from __future__ import annotations

from pathlib import Path


SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def _normalized_skill_text() -> str:
    return " ".join(SKILL_MD.read_text().split())


def test_skill_uses_context_resolve_for_persistent_login_state() -> None:
    text = SKILL_MD.read_text()

    assert "browser-cli context resolve --create-if-missing" in text
    assert "--metadata-match-json" in text
    assert "cookies, login state, or storage" in text
    assert "decision.selected_context_id" in text
    assert "`recommended_session_command`" in text


def test_skill_explains_locked_context_handling() -> None:
    normalized = _normalized_skill_text()

    assert "`decision.action` is `close_or_create_context`" in normalized
    assert "do not reuse it for a new read/write session" in normalized
    assert "Close the active session only when it belongs to this task" in normalized
    assert "otherwise create a new context" in normalized


def test_skill_explains_context_resolve_decision_contract() -> None:
    normalized = _normalized_skill_text()

    assert "Parse the top-level `decision` object first" in normalized
    assert "decision.can_start_session" in normalized
    assert "decision.should_create_context" in normalized
    assert "decision.should_close_session" in normalized


def test_skill_explains_metadata_matched_context_reuse() -> None:
    normalized = _normalized_skill_text()

    assert (
        "`--metadata-match-json` describing the site, account, or purpose" in normalized
    )
    assert (
        "`decision.reason` is `metadata_mismatch` or `no_matching_contexts`"
        in normalized
    )
    assert "instead of reusing unrelated login state" in normalized


def test_skill_explains_context_modes_and_deletion_safety() -> None:
    normalized = _normalized_skill_text()

    assert "Use `--context-mode read_write` while logging in" in normalized
    assert "Use `--context-mode read_only` for inspection" in normalized
    assert "Do not delete contexts that may hold user login state" in normalized
