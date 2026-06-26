from __future__ import annotations

from pathlib import Path


SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def _normalized_skill_text() -> str:
    return " ".join(SKILL_MD.read_text().split())


def test_skill_prefers_plain_doctor_for_routine_checks() -> None:
    normalized = _normalized_skill_text()

    assert "Prefer plain `browser-cli doctor --json`" in normalized
    assert "routine readiness checks" in normalized
    assert "browser-cli doctor --smoke-session --json" in normalized


def test_skill_uses_doctor_decision_before_creating_sessions() -> None:
    normalized = _normalized_skill_text()

    assert "`decision.ready_for_browser_work` is not `true`" in normalized
    assert "`decision.recommended_action`" in normalized
    assert "`decision.next_command`" in normalized
    assert "`workflow.primary_command`" in normalized
    assert (
        "only continue to browser work when `workflow.can_start_browser_work` is true"
        in normalized
    )
    assert "before creating sessions" in normalized


def test_skill_limits_smoke_session_to_lifecycle_verification() -> None:
    normalized = _normalized_skill_text()

    assert "only to prove session create and close permissions" in normalized
    assert "quota, and project access" in normalized
    assert "Do not run smoke checks in tight loops or before every action" in normalized
    assert "consume browser session capacity" in normalized


def test_skill_handles_smoke_session_cleanup_and_browser_mode() -> None:
    normalized = _normalized_skill_text()

    assert "inspect `session_smoke` and follow `next_steps`" in normalized
    assert "temporary session that needs cleanup" in normalized
    assert "Use `--smoke-browser-mode light`" in normalized
