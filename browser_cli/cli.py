"""Command-line entrypoint for Lexmount browser operations."""

from __future__ import annotations

import argparse
import json
import os
import shlex
from typing import Any, NoReturn
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from lex_browser_runtime.browser.actions import (
    BrowserActionTarget,
    ClickRequest,
    EvalRequest,
    OpenUrlRequest,
    ScreenshotRequest,
    SnapshotRequest,
    TypeRequest,
    WaitSelectorRequest,
    resolve_browser_action_connect_url,
    run_browser_action,
)
from lex_browser_runtime.browser.cases import run_case_file, validate_case_file
from lex_browser_runtime.browser.lexmount import (
    LexmountBrowserAdmin,
    LexmountErrorInfo,
    build_direct_connect_url,
)
from lex_browser_runtime.browser.models import (
    BrowserConfigError,
    BrowserParallelLimitError,
    BrowserRuntimeError,
)

DEFAULT_BROWSER_CONSOLE_URL = "https://browser.lexmount.cn"
DEFAULT_LEXMOUNT_BASE_URL = "https://api.lexmount.cn"
REQUIRED_AUTH_ENV_VARS = ("LEXMOUNT_API_KEY", "LEXMOUNT_PROJECT_ID")


def _json_dump(payload: dict[str, Any], exit_code: int = 0) -> NoReturn:
    print(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    )
    raise SystemExit(exit_code)


def _success(command: str, **payload: Any) -> NoReturn:
    data = {"ok": True, "command": command}
    data.update(payload)
    _json_dump(data)


def _failure(
    command: str,
    error: str,
    message: str,
    *,
    exit_code: int = 1,
    **payload: Any,
) -> NoReturn:
    data = {
        "ok": False,
        "command": command,
        "error": error,
        "message": message,
    }
    data.update(payload)
    _json_dump(data, exit_code=exit_code)


def _failure_from_exception(command: str, exc: Exception) -> NoReturn:
    info = getattr(exc, "lexmount_error_info", None)
    if isinstance(info, LexmountErrorInfo):
        _failure(command, **info.payload())
    if isinstance(exc, BrowserParallelLimitError):
        _failure(command, "browser_parallel_limit_reached", str(exc))
    if isinstance(exc, BrowserConfigError):
        _failure(command, "configuration_error", str(exc))
    if isinstance(exc, BrowserRuntimeError):
        _failure(command, exc.__class__.__name__, str(exc))
    _failure(command, exc.__class__.__name__, str(exc))


def _parse_metadata_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid metadata JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("metadata JSON must decode to an object")
    return value


def _normalize_context_mode(value: str) -> str:
    if value not in {"read_write", "read_only"}:
        raise argparse.ArgumentTypeError("context mode must be read_write or read_only")
    return value


def _normalize_browser_mode(value: str) -> str:
    if value not in {"normal", "light", "chrome-light-docker"}:
        raise argparse.ArgumentTypeError(
            "browser mode must be normal, light, or chrome-light-docker"
        )
    return value


def _model_payload(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _mask_direct_url_secret(connect_url: str) -> str:
    parsed = urlsplit(connect_url)
    query = [
        (key, "***" if key == "api_key" else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query, safe="*"),
            parsed.fragment,
        )
    )


def _masked_connect_url_payload(
    connect_url: str,
    *,
    reveal_connect_url: bool,
) -> dict[str, Any]:
    if reveal_connect_url:
        return {"connect_url": connect_url, "connect_url_masked": False}
    masked = _mask_direct_url_secret(connect_url)
    return {
        "connect_url": masked,
        "connect_url_masked": masked != connect_url,
    }


def _env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    return value


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _auth_env_var_payload(
    name: str,
    *,
    secret: bool = False,
    reveal_secrets: bool = False,
    default: str | None = None,
) -> dict[str, Any]:
    raw_value = _env_value(name)
    value = raw_value if raw_value is not None else default
    masked = bool(secret and raw_value and not reveal_secrets)
    if masked:
        value = _mask_secret(raw_value)
    return {
        "set": raw_value is not None,
        "value": value,
        "masked": masked,
        "default": raw_value is None and default is not None,
    }


