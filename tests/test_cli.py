from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from browser_cli.cli import main as cli_main


class DummyModel:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        for key, value in payload.items():
            setattr(self, key, value)

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return self.payload


def test_version_flag_prints_package_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out == "browser-cli 0.1.0\n"


def _mock_uv(
    monkeypatch: pytest.MonkeyPatch,
    *,
    path: str | None = "/usr/local/bin/uv",
    version: str = "uv 0.9.0",
) -> None:
    monkeypatch.setattr("browser_cli.cli.shutil.which", lambda name: path)

    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=args[0], returncode=0, stdout=version)

    monkeypatch.setattr("browser_cli.cli.subprocess.run", fake_run)


def test_doctor_reports_missing_credentials_without_api_call(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LEXMOUNT_API_KEY", raising=False)
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    _mock_uv(monkeypatch)

    class FakeAdmin:
        def list_sessions(self, **kwargs: Any) -> DummyModel:
            raise AssertionError("doctor should not call API without credentials")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--json"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "doctor"
    assert payload["status"] == "fail"
    assert payload["configuration"]["environment"]["LEXMOUNT_API_KEY"] == {"set": False}
    assert payload["configuration"]["environment"]["LEXMOUNT_PROJECT_ID"] == {
        "set": False
    }
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["credentials"]["ok"] is False
    assert checks["credentials"]["missing"] == [
        "LEXMOUNT_API_KEY",
        "LEXMOUNT_PROJECT_ID",
    ]
    assert checks["api"]["ok"] is False
    assert checks["api"]["skipped"] is True
    assert payload["decision"]["ready_for_browser_work"] is False
    assert payload["decision"]["api_verified"] is False
    assert payload["decision"]["recommended_action"] == "fix_configuration"
    assert "credentials" in payload["decision"]["blocking_checks"]
    assert payload["workflow"] == {
        "next_step": "configure_credentials",
        "can_start_browser_work": False,
        "primary_command": "browser-cli auth bootstrap",
        "commands": [
            "browser-cli auth bootstrap",
            "browser-cli auth login",
            "browser-cli auth status",
            "browser-cli doctor --json",
        ],
        "blocking_checks": ["credentials", "direct-url", "api"],
        "warning_checks": [],
        "smoke_session_recommended": False,
        "notes": [
            "Use primary_command first; parse its JSON before continuing.",
            "Run smoke-session only for onboarding or session lifecycle debugging.",
            "Do not ask the user to paste API keys into chat.",
        ],
    }


def test_doctor_success_checks_api_and_masks_direct_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    _mock_uv(monkeypatch, version="uv 1.0.0")

    class FakeAdmin:
        def list_sessions(self, *, status: str | None) -> SimpleNamespace:
            assert status is None
            return SimpleNamespace(count=2, pagination=None)

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["ok"] is True
    assert payload["status"] in {"pass", "warn"}
    assert payload["api"] == {"session_count": 2, "pagination": None}
    assert payload["direct_url"] == {
        "connect_url": "wss://api.lexmount.cn/connection?project_id=project&api_key=***",
        "masked": True,
    }
    assert payload["decision"]["ready_for_browser_work"] is True
    assert payload["decision"]["api_verified"] is True
    assert payload["decision"]["blocking_checks"] == []
    assert payload["decision"]["recommended_action"] in {
        "continue",
        "continue_with_warnings",
    }
    assert payload["decision"]["next_command"] == "browser-cli session create"
    assert payload["workflow"]["next_step"] == "start_browser_session"
    assert payload["workflow"]["can_start_browser_work"] is True
    assert payload["workflow"]["primary_command"] == "browser-cli session create"
    assert payload["workflow"]["commands"] == [
        "browser-cli session create",
        "browser-cli doctor --smoke-session --json",
    ]
    assert payload["workflow"]["smoke_session_recommended"] is True
    assert "secret" not in output


def test_doctor_skip_api_does_not_fail_ready_configuration(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    _mock_uv(monkeypatch)

    class FakeAdmin:
        def list_sessions(self, **kwargs: Any) -> DummyModel:
            raise AssertionError("doctor --skip-api should not call API")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--skip-api"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"] == "warn"
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["api"]["severity"] == "warning"
    assert checks["api"]["skipped"] is True
    assert payload["decision"]["ready_for_browser_work"] is False
    assert payload["decision"]["api_verified"] is False
    assert payload["decision"]["recommended_action"] == "run_api_check"
    assert payload["decision"]["next_command"] == "browser-cli doctor --json"
    assert payload["workflow"]["next_step"] == "verify_api"
    assert payload["workflow"]["primary_command"] == "browser-cli doctor --json"
    assert payload["workflow"]["commands"] == ["browser-cli doctor --json"]


def test_doctor_handles_missing_uv_as_warning(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    _mock_uv(monkeypatch, path=None)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--skip-api"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"] == "warn"
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["uv"]["ok"] is False
    assert checks["uv"]["severity"] == "warning"
    assert checks["uv"]["available"] is False


def test_doctor_api_failure_is_structured(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    _mock_uv(monkeypatch)

    class FakeAdmin:
        def list_sessions(self, *, status: str | None) -> DummyModel:
            raise RuntimeError("network down")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["api"]["ok"] is False
    assert checks["api"]["error"] == "RuntimeError"
    assert checks["api"]["exception_message"] == "network down"
    assert payload["decision"]["ready_for_browser_work"] is False
    assert payload["decision"]["recommended_action"] == "fix_api_access"
    assert payload["decision"]["blocking_checks"] == ["api"]


def test_doctor_session_smoke_creates_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    _mock_uv(monkeypatch)
    calls: list[tuple[str, dict[str, Any]]] = []

    class FakeSession:
        session_id = "s1"

        def model_dump(self, *, mode: str) -> dict[str, Any]:
            assert mode == "json"
            return {
                "session_id": "s1",
                "status": "active",
                "browser_mode": "light",
                "connect_url": "wss://secret-connect-url",
            }

    class FakeAdmin:
        def list_sessions(self, *, status: str | None) -> SimpleNamespace:
            calls.append(("list", {"status": status}))
            return SimpleNamespace(count=0, pagination=None)

        def create_session(self, **kwargs: Any) -> SimpleNamespace:
            calls.append(("create", kwargs))
            return SimpleNamespace(session=FakeSession())

        def close_session(self, session_id: str) -> None:
            calls.append(("close", {"session_id": session_id}))

    admin = FakeAdmin()
    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: admin)

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--smoke-session"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "secret-connect-url" not in output
    payload = json.loads(output)
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["session-smoke"]["ok"] is True
    assert payload["decision"]["session_smoke_requested"] is True
    assert payload["decision"]["session_smoke_verified"] is True
    assert payload["session_smoke"] == {
        "browser_mode": "light",
        "created": True,
        "closed": True,
        "session": {
            "session_id": "s1",
            "status": "active",
            "browser_mode": "light",
        },
        "session_id": "s1",
    }
    assert calls == [
        ("list", {"status": None}),
        (
            "create",
            {
                "context_id": None,
                "create_context": False,
                "context_mode": "read_write",
                "browser_mode": "light",
                "metadata": None,
            },
        ),
        ("close", {"session_id": "s1"}),
    ]


def test_doctor_session_smoke_reports_close_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "secret")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    _mock_uv(monkeypatch)

    class FakeSession:
        session_id = "s1"

        def model_dump(self, *, mode: str) -> dict[str, Any]:
            assert mode == "json"
            return {"session_id": "s1", "status": "active"}

    class FakeAdmin:
        def list_sessions(self, *, status: str | None) -> SimpleNamespace:
            return SimpleNamespace(count=0, pagination=None)

        def create_session(self, **kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(session=FakeSession())

        def close_session(self, session_id: str) -> None:
            raise RuntimeError("close failed")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--smoke-session"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["session-smoke"]["ok"] is False
    assert payload["decision"]["ready_for_browser_work"] is False
    assert payload["decision"]["recommended_action"] == "fix_session_lifecycle"
    assert payload["decision"]["blocking_checks"] == ["session-smoke"]
    assert payload["workflow"]["next_step"] == "debug_session_lifecycle"
    assert payload["workflow"]["commands"] == [
        "browser-cli doctor --smoke-session --json",
        "browser-cli session list --status active",
    ]
    assert payload["session_smoke"]["created"] is True
    assert payload["session_smoke"]["closed"] is False
    assert payload["session_smoke"]["close_error"] == {
        "error": "RuntimeError",
        "exception_message": "close failed",
    }


def test_doctor_session_smoke_skips_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("LEXMOUNT_API_KEY", raising=False)
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)
    _mock_uv(monkeypatch)

    class FakeAdmin:
        def list_sessions(self, **kwargs: Any) -> DummyModel:
            raise AssertionError("doctor should not call API without credentials")

        def create_session(self, **kwargs: Any) -> DummyModel:
            raise AssertionError(
                "doctor should not create sessions without credentials"
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["doctor", "--smoke-session"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["session-smoke"]["ok"] is False
    assert payload["decision"]["session_smoke_requested"] is True
    assert payload["decision"]["session_smoke_verified"] is False
    assert payload["decision"]["recommended_action"] == "fix_configuration"
    assert payload["session_smoke"] == {
        "skipped": True,
        "reason": "missing_credentials",
    }


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


def test_failure_output_redacts_configured_api_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "abcd1234wxyz")

    class FakeAdmin:
        def list_sessions(
            self,
            *,
            status: str | None,
        ) -> DummyModel:
            raise RuntimeError("request used key abcd1234wxyz")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["session", "list"])

    assert exc_info.value.code == 1
    output = capsys.readouterr().out
    assert "abcd1234wxyz" not in output
    payload = json.loads(output)
    assert payload["ok"] is False
    assert payload["command"] == "session.list"
    assert payload["message"] == "request used key abcd...wxyz"


