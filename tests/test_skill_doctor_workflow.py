from __future__ import annotations

from pathlib import Path


SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def _normalized_skill_text() -> str:
    return " ".join(SKILL_MD.read_text().split())


def test_skill_has_doctor_first_workflow() -> None:
    text = SKILL_MD.read_text()

    assert "browser-cli doctor --json" in text
    assert "before the first browser action" in text
    assert "after credential changes" in text
    assert "when a session/context/action command fails" in text


def test_skill_explains_doctor_status_decisions() -> None:
    text = SKILL_MD.read_text()
    normalized = _normalized_skill_text()

    assert "`ok: true` and `failed: 0`" in text
    assert "`ok: false`" in text
    assert '`status: "fail"`' in text
    assert '`status: "skipped"`' in text
    assert "continue with browser work" in normalized
    assert "stop before creating sessions, inspect `checks`" in normalized
    assert "follow each check's `fix` object" in normalized


def test_skill_limits_skip_api_to_non_proof_checks() -> None:
    normalized = _normalized_skill_text()

    assert "browser-cli doctor --skip-api" in normalized
    assert "only for offline setup checks" in normalized
    assert "Do not treat a skipped API check as proof" in normalized
