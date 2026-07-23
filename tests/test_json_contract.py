from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from browser_cli.cli import main as cli_main
from lex_browser_runtime.browser.models import BrowserConfigError

JSON_CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "json-contract.md"


class DummyModel:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return self.payload


def run_cli_json(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict[str, Any], str]:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(argv)

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert isinstance(payload, dict)
    assert isinstance(payload.get("ok"), bool)
    assert isinstance(payload.get("command"), str)
    return int(exc_info.value.code or 0), payload, output


def assert_error_contract(payload: dict[str, Any]) -> None:
    assert payload["ok"] is False
    assert isinstance(payload["error"], str)
    assert payload["error"]
    assert isinstance(payload["message"], str)
    assert payload["message"]


def test_success_output_contract_for_session_list(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAdmin:
        def list_sessions(self, *, status: str | None) -> DummyModel:
            return DummyModel(
                {
                    "count": 0,
                    "status_filter": status,
                    "sessions": [],
                    "pagination": None,
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    exit_code, payload, output = run_cli_json(
        ["session", "list", "--status", "active"],
        capsys,
    )

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["command"] == "session.list"
    assert payload["count"] == 0
    assert output.endswith("}\n")


def test_success_output_contract_for_version(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "browser_cli.cli._package_version",
        lambda distribution: {
            "browser-cli": "0.2.0",
            "lex-browser-runtime": "1.2.3",
        }.get(distribution),
    )

    exit_code, payload, output = run_cli_json(["--version"], capsys)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["command"] == "version"
    assert payload["version"] == "0.2.0"
    assert payload["lex_browser_runtime_version"] == "0.2.0"
    assert output.endswith("}\n")


def test_commands_catalog_output_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, payload, _output = run_cli_json(
        ["commands", "--group", "action"], capsys
    )

    assert exit_code == 0
    assert payload["command"] == "commands"
    assert payload["schema_version"] == 1
    assert payload["group"] == "action"
    assert payload["command_count"] == len(payload["commands"])
    assert payload["json_output"]["always_json"] is True
    assert "secret_policy" in payload
    assert "agent_entrypoints" in payload
    assert payload["agent_references"]["action_playbook"]["path"] == (
        "references/action-playbook.md"
    )
    assert payload["agent_references"]["action_playbook"]["content_command"] == (
        "browser-cli reference get --id action_playbook"
    )
    assert payload["agent_references"]["action_playbook"]["package_resource"] == (
        "browser_cli.agent_references:action-playbook.md"
    )
    assert payload["agent_references"]["usable_status"]["path"] == (
        "references/usable-status.md"
    )
    assert payload["agent_references"]["usable_status"]["content_command"] == (
        "browser-cli reference get --id usable_status"
    )
    assert payload["agent_references"]["connect_from_codex"]["path"] == (
        "references/connect-from-codex.md"
    )
    assert payload["agent_references"]["connect_from_codex"]["content_command"] == (
        "browser-cli reference get --id connect_from_codex"
    )
    assert payload["agent_examples"]["page_inspection_case"]["content_command"] == (
        "browser-cli example get --id page_inspection_case"
    )
    assert payload["agent_examples"]["page_diagnostics_case"]["format"] == "yaml"
    assert payload["agent_examples"]["form_fill_case"]["format"] == "yaml"
    assert payload["agent_examples"]["navigation_flow_case"]["format"] == "yaml"
    assert payload["agent_examples"]["file_upload_case"]["format"] == "yaml"
    assert payload["agent_examples"]["checkout_flow_case"]["format"] == "yaml"
    assert payload["agent_examples"]["interactive_targeting_case"]["format"] == "yaml"
    assert all(command["group"] == "action" for command in payload["commands"])
    open_url = next(
        command
        for command in payload["commands"]
        if command["name"] == "action.open-url"
    )
    assert open_url["browser_target"]["exactly_one_of"] == [
        "--connect-url",
        "--direct-url",
        "--session-id",
    ]
    assert "--url" in open_url["required_options"]


def test_json_contract_documents_no_command_aliases() -> None:
    text = JSON_CONTRACT.read_text()

    assert "does not expose compatibility command aliases" in text
    assert "`alias_of`" not in text
    assert "`canonical_name`" not in text


def test_json_contract_documents_version_output() -> None:
    text = JSON_CONTRACT.read_text()

    assert "`browser-cli --version`" in text
    assert "`browser-cli version`" in text
    assert "`version_source`" in text
    assert "`lex_browser_runtime_version`" in text
    assert "`python_version`" in text
    assert "`executable`" in text


def test_json_contract_documents_agent_workflows() -> None:
    text = JSON_CONTRACT.read_text()
    normalized = " ".join(text.split())

    assert "`--workflows-only`" in text
    assert "`--workflow <id>`" in text
    assert "`browser-cli action guide`" in text
    assert "`selection_policy`" in text
    assert "`guide.inspect_commands`" in text
    assert "`guide.preferred_commands`" in text
    assert "`guide.verify_commands`" in text
    assert "`guide.custom_js_boundary`" in text
    assert "`set-viewport`" in text
    assert "`content_extraction`" in text
    assert "`text-snapshot`" in text
    assert "`table-snapshot`" in text
    assert "`link-snapshot`" in text
    assert "`browser_state_management`" in text
    assert "`storage-set`" in text
    assert "`cookie-set`" in text
    assert "`file_upload`" in text
    assert "`set-file-input`" in text
    assert "`dialog_frame_handling`" in text
    assert "`dialog-snapshot`" in text
    assert "`frame-snapshot`" in text
    assert "`navigation_flow`" in text
    assert "`link_navigation`" in text
    assert "`visual_capture`" in text
    assert "`semantic_waits`" in text
    assert "`screenshot-role`" in text
    assert "`screenshot-selector`" in text
    assert "`wait-role`" in text
    assert "`go-back`" in text
    assert "`go-forward`" in text
    assert "`menu_keyboard_flow`" in text
    assert "`mouse_interaction`" in text
    assert "`double-click-role`" in text
    assert "`right-click-role`" in text
    assert "`drag-role-to-role`" in text
    assert "`press-key`" in text
    assert "`state_waits`" in text
    assert "`wait-network`" in text
    assert "`wait-storage`" in text
    assert "`wait-cookie`" in text
    assert "`error=unknown_action_guide_task`" in text
    assert "`available_tasks`" in text
    assert "`error=unknown_group`" in text
    assert "`available_groups`" in text
    assert "commands for inspecting valid groups" in normalized
    assert "`workflow_count`" in text
    assert "`agent_workflows`" in text
    assert "`agent_references`" in text
    assert "`references/action-playbook.md`" in text
    assert "`content_command`" in text
    assert "`package_resource`" in text
    assert "`browser-cli reference list`" in text
    assert "`browser-cli reference get --id connect_from_codex`" in text
    assert "`browser-cli reference get --id quickstart`" in text
    assert "`browser-cli reference get --id usable_status`" in text
    assert "`browser-cli reference get --id action_playbook`" in text
    assert "`agent_examples`" in text
    assert "`browser-cli example list`" in text
    assert "`browser-cli example get --id setup_verification_playbook`" in text
    assert "`browser-cli example get --id auth_lifecycle_playbook`" in text
    assert "`browser-cli example get --id persistent_context_playbook`" in text
    assert "`browser-cli example get --id page_inspection_case`" in text
    assert "`browser-cli example get --id agent_primitives_case`" in text
    assert "`browser-cli example get --id content_extraction_case`" in text
    assert "`browser-cli example get --id browser_state_case`" in text
    assert "`browser-cli example get --id navigation_flow_case`" in text
    assert "`browser-cli example get --id file_upload_case`" in text
    assert "`browser-cli example get --id checkout_flow_case`" in text
    assert "`browser-cli example get --id interactive_targeting_case`" in text
    assert "`browser-cli example get --id page_diagnostics_case`" in text
    assert "`scaffold_templates`" in text
    assert "`load_when`" in text
    assert "`related_workflows`" in text
    assert "`grep_patterns`" in text
    assert "Connect from Codex site requirements" in text
    assert "The setup workflow includes" in text
    assert "`doctor --smoke-session`" in text
    assert "`browser_smoke_session.created`" in text
    assert "`auth scopes --include-site-contract`" in text
    assert "`browser_site_contract.scope_ui_fields`" in text
    assert "`auth connect-requirements`" in text
    assert "`required_api_contract`" in text
    assert "`required_token_lifecycle`" in text
    assert "`required_runtime_auth`" in text
    assert "Connect from Codex auth" in text
    assert "device-code auth" in text
    assert "`device_code.required_endpoints`" in text
    assert "`fallback_handoff`" in text
    assert "scoped token lifecycle" in text
    assert "`runtime_auth.usable`" in text
    assert "`runtime_auth.bearer_runtime.required_support`" in text
    assert "scope catalog lookup" in text
    assert "refresh availability" in text
    assert "local logout/revoke-pending fields" in text
    assert "session recovery" in text
    assert "replacement session" in text
    assert "case file task" in text
    assert "`case schema`" in text
    assert "`supported_actions`" in text
    assert "`required_fields`" in text
    assert "`case scaffold`" in text
    assert (
        "page/form/content/state/navigation/upload/checkout/interactive/diagnostic"
        in text
    )
    assert "`next_commands`" in text
    assert "`events_path`" in text
    assert "`artifacts_dir`" in text
    assert "`page-info`" in text
    assert "`wait-url`" in text
    assert "`wait-title`" in text
    assert "`wait-load-state`" in text
    assert "`select-label`" in text
    assert "`check-role`" in text
    assert "`hover-role`" in text
    assert "`press-role`" in text
    assert "`scroll-into-view-role`" in text
    assert "`get-text-role`" in text
    assert "`exists-role`" in text
    assert "form interaction" in text
    assert "interactive targeting" in text
    assert "`selection_order`" in text
    assert "`preferred_commands`" in text
    assert "`alternative_commands`" in text
    assert "page diagnostics" in text
    assert "console/network capture steps" in normalized
    assert "`result.entries`" in text
    assert "`result.entry_count`" in text
    assert "`workflow_id`" in text
    assert "`workflow`" in text
    assert "`available_workflows`" in text
    assert "`error=unknown_workflow`" in text
    assert "commands for inspecting valid workflows" in normalized
    assert "auth availability fields" in normalized
    assert "scope catalog fields" in normalized
    assert "export usability fields" in normalized
    assert "context reuse availability fields" in normalized
    assert "`success_condition`" in text
    assert "`on_failure_read`" in text
    assert "`cleanup`" in text


def test_json_contract_documents_doctor_required_workflows() -> None:
    text = JSON_CONTRACT.read_text()

    assert "`browser_cli.version_source`" in text
    assert "`agent_references`" in text
    assert "`required_references`" in text
    assert "`missing_required_references`" in text
    assert "`invalid_references`" in text
    assert "`checked_references`" in text
    assert "`agent_examples`" in text
    assert "`required_examples`" in text
    assert "`missing_required_examples`" in text
    assert "`invalid_examples`" in text
    assert "`checked_examples`" in text
    assert "`case_valid`" in text
    assert "`case_errors`" in text
    assert "`required_workflows`" in text
    assert "`missing_required_workflows`" in text
    assert "`required_workflow_steps`" in text
    assert "`missing_required_workflow_steps`" in text
    assert "`invalid_workflow_command_references`" in text
    assert "`required_agent_entrypoints`" in text
    assert "`missing_required_agent_entrypoints`" in text
    assert "`invalid_agent_entrypoint_command_references`" in text
    assert "`agent_entrypoint_count`" in text
    assert "`workflow_count`" in text
    assert "`action_guides`" in text
    assert "`required_action_guides`" in text
    assert "`missing_required_action_guides`" in text
    assert "`required_guide_fields`" in text
    assert "`invalid_action_guides`" in text
    assert "`invalid_guide_command_references`" in text
    assert "`required_case_actions`" in text
    assert "`required_case_scaffold_templates`" in text
    assert "`missing_case_scaffold_templates`" in text
    assert "`checked_case_scaffold_templates`" in text
    assert "`invalid_case_scaffold_templates`" in text
    assert "`auth_login_contract`" in text
    assert "`device_code_contract`" in text
    assert "`missing_handoff_fields`" in text
    assert "`missing_setup_blocks`" in text
    assert "`missing_required_device_code_endpoints`" in text
    assert "`missing_required_browser_site_support`" in text


def test_json_contract_documents_doctor_connect_from_codex_repair() -> None:
    text = JSON_CONTRACT.read_text()

    assert "`missing_env`" in text
    assert "`selected_flow`" in text
    assert "`manual_env_available`" in text
    assert "`device_code_available`" in text
    assert "`authenticated`" in text
    assert "`credentials_saved`" in text
    assert "`device_code.verification_uri_complete`" in text
    assert "`polling`" in text
    assert "`credentials`" in text
    assert "`auth scopes`" in text
    assert "`known_scopes`" in text
    assert "`scope_ui_fields`" in text
    assert "`browser_site_acceptance_tests`" in text
    assert "`connect_from_codex_contract`" in text
    assert "`auth connect-requirements`" in text
    assert "`connect_from_codex.device_code_url`" in text
    assert "`unusable_exports`" in text
    assert "`repair_plan`" in text
    assert "`fix` object" in text
    assert "`connect_from_codex`" in text
    assert "`open_command`" in text
    assert "`device_code_url`" in text
    assert "`site_capability_status`" in text
    assert "`required_token_lifecycle`" in text
    assert "`required_runtime_auth`" in text


def test_json_contract_documents_context_selection_decision_fields() -> None:
    text = JSON_CONTRACT.read_text()

    assert "`recommended_next_action`" in text
    assert "`decision_reason`" in text
    assert "`availability`" in text
    assert "`reusable`" in text
    assert "`locked`" in text
    assert "`reuse_reason`" in text
    assert "`metadata_diagnostics`" in text
    assert "`metadata_source`" in text
    assert "`value_redacted=true`" in text


def test_json_contract_documents_doctor_required_action_surface() -> None:
    text = JSON_CONTRACT.read_text()

    for phrase in (
        "press",
        "press-role",
        "hover",
        "hover-role",
        "scroll",
        "scroll-into-view",
        "scroll-into-view-role",
        "set-viewport",
        "reload",
        "go-back",
        "go-forward",
        "screenshot-selector",
        "screenshot-role",
        "wait-url",
        "wait-title",
        "wait-load-state",
        "wait-network-idle",
        "get-text",
        "get-text-role",
        "exists",
        "exists-role",
        "count",
        "wait-count",
        "wait-state",
        "wait-state-role",
        "query",
        "inspect",
        "get-attribute",
        "get-attribute-role",
        "wait-attribute",
        "wait-attribute-role",
        "bounding-box",
        "bounding-box-role",
        "select-option",
        "select-label",
        "select-role",
        "check",
        "uncheck",
        "check-label",
        "check-role",
        "uncheck-label",
        "uncheck-role",
        "click-label",
        "click-text",
        "click-role",
        "click-index",
        "focus",
        "focus-role",
        "fill",
        "fill-label",
        "fill-role",
        "get-value",
        "get-value-role",
        "wait-value",
        "wait-value-role",
        "blur",
        "blur-role",
        "clear",
        "clear-role",
        "set-value",
        "dispatch-event",
        "submit",
        "performance-snapshot",
        "accessibility snapshot",
        "interactive snapshot",
    ):
        assert phrase in text


def test_error_output_contract_for_configuration_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAdmin:
        def list_sessions(self, *, status: str | None) -> DummyModel:
            raise BrowserConfigError("missing credentials")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    exit_code, payload, _output = run_cli_json(["session", "list"], capsys)

    assert exit_code == 1
    assert payload["command"] == "session.list"
    assert_error_contract(payload)
    assert payload["error"] == "configuration_error"
    assert payload["message"] == "missing credentials"


def test_error_output_contract_for_action_target_validation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, payload, _output = run_cli_json(["action", "snapshot"], capsys)

    assert exit_code == 1
    assert payload["command"] == "action.snapshot"
    assert_error_contract(payload)
    assert "Pass exactly one action target" in payload["message"]


def test_context_pick_dry_run_selection_summary_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAdmin:
        def list_contexts(
            self,
            *,
            status: str | None,
            limit: int,
        ) -> DummyModel:
            return DummyModel(
                {
                    "count": 2,
                    "contexts": [
                        {
                            "context_id": "ctx-locked",
                            "status": "locked",
                            "metadata": {"purpose": "codex"},
                        },
                        {
                            "context_id": "ctx-other",
                            "status": "available",
                            "metadata": {"purpose": "manual"},
                        },
                    ],
                }
            )

        def create_context(self, *, metadata: dict[str, Any] | None) -> DummyModel:
            raise AssertionError("dry-run must not create contexts")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    exit_code, payload, _output = run_cli_json(
        [
            "context",
            "pick",
            "--metadata-json",
            '{"purpose":"codex"}',
            "--create-if-missing",
            "--dry-run",
        ],
        capsys,
    )

    assert exit_code == 0
    assert payload["command"] == "context.pick"
    assert payload["selected"] is False
    assert payload["created"] is False
    assert payload["dry_run"] is True
    assert payload["would_create"] is True
    summary = payload["selection_summary"]
    assert summary["checked"] == 2
    assert summary["metadata_matches"] == 1
    assert summary["metadata_mismatches"] == 1
    assert summary["reusable_matches"] == 0
    assert summary["locked_matches"] == 1
    assert summary["recommended_next_action"] == "rerun_without_dry_run_to_create"
    assert summary["decision_reason"] == "dry_run_create_if_missing"
    assert summary["would_create"] is True
    diagnostics = payload["candidates"][0]["metadata_diagnostics"]
    assert diagnostics == {
        "metadata_present": True,
        "metadata_source": "api",
        "metadata_keys": ["purpose"],
        "filter_keys": ["purpose"],
        "matched_keys": ["purpose"],
        "missing_keys": [],
        "different_keys": [],
        "value_redacted": True,
    }
    assert "codex" not in json.dumps(diagnostics)


def test_action_connect_url_secret_masking_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    connect_url = (
        "wss://api.lexmount.cn/connection?project_id=project&api_key=super-secret-key"
    )

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: connect_url,
    )
    monkeypatch.setattr(
        "browser_cli.cli.run_browser_action",
        lambda **kwargs: SimpleNamespace(result={"title": "Example"}),
    )

    exit_code, payload, output = run_cli_json(
        ["action", "snapshot", "--direct-url"],
        capsys,
    )

    assert exit_code == 0
    assert payload["command"] == "action.snapshot"
    assert payload["connect_url_masked"] is True
    assert "super-secret-key" not in output
    assert payload["connect_url"].endswith("api_key=***")


def test_action_connect_url_reveal_requires_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    connect_url = (
        "wss://api.lexmount.cn/connection?project_id=project&api_key=super-secret-key"
    )

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: connect_url,
    )
    monkeypatch.setattr(
        "browser_cli.cli.run_browser_action",
        lambda **kwargs: SimpleNamespace(result={"title": "Example"}),
    )

    exit_code, payload, output = run_cli_json(
        ["action", "snapshot", "--direct-url", "--reveal-connect-url"],
        capsys,
    )

    assert exit_code == 0
    assert payload["connect_url_masked"] is False
    assert "super-secret-key" in output
    assert payload["connect_url"] == connect_url
