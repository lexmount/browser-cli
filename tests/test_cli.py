from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from browser_cli import cli as cli_module
from browser_cli.cli import main as cli_main


@pytest.fixture(autouse=True)
def isolate_device_token_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.setenv(
        "LEXMOUNT_BROWSER_CREDENTIALS_FILE",
        str(tmp_path / "missing-credentials.json"),
    )


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
    assert payload["json_output"]["always_json"] is True
    assert "LEXMOUNT_API_KEY" in payload["secret_policy"]["never_paste"]
    assert "browser-cli auth refresh" in payload["agent_entrypoints"]["setup"]
    assert "browser-cli doctor --smoke-session" in payload["agent_entrypoints"]["setup"]
    assert (
        "browser-cli action page-info --session-id <session_id>"
        in payload["agent_entrypoints"]["one_off_page_task"]
    )
    assert (
        "browser-cli context pick"
        in payload["agent_entrypoints"]["persistent_login_state"][0]
    )

    for name in (
        "commands",
        "auth.login",
        "auth.refresh",
        "doctor",
        "session.create",
        "context.pick",
        "action.open-url",
        "action.page-info",
        "action.wait-title",
        "action.press-key",
        "action.click-role",
        "action.fill-label",
        "action.link-snapshot",
        "action.table-snapshot",
        "action.interactive-snapshot",
        "direct-url",
    ):
        assert name in commands

    open_url = commands["action.open-url"]
    assert open_url["browser_target"] == {
        "required": True,
        "exactly_one_of": ["--connect-url", "--direct-url", "--session-id"],
    }
    assert "--url" in open_url["required_options"]
    assert any(
        "--smoke-session" in option["flags"] for option in commands["doctor"]["options"]
    )
    assert "super-secret-key" not in json.dumps(payload)


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
    assert "action.wait-title" in payload["commands"]
    assert "action.press-key" in payload["commands"]
    assert "action.link-snapshot" in payload["commands"]
    assert "action.table-snapshot" in payload["commands"]
    assert "action.interactive-snapshot" in payload["commands"]
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
    assert checks["lex_browser_runtime"]["version"] == "1.2.3"
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
    checks = _checks_by_name(payload)
    assert checks["browser_cli_executable"]["status"] == "warn"
    assert checks["browser_cli_executable"]["fix"]["code"] == (
        "install_browser_cli_on_path"
    )
    assert "uv tool install" in checks["browser_cli_executable"]["fix"]["commands"][0]
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

    checks = _checks_by_name(payload)
    assert checks["env.LEXMOUNT_API_KEY"]["status"] == "fail"
    assert checks["env.LEXMOUNT_PROJECT_ID"]["status"] == "fail"
    assert checks["direct_url"]["status"] == "fail"
    assert checks["api_connectivity"]["status"] == "skipped"
    assert checks["env.LEXMOUNT_API_KEY"]["fix"]["code"] == "configure_credentials"
    assert checks["env.LEXMOUNT_API_KEY"]["fix"]["env"] == ["LEXMOUNT_API_KEY"]
    assert "browser-cli auth login" in checks["env.LEXMOUNT_API_KEY"]["fix"]["commands"]
    assert checks["env.LEXMOUNT_PROJECT_ID"]["fix"]["env"] == ["LEXMOUNT_PROJECT_ID"]
    assert checks["direct_url"]["fix"]["code"] == "fix_direct_url_configuration"
    assert checks["api_connectivity"]["fix"]["code"] == "run_live_api_check"


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
    assert payload["ready_for_browser_actions"] is False
    assert payload["device_token"]["valid"] is True
    checks = _checks_by_name(payload)
    assert checks["local_device_token"]["status"] == "pass"
    assert checks["local_device_token"]["device_token"]["token_id"] == "tok_123"
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
    assert payload["repair_plan"]["commands"] == ["browser-cli doctor"]
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
    assert "browser-cli doctor" in payload["next_steps"][0]


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
    assert payload["warnings"] == []
    assert payload["commands"] == [
        "export LEXMOUNT_API_KEY='<api-key>'",
        "export LEXMOUNT_PROJECT_ID='<project-id>'",
    ]
    assert payload["exports"][0]["usable"] is False
    assert payload["exports"][1]["usable"] is False


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
    assert payload["warnings"] == []
    assert "local-secret" in payload["script"]
    assert payload["exports"][0]["usable"] is True


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
    assert payload["login_url"] == "https://browser.lexmount.cn"
    assert payload["device_code_available"] is False
    assert payload["flows"][0]["name"] == "manual_env"
    assert payload["flows"][0]["available"] is True
    assert payload["flows"][1]["name"] == "connect_from_codex"
    assert payload["flows"][1]["available"] is False
    assert "browser-cli doctor" in payload["commands"]

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
    assert handoff["copyable_commands"] == [
        "browser-cli auth status",
        "browser-cli auth login",
        "browser-cli auth export-env",
        "browser-cli doctor",
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
    assert handoff["verification"]["doctor_command"] == "browser-cli doctor"
    assert "LEXMOUNT_API_KEY" in handoff["secret_policy"]["do_not_paste_in_chat"]
    assert (
        "browser-cli auth export-env output without --reveal-secrets"
        in handoff["secret_policy"]["safe_to_share"]
    )

    connect = payload["connect_from_codex"]
    assert connect["available"] is False
    assert connect["project_id"] is None
    assert connect["project_id_source"] == "unset"
    assert connect["requested_scopes"] == [
        "browser:sessions",
        "browser:contexts",
        "browser:actions",
    ]
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
    assert connect["requested_expires_in"] == "24h"
    assert payload["handoff"]["local_env"][1]["value"] == "arg-project"
    assert payload["handoff"]["local_env"][1]["value_source"] == "argument"
    assert payload["handoff"]["requested_scopes"] == [
        "browser:sessions",
        "browser:actions",
    ]
    assert payload["handoff"]["requested_expires_in"] == "24h"

    query = parse_qs(urlsplit(connect["url"]).query)
    assert query["project_id"] == ["arg-project"]
    assert query["scope"] == ["browser:sessions", "browser:actions"]
    assert query["expires_in"] == ["24h"]


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
    assert payload["available"] is False
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
    assert any("device-code endpoints" in item for item in payload["next_steps"])


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
    assert payload["reusable"] is False
    assert payload["locked"] is True
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
    assert payload["reuse"]["reusable"] is True
    assert payload["metadata_filter"] == {"purpose": "codex"}
    assert payload["candidates"] == [
        {
            "context_id": "ctx-locked",
            "status": "locked",
            "normalized_status": "locked",
            "availability": "locked",
            "metadata_match": True,
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
            "reusable": True,
            "locked": False,
            "reason": "status_reusable",
        },
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
    assert payload["reuse"]["reusable"] is True
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