def _auth_status_payload(*, reveal_secrets: bool) -> dict[str, Any]:
    missing = [name for name in REQUIRED_AUTH_ENV_VARS if _env_value(name) is None]
    return {
        "configured": not missing,
        "missing": missing,
        "console_url": DEFAULT_BROWSER_CONSOLE_URL,
        "environment": {
            "LEXMOUNT_API_KEY": _auth_env_var_payload(
                "LEXMOUNT_API_KEY",
                secret=True,
                reveal_secrets=reveal_secrets,
            ),
            "LEXMOUNT_PROJECT_ID": _auth_env_var_payload("LEXMOUNT_PROJECT_ID"),
            "LEXMOUNT_BASE_URL": _auth_env_var_payload(
                "LEXMOUNT_BASE_URL",
                default=DEFAULT_LEXMOUNT_BASE_URL,
            ),
            "LEXMOUNT_REGION": _auth_env_var_payload("LEXMOUNT_REGION"),
        },
        "next_steps": _auth_next_steps(missing),
    }


def _auth_next_steps(missing: list[str]) -> list[str]:
    if not missing:
        return [
            "Run browser-cli session list to verify API connectivity.",
            "Run browser-cli auth export-env to generate shell configuration lines.",
        ]
    return [
        f"Open {DEFAULT_BROWSER_CONSOLE_URL} and sign in.",
        "Select a project, copy its Project ID, and create or copy an API key.",
        "Set LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID in the local shell.",
        "Run browser-cli auth status again.",
    ]


def _quote_env_value(value: str, *, shell: str) -> str:
    if shell in {"posix", "fish"}:
        return shlex.quote(value)
    if shell == "powershell":
        return "'" + value.replace("'", "''") + "'"
    raise ValueError(f"Unsupported shell: {shell}")


def _format_env_line(name: str, value: str, *, shell: str) -> str:
    quoted = _quote_env_value(value, shell=shell)
    if shell == "posix":
        return f"export {name}={quoted}"
    if shell == "fish":
        return f"set -gx {name} {quoted}"
    if shell == "powershell":
        return f"$env:{name} = {quoted}"
    raise ValueError(f"Unsupported shell: {shell}")


def _export_env_value(
    name: str,
    *,
    secret: bool,
    reveal_secrets: bool,
    placeholder: str,
    placeholder_usable: bool = False,
) -> tuple[str, bool, bool]:
    raw_value = _env_value(name)
    if raw_value is None:
        return placeholder, False, placeholder_usable
    if secret and not reveal_secrets:
        masked = _mask_secret(raw_value) or "***"
        return masked, True, False
    return raw_value, False, True


def cmd_auth_status(args: argparse.Namespace) -> None:
    _success(
        "auth.status",
        **_auth_status_payload(reveal_secrets=args.reveal_secrets),
    )


