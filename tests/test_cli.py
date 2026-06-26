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

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return self.payload


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
