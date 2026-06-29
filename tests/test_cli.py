from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from browser_cli import cli as cli_module
from browser_cli.cli import main as cli_main
from browser_cli.cli import validate_browser_cli_case_file as validate_case_file


@pytest.fixture(autouse=True)
def isolate_device_token_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv(
        "LEXMOUNT_BROWSER_CREDENTIALS_FILE",
        str(tmp_path / "missing-credentials.json"),
    )
    monkeypatch.setenv(
        "LEXMOUNT_BROWSER_CONTEXT_REGISTRY_FILE",
        str(tmp_path / "context-registry.json"),
    )


class DummyModel:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return self.payload


@pytest.mark.parametrize("argv", [["version"], ["--version"]])
def test_version_command_outputs_json(
    argv: list[str],
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

    with pytest.raises(SystemExit) as exc_info:
        cli_main(argv)

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "version"
    assert payload["package"] == "browser-cli"
    assert payload["version"] == "0.2.0"
    assert payload["version_source"] == "package_metadata"
    assert payload["lex_browser_runtime_version"] == "1.2.3"
    assert payload["lex_browser_runtime_version_known"] is True
    assert payload["python_version"]
    assert payload["executable"]


def test_version_command_falls_back_to_package_constant(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("browser_cli.cli._package_version", lambda distribution: None)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["version"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "version"
    assert payload["version"] == "0.1.0"
    assert payload["version_source"] == "package_fallback"
    assert payload["lex_browser_runtime_version"] == "unknown"
    assert payload["lex_browser_runtime_version_known"] is False


def test_json_dump_handles_broken_pipe_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_print(*_args: Any, **_kwargs: Any) -> None:
        raise BrokenPipeError

    monkeypatch.setattr("builtins.print", broken_print)
    monkeypatch.setattr(
        cli_module.sys,
        "stdout",
        SimpleNamespace(fileno=lambda: (_ for _ in ()).throw(OSError)),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_module._json_dump({"ok": True})

    assert exc_info.value.code == 141


def test_direct_url_masks_secret_by_default(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "key")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["direct-url"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": "direct-url",
        "mode": "direct",
        "connect_url": "wss://api.lexmount.cn/connection?project_id=project&api_key=***",
        "masked": True,
    }


@pytest.mark.parametrize(
    ("argv", "command", "message_part", "usage_part"),
    [
        (
            ["action", "open-url", "--session-id", "s1"],
            "action.open-url",
            "the following arguments are required: --url",
            "browser-cli action open-url",
        ),
        (
            [
                "action",
                "wait-selector",
                "--session-id",
                "s1",
                "--selector",
                "main",
                "--state",
                "nope",
            ],
            "action.wait-selector",
            "invalid choice: 'nope'",
            "browser-cli action wait-selector",
        ),
        (
            ["nope"],
            "browser-cli",
            "invalid choice: 'nope'",
            "browser-cli",
        ),
        (
            [],
            "browser-cli",
            "the following arguments are required: command",
            "browser-cli",
        ),
        (
            [
                "action",
                "click-index",
                "--session-id",
                "s1",
                "--selector",
                ".item",
                "--index",
                "-1",
            ],
            "action.click-index",
            "argument --index: value must be non-negative",
            "browser-cli action click-index",
        ),
    ],
)
def test_argument_errors_emit_json(
    argv: list[str],
    command: str,
    message_part: str,
    usage_part: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(argv)

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["command"] == command
    assert payload["error"] == "argument_error"
    assert message_part in payload["message"]
    assert usage_part in payload["usage"]


def test_json_compatibility_flag_is_accepted_after_subcommands(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAdmin:
        def list_sessions(self, *, status: str | None) -> DummyModel:
            return DummyModel({"count": 0, "status_filter": status, "sessions": []})

        def list_contexts(self, *, status: str | None, limit: int) -> DummyModel:
            return DummyModel(
                {
                    "count": 0,
                    "status_filter": status,
                    "limit": limit,
                    "contexts": [],
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())
    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://api.lexmount.cn/connection?project_id=p&api_key=secret",
    )
    monkeypatch.setattr(
        "browser_cli.cli.run_browser_action",
        lambda **kwargs: SimpleNamespace(result={"title": "Example"}),
    )

    cases = [
        (["auth", "status", "--json"], "auth.status"),
        (["session", "list", "--json"], "session.list"),
        (["session", "--json", "list"], "session.list"),
        (["context", "list", "--json"], "context.list"),
        (["list-contexts", "--json"], "context.list"),
        (["action", "snapshot", "--session-id", "s1", "--json"], "action.snapshot"),
        (["action", "--json", "snapshot", "--session-id", "s1"], "action.snapshot"),
    ]
    for argv, command in cases:
        with pytest.raises(SystemExit) as exc_info:
            cli_main(argv)

        assert exc_info.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["command"] == command


def test_commands_catalog_lists_machine_readable_agent_entrypoints(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    commands = {command["name"]: command for command in payload["commands"]}
    assert payload["ok"] is True
    assert payload["command"] == "commands"
    assert payload["schema_version"] == 1
    assert payload["command_count"] == len(payload["commands"])
    assert "action" in payload["groups"]
    assert "auth" in payload["groups"]
    assert "doctor" in payload["groups"]
    assert "example" in payload["groups"]
    assert "reference" in payload["groups"]
    assert "version" in payload["groups"]
    assert payload["json_output"]["always_json"] is True
    assert "LEXMOUNT_API_KEY" in payload["secret_policy"]["never_paste"]
    references = payload["agent_references"]
    assert references["action_playbook"]["path"] == "references/action-playbook.md"
    assert references["action_playbook"]["content_command"] == (
        "browser-cli reference get --id action_playbook"
    )
    assert references["action_playbook"]["metadata_command"] == (
        "browser-cli reference list"
    )
    assert references["action_playbook"]["package_resource"] == (
        "browser_cli.agent_references:action-playbook.md"
    )
    assert "form_interaction" in references["action_playbook"]["related_workflows"]
    assert "interactive_targeting" in references["action_playbook"]["related_workflows"]
    assert "mouse_interaction" in references["action_playbook"]["related_workflows"]
    assert "navigation_flow" in references["action_playbook"]["related_workflows"]
    assert "link_navigation" in references["action_playbook"]["related_workflows"]
    assert "visual_capture" in references["action_playbook"]["related_workflows"]
    assert "semantic_waits" in references["action_playbook"]["related_workflows"]
    assert "page_diagnostics" in references["action_playbook"]["related_workflows"]
    assert (
        "Structured Results And Masking"
        in references["action_playbook"]["grep_patterns"]
    )
    assert any(
        "semantic actions" in item
        for item in references["action_playbook"]["load_when"]
    )
    examples = payload["agent_examples"]
    assert examples["agent_playbook"]["path"] == "examples/agent-playbook.md"
    assert examples["agent_playbook"]["content_command"] == (
        "browser-cli example get --id agent_playbook"
    )
    assert examples["page_inspection_case"]["format"] == "yaml"
    assert "case_file_task" in examples["page_inspection_case"]["related_workflows"]
    assert examples["form_fill_case"]["package_resource"] == (
        "browser_cli.agent_examples.cases:form-fill.yaml"
    )
    assert (
        "browser-cli auth connect-requirements" in payload["agent_entrypoints"]["setup"]
    )
    assert "browser-cli auth scopes" in payload["agent_entrypoints"]["setup"]
    assert "browser-cli auth refresh" in payload["agent_entrypoints"]["setup"]
    assert "browser-cli doctor --json" in payload["agent_entrypoints"]["setup"]
    assert "browser-cli doctor --smoke-session" in payload["agent_entrypoints"]["setup"]
    assert (
        "browser-cli action page-info --session-id <session_id>"
        in payload["agent_entrypoints"]["one_off_page_task"]
    )
    assert (
        "browser-cli case run --file <case.yaml> --close-created-session"
        in payload["agent_entrypoints"]["case_file_task"]
    )
    assert "browser-cli case schema" in payload["agent_entrypoints"]["case_file_task"]
    assert (
        "browser-cli case scaffold --template page-inspection --url <url> --output case.yaml"
        in payload["agent_entrypoints"]["case_file_task"]
    )
    assert (
        "browser-cli case scaffold --template form-fill --output form-case.yaml"
        in payload["agent_entrypoints"]["case_file_task"]
    )
    assert (
        "browser-cli example get --id form_fill_case --metadata-only"
        in payload["agent_entrypoints"]["case_file_task"]
    )
    assert (
        "browser-cli auth login --device-code"
        in payload["agent_entrypoints"]["device_code_auth"]
    )
    assert (
        "browser-cli auth scopes --include-site-contract"
        in payload["agent_entrypoints"]["connect_from_codex_site_requirements"]
    )
    assert (
        "browser-cli auth connect-requirements"
        in payload["agent_entrypoints"]["connect_from_codex_site_requirements"]
    )
    assert (
        "browser-cli auth token-info --required-scope browser.actions:run"
        in payload["agent_entrypoints"]["scoped_token_lifecycle"]
    )
    assert (
        "browser-cli session keepalive --session-id <session_id> --duration 60 --stop-on-inactive"
        in payload["agent_entrypoints"]["session_recovery"]
    )
    assert (
        "browser-cli context pick"
        in payload["agent_entrypoints"]["persistent_login_state"][0]
    )
    assert "--dry-run" in payload["agent_entrypoints"]["persistent_login_state"][0]
    assert (
        "browser-cli context status --context-id <context_id>"
        in payload["agent_entrypoints"]["persistent_login_state"]
    )
    assert (
        "browser-cli action guide --task browser_state_management"
        in payload["agent_entrypoints"]["browser_state_management"]
    )
    assert (
        "browser-cli action storage-set --session-id <session_id> --area local --key <key> --value <value>"
        in payload["agent_entrypoints"]["browser_state_management"]
    )
    assert (
        "browser-cli action cookie-set --session-id <session_id> --name <name> --value <value> --path /"
        in payload["agent_entrypoints"]["browser_state_management"]
    )
    assert (
        "browser-cli action form-snapshot --session-id <session_id> --selector form"
        in payload["agent_entrypoints"]["form_interaction"]
    )
    assert (
        "browser-cli action guide --task file_upload"
        in payload["agent_entrypoints"]["file_upload"]
    )
    assert (
        'browser-cli action set-file-input --session-id <session_id> --selector "input[type=file]" --file ./upload.txt'
        in payload["agent_entrypoints"]["file_upload"]
    )
    assert (
        'browser-cli action set-file-input --session-id <session_id> --selector "input[type=file]" --file ./front.png --file ./back.png'
        in payload["agent_entrypoints"]["file_upload"]
    )
    assert (
        "browser-cli action guide --task dialog_frame_handling"
        in payload["agent_entrypoints"]["dialog_frame_handling"]
    )
    assert (
        "browser-cli action dialog-snapshot --session-id <session_id> --max-nodes 40 --max-controls 40"
        in payload["agent_entrypoints"]["dialog_frame_handling"]
    )
    assert (
        'browser-cli action frame-snapshot --session-id <session_id> --selector "iframe" --max-nodes 40 --max-chars 1000'
        in payload["agent_entrypoints"]["dialog_frame_handling"]
    )
    assert (
        "browser-cli action interactive-snapshot --session-id <session_id> --max-nodes 80"
        in payload["agent_entrypoints"]["interactive_targeting"]
    )
    assert (
        "browser-cli action guide --task navigation_flow"
        in payload["agent_entrypoints"]["navigation_flow"]
    )
    assert (
        "browser-cli action reload --session-id <session_id>"
        in payload["agent_entrypoints"]["navigation_flow"]
    )
    assert (
        "browser-cli action go-back --session-id <session_id>"
        in payload["agent_entrypoints"]["navigation_flow"]
    )
    assert (
        'browser-cli action wait-title --session-id <session_id> --title "<title text>"'
        in payload["agent_entrypoints"]["navigation_flow"]
    )
    assert (
        "browser-cli action guide --task link_navigation"
        in payload["agent_entrypoints"]["link_navigation"]
    )
    assert (
        "browser-cli action link-snapshot --session-id <session_id> --selector main --max-nodes 80"
        in payload["agent_entrypoints"]["link_navigation"]
    )
    assert (
        'browser-cli action click-role --session-id <session_id> --role link --name "Docs"'
        in payload["agent_entrypoints"]["link_navigation"]
    )
    assert (
        "browser-cli action guide --task mouse_interaction"
        in payload["agent_entrypoints"]["mouse_interaction"]
    )
    assert (
        'browser-cli action double-click-role --session-id <session_id> --role button --name "Edit"'
        in payload["agent_entrypoints"]["mouse_interaction"]
    )
    assert (
        'browser-cli action right-click-role --session-id <session_id> --role row --name "Invoice 123"'
        in payload["agent_entrypoints"]["mouse_interaction"]
    )
    assert (
        "browser-cli action guide --task visual_capture"
        in payload["agent_entrypoints"]["visual_capture"]
    )
    assert (
        "browser-cli action screenshot --session-id <session_id> --output /tmp/browser-cli-page.png --full-page"
        in payload["agent_entrypoints"]["visual_capture"]
    )
    assert (
        "browser-cli action screenshot-selector --session-id <session_id> --selector main --output /tmp/browser-cli-main.png"
        in payload["agent_entrypoints"]["visual_capture"]
    )
    assert (
        'browser-cli action screenshot-role --session-id <session_id> --role button --name "Submit" --output /tmp/browser-cli-target.png'
        in payload["agent_entrypoints"]["visual_capture"]
    )
    assert (
        "browser-cli action guide --task semantic_waits"
        in payload["agent_entrypoints"]["semantic_waits"]
    )
    assert (
        'browser-cli action wait-role --session-id <session_id> --role button --name "Submit" --state visible'
        in payload["agent_entrypoints"]["semantic_waits"]
    )
    assert (
        'browser-cli action wait-text --session-id <session_id> --text "Saved" --match contains'
        in payload["agent_entrypoints"]["semantic_waits"]
    )
    assert (
        'browser-cli action wait-attribute-role --session-id <session_id> --role button --name "Menu" --attribute aria-expanded --value true --match exact'
        in payload["agent_entrypoints"]["semantic_waits"]
    )
    assert (
        "browser-cli action guide --task menu_keyboard_flow"
        in payload["agent_entrypoints"]["menu_keyboard_flow"]
    )
    assert (
        "browser-cli action press-key --session-id <session_id> --key Escape"
        in payload["agent_entrypoints"]["menu_keyboard_flow"]
    )
    assert (
        'browser-cli action list-snapshot --session-id <session_id> --selector "[role=menu], [role=listbox], nav" --max-items 50'
        in payload["agent_entrypoints"]["menu_keyboard_flow"]
    )
    assert (
        "browser-cli action guide --task content_extraction"
        in payload["agent_entrypoints"]["content_extraction"]
    )
    assert (
        "browser-cli action text-snapshot --session-id <session_id> --selector main --max-nodes 80 --max-chars 1000"
        in payload["agent_entrypoints"]["content_extraction"]
    )
    assert (
        "browser-cli action table-snapshot --session-id <session_id> --selector table --max-rows 20 --max-cells 200"
        in payload["agent_entrypoints"]["content_extraction"]
    )
    assert (
        "browser-cli action guide --task state_waits"
        in payload["agent_entrypoints"]["state_waits"]
    )
    assert (
        "browser-cli action wait-load-state --session-id <session_id> --state networkidle"
        in payload["agent_entrypoints"]["state_waits"]
    )
    assert (
        "browser-cli action wait-network --session-id <session_id> --url <path> --url-match contains"
        in payload["agent_entrypoints"]["state_waits"]
    )
    assert (
        "browser-cli action console-snapshot --session-id <session_id> --install-only"
        in payload["agent_entrypoints"]["page_diagnostics"]
    )
    assert (
        "browser-cli action set-viewport --session-id <session_id> --width 1280 --height 720"
        in payload["agent_entrypoints"]["page_diagnostics"]
    )
    assert (
        "browser-cli action screenshot-selector --session-id <session_id> --selector main --output /tmp/browser-cli-main.png"
        in payload["agent_entrypoints"]["page_diagnostics"]
    )
    assert (
        'browser-cli action screenshot-role --session-id <session_id> --role button --name "Submit" --output /tmp/browser-cli-target.png'
        in payload["agent_entrypoints"]["page_diagnostics"]
    )
    workflows = payload["agent_workflows"]
    assert workflows["setup_and_verify"]["steps"][1]["command"] == (
        "browser-cli doctor --json"
    )
    assert (
        "repair_plan.connect_from_codex.url"
        in workflows["setup_and_verify"]["steps"][1]["on_failure_read"]
    )
    assert workflows["setup_and_verify"]["steps"][2]["optional"] is True
    assert (
        "browser_smoke_session.status"
        in workflows["setup_and_verify"]["steps"][2]["read"]
    )
    assert (
        "browser_smoke_session.session_id"
        in workflows["setup_and_verify"]["steps"][2]["read"]
    )
    assert (
        "browser_smoke_session.fix.commands"
        in workflows["setup_and_verify"]["steps"][2]["on_failure_read"]
    )
    site_steps = workflows["connect_from_codex_site_requirements"]["steps"]
    assert [step["id"] for step in site_steps] == [
        "inspect_scope_catalog",
        "inspect_site_requirements",
        "verify_manual_handoff",
        "verify_device_code_handoff",
        "doctor_after_credentials",
    ]
    assert site_steps[0]["command"] == (
        "browser-cli auth scopes --include-site-contract"
    )
    assert "browser_site_contract.scope_ui_fields" in site_steps[0]["read"]
    assert site_steps[1]["command"] == "browser-cli auth connect-requirements"
    assert "connect_from_codex.device_code_url" in site_steps[1]["read"]
    assert "required_device_code_endpoints" in site_steps[1]["read"]
    assert "required_token_lifecycle" in site_steps[1]["read"]
    assert "required_runtime_auth" in site_steps[1]["read"]
    assert site_steps[2]["optional"] is True
    assert "connect_from_codex.required_runtime_auth" in site_steps[2]["read"]
    assert site_steps[3]["command"] == "browser-cli auth login --device-code"
    assert "connect_from_codex.required_runtime_auth" in site_steps[3]["read"]
    assert site_steps[4]["command"] == "browser-cli doctor --json"
    auth_steps = workflows["connect_from_codex_auth"]["steps"]
    assert auth_steps[0]["command"] == "browser-cli auth status"
    assert "runtime_auth.usable" in auth_steps[0]["read"]
    assert "runtime_auth.source" in auth_steps[0]["read"]
    assert auth_steps[1]["command"] == "browser-cli auth scopes"
    assert "default_scopes" in auth_steps[1]["read"]
    assert auth_steps[2]["command"] == "browser-cli auth login"
    assert "selected_flow" in auth_steps[2]["read"]
    assert "manual_env_available" in auth_steps[2]["read"]
    assert "device_code_available" in auth_steps[2]["read"]
    assert "connect_from_codex.url" in auth_steps[2]["read"]
    assert "connect_from_codex.required_runtime_auth" in auth_steps[2]["read"]
    assert "usable" in auth_steps[3]["read"]
    assert "unusable_exports" in auth_steps[3]["read"]
    assert auth_steps[3]["local_shell_only"] is True
    device_steps = workflows["device_code_auth"]["steps"]
    assert [step["id"] for step in device_steps] == [
        "request_device_code",
        "fallback_manual_env",
        "verify_auth_status",
        "doctor",
    ]
    assert device_steps[0]["command"] == "browser-cli auth login --device-code"
    assert "device_code.required_endpoints" in device_steps[0]["read"]
    assert "device_code.required_browser_site_support" in device_steps[0]["read"]
    assert "connect_from_codex.required_runtime_auth" in device_steps[0]["read"]
    assert "device_code.verification_uri_complete" in device_steps[0]["read"]
    assert "polling.status" in device_steps[0]["read"]
    assert "credentials.device_token.valid" in device_steps[0]["read"]
    assert (
        "connect_from_codex.site_capability_status.missing" in device_steps[0]["read"]
    )
    assert "fallback_handoff.setup_blocks" in device_steps[0]["read"]
    assert device_steps[1]["optional"] is True
    assert device_steps[1]["command"] == "browser-cli auth login"
    assert "manual_env_available" in device_steps[1]["read"]
    assert "connect_from_codex.required_runtime_auth" in device_steps[1]["read"]
    assert "runtime_auth.usable" in device_steps[2]["read"]
    assert "runtime_auth.bearer_runtime.required_support" in device_steps[2]["read"]
    assert "device_token.valid" in device_steps[2]["read"]
    assert device_steps[3]["command"] == "browser-cli doctor --json"
    token_steps = workflows["scoped_token_lifecycle"]["steps"]
    assert [step["id"] for step in token_steps] == [
        "status_scoped_token",
        "inspect_scope_catalog",
        "inspect_required_scopes",
        "refresh_if_needed",
        "verify_browser_readiness",
        "logout_or_revoke_when_requested",
    ]
    assert token_steps[0]["command"] == "browser-cli auth status"
    assert "runtime_auth.usable" in token_steps[0]["read"]
    assert "runtime_auth.bearer_runtime.available" in token_steps[0]["read"]
    assert "runtime_auth.bearer_runtime.required_support" in token_steps[0]["read"]
    assert "device_token.refresh_needed" in token_steps[0]["read"]
    assert token_steps[1]["command"] == (
        "browser-cli auth scopes --scope browser:actions"
    )
    assert "scopes[0].permissions" in token_steps[1]["read"]
    assert token_steps[2]["command"] == (
        "browser-cli auth token-info --required-scope browser.actions:run"
    )
    assert "scope_check.satisfied" in token_steps[2]["read"]
    assert "scope_check.missing_scopes" in token_steps[2]["read"]
    assert token_steps[3]["optional"] is True
    assert "refresh_available" in token_steps[3]["read"]
    assert "refresh_endpoint" in token_steps[3]["read"]
    assert "remote_refresh.attempted" in token_steps[3]["read"]
    assert "credentials.saved" in token_steps[3]["read"]
    assert "browser-cli auth login" in token_steps[3]["fallback_commands"]
    assert token_steps[4]["command"] == "browser-cli doctor --json"
    assert "repair_plan.connect_from_codex.url" in token_steps[4]["on_failure_read"]
    assert token_steps[5]["optional"] is True
    assert token_steps[5]["user_requested_only"] is True
    assert "revoke_available" in token_steps[5]["read"]
    assert "revoked" in token_steps[5]["read"]
    assert "revoke_endpoint" in token_steps[5]["read"]
    assert "remote_revoke.attempted" in token_steps[5]["read"]
    session_steps = workflows["session_recovery"]["steps"]
    assert [step["id"] for step in session_steps] == [
        "list_active_sessions",
        "inspect_session",
        "keepalive_session",
        "close_stale_session",
        "create_replacement_session",
    ]
    assert session_steps[0]["command"] == "browser-cli session list --status active"
    assert "sessions" in session_steps[0]["read"]
    assert session_steps[1]["optional"] is True
    assert "session.status" in session_steps[1]["read"]
    assert session_steps[2]["command"] == (
        "browser-cli session keepalive --session-id <session_id> --duration 60 --stop-on-inactive"
    )
    assert "final_status" in session_steps[2]["read"]
    assert session_steps[3]["user_requested_only"] is True
    assert "closed" in session_steps[3]["read"]
    assert "context_reuse.availability" in session_steps[4]["read"]
    assert "browser-cli doctor --json" in session_steps[4]["fallback_commands"]
    one_off_steps = workflows["one_off_page_task"]["steps"]
    assert one_off_steps[0]["id"] == "create_session"
    assert "result.nodes" in one_off_steps[3]["read"]
    assert "result.node_count" in one_off_steps[3]["read"]
    assert one_off_steps[-1] == {
        "id": "close_session",
        "command": "browser-cli session close --session-id <session_id>",
        "cleanup": True,
    }
    navigation_steps = workflows["navigation_flow"]["steps"]
    assert [step["id"] for step in navigation_steps] == [
        "inspect_action_guide",
        "inspect_current_page",
        "choose_navigation_action",
        "run_navigation_action",
        "verify_navigation_result",
    ]
    assert navigation_steps[0]["command"] == (
        "browser-cli action guide --task navigation_flow"
    )
    assert "guide.selection_order" in navigation_steps[0]["read"]
    assert navigation_steps[1]["command"] == (
        "browser-cli action page-info --session-id <session_id>"
    )
    assert "ready_state" in navigation_steps[1]["read"]
    assert "scroll" in navigation_steps[1]["read"]
    assert navigation_steps[2]["agent_action"] is True
    assert navigation_steps[2]["selection_order"][:4] == [
        "open-url",
        "reload",
        "go-back",
        "go-forward",
    ]
    assert "browser-cli action open-url" in navigation_steps[2]["preferred_commands"][0]
    assert "browser-cli action reload" in navigation_steps[2]["preferred_commands"][1]
    assert navigation_steps[3]["agent_action"] is True
    assert "result.navigation_requested" in navigation_steps[3]["read"]
    assert "result.waited_ms" in navigation_steps[3]["read"]
    assert (
        "browser-cli action wait-load-state"
        in navigation_steps[4]["fallback_commands"][0]
    )
    assert (
        "browser-cli action wait-title" in navigation_steps[4]["fallback_commands"][2]
    )
    link_steps = workflows["link_navigation"]["steps"]
    assert [step["id"] for step in link_steps] == [
        "inspect_action_guide",
        "inspect_current_page",
        "inspect_links",
        "choose_link_target",
        "activate_link",
        "verify_navigation_result",
    ]
    assert link_steps[0]["command"] == "browser-cli action guide --task link_navigation"
    assert "browser-cli action link-snapshot" in link_steps[2]["command"]
    assert "result.links[].href_masked" in link_steps[2]["read"]
    assert link_steps[3]["agent_action"] is True
    assert link_steps[3]["secret_handling"].startswith("Do not copy href")
    assert "click-role" in link_steps[3]["selection_order"]
    assert "result.navigation_requested" in link_steps[4]["read"]
    mouse_steps = workflows["mouse_interaction"]["steps"]
    assert [step["id"] for step in mouse_steps] == [
        "inspect_action_guide",
        "inspect_interactive_targets",
        "choose_mouse_action",
        "run_mouse_action",
        "verify_result",
    ]
    assert (
        mouse_steps[0]["command"] == "browser-cli action guide --task mouse_interaction"
    )
    assert "double-click-role" in mouse_steps[2]["selection_order"]
    assert "right-click-role" in mouse_steps[2]["selection_order"]
    assert "result.double_clicked" in mouse_steps[3]["read"]
    assert "result.right_clicked" in mouse_steps[3]["read"]
    visual_steps = workflows["visual_capture"]["steps"]
    assert [step["id"] for step in visual_steps] == [
        "inspect_action_guide",
        "inspect_page_context",
        "set_viewport_if_needed",
        "choose_capture_target",
        "capture_visual_evidence",
        "verify_capture_artifact",
    ]
    assert visual_steps[0]["command"] == (
        "browser-cli action guide --task visual_capture"
    )
    assert "guide.read_fields" in visual_steps[0]["read"]
    assert visual_steps[1]["command"] == (
        "browser-cli action page-info --session-id <session_id>"
    )
    assert "viewport" in visual_steps[1]["read"]
    assert visual_steps[2]["optional"] is True
    assert "browser-cli action set-viewport" in visual_steps[2]["command"]
    assert "result.window_viewport" in visual_steps[2]["read"]
    assert visual_steps[3]["agent_action"] is True
    assert visual_steps[3]["selection_order"][:3] == [
        "screenshot-role",
        "screenshot-selector",
        "screenshot",
    ]
    assert (
        "browser-cli action screenshot-role" in visual_steps[3]["preferred_commands"][0]
    )
    assert visual_steps[4]["agent_action"] is True
    assert "result.screenshot" in visual_steps[4]["read"]
    assert "result.path" in visual_steps[4]["read"]
    assert "result.bounding_box" in visual_steps[4]["read"]
    assert "browser-cli action text-snapshot" in visual_steps[5]["fallback_commands"][1]
    semantic_wait_steps = workflows["semantic_waits"]["steps"]
    assert [step["id"] for step in semantic_wait_steps] == [
        "inspect_action_guide",
        "inspect_current_page",
        "choose_wait_predicate",
        "wait_for_semantic_state",
        "verify_observed_state",
    ]
    assert semantic_wait_steps[0]["command"] == (
        "browser-cli action guide --task semantic_waits"
    )
    assert "guide.custom_js_boundary" in semantic_wait_steps[0]["read"]
    assert semantic_wait_steps[1]["command"] == (
        "browser-cli action page-info --session-id <session_id>"
    )
    assert "visibility_state" in semantic_wait_steps[1]["read"]
    assert semantic_wait_steps[2]["agent_action"] is True
    assert semantic_wait_steps[2]["selection_order"][:4] == [
        "wait-role",
        "wait-text",
        "wait-state-role",
        "wait-attribute-role",
    ]
    assert (
        "browser-cli action wait-role"
        in semantic_wait_steps[2]["preferred_commands"][0]
    )
    assert semantic_wait_steps[3]["agent_action"] is True
    assert "result.waited_ms" in semantic_wait_steps[3]["read"]
    assert "result.state_values" in semantic_wait_steps[3]["read"]
    assert (
        "browser-cli action wait-selector"
        in semantic_wait_steps[3]["fallback_commands"][0]
    )
    assert "result.exists" in semantic_wait_steps[4]["read"]
    assert (
        "browser-cli action get-text-role"
        in semantic_wait_steps[4]["fallback_commands"][0]
    )
    case_steps = workflows["case_file_task"]["steps"]
    assert [step["id"] for step in case_steps] == [
        "inspect_case_commands",
        "inspect_case_schema",
        "inspect_semantic_case_action",
        "inspect_form_case_example",
        "scaffold_case_file",
        "scaffold_form_case_file",
        "validate_case_file",
        "run_case_file",
    ]
    assert case_steps[0]["command"] == "browser-cli commands --group case"
    assert "commands" in case_steps[0]["read"]
    assert case_steps[1]["command"] == "browser-cli case schema"
    assert "supported_actions" in case_steps[1]["read"]
    assert "required_fields" in case_steps[1]["read"]
    assert "top_level" in case_steps[1]["read"]
    assert case_steps[2]["command"] == "browser-cli case schema --action fill-label"
    assert "action_schema.result_fields" in case_steps[2]["read"]
    assert case_steps[3]["command"] == (
        "browser-cli example get --id form_fill_case --metadata-only"
    )
    assert "example.grep_patterns" in case_steps[3]["read"]
    assert case_steps[4]["command"] == (
        "browser-cli case scaffold --template page-inspection --url <url> --output case.yaml"
    )
    assert case_steps[4]["optional"] is True
    assert case_steps[4]["success_condition"] == "valid=true and wrote_file=true"
    assert "next_commands" in case_steps[4]["read"]
    assert case_steps[5]["command"] == (
        "browser-cli case scaffold --template form-fill --output form-case.yaml"
    )
    assert case_steps[5]["optional"] is True
    assert "case.steps" in case_steps[5]["read"]
    assert "supported_actions" in case_steps[5]["read"]
    assert case_steps[6]["command"] == "browser-cli case validate --file <case.yaml>"
    assert case_steps[6]["success_condition"] == "valid=true"
    assert "errors" in case_steps[6]["read"]
    assert case_steps[7]["command"] == (
        "browser-cli case run --file <case.yaml> --close-created-session"
    )
    assert "events_path" in case_steps[7]["read"]
    assert "message" in case_steps[7]["on_failure_read"]
    context_steps = workflows["persistent_login_state"]["steps"]
    assert "availability" in context_steps[0]["read"]
    assert "reusable" in context_steps[0]["read"]
    assert "locked" in context_steps[0]["read"]
    assert "reuse_reason" in context_steps[0]["read"]
    assert "selection_strategy" in context_steps[0]["read"]
    assert "selection_summary.recommended_next_action" in context_steps[0]["read"]
    assert "selection_summary.reusable_matches" in context_steps[0]["read"]
    assert "selection_summary.metadata_mismatches" in context_steps[0]["read"]
    assert "selection_summary.availability_counts" in context_steps[0]["read"]
    assert context_steps[1]["id"] == "inspect_context_status"
    assert context_steps[1]["optional"] is True
    assert context_steps[1]["command"] == (
        "browser-cli context status --context-id <context_id>"
    )
    assert "availability" in context_steps[1]["read"]
    assert "reusable" in context_steps[1]["read"]
    assert "locked" in context_steps[1]["read"]
    assert "normalized_status" in context_steps[1]["read"]
    assert "context_reuse.availability" in context_steps[2]["read"]
    assert "context_reuse.reusable" in context_steps[2]["read"]
    assert "context_reuse.locked" in context_steps[2]["read"]
    assert "context_reuse.reuse_reason" in context_steps[2]["read"]
    assert "context_reuse.selection_strategy" in context_steps[2]["read"]
    assert (
        "context_reuse.selection_summary.recommended_next_action"
        in (context_steps[2]["read"])
    )
    state_management_steps = workflows["browser_state_management"]["steps"]
    assert [step["id"] for step in state_management_steps] == [
        "inspect_action_guide",
        "inspect_page_info",
        "read_existing_state",
        "modify_state",
        "wait_for_state",
        "cleanup_state",
    ]
    assert state_management_steps[0]["command"] == (
        "browser-cli action guide --task browser_state_management"
    )
    assert "guide.custom_js_boundary" in state_management_steps[0]["read"]
    assert state_management_steps[1]["command"] == (
        "browser-cli action page-info --session-id <session_id>"
    )
    assert "visibility_state" in state_management_steps[1]["read"]
    assert "result.items" in state_management_steps[2]["read"]
    assert (
        "browser-cli action cookie-get"
        in state_management_steps[2]["alternative_commands"][0]
    )
    assert state_management_steps[3]["agent_action"] is True
    assert "result.previous_value" in state_management_steps[3]["read"]
    assert "result.document_cookie_scope" in state_management_steps[3]["read"]
    assert (
        "browser-cli action cookie-set"
        in state_management_steps[3]["alternative_commands"][0]
    )
    assert "result.requested_value" in state_management_steps[4]["read"]
    assert (
        "browser-cli action wait-cookie"
        in state_management_steps[4]["alternative_commands"][0]
    )
    assert state_management_steps[5]["optional"] is True
    assert state_management_steps[5]["user_requested_only"] is True
    assert "result.cleared_count" in state_management_steps[5]["read"]
    form_steps = workflows["form_interaction"]["steps"]
    assert [step["id"] for step in form_steps] == [
        "inspect_action_guide",
        "inspect_form",
        "fill_labeled_field",
        "choose_labeled_option",
        "check_labeled_control",
        "wait_submit_ready",
        "submit_form",
        "verify_result",
    ]
    assert (
        form_steps[0]["command"] == "browser-cli action guide --task form_interaction"
    )
    assert "guide.preferred_commands" in form_steps[0]["read"]
    assert form_steps[1]["command"] == (
        "browser-cli action form-snapshot --session-id <session_id> --selector form"
    )
    assert "result.fields" in form_steps[1]["read"]
    assert "result.field_count" in form_steps[1]["read"]
    assert "result.filled" in form_steps[2]["read"]
    assert "browser-cli action fill-role" in form_steps[2]["alternative_commands"][0]
    assert form_steps[3]["optional"] is True
    assert "result.option_found" in form_steps[3]["read"]
    assert form_steps[4]["optional"] is True
    assert "result.checked" in form_steps[4]["read"]
    assert "result.element" in form_steps[5]["read"]
    assert "result.matched" in form_steps[5]["read"]
    assert "result.state_values" in form_steps[5]["read"]
    assert "browser-cli action wait-role" in form_steps[5]["fallback_commands"][0]
    assert "result.clicked" in form_steps[6]["read"]
    assert form_steps[7]["optional"] is True
    assert "found" in form_steps[7]["read"]
    assert "browser-cli action wait-text" in form_steps[7]["fallback_commands"][0]
    upload_steps = workflows["file_upload"]["steps"]
    assert [step["id"] for step in upload_steps] == [
        "inspect_action_guide",
        "inspect_upload_controls",
        "attach_files",
        "verify_upload_state",
        "submit_if_requested",
    ]
    assert upload_steps[0]["command"] == "browser-cli action guide --task file_upload"
    assert "guide.custom_js_boundary" in upload_steps[0]["read"]
    assert upload_steps[1]["command"] == (
        "browser-cli action form-snapshot --session-id <session_id> --selector form"
    )
    assert "result.fields" in upload_steps[1]["read"]
    assert "browser-cli action query" in upload_steps[1]["fallback_commands"][0]
    assert upload_steps[2]["agent_action"] is True
    assert "browser-cli action set-file-input" in upload_steps[2]["command"]
    assert "result.requested_files" in upload_steps[2]["read"]
    assert "result.file_count" in upload_steps[2]["read"]
    assert "result.dispatched_events" in upload_steps[2]["read"]
    assert (
        "browser-cli action set-file-input"
        in upload_steps[2]["alternative_commands"][0]
    )
    assert "result.file_input" in upload_steps[3]["read"]
    assert "browser-cli action wait-text" in upload_steps[3]["fallback_commands"][0]
    assert upload_steps[4]["optional"] is True
    assert upload_steps[4]["user_requested_only"] is True
    assert "browser-cli action submit" in upload_steps[4]["fallback_commands"][0]
    dialog_steps = workflows["dialog_frame_handling"]["steps"]
    assert [step["id"] for step in dialog_steps] == [
        "inspect_action_guide",
        "inspect_page_context",
        "inspect_or_wait_dialog",
        "handle_dialog_control",
        "inspect_or_wait_frame",
        "verify_result",
    ]
    assert dialog_steps[0]["command"] == (
        "browser-cli action guide --task dialog_frame_handling"
    )
    assert "guide.read_fields" in dialog_steps[0]["read"]
    assert dialog_steps[1]["command"] == (
        "browser-cli action page-info --session-id <session_id>"
    )
    assert "visibility_state" in dialog_steps[1]["read"]
    assert dialog_steps[2]["optional"] is True
    assert "browser-cli action wait-dialog" in dialog_steps[2]["command"]
    assert "result.controls" in dialog_steps[2]["read"]
    assert "result.control_count" in dialog_steps[2]["read"]
    assert (
        "browser-cli action dialog-snapshot"
        in dialog_steps[2]["alternative_commands"][0]
    )
    assert dialog_steps[3]["optional"] is True
    assert dialog_steps[3]["agent_action"] is True
    assert "result.clicked" in dialog_steps[3]["read"]
    assert "browser-cli action click-text" in dialog_steps[3]["alternative_commands"][0]
    assert dialog_steps[4]["optional"] is True
    assert "browser-cli action wait-frame" in dialog_steps[4]["command"]
    assert "result.readable" in dialog_steps[4]["read"]
    assert "result.same_origin" in dialog_steps[4]["read"]
    assert "result.read_error" in dialog_steps[4]["read"]
    assert (
        "browser-cli action frame-snapshot"
        in dialog_steps[4]["alternative_commands"][0]
    )
    assert (
        "browser-cli action dialog-snapshot" in dialog_steps[5]["fallback_commands"][0]
    )
    targeting_steps = workflows["interactive_targeting"]["steps"]
    assert [step["id"] for step in targeting_steps] == [
        "inspect_action_guide",
        "inspect_interactive_targets",
        "inspect_accessibility_context",
        "choose_click_method",
        "wait_target_ready",
        "activate_target",
        "verify_after_click",
    ]
    assert targeting_steps[0]["command"] == (
        "browser-cli action guide --task interactive_targeting"
    )
    assert "guide.selection_order" in targeting_steps[0]["read"]
    assert targeting_steps[1]["command"] == (
        "browser-cli action interactive-snapshot --session-id <session_id> --max-nodes 80"
    )
    assert "result.nodes" in targeting_steps[1]["read"]
    assert "result.node_count" in targeting_steps[1]["read"]
    assert targeting_steps[2]["optional"] is True
    assert "result.truncated" in targeting_steps[2]["read"]
    assert targeting_steps[3]["agent_action"] is True
    assert targeting_steps[3]["selection_order"] == [
        "exists-role",
        "get-text-role",
        "bounding-box-role",
        "click-role",
        "hover-role",
        "press-role",
        "scroll-into-view-role",
        "click-text",
        "click-index",
    ]
    assert (
        "browser-cli action exists-role" in targeting_steps[3]["preferred_commands"][0]
    )
    assert (
        "browser-cli action get-text-role"
        in targeting_steps[3]["preferred_commands"][1]
    )
    assert (
        "browser-cli action bounding-box-role"
        in targeting_steps[3]["preferred_commands"][2]
    )
    assert (
        "browser-cli action hover-role" in targeting_steps[3]["preferred_commands"][4]
    )
    assert (
        "browser-cli action press-role" in targeting_steps[3]["preferred_commands"][5]
    )
    assert (
        "browser-cli action scroll-into-view-role"
        in targeting_steps[3]["preferred_commands"][6]
    )
    assert "result.element" in targeting_steps[4]["read"]
    assert (
        "browser-cli action exists-role" in targeting_steps[4]["fallback_commands"][0]
    )
    assert "browser-cli action wait-text" in targeting_steps[4]["fallback_commands"][1]
    assert "result.clicked" in targeting_steps[5]["read"]
    assert "result.hovered" in targeting_steps[5]["read"]
    assert "result.pressed" in targeting_steps[5]["read"]
    assert "result.scrolled" in targeting_steps[5]["read"]
    assert (
        "browser-cli action scroll-into-view-role"
        in targeting_steps[5]["alternative_commands"][2]
    )
    assert "browser-cli action wait-url" in targeting_steps[6]["fallback_commands"][0]
    menu_steps = workflows["menu_keyboard_flow"]["steps"]
    assert [step["id"] for step in menu_steps] == [
        "inspect_action_guide",
        "inspect_interactive_targets",
        "open_or_focus_menu",
        "verify_menu_state",
        "inspect_menu_items",
        "send_keyboard_input",
        "verify_result",
    ]
    assert menu_steps[0]["command"] == (
        "browser-cli action guide --task menu_keyboard_flow"
    )
    assert "guide.verify_commands" in menu_steps[0]["read"]
    assert menu_steps[1]["command"] == (
        "browser-cli action interactive-snapshot --session-id <session_id> --max-nodes 80"
    )
    assert "result.nodes" in menu_steps[1]["read"]
    assert (
        "browser-cli action accessibility-snapshot"
        in menu_steps[1]["fallback_commands"][0]
    )
    assert menu_steps[2]["agent_action"] is True
    assert "browser-cli action hover-role" in menu_steps[2]["command"]
    assert "result.hovered" in menu_steps[2]["read"]
    assert "browser-cli action focus-role" in menu_steps[2]["alternative_commands"][0]
    assert menu_steps[3]["optional"] is True
    assert "browser-cli action wait-attribute-role" in menu_steps[3]["command"]
    assert "result.attribute_found" in menu_steps[3]["read"]
    assert "result.requested_value" in menu_steps[3]["read"]
    assert "browser-cli action list-snapshot" in menu_steps[4]["command"]
    assert "result.items" in menu_steps[4]["read"]
    assert "result.selected" in menu_steps[4]["read"]
    assert menu_steps[5]["optional"] is True
    assert menu_steps[5]["agent_action"] is True
    assert "browser-cli action press-key" in menu_steps[5]["command"]
    assert "result.keydown_accepted" in menu_steps[5]["read"]
    assert "result.navigation_requested" in menu_steps[5]["read"]
    assert "browser-cli action click-role" in menu_steps[5]["alternative_commands"][1]
    assert "browser-cli action wait-text" in menu_steps[6]["fallback_commands"][1]
    extraction_steps = workflows["content_extraction"]["steps"]
    assert [step["id"] for step in extraction_steps] == [
        "inspect_action_guide",
        "inspect_page_info",
        "choose_extraction_surface",
        "extract_content",
        "verify_extraction_bounds",
    ]
    assert extraction_steps[0]["command"] == (
        "browser-cli action guide --task content_extraction"
    )
    assert "guide.preferred_commands" in extraction_steps[0]["read"]
    assert extraction_steps[1]["command"] == (
        "browser-cli action page-info --session-id <session_id>"
    )
    assert "visibility_state" in extraction_steps[1]["read"]
    assert extraction_steps[2]["agent_action"] is True
    assert extraction_steps[2]["selection_order"] == [
        "outline-snapshot",
        "text-snapshot",
        "link-snapshot",
        "table-snapshot",
        "list-snapshot",
        "accessibility-snapshot",
        "get-text-role",
        "get-text",
        "snapshot",
    ]
    assert (
        "browser-cli action table-snapshot"
        in extraction_steps[2]["preferred_commands"][3]
    )
    assert extraction_steps[3]["agent_action"] is True
    assert "result.tables" in extraction_steps[3]["read"]
    assert "result.headings" in extraction_steps[3]["read"]
    assert "browser-cli action snapshot" in extraction_steps[3]["fallback_commands"][0]
    assert extraction_steps[4]["agent_action"] is True
    assert "result.truncated" in extraction_steps[4]["read"]
    state_steps = workflows["state_waits"]["steps"]
    assert [step["id"] for step in state_steps] == [
        "inspect_action_guide",
        "inspect_current_state",
        "choose_wait_condition",
        "wait_for_state",
        "verify_after_wait",
    ]
    assert state_steps[0]["command"] == "browser-cli action guide --task state_waits"
    assert "guide.selection_order" in state_steps[0]["read"]
    assert state_steps[1]["command"] == (
        "browser-cli action page-info --session-id <session_id>"
    )
    assert "ready_state" in state_steps[1]["read"]
    assert state_steps[2]["agent_action"] is True
    assert state_steps[2]["selection_order"] == [
        "wait-load-state",
        "wait-url",
        "wait-state-role",
        "wait-attribute-role",
        "wait-selector",
        "wait-role",
        "wait-text",
        "wait-network",
        "wait-console",
        "wait-storage",
        "wait-cookie",
    ]
    assert "browser-cli action wait-network" in state_steps[2]["preferred_commands"][7]
    assert state_steps[3]["agent_action"] is True
    assert "result.matched" in state_steps[3]["read"]
    assert (
        "browser-cli action wait-network-idle" in state_steps[3]["fallback_commands"][0]
    )
    assert "visibility_state" in state_steps[4]["read"]
    diagnostics_steps = workflows["page_diagnostics"]["steps"]
    assert [step["id"] for step in diagnostics_steps] == [
        "inspect_action_guide",
        "page_info_before",
        "set_viewport",
        "install_console_capture",
        "install_network_capture",
        "reproduce_issue",
        "read_console_entries",
        "read_network_entries",
        "capture_visible_state",
    ]
    assert diagnostics_steps[0]["command"] == (
        "browser-cli action guide --task page_diagnostics"
    )
    assert "guide.read_fields" in diagnostics_steps[0]["read"]
    assert diagnostics_steps[2]["command"] == (
        "browser-cli action set-viewport --session-id <session_id> --width 1280 --height 720"
    )
    assert "result.viewport" in diagnostics_steps[2]["read"]
    assert diagnostics_steps[3]["command"] == (
        "browser-cli action console-snapshot --session-id <session_id> --install-only"
    )
    assert "result.newly_installed" in diagnostics_steps[3]["read"]
    assert "result.buffered_count_after" in diagnostics_steps[4]["read"]
    assert diagnostics_steps[5]["agent_action"] is True
    assert "result.entries" in diagnostics_steps[6]["read"]
    assert "result.entry_count" in diagnostics_steps[7]["read"]
    assert (
        "browser-cli action screenshot-role"
        in diagnostics_steps[8]["fallback_commands"][0]
    )
    assert (
        "browser-cli action screenshot-selector"
        in diagnostics_steps[8]["fallback_commands"][1]
    )
    assert (
        "browser-cli action screenshot" in diagnostics_steps[8]["fallback_commands"][2]
    )

    for name in (
        "commands",
        "version",
        "auth.login",
        "auth.refresh",
        "doctor",
        "case.schema",
        "case.scaffold",
        "case.validate",
        "case.run",
        "session.create",
        "context.pick",
        "action.guide",
        "action.open-url",
        "action.page-info",
        "action.set-viewport",
        "action.reload",
        "action.go-back",
        "action.go-forward",
        "action.screenshot-selector",
        "action.screenshot-role",
        "action.wait-url",
        "action.wait-title",
        "action.wait-load-state",
        "action.wait-network-idle",
        "action.count",
        "action.wait-count",
        "action.wait-state-role",
        "action.query",
        "action.inspect",
        "action.get-attribute",
        "action.press-key",
        "action.get-attribute-role",
        "action.wait-attribute",
        "action.wait-attribute-role",
        "action.get-text-role",
        "action.exists-role",
        "action.bounding-box",
        "action.bounding-box-role",
        "action.click-role",
        "action.click-index",
        "action.double-click",
        "action.double-click-role",
        "action.right-click",
        "action.right-click-role",
        "action.focus",
        "action.focus-role",
        "action.hover-role",
        "action.press-role",
        "action.scroll-into-view",
        "action.scroll-into-view-role",
        "action.select-label",
        "action.select-role",
        "action.check-label",
        "action.check-role",
        "action.uncheck-label",
        "action.uncheck-role",
        "action.fill-label",
        "action.fill-role",
        "action.get-value",
        "action.get-value-role",
        "action.wait-value",
        "action.wait-value-role",
        "action.blur",
        "action.blur-role",
        "action.clear",
        "action.clear-role",
        "action.set-value",
        "action.dispatch-event",
        "action.submit",
        "action.link-snapshot",
        "action.table-snapshot",
        "action.list-snapshot",
        "action.text-snapshot",
        "action.dialog-snapshot",
        "action.wait-dialog",
        "action.frame-snapshot",
        "action.wait-frame",
        "action.performance-snapshot",
        "action.network-snapshot",
        "action.wait-network",
        "action.console-snapshot",
        "action.wait-console",
        "action.outline-snapshot",
        "action.interactive-snapshot",
        "action.interactive-only-snapshot",
        "direct-url",
    ):
        assert name in commands

    open_url = commands["action.open-url"]
    assert open_url["browser_target"] == {
        "required": True,
        "exactly_one_of": ["--connect-url", "--direct-url", "--session-id"],
    }
    assert "--url" in open_url["required_options"]
    set_viewport = commands["action.set-viewport"]
    assert set_viewport["required_options"] == ["--width", "--height"]
    assert set_viewport["browser_target"] == open_url["browser_target"]
    screenshot_selector = commands["action.screenshot-selector"]
    assert screenshot_selector["required_options"] == ["--selector"]
    assert screenshot_selector["browser_target"] == open_url["browser_target"]
    assert any(
        "--output" in option["flags"] for option in screenshot_selector["options"]
    )
    screenshot_role = commands["action.screenshot-role"]
    assert screenshot_role["required_options"] == ["--role"]
    assert screenshot_role["browser_target"] == open_url["browser_target"]
    assert any("--name" in option["flags"] for option in screenshot_role["options"])
    assert any(
        "--include-hidden" in option["flags"] for option in screenshot_role["options"]
    )
    action_guide = commands["action.guide"]
    assert action_guide["required_options"] == []
    assert action_guide["required_one_of"] == []
    assert any("--task" in option["flags"] for option in action_guide["options"])
    wait_state_role = commands["action.wait-state-role"]
    assert "--role" in wait_state_role["required_options"]
    assert "--state" in wait_state_role["required_options"]
    assert any("--name" in option["flags"] for option in wait_state_role["options"])
    assert any(
        "--include-hidden" in option["flags"] for option in wait_state_role["options"]
    )
    get_attribute = commands["action.get-attribute"]
    assert get_attribute["required_options"] == ["--selector", "--name"]
    wait_attribute = commands["action.wait-attribute"]
    assert "--selector" in wait_attribute["required_options"]
    assert "--name" in wait_attribute["required_options"]
    assert any("--value" in option["flags"] for option in wait_attribute["options"])
    get_attribute_role = commands["action.get-attribute-role"]
    assert "--role" in get_attribute_role["required_options"]
    assert "--attribute" in get_attribute_role["required_options"]
    assert any("--name" in option["flags"] for option in get_attribute_role["options"])
    wait_attribute_role = commands["action.wait-attribute-role"]
    assert "--role" in wait_attribute_role["required_options"]
    assert "--attribute" in wait_attribute_role["required_options"]
    assert any(
        "--value" in option["flags"] for option in wait_attribute_role["options"]
    )
    get_text_role = commands["action.get-text-role"]
    assert get_text_role["required_options"] == ["--role"]
    assert any("--name" in option["flags"] for option in get_text_role["options"])
    assert any(
        "--include-hidden" in option["flags"] for option in get_text_role["options"]
    )
    exists_role = commands["action.exists-role"]
    assert exists_role["required_options"] == ["--role"]
    assert any("--name" in option["flags"] for option in exists_role["options"])
    assert any(
        "--include-hidden" in option["flags"] for option in exists_role["options"]
    )
    bounding_box_role = commands["action.bounding-box-role"]
    assert bounding_box_role["required_options"] == ["--role"]
    assert any("--name" in option["flags"] for option in bounding_box_role["options"])
    assert any(
        "--include-hidden" in option["flags"] for option in bounding_box_role["options"]
    )
    fill_role = commands["action.fill-role"]
    assert "--role" in fill_role["required_options"]
    assert "--text" in fill_role["required_options"]
    assert any("--name" in option["flags"] for option in fill_role["options"])
    get_value_role = commands["action.get-value-role"]
    assert get_value_role["required_options"] == ["--role"]
    assert any("--name" in option["flags"] for option in get_value_role["options"])
    wait_value_role = commands["action.wait-value-role"]
    assert "--role" in wait_value_role["required_options"]
    assert "--value" in wait_value_role["required_options"]
    assert any("--exact" in option["flags"] for option in wait_value_role["options"])
    focus_role = commands["action.focus-role"]
    assert focus_role["required_options"] == ["--role"]
    assert any("--name" in option["flags"] for option in focus_role["options"])
    assert any(
        "--prevent-scroll" in option["flags"] for option in focus_role["options"]
    )
    blur_role = commands["action.blur-role"]
    assert blur_role["required_options"] == ["--role"]
    assert any("--name" in option["flags"] for option in blur_role["options"])
    clear_role = commands["action.clear-role"]
    assert clear_role["required_options"] == ["--role"]
    assert any("--name" in option["flags"] for option in clear_role["options"])
    select_role = commands["action.select-role"]
    assert select_role["required_options"] == ["--role"]
    assert select_role["required_one_of"] == [["--value", "--option-label"]]
    assert any("--name" in option["flags"] for option in select_role["options"])
    check_role = commands["action.check-role"]
    assert check_role["required_options"] == ["--role"]
    assert any("--name" in option["flags"] for option in check_role["options"])
    uncheck_role = commands["action.uncheck-role"]
    assert uncheck_role["required_options"] == ["--role"]
    assert any("--name" in option["flags"] for option in uncheck_role["options"])
    hover_role = commands["action.hover-role"]
    assert hover_role["required_options"] == ["--role"]
    assert any("--name" in option["flags"] for option in hover_role["options"])
    double_click = commands["action.double-click"]
    assert double_click["required_options"] == ["--selector"]
    double_click_role = commands["action.double-click-role"]
    assert double_click_role["required_options"] == ["--role"]
    assert any("--name" in option["flags"] for option in double_click_role["options"])
    right_click = commands["action.right-click"]
    assert right_click["required_options"] == ["--selector"]
    right_click_role = commands["action.right-click-role"]
    assert right_click_role["required_options"] == ["--role"]
    assert any("--name" in option["flags"] for option in right_click_role["options"])
    press_role = commands["action.press-role"]
    assert "--role" in press_role["required_options"]
    assert "--key" in press_role["required_options"]
    assert any("--name" in option["flags"] for option in press_role["options"])
    scroll_role = commands["action.scroll-into-view-role"]
    assert scroll_role["required_options"] == ["--role"]
    assert any("--block" in option["flags"] for option in scroll_role["options"])
    assert any("--name" in option["flags"] for option in scroll_role["options"])
    interactive = commands["action.interactive-snapshot"]
    interactive_only = commands["action.interactive-only-snapshot"]
    assert interactive["aliases"] == ["action.interactive-only-snapshot"]
    assert interactive_only["alias_of"] == "action.interactive-snapshot"
    assert interactive_only["canonical_name"] == "action.interactive-snapshot"
    assert any(
        "--smoke-session" in option["flags"] for option in commands["doctor"]["options"]
    )
    assert any(
        "--workflows-only" in option["flags"]
        for option in commands["commands"]["options"]
    )
    assert any(
        "--workflow" in option["flags"] for option in commands["commands"]["options"]
    )
    assert any(
        "--metadata-only" in option["flags"]
        for option in commands["reference.get"]["options"]
    )
    assert any(
        "--metadata-only" in option["flags"]
        for option in commands["example.get"]["options"]
    )
    assert "super-secret-key" not in json.dumps(payload)


def test_action_guide_lists_tasks_and_returns_task_guidance(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--names-only"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": "action.guide",
        "schema_version": 1,
        "task_count": 14,
        "tasks": [
            "browser_state_management",
            "content_extraction",
            "dialog_frame_handling",
            "file_upload",
            "form_interaction",
            "interactive_targeting",
            "link_navigation",
            "menu_keyboard_flow",
            "mouse_interaction",
            "navigation_flow",
            "page_diagnostics",
            "semantic_waits",
            "state_waits",
            "visual_capture",
        ],
        "selection_policy": {
            "inspect_before_acting": True,
            "prefer_semantic_actions": True,
            "prefer_waits_over_sleep": True,
            "custom_javascript_last": True,
            "reference_command": "browser-cli reference get --id action_playbook",
            "command_catalog": "browser-cli commands --group action",
        },
    }

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "interactive_targeting"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "action.guide"
    assert payload["task"] == "interactive_targeting"
    assert payload["available_tasks"] == [
        "browser_state_management",
        "content_extraction",
        "dialog_frame_handling",
        "file_upload",
        "form_interaction",
        "interactive_targeting",
        "link_navigation",
        "menu_keyboard_flow",
        "mouse_interaction",
        "navigation_flow",
        "page_diagnostics",
        "semantic_waits",
        "state_waits",
        "visual_capture",
    ]
    assert payload["selection_policy"]["custom_javascript_last"] is True
    guide = payload["guide"]
    assert guide["related_workflows"] == ["interactive_targeting"]
    assert guide["selection_order"][:3] == [
        "interactive-snapshot",
        "accessibility-snapshot",
        "wait-role",
    ]
    assert guide["selection_order"][3:6] == [
        "exists-role",
        "get-text-role",
        "bounding-box-role",
    ]
    assert "browser-cli action accessibility-snapshot" in guide["inspect_commands"][1]
    assert "browser-cli action exists-role" in guide["preferred_commands"][1]
    assert "browser-cli action get-text-role" in guide["preferred_commands"][2]
    assert "browser-cli action bounding-box-role" in guide["preferred_commands"][3]
    assert any(
        "browser-cli action click-role" in command
        for command in guide["preferred_commands"]
    )
    assert any(
        "browser-cli action hover-role" in command
        for command in guide["preferred_commands"]
    )
    assert any(
        "browser-cli action press-role" in command
        for command in guide["preferred_commands"]
    )
    assert any(
        "browser-cli action scroll-into-view-role" in command
        for command in guide["fallback_commands"]
    )
    assert any(
        "browser-cli action hover --session-id" in command
        for command in guide["fallback_commands"]
    )
    assert "browser-cli action wait-url" in guide["verify_commands"][0]
    assert "result.nodes" in guide["read_fields"]
    assert "result.exists" in guide["read_fields"]
    assert "result.text" in guide["read_fields"]
    assert "result.bounding_box" in guide["read_fields"]
    assert "action eval only after" in guide["custom_js_boundary"]
    assert payload["next_commands"] == [
        "browser-cli commands --workflow interactive_targeting"
    ]

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "menu_keyboard_flow"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guide"]["related_workflows"] == [
        "menu_keyboard_flow",
        "interactive_targeting",
        "state_waits",
    ]
    assert payload["guide"]["selection_order"][:4] == [
        "interactive-snapshot",
        "accessibility-snapshot",
        "hover-role",
        "focus-role",
    ]
    assert "press-key" in payload["guide"]["selection_order"]
    assert any(
        "browser-cli action hover-role" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action press-key" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action wait-attribute-role" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action click-role" in command
        for command in payload["guide"]["fallback_commands"]
    )
    assert "result.items" in payload["guide"]["read_fields"]
    assert "result.keydown_accepted" in payload["guide"]["read_fields"]
    assert "result.navigation_requested" in payload["guide"]["read_fields"]
    assert "press-key" in payload["guide"]["custom_js_boundary"]
    assert payload["next_commands"][0] == (
        "browser-cli commands --workflow menu_keyboard_flow"
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "mouse_interaction"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guide"]["related_workflows"] == [
        "mouse_interaction",
        "interactive_targeting",
        "menu_keyboard_flow",
    ]
    assert payload["guide"]["selection_order"][:4] == [
        "interactive-snapshot",
        "accessibility-snapshot",
        "double-click-role",
        "right-click-role",
    ]
    assert any(
        "browser-cli action double-click-role" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action right-click" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert "result.double_clicked" in payload["guide"]["read_fields"]
    assert "result.context_menu" in payload["guide"]["read_fields"]
    assert "double-click/right-click" in payload["guide"]["custom_js_boundary"]
    assert payload["next_commands"] == [
        "browser-cli commands --workflow mouse_interaction",
        "browser-cli commands --workflow interactive_targeting",
        "browser-cli commands --workflow menu_keyboard_flow",
    ]

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "navigation_flow"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guide"]["related_workflows"] == [
        "navigation_flow",
        "state_waits",
        "one_off_page_task",
    ]
    assert payload["guide"]["selection_order"][:5] == [
        "page-info",
        "open-url",
        "reload",
        "go-back",
        "go-forward",
    ]
    assert any(
        "browser-cli action open-url" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action reload" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action wait-load-state" in command
        for command in payload["guide"]["verify_commands"]
    )
    assert "result.navigation_requested" in payload["guide"]["read_fields"]
    assert "result.waited_ms" in payload["guide"]["read_fields"]
    assert "open-url/reload/back/forward" in payload["guide"]["custom_js_boundary"]
    assert payload["next_commands"] == [
        "browser-cli commands --workflow navigation_flow",
        "browser-cli commands --workflow state_waits",
        "browser-cli commands --workflow one_off_page_task",
    ]

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "link_navigation"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guide"]["related_workflows"] == [
        "link_navigation",
        "navigation_flow",
        "interactive_targeting",
        "content_extraction",
    ]
    assert payload["guide"]["selection_order"][:5] == [
        "page-info",
        "link-snapshot",
        "interactive-snapshot",
        "accessibility-snapshot",
        "wait-role",
    ]
    assert any(
        "browser-cli action link-snapshot" in command
        for command in payload["guide"]["inspect_commands"]
    )
    assert any(
        "browser-cli action click-role" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action click-index" in command
        for command in payload["guide"]["fallback_commands"]
    )
    assert "result.links" in payload["guide"]["read_fields"]
    assert "result.links[].absolute_url_masked" in payload["guide"]["read_fields"]
    assert "link-snapshot" in payload["guide"]["custom_js_boundary"]
    assert payload["next_commands"] == [
        "browser-cli commands --workflow link_navigation",
        "browser-cli commands --workflow navigation_flow",
        "browser-cli commands --workflow interactive_targeting",
        "browser-cli commands --workflow content_extraction",
    ]

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "visual_capture"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guide"]["related_workflows"] == [
        "visual_capture",
        "page_diagnostics",
        "interactive_targeting",
    ]
    assert payload["guide"]["selection_order"][:5] == [
        "page-info",
        "set-viewport",
        "screenshot-role",
        "screenshot-selector",
        "screenshot",
    ]
    assert any(
        "browser-cli action screenshot-role" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action screenshot-selector" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert "result.screenshot" in payload["guide"]["read_fields"]
    assert "result.path" in payload["guide"]["read_fields"]
    assert "result.bounding_box" in payload["guide"]["read_fields"]
    assert "viewport setup" in payload["guide"]["custom_js_boundary"]
    assert payload["next_commands"] == [
        "browser-cli commands --workflow visual_capture",
        "browser-cli commands --workflow page_diagnostics",
        "browser-cli commands --workflow interactive_targeting",
    ]

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "semantic_waits"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guide"]["related_workflows"] == [
        "semantic_waits",
        "state_waits",
        "interactive_targeting",
    ]
    assert payload["guide"]["selection_order"][:5] == [
        "wait-role",
        "wait-text",
        "wait-state-role",
        "wait-attribute-role",
        "wait-count",
    ]
    assert any(
        "browser-cli action wait-role" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action wait-attribute-role" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert "result.waited_ms" in payload["guide"]["read_fields"]
    assert "result.state_values" in payload["guide"]["read_fields"]
    assert "result.attribute_found" in payload["guide"]["read_fields"]
    assert "semantic waits" in payload["guide"]["custom_js_boundary"]
    assert payload["next_commands"] == [
        "browser-cli commands --workflow semantic_waits",
        "browser-cli commands --workflow state_waits",
        "browser-cli commands --workflow interactive_targeting",
    ]

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "form_interaction"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "fill-role" in payload["guide"]["selection_order"]
    assert "clear-role" in payload["guide"]["selection_order"]
    assert "select-role" in payload["guide"]["selection_order"]
    assert "check-role" in payload["guide"]["selection_order"]
    assert "blur-role" in payload["guide"]["selection_order"]
    assert "wait-state-role" in payload["guide"]["selection_order"]
    assert any(
        "browser-cli action fill-role" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action select-role" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action check-role" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action wait-state-role" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action clear-role" in command
        for command in payload["guide"]["fallback_commands"]
    )
    assert any(
        "browser-cli action focus-role" in command
        for command in payload["guide"]["fallback_commands"]
    )
    assert any(
        "browser-cli action press-role" in command
        for command in payload["guide"]["fallback_commands"]
    )
    assert any(
        "browser-cli action uncheck-role" in command
        for command in payload["guide"]["fallback_commands"]
    )
    assert any(
        "browser-cli action blur-role" in command
        for command in payload["guide"]["verify_commands"]
    )
    assert any(
        "browser-cli action wait-value-role" in command
        for command in payload["guide"]["verify_commands"]
    )
    assert any(
        "browser-cli action get-value-role" in command
        for command in payload["guide"]["verify_commands"]
    )
    assert (
        "browser-cli action dispatch-event --session-id <session_id> "
        '--selector "<selector>" --event input --event change'
    ) in payload["guide"]["fallback_commands"]

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "file_upload"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guide"]["related_workflows"] == [
        "file_upload",
        "form_interaction",
        "interactive_targeting",
    ]
    assert payload["guide"]["selection_order"][:4] == [
        "form-snapshot",
        "query input[type=file]",
        "inspect",
        "set-file-input",
    ]
    assert any(
        "browser-cli action set-file-input" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action submit" in command
        for command in payload["guide"]["fallback_commands"]
    )
    assert "result.requested_files" in payload["guide"]["read_fields"]
    assert "result.file_count" in payload["guide"]["read_fields"]
    assert "OS picker" in payload["guide"]["custom_js_boundary"]
    assert payload["next_commands"][0] == "browser-cli commands --workflow file_upload"

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "dialog_frame_handling"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guide"]["related_workflows"] == [
        "dialog_frame_handling",
        "interactive_targeting",
        "page_diagnostics",
    ]
    assert payload["guide"]["selection_order"][:3] == [
        "page-info",
        "wait-dialog",
        "dialog-snapshot",
    ]
    assert "wait-frame" in payload["guide"]["selection_order"]
    assert "frame-snapshot" in payload["guide"]["selection_order"]
    assert any(
        "browser-cli action wait-dialog" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action frame-snapshot" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert "result.dialogs" in payload["guide"]["read_fields"]
    assert "result.controls" in payload["guide"]["read_fields"]
    assert "result.frames" in payload["guide"]["read_fields"]
    assert "result.readable" in payload["guide"]["read_fields"]
    assert "cross-origin frames" in payload["guide"]["custom_js_boundary"]
    assert payload["next_commands"][0] == (
        "browser-cli commands --workflow dialog_frame_handling"
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "page_diagnostics"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guide"]["selection_order"][:2] == [
        "page-info",
        "set-viewport",
    ]
    assert any(
        "browser-cli action set-viewport" in command
        for command in payload["guide"]["inspect_commands"]
    )
    assert any(
        "browser-cli action set-viewport" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert "result.viewport" in payload["guide"]["read_fields"]
    assert "result.window_viewport" in payload["guide"]["read_fields"]
    assert "screenshot-role" in payload["guide"]["selection_order"]
    assert "screenshot-selector" in payload["guide"]["selection_order"]
    assert any(
        "browser-cli action screenshot-role" in command
        for command in payload["guide"]["fallback_commands"]
    )
    assert any(
        "browser-cli action screenshot-selector" in command
        for command in payload["guide"]["fallback_commands"]
    )
    assert "result.screenshot" in payload["guide"]["read_fields"]
    assert "result.path" in payload["guide"]["read_fields"]
    assert (
        "set-viewport when viewport state matters"
        in payload["guide"]["custom_js_boundary"]
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "content_extraction"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guide"]["related_workflows"] == ["content_extraction"]
    assert payload["guide"]["selection_order"][:4] == [
        "page-info",
        "outline-snapshot",
        "text-snapshot",
        "link-snapshot",
    ]
    assert "table-snapshot" in payload["guide"]["selection_order"]
    assert any(
        "browser-cli action table-snapshot" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action snapshot" in command
        for command in payload["guide"]["fallback_commands"]
    )
    assert "result.texts" in payload["guide"]["read_fields"]
    assert "result.links" in payload["guide"]["read_fields"]
    assert "result.tables" in payload["guide"]["read_fields"]
    assert "result.headings" in payload["guide"]["read_fields"]
    assert "result.truncated" in payload["guide"]["read_fields"]
    assert "snapshots and get-text commands" in payload["guide"]["custom_js_boundary"]
    assert payload["next_commands"] == [
        "browser-cli commands --workflow content_extraction"
    ]

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "browser_state_management"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guide"]["related_workflows"] == [
        "browser_state_management",
        "persistent_login_state",
        "state_waits",
    ]
    assert payload["guide"]["selection_order"][:3] == [
        "page-info",
        "storage-get",
        "cookie-get",
    ]
    assert "storage-set" in payload["guide"]["selection_order"]
    assert "cookie-set" in payload["guide"]["selection_order"]
    assert any(
        "browser-cli action storage-set" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action cookie-set" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action wait-storage" in command
        for command in payload["guide"]["verify_commands"]
    )
    assert any(
        "browser-cli action wait-cookie" in command
        for command in payload["guide"]["verify_commands"]
    )
    assert "result.document_cookie_scope" in payload["guide"]["read_fields"]
    assert "cannot read HttpOnly cookies" in payload["guide"]["custom_js_boundary"]
    assert payload["next_commands"][0] == (
        "browser-cli commands --workflow browser_state_management"
    )
    assert (
        "browser-cli commands --workflow persistent_login_state"
        in payload["next_commands"]
    )
    assert "browser-cli commands --workflow state_waits" in payload["next_commands"]

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "state_waits"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["guide"]["related_workflows"][0] == "state_waits"
    assert payload["guide"]["selection_order"][:3] == [
        "wait-load-state",
        "wait-url",
        "wait-state-role",
    ]
    assert "wait-attribute-role" in payload["guide"]["selection_order"]
    assert any(
        "browser-cli action wait-state-role" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action wait-attribute-role" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action wait-network" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action wait-storage" in command
        for command in payload["guide"]["preferred_commands"]
    )
    assert any(
        "browser-cli action get-attribute-role" in command
        for command in payload["guide"]["inspect_commands"]
    )
    assert "result.matched" in payload["guide"]["read_fields"]
    assert "result.state_values" in payload["guide"]["read_fields"]
    assert "result.attribute_found" in payload["guide"]["read_fields"]


def test_action_guide_fails_unknown_task_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "guide", "--task", "missing"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "action.guide"
    assert payload["error"] == "unknown_action_guide_task"
    assert payload["task"] == "missing"
    assert payload["available_tasks"] == [
        "browser_state_management",
        "content_extraction",
        "dialog_frame_handling",
        "file_upload",
        "form_interaction",
        "interactive_targeting",
        "link_navigation",
        "menu_keyboard_flow",
        "mouse_interaction",
        "navigation_flow",
        "page_diagnostics",
        "semantic_waits",
        "state_waits",
        "visual_capture",
    ]
    assert payload["fix"]["code"] == "inspect_action_guide_tasks"
    assert "browser-cli action guide --names-only" in payload["fix"]["commands"]


def test_commands_catalog_returns_workflows_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflows-only"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "commands"
    assert payload["schema_version"] == 1
    assert payload["group"] is None
    assert payload["workflow_count"] == 23
    assert "commands" not in payload
    assert payload["agent_references"]["action_playbook"]["path"] == (
        "references/action-playbook.md"
    )
    assert payload["agent_examples"]["page_inspection_case"]["content_command"] == (
        "browser-cli example get --id page_inspection_case"
    )
    assert (
        "page_diagnostics"
        in payload["agent_references"]["action_playbook"]["related_workflows"]
    )
    assert payload["agent_workflows"]["setup_and_verify"]["steps"][1]["command"] == (
        "browser-cli doctor --json"
    )
    assert (
        "browser_smoke_session.status"
        in payload["agent_workflows"]["setup_and_verify"]["steps"][2]["read"]
    )
    assert (
        "required_token_lifecycle"
        in payload["agent_workflows"]["connect_from_codex_site_requirements"]["steps"][
            1
        ]["read"]
    )
    assert (
        payload["agent_workflows"]["connect_from_codex_auth"]["steps"][2]["command"]
        == "browser-cli auth login"
    )
    assert (
        "device_code.required_endpoints"
        in payload["agent_workflows"]["device_code_auth"]["steps"][0]["read"]
    )
    assert payload["agent_workflows"]["one_off_page_task"]["steps"][-1] == {
        "id": "close_session",
        "command": "browser-cli session close --session-id <session_id>",
        "cleanup": True,
    }
    assert (
        "result.nodes"
        in payload["agent_workflows"]["one_off_page_task"]["steps"][3]["read"]
    )
    assert (
        "result.navigation_requested"
        in payload["agent_workflows"]["navigation_flow"]["steps"][3]["read"]
    )
    assert (
        "result.links[].absolute_url_masked"
        in payload["agent_workflows"]["link_navigation"]["steps"][2]["read"]
    )
    assert (
        "result.double_clicked"
        in payload["agent_workflows"]["mouse_interaction"]["steps"][3]["read"]
    )
    assert (
        "events_path"
        in payload["agent_workflows"]["case_file_task"]["steps"][7]["read"]
    )
    assert (
        "selection_summary.recommended_next_action"
        in payload["agent_workflows"]["persistent_login_state"]["steps"][0]["read"]
    )
    assert (
        "context_reuse.availability"
        in payload["agent_workflows"]["persistent_login_state"]["steps"][2]["read"]
    )
    assert (
        "result.document_cookie_scope"
        in payload["agent_workflows"]["browser_state_management"]["steps"][3]["read"]
    )
    assert (
        "normalized_status"
        in payload["agent_workflows"]["persistent_login_state"]["steps"][1]["read"]
    )
    assert (
        "unusable_exports"
        in payload["agent_workflows"]["connect_from_codex_auth"]["steps"][3]["read"]
    )
    assert (
        "scope_check.missing_scopes"
        in payload["agent_workflows"]["scoped_token_lifecycle"]["steps"][2]["read"]
    )
    assert (
        "final_status"
        in payload["agent_workflows"]["session_recovery"]["steps"][2]["read"]
    )
    assert (
        "result.filled"
        in payload["agent_workflows"]["form_interaction"]["steps"][2]["read"]
    )
    assert (
        "result.requested_files"
        in payload["agent_workflows"]["file_upload"]["steps"][2]["read"]
    )
    assert (
        "result.frames"
        in payload["agent_workflows"]["dialog_frame_handling"]["steps"][4]["read"]
    )
    assert (
        "result.nodes"
        in payload["agent_workflows"]["interactive_targeting"]["steps"][1]["read"]
    )
    assert (
        "result.screenshot"
        in payload["agent_workflows"]["visual_capture"]["steps"][4]["read"]
    )
    assert (
        "result.waited_ms"
        in payload["agent_workflows"]["semantic_waits"]["steps"][3]["read"]
    )
    assert (
        "result.keydown_accepted"
        in payload["agent_workflows"]["menu_keyboard_flow"]["steps"][5]["read"]
    )
    assert (
        "result.texts"
        in payload["agent_workflows"]["content_extraction"]["steps"][3]["read"]
    )
    assert (
        "result.matched"
        in payload["agent_workflows"]["state_waits"]["steps"][3]["read"]
    )
    assert (
        "result.entries"
        in payload["agent_workflows"]["page_diagnostics"]["steps"][6]["read"]
    )
    assert "browser-cli auth login" in payload["agent_entrypoints"]["setup"]
    assert payload["json_output"]["always_json"] is True
    assert "LEXMOUNT_API_KEY" in payload["secret_policy"]["never_paste"]


def test_commands_catalog_returns_single_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "one_off_page_task"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "commands"
    assert payload["workflow_id"] == "one_off_page_task"
    assert "commands" not in payload
    assert "agent_workflows" not in payload
    assert payload["agent_references"]["action_playbook"]["path"] == (
        "references/action-playbook.md"
    )
    assert payload["agent_examples"]["agent_playbook"]["content_command"] == (
        "browser-cli example get --id agent_playbook"
    )
    assert payload["workflow"]["steps"][0]["id"] == "create_session"
    assert "result.nodes" in payload["workflow"]["steps"][3]["read"]
    assert payload["workflow"]["steps"][-1] == {
        "id": "close_session",
        "command": "browser-cli session close --session-id <session_id>",
        "cleanup": True,
    }
    assert (
        "browser-cli session create"
        in payload["agent_entrypoints"]["one_off_page_task"]
    )


def test_commands_catalog_returns_navigation_flow_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "navigation_flow"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "navigation_flow"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "inspect_action_guide",
        "inspect_current_page",
        "choose_navigation_action",
        "run_navigation_action",
        "verify_navigation_result",
    ]
    assert steps[0]["command"] == "browser-cli action guide --task navigation_flow"
    assert "guide.custom_js_boundary" in steps[0]["read"]
    assert steps[1]["command"] == (
        "browser-cli action page-info --session-id <session_id>"
    )
    assert "visibility_state" in steps[1]["read"]
    assert steps[2]["agent_action"] is True
    assert steps[2]["selection_order"] == [
        "open-url",
        "reload",
        "go-back",
        "go-forward",
        "wait-load-state",
        "wait-url",
        "wait-title",
    ]
    assert "browser-cli action reload" in steps[2]["preferred_commands"][1]
    assert steps[3]["agent_action"] is True
    assert "result.navigation_requested" in steps[3]["read"]
    assert steps[-1]["id"] == "verify_navigation_result"
    assert "browser-cli action wait-url" in steps[-1]["fallback_commands"][1]
    assert (
        "browser-cli action guide --task navigation_flow"
        in payload["agent_entrypoints"]["navigation_flow"][0]
    )


def test_commands_catalog_returns_link_navigation_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "link_navigation"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "link_navigation"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "inspect_action_guide",
        "inspect_current_page",
        "inspect_links",
        "choose_link_target",
        "activate_link",
        "verify_navigation_result",
    ]
    assert steps[0]["command"] == "browser-cli action guide --task link_navigation"
    assert "guide.custom_js_boundary" in steps[0]["read"]
    assert "browser-cli action link-snapshot" in steps[2]["command"]
    assert "result.links[].absolute_url_masked" in steps[2]["read"]
    assert steps[3]["agent_action"] is True
    assert steps[3]["selection_order"][:3] == [
        "click-role",
        "click-text",
        "open-url",
    ]
    assert "browser-cli action click-role" in steps[3]["preferred_commands"][1]
    assert "browser-cli action click-index" in steps[3]["fallback_commands"][2]
    assert steps[3]["secret_handling"].startswith("Do not copy href")
    assert "result.navigation_requested" in steps[4]["read"]
    assert "browser-cli action wait-url" in steps[-1]["fallback_commands"][0]
    assert (
        "browser-cli action guide --task link_navigation"
        in payload["agent_entrypoints"]["link_navigation"][0]
    )


def test_commands_catalog_returns_mouse_interaction_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "mouse_interaction"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "mouse_interaction"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "inspect_action_guide",
        "inspect_interactive_targets",
        "choose_mouse_action",
        "run_mouse_action",
        "verify_result",
    ]
    assert steps[0]["command"] == "browser-cli action guide --task mouse_interaction"
    assert "guide.custom_js_boundary" in steps[0]["read"]
    assert "browser-cli action interactive-snapshot" in steps[1]["command"]
    assert steps[2]["agent_action"] is True
    assert steps[2]["selection_order"][:4] == [
        "double-click-role",
        "right-click-role",
        "double-click",
        "right-click",
    ]
    assert "browser-cli action double-click-role" in steps[2]["preferred_commands"][0]
    assert "browser-cli action right-click" in steps[2]["preferred_commands"][3]
    assert steps[3]["agent_action"] is True
    assert "result.double_clicked" in steps[3]["read"]
    assert "result.context_menu" in steps[3]["read"]
    assert "browser-cli action wait-text" in steps[-1]["fallback_commands"][0]
    assert (
        "browser-cli action guide --task mouse_interaction"
        in payload["agent_entrypoints"]["mouse_interaction"][0]
    )


def test_commands_catalog_returns_visual_capture_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "visual_capture"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "visual_capture"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "inspect_action_guide",
        "inspect_page_context",
        "set_viewport_if_needed",
        "choose_capture_target",
        "capture_visual_evidence",
        "verify_capture_artifact",
    ]
    assert steps[0]["command"] == "browser-cli action guide --task visual_capture"
    assert "guide.custom_js_boundary" in steps[0]["read"]
    assert steps[1]["command"] == (
        "browser-cli action page-info --session-id <session_id>"
    )
    assert "viewport" in steps[1]["read"]
    assert steps[2]["optional"] is True
    assert "browser-cli action set-viewport" in steps[2]["command"]
    assert steps[3]["agent_action"] is True
    assert steps[3]["selection_order"][:3] == [
        "screenshot-role",
        "screenshot-selector",
        "screenshot",
    ]
    assert steps[4]["agent_action"] is True
    assert "result.screenshot" in steps[4]["read"]
    assert "result.path" in steps[4]["read"]
    assert "browser-cli action text-snapshot" in steps[-1]["fallback_commands"][1]
    assert (
        "browser-cli action guide --task visual_capture"
        in payload["agent_entrypoints"]["visual_capture"][0]
    )


def test_commands_catalog_returns_semantic_waits_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "semantic_waits"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "semantic_waits"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "inspect_action_guide",
        "inspect_current_page",
        "choose_wait_predicate",
        "wait_for_semantic_state",
        "verify_observed_state",
    ]
    assert steps[0]["command"] == "browser-cli action guide --task semantic_waits"
    assert "guide.custom_js_boundary" in steps[0]["read"]
    assert steps[2]["agent_action"] is True
    assert steps[2]["selection_order"][:4] == [
        "wait-role",
        "wait-text",
        "wait-state-role",
        "wait-attribute-role",
    ]
    assert "browser-cli action wait-role" in steps[2]["preferred_commands"][0]
    assert steps[3]["agent_action"] is True
    assert "result.waited_ms" in steps[3]["read"]
    assert "result.attribute_found" in steps[3]["read"]
    assert "browser-cli action wait-selector" in steps[3]["fallback_commands"][0]
    assert "result.exists" in steps[-1]["read"]
    assert (
        "browser-cli action guide --task semantic_waits"
        in payload["agent_entrypoints"]["semantic_waits"][0]
    )


def test_reference_list_returns_packaged_agent_references(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["reference", "list"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "reference.list"
    assert payload["reference_count"] == 1
    reference = payload["references"]["action_playbook"]
    assert reference["id"] == "action_playbook"
    assert reference["path"] == "references/action-playbook.md"
    assert reference["content_command"] == (
        "browser-cli reference get --id action_playbook"
    )
    assert reference["package_resource"] == (
        "browser_cli.agent_references:action-playbook.md"
    )
    assert "Common Task Recipes" in reference["grep_patterns"]


def test_reference_list_names_only(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["reference", "list", "--names-only"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "reference.list"
    assert payload["reference_count"] == 1
    assert payload["references"] == ["action_playbook"]


def test_reference_get_returns_packaged_action_playbook(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["reference", "get", "--id", "action_playbook"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "reference.get"
    assert payload["reference_id"] == "action_playbook"
    assert payload["reference"]["id"] == "action_playbook"
    assert payload["content_format"] == "markdown"
    assert payload["content_included"] is True
    assert payload["content_length"] == len(payload["content"])
    assert "# Browser Action Playbook" in payload["content"]
    assert "Common Task Recipes" in payload["content"]
    assert "Target Contract" in payload["content"]


def test_reference_get_metadata_only_omits_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["reference", "get", "--id", "action_playbook", "--metadata-only"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "reference.get"
    assert payload["reference_id"] == "action_playbook"
    assert payload["content_included"] is False
    assert "content" not in payload
    assert payload["reference"]["content_command"] == (
        "browser-cli reference get --id action_playbook"
    )


def test_reference_get_fails_unknown_reference_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["reference", "get", "--id", "missing"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "reference.get"
    assert payload["error"] == "unknown_reference"
    assert payload["reference_id"] == "missing"
    assert payload["available_references"] == ["action_playbook"]
    assert payload["fix"]["code"] == "inspect_available_agent_references"


def test_example_list_returns_packaged_agent_examples(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["example", "list"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "example.list"
    assert payload["example_count"] == 3
    assert sorted(payload["examples"]) == [
        "agent_playbook",
        "form_fill_case",
        "page_inspection_case",
    ]
    playbook = payload["examples"]["agent_playbook"]
    assert playbook["id"] == "agent_playbook"
    assert playbook["path"] == "examples/agent-playbook.md"
    assert playbook["content_command"] == (
        "browser-cli example get --id agent_playbook"
    )
    assert playbook["package_resource"] == (
        "browser_cli.agent_examples:agent-playbook.md"
    )
    assert "case_file_task" in playbook["related_workflows"]
    assert payload["examples"]["page_inspection_case"]["format"] == "yaml"
    assert payload["examples"]["form_fill_case"]["format"] == "yaml"


def test_example_list_names_only(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["example", "list", "--names-only"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "example.list"
    assert payload["example_count"] == 3
    assert payload["examples"] == [
        "agent_playbook",
        "form_fill_case",
        "page_inspection_case",
    ]


def test_example_get_returns_packaged_case_file(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["example", "get", "--id", "page_inspection_case"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "example.get"
    assert payload["example_id"] == "page_inspection_case"
    assert payload["example"]["id"] == "page_inspection_case"
    assert payload["content_format"] == "yaml"
    assert payload["content_included"] is True
    assert payload["content_length"] == len(payload["content"])
    assert "name: page-inspection" in payload["content"]
    assert "action: open-url" in payload["content"]
    assert "action: screenshot" in payload["content"]


def test_example_get_metadata_only_omits_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["example", "get", "--id", "agent_playbook", "--metadata-only"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "example.get"
    assert payload["example_id"] == "agent_playbook"
    assert payload["content_included"] is False
    assert "content" not in payload
    assert payload["example"]["content_command"] == (
        "browser-cli example get --id agent_playbook"
    )


def test_example_get_fails_unknown_example_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["example", "get", "--id", "missing"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "example.get"
    assert payload["error"] == "unknown_example"
    assert payload["example_id"] == "missing"
    assert payload["available_examples"] == [
        "agent_playbook",
        "form_fill_case",
        "page_inspection_case",
    ]
    assert payload["fix"]["code"] == "inspect_available_agent_examples"


def test_case_schema_returns_supported_actions_and_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["case", "schema"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "case.schema"
    assert payload["schema_version"] == 1
    assert payload["supported_actions"] == [
        "accessibility-snapshot",
        "blur",
        "blur-role",
        "bounding-box",
        "bounding-box-role",
        "check",
        "check-label",
        "check-role",
        "clear",
        "clear-role",
        "click",
        "click-index",
        "click-role",
        "click-text",
        "console-snapshot",
        "cookie-clear",
        "cookie-delete",
        "cookie-get",
        "cookie-set",
        "count",
        "dialog-snapshot",
        "dispatch-event",
        "double-click",
        "double-click-role",
        "eval",
        "exists",
        "exists-role",
        "fill-label",
        "fill-role",
        "focus",
        "focus-role",
        "form-snapshot",
        "frame-snapshot",
        "get-attribute",
        "get-attribute-role",
        "get-text",
        "get-text-role",
        "get-value",
        "get-value-role",
        "hover",
        "hover-role",
        "inspect",
        "interactive-only-snapshot",
        "interactive-snapshot",
        "link-snapshot",
        "list-snapshot",
        "network-snapshot",
        "open-url",
        "outline-snapshot",
        "page-info",
        "performance-snapshot",
        "press",
        "press-key",
        "press-role",
        "query",
        "right-click",
        "right-click-role",
        "screenshot",
        "scroll",
        "scroll-into-view",
        "scroll-into-view-role",
        "select-label",
        "select-option",
        "select-role",
        "set-file-input",
        "set-value",
        "snapshot",
        "storage-clear",
        "storage-get",
        "storage-remove",
        "storage-set",
        "submit",
        "table-snapshot",
        "text-snapshot",
        "type",
        "uncheck",
        "uncheck-label",
        "uncheck-role",
        "wait-attribute",
        "wait-attribute-role",
        "wait-console",
        "wait-cookie",
        "wait-count",
        "wait-dialog",
        "wait-frame",
        "wait-load-state",
        "wait-network",
        "wait-network-idle",
        "wait-role",
        "wait-selector",
        "wait-state",
        "wait-state-role",
        "wait-storage",
        "wait-text",
        "wait-title",
        "wait-url",
        "wait-value",
        "wait-value-role",
    ]
    assert payload["required_fields"]["type"] == ["selector", "text"]
    assert payload["required_fields"]["fill-label"] == ["label", "text"]
    assert payload["required_fields"]["click-role"] == ["role"]
    assert payload["required_fields"]["click-index"] == ["selector", "index"]
    assert payload["required_fields"]["double-click"] == ["selector"]
    assert payload["required_fields"]["double-click-role"] == ["role"]
    assert payload["required_fields"]["right-click"] == ["selector"]
    assert payload["required_fields"]["right-click-role"] == ["role"]
    assert payload["required_fields"]["select-label"] == ["label"]
    assert payload["required_fields"]["select-role"] == ["role"]
    assert payload["actions"]["select-label"]["required_one_of"] == [
        ["value", "option_label"]
    ]
    assert payload["actions"]["select-role"]["required_one_of"] == [
        ["value", "option_label"]
    ]
    assert payload["required_fields"]["wait-load-state"] == []
    assert payload["required_fields"]["wait-state"] == ["selector", "state"]
    assert payload["required_fields"]["wait-state-role"] == ["role", "state"]
    assert payload["required_fields"]["wait-count"] == ["selector", "count"]
    assert payload["required_fields"]["dialog-snapshot"] == []
    assert payload["required_fields"]["wait-dialog"] == []
    assert payload["required_fields"]["frame-snapshot"] == []
    assert payload["required_fields"]["wait-frame"] == []
    assert payload["required_fields"]["performance-snapshot"] == []
    assert payload["required_fields"]["network-snapshot"] == []
    assert payload["required_fields"]["wait-network"] == []
    assert payload["required_fields"]["console-snapshot"] == []
    assert payload["required_fields"]["wait-console"] == []
    assert payload["required_fields"]["get-attribute"] == ["selector", "name"]
    assert payload["required_fields"]["get-attribute-role"] == [
        "role",
        "attribute",
    ]
    assert payload["required_fields"]["set-value"] == ["selector", "value"]
    assert payload["required_fields"]["set-file-input"] == ["selector", "file"]
    assert payload["required_fields"]["storage-get"] == []
    assert payload["required_fields"]["storage-set"] == ["key", "value"]
    assert payload["required_fields"]["storage-remove"] == ["key"]
    assert payload["required_fields"]["storage-clear"] == []
    assert payload["required_fields"]["wait-storage"] == ["key"]
    assert payload["required_fields"]["cookie-get"] == []
    assert payload["required_fields"]["cookie-set"] == ["name", "value"]
    assert payload["required_fields"]["cookie-delete"] == ["name"]
    assert payload["required_fields"]["cookie-clear"] == []
    assert payload["required_fields"]["wait-cookie"] == ["name"]
    assert payload["required_fields"]["wait-text"] == ["text"]
    assert payload["required_fields"]["wait-title"] == ["title"]
    assert payload["required_fields"]["wait-url"] == ["url"]
    assert payload["required_fields"]["wait-role"] == ["role"]
    assert payload["required_fields"]["screenshot"] == []
    assert payload["actions"]["open-url"]["required_fields"] == ["url"]
    assert "wait_until" in payload["actions"]["open-url"]["optional_fields"]
    assert payload["actions"]["fill-label"]["example_step"] == {
        "action": "fill-label",
        "label": "Email",
        "text": "me@example.com",
    }
    assert "nodes" in payload["actions"]["accessibility-snapshot"]["result_fields"]
    assert payload["actions"]["type"]["example_step"] == {
        "action": "type",
        "selector": "input[name=q]",
        "text": "hello",
    }
    assert "double_clicked" in payload["actions"]["double-click-role"]["result_fields"]
    assert "context_menu" in payload["actions"]["right-click-role"]["result_fields"]
    assert "ready_state" in payload["actions"]["page-info"]["result_fields"]
    assert "requested_url" in payload["actions"]["wait-url"]["result_fields"]
    assert "requested_title" in payload["actions"]["wait-title"]["result_fields"]
    assert "requested_state" in payload["actions"]["wait-load-state"]["result_fields"]
    assert "matched" in payload["actions"]["wait-state-role"]["result_fields"]
    assert "attribute_found" in payload["actions"]["wait-attribute"]["result_fields"]
    assert "requested_count" in payload["actions"]["wait-count"]["result_fields"]
    assert "value_masked" in payload["actions"]["get-value"]["result_fields"]
    assert "attributes" in payload["actions"]["inspect"]["result_fields"]
    assert "bounding_box" in payload["actions"]["bounding-box"]["result_fields"]
    assert "set" in payload["actions"]["set-value"]["result_fields"]
    assert "set" in payload["actions"]["set-file-input"]["result_fields"]
    assert "dispatched" in payload["actions"]["dispatch-event"]["result_fields"]
    assert "pressed" in payload["actions"]["press-key"]["result_fields"]
    assert "clicked" in payload["actions"]["click-index"]["result_fields"]
    assert "candidate_count" in payload["actions"]["wait-role"]["result_fields"]
    assert "area" in payload["actions"]["storage-get"]["optional_fields"]
    assert "value" in payload["actions"]["storage-get"]["result_fields"]
    assert "cleared_count" in payload["actions"]["storage-clear"]["result_fields"]
    assert payload["actions"]["storage-set"]["example_step"]["key"] == "seenIntro"
    assert "document_cookie_scope" in payload["actions"]["cookie-get"]["result_fields"]
    assert "max_age" in payload["actions"]["cookie-set"]["optional_fields"]
    assert "deleted" in payload["actions"]["cookie-delete"]["result_fields"]
    assert "requested_value" in payload["actions"]["wait-cookie"]["result_fields"]
    assert payload["actions"]["wait-storage"]["example_step"]["match"] == "exact"
    assert "submitted" in payload["actions"]["submit"]["result_fields"]
    assert "network_idle" in payload["actions"]["wait-network-idle"]["result_fields"]
    assert "hovered" in payload["actions"]["hover-role"]["result_fields"]
    assert "pressed" in payload["actions"]["press-role"]["result_fields"]
    assert "selected" in payload["actions"]["select-label"]["result_fields"]
    assert "checked" in payload["actions"]["check-role"]["result_fields"]
    assert "text" in payload["actions"]["get-text-role"]["result_fields"]
    assert "exists" in payload["actions"]["exists-role"]["result_fields"]
    assert "scrolled" in payload["actions"]["scroll-into-view-role"]["result_fields"]
    assert "links" in payload["actions"]["link-snapshot"]["result_fields"]
    assert "lists" in payload["actions"]["list-snapshot"]["result_fields"]
    assert "tables" in payload["actions"]["table-snapshot"]["result_fields"]
    assert "texts" in payload["actions"]["text-snapshot"]["result_fields"]
    assert "dialogs" in payload["actions"]["dialog-snapshot"]["result_fields"]
    assert "dialog" in payload["actions"]["wait-dialog"]["result_fields"]
    assert "frames" in payload["actions"]["frame-snapshot"]["result_fields"]
    assert "frame" in payload["actions"]["wait-frame"]["result_fields"]
    assert "resources" in payload["actions"]["performance-snapshot"]["result_fields"]
    assert "entries" in payload["actions"]["network-snapshot"]["result_fields"]
    assert "entry" in payload["actions"]["wait-network"]["result_fields"]
    assert "entries" in payload["actions"]["console-snapshot"]["result_fields"]
    assert "entry" in payload["actions"]["wait-console"]["result_fields"]
    assert "steps" in payload["top_level"]["required_fields"]
    assert "session.create" in payload["top_level"]["target_options"]
    assert "field/path" in payload["top_level"]["step_options"]["expect"]
    assert payload["workflow"]["validate"] == (
        "browser-cli case validate --file case.yaml"
    )


def test_case_schema_names_only(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["case", "schema", "--names-only"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": "case.schema",
        "schema_version": 1,
        "action_count": 98,
        "supported_actions": [
            "accessibility-snapshot",
            "blur",
            "blur-role",
            "bounding-box",
            "bounding-box-role",
            "check",
            "check-label",
            "check-role",
            "clear",
            "clear-role",
            "click",
            "click-index",
            "click-role",
            "click-text",
            "console-snapshot",
            "cookie-clear",
            "cookie-delete",
            "cookie-get",
            "cookie-set",
            "count",
            "dialog-snapshot",
            "dispatch-event",
            "double-click",
            "double-click-role",
            "eval",
            "exists",
            "exists-role",
            "fill-label",
            "fill-role",
            "focus",
            "focus-role",
            "form-snapshot",
            "frame-snapshot",
            "get-attribute",
            "get-attribute-role",
            "get-text",
            "get-text-role",
            "get-value",
            "get-value-role",
            "hover",
            "hover-role",
            "inspect",
            "interactive-only-snapshot",
            "interactive-snapshot",
            "link-snapshot",
            "list-snapshot",
            "network-snapshot",
            "open-url",
            "outline-snapshot",
            "page-info",
            "performance-snapshot",
            "press",
            "press-key",
            "press-role",
            "query",
            "right-click",
            "right-click-role",
            "screenshot",
            "scroll",
            "scroll-into-view",
            "scroll-into-view-role",
            "select-label",
            "select-option",
            "select-role",
            "set-file-input",
            "set-value",
            "snapshot",
            "storage-clear",
            "storage-get",
            "storage-remove",
            "storage-set",
            "submit",
            "table-snapshot",
            "text-snapshot",
            "type",
            "uncheck",
            "uncheck-label",
            "uncheck-role",
            "wait-attribute",
            "wait-attribute-role",
            "wait-console",
            "wait-cookie",
            "wait-count",
            "wait-dialog",
            "wait-frame",
            "wait-load-state",
            "wait-network",
            "wait-network-idle",
            "wait-role",
            "wait-selector",
            "wait-state",
            "wait-state-role",
            "wait-storage",
            "wait-text",
            "wait-title",
            "wait-url",
            "wait-value",
            "wait-value-role",
        ],
    }


def test_case_schema_single_action_and_unknown_action_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["case", "schema", "--action", "wait-selector"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "case.schema"
    assert payload["action"] == "wait-selector"
    assert payload["action_schema"]["required_fields"] == ["selector"]
    assert "state" in payload["action_schema"]["optional_fields"]
    assert payload["action_schema"]["example_step"]["selector"] == "main"

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["case", "schema", "--action", "storage-get"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "case.schema"
    assert payload["action"] == "storage-get"
    assert payload["action_schema"]["required_fields"] == []
    assert payload["action_schema"]["example_step"]["key"] == "featureFlag"

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["case", "schema", "--action", "not-a-case-action"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "case.schema"
    assert payload["error"] == "unknown_case_action"
    assert payload["action"] == "not-a-case-action"
    assert "wait-selector" in payload["available_actions"]
    assert payload["fix"]["code"] == "inspect_available_case_actions"


def test_case_scaffold_returns_valid_json_case_content(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "case",
                "scaffold",
                "--template",
                "page-inspection",
                "--url",
                "https://example.test",
                "--selector",
                "main",
                "--format",
                "json",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "case.scaffold"
    assert payload["template"] == "page-inspection"
    assert payload["output_format"] == "json"
    assert payload["wrote_file"] is False
    assert payload["valid"] is True
    assert payload["errors"] == []
    assert payload["step_count"] == 4
    assert "open-url" in payload["supported_actions"]
    assert payload["case"]["steps"][0]["url"] == "https://example.test"
    assert payload["case"]["steps"][1]["selector"] == "main"
    assert json.loads(payload["content"]) == payload["case"]
    assert payload["next_commands"] == [
        "browser-cli case validate --file case.yaml",
        "browser-cli case run --file case.yaml --close-created-session",
    ]


def test_case_scaffold_writes_valid_yaml_case_file(
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "generated-case.yaml"

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "case",
                "scaffold",
                "--template",
                "form-fill",
                "--text",
                "hello from scaffold",
                "--output",
                str(output),
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "case.scaffold"
    assert payload["template"] == "form-fill"
    assert payload["output"] == str(output)
    assert payload["wrote_file"] is True
    content = output.read_text()
    assert "hello from scaffold" in content
    assert "expression: |" in content
    assert "action: fill-label" in content
    assert "action: click-role" in content
    assert "action: wait-text" in content
    assert "action: get-value-role" in content
    assert "action: type" not in content
    result = validate_case_file(output)
    assert result.valid is True
    assert result.step_count == 8
    assert payload["next_commands"] == [
        f"browser-cli case validate --file {str(output)}",
        f"browser-cli case run --file {str(output)} --close-created-session",
    ]


def test_case_scaffold_refuses_to_overwrite_existing_file(
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "existing.yaml"
    output.write_text("keep me", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["case", "scaffold", "--output", str(output)])

    assert exc_info.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "case.scaffold"
    assert payload["error"] == "file_exists"
    assert payload["output"] == str(output)
    assert output.read_text(encoding="utf-8") == "keep me"


def test_case_validate_select_actions_require_value_or_option_label(
    tmp_path: Any,
) -> None:
    missing = tmp_path / "missing-select-target.json"
    missing.write_text(
        json.dumps({"steps": [{"action": "select-label", "label": "Plan"}]}),
        encoding="utf-8",
    )

    missing_result = validate_case_file(missing)
    assert missing_result.valid is False
    assert "steps[0] missing one of 'value' or 'option_label'" in missing_result.errors

    both = tmp_path / "both-select-targets.json"
    both.write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "action": "select-role",
                        "role": "combobox",
                        "value": "pro",
                        "option_label": "Pro",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    both_result = validate_case_file(both)
    assert both_result.valid is False
    assert "steps[0] must not set both 'value' and 'option_label'" in both_result.errors


def test_case_validate_browser_state_actions_reject_invalid_choices(
    tmp_path: Any,
) -> None:
    invalid = tmp_path / "invalid-browser-state.json"
    invalid.write_text(
        json.dumps(
            {
                "steps": [
                    {"action": "storage-get", "area": "indexeddb"},
                    {"action": "wait-storage", "key": "ready", "state": "visible"},
                    {"action": "wait-cookie", "name": "consent", "match": "glob"},
                    {
                        "action": "cookie-set",
                        "name": "consent",
                        "value": "yes",
                        "same_site": "wide",
                    },
                    {"action": "wait-cookie", "name": "consent", "state": ["present"]},
                ]
            }
        ),
        encoding="utf-8",
    )

    result = validate_case_file(invalid)

    assert result.valid is False
    assert "steps[0].area must be one of 'local', 'session'" in result.errors
    assert "steps[1].state must be one of 'absent', 'present'" in result.errors
    assert "steps[2].match must be one of 'contains', 'exact', 'regex'" in result.errors
    assert "steps[3].same_site must be one of 'lax', 'none', 'strict'" in result.errors
    assert "steps[4].state must be one of 'absent', 'present'" in result.errors


def test_case_validate_diagnostic_wait_actions_reject_invalid_match_modes(
    tmp_path: Any,
) -> None:
    invalid = tmp_path / "invalid-diagnostic-waits.json"
    invalid.write_text(
        json.dumps(
            {
                "steps": [
                    {"action": "network-snapshot", "source": "beacon"},
                    {
                        "action": "wait-network",
                        "url_match": "glob",
                        "source": "resource",
                    },
                    {
                        "action": "wait-console",
                        "match": "wildcard",
                        "source": "stderr",
                        "level": "fatal",
                    },
                    {"action": "wait-dialog", "match": "glob"},
                    {
                        "action": "wait-frame",
                        "url_match": "glob",
                        "text_match": "wildcard",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = validate_case_file(invalid)

    assert result.valid is False
    assert "steps[0].source must be one of 'fetch', 'xhr'" in result.errors
    assert (
        "steps[1].url_match must be one of 'contains', 'exact', 'regex'"
        in result.errors
    )
    assert "steps[1].source must be one of 'fetch', 'xhr'" in result.errors
    assert "steps[2].match must be one of 'contains', 'exact', 'regex'" in result.errors
    assert (
        "steps[2].source must be one of 'console', 'pageerror', 'unhandledrejection'"
    ) in result.errors
    assert (
        "steps[2].level must be one of 'debug', 'error', 'info', 'warn'"
        in result.errors
    )
    assert "steps[3].match must be one of 'contains', 'exact', 'regex'" in result.errors
    assert (
        "steps[4].url_match must be one of 'contains', 'exact', 'regex'"
        in result.errors
    )
    assert (
        "steps[4].text_match must be one of 'contains', 'exact', 'regex'"
        in result.errors
    )


def test_case_validate_dispatch_event_rejects_unknown_events(tmp_path: Any) -> None:
    invalid = tmp_path / "invalid-dispatch-events.json"
    invalid.write_text(
        json.dumps(
            {
                "steps": [
                    {"action": "dispatch-event", "selector": "input", "event": "tap"},
                    {
                        "action": "dispatch-event",
                        "selector": "input",
                        "event": ["input", "magic"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = validate_case_file(invalid)

    assert result.valid is False
    assert any(
        error.startswith("steps[0].event must be one of") for error in result.errors
    )
    assert any(
        error.startswith("steps[1].event must be one of") for error in result.errors
    )


def test_case_validate_expect_requires_object_with_string_paths() -> None:
    errors = cli_module._validate_browser_cli_case_spec(
        {
            "steps": [
                {"action": "wait-text", "text": "Saved", "expect": ["found"]},
                {"action": "wait-text", "text": "Saved", "expect": {"": True}},
                {"action": "wait-text", "text": "Saved", "expect": {1: True}},
            ]
        }
    )

    assert "steps[0].expect must be an object" in errors
    assert "steps[1].expect keys must be non-empty strings" in errors
    assert "steps[2].expect keys must be non-empty strings" in errors


def test_case_expectation_failures_support_nested_paths() -> None:
    failures = cli_module._case_expectation_failures(
        {
            "expect": {
                "found": True,
                "items.0.key": "first",
                "items.1.key": "second",
                "missing.path": "value",
            }
        },
        {
            "found": False,
            "items": [{"key": "first"}],
        },
    )

    assert failures == [
        {
            "path": "found",
            "reason": "mismatch",
            "expected": True,
            "actual": False,
        },
        {
            "path": "items.1.key",
            "reason": "missing",
            "expected": "second",
            "actual": None,
        },
        {
            "path": "missing.path",
            "reason": "missing",
            "expected": "value",
            "actual": None,
        },
    ]
    assert cli_module._case_expectation_message(failures).startswith(
        "Case expectation failed: found expected true, got false"
    )


def test_extended_case_step_uses_semantic_action_expression(tmp_path: Any) -> None:
    class FakePage:
        url = "https://example.test/form"

        def __init__(self) -> None:
            self.expressions: list[str] = []

        def evaluate(self, expression: str) -> dict[str, Any]:
            self.expressions.append(expression)
            return {"found": True, "filled": True}

    page = FakePage()
    result = cli_module._run_browser_cli_case_step(
        page,
        {"action": "fill-label", "label": "Email", "text": "me@example.com"},
        tmp_path,
        0,
    )

    assert result["found"] is True
    assert result["filled"] is True
    assert result["url"] == "https://example.test/form"
    assert "requestedLabel" in page.expressions[0]
    assert "Email" in page.expressions[0]


@pytest.mark.parametrize(
    ("step", "expression_snippet", "expected"),
    [
        (
            {"action": "page-info"},
            "visibilityState",
            {
                "url": "https://example.test/dashboard",
                "ready_state": "complete",
            },
        ),
        (
            {
                "action": "wait-url",
                "url": "/dashboard",
                "match": "contains",
                "timeout_ms": 1000,
                "poll_ms": 50,
            },
            "requestedUrl",
            {
                "found": True,
                "requested_url": "/dashboard",
                "match": "contains",
            },
        ),
        (
            {
                "action": "wait-title",
                "title": "Dashboard",
                "match": "exact",
                "case_sensitive": True,
                "timeout_ms": 1000,
                "poll_ms": 50,
            },
            "requestedTitle",
            {
                "found": True,
                "requested_title": "Dashboard",
                "match": "exact",
                "case_sensitive": True,
            },
        ),
        (
            {
                "action": "wait-load-state",
                "state": "domcontentloaded",
                "timeout_ms": 1000,
                "poll_ms": 50,
            },
            "requestedState",
            {
                "found": True,
                "requested_state": "domcontentloaded",
                "target_state": "interactive",
            },
        ),
    ],
)
def test_extended_case_step_uses_navigation_status_expressions(
    tmp_path: Any,
    step: dict[str, Any],
    expression_snippet: str,
    expected: dict[str, Any],
) -> None:
    class FakePage:
        url = "https://example.test/dashboard"

        def __init__(self) -> None:
            self.expressions: list[str] = []

        def evaluate(self, expression: str) -> dict[str, Any]:
            self.expressions.append(expression)
            if "requestedUrl" in expression:
                return {
                    "found": True,
                    "requested_url": "/dashboard",
                    "match": "contains",
                    "waited_ms": 50,
                }
            if "requestedTitle" in expression:
                return {
                    "found": True,
                    "title": "Dashboard",
                    "requested_title": "Dashboard",
                    "match": "exact",
                    "case_sensitive": True,
                    "waited_ms": 50,
                }
            if "requestedState" in expression:
                return {
                    "found": True,
                    "state": "interactive",
                    "requested_state": "domcontentloaded",
                    "target_state": "interactive",
                    "waited_ms": 50,
                }
            return {
                "url": self.url,
                "title": "Dashboard",
                "ready_state": "complete",
            }

    page = FakePage()
    result = cli_module._run_browser_cli_case_step(page, step, tmp_path, 0)

    assert expression_snippet in page.expressions[0]
    for key, value in expected.items():
        assert result[key] == value
    assert result["url"] == "https://example.test/dashboard"


@pytest.mark.parametrize(
    ("step", "expression_snippet"),
    [
        ({"action": "check", "selector": "#agree"}, "element.checked = true"),
        ({"action": "check-label", "label": "Agree"}, "requestedChecked = true"),
        (
            {"action": "check-role", "role": "checkbox", "name": "Agree"},
            "requestedChecked = true",
        ),
        ({"action": "exists", "selector": ".toast"}, "document.querySelector"),
        (
            {"action": "exists-role", "role": "alert", "name": "Saved"},
            "exists: true",
        ),
        ({"action": "get-text", "selector": ".status"}, "innerText"),
        (
            {"action": "get-text-role", "role": "alert", "name": "Saved"},
            "text_length",
        ),
        ({"action": "hover", "selector": ".menu"}, "mouseover"),
        (
            {"action": "hover-role", "role": "button", "name": "Menu"},
            "hovered",
        ),
        ({"action": "press", "selector": "input", "key": "Enter"}, "KeyboardEvent"),
        (
            {
                "action": "press-role",
                "role": "textbox",
                "name": "Search",
                "key": "Enter",
            },
            "KeyboardEvent",
        ),
        ({"action": "scroll", "y": 400}, "scrollBy"),
        ({"action": "scroll-into-view", "selector": "#details"}, "scrollIntoView"),
        (
            {
                "action": "scroll-into-view-role",
                "role": "button",
                "name": "Save",
            },
            "scrollIntoView",
        ),
        (
            {"action": "select-label", "label": "Plan", "option_label": "Pro"},
            "requestedOptionLabel",
        ),
        (
            {"action": "select-option", "selector": "select", "value": "pro"},
            "requestedValue",
        ),
        (
            {"action": "select-role", "role": "combobox", "value": "pro"},
            "requestedValue",
        ),
        ({"action": "uncheck", "selector": "#subscribe"}, "element.checked = false"),
        (
            {"action": "uncheck-label", "label": "Subscribe"},
            "requestedChecked = false",
        ),
        (
            {"action": "uncheck-role", "role": "checkbox", "name": "Subscribe"},
            "requestedChecked = false",
        ),
    ],
)
def test_extended_case_step_uses_form_and_control_action_expressions(
    tmp_path: Any,
    step: dict[str, Any],
    expression_snippet: str,
) -> None:
    class FakePage:
        url = "https://example.test/form"

        def __init__(self) -> None:
            self.expressions: list[str] = []

        def evaluate(self, expression: str) -> dict[str, Any]:
            self.expressions.append(expression)
            return {"found": True}

    page = FakePage()
    result = cli_module._run_browser_cli_case_step(page, step, tmp_path, 0)

    assert result["found"] is True
    assert result["url"] == "https://example.test/form"
    assert expression_snippet in page.expressions[0]


@pytest.mark.parametrize(
    ("step", "expression_snippet"),
    [
        ({"action": "blur", "selector": "input"}, "element.blur"),
        (
            {"action": "blur-role", "role": "textbox", "name": "Email"},
            "blurred",
        ),
        ({"action": "bounding-box", "selector": "button"}, "bounding_box"),
        (
            {"action": "bounding-box-role", "role": "button", "name": "Submit"},
            "bounding_box",
        ),
        ({"action": "clear", "selector": "input"}, "clearable"),
        (
            {"action": "clear-role", "role": "textbox", "name": "Email"},
            "clearable",
        ),
        ({"action": "count", "selector": ".item"}, "visible_count"),
        ({"action": "dialog-snapshot"}, 'kind: "dialogs"'),
        (
            {"action": "dispatch-event", "selector": "input", "event": ["input"]},
            "requestedEvents",
        ),
        ({"action": "focus", "selector": "input"}, "preventScroll"),
        (
            {"action": "focus-role", "role": "textbox", "name": "Email"},
            "preventScroll",
        ),
        (
            {"action": "frame-snapshot", "selector": "iframe"},
            'kind: "frames"',
        ),
        (
            {"action": "get-attribute", "selector": "button", "name": "disabled"},
            "attribute_value",
        ),
        (
            {
                "action": "get-attribute-role",
                "role": "button",
                "name": "Submit",
                "attribute": "disabled",
            },
            "attribute_value",
        ),
        ({"action": "get-value", "selector": "input"}, "readFormValue"),
        ({"action": "inspect", "selector": "button"}, "selected_options"),
        ({"action": "interactive-only-snapshot"}, 'kind: "interactive"'),
        ({"action": "link-snapshot", "selector": "main"}, 'kind: "links"'),
        ({"action": "list-snapshot", "selector": "main"}, 'kind: "lists"'),
        ({"action": "outline-snapshot", "selector": "main"}, "heading_count"),
        (
            {"action": "performance-snapshot", "max_resources": 5},
            'kind: "performance"',
        ),
        (
            {"action": "network-snapshot", "source": "fetch", "method": "post"},
            "__browserCliNetworkSnapshot",
        ),
        (
            {
                "action": "wait-network",
                "url": "/save",
                "url_match": "regex",
                "source": "fetch",
                "method": "post",
                "status": 201,
                "after_index": 1,
            },
            'kind: "network_wait"',
        ),
        (
            {"action": "console-snapshot", "max_entries": 5},
            "__browserCliConsoleSnapshot",
        ),
        (
            {
                "action": "wait-console",
                "text": "Boom",
                "match": "regex",
                "source": "pageerror",
                "level": "error",
                "after_index": 1,
            },
            'kind: "console_wait"',
        ),
        ({"action": "query", "selector": ".item"}, 'kind: "query"'),
        (
            {"action": "click-index", "selector": ".item", "index": 1},
            "candidates[index]",
        ),
        (
            {"action": "set-value", "selector": "input", "value": "hello"},
            "requestedValue",
        ),
        ({"action": "submit", "selector": "form"}, "requestSubmit"),
        ({"action": "table-snapshot", "selector": "table"}, 'kind: "tables"'),
        ({"action": "text-snapshot", "selector": "main"}, 'kind: "text"'),
        (
            {"action": "wait-attribute", "selector": "button", "name": "disabled"},
            "requestedState",
        ),
        (
            {
                "action": "wait-attribute-role",
                "role": "button",
                "attribute": "disabled",
            },
            "requestedState",
        ),
        ({"action": "wait-count", "selector": ".item", "count": 2}, "requestedCount"),
        ({"action": "press-key", "key": "Escape"}, "targetKind"),
        (
            {
                "action": "wait-dialog",
                "text": "Confirm",
                "match": "exact",
                "modal_only": True,
            },
            'kind: "dialog_wait"',
        ),
        (
            {
                "action": "wait-frame",
                "url": "/checkout",
                "url_match": "contains",
                "readable_only": True,
            },
            'kind: "frame_wait"',
        ),
        ({"action": "wait-network-idle"}, "network_idle"),
        ({"action": "wait-role", "role": "button"}, "requestedRole"),
        (
            {"action": "wait-state", "selector": "button", "state": "enabled"},
            "state_values",
        ),
        (
            {"action": "wait-state-role", "role": "button", "state": "enabled"},
            "state_values",
        ),
        (
            {"action": "wait-value", "selector": "input", "value": "hello"},
            "requested_value",
        ),
        (
            {"action": "wait-value-role", "role": "textbox", "value": "hello"},
            "requested_value",
        ),
    ],
)
def test_extended_case_step_uses_selector_state_and_value_expressions(
    tmp_path: Any,
    step: dict[str, Any],
    expression_snippet: str,
) -> None:
    class FakePage:
        url = "https://example.test/state"

        def __init__(self) -> None:
            self.expressions: list[str] = []

        def evaluate(self, expression: str) -> dict[str, Any]:
            self.expressions.append(expression)
            return {"found": True}

    page = FakePage()
    result = cli_module._run_browser_cli_case_step(page, step, tmp_path, 0)

    assert result["found"] is True
    assert result["url"] == "https://example.test/state"
    assert expression_snippet in page.expressions[0]


def test_extended_case_step_uses_set_file_input_expression(tmp_path: Any) -> None:
    upload = tmp_path / "upload.txt"
    upload.write_text("hello from case file", encoding="utf-8")

    class FakePage:
        url = "https://example.test/upload"

        def __init__(self) -> None:
            self.expressions: list[str] = []

        def evaluate(self, expression: str) -> dict[str, Any]:
            self.expressions.append(expression)
            return {"found": True, "file_input": True, "set": True}

    page = FakePage()
    result = cli_module._run_browser_cli_case_step(
        page,
        {
            "action": "set-file-input",
            "selector": "input[type=file]",
            "file": str(upload),
        },
        tmp_path,
        0,
    )

    assert result["set"] is True
    assert result["url"] == "https://example.test/upload"
    assert "requestedFiles" in page.expressions[0]
    assert "upload.txt" in page.expressions[0]


@pytest.mark.parametrize(
    ("step", "expression_snippet"),
    [
        (
            {"action": "storage-get", "area": "local", "key": "featureFlag"},
            "requestedKey",
        ),
        (
            {"action": "storage-set", "key": "seenIntro", "value": "true"},
            "storage.setItem",
        ),
        (
            {"action": "storage-remove", "area": "session", "key": "draft"},
            "storage.removeItem",
        ),
        (
            {"action": "storage-clear", "area": "session", "prefix": "tmp:"},
            "cleared_count",
        ),
        (
            {
                "action": "wait-storage",
                "key": "seenIntro",
                "value": "true",
                "match": "exact",
                "timeout_ms": 1000,
                "poll_ms": 50,
            },
            "requestedState",
        ),
        ({"action": "cookie-get", "name": "consent"}, "documentCookieScope"),
        (
            {
                "action": "cookie-set",
                "name": "consent",
                "value": "yes",
                "path": "/",
                "same_site": "lax",
                "secure": True,
            },
            "document.cookie = assignment",
        ),
        ({"action": "cookie-delete", "name": "consent", "path": "/"}, "maxAge: 0"),
        ({"action": "cookie-clear", "prefix": "tmp:"}, "matched_count"),
        (
            {
                "action": "wait-cookie",
                "name": "consent",
                "value": "yes",
                "match": "exact",
                "timeout_ms": 1000,
                "poll_ms": 50,
            },
            "requestedState",
        ),
    ],
)
def test_extended_case_step_uses_browser_state_expressions(
    tmp_path: Any,
    step: dict[str, Any],
    expression_snippet: str,
) -> None:
    class FakePage:
        url = "https://example.test/state"

        def __init__(self) -> None:
            self.expressions: list[str] = []

        def evaluate(self, expression: str) -> dict[str, Any]:
            self.expressions.append(expression)
            return {"found": True}

    page = FakePage()
    result = cli_module._run_browser_cli_case_step(page, step, tmp_path, 0)

    assert result["found"] is True
    assert result["url"] == "https://example.test/state"
    assert expression_snippet in page.expressions[0]


def test_case_run_expectation_failure_marks_summary_failed(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import playwright.sync_api as sync_api

    case_file = tmp_path / "expectation-case.json"
    case_file.write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "action": "wait-text",
                        "text": "Saved",
                        "expect": {"found": True},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class FakeBrowser:
        contexts = [object()]

        def close(self) -> None:
            pass

    class FakeChromium:
        def connect_over_cdp(self, connect_url: str) -> FakeBrowser:
            assert connect_url == "wss://browser.example.test/devtools?api_key=secret"
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        def __enter__(self) -> "FakePlaywright":
            return self

        def __exit__(self, *args: object) -> None:
            pass

    class FakePage:
        url = "https://example.test"

    monkeypatch.setattr(sync_api, "sync_playwright", lambda: FakePlaywright())
    monkeypatch.setattr(cli_module, "LexmountBrowserAdmin", lambda: object())
    monkeypatch.setattr(cli_module, "get_or_create_page", lambda _: FakePage())
    monkeypatch.setattr(
        cli_module,
        "resolve_case_target",
        lambda *_: SimpleNamespace(
            connect_url="wss://browser.example.test/devtools?api_key=secret",
            session={"session_id": "s1"},
            created_session=False,
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "_run_browser_cli_case_step",
        lambda *_: {"found": False, "url": "https://example.test"},
    )

    summary = cli_module.run_browser_cli_case_file(
        file=case_file,
        artifacts_dir=tmp_path / "artifacts",
    )

    assert summary.ok is False
    assert summary.steps[0].ok is False
    assert summary.steps[0].error == "case_expectation_failed"
    assert summary.steps[0].message == (
        "Case expectation failed: found expected true, got false"
    )
    assert summary.steps[0].result == {
        "found": False,
        "url": "https://example.test",
        "expectation_failures": [
            {
                "path": "found",
                "reason": "mismatch",
                "expected": True,
                "actual": False,
            }
        ],
    }


def test_case_run_masks_connect_url_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_run_browser_cli_case_file(**_: Any) -> Any:
        return cli_module.CaseRunSummary(
            ok=True,
            file="case.yaml",
            run_id="run-1",
            artifacts_dir="/tmp/browser-cli-case",
            events_path="/tmp/browser-cli-case/events.jsonl",
            connect_url="wss://browser.example.test/devtools?api_key=secret",
            session={
                "session_id": "s1",
                "connect_url": "wss://browser.example.test/devtools?api_key=secret",
            },
            steps=[],
        )

    monkeypatch.setattr(
        cli_module, "run_browser_cli_case_file", fake_run_browser_cli_case_file
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["case", "run", "--file", "case.yaml"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["connect_url"] == "wss://browser.example.test/devtools?api_key=***"
    assert payload["connect_url_masked"] is True
    assert payload["session"]["connect_url"].endswith("api_key=***")


def test_commands_catalog_returns_connect_from_codex_site_requirements_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "connect_from_codex_site_requirements"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "connect_from_codex_site_requirements"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "inspect_scope_catalog",
        "inspect_site_requirements",
        "verify_manual_handoff",
        "verify_device_code_handoff",
        "doctor_after_credentials",
    ]
    assert steps[0]["command"] == "browser-cli auth scopes --include-site-contract"
    assert "browser_site_contract.scope_ui_fields" in steps[0]["read"]
    assert steps[1]["command"] == "browser-cli auth connect-requirements"
    assert "connect_from_codex.site_capabilities" in steps[1]["read"]
    assert "required_device_code_endpoints" in steps[1]["read"]
    assert steps[2]["optional"] is True
    assert "handoff.setup_blocks" in steps[2]["read"]
    assert steps[3]["command"] == "browser-cli auth login --device-code"
    assert "device_code.required_endpoints" in steps[3]["read"]
    assert steps[4]["command"] == "browser-cli doctor --json"
    assert (
        "browser-cli auth scopes --include-site-contract"
        in payload["agent_entrypoints"]["connect_from_codex_site_requirements"]
    )
    assert (
        "browser-cli auth connect-requirements"
        in payload["agent_entrypoints"]["connect_from_codex_site_requirements"]
    )


def test_commands_catalog_returns_form_interaction_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "form_interaction"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "form_interaction"
    assert "agent_workflows" not in payload
    assert payload["workflow"]["steps"][0]["id"] == "inspect_action_guide"
    assert payload["workflow"]["steps"][0]["command"] == (
        "browser-cli action guide --task form_interaction"
    )
    assert "guide.preferred_commands" in payload["workflow"]["steps"][0]["read"]
    assert payload["workflow"]["steps"][1]["id"] == "inspect_form"
    assert payload["workflow"]["steps"][1]["command"] == (
        "browser-cli action form-snapshot --session-id <session_id> --selector form"
    )
    assert payload["workflow"]["steps"][2]["id"] == "fill_labeled_field"
    assert "result.filled" in payload["workflow"]["steps"][2]["read"]
    assert (
        "browser-cli action fill-role"
        in payload["workflow"]["steps"][2]["alternative_commands"][0]
    )
    assert payload["workflow"]["steps"][5]["id"] == "wait_submit_ready"
    assert (
        "browser-cli action wait-state-role"
        in payload["workflow"]["steps"][5]["command"]
    )
    assert "result.state_values" in payload["workflow"]["steps"][5]["read"]
    assert payload["workflow"]["steps"][-1]["id"] == "verify_result"
    assert payload["workflow"]["steps"][-1]["optional"] is True
    assert (
        "browser-cli action guide --task form_interaction"
        in payload["agent_entrypoints"]["form_interaction"][0]
    )


def test_commands_catalog_returns_file_upload_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "file_upload"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "file_upload"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "inspect_action_guide",
        "inspect_upload_controls",
        "attach_files",
        "verify_upload_state",
        "submit_if_requested",
    ]
    assert steps[0]["command"] == "browser-cli action guide --task file_upload"
    assert "guide.read_fields" in steps[0]["read"]
    assert steps[1]["command"] == (
        "browser-cli action form-snapshot --session-id <session_id> --selector form"
    )
    assert "result.fields" in steps[1]["read"]
    assert "browser-cli action query" in steps[1]["fallback_commands"][0]
    assert steps[2]["agent_action"] is True
    assert "browser-cli action set-file-input" in steps[2]["command"]
    assert "result.requested_files" in steps[2]["read"]
    assert "result.file_count" in steps[2]["read"]
    assert "result.dispatched_events" in steps[2]["read"]
    assert "browser-cli action set-file-input" in steps[2]["alternative_commands"][0]
    assert steps[3]["command"] == (
        'browser-cli action inspect --session-id <session_id> --selector "input[type=file]"'
    )
    assert "result.file_input" in steps[3]["read"]
    assert steps[-1]["id"] == "submit_if_requested"
    assert steps[-1]["optional"] is True
    assert steps[-1]["user_requested_only"] is True


def test_commands_catalog_returns_dialog_frame_handling_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "dialog_frame_handling"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "dialog_frame_handling"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "inspect_action_guide",
        "inspect_page_context",
        "inspect_or_wait_dialog",
        "handle_dialog_control",
        "inspect_or_wait_frame",
        "verify_result",
    ]
    assert steps[0]["command"] == (
        "browser-cli action guide --task dialog_frame_handling"
    )
    assert "guide.read_fields" in steps[0]["read"]
    assert "browser-cli action wait-dialog" in steps[2]["command"]
    assert "result.controls" in steps[2]["read"]
    assert "browser-cli action click-role" in steps[3]["command"]
    assert steps[3]["agent_action"] is True
    assert "browser-cli action wait-frame" in steps[4]["command"]
    assert "result.same_origin" in steps[4]["read"]
    assert "result.read_error" in steps[4]["read"]
    assert (
        "browser-cli action guide --task dialog_frame_handling"
        in payload["agent_entrypoints"]["dialog_frame_handling"][0]
    )


def test_commands_catalog_returns_device_code_auth_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "device_code_auth"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "device_code_auth"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "request_device_code",
        "fallback_manual_env",
        "verify_auth_status",
        "doctor",
    ]
    assert steps[0]["command"] == "browser-cli auth login --device-code"
    assert "available=false" in steps[0]["success_condition"]
    assert "saved credentials" in steps[0]["success_condition"]
    assert "device_code.required_endpoints" in steps[0]["read"]
    assert "device_code.verification_uri_complete" in steps[0]["read"]
    assert "connect_from_codex.required_runtime_auth" in steps[0]["read"]
    assert "polling.status" in steps[0]["read"]
    assert "credentials.device_token.valid" in steps[0]["read"]
    assert "fallback_handoff.setup_blocks" in steps[0]["read"]
    assert steps[1]["optional"] is True
    assert "manual_env_available" in steps[1]["read"]
    assert "connect_from_codex.required_runtime_auth" in steps[1]["read"]
    assert "runtime_auth.usable" in steps[2]["read"]
    assert "runtime_auth.bearer_runtime.required_support" in steps[2]["read"]
    assert "device_token.valid" in steps[2]["read"]
    assert steps[3]["command"] == "browser-cli doctor --json"
    assert (
        "browser-cli auth login --device-code"
        in payload["agent_entrypoints"]["device_code_auth"]
    )


def test_commands_catalog_returns_scoped_token_lifecycle_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "scoped_token_lifecycle"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "scoped_token_lifecycle"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "status_scoped_token",
        "inspect_scope_catalog",
        "inspect_required_scopes",
        "refresh_if_needed",
        "verify_browser_readiness",
        "logout_or_revoke_when_requested",
    ]
    assert steps[0]["command"] == "browser-cli auth status"
    assert "device_token.valid" in steps[0]["read"]
    assert "runtime_auth.usable" in steps[0]["read"]
    assert "runtime_auth.bearer_runtime.required_support" in steps[0]["read"]
    assert steps[1]["command"] == ("browser-cli auth scopes --scope browser:actions")
    assert "scopes[0].permissions" in steps[1]["read"]
    assert steps[2]["command"] == (
        "browser-cli auth token-info --required-scope browser.actions:run"
    )
    assert "scope_check.satisfied" in steps[2]["read"]
    assert steps[3]["optional"] is True
    assert "refresh_available" in steps[3]["read"]
    assert "remote_refresh.status_code" in steps[3]["read"]
    assert "credentials.saved" in steps[3]["read"]
    assert steps[4]["success_condition"] == (
        "ok=true and ready_for_browser_actions=true"
    )
    assert steps[5]["user_requested_only"] is True
    assert "revoke_available" in steps[5]["read"]
    assert "remote_revoke.status_code" in steps[5]["read"]
    assert (
        "browser-cli auth scopes --scope browser:actions"
        in payload["agent_entrypoints"]["scoped_token_lifecycle"]
    )
    assert (
        "browser-cli auth token-info --required-scope browser.actions:run"
        in payload["agent_entrypoints"]["scoped_token_lifecycle"]
    )


def test_commands_catalog_returns_case_file_task_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "case_file_task"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "case_file_task"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "inspect_case_commands",
        "inspect_case_schema",
        "inspect_semantic_case_action",
        "inspect_form_case_example",
        "scaffold_case_file",
        "scaffold_form_case_file",
        "validate_case_file",
        "run_case_file",
    ]
    assert steps[0]["command"] == "browser-cli commands --group case"
    assert "command_count" in steps[0]["read"]
    assert steps[1]["command"] == "browser-cli case schema"
    assert "supported_actions" in steps[1]["read"]
    assert "actions" in steps[1]["read"]
    assert steps[2]["command"] == "browser-cli case schema --action fill-label"
    assert "action_schema.example_step" in steps[2]["read"]
    assert steps[3]["command"] == (
        "browser-cli example get --id form_fill_case --metadata-only"
    )
    assert "example.content_command" in steps[3]["read"]
    assert steps[4]["command"] == (
        "browser-cli case scaffold --template page-inspection --url <url> --output case.yaml"
    )
    assert steps[4]["optional"] is True
    assert "next_commands" in steps[4]["read"]
    assert steps[5]["command"] == (
        "browser-cli case scaffold --template form-fill --output form-case.yaml"
    )
    assert steps[5]["optional"] is True
    assert "case.steps" in steps[5]["read"]
    assert steps[6]["success_condition"] == "valid=true"
    assert "step_count" in steps[6]["read"]
    assert steps[7]["command"] == (
        "browser-cli case run --file <case.yaml> --close-created-session"
    )
    assert "artifacts_dir" in steps[7]["read"]
    assert "steps" in steps[7]["on_failure_read"]
    assert "browser-cli case schema" in payload["agent_entrypoints"]["case_file_task"]
    assert (
        "browser-cli case schema --action fill-label"
        in payload["agent_entrypoints"]["case_file_task"]
    )
    assert (
        "browser-cli example get --id form_fill_case --metadata-only"
        in payload["agent_entrypoints"]["case_file_task"]
    )
    assert (
        "browser-cli case scaffold --template page-inspection --url <url> --output case.yaml"
        in payload["agent_entrypoints"]["case_file_task"]
    )
    assert (
        "browser-cli case scaffold --template form-fill --output form-case.yaml"
        in payload["agent_entrypoints"]["case_file_task"]
    )
    assert (
        "browser-cli case validate --file <case.yaml>"
        in payload["agent_entrypoints"]["case_file_task"]
    )


def test_commands_catalog_returns_session_recovery_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "session_recovery"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "session_recovery"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "list_active_sessions",
        "inspect_session",
        "keepalive_session",
        "close_stale_session",
        "create_replacement_session",
    ]
    assert steps[0]["command"] == "browser-cli session list --status active"
    assert "sessions" in steps[0]["read"]
    assert steps[1]["optional"] is True
    assert "session.session_id" in steps[1]["read"]
    assert "final_status" in steps[2]["read"]
    assert steps[3]["user_requested_only"] is True
    assert "closed" in steps[3]["read"]
    assert "context_reuse.availability" in steps[4]["read"]
    assert "browser-cli doctor --json" in steps[4]["fallback_commands"][0]
    assert (
        "browser-cli session keepalive --session-id <session_id> --duration 60 --stop-on-inactive"
        in payload["agent_entrypoints"]["session_recovery"]
    )


def test_commands_catalog_returns_interactive_targeting_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "interactive_targeting"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "interactive_targeting"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "inspect_action_guide",
        "inspect_interactive_targets",
        "inspect_accessibility_context",
        "choose_click_method",
        "wait_target_ready",
        "activate_target",
        "verify_after_click",
    ]
    assert steps[0]["command"] == (
        "browser-cli action guide --task interactive_targeting"
    )
    assert "guide.inspect_commands" in steps[0]["read"]
    assert steps[1]["command"] == (
        "browser-cli action interactive-snapshot --session-id <session_id> --max-nodes 80"
    )
    assert "result.nodes" in steps[1]["read"]
    assert steps[2]["optional"] is True
    assert steps[3]["agent_action"] is True
    assert steps[3]["selection_order"] == [
        "exists-role",
        "get-text-role",
        "bounding-box-role",
        "click-role",
        "hover-role",
        "press-role",
        "scroll-into-view-role",
        "click-text",
        "click-index",
    ]
    assert "browser-cli action exists-role" in steps[3]["preferred_commands"][0]
    assert "browser-cli action get-text-role" in steps[3]["preferred_commands"][1]
    assert "browser-cli action bounding-box-role" in steps[3]["preferred_commands"][2]
    assert "browser-cli action click-role" in steps[3]["preferred_commands"][3]
    assert "browser-cli action hover-role" in steps[3]["preferred_commands"][4]
    assert "browser-cli action click-text" in steps[5]["alternative_commands"][3]
    assert steps[-1]["id"] == "verify_after_click"
    assert "browser-cli action wait-url" in steps[-1]["fallback_commands"][0]


def test_commands_catalog_returns_menu_keyboard_flow_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "menu_keyboard_flow"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "menu_keyboard_flow"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "inspect_action_guide",
        "inspect_interactive_targets",
        "open_or_focus_menu",
        "verify_menu_state",
        "inspect_menu_items",
        "send_keyboard_input",
        "verify_result",
    ]
    assert steps[0]["command"] == "browser-cli action guide --task menu_keyboard_flow"
    assert "guide.read_fields" in steps[0]["read"]
    assert "browser-cli action interactive-snapshot" in steps[1]["command"]
    assert "browser-cli action hover-role" in steps[2]["command"]
    assert steps[2]["agent_action"] is True
    assert "result.hovered" in steps[2]["read"]
    assert "browser-cli action wait-attribute-role" in steps[3]["command"]
    assert "result.requested_value" in steps[3]["read"]
    assert "browser-cli action list-snapshot" in steps[4]["command"]
    assert "result.items" in steps[4]["read"]
    assert "browser-cli action press-key" in steps[5]["command"]
    assert "result.keydown_accepted" in steps[5]["read"]
    assert steps[-1]["id"] == "verify_result"
    assert "browser-cli action wait-url" in steps[-1]["fallback_commands"][0]
    assert (
        "browser-cli action guide --task menu_keyboard_flow"
        in payload["agent_entrypoints"]["menu_keyboard_flow"][0]
    )


def test_commands_catalog_returns_state_waits_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "state_waits"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "state_waits"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "inspect_action_guide",
        "inspect_current_state",
        "choose_wait_condition",
        "wait_for_state",
        "verify_after_wait",
    ]
    assert steps[0]["command"] == "browser-cli action guide --task state_waits"
    assert "guide.preferred_commands" in steps[0]["read"]
    assert steps[1]["command"] == (
        "browser-cli action page-info --session-id <session_id>"
    )
    assert "ready_state" in steps[1]["read"]
    assert steps[2]["agent_action"] is True
    assert steps[2]["selection_order"][:4] == [
        "wait-load-state",
        "wait-url",
        "wait-state-role",
        "wait-attribute-role",
    ]
    assert "wait-storage" in steps[2]["selection_order"]
    assert (
        "browser-cli action wait-network --session-id <session_id> --url <path> --url-match contains"
        in steps[2]["preferred_commands"]
    )
    assert steps[3]["agent_action"] is True
    assert "result.attribute_found" in steps[3]["read"]
    assert "browser-cli action wait-count" in steps[3]["fallback_commands"][1]
    assert steps[-1]["id"] == "verify_after_wait"
    assert "visibility_state" in steps[-1]["read"]


def test_commands_catalog_returns_content_extraction_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "content_extraction"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "content_extraction"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "inspect_action_guide",
        "inspect_page_info",
        "choose_extraction_surface",
        "extract_content",
        "verify_extraction_bounds",
    ]
    assert steps[0]["command"] == ("browser-cli action guide --task content_extraction")
    assert "guide.read_fields" in steps[0]["read"]
    assert steps[1]["command"] == (
        "browser-cli action page-info --session-id <session_id>"
    )
    assert "ready_state" in steps[1]["read"]
    assert steps[2]["agent_action"] is True
    assert steps[2]["selection_order"][:4] == [
        "outline-snapshot",
        "text-snapshot",
        "link-snapshot",
        "table-snapshot",
    ]
    assert "browser-cli action text-snapshot" in steps[2]["preferred_commands"][1]
    assert steps[3]["agent_action"] is True
    assert "result.links" in steps[3]["read"]
    assert "result.landmarks" in steps[3]["read"]
    assert "browser-cli action snapshot" in steps[3]["fallback_commands"][0]
    assert steps[-1]["id"] == "verify_extraction_bounds"
    assert "result.truncated" in steps[-1]["read"]
    assert "browser-cli action table-snapshot" in steps[-1]["fallback_commands"][1]


def test_commands_catalog_returns_browser_state_management_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "browser_state_management"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "browser_state_management"
    assert "agent_workflows" not in payload
    steps = payload["workflow"]["steps"]
    assert [step["id"] for step in steps] == [
        "inspect_action_guide",
        "inspect_page_info",
        "read_existing_state",
        "modify_state",
        "wait_for_state",
        "cleanup_state",
    ]
    assert steps[0]["command"] == (
        "browser-cli action guide --task browser_state_management"
    )
    assert "guide.read_fields" in steps[0]["read"]
    assert steps[1]["command"] == (
        "browser-cli action page-info --session-id <session_id>"
    )
    assert "ready_state" in steps[1]["read"]
    assert "result.items" in steps[2]["read"]
    assert "browser-cli action cookie-get" in steps[2]["alternative_commands"][0]
    assert steps[3]["agent_action"] is True
    assert "result.previous_value" in steps[3]["read"]
    assert "result.document_cookie_scope" in steps[3]["read"]
    assert "browser-cli action cookie-set" in steps[3]["alternative_commands"][0]
    assert "result.requested_value" in steps[4]["read"]
    assert "browser-cli action wait-cookie" in steps[4]["alternative_commands"][0]
    assert steps[-1]["id"] == "cleanup_state"
    assert steps[-1]["optional"] is True
    assert steps[-1]["user_requested_only"] is True
    assert "result.cleared_count" in steps[-1]["read"]


def test_commands_catalog_returns_page_diagnostics_workflow(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "page_diagnostics"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["workflow_id"] == "page_diagnostics"
    assert "agent_workflows" not in payload
    assert payload["workflow"]["steps"][0]["id"] == "inspect_action_guide"
    assert payload["workflow"]["steps"][0]["command"] == (
        "browser-cli action guide --task page_diagnostics"
    )
    assert payload["workflow"]["steps"][1]["id"] == "page_info_before"
    assert payload["workflow"]["steps"][2]["command"] == (
        "browser-cli action set-viewport --session-id <session_id> --width 1280 --height 720"
    )
    assert payload["workflow"]["steps"][2]["optional"] is True
    assert "result.window_viewport" in payload["workflow"]["steps"][2]["read"]
    assert payload["workflow"]["steps"][3]["command"] == (
        "browser-cli action console-snapshot --session-id <session_id> --install-only"
    )
    assert payload["workflow"]["steps"][5]["id"] == "reproduce_issue"
    assert payload["workflow"]["steps"][5]["agent_action"] is True
    assert payload["workflow"]["steps"][6]["id"] == "read_console_entries"
    assert "result.entries" in payload["workflow"]["steps"][6]["read"]
    assert payload["workflow"]["steps"][-1]["id"] == "capture_visible_state"
    assert (
        "browser-cli action screenshot-role"
        in payload["workflow"]["steps"][-1]["fallback_commands"][0]
    )
    assert (
        "browser-cli action screenshot-selector"
        in payload["workflow"]["steps"][-1]["fallback_commands"][1]
    )
    assert (
        "browser-cli action screenshot"
        in payload["workflow"]["steps"][-1]["fallback_commands"][2]
    )


def test_commands_catalog_fails_unknown_workflow_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--workflow", "missing"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "commands"
    assert payload["error"] == "unknown_workflow"
    assert payload["workflow"] == "missing"
    assert payload["available_workflows"] == [
        "browser_state_management",
        "case_file_task",
        "connect_from_codex_auth",
        "connect_from_codex_site_requirements",
        "content_extraction",
        "device_code_auth",
        "dialog_frame_handling",
        "file_upload",
        "form_interaction",
        "interactive_targeting",
        "link_navigation",
        "menu_keyboard_flow",
        "mouse_interaction",
        "navigation_flow",
        "one_off_page_task",
        "page_diagnostics",
        "persistent_login_state",
        "scoped_token_lifecycle",
        "semantic_waits",
        "session_recovery",
        "setup_and_verify",
        "state_waits",
        "visual_capture",
    ]
    assert payload["fix"] == {
        "code": "inspect_available_agent_workflows",
        "commands": ["browser-cli commands --workflows-only"],
        "guidance": [
            "Choose one of available_workflows, then rerun commands with that --workflow value."
        ],
    }


def test_commands_catalog_fails_unknown_group_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--group", "missing"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "commands"
    assert payload["error"] == "unknown_group"
    assert payload["group"] == "missing"
    assert "action" in payload["available_groups"]
    assert "auth" in payload["available_groups"]
    assert "version" in payload["available_groups"]
    assert payload["fix"] == {
        "code": "inspect_available_command_groups",
        "commands": [
            "browser-cli commands",
            "browser-cli commands --names-only",
        ],
        "guidance": [
            "Choose one of available_groups, then rerun commands with that --group value."
        ],
    }


def test_commands_catalog_filters_group_and_names_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["commands", "--group", "action", "--names-only"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": "commands",
        "schema_version": 1,
        "group": "action",
        "command_count": len(payload["commands"]),
        "commands": payload["commands"],
    }
    assert "action.open-url" in payload["commands"]
    assert "action.page-info" in payload["commands"]
    assert "action.set-viewport" in payload["commands"]
    assert "action.screenshot-selector" in payload["commands"]
    assert "action.screenshot-role" in payload["commands"]
    assert "action.wait-title" in payload["commands"]
    assert "action.press-key" in payload["commands"]
    assert "action.link-snapshot" in payload["commands"]
    assert "action.table-snapshot" in payload["commands"]
    assert "action.list-snapshot" in payload["commands"]
    assert "action.text-snapshot" in payload["commands"]
    assert "action.dialog-snapshot" in payload["commands"]
    assert "action.wait-dialog" in payload["commands"]
    assert "action.frame-snapshot" in payload["commands"]
    assert "action.wait-frame" in payload["commands"]
    assert "action.performance-snapshot" in payload["commands"]
    assert "action.network-snapshot" in payload["commands"]
    assert "action.wait-network" in payload["commands"]
    assert "action.console-snapshot" in payload["commands"]
    assert "action.wait-console" in payload["commands"]
    assert "action.outline-snapshot" in payload["commands"]
    assert "action.interactive-snapshot" in payload["commands"]
    assert "action.interactive-only-snapshot" in payload["commands"]
    assert "auth.login" not in payload["commands"]
    assert all(command.startswith("action.") for command in payload["commands"])


def _checks_by_name(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {check["name"]: check for check in payload["checks"]}


def test_doctor_checks_install_env_direct_url_and_api(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.delenv("LEXMOUNT_REGION", raising=False)
    monkeypatch.setattr(
        "browser_cli.cli._package_version",
        lambda distribution: {
            "browser-cli": "0.1.0",
            "lex-browser-runtime": "1.2.3",
        }.get(distribution),
    )
    monkeypatch.setattr(
        "browser_cli.cli.shutil.which",
        lambda name: "/usr/local/bin/browser-cli" if name == "browser-cli" else None,
    )

    class FakeAdmin:
        def list_sessions(self, *, status: str | None) -> DummyModel:
            assert status is None
            return DummyModel(
                {
                    "count": 2,
                    "status_filter": status,
                    "sessions": [],
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "doctor"
    assert payload["status"] == "ok"
    assert payload["failed"] == 0
    assert payload["warnings"] == 0
    assert payload["failed_checks"] == []
    assert payload["warning_checks"] == []
    assert payload["skipped_checks"] == []
    assert payload["ready_for_browser_actions"] is True
    assert payload["repair_plan"] == {
        "required": False,
        "recommended": False,
        "commands": [],
        "env": [],
        "guidance": [],
        "fixes": [],
    }
    assert "secret" not in json.dumps(payload)

    checks = _checks_by_name(payload)
    assert checks["python_runtime"]["status"] == "pass"
    assert checks["python_runtime"]["executable"]
    assert checks["browser_cli_executable"] == {
        "name": "browser_cli_executable",
        "status": "pass",
        "message": "browser-cli executable is available on PATH",
        "path": "/usr/local/bin/browser-cli",
    }
    assert checks["browser_cli"]["version"] == "0.1.0"
    assert checks["browser_cli"]["version_known"] is True
    assert checks["browser_cli"]["version_source"] == "package_metadata"
    assert checks["lex_browser_runtime"]["version"] == "1.2.3"
    assert checks["command_catalog"]["status"] == "pass"
    assert checks["command_catalog"]["schema_version"] == 1
    assert checks["command_catalog"]["workflow_count"] == 23
    assert checks["command_catalog"]["required_workflows"] == [
        "setup_and_verify",
        "connect_from_codex_site_requirements",
        "connect_from_codex_auth",
        "device_code_auth",
        "scoped_token_lifecycle",
        "session_recovery",
        "one_off_page_task",
        "navigation_flow",
        "link_navigation",
        "case_file_task",
        "persistent_login_state",
        "browser_state_management",
        "form_interaction",
        "file_upload",
        "dialog_frame_handling",
        "interactive_targeting",
        "mouse_interaction",
        "visual_capture",
        "semantic_waits",
        "menu_keyboard_flow",
        "content_extraction",
        "state_waits",
        "page_diagnostics",
    ]
    assert checks["command_catalog"]["missing_required_workflows"] == []
    assert checks["command_catalog"]["required_workflow_steps"]["setup_and_verify"] == [
        "auth_status",
        "doctor",
        "smoke_session",
    ]
    assert checks["command_catalog"]["required_workflow_steps"][
        "connect_from_codex_auth"
    ] == [
        "auth_status",
        "inspect_scope_catalog",
        "auth_login",
        "export_env",
        "doctor",
    ]
    assert checks["command_catalog"]["required_workflow_steps"][
        "connect_from_codex_site_requirements"
    ] == [
        "inspect_scope_catalog",
        "inspect_site_requirements",
        "verify_manual_handoff",
        "verify_device_code_handoff",
        "doctor_after_credentials",
    ]
    assert checks["command_catalog"]["required_workflow_steps"]["device_code_auth"] == [
        "request_device_code",
        "fallback_manual_env",
        "verify_auth_status",
        "doctor",
    ]
    assert checks["command_catalog"]["required_workflow_steps"][
        "scoped_token_lifecycle"
    ] == [
        "status_scoped_token",
        "inspect_scope_catalog",
        "inspect_required_scopes",
        "refresh_if_needed",
        "verify_browser_readiness",
        "logout_or_revoke_when_requested",
    ]
    assert checks["command_catalog"]["required_workflow_steps"]["session_recovery"] == [
        "list_active_sessions",
        "inspect_session",
        "keepalive_session",
        "close_stale_session",
        "create_replacement_session",
    ]
    assert checks["command_catalog"]["required_workflow_steps"][
        "one_off_page_task"
    ] == [
        "create_session",
        "open_url",
        "find_targets",
        "close_session",
    ]
    assert checks["command_catalog"]["required_workflow_steps"]["navigation_flow"] == [
        "inspect_action_guide",
        "inspect_current_page",
        "choose_navigation_action",
        "run_navigation_action",
        "verify_navigation_result",
    ]
    assert checks["command_catalog"]["required_workflow_steps"]["link_navigation"] == [
        "inspect_action_guide",
        "inspect_current_page",
        "inspect_links",
        "choose_link_target",
        "activate_link",
        "verify_navigation_result",
    ]
    assert checks["command_catalog"]["required_workflow_steps"]["case_file_task"] == [
        "inspect_case_commands",
        "inspect_case_schema",
        "inspect_semantic_case_action",
        "inspect_form_case_example",
        "scaffold_case_file",
        "scaffold_form_case_file",
        "validate_case_file",
        "run_case_file",
    ]
    assert checks["command_catalog"]["required_workflow_steps"][
        "persistent_login_state"
    ] == [
        "dry_run_context_pick",
        "inspect_context_status",
        "create_session_with_context",
        "close_session",
    ]
    assert checks["command_catalog"]["required_workflow_steps"][
        "browser_state_management"
    ] == [
        "inspect_action_guide",
        "inspect_page_info",
        "read_existing_state",
        "modify_state",
        "wait_for_state",
        "cleanup_state",
    ]
    assert checks["command_catalog"]["required_workflow_steps"]["form_interaction"] == [
        "inspect_action_guide",
        "inspect_form",
        "fill_labeled_field",
        "choose_labeled_option",
        "check_labeled_control",
        "wait_submit_ready",
        "submit_form",
        "verify_result",
    ]
    assert checks["command_catalog"]["required_workflow_steps"]["file_upload"] == [
        "inspect_action_guide",
        "inspect_upload_controls",
        "attach_files",
        "verify_upload_state",
        "submit_if_requested",
    ]
    assert checks["command_catalog"]["required_workflow_steps"][
        "dialog_frame_handling"
    ] == [
        "inspect_action_guide",
        "inspect_page_context",
        "inspect_or_wait_dialog",
        "handle_dialog_control",
        "inspect_or_wait_frame",
        "verify_result",
    ]
    assert checks["command_catalog"]["required_workflow_steps"][
        "interactive_targeting"
    ] == [
        "inspect_action_guide",
        "inspect_interactive_targets",
        "inspect_accessibility_context",
        "choose_click_method",
        "wait_target_ready",
        "activate_target",
        "verify_after_click",
    ]
    assert checks["command_catalog"]["required_workflow_steps"][
        "mouse_interaction"
    ] == [
        "inspect_action_guide",
        "inspect_interactive_targets",
        "choose_mouse_action",
        "run_mouse_action",
        "verify_result",
    ]
    assert checks["command_catalog"]["required_workflow_steps"]["visual_capture"] == [
        "inspect_action_guide",
        "inspect_page_context",
        "set_viewport_if_needed",
        "choose_capture_target",
        "capture_visual_evidence",
        "verify_capture_artifact",
    ]
    assert checks["command_catalog"]["required_workflow_steps"]["semantic_waits"] == [
        "inspect_action_guide",
        "inspect_current_page",
        "choose_wait_predicate",
        "wait_for_semantic_state",
        "verify_observed_state",
    ]
    assert checks["command_catalog"]["required_workflow_steps"][
        "menu_keyboard_flow"
    ] == [
        "inspect_action_guide",
        "inspect_interactive_targets",
        "open_or_focus_menu",
        "verify_menu_state",
        "inspect_menu_items",
        "send_keyboard_input",
        "verify_result",
    ]
    assert checks["command_catalog"]["required_workflow_steps"][
        "content_extraction"
    ] == [
        "inspect_action_guide",
        "inspect_page_info",
        "choose_extraction_surface",
        "extract_content",
        "verify_extraction_bounds",
    ]
    assert checks["command_catalog"]["required_workflow_steps"]["state_waits"] == [
        "inspect_action_guide",
        "inspect_current_state",
        "choose_wait_condition",
        "wait_for_state",
        "verify_after_wait",
    ]
    assert checks["command_catalog"]["required_workflow_steps"]["page_diagnostics"] == [
        "inspect_action_guide",
        "page_info_before",
        "set_viewport",
        "install_console_capture",
        "install_network_capture",
        "reproduce_issue",
        "read_console_entries",
        "read_network_entries",
        "capture_visible_state",
    ]
    assert checks["command_catalog"]["missing_required_workflow_steps"] == {}
    for command_name in (
        "action.press",
        "action.press-role",
        "action.press-key",
        "action.hover",
        "action.hover-role",
        "action.scroll",
        "action.scroll-into-view",
        "action.scroll-into-view-role",
        "action.set-viewport",
        "action.reload",
        "action.go-back",
        "action.go-forward",
        "action.screenshot-selector",
        "action.screenshot-role",
        "action.wait-url",
        "action.wait-title",
        "action.wait-load-state",
        "action.wait-network-idle",
        "action.get-text",
        "action.get-text-role",
        "action.exists",
        "action.exists-role",
        "action.count",
        "action.wait-count",
        "action.wait-state-role",
        "action.query",
        "action.inspect",
        "action.get-attribute",
        "action.get-attribute-role",
        "action.wait-attribute",
        "action.wait-attribute-role",
        "action.bounding-box",
        "action.bounding-box-role",
        "action.select-option",
        "action.select-label",
        "action.select-role",
        "action.check",
        "action.uncheck",
        "action.check-label",
        "action.check-role",
        "action.uncheck-label",
        "action.uncheck-role",
        "action.click-text",
        "action.click-role",
        "action.click-index",
        "action.double-click",
        "action.double-click-role",
        "action.right-click",
        "action.right-click-role",
        "action.focus",
        "action.focus-role",
        "action.fill-label",
        "action.fill-role",
        "action.get-value",
        "action.get-value-role",
        "action.wait-value",
        "action.wait-value-role",
        "action.blur",
        "action.blur-role",
        "action.clear",
        "action.clear-role",
        "action.set-value",
        "action.set-file-input",
        "action.dispatch-event",
        "action.submit",
        "action.accessibility-snapshot",
        "action.form-snapshot",
        "action.interactive-only-snapshot",
        "action.storage-get",
        "action.storage-set",
        "action.storage-remove",
        "action.storage-clear",
        "action.wait-storage",
        "action.cookie-get",
        "action.cookie-set",
        "action.cookie-delete",
        "action.cookie-clear",
        "action.wait-cookie",
        "action.wait-text",
        "action.wait-role",
        "action.dialog-snapshot",
        "action.wait-dialog",
        "action.frame-snapshot",
        "action.wait-frame",
        "action.performance-snapshot",
        "reference.list",
        "reference.get",
        "example.list",
        "example.get",
        "version",
        "case.schema",
        "case.scaffold",
        "case.validate",
        "case.run",
    ):
        assert command_name in checks["command_catalog"]["required_commands"]
    assert checks["command_catalog"]["missing_required_commands"] == []
    assert checks["case_schema"]["status"] == "pass"
    assert checks["case_schema"]["schema_version"] == 1
    assert checks["case_schema"]["action_count"] == 98
    assert checks["case_schema"]["supported_action_count"] == 98
    for case_action in (
        "fill-label",
        "click-role",
        "interactive-only-snapshot",
        "network-snapshot",
        "wait-network",
        "console-snapshot",
        "wait-console",
    ):
        assert case_action in checks["case_schema"]["required_case_actions"]
    assert checks["case_schema"]["missing_required_case_actions"] == []
    assert checks["case_schema"]["missing_supported_actions"] == []
    assert checks["case_schema"]["missing_action_schemas"] == []
    assert checks["case_schema"]["invalid_action_schemas"] == []
    assert checks["agent_prompt"]["status"] == "pass"
    assert checks["agent_prompt"]["metadata_id"] == "openai"
    assert checks["agent_prompt"]["package_resource"] == (
        "browser_cli.agent_metadata:openai.yaml"
    )
    assert checks["agent_prompt"]["display_name"] == "Lexmount Browser CLI"
    assert (
        checks["agent_prompt"]["short_description"]
        == "Control Lexmount browsers from Codex"
    )
    assert checks["agent_prompt"]["default_prompt_present"] is True
    assert checks["agent_prompt"]["required_pattern_count"] > 20
    assert checks["agent_prompt"]["missing_fields"] == []
    assert checks["agent_prompt"]["missing_patterns"] == []
    assert checks["agent_prompt"]["mismatched_fields"] == []
    assert checks["agent_references"]["status"] == "pass"
    assert checks["agent_references"]["reference_count"] == 1
    assert checks["agent_references"]["required_references"] == ["action_playbook"]
    assert checks["agent_references"]["missing_required_references"] == []
    assert checks["agent_references"]["invalid_references"] == []
    checked_reference = checks["agent_references"]["checked_references"][0]
    assert checked_reference["id"] == "action_playbook"
    assert checked_reference["status"] == "pass"
    assert checked_reference["content_command"] == (
        "browser-cli reference get --id action_playbook"
    )
    assert checked_reference["package_resource"] == (
        "browser_cli.agent_references:action-playbook.md"
    )
    assert checked_reference["content_length"] > 1000
    assert checked_reference["missing_patterns"] == []
    assert checks["agent_examples"]["status"] == "pass"
    assert checks["agent_examples"]["example_count"] == 3
    assert checks["agent_examples"]["required_examples"] == [
        "agent_playbook",
        "page_inspection_case",
        "form_fill_case",
    ]
    assert checks["agent_examples"]["missing_required_examples"] == []
    assert checks["agent_examples"]["invalid_examples"] == []
    checked_examples = {
        item["id"]: item for item in checks["agent_examples"]["checked_examples"]
    }
    assert checked_examples["agent_playbook"]["status"] == "pass"
    assert checked_examples["agent_playbook"]["content_command"] == (
        "browser-cli example get --id agent_playbook"
    )
    assert checked_examples["agent_playbook"]["content_length"] > 1000
    assert checked_examples["agent_playbook"]["missing_patterns"] == []
    assert checked_examples["page_inspection_case"]["case_valid"] is True
    assert checked_examples["page_inspection_case"]["case_errors"] == []
    assert checked_examples["form_fill_case"]["case_valid"] is True
    assert checked_examples["form_fill_case"]["case_errors"] == []
    assert checks["context_registry"]["status"] == "pass"
    assert checks["context_registry"]["path_source"] == "env"
    assert checks["context_registry"]["exists"] is False
    assert checks["context_registry"]["writable"] is True
    assert checks["context_registry"]["schema_version"] == 1
    assert checks["context_registry"]["context_count"] == 0
    assert checks["context_registry"]["scoped_context_count"] == 0
    assert checks["context_registry"]["metadata_context_count"] == 0
    assert checks["context_registry"]["metadata_values_redacted"] is True
    assert checks["env.LEXMOUNT_API_KEY"]["status"] == "pass"
    assert checks["env.LEXMOUNT_PROJECT_ID"]["status"] == "pass"
    assert checks["direct_url"]["status"] == "pass"
    assert checks["direct_url"]["connect_url"].endswith("api_key=***")
    assert checks["direct_url"]["connect_url_masked"] is True
    assert checks["api_connectivity"] == {
        "name": "api_connectivity",
        "status": "pass",
        "message": "Lexmount API is reachable",
        "session_count": 2,
        "status_filter": None,
    }


def test_doctor_warns_when_context_registry_json_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry_path = tmp_path / "bad-context-registry.json"
    registry_path.write_text("{", encoding="utf-8")
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.setenv("LEXMOUNT_BROWSER_CONTEXT_REGISTRY_FILE", str(registry_path))
    monkeypatch.setattr(
        "browser_cli.cli.shutil.which",
        lambda name: "/usr/local/bin/browser-cli" if name == "browser-cli" else None,
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--skip-api"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"] == "warning"
    assert payload["failed_checks"] == []
    assert "context_registry" in payload["warning_checks"]
    assert "secret" not in json.dumps(payload)

    checks = _checks_by_name(payload)
    registry = checks["context_registry"]
    assert registry["status"] == "warn"
    assert registry["error"] == "registry_invalid_json"
    assert registry["exists"] is True
    assert registry["path"] == str(registry_path)
    assert registry["path_source"] == "env"
    assert registry["readable"] is True
    assert registry["writable"] is True
    assert registry["metadata_values_redacted"] is True
    assert registry["fix"]["code"] == "repair_context_registry"
    assert "LEXMOUNT_BROWSER_CONTEXT_REGISTRY_FILE" in registry["fix"]["env"]
    assert any(
        "context pick --metadata-json" in command
        for command in registry["fix"]["commands"]
    )
    repair = payload["repair_plan"]
    assert repair["required"] is False
    assert "repair_context_registry" in {
        item["code"] for item in repair["fixes"] if item["check"] == "context_registry"
    }


def test_doctor_warns_when_command_catalog_misses_skill_commands(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.setattr(
        "browser_cli.cli.shutil.which",
        lambda name: "/usr/local/bin/browser-cli" if name == "browser-cli" else None,
    )
    monkeypatch.setattr(
        "browser_cli.cli._command_catalog",
        lambda: {
            "schema_version": 1,
            "commands": [
                {"name": "commands"},
                {"name": "doctor"},
                {"name": "session.create"},
            ],
        },
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--skip-api"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"] == "warning"
    assert "command_catalog" in payload["warning_checks"]
    checks = _checks_by_name(payload)
    catalog = checks["command_catalog"]
    assert catalog["status"] == "warn"
    assert catalog["schema_version"] == 1
    assert catalog["command_count"] == 3
    assert catalog["workflow_count"] == 0
    assert "action.press" in catalog["missing_required_commands"]
    assert "action.press-role" in catalog["missing_required_commands"]
    assert "action.press-key" in catalog["missing_required_commands"]
    assert "action.hover-role" in catalog["missing_required_commands"]
    assert "action.scroll-into-view-role" in catalog["missing_required_commands"]
    assert "action.double-click" in catalog["missing_required_commands"]
    assert "action.double-click-role" in catalog["missing_required_commands"]
    assert "action.right-click" in catalog["missing_required_commands"]
    assert "action.right-click-role" in catalog["missing_required_commands"]
    for command_name in (
        "action.reload",
        "action.go-back",
        "action.go-forward",
        "action.wait-title",
        "action.wait-network-idle",
        "action.count",
        "action.wait-count",
        "action.query",
        "action.inspect",
        "action.bounding-box",
        "action.scroll-into-view",
        "action.select-label",
        "action.check-label",
        "action.uncheck-label",
        "action.click-index",
        "action.focus",
        "action.get-value",
        "action.wait-value",
        "action.blur",
        "action.clear",
        "action.set-value",
        "action.dispatch-event",
        "action.submit",
        "action.performance-snapshot",
    ):
        assert command_name in catalog["missing_required_commands"]
    assert "action.set-viewport" in catalog["missing_required_commands"]
    assert "action.screenshot-selector" in catalog["missing_required_commands"]
    assert "action.screenshot-role" in catalog["missing_required_commands"]
    assert "action.wait-url" in catalog["missing_required_commands"]
    assert "action.wait-load-state" in catalog["missing_required_commands"]
    assert "action.guide" in catalog["missing_required_commands"]
    assert "action.get-text-role" in catalog["missing_required_commands"]
    assert "action.exists-role" in catalog["missing_required_commands"]
    assert "action.wait-state-role" in catalog["missing_required_commands"]
    assert "action.get-attribute" in catalog["missing_required_commands"]
    assert "action.get-attribute-role" in catalog["missing_required_commands"]
    assert "action.wait-attribute" in catalog["missing_required_commands"]
    assert "action.wait-attribute-role" in catalog["missing_required_commands"]
    assert "action.bounding-box-role" in catalog["missing_required_commands"]
    assert "action.select-role" in catalog["missing_required_commands"]
    assert "action.check-role" in catalog["missing_required_commands"]
    assert "action.uncheck-role" in catalog["missing_required_commands"]
    assert "action.focus-role" in catalog["missing_required_commands"]
    assert "action.get-value-role" in catalog["missing_required_commands"]
    assert "action.wait-value-role" in catalog["missing_required_commands"]
    assert "action.blur-role" in catalog["missing_required_commands"]
    assert "action.clear-role" in catalog["missing_required_commands"]
    assert "action.set-file-input" in catalog["missing_required_commands"]
    assert "action.accessibility-snapshot" in catalog["missing_required_commands"]
    assert "action.form-snapshot" in catalog["missing_required_commands"]
    assert "action.link-snapshot" in catalog["missing_required_commands"]
    assert "action.table-snapshot" in catalog["missing_required_commands"]
    assert "action.list-snapshot" in catalog["missing_required_commands"]
    assert "action.text-snapshot" in catalog["missing_required_commands"]
    assert "action.outline-snapshot" in catalog["missing_required_commands"]
    assert "action.storage-get" in catalog["missing_required_commands"]
    assert "action.storage-set" in catalog["missing_required_commands"]
    assert "action.storage-remove" in catalog["missing_required_commands"]
    assert "action.storage-clear" in catalog["missing_required_commands"]
    assert "action.wait-storage" in catalog["missing_required_commands"]
    assert "action.cookie-get" in catalog["missing_required_commands"]
    assert "action.cookie-set" in catalog["missing_required_commands"]
    assert "action.cookie-delete" in catalog["missing_required_commands"]
    assert "action.cookie-clear" in catalog["missing_required_commands"]
    assert "action.wait-cookie" in catalog["missing_required_commands"]
    assert "action.wait-text" in catalog["missing_required_commands"]
    assert "action.wait-role" in catalog["missing_required_commands"]
    assert "action.dialog-snapshot" in catalog["missing_required_commands"]
    assert "action.wait-dialog" in catalog["missing_required_commands"]
    assert "action.frame-snapshot" in catalog["missing_required_commands"]
    assert "action.wait-frame" in catalog["missing_required_commands"]
    assert "reference.list" in catalog["missing_required_commands"]
    assert "reference.get" in catalog["missing_required_commands"]
    assert "example.list" in catalog["missing_required_commands"]
    assert "example.get" in catalog["missing_required_commands"]
    assert "version" in catalog["missing_required_commands"]
    assert "case.schema" in catalog["missing_required_commands"]
    assert "case.scaffold" in catalog["missing_required_commands"]
    assert "auth.scopes" in catalog["missing_required_commands"]
    assert "auth.connect-requirements" in catalog["missing_required_commands"]
    assert catalog["missing_required_workflows"] == [
        "setup_and_verify",
        "connect_from_codex_site_requirements",
        "connect_from_codex_auth",
        "device_code_auth",
        "scoped_token_lifecycle",
        "session_recovery",
        "one_off_page_task",
        "navigation_flow",
        "link_navigation",
        "case_file_task",
        "persistent_login_state",
        "browser_state_management",
        "form_interaction",
        "file_upload",
        "dialog_frame_handling",
        "interactive_targeting",
        "mouse_interaction",
        "visual_capture",
        "semantic_waits",
        "menu_keyboard_flow",
        "content_extraction",
        "state_waits",
        "page_diagnostics",
    ]
    assert catalog["missing_required_workflow_steps"] == {}
    assert catalog["fix"]["code"] == "upgrade_browser_cli_command_surface"
    assert "browser-cli commands --names-only" in payload["repair_plan"]["commands"]
    assert "browser-cli commands" in payload["repair_plan"]["commands"]
    assert "api_connectivity" in payload["skipped_checks"]


def test_doctor_warns_when_case_schema_misses_skill_actions(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.setattr(
        "browser_cli.cli.shutil.which",
        lambda name: "/usr/local/bin/browser-cli" if name == "browser-cli" else None,
    )
    monkeypatch.setattr(
        "browser_cli.cli.BROWSER_CLI_SUPPORTED_CASE_ACTIONS",
        frozenset({"open-url"}),
    )
    monkeypatch.setattr(
        "browser_cli.cli._case_action_schema",
        lambda: {
            "open-url": {
                "required_fields": ["url"],
                "required_one_of": [],
                "optional_fields": ["timeout_ms"],
                "result_fields": ["url", "title", "status"],
                "example_step": {"action": "open-url", "url": "https://example.com"},
            }
        },
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--skip-api"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"] == "warning"
    assert "case_schema" in payload["warning_checks"]
    checks = _checks_by_name(payload)
    case_schema = checks["case_schema"]
    assert case_schema["status"] == "warn"
    assert case_schema["action_count"] == 1
    assert case_schema["supported_action_count"] == 1
    assert case_schema["missing_supported_actions"][:3] == [
        "accessibility-snapshot",
        "blur",
        "blur-role",
    ]
    assert "fill-label" in case_schema["missing_required_case_actions"]
    assert "network-snapshot" in case_schema["missing_required_case_actions"]
    assert "wait-console" in case_schema["missing_required_case_actions"]
    assert case_schema["missing_action_schemas"][:3] == [
        "accessibility-snapshot",
        "blur",
        "blur-role",
    ]
    assert case_schema["invalid_action_schemas"] == []
    assert case_schema["fix"]["code"] == "upgrade_browser_cli_case_schema"
    assert "browser-cli case schema --names-only" in payload["repair_plan"]["commands"]
    assert (
        "browser-cli case schema --action network-snapshot"
        in payload["repair_plan"]["commands"]
    )
    assert "api_connectivity" in payload["skipped_checks"]


def test_doctor_warns_when_agent_prompt_metadata_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.setattr(
        "browser_cli.cli.shutil.which",
        lambda name: "/usr/local/bin/browser-cli" if name == "browser-cli" else None,
    )
    monkeypatch.setattr(
        "browser_cli.cli._read_agent_prompt_metadata_content",
        lambda metadata_id="openai": "\n".join(
            [
                "interface:",
                '  display_name: "Wrong"',
                '  short_description: "Control Lexmount browsers from Codex"',
                '  default_prompt: "Use $browser-cli."',
            ]
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--skip-api"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"] == "warning"
    assert "agent_prompt" in payload["warning_checks"]
    checks = _checks_by_name(payload)
    agent_prompt = checks["agent_prompt"]
    assert agent_prompt["status"] == "warn"
    assert agent_prompt["display_name"] == "Wrong"
    assert agent_prompt["mismatched_fields"] == ["display_name"]
    assert agent_prompt["missing_fields"] == []
    assert "doctor --json" in agent_prompt["missing_patterns"]
    assert "commands --workflow" in agent_prompt["missing_patterns"]
    assert agent_prompt["fix"]["code"] == "repair_packaged_agent_prompt"
    assert (
        "browser-cli commands --workflow setup_and_verify"
        in payload["repair_plan"]["commands"]
    )
    assert "api_connectivity" in payload["skipped_checks"]


def test_doctor_warns_when_agent_reference_resource_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.setattr(
        "browser_cli.cli.shutil.which",
        lambda name: "/usr/local/bin/browser-cli" if name == "browser-cli" else None,
    )

    def fail_read(reference_id: str) -> str:
        assert reference_id == "action_playbook"
        raise FileNotFoundError("missing packaged reference")

    monkeypatch.setattr("browser_cli.cli._read_agent_reference_content", fail_read)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--skip-api"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"] == "warning"
    assert "agent_references" in payload["warning_checks"]
    checks = _checks_by_name(payload)
    references = checks["agent_references"]
    assert references["status"] == "warn"
    assert references["required_references"] == ["action_playbook"]
    assert references["missing_required_references"] == []
    assert references["invalid_references"] == [
        {
            "id": "action_playbook",
            "path": "references/action-playbook.md",
            "package_resource": "browser_cli.agent_references:action-playbook.md",
            "content_command": "browser-cli reference get --id action_playbook",
            "status": "unavailable",
            "error": "FileNotFoundError",
        }
    ]
    assert references["fix"]["code"] == "repair_packaged_agent_references"
    assert (
        "browser-cli reference get --id action_playbook"
        in payload["repair_plan"]["commands"]
    )
    assert (
        "uv tool install --force git+https://github.com/lexmount/browser-cli.git"
        in payload["repair_plan"]["commands"]
    )
    assert "api_connectivity" in payload["skipped_checks"]


def test_doctor_warns_when_packaged_case_example_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.setattr(
        "browser_cli.cli.shutil.which",
        lambda name: "/usr/local/bin/browser-cli" if name == "browser-cli" else None,
    )
    original_read = cli_module._read_agent_example_content

    def read_example(example_id: str) -> str:
        if example_id != "form_fill_case":
            return original_read(example_id)
        return """
name: form-fill
steps:
  - action: eval
  - action: type
  - action: screenshot
""".strip()

    monkeypatch.setattr("browser_cli.cli._read_agent_example_content", read_example)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--skip-api"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"] == "warning"
    assert "agent_examples" in payload["warning_checks"]
    examples = _checks_by_name(payload)["agent_examples"]
    assert examples["status"] == "warn"
    assert examples["required_examples"] == [
        "agent_playbook",
        "page_inspection_case",
        "form_fill_case",
    ]
    assert examples["missing_required_examples"] == []
    assert len(examples["invalid_examples"]) == 1
    invalid = examples["invalid_examples"][0]
    assert invalid["id"] == "form_fill_case"
    assert invalid["status"] == "invalid"
    assert invalid["case_valid"] is False
    assert "steps[0] missing required field 'expression'" in invalid["case_errors"]
    assert "steps[1] missing required field 'selector'" in invalid["case_errors"]
    assert "steps[1] missing required field 'text'" in invalid["case_errors"]
    assert invalid["missing_patterns"] == [
        "action: fill-label",
        "action: click-role",
        "action: wait-text",
        "action: get-value-role",
    ]
    assert examples["fix"]["code"] == "repair_packaged_agent_examples"
    assert "browser-cli example list" in payload["repair_plan"]["commands"]
    assert (
        "browser-cli example get --id form_fill_case"
        in payload["repair_plan"]["commands"]
    )
    assert (
        "uv tool install --force git+https://github.com/lexmount/browser-cli.git"
        in payload["repair_plan"]["commands"]
    )
    assert "api_connectivity" in payload["skipped_checks"]


def test_doctor_warns_when_agent_workflow_missing_required_steps(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.setattr(
        "browser_cli.cli.shutil.which",
        lambda name: "/usr/local/bin/browser-cli" if name == "browser-cli" else None,
    )
    monkeypatch.setattr(
        "browser_cli.cli._command_catalog",
        lambda: {
            "schema_version": 1,
            "commands": [
                {"name": command_name}
                for command_name in cli_module.DOCTOR_REQUIRED_COMMANDS
            ],
            "agent_workflows": {
                "setup_and_verify": {
                    "steps": [
                        {"id": "auth_status"},
                        {"id": "doctor"},
                    ],
                },
                "connect_from_codex_site_requirements": {
                    "steps": [
                        {"id": "inspect_scope_catalog"},
                        {"id": "inspect_site_requirements"},
                        {"id": "verify_manual_handoff"},
                        {"id": "verify_device_code_handoff"},
                        {"id": "doctor_after_credentials"},
                    ],
                },
                "connect_from_codex_auth": {
                    "steps": [
                        {"id": "auth_status"},
                        {"id": "inspect_scope_catalog"},
                        {"id": "auth_login"},
                        {"id": "export_env"},
                        {"id": "doctor"},
                    ],
                },
                "device_code_auth": {
                    "steps": [
                        {"id": "request_device_code"},
                        {"id": "fallback_manual_env"},
                        {"id": "verify_auth_status"},
                        {"id": "doctor"},
                    ],
                },
                "scoped_token_lifecycle": {
                    "steps": [
                        {"id": "status_scoped_token"},
                        {"id": "inspect_scope_catalog"},
                        {"id": "inspect_required_scopes"},
                        {"id": "refresh_if_needed"},
                        {"id": "verify_browser_readiness"},
                        {"id": "logout_or_revoke_when_requested"},
                    ],
                },
                "session_recovery": {
                    "steps": [
                        {"id": "list_active_sessions"},
                        {"id": "inspect_session"},
                        {"id": "keepalive_session"},
                        {"id": "close_stale_session"},
                        {"id": "create_replacement_session"},
                    ],
                },
                "one_off_page_task": {
                    "steps": [
                        {"id": "create_session"},
                        {"id": "open_url"},
                        {"id": "find_targets"},
                    ],
                },
                "navigation_flow": {
                    "steps": [
                        {"id": "inspect_action_guide"},
                        {"id": "inspect_current_page"},
                        {"id": "choose_navigation_action"},
                        {"id": "run_navigation_action"},
                    ],
                },
                "link_navigation": {
                    "steps": [
                        {"id": "inspect_action_guide"},
                        {"id": "inspect_current_page"},
                        {"id": "inspect_links"},
                        {"id": "choose_link_target"},
                        {"id": "activate_link"},
                    ],
                },
                "case_file_task": {
                    "steps": [
                        {"id": "inspect_case_commands"},
                        {"id": "inspect_case_schema"},
                        {"id": "scaffold_case_file"},
                        {"id": "validate_case_file"},
                        {"id": "run_case_file"},
                    ],
                },
                "persistent_login_state": {
                    "steps": [
                        {"id": "dry_run_context_pick"},
                        {"id": "inspect_context_status"},
                        {"id": "create_session_with_context"},
                        {"id": "close_session"},
                    ],
                },
                "browser_state_management": {
                    "steps": [
                        {"id": "inspect_action_guide"},
                        {"id": "inspect_page_info"},
                        {"id": "read_existing_state"},
                        {"id": "modify_state"},
                        {"id": "wait_for_state"},
                    ],
                },
                "form_interaction": {
                    "steps": [
                        {"id": "inspect_action_guide"},
                        {"id": "inspect_form"},
                        {"id": "fill_labeled_field"},
                        {"id": "choose_labeled_option"},
                        {"id": "check_labeled_control"},
                        {"id": "wait_submit_ready"},
                        {"id": "submit_form"},
                        {"id": "verify_result"},
                    ],
                },
                "file_upload": {
                    "steps": [
                        {"id": "inspect_action_guide"},
                        {"id": "inspect_upload_controls"},
                        {"id": "attach_files"},
                        {"id": "verify_upload_state"},
                    ],
                },
                "dialog_frame_handling": {
                    "steps": [
                        {"id": "inspect_action_guide"},
                        {"id": "inspect_page_context"},
                        {"id": "inspect_or_wait_dialog"},
                        {"id": "handle_dialog_control"},
                        {"id": "inspect_or_wait_frame"},
                    ],
                },
                "interactive_targeting": {
                    "steps": [
                        {"id": "inspect_action_guide"},
                        {"id": "inspect_interactive_targets"},
                        {"id": "inspect_accessibility_context"},
                        {"id": "choose_click_method"},
                        {"id": "wait_target_ready"},
                        {"id": "activate_target"},
                        {"id": "verify_after_click"},
                    ],
                },
                "mouse_interaction": {
                    "steps": [
                        {"id": "inspect_action_guide"},
                        {"id": "inspect_interactive_targets"},
                        {"id": "choose_mouse_action"},
                        {"id": "run_mouse_action"},
                    ],
                },
                "visual_capture": {
                    "steps": [
                        {"id": "inspect_action_guide"},
                        {"id": "inspect_page_context"},
                        {"id": "set_viewport_if_needed"},
                        {"id": "choose_capture_target"},
                        {"id": "capture_visual_evidence"},
                    ],
                },
                "semantic_waits": {
                    "steps": [
                        {"id": "inspect_action_guide"},
                        {"id": "inspect_current_page"},
                        {"id": "choose_wait_predicate"},
                        {"id": "wait_for_semantic_state"},
                    ],
                },
                "menu_keyboard_flow": {
                    "steps": [
                        {"id": "inspect_action_guide"},
                        {"id": "inspect_interactive_targets"},
                        {"id": "open_or_focus_menu"},
                        {"id": "verify_menu_state"},
                        {"id": "inspect_menu_items"},
                        {"id": "send_keyboard_input"},
                    ],
                },
                "content_extraction": {
                    "steps": [
                        {"id": "inspect_action_guide"},
                        {"id": "inspect_page_info"},
                        {"id": "choose_extraction_surface"},
                        {"id": "extract_content"},
                    ],
                },
                "state_waits": {
                    "steps": [
                        {"id": "inspect_action_guide"},
                        {"id": "inspect_current_state"},
                        {"id": "choose_wait_condition"},
                        {"id": "wait_for_state"},
                    ],
                },
                "page_diagnostics": {
                    "steps": [
                        {"id": "inspect_action_guide"},
                        {"id": "page_info_before"},
                        {"id": "set_viewport"},
                        {"id": "install_console_capture"},
                        {"id": "install_network_capture"},
                        {"id": "reproduce_issue"},
                        {"id": "read_console_entries"},
                        {"id": "read_network_entries"},
                        {"id": "capture_visible_state"},
                    ],
                },
            },
        },
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--skip-api"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "warning"
    catalog = _checks_by_name(payload)["command_catalog"]
    assert catalog["missing_required_commands"] == []
    assert catalog["missing_required_workflows"] == []
    assert catalog["missing_required_workflow_steps"] == {
        "case_file_task": [
            "inspect_semantic_case_action",
            "inspect_form_case_example",
            "scaffold_form_case_file",
        ],
        "one_off_page_task": ["close_session"],
        "navigation_flow": ["verify_navigation_result"],
        "link_navigation": ["verify_navigation_result"],
        "setup_and_verify": ["smoke_session"],
        "browser_state_management": ["cleanup_state"],
        "file_upload": ["submit_if_requested"],
        "dialog_frame_handling": ["verify_result"],
        "mouse_interaction": ["verify_result"],
        "visual_capture": ["verify_capture_artifact"],
        "semantic_waits": ["verify_observed_state"],
        "menu_keyboard_flow": ["verify_result"],
        "content_extraction": ["verify_extraction_bounds"],
        "state_waits": ["verify_after_wait"],
    }
    assert catalog["fix"]["code"] == "upgrade_browser_cli_command_surface"
    assert "browser-cli commands" in payload["repair_plan"]["commands"]


def test_doctor_smoke_session_creates_and_closes_temp_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.setattr(
        "browser_cli.cli.shutil.which",
        lambda name: "/usr/local/bin/browser-cli" if name == "browser-cli" else None,
    )
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeAdmin:
        def list_sessions(self, *, status: str | None) -> DummyModel:
            calls.append(("list_sessions", {"status": status}))
            return DummyModel({"count": 0, "status_filter": status, "sessions": []})

        def create_session(
            self,
            *,
            context_id: str | None,
            create_context: bool,
            context_mode: str,
            browser_mode: str,
            metadata: dict[str, Any] | None,
        ) -> DummyModel:
            calls.append(
                (
                    "create_session",
                    {
                        "context_id": context_id,
                        "create_context": create_context,
                        "context_mode": context_mode,
                        "browser_mode": browser_mode,
                        "metadata": metadata,
                    },
                )
            )
            return DummyModel(
                {
                    "session": {"session_id": "smoke-1", "status": "active"},
                }
            )

        def close_session(self, session_id: str) -> None:
            calls.append(("close_session", {"session_id": session_id}))

    admin = FakeAdmin()
    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: admin)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--smoke-session"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["ready_for_browser_actions"] is True
    assert payload["failed_checks"] == []
    assert payload["skipped_checks"] == []
    checks = _checks_by_name(payload)
    assert checks["api_connectivity"]["status"] == "pass"
    assert checks["browser_smoke_session"] == {
        "name": "browser_smoke_session",
        "status": "pass",
        "message": "Temporary browser session can be created and closed",
        "stage": "closed",
        "created": True,
        "closed": True,
        "session_id": "smoke-1",
    }
    assert calls == [
        ("list_sessions", {"status": None}),
        (
            "create_session",
            {
                "context_id": None,
                "create_context": False,
                "context_mode": "read_write",
                "browser_mode": "normal",
                "metadata": {"purpose": "browser-cli-doctor-smoke"},
            },
        ),
        ("close_session", {"session_id": "smoke-1"}),
    ]


def test_doctor_smoke_session_is_skipped_with_skip_api(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)

    class FakeAdmin:
        def __init__(self) -> None:
            raise AssertionError("doctor --skip-api should not call API")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", FakeAdmin)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--skip-api", "--smoke-session"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["ready_for_browser_actions"] is False
    assert payload["failed_checks"] == []
    assert payload["skipped_checks"] == [
        "api_connectivity",
        "browser_smoke_session",
    ]
    assert "browser-cli doctor --smoke-session" in payload["repair_plan"]["commands"]
    checks = _checks_by_name(payload)
    assert checks["browser_smoke_session"]["status"] == "skipped"
    assert checks["browser_smoke_session"]["fix"]["code"] == (
        "run_browser_smoke_session"
    )


def test_doctor_smoke_session_close_failure_is_masked_and_actionable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "very-secret-key")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)

    class FakeAdmin:
        def list_sessions(self, *, status: str | None) -> DummyModel:
            return DummyModel({"count": 0, "status_filter": status, "sessions": []})

        def create_session(self, **kwargs: Any) -> DummyModel:
            return DummyModel(
                {
                    "session": {"session_id": "smoke-1", "status": "active"},
                }
            )

        def close_session(self, session_id: str) -> None:
            raise RuntimeError(
                f"close failed token=abc raw very-secret-key {session_id}"
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--smoke-session"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "very-secret-key" not in serialized
    assert "token=***" in serialized
    assert payload["ok"] is False
    assert payload["ready_for_browser_actions"] is False
    assert "browser_smoke_session" in payload["failed_checks"]
    checks = _checks_by_name(payload)
    assert checks["browser_smoke_session"]["status"] == "fail"
    assert checks["browser_smoke_session"]["stage"] == "close"
    assert checks["browser_smoke_session"]["created"] is True
    assert checks["browser_smoke_session"]["closed"] is False
    assert checks["browser_smoke_session"]["session_id"] == "smoke-1"
    assert checks["browser_smoke_session"]["fix"]["commands"][0] == (
        "browser-cli session close --session-id smoke-1"
    )


def test_doctor_warns_when_executable_is_not_on_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.setattr("browser_cli.cli.shutil.which", lambda name: None)

    class FakeAdmin:
        def list_sessions(self, *, status: str | None) -> DummyModel:
            return DummyModel({"count": 0, "status_filter": status, "sessions": []})

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"] == "warning"
    assert payload["failed"] == 0
    assert payload["warnings"] == 1
    assert payload["failed_checks"] == []
    assert payload["warning_checks"] == ["browser_cli_executable"]
    assert payload["ready_for_browser_actions"] is True
    assert payload["repair_plan"]["required"] is False
    assert payload["repair_plan"]["recommended"] is True
    assert payload["repair_plan"]["fixes"][0]["check"] == "browser_cli_executable"
    assert "uv tool install" in payload["repair_plan"]["commands"][0]
    assert "browser-cli --version" in payload["repair_plan"]["commands"]
    checks = _checks_by_name(payload)
    assert checks["browser_cli_executable"]["status"] == "warn"
    assert checks["browser_cli_executable"]["fix"]["code"] == (
        "install_browser_cli_on_path"
    )
    assert "uv tool install" in checks["browser_cli_executable"]["fix"]["commands"][0]
    assert (
        "browser-cli --version" in checks["browser_cli_executable"]["fix"]["commands"]
    )


def test_doctor_uses_package_version_fallback_when_metadata_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.delenv("LEXMOUNT_REGION", raising=False)
    monkeypatch.setattr("browser_cli.cli._package_version", lambda distribution: None)
    monkeypatch.setattr(
        "browser_cli.cli.shutil.which",
        lambda name: "/usr/local/bin/browser-cli" if name == "browser-cli" else None,
    )

    class FakeAdmin:
        def list_sessions(self, *, status: str | None) -> DummyModel:
            return DummyModel({"count": 0, "status_filter": status, "sessions": []})

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    checks = _checks_by_name(payload)
    assert checks["browser_cli"]["version"] == "0.1.0"
    assert checks["browser_cli"]["version_known"] is True
    assert checks["browser_cli"]["version_source"] == "package_fallback"
    assert checks["lex_browser_runtime"]["version"] == "unknown"
    assert checks["lex_browser_runtime"]["version_known"] is False
    assert checks["api_connectivity"]["status"] == "pass"


def test_doctor_fails_missing_required_env(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LEXMOUNT_API_KEY", raising=False)
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.delenv("LEXMOUNT_REGION", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--skip-api"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "doctor_failed"
    assert payload["failed"] >= 2
    assert payload["ready_for_browser_actions"] is False
    assert "env.LEXMOUNT_API_KEY" in payload["failed_checks"]
    assert "env.LEXMOUNT_PROJECT_ID" in payload["failed_checks"]
    assert "direct_url" in payload["failed_checks"]
    assert payload["skipped_checks"] == ["api_connectivity"]
    assert payload["repair_plan"]["required"] is True
    assert "LEXMOUNT_API_KEY" in payload["repair_plan"]["env"]
    assert "LEXMOUNT_PROJECT_ID" in payload["repair_plan"]["env"]
    assert "browser-cli auth login" in payload["repair_plan"]["commands"]
    connect = payload["repair_plan"]["connect_from_codex"]
    assert connect["available"] is False
    assert connect["url"].startswith("https://browser.lexmount.cn/connect/codex?")
    assert connect["open_command"] == "browser-cli auth login --open"
    assert connect["project_id"] is None
    assert connect["project_id_source"] == "unset"
    assert connect["requested_scopes"] == [
        "browser:sessions",
        "browser:contexts",
        "browser:actions",
    ]
    assert connect["requested_expires_in"] == "7d"
    assert connect["device_code_url"].startswith(
        "https://browser.lexmount.cn/connect/codex?"
    )
    assert connect["site_capability_status"]["available"] is False
    assert connect["site_capability_status"]["missing_count"] == 6
    assert [item["id"] for item in connect["required_token_lifecycle"]] == [
        "issue_scoped_key",
        "refresh_token",
        "revoke_token",
        "expire_token",
    ]
    assert [item["id"] for item in connect["required_runtime_auth"]] == [
        "sdk_accepts_bearer_token",
        "api_accepts_bearer_token",
        "browser_gateway_accepts_bearer_token",
    ]
    assert any(
        "Implement /connect/codex" in item
        for item in connect["browser_site_requirements"]
    )
    assert connect["verification"]["doctor_command"] == "browser-cli doctor --json"
    query = parse_qs(urlsplit(connect["url"]).query)
    assert "project_id" not in query
    assert query["response"] == ["env"]
    assert query["scope"] == [
        "browser:sessions",
        "browser:contexts",
        "browser:actions",
    ]

    checks = _checks_by_name(payload)
    assert checks["env.LEXMOUNT_API_KEY"]["status"] == "fail"
    assert checks["env.LEXMOUNT_PROJECT_ID"]["status"] == "fail"
    assert checks["direct_url"]["status"] == "fail"
    assert checks["api_connectivity"]["status"] == "skipped"
    assert checks["env.LEXMOUNT_API_KEY"]["fix"]["code"] == "configure_credentials"
    assert checks["env.LEXMOUNT_API_KEY"]["fix"]["env"] == ["LEXMOUNT_API_KEY"]
    assert "browser-cli auth login" in checks["env.LEXMOUNT_API_KEY"]["fix"]["commands"]
    assert checks["env.LEXMOUNT_API_KEY"]["fix"]["connect_from_codex"] == connect
    assert checks["env.LEXMOUNT_PROJECT_ID"]["fix"]["env"] == ["LEXMOUNT_PROJECT_ID"]
    assert checks["direct_url"]["fix"]["code"] == "fix_direct_url_configuration"
    assert checks["api_connectivity"]["fix"]["code"] == "run_live_api_check"


def test_doctor_connect_from_codex_repair_uses_env_project_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LEXMOUNT_API_KEY", raising=False)
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "doctor-project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.delenv("LEXMOUNT_REGION", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--skip-api"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    connect = payload["repair_plan"]["connect_from_codex"]
    assert connect["project_id"] == "doctor-project"
    assert connect["project_id_source"] == "env"
    query = parse_qs(urlsplit(connect["url"]).query)
    assert query["project_id"] == ["doctor-project"]
    assert connect["setup_blocks"][2]["commands"] == [
        "browser-cli auth export-env",
        "export LEXMOUNT_API_KEY='<api-key>'",
        "export LEXMOUNT_PROJECT_ID=doctor-project",
    ]


def test_doctor_reports_device_token_without_treating_it_as_runtime_auth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LEXMOUNT_API_KEY", raising=False)
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)
    monkeypatch.setattr(
        cli_module,
        "_now_utc",
        lambda: datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc),
    )
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "kind": "device_token",
                "project_id": "project",
                "api_base_url": "https://api.lexmount.cn",
                "access_token": "secret-access-token",
                "refresh_token": "secret-refresh-token",
                "expires_at": "2026-06-26T01:00:00Z",
                "scopes": ["browser.sessions:create"],
                "token_id": "tok_123",
            }
        )
    )
    credentials_file.chmod(0o600)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "doctor",
                "--skip-api",
                "--credentials-file",
                str(credentials_file),
            ]
        )

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "secret-access-token" not in serialized
    assert "secret-refresh-token" not in serialized
    assert payload["auth_source"] == "device_token"
    assert payload["runtime_auth_usable"] is False
    assert payload["runtime_auth"]["usable"] is False
    assert payload["runtime_auth"]["source"] == "device_token_pending_runtime"
    assert payload["runtime_auth"]["bearer_runtime"]["device_token_valid"] is True
    assert [
        item["id"]
        for item in payload["runtime_auth"]["bearer_runtime"]["required_support"]
    ] == [
        "sdk_accepts_bearer_token",
        "api_accepts_bearer_token",
        "browser_gateway_accepts_bearer_token",
    ]
    assert payload["ready_for_browser_actions"] is False
    assert payload["device_token"]["valid"] is True
    checks = _checks_by_name(payload)
    assert checks["local_device_token"]["status"] == "pass"
    assert checks["local_device_token"]["device_token"]["token_id"] == "tok_123"
    assert checks["local_device_token"]["runtime_auth"]["source"] == (
        "device_token_pending_runtime"
    )
    assert (
        "bearer-token runtime auth is pending"
        in checks["local_device_token"]["message"]
    )
    assert "env.LEXMOUNT_API_KEY" in payload["failed_checks"]
    assert "direct_url" in payload["failed_checks"]


def test_doctor_skip_api_does_not_call_admin(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)

    class FakeAdmin:
        def __init__(self) -> None:
            raise AssertionError("doctor --skip-api should not call API")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", FakeAdmin)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--skip-api"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    checks = _checks_by_name(payload)
    assert payload["ok"] is True
    assert payload["ready_for_browser_actions"] is False
    assert payload["failed_checks"] == []
    assert payload["skipped_checks"] == ["api_connectivity"]
    assert payload["repair_plan"]["required"] is False
    assert payload["repair_plan"]["recommended"] is True
    assert "browser-cli doctor" in payload["repair_plan"]["commands"]
    assert checks["direct_url"]["status"] == "pass"
    assert checks["api_connectivity"]["status"] == "skipped"
    assert checks["api_connectivity"]["fix"] == {
        "code": "run_live_api_check",
        "commands": ["browser-cli doctor"],
        "guidance": [
            "Rerun doctor without --skip-api when live API access is available."
        ],
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["doctor", "--json", "--skip-api"],
        ["--json", "doctor", "--skip-api"],
    ],
)
def test_doctor_accepts_json_compatibility_flag(
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(argv)

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "doctor"
    assert _checks_by_name(payload)["api_connectivity"]["status"] == "skipped"


def test_doctor_masks_api_error_messages(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "very-secret-key")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)

    class FakeAdmin:
        def list_sessions(self, *, status: str | None) -> DummyModel:
            raise RuntimeError(
                f"request failed api_key=very-secret-key raw very-secret-key {status}"
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "very-secret-key" not in serialized
    assert "api_key=***" in serialized
    checks = _checks_by_name(payload)
    assert checks["api_connectivity"]["status"] == "fail"
    assert checks["api_connectivity"]["error"] == "RuntimeError"
    assert checks["api_connectivity"]["fix"]["code"] == "verify_api_connectivity"
    assert checks["api_connectivity"]["fix"]["commands"] == [
        "browser-cli auth status",
        "browser-cli doctor",
    ]


def test_runtime_failures_mask_sensitive_values(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "local-secret")
    connect_url = (
        "wss://api.lexmount.cn/connection?project_id=project"
        "&api_key=server-secret&token=session-token"
    )
    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: connect_url,
    )

    def fake_run_browser_action(**kwargs: Any) -> SimpleNamespace:
        raise RuntimeError(
            "failed api_key=server-secret token=session-token raw local-secret"
        )

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "snapshot", "--session-id", "s1"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "server-secret" not in serialized
    assert "session-token" not in serialized
    assert "local-secret" not in serialized
    assert "api_key=***" in serialized
    assert "token=***" in serialized


def test_failure_payload_masks_nested_sensitive_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "local-secret")

    with pytest.raises(SystemExit) as exc_info:
        cli_module._failure(
            "test.command",
            "test_error",
            "message api_key=server-secret raw local-secret",
            api_key="server-secret",
            details={
                "access_token": "access-secret",
                "url": "https://example.test?api_key=server-secret",
                "items": [{"token": "item-secret"}],
            },
        )

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "server-secret" not in serialized
    assert "access-secret" not in serialized
    assert "item-secret" not in serialized
    assert "local-secret" not in serialized
    assert payload["api_key"] == "***"
    assert payload["details"]["access_token"] == "***"
    assert payload["details"]["items"] == [{"token": "***"}]
    assert payload["details"]["url"].endswith("api_key=***")


def test_auth_status_reports_env_without_revealing_api_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "local-secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.setenv("LEXMOUNT_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("LEXMOUNT_REGION", "cn")

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "status"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "local-secret" not in json.dumps(payload)
    assert payload["ok"] is True
    assert payload["command"] == "auth.status"
    assert payload["configured"] is True
    assert payload["missing_env"] == []
    assert payload["runtime_auth"] == {
        "usable": True,
        "source": "env_api_key",
        "browser_actions_auth": "env_api_key",
        "fallback_missing_env": [],
        "device_token_runtime_usable": False,
        "bearer_runtime": {
            "available": False,
            "reason": "bearer_token_runtime_not_enabled",
            "device_token_present": False,
            "device_token_valid": False,
            "required_support": [
                {
                    "id": "sdk_accepts_bearer_token",
                    "owner": "lexmount-python-sdk",
                    "required_change": (
                        "Lexmount client accepts scoped bearer/access tokens and sends "
                        "Authorization: Bearer without requiring LEXMOUNT_API_KEY."
                    ),
                },
                {
                    "id": "api_accepts_bearer_token",
                    "owner": "Lexmount API",
                    "required_change": (
                        "Session, context, and action-related APIs accept project-bound "
                        "scoped bearer tokens with browser.* permissions."
                    ),
                },
                {
                    "id": "browser_gateway_accepts_bearer_token",
                    "owner": "Lexmount browser gateway",
                    "required_change": (
                        "Browser CDP websocket connection can be authorized with a "
                        "short-lived bearer token instead of an api_key query parameter."
                    ),
                },
            ],
        },
        "next_steps": [
            "Run `browser-cli doctor --json` to verify live API connectivity.",
            "Continue using env API-key credentials for browser actions until bearer-token runtime support lands.",
        ],
    }
    assert "fix" not in payload
    assert payload["api_key"] == {
        "present": True,
        "masked_value": "***",
        "length": len("local-secret"),
    }
    assert payload["project_id"]["value"] == "project"
    assert payload["base_url"] == {
        "present": True,
        "value": "https://api.example.test",
        "default": "https://api.lexmount.cn",
        "effective_value": "https://api.example.test",
        "using_default": False,
    }
    assert payload["region"]["value"] == "cn"
    assert "browser-cli doctor --json" in payload["next_steps"][0]


def test_auth_status_reports_connect_from_codex_fix_when_env_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LEXMOUNT_API_KEY", raising=False)
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "status-project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "status"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert payload["ok"] is True
    assert payload["configured"] is False
    assert payload["runtime_auth_usable"] is False
    assert payload["runtime_auth"]["usable"] is False
    assert payload["runtime_auth"]["source"] == "missing"
    assert payload["runtime_auth"]["browser_actions_auth"] == "missing_env_api_key"
    assert payload["runtime_auth"]["fallback_missing_env"] == ["LEXMOUNT_API_KEY"]
    assert payload["runtime_auth"]["bearer_runtime"]["reason"] == (
        "no_device_token_metadata"
    )
    assert payload["missing_env"] == ["LEXMOUNT_API_KEY"]
    assert payload["auth_source"] == "missing"
    assert "status-project" in serialized
    assert "api_key=" not in serialized
    fix = payload["fix"]
    assert fix["code"] == "configure_credentials"
    assert fix["env"] == ["LEXMOUNT_API_KEY"]
    assert fix["commands"] == [
        "browser-cli auth login",
        "browser-cli auth export-env",
        "browser-cli auth status",
        "browser-cli doctor --json",
    ]
    connect = fix["connect_from_codex"]
    assert connect["url"].startswith("https://browser.lexmount.cn/connect/codex?")
    assert connect["project_id"] == "status-project"
    assert connect["project_id_source"] == "env"
    assert connect["open_command"] == "browser-cli auth login --open"
    assert [item["id"] for item in connect["required_runtime_auth"]] == [
        "sdk_accepts_bearer_token",
        "api_accepts_bearer_token",
        "browser_gateway_accepts_bearer_token",
    ]
    assert connect["site_capability_status"]["missing"] == [
        "project_id_display",
        "scoped_api_key",
        "copy_install_and_env",
        "doctor_verification",
        "scoped_key_lifecycle",
        "device_code_oauth",
    ]
    assert connect["verification"]["doctor_command"] == "browser-cli doctor --json"


def test_auth_status_reports_device_token_file_without_revealing_tokens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LEXMOUNT_API_KEY", raising=False)
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)
    monkeypatch.setattr(
        cli_module,
        "_now_utc",
        lambda: datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc),
    )
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "kind": "device_token",
                "project_id": "project",
                "api_base_url": "https://api.lexmount.cn",
                "access_token": "secret-access-token",
                "refresh_token": "secret-refresh-token",
                "expires_at": "2026-06-26T01:00:00Z",
                "scopes": ["browser.sessions:create"],
                "token_id": "tok_123",
            }
        )
    )
    credentials_file.chmod(0o600)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "status", "--credentials-file", str(credentials_file)])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "secret-access-token" not in serialized
    assert "secret-refresh-token" not in serialized
    assert payload["configured"] is False
    assert payload["auth_source"] == "device_token"
    assert payload["runtime_auth_usable"] is False
    assert payload["runtime_auth"]["usable"] is False
    assert payload["runtime_auth"]["source"] == "device_token_pending_runtime"
    assert payload["runtime_auth"]["fallback_missing_env"] == [
        "LEXMOUNT_API_KEY",
        "LEXMOUNT_PROJECT_ID",
    ]
    bearer_runtime = payload["runtime_auth"]["bearer_runtime"]
    assert bearer_runtime["available"] is False
    assert bearer_runtime["reason"] == "bearer_token_runtime_not_enabled"
    assert bearer_runtime["device_token_present"] is True
    assert bearer_runtime["device_token_valid"] is True
    assert [item["id"] for item in bearer_runtime["required_support"]] == [
        "sdk_accepts_bearer_token",
        "api_accepts_bearer_token",
        "browser_gateway_accepts_bearer_token",
    ]
    token = payload["device_token"]
    assert token["present"] is True
    assert token["path"] == str(credentials_file)
    assert token["path_source"] == "argument"
    assert token["kind"] == "device_token"
    assert token["valid"] is True
    assert token["expired"] is False
    assert token["refresh_needed"] is False
    assert token["expires_in_seconds"] == 3600
    assert token["project_id"] == "project"
    assert token["api_base_url"] == "https://api.lexmount.cn"
    assert token["scopes"] == ["browser.sessions:create"]
    assert token["scope_count"] == 1
    assert token["token_id"] == "tok_123"
    assert token["has_access_token"] is True
    assert token["has_refresh_token"] is True
    assert token["usable_for_runtime"] is False
    assert token["warnings"] == []
    if "file_mode_ok" in token:
        assert token["file_mode"] == "0o600"
        assert token["file_mode_ok"] is True
    assert (
        "browser actions still require env API-key credentials"
        in payload["next_steps"][0]
    )


def test_auth_status_reports_expired_device_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LEXMOUNT_API_KEY", raising=False)
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)
    monkeypatch.setattr(
        cli_module,
        "_now_utc",
        lambda: datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc),
    )
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "kind": "device_token",
                "project_id": "project",
                "access_token": "secret-access-token",
                "expires_at": "2026-06-25T23:59:00Z",
            }
        )
    )
    credentials_file.chmod(0o600)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "status", "--credentials-file", str(credentials_file)])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "secret-access-token" not in serialized
    assert payload["auth_source"] == "device_token"
    token = payload["device_token"]
    assert token["valid"] is False
    assert token["expired"] is True
    assert token["refresh_needed"] is True
    assert token["expires_in_seconds"] == -60
    assert "Device token is expired." in token["warnings"]
    assert payload["next_steps"][0] == (
        "Local device-token metadata is present but not currently valid."
    )


def test_auth_token_info_reports_scope_check_without_revealing_tokens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module,
        "_now_utc",
        lambda: datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc),
    )
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "kind": "device_token",
                "project_id": "project",
                "api_base_url": "https://api.lexmount.cn",
                "access_token": "secret-access-token",
                "refresh_token": "secret-refresh-token",
                "expires_at": "2026-06-26T01:00:00Z",
                "scopes": ["browser.sessions:create", "browser.actions:run"],
                "token_id": "tok_123",
            }
        )
    )
    credentials_file.chmod(0o600)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "token-info",
                "--credentials-file",
                str(credentials_file),
                "--required-scope",
                "browser.actions:run",
                "--required-scope",
                "browser.contexts:create",
                "--required-scope",
                "browser.actions:run",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "secret-access-token" not in serialized
    assert "secret-refresh-token" not in serialized
    assert payload["command"] == "auth.token-info"
    assert payload["present"] is True
    assert payload["valid"] is True
    assert payload["expired"] is False
    assert payload["refresh_needed"] is False
    assert payload["runtime_auth_usable"] is False
    assert payload["device_token"]["token_id"] == "tok_123"
    assert payload["scope_check"] == {
        "required_scopes": ["browser.actions:run", "browser.contexts:create"],
        "available_scopes": ["browser.sessions:create", "browser.actions:run"],
        "missing_scopes": ["browser.contexts:create"],
        "satisfied": False,
    }
    assert "missing one or more requested scopes" in payload["next_steps"][0]


def test_auth_token_info_reports_missing_credentials_file(
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    credentials_file = tmp_path / "missing.json"

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "token-info", "--credentials-file", str(credentials_file)])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "auth.token-info"
    assert payload["present"] is False
    assert payload["valid"] is False
    assert payload["expired"] is None
    assert payload["refresh_needed"] is None
    assert payload["runtime_auth_usable"] is False
    assert payload["device_token"]["path"] == str(credentials_file)
    assert payload["device_token"]["path_source"] == "argument"
    assert payload["scope_check"] == {
        "required_scopes": [],
        "available_scopes": [],
        "missing_scopes": [],
        "satisfied": True,
    }
    assert payload["next_steps"][0] == "No local device-token metadata was found."


def test_auth_refresh_reports_remote_refresh_pending_without_revealing_tokens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module,
        "_now_utc",
        lambda: datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc),
    )
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "kind": "device_token",
                "project_id": "project",
                "api_base_url": "https://api.lexmount.cn",
                "access_token": "secret-access-token",
                "refresh_token": "secret-refresh-token",
                "expires_at": "2026-06-25T23:59:00Z",
                "scopes": ["browser.sessions:create"],
                "token_id": "tok_123",
            }
        )
    )
    credentials_file.chmod(0o600)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "refresh", "--credentials-file", str(credentials_file)])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "secret-access-token" not in serialized
    assert "secret-refresh-token" not in serialized
    assert payload["command"] == "auth.refresh"
    assert payload["credentials_file"] == str(credentials_file)
    assert payload["path_source"] == "argument"
    assert payload["present"] is True
    assert payload["valid"] is False
    assert payload["expired"] is True
    assert payload["refresh_needed"] is True
    assert payload["has_refresh_token"] is True
    assert payload["force_requested"] is False
    assert payload["refresh_requested"] is True
    assert payload["refresh_available"] is False
    assert payload["refreshed"] is False
    assert payload["reason"] == "remote_refresh_unavailable"
    assert payload["runtime_auth_usable"] is False
    assert "Device token is expired." in payload["warnings"]
    assert "Remote token refresh is not implemented yet" in payload["warnings"][-1]
    assert payload["device_token"]["token_id"] == "tok_123"
    assert payload["next_steps"][0] == "Local device-token metadata is expired."


def test_auth_refresh_calls_configured_token_endpoint_and_saves_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module,
        "_now_utc",
        lambda: datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc),
    )
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "kind": "device_token",
                "project_id": "project",
                "api_base_url": "https://api.lexmount.cn",
                "access_token": "old-secret-access-token",
                "refresh_token": "secret-refresh-token",
                "expires_at": "2026-06-25T23:59:00Z",
                "scopes": ["browser.sessions:create"],
                "token_id": "tok_123",
            }
        )
    )
    credentials_file.chmod(0o600)
    calls: list[tuple[str, dict[str, Any], float]] = []

    def fake_post(
        url: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        calls.append((url, payload, timeout_seconds))
        return {
            "ok": True,
            "status_code": 200,
            "error": None,
            "message": None,
            "json": {
                "access_token": "new-secret-access-token",
                "expires_in": 3600,
                "token_id": "tok_456",
            },
        }

    monkeypatch.setattr(cli_module, "_json_http_post", fake_post)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "refresh",
                "--credentials-file",
                str(credentials_file),
                "--token-base-url",
                "https://browser.lexmount.cn",
                "--http-timeout-seconds",
                "7",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "old-secret-access-token" not in serialized
    assert "new-secret-access-token" not in serialized
    assert "secret-refresh-token" not in serialized
    assert calls == [
        (
            "https://browser.lexmount.cn/api/auth/token/refresh",
            {
                "client_name": "browser-cli",
                "refresh_token": "secret-refresh-token",
                "token_id": "tok_123",
                "project_id": "project",
            },
            7,
        )
    ]
    assert payload["token_lifecycle_base_url"] == "https://browser.lexmount.cn"
    assert payload["token_lifecycle_base_url_source"] == "argument"
    assert (
        payload["refresh_endpoint"]
        == "https://browser.lexmount.cn/api/auth/token/refresh"
    )
    assert payload["refresh_available"] is True
    assert payload["refreshed"] is True
    assert payload["reason"] == "refreshed"
    assert payload["remote_refresh"]["attempted"] is True
    assert payload["remote_refresh"]["status_code"] == 200
    assert payload["credentials"]["saved"] is True
    assert payload["credentials"]["device_token"]["token_id"] == "tok_456"
    assert (
        payload["next_steps"][0]
        == "Local device-token metadata was refreshed and saved."
    )
    stored = json.loads(credentials_file.read_text())
    assert stored["access_token"] == "new-secret-access-token"
    assert stored["refresh_token"] == "secret-refresh-token"
    assert stored["project_id"] == "project"
    assert stored["scopes"] == ["browser.sessions:create"]


def test_auth_refresh_reports_not_needed_without_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module,
        "_now_utc",
        lambda: datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc),
    )
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "kind": "device_token",
                "project_id": "project",
                "access_token": "secret-access-token",
                "refresh_token": "secret-refresh-token",
                "expires_at": "2026-06-26T01:00:00Z",
            }
        )
    )
    credentials_file.chmod(0o600)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "refresh", "--credentials-file", str(credentials_file)])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "secret-access-token" not in serialized
    assert "secret-refresh-token" not in serialized
    assert payload["valid"] is True
    assert payload["refresh_needed"] is False
    assert payload["refresh_requested"] is False
    assert payload["refresh_available"] is False
    assert payload["refreshed"] is False
    assert payload["reason"] == "refresh_not_needed"
    assert payload["warnings"] == []
    assert "does not currently need refresh" in payload["next_steps"][0]


def test_auth_refresh_missing_credentials_file_is_actionable(
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    credentials_file = tmp_path / "missing.json"

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "refresh", "--credentials-file", str(credentials_file)])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "auth.refresh"
    assert payload["credentials_file"] == str(credentials_file)
    assert payload["path_source"] == "argument"
    assert payload["present"] is False
    assert payload["valid"] is False
    assert payload["expired"] is None
    assert payload["refresh_needed"] is None
    assert payload["has_refresh_token"] is False
    assert payload["refresh_requested"] is True
    assert payload["refresh_available"] is False
    assert payload["refreshed"] is False
    assert payload["reason"] == "missing_credentials_file"
    assert payload["warnings"] == []
    assert payload["device_token"]["present"] is False
    assert payload["next_steps"][0] == "No local device-token metadata was found."


def test_auth_logout_deletes_device_token_file_without_revealing_tokens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module,
        "_now_utc",
        lambda: datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc),
    )
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "kind": "device_token",
                "project_id": "project",
                "access_token": "secret-access-token",
                "refresh_token": "secret-refresh-token",
                "expires_at": "2026-06-26T01:00:00Z",
                "scopes": ["browser.actions:run"],
                "token_id": "tok_123",
            }
        )
    )
    credentials_file.chmod(0o600)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "logout", "--credentials-file", str(credentials_file)])

    assert exc_info.value.code == 0
    assert not credentials_file.exists()
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "secret-access-token" not in serialized
    assert "secret-refresh-token" not in serialized
    assert payload["command"] == "auth.logout"
    assert payload["credentials_file"] == str(credentials_file)
    assert payload["path_source"] == "argument"
    assert payload["present_before"] is True
    assert payload["present_after"] is False
    assert payload["deleted"] is True
    assert payload["env_unchanged"] is True
    assert payload["revoke_requested"] is False
    assert payload["revoke_available"] is False
    assert payload["warnings"] == []
    assert payload["device_token_before"]["token_id"] == "tok_123"
    assert payload["next_steps"][0] == "Local device-token metadata was removed."


def test_auth_logout_missing_file_is_idempotent(
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    credentials_file = tmp_path / "missing.json"

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "logout", "--credentials-file", str(credentials_file)])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "auth.logout"
    assert payload["present_before"] is False
    assert payload["present_after"] is False
    assert payload["deleted"] is False
    assert payload["env_unchanged"] is True
    assert payload["warnings"] == []
    assert payload["device_token_before"]["present"] is False
    assert (
        payload["next_steps"][0] == "No local device-token metadata file was removed."
    )


def test_auth_logout_revoke_flag_reports_remote_revoke_pending(
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "kind": "device_token",
                "project_id": "project",
                "access_token": "secret-access-token",
                "expires_at": "2026-06-26T01:00:00Z",
            }
        )
    )
    credentials_file.chmod(0o600)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "logout",
                "--credentials-file",
                str(credentials_file),
                "--revoke",
            ]
        )

    assert exc_info.value.code == 0
    assert not credentials_file.exists()
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "secret-access-token" not in serialized
    assert payload["deleted"] is True
    assert payload["revoke_requested"] is True
    assert payload["revoke_available"] is False
    assert payload["warnings"] == [
        "Remote token revoke is not implemented yet; remove local metadata and revoke from browser.lexmount.cn if needed."
    ]
    assert "Remote revoke is not implemented" in payload["next_steps"][-1]


def test_auth_logout_revoke_calls_configured_token_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module,
        "_now_utc",
        lambda: datetime(2026, 6, 26, 0, 0, tzinfo=timezone.utc),
    )
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "kind": "device_token",
                "project_id": "project",
                "access_token": "secret-access-token",
                "refresh_token": "secret-refresh-token",
                "expires_at": "2026-06-26T01:00:00Z",
                "scopes": ["browser.actions:run"],
                "token_id": "tok_123",
            }
        )
    )
    credentials_file.chmod(0o600)
    calls: list[tuple[str, dict[str, Any], float]] = []

    def fake_post(
        url: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        calls.append((url, payload, timeout_seconds))
        return {
            "ok": True,
            "status_code": 200,
            "error": None,
            "message": None,
            "json": {"revoked": True},
        }

    monkeypatch.setattr(cli_module, "_json_http_post", fake_post)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "logout",
                "--credentials-file",
                str(credentials_file),
                "--revoke",
                "--token-base-url",
                "https://browser.lexmount.cn",
                "--http-timeout-seconds",
                "8",
            ]
        )

    assert exc_info.value.code == 0
    assert not credentials_file.exists()
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "secret-access-token" not in serialized
    assert "secret-refresh-token" not in serialized
    assert calls == [
        (
            "https://browser.lexmount.cn/api/auth/token/revoke",
            {
                "client_name": "browser-cli",
                "access_token": "secret-access-token",
                "refresh_token": "secret-refresh-token",
                "token_id": "tok_123",
                "project_id": "project",
            },
            8,
        )
    ]
    assert payload["token_lifecycle_base_url"] == "https://browser.lexmount.cn"
    assert payload["token_lifecycle_base_url_source"] == "argument"
    assert (
        payload["revoke_endpoint"]
        == "https://browser.lexmount.cn/api/auth/token/revoke"
    )
    assert payload["revoke_available"] is True
    assert payload["revoked"] is True
    assert payload["remote_revoke"]["attempted"] is True
    assert payload["remote_revoke"]["status_code"] == 200
    assert payload["deleted"] is True
    assert payload["next_steps"][-1] == "Remote token revoke completed."


def test_auth_export_env_emits_safe_placeholders(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "export-env"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "auth.export-env"
    assert payload["shell"] == "posix"
    assert payload["secrets_revealed"] is False
    assert payload["usable"] is False
    assert payload["unusable_exports"] == [
        "LEXMOUNT_API_KEY",
        "LEXMOUNT_PROJECT_ID",
    ]
    assert payload["warnings"] == []
    assert payload["commands"] == [
        "export LEXMOUNT_API_KEY='<api-key>'",
        "export LEXMOUNT_PROJECT_ID='<project-id>'",
    ]
    assert payload["exports"][0]["usable"] is False
    assert payload["exports"][1]["usable"] is False
    assert "Replace placeholder or redacted export values" in payload["next_steps"][0]
    assert payload["next_steps"][-1] == (
        "Run `browser-cli doctor --json` to verify credentials."
    )


def test_auth_export_env_from_current_masks_api_key_by_default(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "local-secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.setenv("LEXMOUNT_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("LEXMOUNT_REGION", "cn")

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "export-env",
                "--from-current",
                "--include-base-url",
                "--include-region",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    serialized = json.dumps(payload)
    assert "local-secret" not in serialized
    assert payload["commands"] == [
        "export LEXMOUNT_API_KEY='<redacted-api-key>'",
        "export LEXMOUNT_PROJECT_ID=project",
        "export LEXMOUNT_BASE_URL=https://api.example.test",
        "export LEXMOUNT_REGION=cn",
    ]
    assert payload["warnings"]
    assert payload["usable"] is False
    assert payload["unusable_exports"] == ["LEXMOUNT_API_KEY"]
    assert "Replace placeholder or redacted export values" in payload["next_steps"][0]
    assert payload["exports"][0]["source"] == "env"
    assert payload["exports"][0]["usable"] is False
    assert payload["exports"][1]["source"] == "env"
    assert payload["exports"][1]["usable"] is True


def test_auth_export_env_can_reveal_current_secret_explicitly(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "local-secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "export-env", "--from-current", "--reveal-secrets"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["secrets_revealed"] is True
    assert payload["usable"] is True
    assert payload["unusable_exports"] == []
    assert payload["next_steps"][0] == "Run the export commands in the local shell."
    assert payload["warnings"] == []
    assert "local-secret" in payload["script"]
    assert payload["exports"][0]["usable"] is True


def test_auth_scopes_lists_machine_readable_scope_catalog(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "scopes"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "auth.scopes"
    assert payload["schema_version"] == 1
    assert payload["known_scopes"] == [
        "browser:sessions",
        "browser:contexts",
        "browser:actions",
    ]
    assert payload["default_scopes"] == payload["known_scopes"]
    assert payload["requested_scopes"] == payload["default_scopes"]
    assert payload["scope_count"] == 3
    assert payload["known_scope_count"] == 3
    assert payload["unknown_scopes"] == []
    assert payload["all_selected_scopes_known"] is True
    assert payload["scope_query_parameter"] == {
        "name": "scope",
        "repeatable": True,
        "default": [
            "browser:sessions",
            "browser:contexts",
            "browser:actions",
        ],
    }
    assert payload["secret_policy"]["contains_secret_values"] is False
    assert payload["secret_policy"]["safe_to_share"] is True
    assert "browser-cli auth connect-requirements" in payload["next_commands"]

    action_scope = payload["scopes"][2]
    assert action_scope["scope"] == "browser:actions"
    assert action_scope["known"] is True
    assert action_scope["label"] == "Browser actions"
    assert action_scope["permissions"] == ["browser.actions:run"]
    assert action_scope["permission_count"] == 1
    assert action_scope["default_requested"] is True
    assert action_scope["risk"] == "high"
    assert action_scope["query_parameter"] == "scope=browser:actions"
    assert action_scope["browser_site_ui"]["risk_field"] == "risk"
    assert "browser_site_contract" not in payload


def test_auth_scopes_filters_unknown_scopes_and_reports_site_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "env-project")

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "scopes",
                "--scope",
                "browser:actions",
                "--scope",
                "browser:future",
                "--include-site-contract",
                "--project-id",
                "arg-project",
                "--expires-in",
                "24h",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "auth.scopes"
    assert payload["requested_scopes"] == ["browser:actions", "browser:future"]
    assert payload["scope_count"] == 2
    assert payload["known_scope_count"] == 1
    assert payload["unknown_scopes"] == ["browser:future"]
    assert payload["all_selected_scopes_known"] is False
    assert payload["scopes"][0]["permission_count"] == 1
    assert payload["scopes"][1] == {
        "scope": "browser:future",
        "known": False,
        "label": "browser:future",
        "description": "Custom or future scope requested by the caller.",
        "permissions": ["browser:future"],
        "risk": "unknown",
        "destructive": None,
        "default_requested": False,
        "permission_count": 1,
        "query_parameter": "scope=browser:future",
        "browser_site_ui": {
            "label_field": "label",
            "description_field": "description",
            "permissions_field": "permissions",
            "risk_field": "risk",
            "destructive_field": "destructive",
            "default_checked_field": "default_requested",
            "custom_scope": True,
        },
    }

    contract = payload["browser_site_contract"]
    assert contract["available"] is False
    assert contract["reason"] == "browser_site_contract_pending"
    assert contract["project_id"] == "arg-project"
    assert contract["project_id_source"] == "argument"
    assert contract["requested_scopes"] == ["browser:actions", "browser:future"]
    assert contract["requested_scope_details"][1]["known"] is False
    assert contract["requested_expires_in"] == "24h"
    assert contract["scope_catalog_command"] == "browser-cli auth scopes"
    assert contract["scope_ui_fields"] == [
        "scope",
        "label",
        "description",
        "permissions",
        "risk",
        "destructive",
        "default_requested",
        "permission_count",
    ]
    assert "scope=<scope> (repeatable)" in contract["required_query_parameters"]
    assert [item["id"] for item in contract["required_token_lifecycle"]] == [
        "issue_scoped_key",
        "refresh_token",
        "revoke_token",
        "expire_token",
    ]
    assert (
        contract["required_token_lifecycle"][1]["endpoint"]
        == "POST /api/auth/token/refresh"
    )
    assert (
        "LEXMOUNT_BROWSER_TOKEN_BASE_URL"
        in contract["required_token_lifecycle"][1]["configure_with"]
    )
    assert (
        contract["required_token_lifecycle"][2]["endpoint"]
        == "POST /api/auth/token/revoke"
    )
    assert [item["id"] for item in contract["required_runtime_auth"]] == [
        "sdk_accepts_bearer_token",
        "api_accepts_bearer_token",
        "browser_gateway_accepts_bearer_token",
    ]
    assert contract["required_runtime_auth"][0]["required"] is True
    assert contract["site_capability_status"]["missing_count"] == 6
    assert any(
        "permission names" in item for item in contract["browser_site_requirements"]
    )
    env_query = parse_qs(urlsplit(contract["url"]).query)
    assert env_query["project_id"] == ["arg-project"]
    assert env_query["scope"] == ["browser:actions", "browser:future"]
    assert env_query["expires_in"] == ["24h"]
    assert env_query["response"] == ["env"]
    device_query = parse_qs(urlsplit(contract["device_code_url"]).query)
    assert device_query["response"] == ["device_code"]
    assert "real-api-key-value" not in json.dumps(payload)


def test_auth_connect_requirements_reports_browser_site_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "env-project")

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "connect-requirements",
                "--project-id",
                "arg-project",
                "--scope",
                "browser:actions",
                "--scope",
                "browser:actions",
                "--expires-in",
                "24h",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "auth.connect-requirements"
    assert payload["available"] is False
    assert payload["reason"] == "browser_site_contract_pending"
    assert payload["project_id"] == "arg-project"
    assert payload["project_id_source"] == "argument"
    assert payload["requested_scopes"] == ["browser:actions"]
    assert payload["requested_expires_in"] == "24h"
    assert payload["requested_scope_details"][0]["label"] == "Browser actions"
    assert payload["requested_scope_details"][0]["risk"] == "high"
    assert [block["id"] for block in payload["setup_blocks"]] == [
        "install",
        "open_connect",
        "local_env",
        "verify",
    ]
    assert payload["setup_blocks"][2]["commands"][-1] == (
        "export LEXMOUNT_PROJECT_ID=arg-project"
    )
    assert payload["required_device_code_endpoints"] == [
        "POST /api/auth/device/code",
        "POST /api/auth/device/token",
    ]
    assert any(
        "bearer-token authentication" in item
        for item in payload["required_device_code_support"]
    )
    assert [item["id"] for item in payload["required_token_lifecycle"]] == [
        "issue_scoped_key",
        "refresh_token",
        "revoke_token",
        "expire_token",
    ]
    assert (
        payload["required_token_lifecycle"][1]["endpoint"]
        == "POST /api/auth/token/refresh"
    )
    assert (
        payload["required_token_lifecycle"][2]["endpoint"]
        == "POST /api/auth/token/revoke"
    )
    assert [item["id"] for item in payload["required_runtime_auth"]] == [
        "sdk_accepts_bearer_token",
        "api_accepts_bearer_token",
        "browser_gateway_accepts_bearer_token",
    ]
    assert payload["required_runtime_auth"][1]["owner"] == "Lexmount API"
    assert (
        payload["required_api_contract"]["device_code"][0]["path"]
        == "/api/auth/device/code"
    )
    assert payload["required_api_contract"]["device_code"][1]["secret_fields"] == [
        "access_token",
        "refresh_token",
    ]
    assert payload["verification"]["workflow_command"] == (
        "browser-cli commands --workflow connect_from_codex_site_requirements"
    )
    assert "browser-cli auth connect-requirements" in payload["agent_commands"]
    assert payload["secret_policy"]["contains_secret_values"] is False
    assert "LEXMOUNT_API_KEY" in payload["secret_policy"]["do_not_paste_in_chat"]

    connect = payload["connect_from_codex"]
    assert connect["url"].startswith("https://browser.lexmount.cn/connect/codex?")
    assert connect["device_code_url"].startswith(
        "https://browser.lexmount.cn/connect/codex?"
    )
    assert connect["project_id"] == "arg-project"
    assert connect["requested_scopes"] == ["browser:actions"]
    assert connect["setup_blocks"] == payload["setup_blocks"]
    assert connect["required_runtime_auth"] == payload["required_runtime_auth"]
    assert connect["supported_response_modes"] == ["env", "device_code"]
    assert "response=env|device_code" in connect["required_query_parameters"]
    capability_ids = [
        "project_id_display",
        "scoped_api_key",
        "copy_install_and_env",
        "doctor_verification",
        "scoped_key_lifecycle",
        "device_code_oauth",
    ]
    assert connect["site_capability_status"] == {
        "available": False,
        "available_count": 0,
        "missing_count": len(capability_ids),
        "missing": capability_ids,
    }
    assert [item["id"] for item in connect["site_capabilities"]] == capability_ids
    assert any(
        "scope" in item and "expires_in" in item
        for item in connect["browser_site_requirements"]
    )

    env_query = parse_qs(urlsplit(connect["url"]).query)
    assert env_query["project_id"] == ["arg-project"]
    assert env_query["scope"] == ["browser:actions"]
    assert env_query["expires_in"] == ["24h"]
    assert env_query["response"] == ["env"]
    device_query = parse_qs(urlsplit(connect["device_code_url"]).query)
    assert device_query["response"] == ["device_code"]
    assert "arg-project" in json.dumps(payload)
    assert "real-api-key-value" not in json.dumps(payload)


def test_auth_login_guides_manual_browser_flow(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "login"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "auth.login"
    assert payload["flow"] == "manual_env"
    assert payload["selected_flow"] == "manual_env"
    assert payload["available"] is True
    assert payload["manual_env_available"] is True
    assert payload["login_url"] == "https://browser.lexmount.cn"
    assert payload["device_code_available"] is False
    assert payload["flows"][0]["name"] == "manual_env"
    assert payload["flows"][0]["available"] is True
    assert payload["flows"][1]["name"] == "connect_from_codex"
    assert payload["flows"][1]["available"] is False
    assert "browser-cli doctor --json" in payload["commands"]

    handoff = payload["handoff"]
    assert handoff["recommended_flow"] == "manual_env"
    assert handoff["login_url"] == "https://browser.lexmount.cn"
    assert handoff["connect_from_codex_url"].startswith(
        "https://browser.lexmount.cn/connect/codex?"
    )
    assert handoff["connect_from_codex_available"] is False
    assert handoff["open_command"] == "browser-cli auth login --open"
    assert handoff["open_url"] == handoff["connect_from_codex_url"]
    assert handoff["install_command"] == (
        "uv tool install git+https://github.com/lexmount/browser-cli.git"
    )
    assert [block["id"] for block in handoff["setup_blocks"]] == [
        "install",
        "open_connect",
        "local_env",
        "verify",
    ]
    assert handoff["setup_blocks"][0]["commands"] == [
        "uv tool install git+https://github.com/lexmount/browser-cli.git",
        "browser-cli --help",
        "browser-cli --version",
    ]
    assert handoff["setup_blocks"][2] == {
        "id": "local_env",
        "label": "Configure local shell",
        "commands": [
            "browser-cli auth export-env",
            "export LEXMOUNT_API_KEY='<api-key>'",
            "export LEXMOUNT_PROJECT_ID='<project-id>'",
        ],
        "secret_env": ["LEXMOUNT_API_KEY"],
        "contains_secret_values": False,
        "contains_secret_placeholders": True,
        "safe_to_paste_in_chat": False,
        "local_shell_only": True,
    }
    assert handoff["setup_blocks"][3]["commands"] == [
        "browser-cli auth status",
        "browser-cli doctor --json",
        "browser-cli doctor --smoke-session",
    ]
    assert handoff["setup_blocks"][3]["safe_to_paste_in_chat"] is True
    assert handoff["copyable_commands"] == [
        "browser-cli auth status",
        "browser-cli auth login",
        "browser-cli auth export-env",
        "browser-cli doctor --json",
    ]
    assert handoff["local_env"] == [
        {
            "name": "LEXMOUNT_API_KEY",
            "secret": True,
            "required": True,
            "source": "browser.lexmount.cn scoped API key",
        },
        {
            "name": "LEXMOUNT_PROJECT_ID",
            "secret": False,
            "required": True,
            "source": "browser.lexmount.cn Project ID",
            "value": None,
            "value_source": "unset",
        },
    ]
    assert handoff["verification"]["doctor_command"] == "browser-cli doctor --json"
    assert "LEXMOUNT_API_KEY" in handoff["secret_policy"]["do_not_paste_in_chat"]
    assert (
        "browser-cli auth export-env output without --reveal-secrets"
        in handoff["secret_policy"]["safe_to_share"]
    )

    connect = payload["connect_from_codex"]
    assert connect["available"] is False
    assert connect["project_id"] is None
    assert connect["project_id_source"] == "unset"
    assert connect["setup_blocks"] == handoff["setup_blocks"]
    capability_ids = [
        "project_id_display",
        "scoped_api_key",
        "copy_install_and_env",
        "doctor_verification",
        "scoped_key_lifecycle",
        "device_code_oauth",
    ]
    assert connect["site_capability_status"] == {
        "available": False,
        "available_count": 0,
        "missing_count": len(capability_ids),
        "missing": capability_ids,
    }
    assert [item["id"] for item in connect["site_capabilities"]] == capability_ids
    assert all(item["available"] is False for item in connect["site_capabilities"])
    lifecycle = next(
        item
        for item in connect["site_capabilities"]
        if item["id"] == "scoped_key_lifecycle"
    )
    assert "permission labels" in lifecycle["browser_site_action"]
    assert "expiration" in lifecycle["browser_site_action"]
    assert "revoke" in lifecycle["browser_site_action"]
    assert (
        "`browser-cli doctor --json` verification guidance"
        in connect["expected_outputs"]
    )
    assert connect["requested_scopes"] == [
        "browser:sessions",
        "browser:contexts",
        "browser:actions",
    ]
    assert [item["scope"] for item in connect["requested_scope_details"]] == [
        "browser:sessions",
        "browser:contexts",
        "browser:actions",
    ]
    assert connect["requested_scope_details"][0] == {
        "scope": "browser:sessions",
        "known": True,
        "label": "Browser sessions",
        "description": "Create, list, inspect, keep alive, and close browser sessions.",
        "permissions": [
            "browser.sessions:create",
            "browser.sessions:list",
            "browser.sessions:read",
            "browser.sessions:close",
        ],
        "risk": "medium",
        "destructive": False,
    }
    context_detail = connect["requested_scope_details"][1]
    assert context_detail["label"] == "Persistent browser contexts"
    assert context_detail["destructive"] is True
    assert "browser.contexts:delete" in context_detail["permissions"]
    action_detail = connect["requested_scope_details"][2]
    assert action_detail["label"] == "Browser actions"
    assert action_detail["risk"] == "high"
    assert action_detail["permissions"] == ["browser.actions:run"]
    assert [item["id"] for item in connect["required_runtime_auth"]] == [
        "sdk_accepts_bearer_token",
        "api_accepts_bearer_token",
        "browser_gateway_accepts_bearer_token",
    ]
    assert handoff["requested_scope_details"] == connect["requested_scope_details"]
    assert connect["requested_expires_in"] == "7d"
    assert connect["url"].startswith("https://browser.lexmount.cn/connect/codex?")
    query = parse_qs(urlsplit(connect["url"]).query)
    assert query["source"] == ["browser-cli"]
    assert query["intent"] == ["agent-browser-control"]
    assert query["response"] == ["env"]
    assert query["expires_in"] == ["7d"]
    assert query["scope"] == [
        "browser:sessions",
        "browser:contexts",
        "browser:actions",
    ]
    assert "project_id" not in query
    assert any(
        "scoped API keys" in item for item in payload["browser_site_recommendations"]
    )
    assert any(
        "/connect/codex" in item for item in connect["browser_site_requirements"]
    )
    assert payload["open_result"] == {
        "requested": False,
        "url": connect["url"],
        "opened": False,
    }
    assert any("browser-cli doctor --json" in step for step in payload["steps"])
    assert payload["warnings"] == []


def test_auth_login_builds_connect_from_codex_contract_from_args(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "env-project")

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "login",
                "--project-id",
                "arg-project",
                "--scope",
                "browser:sessions",
                "--scope",
                "browser:actions",
                "--scope",
                "browser:sessions",
                "--expires-in",
                "24h",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    connect = payload["connect_from_codex"]
    assert connect["project_id"] == "arg-project"
    assert connect["project_id_source"] == "argument"
    assert connect["requested_scopes"] == ["browser:sessions", "browser:actions"]
    assert [item["scope"] for item in connect["requested_scope_details"]] == [
        "browser:sessions",
        "browser:actions",
    ]
    assert (
        payload["handoff"]["requested_scope_details"]
        == connect["requested_scope_details"]
    )
    assert connect["requested_expires_in"] == "24h"
    assert payload["handoff"]["local_env"][1]["value"] == "arg-project"
    assert payload["handoff"]["local_env"][1]["value_source"] == "argument"
    assert payload["handoff"]["requested_scopes"] == [
        "browser:sessions",
        "browser:actions",
    ]
    assert payload["handoff"]["requested_expires_in"] == "24h"
    assert payload["handoff"]["setup_blocks"][2]["commands"][-1] == (
        "export LEXMOUNT_PROJECT_ID=arg-project"
    )
    assert connect["setup_blocks"] == payload["handoff"]["setup_blocks"]
    assert connect["required_runtime_auth"][0]["owner"] == "lexmount-python-sdk"

    query = parse_qs(urlsplit(connect["url"]).query)
    assert query["project_id"] == ["arg-project"]
    assert query["scope"] == ["browser:sessions", "browser:actions"]
    assert query["expires_in"] == ["24h"]


def test_auth_login_reports_unknown_scope_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "login", "--scope", "browser:future"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    connect = payload["connect_from_codex"]
    assert connect["requested_scopes"] == ["browser:future"]
    assert connect["requested_scope_details"] == [
        {
            "scope": "browser:future",
            "known": False,
            "label": "browser:future",
            "description": "Custom or future scope requested by the caller.",
            "permissions": ["browser:future"],
            "risk": "unknown",
            "destructive": None,
        }
    ]
    assert (
        payload["handoff"]["requested_scope_details"]
        == connect["requested_scope_details"]
    )


def test_auth_login_device_code_reports_pending_browser_site_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)
    opened: list[str] = []
    monkeypatch.setattr(
        cli_module.webbrowser,
        "open",
        lambda url: opened.append(url) or True,
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "login",
                "--device-code",
                "--open",
                "--project-id",
                "arg-project",
                "--scope",
                "browser:actions",
                "--expires-in",
                "24h",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "auth.login"
    assert payload["flow"] == "device_code"
    assert payload["selected_flow"] == "device_code"
    assert payload["available"] is False
    assert payload["manual_env_available"] is True
    assert payload["device_code_available"] is False
    assert payload["reason"] == "browser_site_endpoint_missing"
    assert payload["fallback_flow"] == "manual_env"
    assert payload["fallback_handoff"]["recommended_flow"] == "manual_env"
    assert payload["handoff"]["recommended_flow"] == "manual_env"
    assert payload["flows"][0] == {
        "name": "device_code",
        "available": False,
        "reason": "browser_site_endpoint_missing",
        "description": "Planned browser approval flow for scoped local credentials.",
    }
    assert payload["flows"][1]["name"] == "manual_env"
    assert payload["flows"][1]["available"] is True
    assert payload["warnings"] == [
        "Device-code login is not available yet; use the manual_env fallback until browser.lexmount.cn exposes device-code endpoints."
    ]

    device_code = payload["device_code"]
    assert device_code["available"] is False
    assert device_code["reason"] == "browser_site_endpoint_missing"
    assert (
        device_code["verification_uri"] == "https://browser.lexmount.cn/connect/codex"
    )
    assert device_code["project_id"] == "arg-project"
    assert device_code["project_id_source"] == "argument"
    assert device_code["requested_scopes"] == ["browser:actions"]
    assert device_code["requested_scope_details"][0]["scope"] == "browser:actions"
    assert device_code["requested_scope_details"][0]["label"] == "Browser actions"
    assert device_code["requested_scope_details"][0]["risk"] == "high"
    assert device_code["requested_expires_in"] == "24h"
    assert "POST /api/auth/device/code" in device_code["required_endpoints"]
    assert "POST /api/auth/device/token" in device_code["required_endpoints"]
    assert any(
        "bearer-token authentication" in item
        for item in device_code["required_browser_site_support"]
    )

    connect = payload["connect_from_codex"]
    assert connect["response"] == "device_code"
    assert connect["url"] == device_code["connect_from_codex_url"]
    assert connect["setup_blocks"] == payload["fallback_handoff"]["setup_blocks"]
    assert connect["requested_scope_details"] == device_code["requested_scope_details"]
    assert connect["site_capability_status"]["available"] is False
    assert "device_code_oauth" in connect["site_capability_status"]["missing"]
    assert [item["id"] for item in connect["required_runtime_auth"]] == [
        "sdk_accepts_bearer_token",
        "api_accepts_bearer_token",
        "browser_gateway_accepts_bearer_token",
    ]
    device_code_oauth = next(
        item
        for item in connect["site_capabilities"]
        if item["id"] == "device_code_oauth"
    )
    assert "OAuth" in device_code_oauth["browser_site_action"]
    assert "scoped" in device_code_oauth["browser_site_action"]
    query = parse_qs(urlsplit(connect["url"]).query)
    assert query["project_id"] == ["arg-project"]
    assert query["scope"] == ["browser:actions"]
    assert query["expires_in"] == ["24h"]
    assert query["response"] == ["device_code"]
    assert opened == [connect["url"]]
    assert payload["open_result"] == {
        "requested": True,
        "url": connect["url"],
        "opened": True,
    }
    assert any("browser-cli doctor --json" in item for item in payload["next_steps"])
    assert any("device-code endpoints" in item for item in payload["next_steps"])


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any], *, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_auth_login_device_code_starts_approval_with_configured_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requests: list[dict[str, Any]] = []

    def fake_urlopen(request: Any, *, timeout: float) -> FakeHTTPResponse:
        requests.append(
            {
                "url": request.full_url,
                "timeout": timeout,
                "body": json.loads(request.data.decode("utf-8")),
            }
        )
        return FakeHTTPResponse(
            {
                "device_code": "dc-secret-code",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://browser.lexmount.cn/connect/codex",
                "verification_uri_complete": (
                    "https://browser.lexmount.cn/connect/codex?user_code=ABCD-EFGH"
                ),
                "expires_in": 600,
                "interval": 5,
            }
        )

    monkeypatch.setattr(cli_module, "urlopen", fake_urlopen)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "login",
                "--device-code",
                "--device-code-base-url",
                "https://auth.lexmount.test",
                "--project-id",
                "project-1",
                "--scope",
                "browser:actions",
                "--device-name",
                "Codex test device",
            ]
        )

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["flow"] == "device_code"
    assert payload["available"] is True
    assert payload["device_code_available"] is True
    assert payload["authenticated"] is False
    assert payload["credentials_saved"] is False
    assert payload["reason"] == "approval_required"
    assert [
        item["id"] for item in payload["connect_from_codex"]["required_runtime_auth"]
    ] == [
        "sdk_accepts_bearer_token",
        "api_accepts_bearer_token",
        "browser_gateway_accepts_bearer_token",
    ]
    assert payload["polling"] == {
        "requested": False,
        "authenticated": False,
        "status": "not_requested",
        "attempts": 0,
    }
    device_code = payload["device_code"]
    assert device_code["base_url"] == "https://auth.lexmount.test"
    assert device_code["base_url_source"] == "argument"
    assert device_code["code_endpoint"] == (
        "https://auth.lexmount.test/api/auth/device/code"
    )
    assert device_code["token_endpoint"] == (
        "https://auth.lexmount.test/api/auth/device/token"
    )
    assert device_code["user_code"] == "ABCD-EFGH"
    assert device_code["device_code_present"] is True
    assert device_code["device_code_length"] == len("dc-secret-code")
    assert device_code["requested_scopes"] == ["browser:actions"]
    assert payload["open_result"] == {
        "requested": False,
        "url": "https://browser.lexmount.cn/connect/codex?user_code=ABCD-EFGH",
        "opened": False,
    }
    assert requests == [
        {
            "url": "https://auth.lexmount.test/api/auth/device/code",
            "timeout": 10,
            "body": {
                "client_name": "browser-cli",
                "client_version": cli_module.__version__,
                "device_name": "Codex test device",
                "project_id": "project-1",
                "requested_scopes": ["browser:actions"],
                "audience": "lexmount-browser",
                "expires_in": "7d",
            },
        }
    ]
    assert "dc-secret-code" not in output


def test_auth_login_device_code_endpoint_error_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_urlopen(request: Any, *, timeout: float) -> FakeHTTPResponse:
        return FakeHTTPResponse(
            {
                "error": "not_found",
                "message": "Device-code endpoint is missing.",
            },
            status=404,
        )

    monkeypatch.setattr(cli_module, "urlopen", fake_urlopen)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "login",
                "--device-code",
                "--device-code-base-url",
                "https://auth.lexmount.test",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["available"] is False
    assert payload["device_code_available"] is False
    assert payload["reason"] == "device_code_endpoint_error"
    assert payload["fallback_flow"] == "manual_env"
    assert payload["device_code"] == {}
    assert payload["polling"] is None
    assert payload["credentials"] is None
    assert "Device-code endpoints are not available" in payload["next_steps"][0]
    assert "manual_env fallback" in payload["next_steps"][1]


def test_auth_login_device_code_wait_saves_token_without_revealing_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    credentials_file = tmp_path / "credentials.json"
    responses = [
        {
            "device_code": "dc-secret-code",
            "user_code": "WXYZ-1234",
            "verification_uri": "https://browser.lexmount.cn/connect/codex",
            "verification_uri_complete": (
                "https://browser.lexmount.cn/connect/codex?user_code=WXYZ-1234"
            ),
            "expires_in": 600,
            "interval": 1,
        },
        {
            "access_token": "secret-access-token",
            "refresh_token": "secret-refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "project_id": "project-1",
            "api_base_url": "https://api.lexmount.cn",
            "scopes": ["browser.actions:run"],
            "token_id": "tok_123",
        },
    ]
    requests: list[dict[str, Any]] = []

    def fake_urlopen(request: Any, *, timeout: float) -> FakeHTTPResponse:
        requests.append(
            {
                "url": request.full_url,
                "timeout": timeout,
                "body": json.loads(request.data.decode("utf-8")),
            }
        )
        return FakeHTTPResponse(responses.pop(0))

    monkeypatch.setattr(cli_module, "urlopen", fake_urlopen)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "login",
                "--device-code",
                "--device-code-base-url",
                "https://auth.lexmount.test",
                "--wait",
                "--device-code-timeout-seconds",
                "5",
                "--credentials-file",
                str(credentials_file),
                "--project-id",
                "project-1",
            ]
        )

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["available"] is True
    assert payload["authenticated"] is True
    assert payload["credentials_saved"] is True
    assert payload["reason"] == "authenticated"
    assert payload["fallback_flow"] is None
    assert payload["polling"]["status"] == "approved"
    assert payload["polling"]["attempts"] == 1
    assert payload["credentials"]["saved"] is True
    assert payload["credentials"]["credentials_file"] == str(credentials_file)
    assert payload["credentials"]["device_token"]["present"] is True
    assert payload["credentials"]["device_token"]["valid"] is True
    assert payload["credentials"]["device_token"]["has_access_token"] is True
    assert payload["credentials"]["device_token"]["has_refresh_token"] is True
    assert payload["credentials"]["device_token"]["token_id"] == "tok_123"
    assert payload["credentials"]["device_token"]["scopes"] == ["browser.actions:run"]
    serialized = json.dumps(payload)
    assert "secret-access-token" not in serialized
    assert "secret-refresh-token" not in serialized
    assert "dc-secret-code" not in serialized

    stored = json.loads(credentials_file.read_text())
    assert stored["access_token"] == "secret-access-token"
    assert stored["refresh_token"] == "secret-refresh-token"
    assert stored["kind"] == "device_token"
    assert stored["project_id"] == "project-1"
    assert requests[0]["url"] == "https://auth.lexmount.test/api/auth/device/code"
    assert requests[1]["url"] == "https://auth.lexmount.test/api/auth/device/token"
    assert requests[1]["body"] == {
        "device_code": "dc-secret-code",
        "client_name": "browser-cli",
    }


def test_auth_login_uses_env_project_id_for_connect_from_codex_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "env-project")

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "login"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    connect = payload["connect_from_codex"]
    assert connect["project_id"] == "env-project"
    assert connect["project_id_source"] == "env"

    query = parse_qs(urlsplit(connect["url"]).query)
    assert query["project_id"] == ["env-project"]


def test_auth_login_open_attempts_browser_and_reports_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)
    opened: list[str] = []
    monkeypatch.setattr(
        cli_module.webbrowser,
        "open",
        lambda url: opened.append(url) or True,
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "login", "--open", "--project-id", "arg-project"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    connect_url = payload["connect_from_codex"]["url"]
    assert opened == [connect_url]
    assert payload["open_result"] == {
        "requested": True,
        "url": connect_url,
        "opened": True,
    }
    assert payload["handoff"]["open_command"] == "browser-cli auth login --open"
    assert payload["handoff"]["open_url"] == connect_url
    assert payload["warnings"] == []


def test_auth_login_open_failure_is_non_fatal_and_masked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret-api-key")

    def fail_open(url: str) -> bool:
        raise RuntimeError(f"failed token=abc api_key={url} secret-api-key")

    monkeypatch.setattr(cli_module.webbrowser, "open", fail_open)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "login", "--open"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["open_result"]["requested"] is True
    assert payload["open_result"]["opened"] is False
    assert payload["open_result"]["url"] == payload["connect_from_codex"]["url"]
    assert "secret-api-key" not in payload["open_result"]["error"]
    assert "token=***" in payload["open_result"]["error"]
    assert "api_key=***" in payload["open_result"]["error"]
    assert payload["warnings"] == [
        "Failed to open the Connect from Codex URL automatically; copy the URL manually."
    ]


def test_session_list_passes_status_filter(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    class FakeAdmin:
        def list_sessions(
            self,
            *,
            status: str | None,
        ) -> DummyModel:
            observed.update({"status": status})
            return DummyModel(
                {
                    "count": 0,
                    "status_filter": status,
                    "sessions": [],
                    "pagination": None,
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["session", "list", "--status", "active"])

    assert exc_info.value.code == 0
    assert observed == {"status": "active"}
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": "session.list",
        "count": 0,
        "status_filter": "active",
        "sessions": [],
        "pagination": None,
    }


def test_session_create_passes_context_options(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    class FakeAdmin:
        def create_session(
            self,
            *,
            context_id: str | None,
            create_context: bool,
            context_mode: str,
            browser_mode: str,
            metadata: dict[str, Any] | None,
        ) -> DummyModel:
            observed.update(
                {
                    "context_id": context_id,
                    "create_context": create_context,
                    "context_mode": context_mode,
                    "browser_mode": browser_mode,
                    "metadata": metadata,
                }
            )
            return DummyModel(
                {
                    "mode": "sdk",
                    "context_id": "ctx1",
                    "created_context": True,
                    "session": {"session_id": "s1", "status": "active"},
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "session",
                "create",
                "--create-context",
                "--context-mode",
                "read_only",
                "--browser-mode",
                "light",
                "--metadata-json",
                '{"owner":"codex"}',
            ]
        )

    assert exc_info.value.code == 0
    assert observed == {
        "context_id": None,
        "create_context": True,
        "context_mode": "read_only",
        "browser_mode": "light",
        "metadata": {"owner": "codex"},
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["command"] == "session.create"
    assert payload["session"] == {"session_id": "s1", "status": "active"}


def test_session_create_can_reuse_context_by_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeAdmin:
        def list_contexts(
            self,
            *,
            status: str | None,
            limit: int,
        ) -> DummyModel:
            calls.append(("list_contexts", {"status": status, "limit": limit}))
            return DummyModel(
                {
                    "count": 2,
                    "contexts": [
                        {
                            "context_id": "ctx-locked",
                            "status": "locked",
                            "metadata": {"purpose": "codex-login"},
                        },
                        {
                            "context_id": "ctx-ready",
                            "status": "available",
                            "metadata": {"purpose": "codex-login"},
                        },
                    ],
                }
            )

        def create_session(
            self,
            *,
            context_id: str | None,
            create_context: bool,
            context_mode: str,
            browser_mode: str,
            metadata: dict[str, Any] | None,
        ) -> DummyModel:
            calls.append(
                (
                    "create_session",
                    {
                        "context_id": context_id,
                        "create_context": create_context,
                        "context_mode": context_mode,
                        "browser_mode": browser_mode,
                        "metadata": metadata,
                    },
                )
            )
            return DummyModel(
                {
                    "mode": "sdk",
                    "context_id": context_id,
                    "session": {"session_id": "s1", "status": "active"},
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "session",
                "create",
                "--context-metadata-json",
                '{"purpose":"codex-login"}',
                "--context-status",
                "available",
                "--context-limit",
                "5",
                "--metadata-json",
                '{"task":"smoke"}',
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "session.create"
    assert payload["context_id"] == "ctx-ready"
    assert payload["session"] == {"session_id": "s1", "status": "active"}
    assert payload["context_reuse"]["selected"] is True
    assert payload["context_reuse"]["created"] is False
    assert payload["context_reuse"]["context_id"] == "ctx-ready"
    assert payload["context_reuse"]["availability"] == "available"
    assert payload["context_reuse"]["reusable"] is True
    assert payload["context_reuse"]["locked"] is False
    assert payload["context_reuse"]["reuse_reason"] == "status_reusable"
    assert payload["context_reuse"]["reuse"]["reusable"] is True
    assert payload["context_reuse"]["checked"] == 2
    assert payload["context_reuse"]["metadata_filter"] == {"purpose": "codex-login"}
    assert payload["context_reuse"]["status_filter"] == "available"
    assert payload["context_reuse"]["limit"] == 5
    assert calls == [
        ("list_contexts", {"status": "available", "limit": 5}),
        (
            "create_session",
            {
                "context_id": "ctx-ready",
                "create_context": False,
                "context_mode": "read_write",
                "browser_mode": "normal",
                "metadata": {"task": "smoke"},
            },
        ),
    ]


def test_session_create_reuses_context_metadata_from_local_registry(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    cli_module._record_context_metadata(
        {
            "context_id": "ctx-ready",
            "status": "available",
            "metadata": {"purpose": "codex-login", "marker": "registry-value"},
        }
    )

    class FakeAdmin:
        def list_contexts(
            self,
            *,
            status: str | None,
            limit: int,
        ) -> DummyModel:
            calls.append(("list_contexts", {"status": status, "limit": limit}))
            return DummyModel(
                {
                    "count": 1,
                    "contexts": [
                        {
                            "context_id": "ctx-ready",
                            "status": "available",
                            "metadata": {},
                        }
                    ],
                }
            )

        def create_session(
            self,
            *,
            context_id: str | None,
            create_context: bool,
            context_mode: str,
            browser_mode: str,
            metadata: dict[str, Any] | None,
        ) -> DummyModel:
            calls.append(
                (
                    "create_session",
                    {
                        "context_id": context_id,
                        "create_context": create_context,
                        "context_mode": context_mode,
                        "browser_mode": browser_mode,
                        "metadata": metadata,
                    },
                )
            )
            return DummyModel({"session": {"session_id": "s1"}})

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "session",
                "create",
                "--context-metadata-json",
                '{"purpose":"codex-login"}',
            ]
        )

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["context_reuse"]["selected"] is True
    assert payload["context_reuse"]["context_id"] == "ctx-ready"
    diagnostics = payload["context_reuse"]["candidates"][0]["metadata_diagnostics"]
    assert diagnostics["metadata_source"] == "local_registry"
    assert diagnostics["matched_keys"] == ["purpose"]
    assert "registry-value" not in output
    assert calls == [
        ("list_contexts", {"status": None, "limit": 20}),
        (
            "create_session",
            {
                "context_id": "ctx-ready",
                "create_context": False,
                "context_mode": "read_write",
                "browser_mode": "normal",
                "metadata": None,
            },
        ),
    ]


def test_session_create_can_select_oldest_matching_context(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeAdmin:
        def list_contexts(
            self,
            *,
            status: str | None,
            limit: int,
        ) -> DummyModel:
            calls.append(("list_contexts", {"status": status, "limit": limit}))
            return DummyModel(
                {
                    "count": 2,
                    "contexts": [
                        {
                            "context_id": "ctx-new",
                            "status": "available",
                            "metadata": {"purpose": "codex-login"},
                            "updated_at": "2026-01-02T00:00:00Z",
                        },
                        {
                            "context_id": "ctx-old",
                            "status": "available",
                            "metadata": {"purpose": "codex-login"},
                            "updated_at": "2026-01-01T00:00:00Z",
                        },
                    ],
                }
            )

        def create_session(
            self,
            *,
            context_id: str | None,
            create_context: bool,
            context_mode: str,
            browser_mode: str,
            metadata: dict[str, Any] | None,
        ) -> DummyModel:
            calls.append(("create_session", {"context_id": context_id}))
            return DummyModel(
                {"context_id": context_id, "session": {"session_id": "s1"}}
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "session",
                "create",
                "--context-metadata-json",
                '{"purpose":"codex-login"}',
                "--context-selection",
                "oldest",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["context_id"] == "ctx-old"
    assert payload["context_reuse"]["context_id"] == "ctx-old"
    assert payload["context_reuse"]["selection_strategy"] == "oldest"
    assert calls == [
        ("list_contexts", {"status": None, "limit": 20}),
        ("create_session", {"context_id": "ctx-old"}),
    ]


def test_session_create_can_create_context_when_no_reusable_metadata_match(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeAdmin:
        def list_contexts(
            self,
            *,
            status: str | None,
            limit: int,
        ) -> DummyModel:
            calls.append(("list_contexts", {"status": status, "limit": limit}))
            return DummyModel(
                {
                    "count": 1,
                    "contexts": [
                        {
                            "context_id": "ctx-busy",
                            "status": "busy",
                            "metadata": {"purpose": "codex-login"},
                        }
                    ],
                }
            )

        def create_context(self, *, metadata: dict[str, Any] | None) -> DummyModel:
            calls.append(("create_context", {"metadata": metadata}))
            return DummyModel(
                {
                    "context_id": "ctx-new",
                    "status": "available",
                    "metadata": metadata,
                }
            )

        def create_session(
            self,
            *,
            context_id: str | None,
            create_context: bool,
            context_mode: str,
            browser_mode: str,
            metadata: dict[str, Any] | None,
        ) -> DummyModel:
            calls.append(
                (
                    "create_session",
                    {
                        "context_id": context_id,
                        "create_context": create_context,
                        "context_mode": context_mode,
                        "browser_mode": browser_mode,
                        "metadata": metadata,
                    },
                )
            )
            return DummyModel({"session": {"session_id": "s1"}})

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "session",
                "create",
                "--context-metadata-json",
                '{"purpose":"codex-login"}',
                "--create-context-if-missing",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["context_reuse"]["selected"] is True
    assert payload["context_reuse"]["created"] is True
    assert payload["context_reuse"]["context_id"] == "ctx-new"
    assert payload["context_reuse"]["availability"] == "available"
    assert payload["context_reuse"]["reusable"] is True
    assert payload["context_reuse"]["locked"] is False
    assert payload["context_reuse"]["reuse_reason"] == "status_reusable"
    assert payload["context_reuse"]["candidates"][0]["locked"] is True
    assert calls == [
        ("list_contexts", {"status": None, "limit": 20}),
        ("create_context", {"metadata": {"purpose": "codex-login"}}),
        (
            "create_session",
            {
                "context_id": "ctx-new",
                "create_context": False,
                "context_mode": "read_write",
                "browser_mode": "normal",
                "metadata": None,
            },
        ),
    ]


def test_session_create_fails_when_metadata_context_is_unavailable(
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
                    "count": 1,
                    "contexts": [
                        {
                            "context_id": "ctx-busy",
                            "status": "busy",
                            "metadata": {"purpose": "codex-login"},
                        }
                    ],
                }
            )

        def create_session(self, **kwargs: Any) -> DummyModel:
            raise AssertionError("session should not be created without a context")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "session",
                "create",
                "--context-metadata-json",
                '{"purpose":"codex-login"}',
            ]
        )

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "session.create"
    assert payload["error"] == "no_available_context"
    assert payload["selected"] is False
    assert payload["created"] is False
    assert payload["candidates"][0]["reason"] == "status_locked"


def test_session_create_rejects_conflicting_context_reuse_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "session",
                "create",
                "--context-id",
                "ctx1",
                "--context-metadata-json",
                '{"purpose":"codex-login"}',
            ]
        )

    assert exc_info.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "session.create"
    assert payload["error"] == "argument_error"
    assert "--context-metadata-json" in payload["message"]


def test_session_get_close_and_keepalive_emit_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeAdmin:
        def get_session(self, session_id: str) -> DummyModel:
            calls.append(("get", {"session_id": session_id}))
            return DummyModel({"session_id": session_id, "status": "active"})

        def close_session(self, session_id: str) -> None:
            calls.append(("close", {"session_id": session_id}))

        def keepalive_session(
            self,
            *,
            session_id: str,
            interval: float,
            duration: float,
            stop_on_inactive: bool,
        ) -> dict[str, Any]:
            calls.append(
                (
                    "keepalive",
                    {
                        "session_id": session_id,
                        "interval": interval,
                        "duration": duration,
                        "stop_on_inactive": stop_on_inactive,
                    },
                )
            )
            return {"session_id": session_id, "checks": 1, "final_status": "active"}

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["session", "get", "--session-id", "s1"])
    assert exc_info.value.code == 0
    assert json.loads(capsys.readouterr().out)["session"] == {
        "session_id": "s1",
        "status": "active",
    }

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["session", "close", "--session-id", "s1"])
    assert exc_info.value.code == 0
    assert json.loads(capsys.readouterr().out)["closed"] is True

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "session",
                "keepalive",
                "--session-id",
                "s1",
                "--interval",
                "0.5",
                "--duration",
                "0",
                "--stop-on-inactive",
            ]
        )
    assert exc_info.value.code == 0
    assert json.loads(capsys.readouterr().out)["checks"] == 1

    assert calls == [
        ("get", {"session_id": "s1"}),
        ("close", {"session_id": "s1"}),
        (
            "keepalive",
            {
                "session_id": "s1",
                "interval": 0.5,
                "duration": 0.0,
                "stop_on_inactive": True,
            },
        ),
    ]


@pytest.mark.parametrize(
    ("status", "normalized_status", "availability", "reusable", "locked", "reason"),
    [
        ("available", "available", "available", True, False, "status_reusable"),
        ("Ready", "ready", "available", True, False, "status_reusable"),
        ("IN-USE", "in_use", "locked", False, True, "status_locked"),
        ("in use", "in_use", "locked", False, True, "status_locked"),
        ("reserved", "reserved", "locked", False, True, "status_locked"),
        ("failed", "failed", "unavailable", False, False, "status_unavailable"),
        ("archived", "archived", "unavailable", False, False, "status_unavailable"),
        ("maintenance", "maintenance", "unknown", False, False, "status_not_reusable"),
        (None, None, "unknown", False, False, "status_missing"),
    ],
)
def test_context_reuse_state_normalizes_status_aliases(
    status: str | None,
    normalized_status: str | None,
    availability: str,
    reusable: bool,
    locked: bool,
    reason: str,
) -> None:
    assert cli_module._context_reuse_state({"status": status}) == {
        "status": status,
        "normalized_status": normalized_status,
        "availability": availability,
        "reusable": reusable,
        "locked": locked,
        "reason": reason,
    }


def test_context_commands_emit_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeAdmin:
        def create_context(
            self,
            *,
            metadata: dict[str, Any] | None,
        ) -> DummyModel:
            calls.append(("create", {"metadata": metadata}))
            return DummyModel({"context_id": "ctx1", "metadata": metadata})

        def list_contexts(
            self,
            *,
            status: str | None,
            limit: int,
        ) -> DummyModel:
            calls.append(("list", {"status": status, "limit": limit}))
            return DummyModel(
                {
                    "count": 1,
                    "status_filter": status,
                    "limit": limit,
                    "contexts": [{"context_id": "ctx1"}],
                }
            )

        def get_context(self, context_id: str) -> DummyModel:
            calls.append(("get", {"context_id": context_id}))
            return DummyModel({"context_id": context_id, "status": "available"})

        def delete_context(self, context_id: str) -> None:
            calls.append(("delete", {"context_id": context_id}))

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["context", "create", "--metadata-json", '{"purpose":"test"}'])
    assert exc_info.value.code == 0
    assert json.loads(capsys.readouterr().out)["context"]["context_id"] == "ctx1"

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["context", "list", "--status", "available", "--limit", "5"])
    assert exc_info.value.code == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["count"] == 1
    assert listed["limit"] == 5

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["context", "get", "--context-id", "ctx1"])
    assert exc_info.value.code == 0
    assert json.loads(capsys.readouterr().out)["context"]["status"] == "available"

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["context", "delete", "--context-id", "ctx1"])
    assert exc_info.value.code == 0
    assert json.loads(capsys.readouterr().out)["deleted"] is True

    assert calls == [
        ("create", {"metadata": {"purpose": "test"}}),
        ("list", {"status": "available", "limit": 5}),
        ("get", {"context_id": "ctx1"}),
        ("delete", {"context_id": "ctx1"}),
    ]


def test_context_status_reports_reusable_and_locked_state(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAdmin:
        def get_context(self, context_id: str) -> DummyModel:
            return DummyModel({"context_id": context_id, "status": "locked"})

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["context", "status", "--context-id", "ctx1"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "context.status"
    assert payload["context_id"] == "ctx1"
    assert payload["normalized_status"] == "locked"
    assert payload["availability"] == "locked"
    assert payload["reusable"] is False
    assert payload["locked"] is True
    assert payload["reuse_reason"] == "status_locked"
    assert payload["reuse"] == {
        "status": "locked",
        "normalized_status": "locked",
        "availability": "locked",
        "reusable": False,
        "locked": True,
        "reason": "status_locked",
    }


def test_context_pick_selects_first_available_metadata_match(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    class FakeAdmin:
        def list_contexts(
            self,
            *,
            status: str | None,
            limit: int,
        ) -> DummyModel:
            observed.update({"status": status, "limit": limit})
            return DummyModel(
                {
                    "count": 3,
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
                        {
                            "context_id": "ctx-ready",
                            "status": "available",
                            "metadata": {"purpose": "codex"},
                        },
                    ],
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "context",
                "pick",
                "--limit",
                "10",
                "--metadata-json",
                '{"purpose":"codex"}',
            ]
        )

    assert exc_info.value.code == 0
    assert observed == {"status": None, "limit": 10}
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "context.pick"
    assert payload["selected"] is True
    assert payload["created"] is False
    assert payload["context_id"] == "ctx-ready"
    assert payload["normalized_status"] == "available"
    assert payload["availability"] == "available"
    assert payload["reusable"] is True
    assert payload["locked"] is False
    assert payload["reuse_reason"] == "status_reusable"
    assert payload["reuse"]["reusable"] is True
    assert payload["metadata_filter"] == {"purpose": "codex"}
    assert payload["selection_summary"] == {
        "checked": 3,
        "selected_context_id": "ctx-ready",
        "recommended_next_action": "use_selected_context",
        "decision_reason": "reusable_context_selected",
        "metadata_matches": 2,
        "metadata_mismatches": 1,
        "reusable_matches": 1,
        "locked_matches": 1,
        "unavailable_matches": 0,
        "unknown_matches": 0,
        "availability_counts": {"available": 2, "locked": 1},
        "reason_counts": {
            "metadata_mismatch": 1,
            "status_locked": 1,
            "status_reusable": 1,
        },
        "create_if_missing": False,
        "dry_run": False,
        "would_create": False,
    }
    assert payload["candidates"] == [
        {
            "context_id": "ctx-locked",
            "status": "locked",
            "normalized_status": "locked",
            "availability": "locked",
            "metadata_match": True,
            "metadata_diagnostics": {
                "metadata_present": True,
                "metadata_source": "api",
                "metadata_keys": ["purpose"],
                "filter_keys": ["purpose"],
                "matched_keys": ["purpose"],
                "missing_keys": [],
                "different_keys": [],
                "value_redacted": True,
            },
            "reusable": False,
            "locked": True,
            "reason": "status_locked",
        },
        {
            "context_id": "ctx-other",
            "status": "available",
            "normalized_status": "available",
            "availability": "available",
            "metadata_match": False,
            "metadata_diagnostics": {
                "metadata_present": True,
                "metadata_source": "api",
                "metadata_keys": ["purpose"],
                "filter_keys": ["purpose"],
                "matched_keys": [],
                "missing_keys": [],
                "different_keys": ["purpose"],
                "value_redacted": True,
            },
            "reusable": True,
            "locked": False,
            "reason": "metadata_mismatch",
        },
        {
            "context_id": "ctx-ready",
            "status": "available",
            "normalized_status": "available",
            "availability": "available",
            "metadata_match": True,
            "metadata_diagnostics": {
                "metadata_present": True,
                "metadata_source": "api",
                "metadata_keys": ["purpose"],
                "filter_keys": ["purpose"],
                "matched_keys": ["purpose"],
                "missing_keys": [],
                "different_keys": [],
                "value_redacted": True,
            },
            "reusable": True,
            "locked": False,
            "reason": "status_reusable",
        },
    ]


def test_context_pick_can_select_newest_matching_context(
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
                    "count": 3,
                    "contexts": [
                        {
                            "context_id": "ctx-old",
                            "status": "available",
                            "metadata": {"purpose": "codex"},
                            "updated_at": "2026-01-01T00:00:00Z",
                        },
                        {
                            "context_id": "ctx-new",
                            "status": "available",
                            "metadata": {"purpose": "codex"},
                            "updated_at": "2026-01-02T00:00:00Z",
                        },
                        {
                            "context_id": "ctx-locked-newer",
                            "status": "locked",
                            "metadata": {"purpose": "codex"},
                            "updated_at": "2026-01-03T00:00:00Z",
                        },
                    ],
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "context",
                "pick",
                "--metadata-json",
                '{"purpose":"codex"}',
                "--selection",
                "newest",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected"] is True
    assert payload["context_id"] == "ctx-new"
    assert payload["selection_strategy"] == "newest"
    assert payload["selection_summary"]["reusable_matches"] == 2
    assert payload["selection_summary"]["locked_matches"] == 1


def test_context_pick_uses_local_registry_when_api_metadata_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeAdmin:
        def create_context(self, *, metadata: dict[str, Any] | None) -> DummyModel:
            calls.append(("create", {"metadata": metadata}))
            return DummyModel(
                {
                    "context_id": "ctx-local",
                    "status": "available",
                    "metadata": metadata,
                }
            )

        def list_contexts(
            self,
            *,
            status: str | None,
            limit: int,
        ) -> DummyModel:
            calls.append(("list", {"status": status, "limit": limit}))
            return DummyModel(
                {
                    "count": 1,
                    "contexts": [
                        {
                            "context_id": "ctx-local",
                            "status": "available",
                            "metadata": {},
                        }
                    ],
                }
            )

        def delete_context(self, context_id: str) -> None:
            calls.append(("delete", {"context_id": context_id}))

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "context",
                "create",
                "--metadata-json",
                '{"purpose":"codex","marker":"registry-value"}',
            ]
        )

    assert exc_info.value.code == 0
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "context",
                "pick",
                "--metadata-json",
                '{"purpose":"codex"}',
                "--dry-run",
            ]
        )

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["selected"] is True
    assert payload["context_id"] == "ctx-local"
    diagnostics = payload["candidates"][0]["metadata_diagnostics"]
    assert diagnostics["metadata_source"] == "local_registry"
    assert diagnostics["metadata_keys"] == ["marker", "purpose"]
    assert diagnostics["matched_keys"] == ["purpose"]
    assert diagnostics["value_redacted"] is True
    assert "registry-value" not in output

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["context", "delete", "--context-id", "ctx-local"])

    assert exc_info.value.code == 0
    assert "ctx-local" not in cli_module._read_context_registry()["contexts"]
    assert calls == [
        ("create", {"metadata": {"purpose": "codex", "marker": "registry-value"}}),
        ("list", {"status": None, "limit": 20}),
        ("delete", {"context_id": "ctx-local"}),
    ]


def test_context_pick_can_create_when_no_reusable_context_matches(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeAdmin:
        def list_contexts(
            self,
            *,
            status: str | None,
            limit: int,
        ) -> DummyModel:
            calls.append(("list", {"status": status, "limit": limit}))
            return DummyModel(
                {
                    "count": 1,
                    "contexts": [
                        {
                            "context_id": "ctx-busy",
                            "status": "busy",
                            "metadata": {"purpose": "codex"},
                        }
                    ],
                }
            )

        def create_context(self, *, metadata: dict[str, Any] | None) -> DummyModel:
            calls.append(("create", {"metadata": metadata}))
            return DummyModel(
                {
                    "context_id": "ctx-new",
                    "status": "available",
                    "metadata": metadata,
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "context",
                "pick",
                "--metadata-json",
                '{"purpose":"codex"}',
                "--create-if-missing",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected"] is True
    assert payload["created"] is True
    assert payload["context_id"] == "ctx-new"
    assert payload["availability"] == "available"
    assert payload["reusable"] is True
    assert payload["locked"] is False
    assert payload["reuse_reason"] == "status_reusable"
    assert payload["reuse"]["reusable"] is True
    assert payload["selection_summary"]["checked"] == 1
    assert payload["selection_summary"]["locked_matches"] == 1
    assert payload["selection_summary"]["create_if_missing"] is True
    assert (
        payload["selection_summary"]["recommended_next_action"] == "use_created_context"
    )
    assert payload["selection_summary"]["decision_reason"] == "created_context_selected"
    assert payload["selection_summary"]["would_create"] is False
    assert calls == [
        ("list", {"status": None, "limit": 20}),
        ("create", {"metadata": {"purpose": "codex"}}),
    ]


def test_context_pick_fails_when_no_reusable_context_matches(
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
                    "count": 1,
                    "contexts": [
                        {
                            "context_id": "ctx-busy",
                            "status": "busy",
                            "metadata": {"purpose": "codex"},
                        }
                    ],
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["context", "pick", "--metadata-json", '{"purpose":"codex"}'])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "context.pick"
    assert payload["error"] == "no_available_context"
    assert payload["selected"] is False
    assert payload["checked"] == 1
    assert payload["candidates"][0]["reason"] == "status_locked"
    assert payload["selection_summary"]["locked_matches"] == 1
    assert (
        payload["selection_summary"]["recommended_next_action"]
        == "wait_or_choose_different_context"
    )
    assert payload["selection_summary"]["decision_reason"] == "locked_context_matches"
    assert payload["selection_summary"]["would_create"] is False


def test_context_pick_dry_run_reports_would_create_without_creating(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeAdmin:
        def list_contexts(
            self,
            *,
            status: str | None,
            limit: int,
        ) -> DummyModel:
            calls.append(("list", {"status": status, "limit": limit}))
            return DummyModel(
                {
                    "count": 2,
                    "contexts": [
                        {
                            "context_id": "ctx-busy",
                            "status": "busy",
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
            raise AssertionError("dry-run context pick must not create a context")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "context",
                "pick",
                "--metadata-json",
                '{"purpose":"codex"}',
                "--create-if-missing",
                "--dry-run",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "context.pick"
    assert payload["selected"] is False
    assert payload["created"] is False
    assert payload["dry_run"] is True
    assert payload["would_create"] is True
    assert payload["context"] is None
    assert payload["selection_summary"] == {
        "checked": 2,
        "selected_context_id": None,
        "recommended_next_action": "rerun_without_dry_run_to_create",
        "decision_reason": "dry_run_create_if_missing",
        "metadata_matches": 1,
        "metadata_mismatches": 1,
        "reusable_matches": 0,
        "locked_matches": 1,
        "unavailable_matches": 0,
        "unknown_matches": 0,
        "availability_counts": {"available": 1, "locked": 1},
        "reason_counts": {"metadata_mismatch": 1, "status_locked": 1},
        "create_if_missing": True,
        "dry_run": True,
        "would_create": True,
    }
    assert calls == [("list", {"status": None, "limit": 20})]


def test_compatibility_aliases_still_work(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []

    class FakeAdmin:
        def create_session(self, **kwargs: Any) -> DummyModel:
            calls.append("prepare")
            return DummyModel({"session": {"session_id": "s1"}})

        def list_contexts(self, **kwargs: Any) -> DummyModel:
            calls.append("list-contexts")
            return DummyModel({"count": 0, "contexts": []})

        def close_session(self, session_id: str) -> None:
            calls.append(f"close-session:{session_id}")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["prepare"])
    assert exc_info.value.code == 0
    assert json.loads(capsys.readouterr().out)["command"] == "session.create"

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["list-contexts"])
    assert exc_info.value.code == 0
    assert json.loads(capsys.readouterr().out)["command"] == "context.list"

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["close-session", "--session-id", "s1"])
    assert exc_info.value.code == 0
    assert json.loads(capsys.readouterr().out)["command"] == "session.close"

    assert calls == ["prepare", "list-contexts", "close-session:s1"]


def test_action_direct_url_masks_resolved_connect_url_by_default(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}
    connect_url = "wss://api.lexmount.cn/connection?project_id=project&api_key=secret"

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: connect_url,
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed.update(
            {
                "connect_url": connect_url,
                "action": action,
                "request_type": request.__class__.__name__,
            }
        )
        return SimpleNamespace(result={"title": "Example"})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "snapshot", "--direct-url"])

    assert exc_info.value.code == 0
    assert observed == {
        "connect_url": connect_url,
        "action": "snapshot",
        "request_type": "SnapshotRequest",
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": "action.snapshot",
        "session_id": None,
        "connect_url": "wss://api.lexmount.cn/connection?project_id=project&api_key=***",
        "connect_url_masked": True,
        "result": {"title": "Example"},
    }


def test_action_reveal_connect_url_requires_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    connect_url = "wss://api.lexmount.cn/connection?project_id=project&api_key=secret"

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: connect_url,
    )
    monkeypatch.setattr(
        "browser_cli.cli.run_browser_action",
        lambda **kwargs: SimpleNamespace(result={"ok": True}),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "snapshot", "--direct-url", "--reveal-connect-url"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["connect_url"] == connect_url
    assert payload["connect_url_masked"] is False


def test_action_target_is_required(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "snapshot"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "action.snapshot"
    assert payload["error"] == "BrowserRuntimeError"
    assert "Pass exactly one action target" in payload["message"]


def test_action_target_rejects_multiple_targets(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "snapshot", "--session-id", "s1", "--direct-url"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "action.snapshot"
    assert payload["error"] == "BrowserRuntimeError"
    assert "Pass exactly one action target" in payload["message"]


def test_action_eval_accepts_script_alias(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    def fake_resolve(target: Any) -> str:
        assert target.session_id == "s1"
        return "wss://example.test/devtools"

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed.update(
            {
                "connect_url": connect_url,
                "action": action,
                "expression": request.expression,
            }
        )
        return SimpleNamespace(result={"value": "Example"})

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url", fake_resolve
    )
    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "eval",
                "--session-id",
                "s1",
                "--script",
                "() => document.title",
            ]
        )

    assert exc_info.value.code == 0
    assert observed == {
        "connect_url": "wss://example.test/devtools",
        "action": "eval",
        "expression": "() => document.title",
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == {"value": "Example"}


def test_action_set_file_input_embeds_local_file_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Any,
) -> None:
    upload = tmp_path / "upload.txt"
    upload.write_text("Hello", encoding="utf-8")
    observed: dict[str, Any] = {}

    def fake_resolve(target: Any) -> str:
        assert target.session_id == "s1"
        return "wss://example.test/devtools"

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed.update(
            {
                "connect_url": connect_url,
                "action": action,
                "expression": request.expression,
            }
        )
        return SimpleNamespace(
            result={
                "url": "https://example.test/upload",
                "value": {
                    "selector": "input[type=file]",
                    "found": True,
                    "file_input": True,
                    "set": True,
                    "requested_count": 1,
                    "file_count": 1,
                    "files": [
                        {
                            "name": "upload.txt",
                            "type": "text/plain",
                            "size": 5,
                        }
                    ],
                    "value": "***",
                    "value_masked": True,
                    "dispatched_events": ["input", "change"],
                },
            }
        )

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url", fake_resolve
    )
    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "set-file-input",
                "--session-id",
                "s1",
                "--selector",
                "input[type=file]",
                "--file",
                str(upload),
            ]
        )

    assert exc_info.value.code == 0
    assert observed["connect_url"] == "wss://example.test/devtools"
    assert observed["action"] == "eval"
    assert '"name": "upload.txt"' in observed["expression"]
    assert '"type": "text/plain"' in observed["expression"]
    assert '"size": 5' in observed["expression"]
    assert '"data_base64": "SGVsbG8="' in observed["expression"]
    assert "DataTransfer" in observed["expression"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.set-file-input"
    assert payload["result"]["set"] is True
    assert payload["result"]["files"][0]["name"] == "upload.txt"
    assert payload["result"]["value_masked"] is True


def test_action_set_file_input_missing_file_is_json(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Any,
) -> None:
    missing = tmp_path / "missing.txt"

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "set-file-input",
                "--session-id",
                "s1",
                "--selector",
                "input[type=file]",
                "--file",
                str(missing),
            ]
        )

    assert exc_info.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "action.set-file-input"
    assert payload["error"] == "file_not_found"
    assert payload["file"] == str(missing)


@pytest.mark.parametrize(
    ("argv", "command", "value", "expected_result"),
    [
        (
            ["action", "get-text", "--session-id", "s1", "--selector", "main"],
            "action.get-text",
            {"selector": "main", "found": True, "text": "Hello"},
            {
                "selector": "main",
                "found": True,
                "text": "Hello",
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "get-text-role",
                "--session-id",
                "s1",
                "--role",
                "heading",
                "--name",
                "Welcome",
            ],
            "action.get-text-role",
            {
                "role": "heading",
                "name": "Welcome",
                "found": True,
                "role_found": True,
                "include_hidden": False,
                "text": "Welcome",
                "text_length": 7,
                "candidate_count": 1,
            },
            {
                "role": "heading",
                "name": "Welcome",
                "found": True,
                "role_found": True,
                "include_hidden": False,
                "text": "Welcome",
                "text_length": 7,
                "candidate_count": 1,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            ["action", "exists", "--session-id", "s1", "--selector", "#missing"],
            "action.exists",
            {"selector": "#missing", "exists": False},
            {
                "selector": "#missing",
                "exists": False,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "exists-role",
                "--session-id",
                "s1",
                "--role",
                "alert",
                "--name",
                "Saved",
                "--include-hidden",
            ],
            "action.exists-role",
            {
                "role": "alert",
                "name": "Saved",
                "exists": True,
                "found": True,
                "role_found": True,
                "include_hidden": True,
                "candidate_count": 1,
            },
            {
                "role": "alert",
                "name": "Saved",
                "exists": True,
                "found": True,
                "role_found": True,
                "include_hidden": True,
                "candidate_count": 1,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "inspect",
                "--session-id",
                "s1",
                "--selector",
                "input[name=password]",
                "--include-html",
            ],
            "action.inspect",
            {
                "selector": "input[name=password]",
                "found": True,
                "element": {
                    "selector": "#password",
                    "tag": "input",
                    "role": "textbox",
                    "name": "Password",
                    "text": "",
                    "visible": True,
                },
                "attributes": {"type": "password", "name": "password", "value": "***"},
                "state": {
                    "visible": True,
                    "focused": False,
                    "disabled": False,
                    "readonly": False,
                    "required": True,
                    "checked": None,
                    "selected": None,
                    "multiple": None,
                    "contenteditable": False,
                },
                "readable": True,
                "value": "***",
                "value_type": "value",
                "value_masked": True,
                "value_length": 8,
                "visible": True,
                "in_viewport": True,
                "html": '<input id="password" type="password" value="***">',
                "html_length": 49,
                "html_truncated": False,
            },
            {
                "selector": "input[name=password]",
                "found": True,
                "element": {
                    "selector": "#password",
                    "tag": "input",
                    "role": "textbox",
                    "name": "Password",
                    "text": "",
                    "visible": True,
                },
                "attributes": {"type": "password", "name": "password", "value": "***"},
                "state": {
                    "visible": True,
                    "focused": False,
                    "disabled": False,
                    "readonly": False,
                    "required": True,
                    "checked": None,
                    "selected": None,
                    "multiple": None,
                    "contenteditable": False,
                },
                "readable": True,
                "value": "***",
                "value_type": "value",
                "value_masked": True,
                "value_length": 8,
                "visible": True,
                "in_viewport": True,
                "html": '<input id="password" type="password" value="***">',
                "html_length": 49,
                "html_truncated": False,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            ["action", "scroll", "--session-id", "s1", "--y", "300"],
            "action.scroll",
            {
                "selector": None,
                "found": True,
                "scrolled": True,
                "x": 0,
                "y": 300,
                "scroll_x": 0,
                "scroll_y": 300,
            },
            {
                "selector": None,
                "found": True,
                "scrolled": True,
                "x": 0,
                "y": 300,
                "scroll_x": 0,
                "scroll_y": 300,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "bounding-box",
                "--session-id",
                "s1",
                "--selector",
                "button",
            ],
            "action.bounding-box",
            {
                "selector": "button",
                "found": True,
                "visible": True,
                "in_viewport": True,
                "bounding_box": {
                    "x": 10,
                    "y": 20,
                    "top": 20,
                    "right": 110,
                    "bottom": 60,
                    "left": 10,
                    "width": 100,
                    "height": 40,
                },
                "center": {"x": 60, "y": 40},
            },
            {
                "selector": "button",
                "found": True,
                "visible": True,
                "in_viewport": True,
                "bounding_box": {
                    "x": 10,
                    "y": 20,
                    "top": 20,
                    "right": 110,
                    "bottom": 60,
                    "left": 10,
                    "width": 100,
                    "height": 40,
                },
                "center": {"x": 60, "y": 40},
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "bounding-box-role",
                "--session-id",
                "s1",
                "--role",
                "button",
                "--name",
                "Submit",
            ],
            "action.bounding-box-role",
            {
                "role": "button",
                "name": "Submit",
                "found": True,
                "role_found": True,
                "include_hidden": False,
                "visible": True,
                "in_viewport": True,
                "bounding_box": {
                    "x": 10,
                    "y": 20,
                    "top": 20,
                    "right": 110,
                    "bottom": 60,
                    "left": 10,
                    "width": 100,
                    "height": 40,
                },
                "center": {"x": 60, "y": 40},
                "candidate_count": 1,
            },
            {
                "role": "button",
                "name": "Submit",
                "found": True,
                "role_found": True,
                "include_hidden": False,
                "visible": True,
                "in_viewport": True,
                "bounding_box": {
                    "x": 10,
                    "y": 20,
                    "top": 20,
                    "right": 110,
                    "bottom": 60,
                    "left": 10,
                    "width": 100,
                    "height": 40,
                },
                "center": {"x": 60, "y": 40},
                "candidate_count": 1,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "scroll-into-view",
                "--session-id",
                "s1",
                "--selector",
                "button",
                "--block",
                "nearest",
            ],
            "action.scroll-into-view",
            {
                "selector": "button",
                "found": True,
                "scrolled": True,
                "block": "nearest",
                "inline": "nearest",
                "behavior": "auto",
                "in_viewport": True,
            },
            {
                "selector": "button",
                "found": True,
                "scrolled": True,
                "block": "nearest",
                "inline": "nearest",
                "behavior": "auto",
                "in_viewport": True,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "click-index",
                "--session-id",
                "s1",
                "--selector",
                ".item button",
                "--index",
                "2",
            ],
            "action.click-index",
            {
                "selector": ".item button",
                "index": 2,
                "include_hidden": False,
                "found": True,
                "clicked": True,
                "count": 4,
                "total_count": 5,
                "visible_count": 4,
            },
            {
                "selector": ".item button",
                "index": 2,
                "include_hidden": False,
                "found": True,
                "clicked": True,
                "count": 4,
                "total_count": 5,
                "visible_count": 4,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "select-option",
                "--session-id",
                "s1",
                "--selector",
                "select[name=plan]",
                "--value",
                "pro",
            ],
            "action.select-option",
            {
                "selector": "select[name=plan]",
                "found": True,
                "selected": True,
                "value": "pro",
                "requested_value": "pro",
                "previous_value": "free",
            },
            {
                "selector": "select[name=plan]",
                "found": True,
                "selected": True,
                "value": "pro",
                "requested_value": "pro",
                "previous_value": "free",
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "select-label",
                "--session-id",
                "s1",
                "--label",
                "Plan",
                "--option-label",
                "Pro",
            ],
            "action.select-label",
            {
                "found": True,
                "selectable": True,
                "selected": True,
                "label": "Plan",
                "requested_value": "pro",
                "requested_option_label": "Pro",
                "option_found": True,
                "value": "pro",
                "option_label": "Pro",
                "previous_value": "free",
                "previous_option_label": "Free",
                "changed": True,
            },
            {
                "found": True,
                "selectable": True,
                "selected": True,
                "label": "Plan",
                "requested_value": "pro",
                "requested_option_label": "Pro",
                "option_found": True,
                "value": "pro",
                "option_label": "Pro",
                "previous_value": "free",
                "previous_option_label": "Free",
                "changed": True,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "select-label",
                "--session-id",
                "s1",
                "--label",
                "Plan",
                "--value",
                "team",
                "--exact",
            ],
            "action.select-label",
            {
                "found": True,
                "selectable": True,
                "selected": True,
                "label": "Plan",
                "requested_value": "team",
                "requested_option_label": None,
                "option_found": None,
                "value": "team",
                "option_label": "Team",
                "previous_value": "pro",
                "previous_option_label": "Pro",
                "changed": True,
            },
            {
                "found": True,
                "selectable": True,
                "selected": True,
                "label": "Plan",
                "requested_value": "team",
                "requested_option_label": None,
                "option_found": None,
                "value": "team",
                "option_label": "Team",
                "previous_value": "pro",
                "previous_option_label": "Pro",
                "changed": True,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "set-value",
                "--session-id",
                "s1",
                "--selector",
                "input[name=q]",
                "--value",
                "query",
            ],
            "action.set-value",
            {
                "selector": "input[name=q]",
                "found": True,
                "writable": True,
                "set": True,
                "previous_value": "",
                "value": "query",
                "requested_value": "query",
                "dispatched_events": ["input", "change"],
            },
            {
                "selector": "input[name=q]",
                "found": True,
                "writable": True,
                "set": True,
                "previous_value": "",
                "value": "query",
                "requested_value": "query",
                "dispatched_events": ["input", "change"],
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "dispatch-event",
                "--session-id",
                "s1",
                "--selector",
                "input[name=q]",
                "--event",
                "input",
                "--event",
                "change",
            ],
            "action.dispatch-event",
            {
                "selector": "input[name=q]",
                "found": True,
                "dispatched": True,
                "requested_events": ["input", "change"],
                "events": [
                    {"type": "input", "accepted": True},
                    {"type": "change", "accepted": True},
                ],
                "focused": False,
            },
            {
                "selector": "input[name=q]",
                "found": True,
                "dispatched": True,
                "requested_events": ["input", "change"],
                "events": [
                    {"type": "input", "accepted": True},
                    {"type": "change", "accepted": True},
                ],
                "focused": False,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            ["action", "check", "--session-id", "s1", "--selector", "#agree"],
            "action.check",
            {
                "selector": "#agree",
                "found": True,
                "checkable": True,
                "checked": True,
            },
            {
                "selector": "#agree",
                "found": True,
                "checkable": True,
                "checked": True,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            ["action", "uncheck", "--session-id", "s1", "--selector", "#agree"],
            "action.uncheck",
            {
                "selector": "#agree",
                "found": True,
                "checkable": True,
                "checked": False,
            },
            {
                "selector": "#agree",
                "found": True,
                "checkable": True,
                "checked": False,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "check-label",
                "--session-id",
                "s1",
                "--label",
                "Remember me",
            ],
            "action.check-label",
            {
                "found": True,
                "checkable": True,
                "label": "Remember me",
                "requested_checked": True,
                "previous_checked": False,
                "checked": True,
                "changed": True,
            },
            {
                "found": True,
                "checkable": True,
                "label": "Remember me",
                "requested_checked": True,
                "previous_checked": False,
                "checked": True,
                "changed": True,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "uncheck-label",
                "--session-id",
                "s1",
                "--label",
                "Remember me",
                "--exact",
            ],
            "action.uncheck-label",
            {
                "found": True,
                "checkable": True,
                "label": "Remember me",
                "requested_checked": False,
                "previous_checked": True,
                "checked": False,
                "changed": True,
            },
            {
                "found": True,
                "checkable": True,
                "label": "Remember me",
                "requested_checked": False,
                "previous_checked": True,
                "checked": False,
                "changed": True,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            ["action", "hover", "--session-id", "s1", "--selector", "button"],
            "action.hover",
            {"selector": "button", "found": True, "hovered": True},
            {
                "selector": "button",
                "found": True,
                "hovered": True,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "double-click",
                "--session-id",
                "s1",
                "--selector",
                ".row",
            ],
            "action.double-click",
            {
                "selector": ".row",
                "found": True,
                "double_clicked": True,
                "context_menu": False,
                "events": [
                    "mousedown",
                    "mouseup",
                    "click",
                    "mousedown",
                    "mouseup",
                    "click",
                    "dblclick",
                ],
            },
            {
                "selector": ".row",
                "found": True,
                "double_clicked": True,
                "context_menu": False,
                "events": [
                    "mousedown",
                    "mouseup",
                    "click",
                    "mousedown",
                    "mouseup",
                    "click",
                    "dblclick",
                ],
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "double-click-role",
                "--session-id",
                "s1",
                "--role",
                "button",
                "--name",
                "Edit",
            ],
            "action.double-click-role",
            {
                "role": "button",
                "name": "Edit",
                "found": True,
                "role_found": True,
                "double_clicked": True,
                "candidate_count": 1,
                "events": ["mousedown", "mouseup", "click", "dblclick"],
            },
            {
                "role": "button",
                "name": "Edit",
                "found": True,
                "role_found": True,
                "double_clicked": True,
                "candidate_count": 1,
                "events": ["mousedown", "mouseup", "click", "dblclick"],
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "right-click",
                "--session-id",
                "s1",
                "--selector",
                ".row",
            ],
            "action.right-click",
            {
                "selector": ".row",
                "found": True,
                "right_clicked": True,
                "context_menu": True,
                "events": ["mousedown", "mouseup", "contextmenu"],
            },
            {
                "selector": ".row",
                "found": True,
                "right_clicked": True,
                "context_menu": True,
                "events": ["mousedown", "mouseup", "contextmenu"],
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "right-click-role",
                "--session-id",
                "s1",
                "--role",
                "row",
                "--name",
                "Invoice 123",
            ],
            "action.right-click-role",
            {
                "role": "row",
                "name": "Invoice 123",
                "found": True,
                "role_found": True,
                "right_clicked": True,
                "context_menu": True,
                "candidate_count": 1,
                "events": ["mousedown", "mouseup", "contextmenu"],
            },
            {
                "role": "row",
                "name": "Invoice 123",
                "found": True,
                "role_found": True,
                "right_clicked": True,
                "context_menu": True,
                "candidate_count": 1,
                "events": ["mousedown", "mouseup", "contextmenu"],
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "press",
                "--session-id",
                "s1",
                "--selector",
                "input[name=q]",
                "--key",
                "Enter",
            ],
            "action.press",
            {
                "selector": "input[name=q]",
                "found": True,
                "focused": True,
                "key": "Enter",
                "pressed": True,
                "keydown_accepted": True,
            },
            {
                "selector": "input[name=q]",
                "found": True,
                "focused": True,
                "key": "Enter",
                "pressed": True,
                "keydown_accepted": True,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
        (
            [
                "action",
                "press-key",
                "--session-id",
                "s1",
                "--key",
                "Escape",
                "--shift-key",
            ],
            "action.press-key",
            {
                "key": "Escape",
                "code": "Escape",
                "pressed": True,
                "target": "body",
                "target_info": {"tag_name": "body"},
                "modifiers": {
                    "alt_key": False,
                    "ctrl_key": False,
                    "meta_key": False,
                    "shift_key": True,
                },
                "events": [
                    {"type": "keydown", "accepted": True},
                    {"type": "keypress", "accepted": True},
                    {"type": "keyup", "accepted": True},
                ],
                "keydown_accepted": True,
            },
            {
                "key": "Escape",
                "code": "Escape",
                "pressed": True,
                "target": "body",
                "target_info": {"tag_name": "body"},
                "modifiers": {
                    "alt_key": False,
                    "ctrl_key": False,
                    "meta_key": False,
                    "shift_key": True,
                },
                "events": [
                    {"type": "keydown", "accepted": True},
                    {"type": "keypress", "accepted": True},
                    {"type": "keyup", "accepted": True},
                ],
                "keydown_accepted": True,
                "url": "https://example.test",
                "fallback": "cdp",
            },
        ),
    ],
)
def test_eval_backed_action_commands_emit_structured_results(
    argv: list[str],
    command: str,
    value: dict[str, Any],
    expected_result: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}
    connect_url = "wss://api.lexmount.cn/connection?project_id=project&api_key=secret"

    def fake_resolve(target: Any) -> str:
        assert target.session_id == "s1"
        return connect_url

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed.update(
            {
                "connect_url": connect_url,
                "action": action,
                "expression": request.expression,
            }
        )
        return SimpleNamespace(
            result={
                "url": "https://example.test",
                "value": value,
                "fallback": "cdp",
            }
        )

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url", fake_resolve
    )
    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(argv)

    assert exc_info.value.code == 0
    assert observed["connect_url"] == connect_url
    assert observed["action"] == "eval"
    assert observed["expression"].startswith("() =>")
    if command in {"action.double-click", "action.double-click-role"}:
        assert '"double-click"' in observed["expression"]
        assert "dblclick" in observed["expression"]
    if command in {"action.right-click", "action.right-click-role"}:
        assert '"right-click"' in observed["expression"]
        assert "contextmenu" in observed["expression"]
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": command,
        "session_id": "s1",
        "connect_url": (
            "wss://api.lexmount.cn/connection?project_id=project&api_key=***"
        ),
        "connect_url_masked": True,
        "result": expected_result,
    }


def test_eval_backed_action_reports_missing_selector(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )
    monkeypatch.setattr(
        "browser_cli.cli.run_browser_action",
        lambda **kwargs: SimpleNamespace(
            result={"url": "https://example.test", "value": {"found": False}}
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "get-text", "--session-id", "s1", "--selector", "#x"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.get-text"
    assert payload["result"] == {"found": False, "url": "https://example.test"}


def test_action_set_viewport_emits_structured_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}
    connect_url = "wss://api.lexmount.cn/connection?project_id=project&api_key=secret"

    def fake_resolve(target: Any) -> str:
        assert target.session_id == "s1"
        return connect_url

    def fake_set_page_viewport(
        *,
        connect_url: str,
        width: int,
        height: int,
    ) -> dict[str, Any]:
        observed.update(
            {
                "connect_url": connect_url,
                "width": width,
                "height": height,
            }
        )
        return {
            "url": "https://example.test",
            "title": "Example",
            "requested_viewport": {"width": width, "height": height},
            "previous_viewport": {"width": 800, "height": 600},
            "viewport": {"width": width, "height": height},
            "window_viewport": {
                "width": width,
                "height": height,
                "device_pixel_ratio": 1,
            },
            "changed": True,
        }

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url", fake_resolve
    )
    monkeypatch.setattr("browser_cli.cli._set_page_viewport", fake_set_page_viewport)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "set-viewport",
                "--session-id",
                "s1",
                "--width",
                "1280",
                "--height",
                "720",
            ]
        )

    assert exc_info.value.code == 0
    assert observed == {
        "connect_url": connect_url,
        "width": 1280,
        "height": 720,
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": "action.set-viewport",
        "session_id": "s1",
        "connect_url": (
            "wss://api.lexmount.cn/connection?project_id=project&api_key=***"
        ),
        "connect_url_masked": True,
        "result": {
            "url": "https://example.test",
            "title": "Example",
            "requested_viewport": {"width": 1280, "height": 720},
            "previous_viewport": {"width": 800, "height": 600},
            "viewport": {"width": 1280, "height": 720},
            "window_viewport": {
                "width": 1280,
                "height": 720,
                "device_pixel_ratio": 1,
            },
            "changed": True,
        },
    }


def test_action_set_viewport_rejects_invalid_size_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "set-viewport",
                "--session-id",
                "s1",
                "--width",
                "0",
                "--height",
                "720",
            ]
        )

    assert exc_info.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "action.set-viewport"
    assert payload["error"] == "argument_error"
    assert payload["width"] == 0
    assert payload["height"] == 720


def test_action_screenshot_selector_emits_structured_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}
    connect_url = "wss://api.lexmount.cn/connection?project_id=project&api_key=secret"

    def fake_resolve(target: Any) -> str:
        assert target.session_id == "s1"
        return connect_url

    def fake_screenshot_selector(
        *,
        connect_url: str,
        selector: str,
        output: str | None,
        index: int,
        timeout_ms: float,
    ) -> dict[str, Any]:
        observed.update(
            {
                "connect_url": connect_url,
                "selector": selector,
                "output": output,
                "index": index,
                "timeout_ms": timeout_ms,
            }
        )
        return {
            "url": "https://example.test",
            "selector": selector,
            "index": index,
            "found": True,
            "visible": True,
            "screenshot": True,
            "path": output,
            "match_count": 2,
            "bounding_box": {"x": 10, "y": 20, "width": 300, "height": 120},
        }

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url", fake_resolve
    )
    monkeypatch.setattr(
        "browser_cli.cli._screenshot_selector", fake_screenshot_selector
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "screenshot-selector",
                "--session-id",
                "s1",
                "--selector",
                "main",
                "--output",
                "/tmp/main.png",
                "--index",
                "1",
                "--timeout-ms",
                "1234",
            ]
        )

    assert exc_info.value.code == 0
    assert observed == {
        "connect_url": connect_url,
        "selector": "main",
        "output": "/tmp/main.png",
        "index": 1,
        "timeout_ms": 1234.0,
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": "action.screenshot-selector",
        "session_id": "s1",
        "connect_url": (
            "wss://api.lexmount.cn/connection?project_id=project&api_key=***"
        ),
        "connect_url_masked": True,
        "result": {
            "url": "https://example.test",
            "selector": "main",
            "index": 1,
            "found": True,
            "visible": True,
            "screenshot": True,
            "path": "/tmp/main.png",
            "match_count": 2,
            "bounding_box": {"x": 10, "y": 20, "width": 300, "height": 120},
        },
    }


def test_action_screenshot_selector_rejects_invalid_index_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "screenshot-selector",
                "--session-id",
                "s1",
                "--selector",
                "main",
                "--index",
                "-1",
            ]
        )

    assert exc_info.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "action.screenshot-selector"
    assert payload["error"] == "argument_error"
    assert payload["index"] == -1


def test_action_screenshot_role_emits_structured_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}
    connect_url = "wss://api.lexmount.cn/connection?project_id=project&api_key=secret"

    def fake_resolve(target: Any) -> str:
        assert target.session_id == "s1"
        return connect_url

    def fake_screenshot_role(
        *,
        connect_url: str,
        role: str,
        name: str | None,
        output: str | None,
        index: int,
        timeout_ms: float,
        exact: bool,
        include_hidden: bool,
    ) -> dict[str, Any]:
        observed.update(
            {
                "connect_url": connect_url,
                "role": role,
                "name": name,
                "output": output,
                "index": index,
                "timeout_ms": timeout_ms,
                "exact": exact,
                "include_hidden": include_hidden,
            }
        )
        return {
            "url": "https://example.test",
            "role": role,
            "name": name,
            "exact": exact,
            "include_hidden": include_hidden,
            "index": index,
            "found": True,
            "role_found": True,
            "visible": True,
            "screenshot": True,
            "path": output,
            "match_count": 1,
            "bounding_box": {"x": 10, "y": 20, "width": 140, "height": 40},
        }

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url", fake_resolve
    )
    monkeypatch.setattr("browser_cli.cli._screenshot_role", fake_screenshot_role)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "screenshot-role",
                "--session-id",
                "s1",
                "--role",
                "button",
                "--name",
                "Submit",
                "--output",
                "/tmp/submit.png",
                "--index",
                "0",
                "--timeout-ms",
                "1234",
                "--exact",
                "--include-hidden",
            ]
        )

    assert exc_info.value.code == 0
    assert observed == {
        "connect_url": connect_url,
        "role": "button",
        "name": "Submit",
        "output": "/tmp/submit.png",
        "index": 0,
        "timeout_ms": 1234.0,
        "exact": True,
        "include_hidden": True,
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": "action.screenshot-role",
        "session_id": "s1",
        "connect_url": (
            "wss://api.lexmount.cn/connection?project_id=project&api_key=***"
        ),
        "connect_url_masked": True,
        "result": {
            "url": "https://example.test",
            "role": "button",
            "name": "Submit",
            "exact": True,
            "include_hidden": True,
            "index": 0,
            "found": True,
            "role_found": True,
            "visible": True,
            "screenshot": True,
            "path": "/tmp/submit.png",
            "match_count": 1,
            "bounding_box": {"x": 10, "y": 20, "width": 140, "height": 40},
        },
    }


def test_action_screenshot_role_rejects_invalid_index_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "screenshot-role",
                "--session-id",
                "s1",
                "--role",
                "button",
                "--index",
                "-1",
            ]
        )

    assert exc_info.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "action.screenshot-role"
    assert payload["error"] == "argument_error"
    assert payload["index"] == -1


def test_action_dom_snapshots_mask_sensitive_accessible_names(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"kind": "interactive", "nodes": []}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "interactive-snapshot", "--session-id", "s1"])

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert "sensitiveNamePattern" in expression
    assert "maskValue(element, element.value)" in expression
    assert "valueNameOf(element)" in expression
    assert "nameFromLabels(element)" in expression
    assert "element.value ||" not in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.interactive-snapshot"


@pytest.mark.parametrize(
    ("argv", "snippets"),
    [
        (
            ["action", "get-value", "--session-id", "s1", "--selector", "#password"],
            ["publicValue(element, readFormValue(element))", "value_masked"],
        ),
        (
            [
                "action",
                "get-value-role",
                "--session-id",
                "s1",
                "--role",
                "textbox",
                "--name",
                "Password",
            ],
            [
                "publicValue(element, readFormValue(element))",
                "role_found",
                "value_masked",
            ],
        ),
        (
            [
                "action",
                "wait-value",
                "--session-id",
                "s1",
                "--selector",
                "input[name=password]",
                "--value",
                "fake-secret",
            ],
            ["publicRequestedValue(element, requestedValue)", "requested_value_masked"],
        ),
        (
            [
                "action",
                "wait-value-role",
                "--session-id",
                "s1",
                "--role",
                "textbox",
                "--name",
                "Password",
                "--value",
                "fake-secret",
            ],
            [
                "publicRequestedValue(element, requestedValue)",
                "requested_value_masked",
                "role_found",
            ],
        ),
        (
            [
                "action",
                "set-value",
                "--session-id",
                "s1",
                "--selector",
                "input[name=api_key]",
                "--value",
                "fake-secret",
            ],
            ["previous_value_masked", "requested_value_masked", "value_masked"],
        ),
        (
            ["action", "clear", "--session-id", "s1", "--selector", "#token"],
            ["previous_value_masked", "value_masked"],
        ),
        (
            [
                "action",
                "clear-role",
                "--session-id",
                "s1",
                "--role",
                "textbox",
                "--name",
                "Password",
            ],
            ["previous_value_masked", "value_masked", "role_found"],
        ),
        (
            [
                "action",
                "fill-label",
                "--session-id",
                "s1",
                "--label",
                "Password",
                "--text",
                "fake-secret",
            ],
            ["text_masked", "previous_value_masked", "value_masked"],
        ),
        (
            [
                "action",
                "fill-role",
                "--session-id",
                "s1",
                "--role",
                "textbox",
                "--name",
                "Password",
                "--text",
                "fake-secret",
            ],
            ["text_masked", "previous_value_masked", "value_masked"],
        ),
        (
            [
                "action",
                "inspect",
                "--session-id",
                "s1",
                "--selector",
                "input[name=token]",
                "--include-html",
            ],
            ["sensitiveElement(element) && !revealSensitiveValues", "value_masked"],
        ),
        (
            [
                "action",
                "form-snapshot",
                "--session-id",
                "s1",
                "--selector",
                "form",
            ],
            ["const sensitive = sensitiveElement(field)", "value_masked"],
        ),
    ],
)
def test_sensitive_value_action_expressions_emit_masking_metadata(
    argv: list[str],
    snippets: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"found": True}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(argv)

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert "sensitiveNamePattern" in expression
    for snippet in snippets:
        assert snippet in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True


def test_action_link_snapshot_expression_masks_sensitive_url_parts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"kind": "links", "links": []}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "link-snapshot",
                "--session-id",
                "s1",
                "--selector",
                "main",
                "--include-empty",
                "--same-origin-only",
            ]
        )

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert 'const rootSelector = "main"' in expression
    assert "const includeEmpty = true" in expression
    assert "const sameOriginOnly = true" in expression
    assert "sensitiveUrlParamName" in expression
    assert "sensitiveUrlParamPattern" in expression
    assert "absolute_url_masked" in expression
    assert "href_masked" in expression
    assert "same_origin: parsed.origin === location.origin" in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.link-snapshot"


def test_action_table_snapshot_expression_extracts_bounded_rows_and_cells(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"kind": "tables", "tables": []}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "table-snapshot",
                "--session-id",
                "s1",
                "--selector",
                ".report",
                "--include-hidden",
                "--max-rows",
                "7",
                "--max-cells",
                "3",
            ]
        )

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert 'const rootSelector = ".report"' in expression
    assert "const maxRows = Math.max(0, 7)" in expression
    assert "const maxCells = Math.max(0, 3)" in expression
    assert "table,[role~='table'],[role~='grid']" in expression
    assert "[role~='gridcell']" in expression
    assert (
        "headers: headerRow ? headerRow.cells.map((cell) => cell.text) : []"
        in expression
    )
    assert "sensitiveUrlParamName" in expression
    assert "absolute_url_masked" in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.table-snapshot"


def test_action_list_snapshot_expression_extracts_items_and_links(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"kind": "lists", "lists": []}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "list-snapshot",
                "--session-id",
                "s1",
                "--selector",
                ".results",
                "--include-hidden",
                "--max-items",
                "7",
            ]
        )

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert 'const rootSelector = ".results"' in expression
    assert "const maxItems = Math.max(0, 7)" in expression
    assert "ul" in expression
    assert "[role~='listbox']" in expression
    assert "[role~='menuitemcheckbox']" in expression
    assert "[role~='treeitem']" in expression
    assert "item_count: candidateItems.length" in expression
    assert "selected: selectedState(item)" in expression
    assert "checked: checkedState(item)" in expression
    assert "href_masked" in expression
    assert "absolute_url_masked" in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.list-snapshot"


def test_action_text_snapshot_expression_extracts_bounded_text_blocks(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"kind": "text", "texts": []}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "text-snapshot",
                "--session-id",
                "s1",
                "--selector",
                "main",
                "--include-hidden",
                "--max-chars",
                "120",
            ]
        )

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert 'const rootSelector = "main"' in expression
    assert "const maxChars = Math.max(0, 120)" in expression
    assert "[role~='alert']" in expression
    assert "[role~='status']" in expression
    assert "[aria-live]" in expression
    assert "text_count: nonEmptyTextBlocks.length" in expression
    assert "text_truncated" in expression
    assert "aria_live" in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.text-snapshot"


def test_action_dialog_snapshot_expression_extracts_dialog_controls(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"kind": "dialogs", "dialogs": []}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "dialog-snapshot",
                "--session-id",
                "s1",
                "--selector",
                "body",
                "--include-hidden",
                "--max-controls",
                "8",
                "--max-chars",
                "240",
            ]
        )

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert 'const rootSelector = "body"' in expression
    assert "const maxControls = Math.max(0, 8)" in expression
    assert "const maxChars = Math.max(0, 240)" in expression
    assert "[role~='alertdialog']" in expression
    assert "[aria-modal='true']" in expression
    assert "interactiveSelector" in expression
    assert "control_count: candidateControls.length" in expression
    assert "controls_truncated" in expression
    assert "href_masked" in expression
    assert "absolute_url_masked" in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.dialog-snapshot"


def test_action_wait_dialog_expression_waits_for_matching_dialogs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"kind": "dialog_wait"}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "wait-dialog",
                "--session-id",
                "s1",
                "--selector",
                "body",
                "--text",
                "Delete",
                "--match",
                "regex",
                "--modal-only",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
                "--case-sensitive",
                "--max-controls",
                "8",
                "--max-chars",
                "240",
            ]
        )

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert "new Promise" in expression
    assert "const collectDialogs = () =>" in expression
    assert 'const rootSelector = "body"' in expression
    assert 'const requestedText = "Delete"' in expression
    assert 'const matchMode = "regex"' in expression
    assert "const modalOnly = true" in expression
    assert "const timeoutMs = Math.max(0, 1000.0)" in expression
    assert "const maxControls = Math.max(0, 8)" in expression
    assert "dialogText" in expression
    assert "total_dialog_count" in expression
    assert 'error: "invalid_regex"' in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.wait-dialog"


def test_action_frame_snapshot_expression_extracts_frame_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"kind": "frames", "frames": []}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "frame-snapshot",
                "--session-id",
                "s1",
                "--selector",
                "main",
                "--include-hidden",
                "--max-chars",
                "160",
            ]
        )

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert 'const rootSelector = "main"' in expression
    assert "const maxChars = Math.max(0, 160)" in expression
    assert 'const frameSelector = "iframe,frame"' in expression
    assert "contentDocument" in expression
    assert "readable: true" in expression
    assert "readable: false" in expression
    assert "frame_url_masked" in expression
    assert "src_masked" in expression
    assert "absolute_url_masked" in expression
    assert "bounding_box: rectInfo(frame)" in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.frame-snapshot"


def test_action_wait_frame_expression_waits_for_matching_frames(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"kind": "frame_wait"}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "wait-frame",
                "--session-id",
                "s1",
                "--selector",
                "main",
                "--url",
                "checkout",
                "--url-match",
                "regex",
                "--text",
                "Pay",
                "--text-match",
                "contains",
                "--readable-only",
                "--same-origin-only",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
                "--case-sensitive",
                "--max-chars",
                "240",
            ]
        )

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert "new Promise" in expression
    assert "const collectFrames = () =>" in expression
    assert 'const rootSelector = "main"' in expression
    assert 'const requestedUrl = "checkout"' in expression
    assert 'const urlMatchMode = "regex"' in expression
    assert 'const requestedText = "Pay"' in expression
    assert 'const textMatchMode = "contains"' in expression
    assert "const readableOnly = true" in expression
    assert "const sameOriginOnly = true" in expression
    assert "const timeoutMs = Math.max(0, 1000.0)" in expression
    assert "const maxChars = Math.max(0, 240)" in expression
    assert "frameUrlText" in expression
    assert "total_frame_count" in expression
    assert 'error: "invalid_regex"' in expression
    assert "invalid_filter: invalidFilter" in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.wait-frame"


def test_action_performance_snapshot_expression_extracts_timing_entries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(
            result={"value": {"kind": "performance", "resources": []}}
        )

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "performance-snapshot",
                "--session-id",
                "s1",
                "--max-resources",
                "7",
                "--initiator-type",
                "fetch",
                "--min-duration-ms",
                "50",
            ]
        )

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert "const maxResources = Math.max(0, 7)" in expression
    assert 'const requestedInitiatorType = "fetch"' in expression
    assert "const minDurationMs = Math.max(0, 50.0)" in expression
    assert 'performance.getEntriesByType("navigation")' in expression
    assert 'performance.getEntriesByType("resource")' in expression
    assert "initiator_types: initiatorTypes" in expression
    assert "response_status: responseStatus(entry)" in expression
    assert "name_masked" in expression
    assert "absolute_url_masked" in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.performance-snapshot"


def test_action_network_snapshot_expression_installs_fetch_and_xhr_buffer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"kind": "network", "entries": []}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "network-snapshot",
                "--session-id",
                "s1",
                "--max-entries",
                "9",
                "--source",
                "fetch",
                "--method",
                "post",
                "--failed-only",
                "--install-only",
                "--clear",
            ]
        )

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert "const maxEntries = Math.max(0, 9)" in expression
    assert "const clearRequested = true" in expression
    assert "const installOnly = true" in expression
    assert 'const requestedSource = "fetch"' in expression
    assert 'const requestedMethod = "POST"' in expression
    assert "const failedOnly = true" in expression
    assert "__browserCliNetworkSnapshot" in expression
    assert "window.fetch = function" in expression
    assert "XMLHttpRequest.prototype.open" in expression
    assert "XMLHttpRequest.prototype.send" in expression
    assert "xhr_timeout" in expression
    assert "request_has_body" in expression
    assert "duration_ms" in expression
    assert "absolute_url_masked" in expression
    assert "buffered_count_after" in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.network-snapshot"


def test_action_wait_network_expression_waits_for_matching_entries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"kind": "network_wait"}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "wait-network",
                "--session-id",
                "s1",
                "--url",
                "/api/save",
                "--url-match",
                "regex",
                "--source",
                "fetch",
                "--method",
                "post",
                "--status",
                "201",
                "--after-index",
                "2",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
                "--case-sensitive",
            ]
        )

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert "new Promise" in expression
    assert 'const requestedUrl = "/api/save"' in expression
    assert 'const urlMatchMode = "regex"' in expression
    assert 'const requestedSource = "fetch"' in expression
    assert 'const requestedMethod = "POST"' in expression
    assert "const requestedStatus = 201" in expression
    assert "const caseSensitive = true" in expression
    assert "const afterIndex = 2" in expression
    assert "const timeoutMs = Math.max(0, 1000.0)" in expression
    assert "__browserCliNetworkSnapshot" in expression
    assert (
        "const matchingEntries = () => state.entries.filter(entryMatches)" in expression
    )
    assert 'error: "invalid_regex"' in expression
    assert "url_masked" in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.wait-network"


def test_action_console_snapshot_expression_installs_buffered_listener(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"kind": "console", "entries": []}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "console-snapshot",
                "--session-id",
                "s1",
                "--max-entries",
                "9",
                "--install-only",
                "--clear",
            ]
        )

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert "const maxEntries = Math.max(0, 9)" in expression
    assert "const clearRequested = true" in expression
    assert "const installOnly = true" in expression
    assert "__browserCliConsoleSnapshot" in expression
    assert 'window.addEventListener("error"' in expression
    assert 'window.addEventListener("unhandledrejection"' in expression
    assert "console[method] = function" in expression
    assert "sensitivePairPattern" in expression
    assert "entryTextPayload" in expression
    assert "filename_masked" in expression
    assert "url_masked" in expression
    assert "text_masked" in expression
    assert "buffered_count_after" in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.console-snapshot"


def test_action_wait_console_expression_waits_for_matching_entries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"kind": "console_wait"}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "wait-console",
                "--session-id",
                "s1",
                "--text",
                "Boom",
                "--match",
                "regex",
                "--source",
                "pageerror",
                "--level",
                "error",
                "--after-index",
                "2",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
                "--case-sensitive",
            ]
        )

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert "new Promise" in expression
    assert 'const requestedText = "Boom"' in expression
    assert 'const matchMode = "regex"' in expression
    assert 'const requestedSource = "pageerror"' in expression
    assert 'const requestedLevel = "error"' in expression
    assert "const caseSensitive = true" in expression
    assert "const afterIndex = 2" in expression
    assert "const timeoutMs = Math.max(0, 1000.0)" in expression
    assert "__browserCliConsoleSnapshot" in expression
    assert (
        "const matchingEntries = () => state.entries.filter(entryMatches)" in expression
    )
    assert 'error: "invalid_regex"' in expression
    assert "url_masked" in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.wait-console"


def test_action_outline_snapshot_expression_extracts_headings_and_landmarks(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url",
        lambda target: "wss://example.test/devtools",
    )

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed["expression"] = request.expression
        return SimpleNamespace(result={"value": {"kind": "outline", "nodes": []}})

    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "action",
                "outline-snapshot",
                "--session-id",
                "s1",
                "--selector",
                "main",
                "--include-hidden",
                "--max-nodes",
                "4",
            ]
        )

    assert exc_info.value.code == 0
    expression = observed["expression"]
    assert 'const rootSelector = "main"' in expression
    assert "[role~='heading']" in expression
    assert "[role~='navigation']" in expression
    assert "semanticLandmarkRole" in expression
    assert "headingLevel" in expression
    assert "heading_count: headings.length" in expression
    assert "landmark_count: landmarks.length" in expression
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "action.outline-snapshot"


@pytest.mark.parametrize(
    ("argv", "command", "value", "expected_result"),
    [
        (
            [
                "action",
                "click-text",
                "--session-id",
                "s1",
                "--text",
                "Save",
                "--exact",
            ],
            "action.click-text",
            {"found": True, "clicked": True, "text": "Save"},
            {
                "found": True,
                "clicked": True,
                "text": "Save",
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "select-role",
                "--session-id",
                "s1",
                "--role",
                "combobox",
                "--name",
                "Plan",
                "--option-label",
                "Pro",
            ],
            "action.select-role",
            {
                "role": "combobox",
                "name": "Plan",
                "found": True,
                "role_found": True,
                "selectable": True,
                "selected": True,
                "requested_value": "pro",
                "requested_option_label": "Pro",
                "option_found": True,
                "value": "pro",
                "option_label": "Pro",
                "previous_value": "free",
                "changed": True,
                "candidate_count": 1,
            },
            {
                "role": "combobox",
                "name": "Plan",
                "found": True,
                "role_found": True,
                "selectable": True,
                "selected": True,
                "requested_value": "pro",
                "requested_option_label": "Pro",
                "option_found": True,
                "value": "pro",
                "option_label": "Pro",
                "previous_value": "free",
                "changed": True,
                "candidate_count": 1,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "check-role",
                "--session-id",
                "s1",
                "--role",
                "checkbox",
                "--name",
                "Remember me",
            ],
            "action.check-role",
            {
                "role": "checkbox",
                "name": "Remember me",
                "found": True,
                "role_found": True,
                "checkable": True,
                "requested_checked": True,
                "previous_checked": False,
                "checked": True,
                "changed": True,
                "candidate_count": 1,
            },
            {
                "role": "checkbox",
                "name": "Remember me",
                "found": True,
                "role_found": True,
                "checkable": True,
                "requested_checked": True,
                "previous_checked": False,
                "checked": True,
                "changed": True,
                "candidate_count": 1,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "uncheck-role",
                "--session-id",
                "s1",
                "--role",
                "checkbox",
                "--name",
                "Remember me",
            ],
            "action.uncheck-role",
            {
                "role": "checkbox",
                "name": "Remember me",
                "found": True,
                "role_found": True,
                "checkable": True,
                "requested_checked": False,
                "previous_checked": True,
                "checked": False,
                "changed": True,
                "candidate_count": 1,
            },
            {
                "role": "checkbox",
                "name": "Remember me",
                "found": True,
                "role_found": True,
                "checkable": True,
                "requested_checked": False,
                "previous_checked": True,
                "checked": False,
                "changed": True,
                "candidate_count": 1,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "click-role",
                "--session-id",
                "s1",
                "--role",
                "button",
                "--name",
                "Submit",
            ],
            "action.click-role",
            {"found": True, "clicked": True, "role": "button", "name": "Submit"},
            {
                "found": True,
                "clicked": True,
                "role": "button",
                "name": "Submit",
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "hover-role",
                "--session-id",
                "s1",
                "--role",
                "button",
                "--name",
                "Menu",
            ],
            "action.hover-role",
            {
                "role": "button",
                "name": "Menu",
                "found": True,
                "role_found": True,
                "hovered": True,
                "candidate_count": 1,
            },
            {
                "role": "button",
                "name": "Menu",
                "found": True,
                "role_found": True,
                "hovered": True,
                "candidate_count": 1,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "press-role",
                "--session-id",
                "s1",
                "--role",
                "textbox",
                "--name",
                "Search",
                "--key",
                "Enter",
            ],
            "action.press-role",
            {
                "role": "textbox",
                "name": "Search",
                "found": True,
                "role_found": True,
                "focused": True,
                "key": "Enter",
                "pressed": True,
                "keydown_accepted": True,
                "candidate_count": 1,
            },
            {
                "role": "textbox",
                "name": "Search",
                "found": True,
                "role_found": True,
                "focused": True,
                "key": "Enter",
                "pressed": True,
                "keydown_accepted": True,
                "candidate_count": 1,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "scroll-into-view-role",
                "--session-id",
                "s1",
                "--role",
                "button",
                "--name",
                "Submit",
                "--block",
                "center",
            ],
            "action.scroll-into-view-role",
            {
                "role": "button",
                "name": "Submit",
                "found": True,
                "role_found": True,
                "scrolled": True,
                "block": "center",
                "inline": "nearest",
                "behavior": "auto",
                "in_viewport": True,
                "candidate_count": 1,
            },
            {
                "role": "button",
                "name": "Submit",
                "found": True,
                "role_found": True,
                "scrolled": True,
                "block": "center",
                "inline": "nearest",
                "behavior": "auto",
                "in_viewport": True,
                "candidate_count": 1,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "fill-label",
                "--session-id",
                "s1",
                "--label",
                "Email",
                "--text",
                "user@example.test",
            ],
            "action.fill-label",
            {
                "found": True,
                "filled": True,
                "label": "Email",
                "value": "user@example.test",
            },
            {
                "found": True,
                "filled": True,
                "label": "Email",
                "value": "user@example.test",
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "fill-role",
                "--session-id",
                "s1",
                "--role",
                "textbox",
                "--name",
                "Email",
                "--text",
                "user@example.test",
            ],
            "action.fill-role",
            {
                "found": True,
                "filled": True,
                "role": "textbox",
                "name": "Email",
                "value": "user@example.test",
            },
            {
                "found": True,
                "filled": True,
                "role": "textbox",
                "name": "Email",
                "value": "user@example.test",
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "page-info",
                "--session-id",
                "s1",
            ],
            "action.page-info",
            {
                "url": "https://example.test/dashboard",
                "title": "Dashboard",
                "ready_state": "complete",
                "visibility_state": "visible",
                "body_text_length": 120,
                "html_length": 2048,
                "viewport": {
                    "width": 1280,
                    "height": 720,
                    "device_pixel_ratio": 2,
                },
                "scroll": {"x": 0, "y": 240},
            },
            {
                "url": "https://example.test/dashboard",
                "title": "Dashboard",
                "ready_state": "complete",
                "visibility_state": "visible",
                "body_text_length": 120,
                "html_length": 2048,
                "viewport": {
                    "width": 1280,
                    "height": 720,
                    "device_pixel_ratio": 2,
                },
                "scroll": {"x": 0, "y": 240},
            },
        ),
        (
            [
                "action",
                "accessibility-snapshot",
                "--session-id",
                "s1",
                "--max-nodes",
                "2",
            ],
            "action.accessibility-snapshot",
            {"kind": "dom-accessibility", "node_count": 2, "nodes": []},
            {
                "kind": "dom-accessibility",
                "node_count": 2,
                "nodes": [],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "link-snapshot",
                "--session-id",
                "s1",
                "--selector",
                "nav",
                "--same-origin-only",
                "--max-nodes",
                "2",
            ],
            "action.link-snapshot",
            {
                "kind": "links",
                "selector": "nav",
                "link_count": 1,
                "node_count": 1,
                "links": [
                    {
                        "selector": "#settings-link",
                        "tag": "a",
                        "role": "link",
                        "name": "Settings",
                        "text": "Settings",
                        "href": "/settings?token=***",
                        "href_masked": True,
                        "absolute_url": "https://example.test/settings?token=***",
                        "absolute_url_masked": True,
                        "same_origin": True,
                        "external": False,
                    }
                ],
            },
            {
                "kind": "links",
                "selector": "nav",
                "link_count": 1,
                "node_count": 1,
                "links": [
                    {
                        "selector": "#settings-link",
                        "tag": "a",
                        "role": "link",
                        "name": "Settings",
                        "text": "Settings",
                        "href": "/settings?token=***",
                        "href_masked": True,
                        "absolute_url": "https://example.test/settings?token=***",
                        "absolute_url_masked": True,
                        "same_origin": True,
                        "external": False,
                    }
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "table-snapshot",
                "--session-id",
                "s1",
                "--selector",
                ".results",
                "--max-rows",
                "5",
                "--max-cells",
                "4",
            ],
            "action.table-snapshot",
            {
                "kind": "tables",
                "selector": ".results",
                "table_count": 1,
                "node_count": 1,
                "tables": [
                    {
                        "table_index": 0,
                        "selector": "table.results",
                        "caption": "Invoices",
                        "headers": ["Invoice", "Amount"],
                        "row_count": 2,
                        "rows": [
                            {
                                "row_index": 0,
                                "cell_count": 2,
                                "cells": [
                                    {"column_index": 0, "text": "Invoice"},
                                    {"column_index": 1, "text": "Amount"},
                                ],
                            },
                            {
                                "row_index": 1,
                                "cell_count": 2,
                                "cells": [
                                    {
                                        "column_index": 0,
                                        "text": "INV-1",
                                        "links": [
                                            {
                                                "text": "INV-1",
                                                "href": "/invoice?id=1&token=***",
                                                "href_masked": True,
                                                "absolute_url": "https://example.test/invoice?id=1&token=***",
                                                "absolute_url_masked": True,
                                            }
                                        ],
                                    },
                                    {"column_index": 1, "text": "$42"},
                                ],
                            },
                        ],
                    }
                ],
            },
            {
                "kind": "tables",
                "selector": ".results",
                "table_count": 1,
                "node_count": 1,
                "tables": [
                    {
                        "table_index": 0,
                        "selector": "table.results",
                        "caption": "Invoices",
                        "headers": ["Invoice", "Amount"],
                        "row_count": 2,
                        "rows": [
                            {
                                "row_index": 0,
                                "cell_count": 2,
                                "cells": [
                                    {"column_index": 0, "text": "Invoice"},
                                    {"column_index": 1, "text": "Amount"},
                                ],
                            },
                            {
                                "row_index": 1,
                                "cell_count": 2,
                                "cells": [
                                    {
                                        "column_index": 0,
                                        "text": "INV-1",
                                        "links": [
                                            {
                                                "text": "INV-1",
                                                "href": "/invoice?id=1&token=***",
                                                "href_masked": True,
                                                "absolute_url": "https://example.test/invoice?id=1&token=***",
                                                "absolute_url_masked": True,
                                            }
                                        ],
                                    },
                                    {"column_index": 1, "text": "$42"},
                                ],
                            },
                        ],
                    }
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "list-snapshot",
                "--session-id",
                "s1",
                "--selector",
                ".results",
                "--max-items",
                "5",
            ],
            "action.list-snapshot",
            {
                "kind": "lists",
                "selector": ".results",
                "list_count": 1,
                "node_count": 1,
                "lists": [
                    {
                        "list_index": 0,
                        "selector": "ul.results",
                        "item_count": 2,
                        "items": [
                            {
                                "item_index": 0,
                                "selector": "li:nth-of-type(1)",
                                "text": "Alpha",
                                "checked": None,
                                "selected": None,
                                "links": [],
                            },
                            {
                                "item_index": 1,
                                "selector": "li:nth-of-type(2)",
                                "text": "Beta",
                                "checked": True,
                                "selected": False,
                                "links": [
                                    {
                                        "text": "Details",
                                        "href": "/details?token=***",
                                        "href_masked": True,
                                        "absolute_url": "https://example.test/details?token=***",
                                        "absolute_url_masked": True,
                                    }
                                ],
                            },
                        ],
                    }
                ],
            },
            {
                "kind": "lists",
                "selector": ".results",
                "list_count": 1,
                "node_count": 1,
                "lists": [
                    {
                        "list_index": 0,
                        "selector": "ul.results",
                        "item_count": 2,
                        "items": [
                            {
                                "item_index": 0,
                                "selector": "li:nth-of-type(1)",
                                "text": "Alpha",
                                "checked": None,
                                "selected": None,
                                "links": [],
                            },
                            {
                                "item_index": 1,
                                "selector": "li:nth-of-type(2)",
                                "text": "Beta",
                                "checked": True,
                                "selected": False,
                                "links": [
                                    {
                                        "text": "Details",
                                        "href": "/details?token=***",
                                        "href_masked": True,
                                        "absolute_url": "https://example.test/details?token=***",
                                        "absolute_url_masked": True,
                                    }
                                ],
                            },
                        ],
                    }
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "text-snapshot",
                "--session-id",
                "s1",
                "--selector",
                "main",
                "--max-chars",
                "40",
            ],
            "action.text-snapshot",
            {
                "kind": "text",
                "selector": "main",
                "text_count": 2,
                "node_count": 2,
                "texts": [
                    {
                        "index": 0,
                        "selector": "h1",
                        "tag": "h1",
                        "role": "heading",
                        "kind": "heading",
                        "level": 1,
                        "text": "Dashboard",
                        "text_length": 9,
                        "text_truncated": False,
                    },
                    {
                        "index": 1,
                        "selector": "[role=status]",
                        "tag": "div",
                        "role": "status",
                        "kind": "live-region",
                        "aria_live": "polite",
                        "text": "Saved",
                        "text_length": 5,
                        "text_truncated": False,
                    },
                ],
            },
            {
                "kind": "text",
                "selector": "main",
                "text_count": 2,
                "node_count": 2,
                "texts": [
                    {
                        "index": 0,
                        "selector": "h1",
                        "tag": "h1",
                        "role": "heading",
                        "kind": "heading",
                        "level": 1,
                        "text": "Dashboard",
                        "text_length": 9,
                        "text_truncated": False,
                    },
                    {
                        "index": 1,
                        "selector": "[role=status]",
                        "tag": "div",
                        "role": "status",
                        "kind": "live-region",
                        "aria_live": "polite",
                        "text": "Saved",
                        "text_length": 5,
                        "text_truncated": False,
                    },
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "dialog-snapshot",
                "--session-id",
                "s1",
                "--selector",
                "body",
                "--max-controls",
                "4",
                "--max-chars",
                "120",
            ],
            "action.dialog-snapshot",
            {
                "kind": "dialogs",
                "selector": "body",
                "dialog_count": 1,
                "node_count": 1,
                "dialogs": [
                    {
                        "dialog_index": 0,
                        "selector": "[role=dialog]",
                        "tag": "div",
                        "role": "dialog",
                        "name": "Confirm",
                        "title": "Confirm",
                        "description": "Delete this item?",
                        "modal": True,
                        "text": "Confirm Delete this item?",
                        "text_length": 25,
                        "text_truncated": False,
                        "control_count": 2,
                        "controls": [
                            {
                                "control_index": 0,
                                "selector": "button:nth-of-type(1)",
                                "tag": "button",
                                "role": "button",
                                "name": "Cancel",
                                "text": "Cancel",
                                "disabled": False,
                            },
                            {
                                "control_index": 1,
                                "selector": "a.confirm",
                                "tag": "a",
                                "role": "link",
                                "name": "Delete",
                                "text": "Delete",
                                "href": "/delete?token=***",
                                "href_masked": True,
                                "absolute_url": "https://example.test/delete?token=***",
                                "absolute_url_masked": True,
                            },
                        ],
                    }
                ],
            },
            {
                "kind": "dialogs",
                "selector": "body",
                "dialog_count": 1,
                "node_count": 1,
                "dialogs": [
                    {
                        "dialog_index": 0,
                        "selector": "[role=dialog]",
                        "tag": "div",
                        "role": "dialog",
                        "name": "Confirm",
                        "title": "Confirm",
                        "description": "Delete this item?",
                        "modal": True,
                        "text": "Confirm Delete this item?",
                        "text_length": 25,
                        "text_truncated": False,
                        "control_count": 2,
                        "controls": [
                            {
                                "control_index": 0,
                                "selector": "button:nth-of-type(1)",
                                "tag": "button",
                                "role": "button",
                                "name": "Cancel",
                                "text": "Cancel",
                                "disabled": False,
                            },
                            {
                                "control_index": 1,
                                "selector": "a.confirm",
                                "tag": "a",
                                "role": "link",
                                "name": "Delete",
                                "text": "Delete",
                                "href": "/delete?token=***",
                                "href_masked": True,
                                "absolute_url": "https://example.test/delete?token=***",
                                "absolute_url_masked": True,
                            },
                        ],
                    }
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-dialog",
                "--session-id",
                "s1",
                "--selector",
                "body",
                "--text",
                "Delete",
                "--modal-only",
            ],
            "action.wait-dialog",
            {
                "kind": "dialog_wait",
                "found": True,
                "matched": True,
                "timed_out": False,
                "requested_text": "Delete",
                "match": "contains",
                "case_sensitive": False,
                "modal_only": True,
                "dialog_count": 1,
                "total_dialog_count": 1,
                "dialog": {
                    "dialog_index": 0,
                    "selector": "[role=dialog]",
                    "role": "dialog",
                    "title": "Confirm",
                    "modal": True,
                    "text": "Confirm Delete this item?",
                    "controls": [
                        {
                            "control_index": 1,
                            "selector": "a.confirm",
                            "role": "link",
                            "name": "Delete",
                            "text": "Delete",
                            "href": "/delete?token=***",
                            "href_masked": True,
                        }
                    ],
                },
                "dialogs": [
                    {
                        "dialog_index": 0,
                        "selector": "[role=dialog]",
                        "role": "dialog",
                        "title": "Confirm",
                        "modal": True,
                        "text": "Confirm Delete this item?",
                        "controls": [
                            {
                                "control_index": 1,
                                "selector": "a.confirm",
                                "role": "link",
                                "name": "Delete",
                                "text": "Delete",
                                "href": "/delete?token=***",
                                "href_masked": True,
                            }
                        ],
                    }
                ],
            },
            {
                "kind": "dialog_wait",
                "found": True,
                "matched": True,
                "timed_out": False,
                "requested_text": "Delete",
                "match": "contains",
                "case_sensitive": False,
                "modal_only": True,
                "dialog_count": 1,
                "total_dialog_count": 1,
                "dialog": {
                    "dialog_index": 0,
                    "selector": "[role=dialog]",
                    "role": "dialog",
                    "title": "Confirm",
                    "modal": True,
                    "text": "Confirm Delete this item?",
                    "controls": [
                        {
                            "control_index": 1,
                            "selector": "a.confirm",
                            "role": "link",
                            "name": "Delete",
                            "text": "Delete",
                            "href": "/delete?token=***",
                            "href_masked": True,
                        }
                    ],
                },
                "dialogs": [
                    {
                        "dialog_index": 0,
                        "selector": "[role=dialog]",
                        "role": "dialog",
                        "title": "Confirm",
                        "modal": True,
                        "text": "Confirm Delete this item?",
                        "controls": [
                            {
                                "control_index": 1,
                                "selector": "a.confirm",
                                "role": "link",
                                "name": "Delete",
                                "text": "Delete",
                                "href": "/delete?token=***",
                                "href_masked": True,
                            }
                        ],
                    }
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "frame-snapshot",
                "--session-id",
                "s1",
                "--selector",
                "main",
                "--max-chars",
                "80",
            ],
            "action.frame-snapshot",
            {
                "kind": "frames",
                "selector": "main",
                "frame_count": 1,
                "node_count": 1,
                "frames": [
                    {
                        "frame_index": 0,
                        "selector": "iframe.checkout",
                        "tag": "iframe",
                        "name": "Checkout",
                        "id": "checkout-frame",
                        "name_attribute": "checkout",
                        "title_attribute": "Checkout",
                        "src": "/checkout?token=***",
                        "src_masked": True,
                        "absolute_url": "https://example.test/checkout?token=***",
                        "absolute_url_masked": True,
                        "same_origin": True,
                        "readable": True,
                        "frame_url": "https://example.test/checkout?token=***",
                        "frame_url_masked": True,
                        "frame_title": "Checkout",
                        "body_text": "Pay now",
                        "body_text_length": 7,
                        "body_text_truncated": False,
                    }
                ],
            },
            {
                "kind": "frames",
                "selector": "main",
                "frame_count": 1,
                "node_count": 1,
                "frames": [
                    {
                        "frame_index": 0,
                        "selector": "iframe.checkout",
                        "tag": "iframe",
                        "name": "Checkout",
                        "id": "checkout-frame",
                        "name_attribute": "checkout",
                        "title_attribute": "Checkout",
                        "src": "/checkout?token=***",
                        "src_masked": True,
                        "absolute_url": "https://example.test/checkout?token=***",
                        "absolute_url_masked": True,
                        "same_origin": True,
                        "readable": True,
                        "frame_url": "https://example.test/checkout?token=***",
                        "frame_url_masked": True,
                        "frame_title": "Checkout",
                        "body_text": "Pay now",
                        "body_text_length": 7,
                        "body_text_truncated": False,
                    }
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-frame",
                "--session-id",
                "s1",
                "--selector",
                "main",
                "--url",
                "checkout",
                "--text",
                "Pay",
                "--readable-only",
                "--same-origin-only",
            ],
            "action.wait-frame",
            {
                "kind": "frame_wait",
                "found": True,
                "matched": True,
                "timed_out": False,
                "requested_url": "checkout",
                "url_match": "contains",
                "requested_text": "Pay",
                "text_match": "contains",
                "case_sensitive": False,
                "readable_only": True,
                "same_origin_only": True,
                "frame_count": 1,
                "total_frame_count": 1,
                "frame": {
                    "frame_index": 0,
                    "selector": "iframe.checkout",
                    "tag": "iframe",
                    "name": "Checkout",
                    "id": "checkout-frame",
                    "name_attribute": "checkout",
                    "title_attribute": "Checkout",
                    "src": "/checkout?token=***",
                    "src_masked": True,
                    "absolute_url": "https://example.test/checkout?token=***",
                    "absolute_url_masked": True,
                    "same_origin": True,
                    "readable": True,
                    "frame_url": "https://example.test/checkout?token=***",
                    "frame_url_masked": True,
                    "frame_title": "Checkout",
                    "body_text": "Pay now",
                    "body_text_length": 7,
                    "body_text_truncated": False,
                },
                "frames": [
                    {
                        "frame_index": 0,
                        "selector": "iframe.checkout",
                        "tag": "iframe",
                        "name": "Checkout",
                        "id": "checkout-frame",
                        "name_attribute": "checkout",
                        "title_attribute": "Checkout",
                        "src": "/checkout?token=***",
                        "src_masked": True,
                        "absolute_url": "https://example.test/checkout?token=***",
                        "absolute_url_masked": True,
                        "same_origin": True,
                        "readable": True,
                        "frame_url": "https://example.test/checkout?token=***",
                        "frame_url_masked": True,
                        "frame_title": "Checkout",
                        "body_text": "Pay now",
                        "body_text_length": 7,
                        "body_text_truncated": False,
                    }
                ],
            },
            {
                "kind": "frame_wait",
                "found": True,
                "matched": True,
                "timed_out": False,
                "requested_url": "checkout",
                "url_match": "contains",
                "requested_text": "Pay",
                "text_match": "contains",
                "case_sensitive": False,
                "readable_only": True,
                "same_origin_only": True,
                "frame_count": 1,
                "total_frame_count": 1,
                "frame": {
                    "frame_index": 0,
                    "selector": "iframe.checkout",
                    "tag": "iframe",
                    "name": "Checkout",
                    "id": "checkout-frame",
                    "name_attribute": "checkout",
                    "title_attribute": "Checkout",
                    "src": "/checkout?token=***",
                    "src_masked": True,
                    "absolute_url": "https://example.test/checkout?token=***",
                    "absolute_url_masked": True,
                    "same_origin": True,
                    "readable": True,
                    "frame_url": "https://example.test/checkout?token=***",
                    "frame_url_masked": True,
                    "frame_title": "Checkout",
                    "body_text": "Pay now",
                    "body_text_length": 7,
                    "body_text_truncated": False,
                },
                "frames": [
                    {
                        "frame_index": 0,
                        "selector": "iframe.checkout",
                        "tag": "iframe",
                        "name": "Checkout",
                        "id": "checkout-frame",
                        "name_attribute": "checkout",
                        "title_attribute": "Checkout",
                        "src": "/checkout?token=***",
                        "src_masked": True,
                        "absolute_url": "https://example.test/checkout?token=***",
                        "absolute_url_masked": True,
                        "same_origin": True,
                        "readable": True,
                        "frame_url": "https://example.test/checkout?token=***",
                        "frame_url_masked": True,
                        "frame_title": "Checkout",
                        "body_text": "Pay now",
                        "body_text_length": 7,
                        "body_text_truncated": False,
                    }
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "performance-snapshot",
                "--session-id",
                "s1",
                "--max-resources",
                "5",
                "--initiator-type",
                "fetch",
                "--min-duration-ms",
                "25",
            ],
            "action.performance-snapshot",
            {
                "kind": "performance",
                "requested_initiator_type": "fetch",
                "min_duration_ms": 25,
                "resource_count": 1,
                "node_count": 1,
                "navigation": {
                    "name": "https://example.test/dashboard?token=***",
                    "name_masked": True,
                    "absolute_url": "https://example.test/dashboard?token=***",
                    "absolute_url_masked": True,
                    "type": "navigate",
                    "duration": 120.5,
                    "response_status": 200,
                },
                "resources": [
                    {
                        "index": 0,
                        "name": "https://api.example.test/data?token=***",
                        "name_masked": True,
                        "absolute_url": "https://api.example.test/data?token=***",
                        "absolute_url_masked": True,
                        "initiator_type": "fetch",
                        "duration": 55.5,
                        "transfer_size": 2048,
                        "response_status": 200,
                    }
                ],
            },
            {
                "kind": "performance",
                "requested_initiator_type": "fetch",
                "min_duration_ms": 25,
                "resource_count": 1,
                "node_count": 1,
                "navigation": {
                    "name": "https://example.test/dashboard?token=***",
                    "name_masked": True,
                    "absolute_url": "https://example.test/dashboard?token=***",
                    "absolute_url_masked": True,
                    "type": "navigate",
                    "duration": 120.5,
                    "response_status": 200,
                },
                "resources": [
                    {
                        "index": 0,
                        "name": "https://api.example.test/data?token=***",
                        "name_masked": True,
                        "absolute_url": "https://api.example.test/data?token=***",
                        "absolute_url_masked": True,
                        "initiator_type": "fetch",
                        "duration": 55.5,
                        "transfer_size": 2048,
                        "response_status": 200,
                    }
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "network-snapshot",
                "--session-id",
                "s1",
                "--max-entries",
                "5",
                "--source",
                "fetch",
                "--method",
                "POST",
            ],
            "action.network-snapshot",
            {
                "kind": "network",
                "installed": True,
                "newly_installed": False,
                "entry_count": 1,
                "matched_count": 1,
                "buffered_count": 2,
                "entries": [
                    {
                        "index": 1,
                        "source": "fetch",
                        "method": "POST",
                        "url": "https://api.example.test/save?token=***",
                        "url_masked": True,
                        "absolute_url": "https://api.example.test/save?token=***",
                        "absolute_url_masked": True,
                        "status": 201,
                        "ok": True,
                        "failed": False,
                        "request_has_body": True,
                        "duration_ms": 42.5,
                    }
                ],
            },
            {
                "kind": "network",
                "installed": True,
                "newly_installed": False,
                "entry_count": 1,
                "matched_count": 1,
                "buffered_count": 2,
                "entries": [
                    {
                        "index": 1,
                        "source": "fetch",
                        "method": "POST",
                        "url": "https://api.example.test/save?token=***",
                        "url_masked": True,
                        "absolute_url": "https://api.example.test/save?token=***",
                        "absolute_url_masked": True,
                        "status": 201,
                        "ok": True,
                        "failed": False,
                        "request_has_body": True,
                        "duration_ms": 42.5,
                    }
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-network",
                "--session-id",
                "s1",
                "--url",
                "/save",
                "--source",
                "fetch",
                "--method",
                "POST",
                "--status",
                "201",
                "--after-index",
                "1",
            ],
            "action.wait-network",
            {
                "kind": "network_wait",
                "found": True,
                "matched": True,
                "timed_out": False,
                "requested_url": "/save",
                "url_match": "contains",
                "case_sensitive": False,
                "requested_source": "fetch",
                "requested_method": "POST",
                "requested_status": 201,
                "failed_only": False,
                "after_index": 1,
                "entry_count": 1,
                "buffered_count": 2,
                "entry": {
                    "index": 2,
                    "source": "fetch",
                    "method": "POST",
                    "url": "https://api.example.test/save?token=***",
                    "url_masked": True,
                    "absolute_url": "https://api.example.test/save?token=***",
                    "absolute_url_masked": True,
                    "status": 201,
                    "ok": True,
                    "failed": False,
                    "duration_ms": 42.5,
                },
                "entries": [
                    {
                        "index": 2,
                        "source": "fetch",
                        "method": "POST",
                        "url": "https://api.example.test/save?token=***",
                        "url_masked": True,
                        "absolute_url": "https://api.example.test/save?token=***",
                        "absolute_url_masked": True,
                        "status": 201,
                        "ok": True,
                        "failed": False,
                        "duration_ms": 42.5,
                    }
                ],
            },
            {
                "kind": "network_wait",
                "found": True,
                "matched": True,
                "timed_out": False,
                "requested_url": "/save",
                "url_match": "contains",
                "case_sensitive": False,
                "requested_source": "fetch",
                "requested_method": "POST",
                "requested_status": 201,
                "failed_only": False,
                "after_index": 1,
                "entry_count": 1,
                "buffered_count": 2,
                "entry": {
                    "index": 2,
                    "source": "fetch",
                    "method": "POST",
                    "url": "https://api.example.test/save?token=***",
                    "url_masked": True,
                    "absolute_url": "https://api.example.test/save?token=***",
                    "absolute_url_masked": True,
                    "status": 201,
                    "ok": True,
                    "failed": False,
                    "duration_ms": 42.5,
                },
                "entries": [
                    {
                        "index": 2,
                        "source": "fetch",
                        "method": "POST",
                        "url": "https://api.example.test/save?token=***",
                        "url_masked": True,
                        "absolute_url": "https://api.example.test/save?token=***",
                        "absolute_url_masked": True,
                        "status": 201,
                        "ok": True,
                        "failed": False,
                        "duration_ms": 42.5,
                    }
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "console-snapshot",
                "--session-id",
                "s1",
                "--max-entries",
                "5",
            ],
            "action.console-snapshot",
            {
                "kind": "console",
                "installed": True,
                "newly_installed": False,
                "entry_count": 2,
                "buffered_count": 2,
                "entries": [
                    {
                        "index": 0,
                        "source": "console",
                        "level": "warn",
                        "method": "warn",
                        "text": "token=***",
                        "text_masked": True,
                        "text_truncated": False,
                        "args": [
                            {
                                "type": "string",
                                "text": "token=***",
                                "text_masked": True,
                            }
                        ],
                    },
                    {
                        "index": 1,
                        "source": "pageerror",
                        "level": "error",
                        "method": "error",
                        "text": "Boom",
                        "text_masked": False,
                        "text_truncated": False,
                        "filename": "https://example.test/app.js",
                        "filename_masked": False,
                        "lineno": 12,
                    },
                ],
            },
            {
                "kind": "console",
                "installed": True,
                "newly_installed": False,
                "entry_count": 2,
                "buffered_count": 2,
                "entries": [
                    {
                        "index": 0,
                        "source": "console",
                        "level": "warn",
                        "method": "warn",
                        "text": "token=***",
                        "text_masked": True,
                        "text_truncated": False,
                        "args": [
                            {
                                "type": "string",
                                "text": "token=***",
                                "text_masked": True,
                            }
                        ],
                    },
                    {
                        "index": 1,
                        "source": "pageerror",
                        "level": "error",
                        "method": "error",
                        "text": "Boom",
                        "text_masked": False,
                        "text_truncated": False,
                        "filename": "https://example.test/app.js",
                        "filename_masked": False,
                        "lineno": 12,
                    },
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-console",
                "--session-id",
                "s1",
                "--text",
                "Boom",
                "--source",
                "pageerror",
                "--level",
                "error",
                "--after-index",
                "1",
            ],
            "action.wait-console",
            {
                "kind": "console_wait",
                "found": True,
                "matched": True,
                "timed_out": False,
                "requested_text": "Boom",
                "match": "contains",
                "case_sensitive": False,
                "requested_source": "pageerror",
                "requested_level": "error",
                "after_index": 1,
                "entry_count": 1,
                "buffered_count": 2,
                "entry": {
                    "index": 2,
                    "source": "pageerror",
                    "level": "error",
                    "method": "error",
                    "text": "Boom",
                    "text_masked": False,
                },
                "entries": [
                    {
                        "index": 2,
                        "source": "pageerror",
                        "level": "error",
                        "method": "error",
                        "text": "Boom",
                        "text_masked": False,
                    }
                ],
            },
            {
                "kind": "console_wait",
                "found": True,
                "matched": True,
                "timed_out": False,
                "requested_text": "Boom",
                "match": "contains",
                "case_sensitive": False,
                "requested_source": "pageerror",
                "requested_level": "error",
                "after_index": 1,
                "entry_count": 1,
                "buffered_count": 2,
                "entry": {
                    "index": 2,
                    "source": "pageerror",
                    "level": "error",
                    "method": "error",
                    "text": "Boom",
                    "text_masked": False,
                },
                "entries": [
                    {
                        "index": 2,
                        "source": "pageerror",
                        "level": "error",
                        "method": "error",
                        "text": "Boom",
                        "text_masked": False,
                    }
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "outline-snapshot",
                "--session-id",
                "s1",
                "--selector",
                "main",
                "--max-nodes",
                "5",
            ],
            "action.outline-snapshot",
            {
                "kind": "outline",
                "selector": "main",
                "node_count": 2,
                "heading_count": 1,
                "landmark_count": 1,
                "headings": [
                    {
                        "index": 0,
                        "node_type": "heading",
                        "selector": "h1",
                        "tag": "h1",
                        "role": "heading",
                        "level": 1,
                        "name": "Dashboard",
                        "text": "Dashboard",
                        "visible": True,
                    }
                ],
                "landmarks": [
                    {
                        "index": 1,
                        "node_type": "landmark",
                        "selector": "nav",
                        "tag": "nav",
                        "role": "navigation",
                        "level": None,
                        "name": "Primary",
                        "text": "Primary",
                        "visible": True,
                    }
                ],
            },
            {
                "kind": "outline",
                "selector": "main",
                "node_count": 2,
                "heading_count": 1,
                "landmark_count": 1,
                "headings": [
                    {
                        "index": 0,
                        "node_type": "heading",
                        "selector": "h1",
                        "tag": "h1",
                        "role": "heading",
                        "level": 1,
                        "name": "Dashboard",
                        "text": "Dashboard",
                        "visible": True,
                    }
                ],
                "landmarks": [
                    {
                        "index": 1,
                        "node_type": "landmark",
                        "selector": "nav",
                        "tag": "nav",
                        "role": "navigation",
                        "level": None,
                        "name": "Primary",
                        "text": "Primary",
                        "visible": True,
                    }
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "form-snapshot",
                "--session-id",
                "s1",
                "--selector",
                "form",
                "--max-nodes",
                "2",
            ],
            "action.form-snapshot",
            {
                "kind": "form",
                "selector": "form",
                "node_count": 2,
                "field_count": 3,
                "fields": [
                    {
                        "selector": "#email",
                        "tag": "input",
                        "type": "email",
                        "name": "Email",
                        "name_attribute": "email",
                        "labels": ["Email"],
                        "value": "user@example.test",
                        "value_masked": False,
                    },
                    {
                        "selector": "#password",
                        "tag": "input",
                        "type": "password",
                        "name": "Password",
                        "name_attribute": "password",
                        "labels": ["Password"],
                        "value": "***",
                        "value_masked": True,
                        "value_length": 12,
                    },
                ],
            },
            {
                "kind": "form",
                "selector": "form",
                "node_count": 2,
                "field_count": 3,
                "fields": [
                    {
                        "selector": "#email",
                        "tag": "input",
                        "type": "email",
                        "name": "Email",
                        "name_attribute": "email",
                        "labels": ["Email"],
                        "value": "user@example.test",
                        "value_masked": False,
                    },
                    {
                        "selector": "#password",
                        "tag": "input",
                        "type": "password",
                        "name": "Password",
                        "name_attribute": "password",
                        "labels": ["Password"],
                        "value": "***",
                        "value_masked": True,
                        "value_length": 12,
                    },
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "interactive-snapshot",
                "--session-id",
                "s1",
                "--include-hidden",
            ],
            "action.interactive-snapshot",
            {"kind": "interactive", "node_count": 1, "nodes": []},
            {
                "kind": "interactive",
                "node_count": 1,
                "nodes": [],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "interactive-only-snapshot",
                "--session-id",
                "s1",
                "--include-hidden",
            ],
            "action.interactive-only-snapshot",
            {"kind": "interactive", "node_count": 1, "nodes": []},
            {
                "kind": "interactive",
                "node_count": 1,
                "nodes": [],
                "url": "https://example.test",
            },
        ),
    ],
)
def test_second_batch_eval_backed_action_commands_emit_structured_results(
    argv: list[str],
    command: str,
    value: dict[str, Any],
    expected_result: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}
    connect_url = "wss://api.lexmount.cn/connection?project_id=project&api_key=secret"

    def fake_resolve(target: Any) -> str:
        assert target.session_id == "s1"
        return connect_url

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed.update(
            {
                "connect_url": connect_url,
                "action": action,
                "expression": request.expression,
            }
        )
        return SimpleNamespace(
            result={
                "url": "https://example.test",
                "value": value,
            }
        )

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url", fake_resolve
    )
    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(argv)

    assert exc_info.value.code == 0
    assert observed["connect_url"] == connect_url
    assert observed["action"] == "eval"
    assert observed["expression"].startswith("() =>")
    if command == "action.wait-role":
        assert "new Promise" in observed["expression"]
        assert "requestedRole" in observed["expression"]
        assert '"button"' in observed["expression"]
        assert '"Save"' in observed["expression"]
        assert "includeHidden" in observed["expression"]
        assert "timeoutMs" in observed["expression"]
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": command,
        "session_id": "s1",
        "connect_url": (
            "wss://api.lexmount.cn/connection?project_id=project&api_key=***"
        ),
        "connect_url_masked": True,
        "result": expected_result,
    }


@pytest.mark.parametrize(
    ("argv", "command", "value", "expected_result"),
    [
        (
            [
                "action",
                "count",
                "--session-id",
                "s1",
                "--selector",
                ".item",
            ],
            "action.count",
            {
                "selector": ".item",
                "include_hidden": False,
                "count": 2,
                "total_count": 3,
                "visible_count": 2,
            },
            {
                "selector": ".item",
                "include_hidden": False,
                "count": 2,
                "total_count": 3,
                "visible_count": 2,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-count",
                "--session-id",
                "s1",
                "--selector",
                ".item",
                "--count",
                "3",
                "--comparison",
                "gte",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
            ],
            "action.wait-count",
            {
                "selector": ".item",
                "found": True,
                "count": 3,
                "requested_count": 3,
                "comparison": "gte",
                "include_hidden": False,
                "total_count": 3,
                "visible_count": 3,
                "waited_ms": 50,
            },
            {
                "selector": ".item",
                "found": True,
                "count": 3,
                "requested_count": 3,
                "comparison": "gte",
                "include_hidden": False,
                "total_count": 3,
                "visible_count": 3,
                "waited_ms": 50,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "query",
                "--session-id",
                "s1",
                "--selector",
                ".item",
                "--max-nodes",
                "1",
                "--include-hidden",
            ],
            "action.query",
            {
                "selector": ".item",
                "kind": "query",
                "count": 1,
                "node_count": 1,
                "nodes": [{"selector": ".item", "text": "A"}],
                "truncated": False,
            },
            {
                "selector": ".item",
                "kind": "query",
                "count": 1,
                "node_count": 1,
                "nodes": [{"selector": ".item", "text": "A"}],
                "truncated": False,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "get-attribute",
                "--session-id",
                "s1",
                "--selector",
                "a",
                "--name",
                "href",
            ],
            "action.get-attribute",
            {
                "selector": "a",
                "found": True,
                "name": "href",
                "value": "/docs",
                "attribute_value": "/docs",
                "property_value": "https://example.test/docs",
            },
            {
                "selector": "a",
                "found": True,
                "name": "href",
                "value": "/docs",
                "attribute_value": "/docs",
                "property_value": "https://example.test/docs",
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-attribute",
                "--session-id",
                "s1",
                "--selector",
                "button",
                "--name",
                "aria-busy",
                "--state",
                "absent",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
            ],
            "action.wait-attribute",
            {
                "selector": "button",
                "name": "aria-busy",
                "found": True,
                "state": "absent",
                "selector_found": True,
                "attribute_found": False,
                "value": None,
                "requested_value": None,
                "match": "contains",
                "waited_ms": 50,
            },
            {
                "selector": "button",
                "name": "aria-busy",
                "found": True,
                "state": "absent",
                "selector_found": True,
                "attribute_found": False,
                "value": None,
                "requested_value": None,
                "match": "contains",
                "waited_ms": 50,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "get-attribute-role",
                "--session-id",
                "s1",
                "--role",
                "button",
                "--name",
                "Menu",
                "--attribute",
                "aria-expanded",
            ],
            "action.get-attribute-role",
            {
                "role": "button",
                "name": "Menu",
                "attribute": "aria-expanded",
                "found": True,
                "role_found": True,
                "value": "false",
                "attribute_value": "false",
                "property_value": None,
                "include_hidden": False,
                "candidate_count": 1,
            },
            {
                "role": "button",
                "name": "Menu",
                "attribute": "aria-expanded",
                "found": True,
                "role_found": True,
                "value": "false",
                "attribute_value": "false",
                "property_value": None,
                "include_hidden": False,
                "candidate_count": 1,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-attribute-role",
                "--session-id",
                "s1",
                "--role",
                "button",
                "--name",
                "Menu",
                "--attribute",
                "aria-expanded",
                "--value",
                "true",
                "--match",
                "exact",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
            ],
            "action.wait-attribute-role",
            {
                "role": "button",
                "name": "Menu",
                "attribute": "aria-expanded",
                "found": True,
                "state": "present",
                "role_found": True,
                "attribute_found": True,
                "value": "true",
                "requested_value": "true",
                "match": "exact",
                "include_hidden": False,
                "waited_ms": 50,
                "candidate_count": 1,
            },
            {
                "role": "button",
                "name": "Menu",
                "attribute": "aria-expanded",
                "found": True,
                "state": "present",
                "role_found": True,
                "attribute_found": True,
                "value": "true",
                "requested_value": "true",
                "match": "exact",
                "include_hidden": False,
                "waited_ms": 50,
                "candidate_count": 1,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-state",
                "--session-id",
                "s1",
                "--selector",
                "button[type=submit]",
                "--state",
                "enabled",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
            ],
            "action.wait-state",
            {
                "selector": "button[type=submit]",
                "state": "enabled",
                "found": True,
                "matched": True,
                "waited_ms": 50,
                "timeout_ms": 1000,
                "poll_ms": 50,
                "state_values": {
                    "attached": True,
                    "detached": False,
                    "visible": True,
                    "hidden": False,
                    "enabled": True,
                    "disabled": False,
                    "editable": False,
                    "readonly": False,
                    "checked": None,
                    "unchecked": None,
                    "focused": False,
                    "in_viewport": True,
                    "out_of_viewport": False,
                },
            },
            {
                "selector": "button[type=submit]",
                "state": "enabled",
                "found": True,
                "matched": True,
                "waited_ms": 50,
                "timeout_ms": 1000,
                "poll_ms": 50,
                "state_values": {
                    "attached": True,
                    "detached": False,
                    "visible": True,
                    "hidden": False,
                    "enabled": True,
                    "disabled": False,
                    "editable": False,
                    "readonly": False,
                    "checked": None,
                    "unchecked": None,
                    "focused": False,
                    "in_viewport": True,
                    "out_of_viewport": False,
                },
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-state-role",
                "--session-id",
                "s1",
                "--role",
                "button",
                "--name",
                "Submit",
                "--state",
                "enabled",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
            ],
            "action.wait-state-role",
            {
                "role": "button",
                "name": "Submit",
                "state": "enabled",
                "found": True,
                "role_found": True,
                "matched": True,
                "include_hidden": False,
                "waited_ms": 50,
                "timeout_ms": 1000,
                "poll_ms": 50,
                "candidate_count": 1,
                "state_values": {
                    "attached": True,
                    "detached": False,
                    "visible": True,
                    "hidden": False,
                    "enabled": True,
                    "disabled": False,
                    "editable": False,
                    "readonly": False,
                    "checked": None,
                    "unchecked": None,
                    "focused": False,
                    "in_viewport": True,
                    "out_of_viewport": False,
                },
            },
            {
                "role": "button",
                "name": "Submit",
                "state": "enabled",
                "found": True,
                "role_found": True,
                "matched": True,
                "include_hidden": False,
                "waited_ms": 50,
                "timeout_ms": 1000,
                "poll_ms": 50,
                "candidate_count": 1,
                "state_values": {
                    "attached": True,
                    "detached": False,
                    "visible": True,
                    "hidden": False,
                    "enabled": True,
                    "disabled": False,
                    "editable": False,
                    "readonly": False,
                    "checked": None,
                    "unchecked": None,
                    "focused": False,
                    "in_viewport": True,
                    "out_of_viewport": False,
                },
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-text",
                "--session-id",
                "s1",
                "--selector",
                "main",
                "--text",
                "Ready",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
            ],
            "action.wait-text",
            {
                "selector": "main",
                "found": True,
                "text": "Ready",
                "waited_ms": 50,
                "candidate_count": 1,
            },
            {
                "selector": "main",
                "found": True,
                "text": "Ready",
                "waited_ms": 50,
                "candidate_count": 1,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-text",
                "--session-id",
                "s1",
                "--selector",
                "main",
                "--text",
                "Loading",
                "--state",
                "absent",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
            ],
            "action.wait-text",
            {
                "selector": "main",
                "found": False,
                "matched": False,
                "state": "absent",
                "text": "Loading",
                "waited_ms": 50,
                "candidate_count": 1,
                "element": None,
            },
            {
                "selector": "main",
                "found": False,
                "matched": False,
                "state": "absent",
                "text": "Loading",
                "waited_ms": 50,
                "candidate_count": 1,
                "element": None,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-role",
                "--session-id",
                "s1",
                "--role",
                "button",
                "--name",
                "Save",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
            ],
            "action.wait-role",
            {
                "found": True,
                "role": "button",
                "name": "Save",
                "waited_ms": 50,
                "timeout_ms": 1000,
                "poll_ms": 50,
                "candidate_count": 1,
                "total_candidate_count": 4,
            },
            {
                "found": True,
                "role": "button",
                "name": "Save",
                "waited_ms": 50,
                "timeout_ms": 1000,
                "poll_ms": 50,
                "candidate_count": 1,
                "total_candidate_count": 4,
                "url": "https://example.test",
            },
        ),
    ],
)
def test_third_batch_eval_backed_action_commands_emit_structured_results(
    argv: list[str],
    command: str,
    value: dict[str, Any],
    expected_result: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}
    connect_url = "wss://api.lexmount.cn/connection?project_id=project&api_key=secret"

    def fake_resolve(target: Any) -> str:
        assert target.session_id == "s1"
        return connect_url

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed.update(
            {
                "connect_url": connect_url,
                "action": action,
                "expression": request.expression,
            }
        )
        return SimpleNamespace(result={"url": "https://example.test", "value": value})

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url", fake_resolve
    )
    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(argv)

    assert exc_info.value.code == 0
    assert observed["connect_url"] == connect_url
    assert observed["action"] == "eval"
    assert observed["expression"].startswith("() =>")
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": command,
        "session_id": "s1",
        "connect_url": (
            "wss://api.lexmount.cn/connection?project_id=project&api_key=***"
        ),
        "connect_url_masked": True,
        "result": expected_result,
    }


@pytest.mark.parametrize(
    ("argv", "command", "value", "expected_result"),
    [
        (
            ["action", "reload", "--session-id", "s1"],
            "action.reload",
            {
                "action": "reload",
                "navigation_requested": True,
                "reloaded": True,
                "before_url": "https://example.test",
            },
            {
                "action": "reload",
                "navigation_requested": True,
                "reloaded": True,
                "before_url": "https://example.test",
                "url": "https://example.test",
            },
        ),
        (
            ["action", "go-back", "--session-id", "s1"],
            "action.go-back",
            {
                "action": "back",
                "navigation_requested": True,
                "before_url": "https://example.test/page2",
                "history_length": 2,
            },
            {
                "action": "back",
                "navigation_requested": True,
                "before_url": "https://example.test/page2",
                "history_length": 2,
                "url": "https://example.test",
            },
        ),
        (
            ["action", "go-forward", "--session-id", "s1"],
            "action.go-forward",
            {
                "action": "forward",
                "navigation_requested": True,
                "before_url": "https://example.test/page1",
                "history_length": 2,
            },
            {
                "action": "forward",
                "navigation_requested": True,
                "before_url": "https://example.test/page1",
                "history_length": 2,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-url",
                "--session-id",
                "s1",
                "--url",
                "/dashboard",
                "--match",
                "contains",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
            ],
            "action.wait-url",
            {
                "found": True,
                "requested_url": "/dashboard",
                "match": "contains",
                "waited_ms": 50,
            },
            {
                "found": True,
                "requested_url": "/dashboard",
                "match": "contains",
                "waited_ms": 50,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-title",
                "--session-id",
                "s1",
                "--title",
                "Dashboard",
                "--match",
                "exact",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
                "--case-sensitive",
            ],
            "action.wait-title",
            {
                "found": True,
                "title": "Dashboard",
                "requested_title": "Dashboard",
                "match": "exact",
                "case_sensitive": True,
                "waited_ms": 50,
            },
            {
                "found": True,
                "title": "Dashboard",
                "requested_title": "Dashboard",
                "match": "exact",
                "case_sensitive": True,
                "waited_ms": 50,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-load-state",
                "--session-id",
                "s1",
                "--state",
                "complete",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
            ],
            "action.wait-load-state",
            {
                "found": True,
                "state": "complete",
                "requested_state": "complete",
                "target_state": "complete",
                "waited_ms": 50,
            },
            {
                "found": True,
                "state": "complete",
                "requested_state": "complete",
                "target_state": "complete",
                "waited_ms": 50,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-network-idle",
                "--session-id",
                "s1",
                "--idle-ms",
                "500",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
                "--max-inflight",
                "0",
            ],
            "action.wait-network-idle",
            {
                "found": True,
                "network_idle": True,
                "idle_ms": 500,
                "quiet_ms": 500,
                "waited_ms": 550,
                "pending_requests": 0,
                "max_inflight": 0,
                "observed_request_count": 1,
                "observed_response_count": 1,
                "observed_failure_count": 0,
                "observed_resource_count": 2,
                "observer_available": True,
                "fetch_instrumented": True,
                "xhr_instrumented": True,
            },
            {
                "found": True,
                "network_idle": True,
                "idle_ms": 500,
                "quiet_ms": 500,
                "waited_ms": 550,
                "pending_requests": 0,
                "max_inflight": 0,
                "observed_request_count": 1,
                "observed_response_count": 1,
                "observed_failure_count": 0,
                "observed_resource_count": 2,
                "observer_available": True,
                "fetch_instrumented": True,
                "xhr_instrumented": True,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "focus",
                "--session-id",
                "s1",
                "--selector",
                "input[name=q]",
                "--prevent-scroll",
            ],
            "action.focus",
            {
                "selector": "input[name=q]",
                "found": True,
                "focused": True,
                "prevent_scroll": True,
            },
            {
                "selector": "input[name=q]",
                "found": True,
                "focused": True,
                "prevent_scroll": True,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "focus-role",
                "--session-id",
                "s1",
                "--role",
                "textbox",
                "--name",
                "Search",
                "--prevent-scroll",
            ],
            "action.focus-role",
            {
                "role": "textbox",
                "name": "Search",
                "found": True,
                "role_found": True,
                "focused": True,
                "prevent_scroll": True,
                "candidate_count": 1,
            },
            {
                "role": "textbox",
                "name": "Search",
                "found": True,
                "role_found": True,
                "focused": True,
                "prevent_scroll": True,
                "candidate_count": 1,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "get-value",
                "--session-id",
                "s1",
                "--selector",
                "input[name=q]",
            ],
            "action.get-value",
            {
                "selector": "input[name=q]",
                "found": True,
                "readable": True,
                "value": "query",
                "value_type": "value",
            },
            {
                "selector": "input[name=q]",
                "found": True,
                "readable": True,
                "value": "query",
                "value_type": "value",
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "get-value-role",
                "--session-id",
                "s1",
                "--role",
                "textbox",
                "--name",
                "Search",
            ],
            "action.get-value-role",
            {
                "role": "textbox",
                "name": "Search",
                "found": True,
                "role_found": True,
                "readable": True,
                "value": "query",
                "value_type": "value",
            },
            {
                "role": "textbox",
                "name": "Search",
                "found": True,
                "role_found": True,
                "readable": True,
                "value": "query",
                "value_type": "value",
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-value",
                "--session-id",
                "s1",
                "--selector",
                "input[name=q]",
                "--value",
                "query",
                "--match",
                "exact",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
                "--case-sensitive",
            ],
            "action.wait-value",
            {
                "selector": "input[name=q]",
                "found": True,
                "selector_found": True,
                "readable": True,
                "value": "query",
                "value_type": "value",
                "requested_value": "query",
                "match": "exact",
                "waited_ms": 50,
            },
            {
                "selector": "input[name=q]",
                "found": True,
                "selector_found": True,
                "readable": True,
                "value": "query",
                "value_type": "value",
                "requested_value": "query",
                "match": "exact",
                "waited_ms": 50,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-value-role",
                "--session-id",
                "s1",
                "--role",
                "textbox",
                "--name",
                "Search",
                "--value",
                "query",
                "--match",
                "exact",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
                "--case-sensitive",
            ],
            "action.wait-value-role",
            {
                "role": "textbox",
                "name": "Search",
                "found": True,
                "role_found": True,
                "readable": True,
                "value": "query",
                "value_type": "value",
                "requested_value": "query",
                "match": "exact",
                "waited_ms": 50,
            },
            {
                "role": "textbox",
                "name": "Search",
                "found": True,
                "role_found": True,
                "readable": True,
                "value": "query",
                "value_type": "value",
                "requested_value": "query",
                "match": "exact",
                "waited_ms": 50,
                "url": "https://example.test",
            },
        ),
        (
            ["action", "blur", "--session-id", "s1", "--selector", "input[name=q]"],
            "action.blur",
            {
                "selector": "input[name=q]",
                "found": True,
                "blurred": True,
                "was_focused": True,
                "focused": False,
            },
            {
                "selector": "input[name=q]",
                "found": True,
                "blurred": True,
                "was_focused": True,
                "focused": False,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "blur-role",
                "--session-id",
                "s1",
                "--role",
                "textbox",
                "--name",
                "Search",
            ],
            "action.blur-role",
            {
                "role": "textbox",
                "name": "Search",
                "found": True,
                "role_found": True,
                "blurred": True,
                "was_focused": True,
                "focused": False,
                "candidate_count": 1,
            },
            {
                "role": "textbox",
                "name": "Search",
                "found": True,
                "role_found": True,
                "blurred": True,
                "was_focused": True,
                "focused": False,
                "candidate_count": 1,
                "url": "https://example.test",
            },
        ),
        (
            ["action", "clear", "--session-id", "s1", "--selector", "input[name=q]"],
            "action.clear",
            {
                "selector": "input[name=q]",
                "found": True,
                "clearable": True,
                "cleared": True,
                "previous_value": "query",
                "value": "",
            },
            {
                "selector": "input[name=q]",
                "found": True,
                "clearable": True,
                "cleared": True,
                "previous_value": "query",
                "value": "",
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "clear-role",
                "--session-id",
                "s1",
                "--role",
                "textbox",
                "--name",
                "Search",
            ],
            "action.clear-role",
            {
                "role": "textbox",
                "name": "Search",
                "found": True,
                "role_found": True,
                "clearable": True,
                "cleared": True,
                "previous_value": "query",
                "value": "",
                "candidate_count": 1,
            },
            {
                "role": "textbox",
                "name": "Search",
                "found": True,
                "role_found": True,
                "clearable": True,
                "cleared": True,
                "previous_value": "query",
                "value": "",
                "candidate_count": 1,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "submit",
                "--session-id",
                "s1",
                "--selector",
                "form",
                "--skip-validation",
            ],
            "action.submit",
            {
                "selector": "form",
                "found": True,
                "form_found": True,
                "submitted": True,
                "skip_validation": True,
                "used_request_submit": False,
            },
            {
                "selector": "form",
                "found": True,
                "form_found": True,
                "submitted": True,
                "skip_validation": True,
                "used_request_submit": False,
                "url": "https://example.test",
            },
        ),
    ],
)
def test_navigation_and_form_eval_backed_action_commands_emit_structured_results(
    argv: list[str],
    command: str,
    value: dict[str, Any],
    expected_result: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}
    connect_url = "wss://api.lexmount.cn/connection?project_id=project&api_key=secret"

    def fake_resolve(target: Any) -> str:
        assert target.session_id == "s1"
        return connect_url

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed.update(
            {
                "connect_url": connect_url,
                "action": action,
                "expression": request.expression,
            }
        )
        return SimpleNamespace(result={"url": "https://example.test", "value": value})

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url", fake_resolve
    )
    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(argv)

    assert exc_info.value.code == 0
    assert observed["connect_url"] == connect_url
    assert observed["action"] == "eval"
    assert observed["expression"].startswith("() =>")
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": command,
        "session_id": "s1",
        "connect_url": (
            "wss://api.lexmount.cn/connection?project_id=project&api_key=***"
        ),
        "connect_url_masked": True,
        "result": expected_result,
    }


@pytest.mark.parametrize(
    ("argv", "command", "value", "expected_result"),
    [
        (
            [
                "action",
                "storage-get",
                "--session-id",
                "s1",
                "--area",
                "local",
                "--key",
                "featureFlag",
            ],
            "action.storage-get",
            {
                "area": "local",
                "key": "featureFlag",
                "found": True,
                "value": "enabled",
                "value_length": 7,
            },
            {
                "area": "local",
                "key": "featureFlag",
                "found": True,
                "value": "enabled",
                "value_length": 7,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "storage-get",
                "--session-id",
                "s1",
                "--area",
                "session",
                "--prefix",
                "auth:",
                "--max-items",
                "20",
            ],
            "action.storage-get",
            {
                "area": "session",
                "key": None,
                "prefix": "auth:",
                "found": True,
                "count": 1,
                "item_count": 1,
                "max_items": 20,
                "truncated": False,
                "items": [
                    {
                        "key": "auth:mode",
                        "value": "test",
                        "value_length": 4,
                    }
                ],
            },
            {
                "area": "session",
                "key": None,
                "prefix": "auth:",
                "found": True,
                "count": 1,
                "item_count": 1,
                "max_items": 20,
                "truncated": False,
                "items": [
                    {
                        "key": "auth:mode",
                        "value": "test",
                        "value_length": 4,
                    }
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "storage-set",
                "--session-id",
                "s1",
                "--area",
                "local",
                "--key",
                "seenIntro",
                "--value",
                "true",
            ],
            "action.storage-set",
            {
                "area": "local",
                "key": "seenIntro",
                "set": True,
                "found": True,
                "previous_value": None,
                "value": "true",
                "value_length": 4,
            },
            {
                "area": "local",
                "key": "seenIntro",
                "set": True,
                "found": True,
                "previous_value": None,
                "value": "true",
                "value_length": 4,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "storage-remove",
                "--session-id",
                "s1",
                "--area",
                "session",
                "--key",
                "draft",
            ],
            "action.storage-remove",
            {
                "area": "session",
                "key": "draft",
                "removed": True,
                "had_key": True,
                "found": True,
                "previous_value": "hello",
            },
            {
                "area": "session",
                "key": "draft",
                "removed": True,
                "had_key": True,
                "found": True,
                "previous_value": "hello",
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "storage-clear",
                "--session-id",
                "s1",
                "--area",
                "session",
                "--prefix",
                "temp:",
            ],
            "action.storage-clear",
            {
                "area": "session",
                "prefix": "temp:",
                "cleared": True,
                "cleared_count": 2,
                "keys": ["temp:a", "temp:b"],
            },
            {
                "area": "session",
                "prefix": "temp:",
                "cleared": True,
                "cleared_count": 2,
                "keys": ["temp:a", "temp:b"],
                "url": "https://example.test",
            },
        ),
    ],
)
def test_storage_eval_backed_action_commands_emit_structured_results(
    argv: list[str],
    command: str,
    value: dict[str, Any],
    expected_result: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}
    connect_url = "wss://api.lexmount.cn/connection?project_id=project&api_key=secret"

    def fake_resolve(target: Any) -> str:
        assert target.session_id == "s1"
        return connect_url

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed.update(
            {
                "connect_url": connect_url,
                "action": action,
                "expression": request.expression,
            }
        )
        return SimpleNamespace(result={"url": "https://example.test", "value": value})

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url", fake_resolve
    )
    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(argv)

    assert exc_info.value.code == 0
    assert observed["connect_url"] == connect_url
    assert observed["action"] == "eval"
    assert observed["expression"].startswith("() =>")
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": command,
        "session_id": "s1",
        "connect_url": (
            "wss://api.lexmount.cn/connection?project_id=project&api_key=***"
        ),
        "connect_url_masked": True,
        "result": expected_result,
    }


@pytest.mark.parametrize(
    ("argv", "command", "value", "expected_result"),
    [
        (
            [
                "action",
                "cookie-get",
                "--session-id",
                "s1",
                "--name",
                "consent",
            ],
            "action.cookie-get",
            {
                "document_cookie_scope": "document.cookie",
                "name": "consent",
                "found": True,
                "value": "yes",
                "raw_value": "yes",
                "value_length": 3,
            },
            {
                "document_cookie_scope": "document.cookie",
                "name": "consent",
                "found": True,
                "value": "yes",
                "raw_value": "yes",
                "value_length": 3,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "cookie-get",
                "--session-id",
                "s1",
                "--prefix",
                "tmp:",
                "--max-items",
                "10",
            ],
            "action.cookie-get",
            {
                "document_cookie_scope": "document.cookie",
                "name": None,
                "prefix": "tmp:",
                "found": True,
                "count": 1,
                "item_count": 1,
                "max_items": 10,
                "truncated": False,
                "items": [
                    {
                        "name": "tmp:flag",
                        "value": "on",
                        "raw_name": "tmp%3Aflag",
                        "raw_value": "on",
                        "value_length": 2,
                    }
                ],
            },
            {
                "document_cookie_scope": "document.cookie",
                "name": None,
                "prefix": "tmp:",
                "found": True,
                "count": 1,
                "item_count": 1,
                "max_items": 10,
                "truncated": False,
                "items": [
                    {
                        "name": "tmp:flag",
                        "value": "on",
                        "raw_name": "tmp%3Aflag",
                        "raw_value": "on",
                        "value_length": 2,
                    }
                ],
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "cookie-set",
                "--session-id",
                "s1",
                "--name",
                "consent",
                "--value",
                "yes",
                "--path",
                "/",
                "--same-site",
                "lax",
            ],
            "action.cookie-set",
            {
                "document_cookie_scope": "document.cookie",
                "name": "consent",
                "set": True,
                "found": True,
                "previous_value": None,
                "value": "yes",
                "value_length": 3,
                "path": "/",
                "domain": None,
                "max_age": None,
                "expires": None,
                "same_site": "lax",
                "secure": False,
            },
            {
                "document_cookie_scope": "document.cookie",
                "name": "consent",
                "set": True,
                "found": True,
                "previous_value": None,
                "value": "yes",
                "value_length": 3,
                "path": "/",
                "domain": None,
                "max_age": None,
                "expires": None,
                "same_site": "lax",
                "secure": False,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "cookie-delete",
                "--session-id",
                "s1",
                "--name",
                "consent",
                "--path",
                "/",
            ],
            "action.cookie-delete",
            {
                "document_cookie_scope": "document.cookie",
                "name": "consent",
                "deleted": True,
                "had_cookie": True,
                "found": True,
                "previous_value": "yes",
                "path": "/",
                "domain": None,
            },
            {
                "document_cookie_scope": "document.cookie",
                "name": "consent",
                "deleted": True,
                "had_cookie": True,
                "found": True,
                "previous_value": "yes",
                "path": "/",
                "domain": None,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "cookie-clear",
                "--session-id",
                "s1",
                "--prefix",
                "tmp:",
                "--path",
                "/",
            ],
            "action.cookie-clear",
            {
                "document_cookie_scope": "document.cookie",
                "prefix": "tmp:",
                "path": "/",
                "domain": None,
                "cleared": True,
                "cleared_count": 2,
                "matched_count": 2,
                "names": ["tmp:a", "tmp:b"],
                "remaining_count": 0,
            },
            {
                "document_cookie_scope": "document.cookie",
                "prefix": "tmp:",
                "path": "/",
                "domain": None,
                "cleared": True,
                "cleared_count": 2,
                "matched_count": 2,
                "names": ["tmp:a", "tmp:b"],
                "remaining_count": 0,
                "url": "https://example.test",
            },
        ),
    ],
)
def test_cookie_eval_backed_action_commands_emit_structured_results(
    argv: list[str],
    command: str,
    value: dict[str, Any],
    expected_result: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}
    connect_url = "wss://api.lexmount.cn/connection?project_id=project&api_key=secret"

    def fake_resolve(target: Any) -> str:
        assert target.session_id == "s1"
        return connect_url

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed.update(
            {
                "connect_url": connect_url,
                "action": action,
                "expression": request.expression,
            }
        )
        return SimpleNamespace(result={"url": "https://example.test", "value": value})

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url", fake_resolve
    )
    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(argv)

    assert exc_info.value.code == 0
    assert observed["connect_url"] == connect_url
    assert observed["action"] == "eval"
    assert observed["expression"].startswith("() =>")
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": command,
        "session_id": "s1",
        "connect_url": (
            "wss://api.lexmount.cn/connection?project_id=project&api_key=***"
        ),
        "connect_url_masked": True,
        "result": expected_result,
    }


@pytest.mark.parametrize(
    ("argv", "command", "value", "expected_result"),
    [
        (
            [
                "action",
                "wait-storage",
                "--session-id",
                "s1",
                "--area",
                "local",
                "--key",
                "authToken",
                "--value",
                "ready",
                "--match",
                "exact",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
                "--case-sensitive",
            ],
            "action.wait-storage",
            {
                "area": "local",
                "key": "authToken",
                "found": True,
                "state": "present",
                "exists": True,
                "value": "ready",
                "requested_value": "ready",
                "match": "exact",
                "waited_ms": 50,
            },
            {
                "area": "local",
                "key": "authToken",
                "found": True,
                "state": "present",
                "exists": True,
                "value": "ready",
                "requested_value": "ready",
                "match": "exact",
                "waited_ms": 50,
                "url": "https://example.test",
            },
        ),
        (
            [
                "action",
                "wait-cookie",
                "--session-id",
                "s1",
                "--name",
                "consent",
                "--state",
                "absent",
                "--timeout-ms",
                "1000",
                "--poll-ms",
                "50",
            ],
            "action.wait-cookie",
            {
                "document_cookie_scope": "document.cookie",
                "name": "consent",
                "found": True,
                "state": "absent",
                "exists": False,
                "value": None,
                "raw_value": None,
                "requested_value": None,
                "match": "contains",
                "waited_ms": 50,
            },
            {
                "document_cookie_scope": "document.cookie",
                "name": "consent",
                "found": True,
                "state": "absent",
                "exists": False,
                "value": None,
                "raw_value": None,
                "requested_value": None,
                "match": "contains",
                "waited_ms": 50,
                "url": "https://example.test",
            },
        ),
    ],
)
def test_state_wait_eval_backed_action_commands_emit_structured_results(
    argv: list[str],
    command: str,
    value: dict[str, Any],
    expected_result: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observed: dict[str, Any] = {}
    connect_url = "wss://api.lexmount.cn/connection?project_id=project&api_key=secret"

    def fake_resolve(target: Any) -> str:
        assert target.session_id == "s1"
        return connect_url

    def fake_run_browser_action(
        *,
        connect_url: str,
        action: str,
        request: Any,
    ) -> SimpleNamespace:
        observed.update(
            {
                "connect_url": connect_url,
                "action": action,
                "expression": request.expression,
            }
        )
        return SimpleNamespace(result={"url": "https://example.test", "value": value})

    monkeypatch.setattr(
        "browser_cli.cli.resolve_browser_action_connect_url", fake_resolve
    )
    monkeypatch.setattr("browser_cli.cli.run_browser_action", fake_run_browser_action)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(argv)

    assert exc_info.value.code == 0
    assert observed["connect_url"] == connect_url
    assert observed["action"] == "eval"
    assert observed["expression"].startswith("() =>")
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": command,
        "session_id": "s1",
        "connect_url": (
            "wss://api.lexmount.cn/connection?project_id=project&api_key=***"
        ),
        "connect_url_masked": True,
        "result": expected_result,
    }


def test_direct_url_can_reveal_secret_explicitly(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "key")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["direct-url", "--reveal-url"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "command": "direct-url",
        "mode": "direct",
        "connect_url": "wss://api.lexmount.cn/connection?project_id=project&api_key=key",
        "masked": False,
    }