def cmd_auth_export_env(args: argparse.Namespace) -> None:
    command = "auth.export-env"
    env_specs = [
        (
            "LEXMOUNT_API_KEY",
            True,
            f"<api-key from {DEFAULT_BROWSER_CONSOLE_URL}>",
            False,
        ),
        (
            "LEXMOUNT_PROJECT_ID",
            False,
            f"<project-id from {DEFAULT_BROWSER_CONSOLE_URL}>",
            False,
        ),
    ]
    if _env_value("LEXMOUNT_BASE_URL") or args.include_base_url:
        env_specs.append(
            (
                "LEXMOUNT_BASE_URL",
                False,
                DEFAULT_LEXMOUNT_BASE_URL,
                True,
            )
        )
    if _env_value("LEXMOUNT_REGION"):
        env_specs.append(("LEXMOUNT_REGION", False, "<region>", False))

    missing: list[str] = []
    lines: list[str] = []
    masked = False
    all_usable = True
    contains_secrets = False
    for name, secret, placeholder, placeholder_usable in env_specs:
        value, value_masked, usable = _export_env_value(
            name,
            secret=secret,
            reveal_secrets=args.reveal_secrets,
            placeholder=placeholder,
            placeholder_usable=placeholder_usable,
        )
        if name in REQUIRED_AUTH_ENV_VARS and _env_value(name) is None:
            missing.append(name)
        masked = masked or value_masked
        all_usable = all_usable and usable
        contains_secrets = contains_secrets or bool(secret and usable)
        lines.append(_format_env_line(name, value, shell=args.shell))

    _success(
        command,
        shell=args.shell,
        lines=lines,
        script="\n".join(lines),
        complete=not missing,
        missing=missing,
        masked=masked,
        usable=all_usable and not missing,
        contains_secrets=contains_secrets,
        console_url=DEFAULT_BROWSER_CONSOLE_URL,
        next_steps=[
            "Use --reveal-secrets only in a local trusted shell when you need usable export lines.",
            "Do not paste revealed API keys into chat, logs, README files, or commits.",
            "Run browser-cli auth status after exporting credentials.",
        ],
    )


def cmd_auth_login(args: argparse.Namespace) -> None:
    status = _auth_status_payload(reveal_secrets=False)
    _success(
        "auth.login",
        console_url=DEFAULT_BROWSER_CONSOLE_URL,
        configured=status["configured"],
        missing=status["missing"],
        required_env=list(REQUIRED_AUTH_ENV_VARS),
        suggested_commands=[
            "browser-cli auth status",
            "browser-cli auth export-env",
            "browser-cli session list",
        ],
        next_steps=[
            f"Open {DEFAULT_BROWSER_CONSOLE_URL} and sign in.",
            "Select the project Codex should use.",
            "Create or copy a scoped API key for agent/browser automation.",
            "Set LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID in the local shell.",
            "Run browser-cli auth status, then browser-cli session list.",
        ],
        future_flow={
            "name": "Connect from Codex",
            "needs_browser_lexmount_cn": True,
            "description": (
                "A future browser.lexmount.cn flow should let the user approve "
                "Codex access and return scoped local credentials without manual "
                "API key copying."
            ),
        },
    )


def cmd_session_create(args: argparse.Namespace) -> None:
    command = "session.create"
    try:
        result = LexmountBrowserAdmin().create_session(
            context_id=args.context_id,
            create_context=args.create_context,
            context_mode=args.context_mode,
            browser_mode=args.browser_mode,
            metadata=args.metadata,
        )
    except Exception as exc:
        _failure_from_exception(command, exc)
    _success(command, **_model_payload(result))


def cmd_session_list(args: argparse.Namespace) -> None:
    command = "session.list"
    try:
        result = LexmountBrowserAdmin().list_sessions(status=args.status)
    except Exception as exc:
        _failure_from_exception(command, exc)
    _success(command, **_model_payload(result))


def cmd_session_get(args: argparse.Namespace) -> None:
    command = "session.get"
    try:
        session = LexmountBrowserAdmin().get_session(args.session_id)
    except Exception as exc:
        _failure_from_exception(command, exc)
    _success(command, session=_model_payload(session))


def cmd_session_close(args: argparse.Namespace) -> None:
    command = "session.close"
    try:
        LexmountBrowserAdmin().close_session(args.session_id)
    except Exception as exc:
        _failure_from_exception(command, exc)
    _success(command, session_id=args.session_id, closed=True)


def cmd_session_keepalive(args: argparse.Namespace) -> None:
    command = "session.keepalive"
    try:
        result = LexmountBrowserAdmin().keepalive_session(
            session_id=args.session_id,
            interval=args.interval,
            duration=args.duration,
            stop_on_inactive=args.stop_on_inactive,
        )
    except Exception as exc:
        _failure_from_exception(command, exc)
    _success(command, **result)


