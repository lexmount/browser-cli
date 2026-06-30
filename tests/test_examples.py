from __future__ import annotations

from pathlib import Path

from browser_cli.cli import validate_browser_cli_case_file as validate_case_file

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGED_EXAMPLES = REPO_ROOT / "browser_cli" / "agent_examples"


def test_example_case_files_validate() -> None:
    case_files = sorted((REPO_ROOT / "examples" / "cases").glob("*.yaml"))

    assert case_files
    for case_file in case_files:
        result = validate_case_file(case_file)
        assert result.valid, f"{case_file}: {result.errors}"
        assert result.step_count > 0


def test_packaged_examples_match_repo_examples() -> None:
    assert (PACKAGED_EXAMPLES / "agent-playbook.md").read_text() == (
        REPO_ROOT / "examples" / "agent-playbook.md"
    ).read_text()
    assert (PACKAGED_EXAMPLES / "setup-verification-playbook.md").read_text() == (
        REPO_ROOT / "examples" / "setup-verification-playbook.md"
    ).read_text()
    for case_file in sorted((REPO_ROOT / "examples" / "cases").glob("*.yaml")):
        packaged_case = PACKAGED_EXAMPLES / "cases" / case_file.name
        assert packaged_case.read_text() == case_file.read_text()


def test_form_fill_case_uses_semantic_case_actions() -> None:
    text = (REPO_ROOT / "examples" / "cases" / "form-fill.yaml").read_text()

    assert "action: fill-label" in text
    assert "action: click-role" in text
    assert "action: wait-text" in text
    assert "action: get-value-role" in text
    assert "action: type" not in text


def test_interactive_targeting_case_uses_semantic_targets() -> None:
    text = (
        REPO_ROOT / "examples" / "cases" / "interactive-targeting.yaml"
    ).read_text()

    assert "action: interactive-snapshot" in text
    assert "action: accessibility-snapshot" in text
    assert "action: click-role" in text
    assert "action: wait-text" in text
    assert "action: get-text" in text
    assert "action: click\n" not in text


def test_page_diagnostics_case_uses_console_and_network_actions() -> None:
    text = (REPO_ROOT / "examples" / "cases" / "page-diagnostics.yaml").read_text()

    assert "name: page-diagnostics" in text
    assert "action: console-snapshot" in text
    assert "action: network-snapshot" in text
    assert "install_only: true" in text
    assert "action: wait-console" in text
    assert "source: console" in text
    assert "level: error" in text
    assert "action: wait-network" in text
    assert "source: fetch" in text
    assert "diagnostic-network-ok" in text
    assert "action: eval" in text
    assert "action: screenshot" in text
    assert "action: click\n" not in text


def test_agent_playbook_uses_current_context_and_doctor_contracts() -> None:
    text = (REPO_ROOT / "examples" / "agent-playbook.md").read_text()

    assert "browser-cli doctor --json" in text
    assert "browser-cli commands --names-only" in text
    assert "browser-cli commands --workflows-only" in text
    assert "browser-cli commands --workflow setup_and_verify" in text
    assert (
        "browser-cli commands --workflow connect_from_codex_site_requirements" in text
    )
    assert "browser-cli commands --workflow connect_from_codex_auth" in text
    assert "browser-cli commands --workflow device_code_auth" in text
    assert "browser-cli commands --workflow scoped_token_lifecycle" in text
    assert "browser-cli commands --workflow session_recovery" in text
    assert "browser-cli commands --workflow first_browser_task" in text
    assert "browser-cli commands --workflow agent_browser_primitives" in text
    assert "browser-cli commands --workflow case_file_task" in text
    assert "browser-cli case schema" in text
    assert "browser-cli case schema --action fill-label" in text
    assert "browser-cli example get --id form_fill_case --metadata-only" in text
    assert "browser-cli case scaffold --template page-inspection" in text
    assert "browser-cli case scaffold --template form-fill" in text
    assert "browser-cli case scaffold --template interactive-targeting" in text
    assert "browser-cli case scaffold --template page-diagnostics" in text
    assert "browser-cli commands --workflow first_browser_task" in text
    assert "browser-cli commands --workflow agent_browser_primitives" in text
    assert "browser-cli action observe --session-id <session_id>" in text
    assert "browser-cli action extract --session-id <session_id>" in text
    assert "browser-cli commands --workflow one_off_page_task" in text
    assert "browser-cli commands --workflow persistent_login_state" in text
    assert "browser-cli commands --workflow form_interaction" in text
    assert "browser-cli commands --workflow interactive_targeting" in text
    assert "browser-cli commands --workflow content_extraction" in text
    assert "browser-cli commands --workflow browser_state_management" in text
    assert "browser-cli commands --workflow file_upload" in text
    assert "browser-cli commands --workflow dialog_frame_handling" in text
    assert "browser-cli commands --workflow navigation_flow" in text
    assert "browser-cli commands --workflow link_navigation" in text
    assert "browser-cli commands --workflow visual_capture" in text
    assert "browser-cli commands --workflow semantic_waits" in text
    assert "browser-cli commands --workflow menu_keyboard_flow" in text
    assert "browser-cli commands --workflow mouse_interaction" in text
    assert "browser-cli commands --workflow state_waits" in text
    assert "browser-cli commands --workflow page_diagnostics" in text
    assert "runtime_auth.usable" in text
    assert "runtime_auth.bearer_runtime.required_support" in text
    assert "browser-cli action guide --names-only" in text
    assert "browser-cli action guide --task form_interaction" in text
    assert "browser-cli action guide --task interactive_targeting" in text
    assert "browser-cli action guide --task content_extraction" in text
    assert "browser-cli action guide --task browser_state_management" in text
    assert "browser-cli action guide --task file_upload" in text
    assert "browser-cli action guide --task dialog_frame_handling" in text
    assert "browser-cli action guide --task navigation_flow" in text
    assert "browser-cli action guide --task link_navigation" in text
    assert "browser-cli action guide --task visual_capture" in text
    assert "browser-cli action guide --task semantic_waits" in text
    assert "browser-cli action guide --task menu_keyboard_flow" in text
    assert "browser-cli action guide --task mouse_interaction" in text
    assert "browser-cli action guide --task state_waits" in text
    assert "browser-cli action guide --task page_diagnostics" in text
    assert "browser-cli reference list" in text
    assert "browser-cli reference get --id usable_status --metadata-only" in text
    assert "browser-cli reference get --id usable_status" in text
    assert "browser-cli example list" in text
    assert "browser-cli example get --id page_inspection_case --metadata-only" in text
    assert (
        "browser-cli example get --id interactive_targeting_case --metadata-only"
        in text
    )
    assert "browser-cli example get --id page_diagnostics_case --metadata-only" in text
    assert "browser-cli commands --group action" in text
    assert "browser-cli commands --group action --names-only" in text
    assert "action guide --task <task>" in text
    assert "agent_workflows" in text
    assert "workflow step" in text
    assert "`read` array" in text
    assert "auth availability" in text
    assert "export usability" in text
    assert "context reuse fields" in text
    assert "browser-cli auth login" in text
    assert "browser-cli auth connect-requirements" in text
    assert "browser-cli auth connect-requirements --checklist" in text
    assert "browser-cli auth export-env" in text
    assert "safe_to_paste_in_chat" in text
    assert "local_shell_only" in text
    assert "contains_secret_values" in text
    assert "contains_secret_placeholders" in text
    assert "setup_block" in text
    assert "scope_check.missing_scopes" in text
    assert "refresh_available" in text
    assert "revoke_available" in text
    assert "handoff.connect_from_codex_url" in text
    assert "verification.doctor_command" in text
    assert "browser-cli auth login --device-code" in text
    assert "fallback_handoff" in text
    assert "device_code.verification_uri_complete" in text
    assert "credentials" in text
    assert "required_api_contract" in text
    assert "required_token_lifecycle" in text
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
        '\'{"purpose":"login"}\' --selection newest --create-if-missing --dry-run'
    ) in text
    assert "browser-cli context status --context-id <context_id>" in text


