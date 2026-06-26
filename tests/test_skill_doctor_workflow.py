from __future__ import annotations

from pathlib import Path


SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def _normalized_skill_text() -> str:
    return " ".join(SKILL_MD.read_text().split())


def test_skill_has_doctor_first_workflow() -> None:
    text = SKILL_MD.read_text()

    assert "browser-cli doctor --json" in text
    assert "browser-cli doctor --smoke-session" in text
    assert "before the first browser action" in text
    assert "after credential changes" in text
    assert "when a session/context/action command fails" in text


def test_skill_explains_doctor_status_decisions() -> None:
    text = SKILL_MD.read_text()
    normalized = _normalized_skill_text()

    assert "`ok: true` and `failed: 0`" in text
    assert "`ready_for_browser_actions: true`" in text
    assert '`browser_smoke_session` with `status: "pass"`' in text
    assert '`browser_smoke_session` with `status: "fail"`' in text
    assert "`repair_plan`" in text
    assert "`warnings > 0`" in text
    assert "`ok: false`" in text
    assert '`status: "warn"`' in text
    assert '`status: "fail"`' in text
    assert '`status: "skipped"`' in text
    assert "continue with browser work" in normalized
    assert "browser sessions/actions can be attempted" in normalized
    assert "a temporary browser session was created and closed" in normalized
    assert "manual `session close` command" in normalized
    assert (
        "prefer its aggregated `commands`, `env`, `guidance`, and `fixes`" in normalized
    )
    assert "reporting warning check names" in normalized
    assert "stop before creating sessions, inspect `checks`" in normalized
    assert "follow each check's `fix` object" in normalized


def test_skill_limits_skip_api_to_non_proof_checks() -> None:
    normalized = _normalized_skill_text()

    assert "browser-cli doctor --skip-api" in normalized
    assert "only for offline setup checks" in normalized
    assert "Do not treat a skipped API check as proof" in normalized
    assert (
        "Use `browser-cli doctor --smoke-session` only when you need proof"
        in normalized
    )
