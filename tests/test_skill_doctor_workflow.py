from __future__ import annotations

from pathlib import Path


SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def test_skill_uses_doctor_when_readiness_may_have_changed() -> None:
    text = SKILL_MD.read_text()

    assert "browser-cli doctor --json" in text
    assert "only when there is no recent successful readiness signal" in text
    assert "after credentials change" in text
    assert "after an unclear session failure" in text
    assert "ready_for_browser_actions" in text
    assert "failed_checks" in text
    assert "warning_checks" in text
    assert "repair_plan" in text


def test_skill_exposes_progressive_workflow_and_reference_discovery() -> None:
    text = SKILL_MD.read_text()

    assert "Inspect packaged guidance only when it changes the next action" in text
    assert "browser-cli commands --workflows-only" in text
    assert "browser-cli commands --workflow first_browser_task" in text
    assert "browser-cli commands --workflow agent_browser_primitives" in text
    assert "browser-cli reference get --id quickstart" in text
    assert "browser-cli reference get --id ace_page_api" in text


def test_skill_uses_local_pkce_and_credential_lifecycle_helpers() -> None:
    text = SKILL_MD.read_text()

    assert "Prefer local loopback PKCE" in text
    assert "browser-cli auth login --open" in text
    for helper in (
        "auth scopes",
        "token-info",
        "refresh",
        "logout",
        "clear-credentials",
        "connect-requirements",
        "export-env",
    ):
        assert helper in text