def cmd_context_create(args: argparse.Namespace) -> None:
    command = "context.create"
    try:
        context = LexmountBrowserAdmin().create_context(metadata=args.metadata)
    except Exception as exc:
        _failure_from_exception(command, exc)
    _success(command, context=_model_payload(context))


def cmd_context_list(args: argparse.Namespace) -> None:
    command = "context.list"
    try:
        result = LexmountBrowserAdmin().list_contexts(
            status=args.status,
            limit=args.limit,
        )
    except Exception as exc:
        _failure_from_exception(command, exc)
    _success(command, **_model_payload(result))


def cmd_context_get(args: argparse.Namespace) -> None:
    command = "context.get"
    try:
        context = LexmountBrowserAdmin().get_context(args.context_id)
    except Exception as exc:
        _failure_from_exception(command, exc)
    _success(command, context=_model_payload(context))


def cmd_context_delete(args: argparse.Namespace) -> None:
    command = "context.delete"
    try:
        LexmountBrowserAdmin().delete_context(args.context_id)
    except Exception as exc:
        _failure_from_exception(command, exc)
    _success(command, context_id=args.context_id, deleted=True)


def _target_from_args(args: argparse.Namespace) -> BrowserActionTarget:
    target_count = sum(
        bool(value)
        for value in (
            getattr(args, "connect_url", None),
            getattr(args, "session_id", None),
            getattr(args, "direct_url", False),
        )
    )
    if target_count != 1:
        raise BrowserRuntimeError(
            "Pass exactly one action target: --connect-url, --session-id, or --direct-url."
        )
    return BrowserActionTarget(
        connect_url=getattr(args, "connect_url", None),
        session_id=getattr(args, "session_id", None),
        direct_url=bool(getattr(args, "direct_url", False)),
    )


def _run_action_command(
    args: argparse.Namespace,
    command: str,
    request: Any,
) -> None:
    try:
        target = _target_from_args(args)
        connect_url = resolve_browser_action_connect_url(target)
        action_name = command.removeprefix("action.")
        result = run_browser_action(
            connect_url=connect_url,
            action=action_name,  # type: ignore[arg-type]
            request=request,
        )
    except Exception as exc:
        _failure_from_exception(command, exc)
    _success(
        command,
        session_id=getattr(args, "session_id", None),
        **_masked_connect_url_payload(
            connect_url,
            reveal_connect_url=bool(getattr(args, "reveal_connect_url", False)),
        ),
        result=result.result,
    )


def cmd_action_open_url(args: argparse.Namespace) -> None:
    _run_action_command(
        args,
        "action.open-url",
        OpenUrlRequest(
            url=args.url,
            wait_until=args.wait_until,
            timeout_ms=args.timeout_ms,
        ),
    )


def cmd_action_wait_selector(args: argparse.Namespace) -> None:
    _run_action_command(
        args,
        "action.wait-selector",
        WaitSelectorRequest(
            selector=args.selector,
            state=args.state,
            timeout_ms=args.timeout_ms,
        ),
    )


def cmd_action_click(args: argparse.Namespace) -> None:
    _run_action_command(
        args,
        "action.click",
        ClickRequest(
            selector=args.selector,
            timeout_ms=args.timeout_ms,
            wait_after_ms=args.wait_after_ms,
        ),
    )


def cmd_action_type(args: argparse.Namespace) -> None:
    _run_action_command(
        args,
        "action.type",
        TypeRequest(
            selector=args.selector,
            text=args.text,
            timeout_ms=args.timeout_ms,
            press_enter=args.press_enter,
        ),
    )


def cmd_action_screenshot(args: argparse.Namespace) -> None:
    _run_action_command(
        args,
        "action.screenshot",
        ScreenshotRequest(
            output=args.output,
            full_page=args.full_page,
            timeout_ms=args.timeout_ms,
        ),
    )


def cmd_action_eval(args: argparse.Namespace) -> None:
    _run_action_command(
        args,
        "action.eval",
        EvalRequest(expression=args.expression),
    )


