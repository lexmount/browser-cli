from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from browser_cli.cli import main as cli_main
from lex_browser_runtime.browser.models import BrowserConfigError


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


def test_direct_url_secret_masking_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "super-secret-key")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)

    exit_code, payload, output = run_cli_json(["direct-url"], capsys)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["masked"] is True
    assert "super-secret-key" not in output
    assert "api_key=***" in payload["connect_url"]


def test_direct_url_reveal_requires_explicit_flag(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXMOUNT_API_KEY", "super-secret-key")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "project")
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)

    exit_code, payload, output = run_cli_json(["direct-url", "--reveal-url"], capsys)

    assert exit_code == 0
    assert payload["masked"] is False
    assert "super-secret-key" in output
    assert "api_key=super-secret-key" in payload["connect_url"]


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