def test_success_output_redacts_sensitive_fields_and_url_params(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "env-secret-value")

    class FakeAdmin:
        def get_session(self, session_id: str) -> DummyModel:
            return DummyModel(
                {
                    "session_id": session_id,
                    "api_key": "raw-api-key",
                    "nested": {
                        "token": "raw-token-value",
                        "message": "env env-secret-value",
                    },
                    "connect_url": (
                        "wss://api.lexmount.cn/connection?"
                        "project_id=project&api_key=raw-api-key&access_token=abc"
                    ),
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["session", "get", "--session-id", "s1"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "raw-api-key" not in output
    assert "raw-token-value" not in output
    assert "env-secret-value" not in output
    payload = json.loads(output)
    assert payload["session"]["api_key"] == "raw-...-key"
    assert payload["session"]["nested"]["token"] == "raw-...alue"
    assert payload["session"]["nested"]["message"] == "env env-...alue"
    assert payload["session"]["connect_url"] == (
        "wss://api.lexmount.cn/connection?"
        "project_id=project&api_key=***&access_token=***"
    )


def test_unknown_command_error_is_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["not-a-command"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["command"] == "browser-cli"
    assert payload["error"] == "argument_error"
    assert "invalid choice" in payload["message"]
    assert payload["usage"].startswith("usage: ")


def test_subcommand_argument_error_is_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli_main(["session", "create", "--metadata-json", "not-json"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["ok"] is False
    assert payload["command"] == "session.create"
    assert payload["error"] == "argument_error"
    assert "invalid metadata JSON" in payload["message"]
    assert payload["usage"].startswith("usage: ")
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


def test_session_create_resolves_available_context_before_create(
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
                            "context_id": "other_ctx",
                            "status": "available",
                            "metadata": {"site": "docs"},
                        },
                        {
                            "context_id": "target_ctx",
                            "status": "available",
                            "metadata": {"site": "mail", "purpose": "login"},
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
                "--resolve-context",
                "--metadata-match-json",
                '{"site":"mail","purpose":"login"}',
                "--context-limit",
                "5",
            ]
        )

    assert exc_info.value.code == 0
    assert calls == [
        ("list", {"status": None, "limit": 5}),
        (
            "create_session",
            {
                "context_id": "target_ctx",
                "create_context": False,
                "context_mode": "read_write",
                "browser_mode": "normal",
                "metadata": None,
            },
        ),
    ]
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "session.create"
    assert payload["context_id"] == "target_ctx"
    resolution = payload["context_resolution"]
    assert resolution["resolved"] is True
    assert resolution["created"] is False
    assert resolution["context_id"] == "target_ctx"
    assert resolution["matched_count"] == 1
    assert resolution["unmatched_count"] == 1
    assert resolution["decision"] == {
        "action": "start_session",
        "reason": "context_available",
        "can_start_session": True,
        "should_create_context": False,
        "should_close_session": False,
        "selected_context_id": "target_ctx",
        "recommended_context_mode": "read_write",
        "recommended_session_command": (
            "browser-cli session create --context-id target_ctx --context-mode read_write"
        ),
    }


def test_session_create_resolves_and_creates_context_when_missing(
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
                            "context_id": "other_ctx",
                            "status": "available",
                            "metadata": {"site": "docs"},
                        }
                    ],
                }
            )

        def create_context(
            self,
            *,
            metadata: dict[str, Any] | None,
        ) -> DummyModel:
            calls.append(("create_context", {"metadata": metadata}))
            return DummyModel(
                {
                    "context_id": "new_ctx",
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
            return DummyModel(
                {"context_id": context_id, "session": {"session_id": "s1"}}
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "session",
                "create",
                "--resolve-context",
                "--create-context",
                "--metadata-match-json",
                '{"site":"mail","purpose":"login"}',
            ]
        )

    assert exc_info.value.code == 0
    assert calls == [
        ("list", {"status": None, "limit": 20}),
        ("create_context", {"metadata": {"site": "mail", "purpose": "login"}}),
        (
            "create_session",
            {
                "context_id": "new_ctx",
                "create_context": False,
                "context_mode": "read_write",
                "browser_mode": "normal",
                "metadata": None,
            },
        ),
    ]
    payload = json.loads(capsys.readouterr().out)
    assert payload["context_resolution"]["resolved"] is True
    assert payload["context_resolution"]["created"] is True
    assert payload["context_resolution"]["context_id"] == "new_ctx"
    assert payload["context_resolution"]["decision"]["reason"] == "context_created"


def test_session_create_rejects_locked_resolved_context(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAdmin:
        def get_context(self, context_id: str) -> DummyModel:
            return DummyModel({"context_id": context_id, "status": "locked"})

        def create_session(self, **kwargs: Any) -> DummyModel:
            raise AssertionError("session should not start with a locked context")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["session", "create", "--resolve-context", "--context-id", "ctx1"])

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "session.create"
    assert payload["error"] == "context_not_reusable"
    resolution = payload["context_resolution"]
    assert resolution["resolved"] is False
    assert resolution["context_id"] == "ctx1"
    assert resolution["decision"] == {
        "action": "close_or_create_context",
        "reason": "context_locked",
        "can_start_session": False,
        "should_create_context": True,
        "should_close_session": True,
        "selected_context_id": None,
        "recommended_context_mode": None,
        "recommended_session_command": None,
    }


def test_session_create_rejects_metadata_mismatched_resolved_context(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAdmin:
        def get_context(self, context_id: str) -> DummyModel:
            return DummyModel(
                {
                    "context_id": context_id,
                    "status": "available",
                    "metadata": {"site": "docs"},
                }
            )

        def create_session(self, **kwargs: Any) -> DummyModel:
            raise AssertionError("session should not start with mismatched metadata")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "session",
                "create",
                "--resolve-context",
                "--context-id",
                "ctx1",
                "--metadata-match-json",
                '{"site":"mail"}',
            ]
        )

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "context_not_reusable"
    resolution = payload["context_resolution"]
    assert resolution["metadata_matches"] is False
    assert resolution["metadata_match"] == {"site": "mail"}
    assert resolution["decision"] == {
        "action": "create_context",
        "reason": "metadata_mismatch",
        "can_start_session": False,
        "should_create_context": True,
        "should_close_session": False,
        "selected_context_id": None,
        "recommended_context_mode": None,
        "recommended_session_command": None,
    }


def test_session_create_resolve_rejects_mismatched_create_metadata(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAdmin:
        def list_contexts(self, **kwargs: Any) -> DummyModel:
            raise AssertionError("session create should fail before API calls")

        def create_context(self, **kwargs: Any) -> DummyModel:
            raise AssertionError("session create should not create mismatched context")

        def create_session(self, **kwargs: Any) -> DummyModel:
            raise AssertionError("session create should not start a session")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "session",
                "create",
                "--resolve-context",
                "--create-context",
                "--metadata-match-json",
                '{"site":"mail"}',
                "--metadata-json",
                '{"site":"docs"}',
            ]
        )

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "session.create"
    assert payload["error"] == "metadata_mismatch"
    assert payload["metadata_match"] == {"site": "mail"}
    assert payload["metadata"] == {"site": "docs"}


def test_session_create_rejects_metadata_match_without_resolve(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAdmin:
        def create_session(self, **kwargs: Any) -> DummyModel:
            raise AssertionError("session create should fail before API calls")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "session",
                "create",
                "--metadata-match-json",
                '{"site":"mail"}',
            ]
        )

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": False,
        "command": "session.create",
        "error": "invalid_arguments",
        "message": "--metadata-match-json requires --resolve-context.",
    }


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


