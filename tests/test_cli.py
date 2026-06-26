from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from browser_cli.cli import main as cli_main


class DummyModel:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return self.payload


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


def test_auth_status_masks_secret_by_default(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "abcd1234wxyz")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.delenv("LEXMOUNT_REGION", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "status"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "abcd1234wxyz" not in output
    payload = json.loads(output)
    assert payload["ok"] is True
    assert payload["command"] == "auth.status"
    assert payload["configured"] is True
    assert payload["missing"] == []
    assert payload["decision"] == {
        "action": "verify_access",
        "reason": "credentials_configured",
        "can_attempt_api": True,
        "can_start_browser_work": True,
        "should_open_browser": False,
        "missing": [],
        "next_command": "browser-cli session list",
        "fallback_command": "browser-cli doctor --json",
    }
    assert payload["environment"]["LEXMOUNT_API_KEY"] == {
        "set": True,
        "value": "abcd...wxyz",
        "masked": True,
        "default": False,
    }
    assert payload["environment"]["LEXMOUNT_PROJECT_ID"] == {
        "set": True,
        "value": "project",
        "masked": False,
        "default": False,
    }
    assert payload["environment"]["LEXMOUNT_BASE_URL"] == {
        "set": False,
        "value": "https://api.lexmount.cn",
        "masked": False,
        "default": True,
    }


def test_auth_status_reports_missing_required_env(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LEXMOUNT_API_KEY", raising=False)
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.delenv("LEXMOUNT_REGION", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "status"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["configured"] is False
    assert payload["missing"] == ["LEXMOUNT_API_KEY", "LEXMOUNT_PROJECT_ID"]
    assert payload["decision"] == {
        "action": "login",
        "reason": "missing_credentials",
        "can_attempt_api": False,
        "can_start_browser_work": False,
        "should_open_browser": False,
        "missing": ["LEXMOUNT_API_KEY", "LEXMOUNT_PROJECT_ID"],
        "next_command": "browser-cli auth login",
        "optional_open_command": "browser-cli auth login --open",
    }
    assert payload["environment"]["LEXMOUNT_API_KEY"]["value"] is None
    assert payload["environment"]["LEXMOUNT_PROJECT_ID"]["value"] is None
    assert payload["console_url"] == "https://browser.lexmount.cn"


def test_auth_bootstrap_returns_login_workflow_when_credentials_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.delenv("LEXMOUNT_API_KEY", raising=False)
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.setattr("browser_cli.cli.webbrowser.open", fake_open)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "bootstrap", "--open"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert opened == ["https://browser.lexmount.cn/connect/codex"]
    assert payload["command"] == "auth.bootstrap"
    assert payload["configured"] is False
    assert payload["status"]["configured"] is False
    assert payload["status"]["missing"] == ["LEXMOUNT_API_KEY", "LEXMOUNT_PROJECT_ID"]
    assert payload["decision"] == {
        "action": "login",
        "reason": "missing_credentials",
        "can_attempt_api": False,
        "can_start_browser_work": False,
        "requires_user_browser": True,
        "requires_browser_lexmount_cn": True,
        "missing": ["LEXMOUNT_API_KEY", "LEXMOUNT_PROJECT_ID"],
        "next_command": "browser-cli auth login --open",
        "fallback_command": "browser-cli auth export-env",
    }
    assert payload["opened"] == {"requested": True, "ok": True}
    assert payload["workflow"] == [
        "browser-cli auth login --open",
        "browser-cli auth export-env",
        "browser-cli auth status",
        "browser-cli doctor --json",
    ]
    assert payload["connect_from_codex"]["spec_command"] == (
        "browser-cli auth connect-spec"
    )
    assert (
        "Device-code or OAuth approval endpoint"
        in payload["connect_from_codex"]["needs_browser_lexmount_cn"]
    )
    assert any(
        "Do not ask the user to paste API keys" in rule
        for rule in payload["safety_rules"]
    )


def test_auth_bootstrap_returns_verify_workflow_when_credentials_exist(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "abcd1234wxyz")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.setattr(
        "browser_cli.cli.webbrowser.open",
        lambda url: (_ for _ in ()).throw(AssertionError("should not open browser")),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "bootstrap", "--open"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "abcd1234wxyz" not in output
    payload = json.loads(output)
    assert payload["configured"] is True
    assert payload["decision"] == {
        "action": "verify_access",
        "reason": "credentials_configured",
        "can_attempt_api": True,
        "can_start_browser_work": False,
        "requires_user_browser": False,
        "requires_browser_lexmount_cn": False,
        "next_command": "browser-cli doctor --json",
        "fallback_command": "browser-cli session list",
    }
    assert payload["opened"] == {
        "requested": False,
        "ok": None,
        "reason": "credentials_already_configured",
    }
    assert payload["workflow"] == [
        "browser-cli doctor --json",
        "browser-cli session list",
        "browser-cli session create",
    ]
    assert payload["status"]["environment"]["LEXMOUNT_API_KEY"]["value"] == (
        "abcd...wxyz"
    )


def test_auth_export_env_masks_secret_by_default(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "abcd1234wxyz")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.delenv("LEXMOUNT_REGION", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "export-env"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "abcd1234wxyz" not in output
    payload = json.loads(output)
    assert payload["command"] == "auth.export-env"
    assert payload["complete"] is True
    assert payload["masked"] is True
    assert payload["usable"] is False
    assert payload["contains_secrets"] is False
    assert payload["lines"] == [
        "export LEXMOUNT_API_KEY=abcd...wxyz",
        "export LEXMOUNT_PROJECT_ID=project",
    ]


def test_auth_export_env_can_reveal_usable_powershell_script(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret-key")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.delenv("LEXMOUNT_REGION", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "export-env",
                "--shell",
                "powershell",
                "--include-base-url",
                "--reveal-secrets",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["complete"] is True
    assert payload["masked"] is False
    assert payload["usable"] is True
    assert payload["contains_secrets"] is True
    assert payload["lines"] == [
        "$env:LEXMOUNT_API_KEY = 'secret-key'",
        "$env:LEXMOUNT_PROJECT_ID = 'project'",
        "$env:LEXMOUNT_BASE_URL = 'https://api.lexmount.cn'",
    ]


def test_auth_login_returns_browser_console_guidance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LEXMOUNT_API_KEY", raising=False)
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)
    monkeypatch.setattr(
        "browser_cli.cli.webbrowser.open",
        lambda url: (_ for _ in ()).throw(AssertionError("should not open browser")),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "login"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "auth.login"
    assert payload["console_url"] == "https://browser.lexmount.cn"
    assert payload["authorization_url"] == "https://browser.lexmount.cn/connect/codex"
    assert payload["opened"] == {"requested": False, "ok": None}
    assert payload["configured"] is False
    assert payload["required_env"] == ["LEXMOUNT_API_KEY", "LEXMOUNT_PROJECT_ID"]
    assert payload["future_flow"] == {
        "name": "Connect from Codex",
        "needs_browser_lexmount_cn": True,
        "prototype_command": "browser-cli auth device-code",
        "description": (
            "A future browser.lexmount.cn flow should let the user approve "
            "Codex access and return scoped local credentials without manual "
            "API key copying."
        ),
    }


def test_auth_login_can_open_authorization_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr("browser_cli.cli.webbrowser.open", fake_open)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "login", "--open"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert opened == ["https://browser.lexmount.cn/connect/codex"]
    assert payload["opened"] == {"requested": True, "ok": True}


def test_auth_login_reports_browser_open_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_open(url: str) -> bool:
        raise RuntimeError(f"cannot open {url}")

    monkeypatch.setattr("browser_cli.cli.webbrowser.open", fake_open)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "login",
                "--open",
                "--authorization-url",
                "https://browser.lexmount.cn/custom",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["authorization_url"] == "https://browser.lexmount.cn/custom"
    assert payload["opened"] == {
        "requested": True,
        "ok": False,
        "error": "RuntimeError",
        "message": "cannot open https://browser.lexmount.cn/custom",
    }


def test_auth_device_code_returns_future_endpoint_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "browser_cli.cli.webbrowser.open",
        lambda url: (_ for _ in ()).throw(AssertionError("should not open browser")),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "device-code"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "auth.device-code"
    assert payload["flow"] == "device_code"
    assert payload["available"] is False
    assert payload["status"] == "not_available"
    assert payload["reason"] == "browser_lexmount_cn_endpoint_required"
    assert payload["needs_browser_lexmount_cn"] is True
    assert payload["authorization_url"] == "https://browser.lexmount.cn/connect/codex"
    assert (
        payload["device_authorization_endpoint"]
        == "https://browser.lexmount.cn/connect/codex/device"
    )
    assert (
        payload["token_endpoint"] == "https://browser.lexmount.cn/connect/codex/token"
    )
    assert payload["opened"] == {"requested": False, "ok": None}
    assert payload["requested_scopes"] == [
        "browser:sessions",
        "browser:contexts",
        "browser:actions",
    ]
    assert (
        "device_code" in payload["endpoint_contract"]["device_authorization_response"]
    )
    assert "authorization_pending" in payload["endpoint_contract"]["token_error_codes"]
    assert "browser-cli auth login" in payload["fallback_commands"]


def test_auth_device_code_can_open_authorization_url_and_override_scopes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr("browser_cli.cli.webbrowser.open", fake_open)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "device-code",
                "--open",
                "--scope",
                "browser:sessions",
                "--scope",
                "browser:actions",
                "--authorization-url",
                "https://browser.lexmount.cn/connect/custom",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert opened == ["https://browser.lexmount.cn/connect/custom"]
    assert payload["opened"] == {"requested": True, "ok": True}
    assert payload["requested_scopes"] == ["browser:sessions", "browser:actions"]


def test_auth_connect_spec_returns_browser_console_requirements(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["auth", "connect-spec"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "auth.connect-spec"
    assert payload["name"] == "Connect from Codex"
    assert payload["available"] is False
    assert payload["status"] == "spec_only"
    assert payload["authorization_url"] == "https://browser.lexmount.cn/connect/codex"
    assert (
        payload["install_command"]
        == "uv tool install git+https://github.com/lexmount/browser-cli.git"
    )
    assert payload["verification_commands"] == [
        "browser-cli auth status",
        "browser-cli doctor --json",
        "browser-cli session list",
    ]
    endpoint_ids = {endpoint["id"] for endpoint in payload["backend_endpoints"]}
    assert {
        "projects",
        "scoped_api_key_create",
        "scoped_api_key_revoke",
        "device_authorization",
        "device_token",
    }.issubset(endpoint_ids)
    create_endpoint = next(
        endpoint
        for endpoint in payload["backend_endpoints"]
        if endpoint["id"] == "scoped_api_key_create"
    )
    assert create_endpoint["method"] == "POST"
    assert create_endpoint["path"] == "/api/codex/api-keys"
    assert create_endpoint["secret_response_fields"] == ["api_key"]
    frontend_state_ids = {state["id"] for state in payload["frontend_states"]}
    assert {
        "signed_out",
        "project_selected",
        "scoped_key_created",
        "env_ready",
        "doctor_verified",
        "device_code_pending",
    }.issubset(frontend_state_ids)
    assert payload["doctor_verification_contract"] == {
        "command": "browser-cli doctor --json",
        "success_criteria": [
            "ok is true",
            "decision.ready_for_browser_work is true",
            "checks contains credentials, direct-url, and api",
        ],
        "failure_ui": [
            "Show blocking_checks and warning_checks.",
            "Show next_steps and decision.next_command.",
            "Never display raw API keys from local command output.",
        ],
    }
    assert [test["id"] for test in payload["acceptance_tests"]] == [
        "manual_env_flow",
        "device_code_contract",
    ]
    assert payload["credential_lifecycle"]["required_controls"] == [
        "revoke",
        "rotate",
        "extend_expiry",
        "reduce_scope",
    ]
    assert "codex_key_revoked" in payload["credential_lifecycle"]["audit_events"]
    assert [item["scope"] for item in payload["required_scopes"]] == [
        "browser:sessions",
        "browser:contexts",
        "browser:actions",
    ]
    section_ids = {section["id"] for section in payload["page_sections"]}
    assert {
        "project",
        "scoped_api_key",
        "copy_env_install",
        "doctor_verify",
        "permissions",
        "device_code",
    }.issubset(section_ids)
    env_lines = next(
        block["lines"] for block in payload["copy_blocks"] if block["id"] == "env-posix"
    )
    assert "LEXMOUNT_API_KEY=<scoped-api-key>" in "\n".join(env_lines)
    assert payload["device_code_contract"] == {
        "device_authorization_endpoint": (
            "https://browser.lexmount.cn/connect/codex/device"
        ),
        "token_endpoint": "https://browser.lexmount.cn/connect/codex/token",
        "authorization_url": "https://browser.lexmount.cn/connect/codex",
        "response_fields": [
            "device_code",
            "user_code",
            "verification_uri",
            "verification_uri_complete",
            "expires_in",
            "interval",
            "scopes",
        ],
        "token_success_fields": [
            "access_token",
            "token_type",
            "expires_in",
            "scope",
            "project_id",
        ],
        "token_error_codes": [
            "authorization_pending",
            "slow_down",
            "expired_token",
            "access_denied",
        ],
    }


def test_auth_connect_spec_accepts_custom_scopes_and_endpoints(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "auth",
                "connect-spec",
                "--scope",
                "browser:sessions",
                "--scope",
                "browser:actions",
                "--authorization-url",
                "https://browser.lexmount.cn/connect/custom",
                "--device-authorization-url",
                "https://browser.lexmount.cn/connect/custom/device",
                "--token-url",
                "https://browser.lexmount.cn/connect/custom/token",
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["scope"] for item in payload["required_scopes"]] == [
        "browser:sessions",
        "browser:actions",
    ]
    assert (
        payload["device_code_contract"]["authorization_url"]
        == "https://browser.lexmount.cn/connect/custom"
    )
    assert (
        payload["device_code_contract"]["device_authorization_endpoint"]
        == "https://browser.lexmount.cn/connect/custom/device"
    )
    assert (
        payload["device_code_contract"]["token_endpoint"]
        == "https://browser.lexmount.cn/connect/custom/token"
    )
    endpoint_paths = {
        endpoint["id"]: endpoint["path"] for endpoint in payload["backend_endpoints"]
    }
    assert endpoint_paths["device_authorization"] == "/connect/custom/device"
    assert endpoint_paths["device_token"] == "/connect/custom/token"


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
