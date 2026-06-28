from __future__ import annotations

from pathlib import Path

from lex_browser_runtime.browser.cases import validate_case_file

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_example_case_files_validate() -> None:
    case_files = sorted((REPO_ROOT / "examples" / "cases").glob("*.yaml"))

    assert case_files
    for case_file in case_files:
        result = validate_case_file(case_file)
        assert result.valid, f"{case_file}: {result.errors}"
        assert result.step_count > 0


def test_agent_playbook_uses_current_context_and_doctor_contracts() -> None:
    text = (REPO_ROOT / "examples" / "agent-playbook.md").read_text()

    assert "browser-cli doctor --json" in text
    assert "browser-cli commands --names-only" in text
    assert "browser-cli commands --workflows-only" in text
    assert "browser-cli commands --workflow setup_and_verify" in text
    assert "browser-cli commands --workflow connect_from_codex_auth" in text
    assert "browser-cli commands --workflow scoped_token_lifecycle" in text
    assert "browser-cli commands --workflow session_recovery" in text
    assert "browser-cli commands --workflow case_file_task" in text
    assert "browser-cli commands --workflow one_off_page_task" in text
    assert "browser-cli commands --workflow persistent_login_state" in text
    assert "browser-cli commands --workflow form_interaction" in text
    assert "browser-cli commands --workflow interactive_targeting" in text
    assert "browser-cli commands --workflow page_diagnostics" in text
    assert "browser-cli commands --group action" in text
    assert "browser-cli commands --group action --names-only" in text
    assert "agent_workflows" in text
    assert "workflow step" in text
    assert "`read` array" in text
    assert "auth availability" in text
    assert "export usability" in text
    assert "context reuse fields" in text
    assert "browser-cli auth login" in text
    assert "browser-cli auth export-env" in text
    assert "scope_check.missing_scopes" in text
    assert "refresh_available" in text
    assert "revoke_available" in text
    assert "handoff.connect_from_codex_url" in text
    assert "verification.doctor_command" in text
    assert "list active sessions" in text
    assert "close stale sessions" in text
    assert "events_path" in text
    assert "artifacts_dir" in text
    assert "Run doctor before the first browser action" in text
    assert "browser_target.exactly_one_of" in text
    assert "browser-cli doctor --smoke-session" in text
    assert "ready_for_browser_actions" in text
    assert "browser_smoke_session.status" in text
    assert "`fix.commands`" in text
    assert "repair_plan.commands" in text
    assert (
        "browser-cli context pick --metadata-json "
        '\'{"purpose":"login"}\' --create-if-missing --dry-run'
    ) in text
    assert "browser-cli context status --context-id <context_id>" in text
    assert "browser-cli session create --context-metadata-json" in text
    assert "`availability` is `locked` or `unavailable`" in text
    assert "selection_summary.recommended_next_action" in text
    assert "selection_summary.decision_reason" in text
    assert "selection_summary.locked_matches" in text
    assert "would_create" in text
    assert "browser-cli action form-snapshot" in text
    assert "browser-cli action fill-label" in text
    assert "browser-cli action interactive-snapshot" in text
    assert "browser-cli action accessibility-snapshot" in text
    assert "browser-cli action console-snapshot" in text
    assert "browser-cli action network-snapshot" in text
    assert "runtime errors" in text
    assert "fetch/XHR issues" in text
    assert "wait-role" in text
    assert "click-role" in text
    assert "click-text" in text
    assert "page-info" in text
    assert "wait-title" in text
    assert "waiting for text to disappear" in text
    assert "active/global shortcut keys" in text
    assert "interactive-snapshot" in text
    assert "context resolve" not in text
    assert "browser-cli direct-url" not in text
