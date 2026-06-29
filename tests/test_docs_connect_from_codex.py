from __future__ import annotations

from pathlib import Path


DOC = Path(__file__).resolve().parents[1] / "docs" / "connect-from-codex.md"


def test_connect_from_codex_doc_matches_current_cli_contracts() -> None:
    text = DOC.read_text()

    assert "https://browser.lexmount.cn/connect/codex" in text
    assert "browser-cli auth scopes" in text
    assert "browser-cli auth scopes --include-site-contract" in text
    assert "browser-cli auth connect-requirements" in text
    assert "browser-cli auth login" in text
    assert "browser-cli auth login --open" in text
    assert "browser-cli auth login --device-code" in text
    assert "selected_flow" in text
    assert "manual_env_available" in text
    assert "device_code_available" in text
    assert "reason=browser_site_endpoint_missing" in text
    assert "LEXMOUNT_BROWSER_DEVICE_CODE_BASE_URL" in text
    assert "--wait" in text
    assert "fallback_handoff" in text
    assert "browser-cli auth token-info" in text
    assert "browser-cli auth refresh" in text
    assert "browser-cli auth logout" in text
    assert "open_result" in text
    assert "site_capabilities" in text
    assert "site_capability_status" in text
    assert "browser_site_acceptance_tests" in text
    assert "connect_from_codex_contract" in text
    assert "setup_blocks" in text
    assert "required_device_code_endpoints" in text
    assert "required_api_contract" in text
    assert "required_token_lifecycle" in text
    assert "required_runtime_auth" in text
    assert "verification.doctor_command" in text
    assert "contains_secret_values" in text
    assert "contains_secret_placeholders" in text
    assert "safe_to_paste_in_chat" in text
    assert "local_shell_only" in text
    assert "requested_scope_details" in text
    assert "scope_ui_fields" in text
    assert "permission_count" in text
    assert "default_requested" in text
    assert "permission names" in text
    assert "risk level" in text
    assert "known: false" in text
    assert "project_id_display" in text
    assert "scoped_api_key" in text
    assert "copy_install_and_env" in text
    assert "doctor_verification" in text
    assert "scoped_key_lifecycle" in text
    assert "device_code_oauth" in text
    assert "revoke_available=false" in text
    assert "refresh_available=false" in text
    assert "refreshed=false" in text
    assert "browser-cli auth export-env" in text
    assert "browser-cli --version" in text
    assert "browser-cli doctor --json" in text
    assert "browser-cli doctor --smoke-session" in text
    assert "browser_smoke_session" in text
    assert "command catalog compatibility" in text
    assert "command catalog warnings" in text
    assert "ready_for_browser_actions" in text
    assert "runtime_auth.bearer_runtime.required_support" in text
    assert "lexmount-python-sdk bearer-token construction" in text
    assert "repair_plan.commands" in text
    assert "repair_plan.env" in text
    assert "repair_plan.guidance" in text
    assert "repair_plan.connect_from_codex" in text
    assert "repair_plan.connect_from_codex.url" in text
    assert "context pick --dry-run" in text
    assert "selection_summary" in text
    assert "locked_matches" in text
    assert "metadata_mismatches" in text
    assert "recommended_next_action" in text
    assert "decision_reason" in text
    assert "would_create" in text
    assert "browser-cli context pick --metadata-json" in text
    assert "browser-cli session create --context-metadata-json" in text
    assert "persist metadata supplied during context creation" in text
    assert "server-side metadata" in text
    assert 'availability: "available"' in text
    assert 'availability: "locked"' in text
    assert 'availability: "unavailable"' in text
    assert "context resolve" not in text
    assert "environment.configured" not in text


def test_connect_from_codex_doc_keeps_required_site_capabilities() -> None:
    text = DOC.read_text()

    required_phrases = [
        "Project ID with copy button",
        "Create scoped API key button",
        "Permission picker rendered from",
        "Revoke button for each agent key",
        "Expiration display",
        "Copyable env block",
        "Safety reminder: paste into local shell, not chat",
        "verification commands",
        "requested scopes",
        "Device-code tokens should be scoped, time-limited",
    ]

    for phrase in required_phrases:
        assert phrase in text
