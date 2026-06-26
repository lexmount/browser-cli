from __future__ import annotations

import json
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
