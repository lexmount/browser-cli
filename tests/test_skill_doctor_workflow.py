from __future__ import annotations

from pathlib import Path


SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def _normalized_skill_text() -> str:
    return " ".join(SKILL_MD.read_text().split())


def test_skill_has_doctor_first_workflow() -> None:
    text = SKILL_MD.read_text()

    assert "browser-cli commands --workflows-only" in text
    assert "browser-cli commands --workflow setup_and_verify" in text
    assert "browser-cli --version" in text
    assert "browser-cli version" in text
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
    assert '`command_catalog` with `status: "warn"`' in text
    assert '`agent_references` with `status: "warn"`' in text
    assert '`agent_examples` with `status: "warn"`' in text
    assert "browser-cli reference get --id action_playbook" in text
    assert "browser-cli example list" in text
    assert "`invalid_examples` and `checked_examples`" in text
    assert "`missing_required_commands`" in text
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
        "follow its `fix` guidance before relying on the full Skill workflow"
        in normalized
    )
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


def test_skill_documents_agent_workflow_discovery() -> None:
    normalized = _normalized_skill_text()

    assert "browser-cli commands --workflows-only" in normalized
    assert "browser-cli commands --workflow setup_and_verify" in normalized
    assert (
        "browser-cli commands --workflow connect_from_codex_site_requirements"
        in normalized
    )
    assert "browser-cli commands --workflow connect_from_codex_auth" in normalized
    assert "browser-cli commands --workflow device_code_auth" in normalized
    assert "browser-cli commands --workflow scoped_token_lifecycle" in normalized
    assert "browser-cli commands --workflow session_recovery" in normalized
    assert "browser-cli commands --workflow one_off_page_task" in normalized
    assert "browser-cli commands --workflow case_file_task" in normalized
    assert "browser-cli commands --workflow persistent_login_state" in normalized
    assert "browser-cli commands --workflow form_interaction" in normalized
    assert "browser-cli commands --workflow interactive_targeting" in normalized
    assert "browser-cli commands --workflow page_diagnostics" in normalized
    assert "browser-cli reference list" in normalized
    assert "browser-cli example list" in normalized
    assert (
        "Run `browser-cli commands --workflows-only` for a compact agent workflow map"
        in normalized
    )
    assert (
        "`agent_references`, `agent_examples`, `agent_entrypoints`, and `agent_workflows`"
        in normalized
    )
    assert (
        "Follow `agent_references` when detailed action guidance is needed"
        in normalized
    )
    assert "browser-cli example get --id page_inspection_case" in normalized
    assert "then follow each workflow step's `read` array first" in normalized
    assert "auth availability, export usability, and context reuse fields" in normalized