def test_context_outputs_include_reuse_hints(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAdmin:
        def get_context(self, context_id: str) -> DummyModel:
            return DummyModel({"context_id": context_id, "status": "locked"})

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["context", "get", "--context-id", "ctx1"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["context"]["reuse"] == {
        "can_reuse_now": False,
        "reason": "context_locked",
        "recommended_context_mode": None,
        "recommended_session_command": None,
        "next_steps": [
            "Close the active session using this context, then retry.",
            "Create a new context if the current session must stay open.",
        ],
    }


def test_context_resolve_selects_available_context(
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
                    "status_filter": status,
                    "limit": limit,
                    "contexts": [
                        {"context_id": "locked_ctx", "status": "locked"},
                        {"context_id": "available_ctx", "status": "available"},
                    ],
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["context", "resolve", "--limit", "10"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls == [("list", {"status": None, "limit": 10})]
    assert payload["command"] == "context.resolve"
    assert payload["resolved"] is True
    assert payload["created"] is False
    assert payload["context_id"] == "available_ctx"
    assert payload["available_count"] == 1
    assert payload["locked_count"] == 1
    assert payload["decision"] == {
        "action": "start_session",
        "reason": "context_available",
        "can_start_session": True,
        "should_create_context": False,
        "should_close_session": False,
        "selected_context_id": "available_ctx",
        "recommended_context_mode": "read_write",
        "recommended_session_command": (
            "browser-cli session create "
            "--context-id available_ctx --context-mode read_write"
        ),
    }
    assert (
        payload["recommended_session_command"]
        == "browser-cli session create --context-id available_ctx --context-mode read_write"
    )


def test_context_resolve_selects_metadata_matching_context(
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
            assert status is None
            assert limit == 20
            return DummyModel(
                {
                    "count": 2,
                    "contexts": [
                        {
                            "context_id": "other_ctx",
                            "status": "available",
                            "metadata": {"site": "docs", "purpose": "login"},
                        },
                        {
                            "context_id": "target_ctx",
                            "status": "available",
                            "metadata": {"site": "mail", "purpose": "login"},
                        },
                    ],
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "context",
                "resolve",
                "--metadata-match-json",
                '{"site":"mail","purpose":"login"}',
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved"] is True
    assert payload["context_id"] == "target_ctx"
    assert payload["metadata_match"] == {"site": "mail", "purpose": "login"}
    assert payload["matched_count"] == 1
    assert payload["unmatched_count"] == 1
    assert payload["total_count"] == 2


def test_context_resolve_creates_with_metadata_match_when_no_candidate_matches(
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
                            "context_id": "other_ctx",
                            "status": "available",
                            "metadata": {"site": "docs"},
                        }
                    ],
                }
            )

        def create_context(
            self,
            *,
            metadata: dict[str, Any] | None,
        ) -> DummyModel:
            calls.append(("create", {"metadata": metadata}))
            return DummyModel(
                {
                    "context_id": "new_ctx",
                    "status": "available",
                    "metadata": metadata,
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "context",
                "resolve",
                "--create-if-missing",
                "--metadata-match-json",
                '{"site":"mail","purpose":"login"}',
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls == [
        ("list", {"status": None, "limit": 20}),
        ("create", {"metadata": {"site": "mail", "purpose": "login"}}),
    ]
    assert payload["created"] is True
    assert payload["context_id"] == "new_ctx"
    assert payload["metadata_match"] == {"site": "mail", "purpose": "login"}
    assert payload["matched_count"] == 0
    assert payload["unmatched_count"] == 1


def test_context_resolve_rejects_create_metadata_that_does_not_match_filter(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAdmin:
        def list_contexts(self, **kwargs: Any) -> DummyModel:
            raise AssertionError("context resolve should fail before API calls")

        def create_context(self, **kwargs: Any) -> DummyModel:
            raise AssertionError("context resolve should not create mismatched context")

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "context",
                "resolve",
                "--create-if-missing",
                "--metadata-match-json",
                '{"site":"mail"}',
                "--metadata-json",
                '{"site":"docs"}',
            ]
        )

    assert exc_info.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "context.resolve"
    assert payload["error"] == "metadata_mismatch"
    assert payload["metadata_match"] == {"site": "mail"}
    assert payload["metadata"] == {"site": "docs"}


def test_context_resolve_reports_no_matching_contexts_without_create(
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
            assert status is None
            assert limit == 20
            return DummyModel(
                {
                    "count": 1,
                    "contexts": [
                        {
                            "context_id": "other_ctx",
                            "status": "available",
                            "metadata": {"site": "docs"},
                        }
                    ],
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "context",
                "resolve",
                "--metadata-match-json",
                '{"site":"mail"}',
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved"] is False
    assert payload["decision"]["reason"] == "no_matching_contexts"
    assert payload["decision"]["should_create_context"] is True
    assert payload["matched_count"] == 0
    assert payload["unmatched_count"] == 1


def test_context_resolve_creates_when_no_context_is_available(
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
                    "status_filter": status,
                    "limit": limit,
                    "contexts": [{"context_id": "locked_ctx", "status": "locked"}],
                }
            )

        def create_context(
            self,
            *,
            metadata: dict[str, Any] | None,
        ) -> DummyModel:
            calls.append(("create", {"metadata": metadata}))
            return DummyModel({"context_id": "new_ctx", "status": "available"})

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "context",
                "resolve",
                "--create-if-missing",
                "--metadata-json",
                '{"purpose":"login"}',
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert calls == [
        ("list", {"status": None, "limit": 20}),
        ("create", {"metadata": {"purpose": "login"}}),
    ]
    assert payload["resolved"] is True
    assert payload["created"] is True
    assert payload["context_id"] == "new_ctx"
    assert payload["locked_count"] == 1
    assert payload["decision"] == {
        "action": "start_session",
        "reason": "context_created",
        "can_start_session": True,
        "should_create_context": False,
        "should_close_session": False,
        "selected_context_id": "new_ctx",
        "recommended_context_mode": "read_write",
        "recommended_session_command": (
            "browser-cli session create --context-id new_ctx --context-mode read_write"
        ),
    }


def test_context_resolve_reports_explicit_locked_context(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAdmin:
        def get_context(self, context_id: str) -> DummyModel:
            return DummyModel({"context_id": context_id, "status": "locked"})

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["context", "resolve", "--context-id", "ctx1"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved"] is False
    assert payload["created"] is False
    assert payload["context_id"] == "ctx1"
    assert payload["context"]["reuse"]["reason"] == "context_locked"
    assert payload["decision"] == {
        "action": "close_or_create_context",
        "reason": "context_locked",
        "can_start_session": False,
        "should_create_context": True,
        "should_close_session": True,
        "selected_context_id": None,
        "recommended_context_mode": None,
        "recommended_session_command": None,
    }


def test_context_resolve_rejects_explicit_metadata_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FakeAdmin:
        def get_context(self, context_id: str) -> DummyModel:
            return DummyModel(
                {
                    "context_id": context_id,
                    "status": "available",
                    "metadata": {"site": "docs"},
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(
            [
                "context",
                "resolve",
                "--context-id",
                "ctx1",
                "--metadata-match-json",
                '{"site":"mail"}',
            ]
        )

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved"] is False
    assert payload["metadata_matches"] is False
    assert payload["metadata_match"] == {"site": "mail"}
    assert payload["decision"] == {
        "action": "create_context",
        "reason": "metadata_mismatch",
        "can_start_session": False,
        "should_create_context": True,
        "should_close_session": False,
        "selected_context_id": None,
        "recommended_context_mode": None,
        "recommended_session_command": None,
    }


def test_context_resolve_returns_decision_when_only_locked_contexts_exist(
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
            assert status is None
            assert limit == 20
            return DummyModel(
                {
                    "count": 1,
                    "status_filter": status,
                    "limit": limit,
                    "contexts": [{"context_id": "locked_ctx", "status": "locked"}],
                }
            )

    monkeypatch.setattr("browser_cli.cli.LexmountBrowserAdmin", lambda: FakeAdmin())

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["context", "resolve"])

    assert exc_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolved"] is False
    assert payload["context_id"] is None
    assert payload["decision"] == {
        "action": "close_or_create_context",
        "reason": "only_locked_contexts",
        "can_start_session": False,
        "should_create_context": True,
        "should_close_session": True,
        "selected_context_id": None,
        "recommended_context_mode": None,
        "recommended_session_command": None,
    }


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


def test_action_reveal_connect_url_keeps_result_secrets_redacted(
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
        lambda **kwargs: SimpleNamespace(
            result={
                "token": "result-token-value",
                "nested": {
                    "api_key": "nested-api-key",
                    "url": (
                        "https://example.test/callback?access_token=result-token-value"
                    ),
                },
            }
        ),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main(["action", "snapshot", "--direct-url", "--reveal-connect-url"])

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "result-token-value" not in output
    assert "nested-api-key" not in output
    payload = json.loads(output)
    assert payload["connect_url"] == connect_url
    assert payload["connect_url_masked"] is False
    assert payload["result"] == {
        "token": "resu...alue",
        "nested": {
            "api_key": "nest...-key",
            "url": "https://example.test/callback?access_token=***",
        },
    }


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
