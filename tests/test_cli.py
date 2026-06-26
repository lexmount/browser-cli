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