def cmd_action_snapshot(args: argparse.Namespace) -> None:
    _run_action_command(
        args,
        "action.snapshot",
        SnapshotRequest(timeout_ms=args.timeout_ms, max_chars=args.max_chars),
    )


def cmd_direct_url(args: argparse.Namespace) -> None:
    command = "direct-url"
    try:
        connect_url = build_direct_connect_url()
    except Exception as exc:
        _failure_from_exception(command, exc)
    reveal_url = bool(getattr(args, "reveal_url", False))
    _success(
        command,
        mode="direct",
        connect_url=connect_url if reveal_url else _mask_direct_url_secret(connect_url),
        masked=not reveal_url,
    )


def cmd_case_validate(args: argparse.Namespace) -> None:
    command = "case.validate"
    try:
        result = validate_case_file(args.file)
    except Exception as exc:
        _failure_from_exception(command, exc)
    _success(command, **result.model_dump(mode="json"))


def cmd_case_run(args: argparse.Namespace) -> None:
    command = "case.run"
    try:
        summary = run_case_file(
            file=args.file,
            run_id=args.run_id,
            artifacts_dir=args.artifacts_dir,
            stop_on_error=args.stop_on_error,
            close_created_session=args.close_created_session,
        )
    except Exception as exc:
        _failure_from_exception(command, exc)
    _json_dump(summary.model_dump(mode="json"), exit_code=0 if summary.ok else 1)


def _add_session_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--connect-url",
        help="Connect to the browser through an explicit CDP websocket URL",
    )
    parser.add_argument(
        "--session-id",
        help="Resolve connect_url from an existing Lexmount session",
    )
    parser.add_argument(
        "--direct-url",
        action="store_true",
        help="Use the shared direct websocket URL derived from env",
    )
    parser.add_argument(
        "--reveal-connect-url",
        action="store_true",
        help="Print the full resolved connect URL. Default output masks api_key.",
    )


def _add_session_create_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--context-id", help="Reuse an existing context")
    parser.add_argument(
        "--create-context",
        action="store_true",
        help="Create a new context before creating the session",
    )
    parser.add_argument(
        "--context-mode",
        default="read_write",
        type=_normalize_context_mode,
    )
    parser.add_argument(
        "--browser-mode",
        default="normal",
        type=_normalize_browser_mode,
    )
    parser.add_argument(
        "--metadata-json",
        dest="metadata",
        type=_parse_metadata_json,
        help="JSON object used when --create-context creates a context",
    )


def _add_auth_commands(subparsers: argparse._SubParsersAction[Any]) -> None:
    auth = subparsers.add_parser("auth", help="Inspect and configure credentials")
    auth_subparsers = auth.add_subparsers(dest="auth_command", required=True)

    auth_status = auth_subparsers.add_parser(
        "status",
        help="Show local Lexmount credential configuration",
    )
    auth_status.add_argument(
        "--reveal-secrets",
        action="store_true",
        help="Print secret values instead of masked values. Use only locally.",
    )
    auth_status.set_defaults(func=cmd_auth_status)

    auth_export_env = auth_subparsers.add_parser(
        "export-env",
        help="Generate shell env lines for Lexmount credentials",
    )
    auth_export_env.add_argument(
        "--shell",
        choices=["posix", "fish", "powershell"],
        default="posix",
    )
    auth_export_env.add_argument(
        "--include-base-url",
        action="store_true",
        help="Include LEXMOUNT_BASE_URL, using the China default if unset.",
    )
    auth_export_env.add_argument(
        "--reveal-secrets",
        action="store_true",
        help="Print usable secret values. Do not paste this output into chat.",
    )
    auth_export_env.set_defaults(func=cmd_auth_export_env)

    auth_login = auth_subparsers.add_parser(
        "login",
        help="Show browser.lexmount.cn login and configuration guidance",
    )
    auth_login.set_defaults(func=cmd_auth_login)


