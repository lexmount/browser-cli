from __future__ import annotations

from pathlib import Path


SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def _normalized_skill_text() -> str:
    return " ".join(SKILL_MD.read_text().split())


def test_skill_uses_context_resolve_for_persistent_login_state() -> None:
    text = SKILL_MD.read_text()

    assert "browser-cli context resolve --create-if-missing" in text
    assert "cookies, login state, or storage" in text
    assert "`context_id` or `recommended_session_command`" in text


def test_skill_explains_locked_context_handling() -> None:
    normalized = _normalized_skill_text()

    assert "`reuse.reason` is `context_locked`" in normalized
    assert "do not reuse it for a new read/write session" in normalized
    assert "Close the active session only when it belongs to this task" in normalized
    assert "otherwise create a new context" in normalized


def test_skill_explains_context_modes_and_deletion_safety() -> None:
    normalized = _normalized_skill_text()

    assert "Use `--context-mode read_write` while logging in" in normalized
    assert "Use `--context-mode read_only` for inspection" in normalized
    assert "Do not delete contexts that may hold user login state" in normalized