def test_setup_verification_playbook_guides_safe_setup_before_actions() -> None:
    text = (REPO_ROOT / "examples" / "setup-verification-playbook.md").read_text()

    assert "Inspect The Installed CLI" in text
    assert "Check Auth Without Revealing Secrets" in text
    assert "Guide Manual Env Setup" in text
    assert "Handle Device-Code Requests" in text
    assert "Verify Readiness" in text
    assert "browser-cli reference get --id quickstart" in text
    assert "browser-cli reference get --id usable_status" in text
    assert "browser-cli auth status" in text
    assert "browser-cli auth login" in text
    assert "browser-cli auth export-env" in text
    assert "browser-cli auth login --device-code" in text
    assert "browser-cli doctor --json" in text
    assert "browser-cli doctor --smoke-session" in text
    assert "repair_plan.commands" in text
    assert "ready_for_browser_actions=true" in text
    assert "browser-cli session close --session-id <session_id>" in text
    assert "browser-cli session create --context-metadata-json" in text
    assert "`availability` is `locked` or `unavailable`" in text
    assert "selection_summary.recommended_next_action" in text
    assert "selection_summary.decision_reason" in text
    assert "selection_summary.locked_matches" in text
    assert "metadata_diagnostics.missing_keys" in text
    assert "values are intentionally redacted" in text
    assert "metadata_diagnostics.metadata_source" in text
    assert "local_registry" in text
    assert "not API keys, passwords, or session secrets" in text
    assert "would_create" in text
    assert "browser-cli action form-snapshot" in text
    assert "browser-cli action fill-label" in text
    assert "browser-cli action fill-role" in text
    assert "browser-cli action fill --session-id" in text
    assert "browser-cli action clear-role" in text
    assert "browser-cli action wait-state-role" in text
    assert "browser-cli action set-viewport" in text
    assert "browser-cli action screenshot-selector" in text
    assert "browser-cli action screenshot-role" in text
    assert "browser-cli action get-attribute-role" in text
    assert "browser-cli action wait-attribute-role" in text
    assert "browser-cli action select-role" in text
    assert "browser-cli action check-role" in text
    assert "browser-cli action wait-value-role" in text
    assert "browser-cli action blur-role" in text
    assert "browser-cli action interactive-snapshot" in text
    assert "browser-cli action accessibility-snapshot" in text
    assert "browser-cli action exists-role" in text
    assert "browser-cli action get-text-role" in text
    assert "browser-cli action bounding-box-role" in text
    assert "browser-cli action hover-role" in text
    assert "browser-cli action press-role" in text
    assert "browser-cli action scroll-into-view-role" in text
    assert "browser-cli action drag-role-to-role" in text
    assert "browser-cli action console-snapshot" in text
    assert "browser-cli action network-snapshot" in text
    assert "runtime errors" in text
    assert "fetch/XHR issues" in text
    assert "wait-role" in text
    assert "click-label" in text
    assert "click-role" in text
    assert "click-text" in text
    assert "page-info" in text
    assert "stable viewport" in text
    assert "selector or role screenshots" in text
    assert "wait-title" in text
    assert "waiting for text to disappear" in text
    assert "active/global shortcut keys" in text
    assert "interactive-snapshot" in text
    assert "context resolve" not in text
    assert "browser-cli direct-url" not in text