def _add_session_commands(subparsers: argparse._SubParsersAction[Any]) -> None:
    session = subparsers.add_parser("session", help="Manage browser sessions")
    session_subparsers = session.add_subparsers(
        dest="session_command",
        required=True,
    )

    session_create = session_subparsers.add_parser("create", help="Create a session")
    _add_session_create_args(session_create)
    session_create.set_defaults(func=cmd_session_create)

    session_list = session_subparsers.add_parser("list", help="List sessions")
    session_list.add_argument("--status", help="Optional status filter")
    session_list.set_defaults(func=cmd_session_list)

    session_get = session_subparsers.add_parser("get", help="Get one session")
    session_get.add_argument("--session-id", required=True)
    session_get.set_defaults(func=cmd_session_get)

    session_close = session_subparsers.add_parser("close", help="Close a session")
    session_close.add_argument("--session-id", required=True)
    session_close.set_defaults(func=cmd_session_close)

    session_keepalive = session_subparsers.add_parser(
        "keepalive",
        help="Poll one session status",
    )
    session_keepalive.add_argument("--session-id", required=True)
    session_keepalive.add_argument("--interval", type=float, default=5.0)
    session_keepalive.add_argument("--duration", type=float, default=60.0)
    session_keepalive.add_argument("--stop-on-inactive", action="store_true")
    session_keepalive.set_defaults(func=cmd_session_keepalive)


def _add_context_commands(subparsers: argparse._SubParsersAction[Any]) -> None:
    context = subparsers.add_parser("context", help="Manage browser contexts")
    context_subparsers = context.add_subparsers(
        dest="context_command",
        required=True,
    )

    context_create = context_subparsers.add_parser("create", help="Create a context")
    context_create.add_argument(
        "--metadata-json",
        dest="metadata",
        type=_parse_metadata_json,
        help="JSON object sent as context metadata",
    )
    context_create.set_defaults(func=cmd_context_create)

    context_list = context_subparsers.add_parser("list", help="List contexts")
    context_list.add_argument("--status", help="Optional status filter")
    context_list.add_argument("--limit", type=int, default=20)
    context_list.set_defaults(func=cmd_context_list)

    context_get = context_subparsers.add_parser("get", help="Get one context")
    context_get.add_argument("--context-id", required=True)
    context_get.set_defaults(func=cmd_context_get)

    context_delete = context_subparsers.add_parser("delete", help="Delete context")
    context_delete.add_argument("--context-id", required=True)
    context_delete.set_defaults(func=cmd_context_delete)


def _add_action_commands(subparsers: argparse._SubParsersAction[Any]) -> None:
    action = subparsers.add_parser("action", help="Run browser actions")
    action_subparsers = action.add_subparsers(dest="action_command", required=True)

    action_open_url = action_subparsers.add_parser("open-url", help="Open a URL")
    _add_session_target_args(action_open_url)
    action_open_url.add_argument("--url", required=True)
    action_open_url.add_argument(
        "--wait-until",
        default="load",
        choices=["commit", "domcontentloaded", "load", "networkidle"],
    )
    action_open_url.add_argument("--timeout-ms", type=float, default=30000)
    action_open_url.set_defaults(func=cmd_action_open_url)

    action_wait_selector = action_subparsers.add_parser(
        "wait-selector",
        help="Wait for a selector",
    )
    _add_session_target_args(action_wait_selector)
    action_wait_selector.add_argument("--selector", required=True)
    action_wait_selector.add_argument(
        "--state",
        default="visible",
        choices=["attached", "detached", "hidden", "visible"],
    )
    action_wait_selector.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_selector.set_defaults(func=cmd_action_wait_selector)

    action_click = action_subparsers.add_parser("click", help="Click a selector")
    _add_session_target_args(action_click)
    action_click.add_argument("--selector", required=True)
    action_click.add_argument("--timeout-ms", type=float, default=30000)
    action_click.add_argument("--wait-after-ms", type=float, default=0)
    action_click.set_defaults(func=cmd_action_click)

    action_type = action_subparsers.add_parser("type", help="Fill a selector")
    _add_session_target_args(action_type)
    action_type.add_argument("--selector", required=True)
    action_type.add_argument("--text", required=True)
    action_type.add_argument("--timeout-ms", type=float, default=30000)
    action_type.add_argument("--press-enter", action="store_true")
    action_type.set_defaults(func=cmd_action_type)

    action_screenshot = action_subparsers.add_parser(
        "screenshot",
        help="Capture a screenshot",
    )
    _add_session_target_args(action_screenshot)
    action_screenshot.add_argument("--output")
    action_screenshot.add_argument("--full-page", action="store_true")
    action_screenshot.add_argument("--timeout-ms", type=float, default=30000)
    action_screenshot.set_defaults(func=cmd_action_screenshot)

    action_eval = action_subparsers.add_parser(
        "eval",
        help="Run a JavaScript expression",
    )
    _add_session_target_args(action_eval)
    action_eval.add_argument("--expression", "--script", required=True)
    action_eval.set_defaults(func=cmd_action_eval)

    action_snapshot = action_subparsers.add_parser(
        "snapshot",
        help="Capture page title, URL, HTML, and body text",
    )
    _add_session_target_args(action_snapshot)
    action_snapshot.add_argument("--timeout-ms", type=float, default=30000)
    action_snapshot.add_argument("--max-chars", type=int, default=8000)
    action_snapshot.set_defaults(func=cmd_action_snapshot)


def _add_case_commands(subparsers: argparse._SubParsersAction[Any]) -> None:
    case = subparsers.add_parser("case", help="Validate or run a browser case file")
    case_subparsers = case.add_subparsers(dest="case_command", required=True)

    case_validate = case_subparsers.add_parser("validate", help="Validate a case file")
    case_validate.add_argument(
        "--file", required=True, help="Path to a JSON or YAML case file"
    )
    case_validate.set_defaults(func=cmd_case_validate)

    case_run = case_subparsers.add_parser("run", help="Run a case file")
    case_run.add_argument(
        "--file", required=True, help="Path to a JSON or YAML case file"
    )
    case_run.add_argument(
        "--run-id", help="Optional explicit run id used in output summaries"
    )
    case_run.add_argument("--artifacts-dir", help="Directory for run artifacts")
    case_run.add_argument("--stop-on-error", action="store_true")
    case_run.add_argument("--close-created-session", action="store_true")
    case_run.set_defaults(func=cmd_case_run)


def _add_alias_commands(subparsers: argparse._SubParsersAction[Any]) -> None:
    prepare = subparsers.add_parser(
        "prepare",
        help="Backward-compatible alias for session create",
    )
    _add_session_create_args(prepare)
    prepare.set_defaults(func=cmd_session_create)

    list_contexts = subparsers.add_parser(
        "list-contexts",
        help="Backward-compatible alias for context list",
    )
    list_contexts.add_argument("--status", help="Optional status filter")
    list_contexts.add_argument("--limit", type=int, default=20)
    list_contexts.set_defaults(func=cmd_context_list)

    close_session = subparsers.add_parser(
        "close-session",
        help="Backward-compatible alias for session close",
    )
    close_session.add_argument("--session-id", required=True)
    close_session.set_defaults(func=cmd_session_close)

    direct_url = subparsers.add_parser(
        "direct-url",
        help="Build the shared direct websocket URL",
    )
    direct_url.add_argument(
        "--reveal-url",
        action="store_true",
        help="Print the full URL including api_key. Default output masks secrets.",
    )
    direct_url.set_defaults(func=cmd_direct_url)


def build_parser() -> argparse.ArgumentParser:
    """Build the browser-cli parser."""

    parser = argparse.ArgumentParser(description="Lexmount browser operation CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_auth_commands(subparsers)
    _add_session_commands(subparsers)
    _add_context_commands(subparsers)
    _add_action_commands(subparsers)
    _add_case_commands(subparsers)
    _add_alias_commands(subparsers)

    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the Lexmount browser operation CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
