"""Command-line entrypoint for Lexmount browser operations."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
from importlib.metadata import PackageNotFoundError, version
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

DEFAULT_LEXMOUNT_BASE_URL = "https://api.lexmount.cn"
LEXMOUNT_CONSOLE_URL = "https://browser.lexmount.cn"
LEXMOUNT_CODEX_CONNECT_URL = f"{LEXMOUNT_CONSOLE_URL}/connect/codex"
DEFAULT_CODEX_CONNECT_SCOPES = (
    "browser:sessions",
    "browser:contexts",
    "browser:actions",
)
DEFAULT_CODEX_CONNECT_EXPIRES_IN = "7d"
COMMON_DOM_EVENT_NAMES = (
    "input",
    "change",
    "click",
    "focus",
    "blur",
    "submit",
    "mousedown",
    "mouseup",
    "mouseover",
    "mouseenter",
    "mousemove",
)
CONTEXT_REUSABLE_STATUSES = {"available", "ready", "idle"}
CONTEXT_LOCKED_STATUSES = {"locked", "busy", "in_use", "in-use", "active", "running"}
SENSITIVE_PAYLOAD_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "authorization",
    "secret",
    "token",
}


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
        "error": _mask_sensitive_text(error),
        "message": _mask_sensitive_text(message),
    }
    data.update(
        {
            key: "***"
            if _is_sensitive_payload_key(key)
            else _sanitize_failure_value(value)
            for key, value in payload.items()
        }
    )
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


def _command_from_prog(prog: str) -> str:
    parts = prog.split()
    if parts and parts[0] == "browser-cli":
        parts = parts[1:]
    return ".".join(parts) if parts else "browser-cli"


class JsonArgumentParser(argparse.ArgumentParser):
    def add_subparsers(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("parser_class", type(self))
        return super().add_subparsers(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        _failure(
            _command_from_prog(self.prog),
            "argument_error",
            message,
            exit_code=2,
            usage=self.format_usage().strip(),
        )


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


def _parse_filter_metadata_json(raw: str | None) -> dict[str, Any]:
    parsed = _parse_metadata_json(raw)
    return parsed or {}


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


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def _model_payload(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _package_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


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


def _mask_sensitive_text(text: str) -> str:
    masked = re.sub(
        r"(?i)((?:api[_-]?key|access[_-]?token|token)=)[^&\s]+",
        r"\1***",
        text,
    )
    api_key = os.environ.get("LEXMOUNT_API_KEY")
    if api_key:
        masked = masked.replace(api_key, "***")
    return masked


def _is_sensitive_payload_key(key: Any) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return normalized in SENSITIVE_PAYLOAD_KEYS


def _sanitize_failure_value(value: Any) -> Any:
    if isinstance(value, str):
        return _mask_sensitive_text(value)
    if isinstance(value, dict):
        return {
            key: "***"
            if _is_sensitive_payload_key(key)
            else _sanitize_failure_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_failure_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_failure_value(item) for item in value)
    return value


def _doctor_check(
    name: str,
    status: str,
    message: str,
    **details: Any,
) -> dict[str, Any]:
    check = {"name": name, "status": status, "message": message}
    check.update(details)
    return check


def _doctor_fix(
    code: str,
    *,
    commands: list[str] | None = None,
    env: list[str] | None = None,
    guidance: list[str] | None = None,
) -> dict[str, Any]:
    fix: dict[str, Any] = {"code": code}
    if commands:
        fix["commands"] = commands
    if env:
        fix["env"] = env
    if guidance:
        fix["guidance"] = guidance
    return fix


def _credential_doctor_fix(*env: str) -> dict[str, Any]:
    return _doctor_fix(
        "configure_credentials",
        env=list(env),
        commands=[
            "browser-cli auth login",
            "browser-cli auth export-env",
            "browser-cli auth status",
            "browser-cli doctor",
        ],
        guidance=[
            "Get Project ID and API key from https://browser.lexmount.cn.",
            "Set credentials only in the local shell, not in chat.",
            "Run doctor again after exporting credentials.",
        ],
    )


def _normalize_status(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().lower()


def _context_reuse_state(context: dict[str, Any]) -> dict[str, Any]:
    status = _normalize_status(context.get("status"))
    if status in CONTEXT_REUSABLE_STATUSES:
        return {
            "status": context.get("status"),
            "reusable": True,
            "locked": False,
            "reason": "status_reusable",
        }
    if status in CONTEXT_LOCKED_STATUSES:
        return {
            "status": context.get("status"),
            "reusable": False,
            "locked": True,
            "reason": "status_locked",
        }
    if status is None:
        return {
            "status": None,
            "reusable": False,
            "locked": False,
            "reason": "status_missing",
        }
    return {
        "status": context.get("status"),
        "reusable": False,
        "locked": False,
        "reason": "status_not_reusable",
    }


def _metadata_matches(
    metadata: dict[str, Any] | None,
    expected: dict[str, Any],
) -> bool:
    if not expected:
        return True
    if not isinstance(metadata, dict):
        return False
    return all(metadata.get(key) == value for key, value in expected.items())


def _context_pick_candidate(
    context: dict[str, Any],
    metadata_filter: dict[str, Any],
) -> dict[str, Any]:
    reuse = _context_reuse_state(context)
    metadata_match = _metadata_matches(context.get("metadata"), metadata_filter)
    return {
        "context_id": context.get("context_id"),
        "status": context.get("status"),
        "metadata_match": metadata_match,
        "reusable": reuse["reusable"],
        "locked": reuse["locked"],
        "reason": reuse["reason"] if metadata_match else "metadata_mismatch",
    }


def _select_or_create_context_for_session(
    admin: Any,
    *,
    command: str,
    metadata_filter: dict[str, Any],
    status: str | None,
    limit: int,
    create_if_missing: bool,
) -> dict[str, Any]:
    try:
        result = admin.list_contexts(status=status, limit=limit)
    except Exception as exc:
        _failure_from_exception(command, exc)

    payload = _model_payload(result)
    contexts = payload.get("contexts", [])
    if not isinstance(contexts, list):
        contexts = []
    candidates = [
        _context_pick_candidate(context, metadata_filter) for context in contexts
    ]

    for context, candidate in zip(contexts, candidates, strict=True):
        if candidate["metadata_match"] and candidate["reusable"]:
            return {
                "selected": True,
                "created": False,
                "context_id": context.get("context_id"),
                "context": context,
                "reuse": _context_reuse_state(context),
                "checked": len(contexts),
                "candidates": candidates,
                "metadata_filter": metadata_filter,
                "status_filter": status,
                "limit": limit,
            }

    if create_if_missing:
        try:
            context = admin.create_context(metadata=metadata_filter or None)
        except Exception as exc:
            _failure_from_exception(command, exc)
        created_context = _model_payload(context)
        return {
            "selected": True,
            "created": True,
            "context_id": created_context.get("context_id"),
            "context": created_context,
            "reuse": _context_reuse_state(created_context),
            "checked": len(contexts),
            "candidates": candidates,
            "metadata_filter": metadata_filter,
            "status_filter": status,
            "limit": limit,
        }

    _failure(
        command,
        "no_available_context",
        "No reusable context matched the requested session context filters.",
        selected=False,
        created=False,
        checked=len(contexts),
        candidates=candidates,
        metadata_filter=metadata_filter,
        status_filter=status,
        limit=limit,
    )


def _env_value_status(
    name: str,
    *,
    secret: bool = False,
    default: str | None = None,
) -> dict[str, Any]:
    value = os.environ.get(name)
    present = bool(value)
    payload: dict[str, Any] = {"present": present}
    if secret:
        payload.update(
            {
                "masked_value": "***" if present else None,
                "length": len(value) if value else 0,
            }
        )
    else:
        payload["value"] = value
    if default is not None:
        payload["default"] = default
        payload["effective_value"] = value or default
        payload["using_default"] = not present
    return payload


def _auth_next_steps(*, configured: bool) -> list[str]:
    if configured:
        return [
            "Run `browser-cli doctor` to verify live API connectivity.",
            "Create a session with `browser-cli session create`.",
        ]
    return [
        "Run `browser-cli auth login` for browser.lexmount.cn setup guidance.",
        "Set LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID in the local shell.",
        "Run `browser-cli doctor` after setting credentials.",
    ]


def _dedupe_preserving_order(values: list[str] | tuple[str, ...]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _auth_login_project_id(args: argparse.Namespace) -> tuple[str | None, str]:
    if args.project_id:
        return args.project_id, "argument"
    env_project_id = os.environ.get("LEXMOUNT_PROJECT_ID")
    if env_project_id:
        return env_project_id, "env"
    return None, "unset"


def _auth_login_scopes(args: argparse.Namespace) -> list[str]:
    raw_scopes = args.scope or list(DEFAULT_CODEX_CONNECT_SCOPES)
    return _dedupe_preserving_order(raw_scopes)


def _connect_from_codex_url(
    *,
    project_id: str | None,
    scopes: list[str],
    expires_in: str,
) -> str:
    query: list[tuple[str, str]] = [
        ("source", "browser-cli"),
        ("intent", "agent-browser-control"),
        ("response", "env"),
        ("expires_in", expires_in),
    ]
    if project_id:
        query.append(("project_id", project_id))
    query.extend(("scope", scope) for scope in scopes)
    return f"{LEXMOUNT_CODEX_CONNECT_URL}?{urlencode(query)}"


def _quote_env_value(value: str, shell: str) -> str:
    if shell in {"posix", "fish"}:
        return shlex.quote(value)
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _export_command(name: str, value: str, shell: str) -> str:
    quoted = _quote_env_value(value, shell)
    if shell == "fish":
        return f"set -gx {name} {quoted}"
    if shell == "powershell":
        return f"$env:{name} = {quoted}"
    return f"export {name}={quoted}"


def cmd_session_create(args: argparse.Namespace) -> None:
    command = "session.create"
    context_reuse: dict[str, Any] | None = None
    context_metadata_filter = getattr(args, "context_metadata_filter", None)
    if context_metadata_filter is None and (
        args.create_context_if_missing or args.context_status is not None
    ):
        _failure(
            command,
            "argument_error",
            (
                "Use --create-context-if-missing or --context-status together "
                "with --context-metadata-json."
            ),
            exit_code=2,
        )
    if context_metadata_filter is not None and (args.context_id or args.create_context):
        _failure(
            command,
            "argument_error",
            (
                "Use --context-metadata-json without --context-id or "
                "--create-context; pass --create-context-if-missing when a new "
                "matching context should be created."
            ),
            exit_code=2,
        )

    try:
        admin = LexmountBrowserAdmin()
        context_id = args.context_id
        create_context = args.create_context

        if context_metadata_filter is not None:
            context_reuse = _select_or_create_context_for_session(
                admin,
                command=command,
                metadata_filter=context_metadata_filter,
                status=args.context_status,
                limit=args.context_limit,
                create_if_missing=args.create_context_if_missing,
            )
            context_id = context_reuse.get("context_id")
            create_context = False
            if not context_id:
                _failure(
                    command,
                    "context_missing_id",
                    "Selected context does not include a context_id.",
                    context_reuse=context_reuse,
                )

        result = admin.create_session(
            context_id=context_id,
            create_context=create_context,
            context_mode=args.context_mode,
            browser_mode=args.browser_mode,
            metadata=args.metadata,
        )
    except Exception as exc:
        _failure_from_exception(command, exc)
    payload = _model_payload(result)
    if context_reuse is not None:
        payload["context_reuse"] = context_reuse
    _success(command, **payload)


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


def cmd_context_status(args: argparse.Namespace) -> None:
    command = "context.status"
    try:
        context = LexmountBrowserAdmin().get_context(args.context_id)
    except Exception as exc:
        _failure_from_exception(command, exc)
    payload = _model_payload(context)
    reuse = _context_reuse_state(payload)
    _success(
        command,
        context_id=payload.get("context_id") or args.context_id,
        reusable=reuse["reusable"],
        locked=reuse["locked"],
        reuse=reuse,
        context=payload,
    )


def cmd_context_pick(args: argparse.Namespace) -> None:
    command = "context.pick"
    metadata_filter = args.metadata_filter
    admin = LexmountBrowserAdmin()
    try:
        result = admin.list_contexts(
            status=args.status,
            limit=args.limit,
        )
    except Exception as exc:
        _failure_from_exception(command, exc)

    payload = _model_payload(result)
    contexts = payload.get("contexts", [])
    candidates = [
        _context_pick_candidate(context, metadata_filter) for context in contexts
    ]
    selected_context: dict[str, Any] | None = None
    for context, candidate in zip(contexts, candidates, strict=True):
        if candidate["metadata_match"] and candidate["reusable"]:
            selected_context = context
            break

    if selected_context is not None:
        _success(
            command,
            selected=True,
            created=False,
            context_id=selected_context.get("context_id"),
            context=selected_context,
            reuse=_context_reuse_state(selected_context),
            checked=len(contexts),
            candidates=candidates,
            metadata_filter=metadata_filter,
        )

    if args.create_if_missing:
        try:
            context = admin.create_context(metadata=metadata_filter or None)
        except Exception as exc:
            _failure_from_exception(command, exc)
        created_context = _model_payload(context)
        _success(
            command,
            selected=True,
            created=True,
            context_id=created_context.get("context_id"),
            context=created_context,
            reuse=_context_reuse_state(created_context),
            checked=len(contexts),
            candidates=candidates,
            metadata_filter=metadata_filter,
        )

    _failure(
        command,
        "no_available_context",
        "No reusable context matched the requested filters.",
        selected=False,
        created=False,
        checked=len(contexts),
        candidates=candidates,
        metadata_filter=metadata_filter,
    )


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


def _js_literal(value: Any) -> str:
    return json.dumps(value)


def _eval_backed_result_payload(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"value": result}

    value = result.get("value")
    payload = dict(value) if isinstance(value, dict) else {"value": value}
    for key in ("url", "fallback"):
        if key in result and key not in payload:
            payload[key] = result[key]
    return payload


def _run_eval_backed_action_command(
    args: argparse.Namespace,
    command: str,
    expression: str,
) -> None:
    try:
        target = _target_from_args(args)
        connect_url = resolve_browser_action_connect_url(target)
        result = run_browser_action(
            connect_url=connect_url,
            action="eval",
            request=EvalRequest(expression=expression),
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
        result=_eval_backed_result_payload(result.result),
    )


def _selector_expression(selector: str, body: str) -> str:
    return f"""
() => {{
  const selector = {_js_literal(selector)};
  const element = document.querySelector(selector);
  if (!element) {{
    return {{ selector, found: false }};
  }}
{body}
}}
""".strip()


def _event_expression(selector: str, body: str) -> str:
    return _selector_expression(
        selector,
        f"""
  const dispatch = (event) => element.dispatchEvent(event);
{body}
""".rstrip(),
    )


def _dom_helpers_expression(
    *,
    include_hidden: bool = False,
    max_nodes: int | None = None,
) -> str:
    max_nodes_source = "null" if max_nodes is None else _js_literal(max_nodes)
    return f"""
  const includeHidden = {_js_literal(include_hidden)};
  const maxNodes = {max_nodes_source};
  const interactiveSelector = [
    "a[href]",
    "button",
    "input:not([type=hidden])",
    "select",
    "textarea",
    "summary",
    "[role]",
    "[onclick]",
    "[tabindex]:not([tabindex='-1'])",
    "[contenteditable='true']"
  ].join(",");

  const normalize = (value) => String(value ?? "").replace(/\\s+/g, " ").trim();
  const visible = (element) => {{
    if (includeHidden) return true;
    const style = window.getComputedStyle(element);
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.opacity === "0"
    ) {{
      return false;
    }}
    return Boolean(
      element.offsetWidth ||
      element.offsetHeight ||
      element.getClientRects().length
    );
  }};
  const textOf = (element) => normalize(element.innerText ?? element.textContent ?? "");
  const nameFromLabelledBy = (element) => {{
    const labelledBy = element.getAttribute("aria-labelledby");
    if (!labelledBy) return "";
    return normalize(
      labelledBy
        .split(/\\s+/)
        .map((id) => document.getElementById(id)?.innerText ?? "")
        .join(" ")
    );
  }};
  const accessibleName = (element) => normalize(
    element.getAttribute("aria-label") ||
    nameFromLabelledBy(element) ||
    element.getAttribute("alt") ||
    element.getAttribute("title") ||
    element.getAttribute("placeholder") ||
    element.value ||
    textOf(element)
  );
  const roleOf = (element) => {{
    const explicitRole = normalize(element.getAttribute("role")).split(" ")[0];
    if (explicitRole) return explicitRole;
    const tag = element.tagName.toLowerCase();
    const type = String(element.getAttribute("type") || "").toLowerCase();
    if (tag === "a" && element.hasAttribute("href")) return "link";
    if (tag === "button") return "button";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "img") return "img";
    if (tag === "summary") return "button";
    if (tag === "input") {{
      if (["button", "submit", "reset"].includes(type)) return "button";
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "range") return "slider";
      if (["email", "password", "search", "tel", "text", "url", ""].includes(type)) {{
        return "textbox";
      }}
      return type || "input";
    }}
    if (/^h[1-6]$/.test(tag)) return "heading";
    return "";
  }};
  const cssPath = (element) => {{
    if (element.id) return `#${{CSS.escape(element.id)}}`;
    const parts = [];
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 4) {{
      let part = current.tagName.toLowerCase();
      if (current.classList.length) {{
        part += "." + [...current.classList].slice(0, 2).map(CSS.escape).join(".");
      }}
      const parent = current.parentElement;
      if (parent) {{
        const siblings = [...parent.children].filter((child) => child.tagName === current.tagName);
        if (siblings.length > 1) {{
          part += `:nth-of-type(${{siblings.indexOf(current) + 1}})`;
        }}
      }}
      parts.unshift(part);
      current = parent;
    }}
    return parts.join(" > ");
  }};
  const nodeInfo = (element) => ({{
    selector: cssPath(element),
    tag: element.tagName.toLowerCase(),
    role: roleOf(element) || null,
    name: accessibleName(element),
    text: textOf(element),
    visible: visible(element)
  }});
  const matchesText = (candidate, query, exact, caseSensitive) => {{
    let haystack = normalize(candidate);
    let needle = normalize(query);
    if (!caseSensitive) {{
      haystack = haystack.toLowerCase();
      needle = needle.toLowerCase();
    }}
    return exact ? haystack === needle : haystack.includes(needle);
  }};
  const limited = (nodes) => maxNodes === null ? nodes : nodes.slice(0, maxNodes);
""".rstrip()


def _label_control_helpers_expression() -> str:
    return """
  const findFieldByLabel = (requestedLabel, exact, caseSensitive, fieldSelector) => {
    const labelElements = [...document.querySelectorAll("label")].filter(visible);
    for (const labelElement of labelElements) {
      if (!matchesText(textOf(labelElement), requestedLabel, exact, caseSensitive)) {
        continue;
      }
      let element = null;
      if (labelElement.htmlFor) {
        element = document.getElementById(labelElement.htmlFor);
      }
      element ||= labelElement.querySelector(fieldSelector);
      if (element) {
        return { element, label_element: nodeInfo(labelElement) };
      }
    }
    const element = [...document.querySelectorAll(fieldSelector)]
      .filter(visible)
      .find((candidate) =>
        matchesText(accessibleName(candidate), requestedLabel, exact, caseSensitive)
      );
    return { element: element || null, label_element: null };
  };
""".rstrip()


def _click_text_expression(
    *,
    text: str,
    selector: str | None,
    exact: bool,
    case_sensitive: bool,
) -> str:
    selector_source = (
        "interactiveSelector" if selector is None else _js_literal(selector)
    )
    return f"""
() => {{
{_dom_helpers_expression()}
  const requestedText = {_js_literal(text)};
  const selector = {selector_source};
  const candidates = [...document.querySelectorAll(selector)].filter(visible);
  const element = candidates.find((candidate) =>
    matchesText(accessibleName(candidate), requestedText, {_js_literal(exact)}, {_js_literal(case_sensitive)})
  );
  if (!element) {{
    return {{
      found: false,
      clicked: false,
      text: requestedText,
      selector,
      candidate_count: candidates.length,
      candidates: candidates.slice(0, 20).map(nodeInfo)
    }};
  }}
  element.focus?.();
  element.click();
  return {{
    found: true,
    clicked: true,
    text: requestedText,
    selector,
    element: nodeInfo(element)
  }};
}}
""".strip()


def _click_role_expression(
    *,
    role: str,
    name: str | None,
    exact: bool,
    case_sensitive: bool,
) -> str:
    name_source = "null" if name is None else _js_literal(name)
    return f"""
() => {{
{_dom_helpers_expression()}
  const requestedRole = {_js_literal(role)};
  const requestedName = {name_source};
  const exact = {_js_literal(exact)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const candidates = [...document.querySelectorAll(interactiveSelector)].filter(visible);
  const roleMatches = candidates.filter((candidate) => roleOf(candidate) === requestedRole);
  const element = roleMatches.find((candidate) =>
    requestedName === null ||
    matchesText(accessibleName(candidate), requestedName, exact, caseSensitive)
  );
  if (!element) {{
    return {{
      found: false,
      clicked: false,
      role: requestedRole,
      name: requestedName,
      candidate_count: roleMatches.length,
      candidates: roleMatches.slice(0, 20).map(nodeInfo)
    }};
  }}
  element.focus?.();
  element.click();
  return {{
    found: true,
    clicked: true,
    role: requestedRole,
    name: requestedName,
    element: nodeInfo(element)
  }};
}}
""".strip()


def _fill_label_expression(
    *,
    label: str,
    text: str,
    exact: bool,
    case_sensitive: bool,
) -> str:
    return f"""
() => {{
{_dom_helpers_expression()}
{_label_control_helpers_expression()}
  const requestedLabel = {_js_literal(label)};
  const text = {_js_literal(text)};
  const exact = {_js_literal(exact)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const fieldSelector = "input:not([type=hidden]), textarea, select, [contenteditable='true']";
  const match = findFieldByLabel(requestedLabel, exact, caseSensitive, fieldSelector);
  const element = match.element;
  if (!element) {{
    return {{ found: false, filled: false, label: requestedLabel, text }};
  }}
  const previousValue = element.isContentEditable ? element.textContent : element.value;
  if (element.isContentEditable) {{
    element.textContent = text;
  }} else {{
    element.value = text;
  }}
  element.dispatchEvent(new Event("input", {{ bubbles: true }}));
  element.dispatchEvent(new Event("change", {{ bubbles: true }}));
  return {{
    found: true,
    filled: true,
    label: requestedLabel,
    text,
    previous_value: previousValue,
    value: element.isContentEditable ? element.textContent : element.value,
    label_element: match.label_element,
    element: nodeInfo(element)
  }};
}}
""".strip()


def _check_label_expression(
    *,
    label: str,
    checked: bool,
    exact: bool,
    case_sensitive: bool,
) -> str:
    return f"""
() => {{
{_dom_helpers_expression()}
{_label_control_helpers_expression()}
  const requestedLabel = {_js_literal(label)};
  const requestedChecked = {_js_literal(checked)};
  const exact = {_js_literal(exact)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const fieldSelector = [
    "input[type=checkbox]",
    "input[type=radio]",
    "[role~=checkbox]",
    "[role~=radio]",
    "[role~=switch]",
    "[aria-checked]"
  ].join(",");
  const match = findFieldByLabel(requestedLabel, exact, caseSensitive, fieldSelector);
  const element = match.element;
  if (!element) {{
    return {{
      found: false,
      checkable: false,
      label: requestedLabel,
      requested_checked: requestedChecked
    }};
  }}
  const hasNativeChecked = "checked" in element;
  const ariaCheckable = element.hasAttribute("aria-checked") ||
    ["checkbox", "radio", "switch"].includes(roleOf(element));
  if (!hasNativeChecked && !ariaCheckable) {{
    return {{
      found: true,
      checkable: false,
      label: requestedLabel,
      requested_checked: requestedChecked,
      element: nodeInfo(element),
      label_element: match.label_element
    }};
  }}
  const previousChecked = hasNativeChecked
    ? Boolean(element.checked)
    : element.getAttribute("aria-checked") === "true";
  if (hasNativeChecked) {{
    element.checked = requestedChecked;
  }} else {{
    element.setAttribute("aria-checked", requestedChecked ? "true" : "false");
  }}
  element.dispatchEvent(new Event("input", {{ bubbles: true }}));
  element.dispatchEvent(new Event("change", {{ bubbles: true }}));
  const currentChecked = hasNativeChecked
    ? Boolean(element.checked)
    : element.getAttribute("aria-checked") === "true";
  return {{
    found: true,
    checkable: true,
    label: requestedLabel,
    requested_checked: requestedChecked,
    previous_checked: previousChecked,
    checked: currentChecked,
    changed: previousChecked !== currentChecked,
    element: nodeInfo(element),
    label_element: match.label_element
  }};
}}
""".strip()


def _select_label_expression(
    *,
    label: str,
    value: str | None,
    option_label: str | None,
    exact: bool,
    case_sensitive: bool,
) -> str:
    return f"""
() => {{
{_dom_helpers_expression()}
{_label_control_helpers_expression()}
  const requestedLabel = {_js_literal(label)};
  const requestedValueInput = {_js_literal(value)};
  const requestedOptionLabel = {_js_literal(option_label)};
  const exact = {_js_literal(exact)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const match = findFieldByLabel(requestedLabel, exact, caseSensitive, "select");
  const element = match.element;
  if (!element) {{
    return {{
      found: false,
      selectable: false,
      selected: false,
      label: requestedLabel,
      requested_value: requestedValueInput,
      requested_option_label: requestedOptionLabel
    }};
  }}
  if (element.tagName.toLowerCase() !== "select") {{
    return {{
      found: true,
      selectable: false,
      selected: false,
      label: requestedLabel,
      requested_value: requestedValueInput,
      requested_option_label: requestedOptionLabel,
      element: nodeInfo(element),
      label_element: match.label_element
    }};
  }}
  const options = [...element.options];
  const previousValue = element.value;
  const previousOptionLabel = element.selectedOptions[0]
    ? textOf(element.selectedOptions[0])
    : null;
  let requestedValue = requestedValueInput;
  let optionFound = null;
  if (requestedOptionLabel !== null) {{
    const option = options.find((candidate) =>
      matchesText(textOf(candidate), requestedOptionLabel, exact, caseSensitive)
    );
    optionFound = Boolean(option);
    if (!option) {{
      return {{
        found: true,
        selectable: true,
        selected: false,
        label: requestedLabel,
        requested_value: null,
        requested_option_label: requestedOptionLabel,
        option_found: false,
        value: element.value,
        previous_value: previousValue,
        previous_option_label: previousOptionLabel,
        element: nodeInfo(element),
        label_element: match.label_element
      }};
    }}
    requestedValue = option.value;
  }}
  element.value = requestedValue;
  element.dispatchEvent(new Event("input", {{ bubbles: true }}));
  element.dispatchEvent(new Event("change", {{ bubbles: true }}));
  const selectedOption = element.selectedOptions[0] || null;
  return {{
    found: true,
    selectable: true,
    selected: element.value === requestedValue,
    label: requestedLabel,
    requested_value: requestedValue,
    requested_option_label: requestedOptionLabel,
    option_found: optionFound,
    value: element.value,
    option_label: selectedOption ? textOf(selectedOption) : null,
    previous_value: previousValue,
    previous_option_label: previousOptionLabel,
    changed: previousValue !== element.value,
    element: nodeInfo(element),
    label_element: match.label_element
  }};
}}
""".strip()


def _click_index_expression(
    *,
    selector: str,
    index: int,
    include_hidden: bool,
) -> str:
    return f"""
() => {{
{_dom_helpers_expression(include_hidden=include_hidden)}
  const selector = {_js_literal(selector)};
  const index = {_js_literal(index)};
  const all = [...document.querySelectorAll(selector)];
  const visibleNodes = all.filter(visible);
  const candidates = includeHidden ? all : visibleNodes;
  const element = candidates[index] || null;
  if (!element) {{
    return {{
      selector,
      index,
      include_hidden: includeHidden,
      found: false,
      clicked: false,
      count: candidates.length,
      total_count: all.length,
      visible_count: visibleNodes.length,
      candidates: candidates.slice(0, 20).map(nodeInfo)
    }};
  }}
  element.focus?.();
  element.click();
  return {{
    selector,
    index,
    include_hidden: includeHidden,
    found: true,
    clicked: true,
    count: candidates.length,
    total_count: all.length,
    visible_count: visibleNodes.length,
    element: nodeInfo(element)
  }};
}}
""".strip()


def _form_snapshot_expression(
    *,
    selector: str | None,
    include_hidden: bool,
    max_nodes: int,
    reveal_sensitive_values: bool,
) -> str:
    selector_source = "null" if selector is None else _js_literal(selector)
    return f"""
() => {{
{_dom_helpers_expression(include_hidden=include_hidden, max_nodes=max_nodes)}
{_form_value_helpers_expression()}
  const rootSelector = {selector_source};
  const revealSensitiveValues = {_js_literal(reveal_sensitive_values)};
  const fieldSelector = "input, textarea, select, [contenteditable='true']";
  const roots = rootSelector === null
    ? [document.body || document.documentElement].filter(Boolean)
    : [...document.querySelectorAll(rootSelector)];
  const fields = [];
  const seen = new Set();
  for (const root of roots) {{
    const candidates = [
      ...(root.matches?.(fieldSelector) ? [root] : []),
      ...root.querySelectorAll(fieldSelector)
    ];
    for (const field of candidates) {{
      if (seen.has(field) || !visible(field)) continue;
      seen.add(field);
      fields.push(field);
    }}
  }}
  const labelTexts = (field) => {{
    const labels = field.labels ? [...field.labels] : [];
    const closestLabel = field.closest?.("label");
    if (closestLabel && !labels.includes(closestLabel)) labels.push(closestLabel);
    return labels.map((label) => textOf(label)).filter(Boolean);
  }};
  const optionsOf = (field) => field.tagName.toLowerCase() === "select"
    ? [...field.options].map((option) => ({{
        value: option.value,
        text: textOf(option),
        selected: Boolean(option.selected),
        disabled: Boolean(option.disabled)
      }}))
    : null;
  const fieldInfo = (field) => {{
    const info = nodeInfo(field);
    const tag = field.tagName.toLowerCase();
    const type = String(field.getAttribute("type") || "").toLowerCase();
    const rawValue = readFormValue(field);
    const sensitive = tag === "input" && (type === "password" || type === "hidden");
    const valuePayload = sensitive && !revealSensitiveValues
      ? {{
          ...rawValue,
          value: rawValue.value === null || rawValue.value === "" ? rawValue.value : "***",
          value_masked: rawValue.value !== null && rawValue.value !== "",
          value_length: String(rawValue.value ?? "").length
        }}
      : {{ ...rawValue, value_masked: false }};
    return {{
      ...info,
      type: type || null,
      id: field.id || null,
      name_attribute: field.getAttribute("name"),
      labels: labelTexts(field),
      placeholder: field.getAttribute("placeholder"),
      disabled: Boolean(field.disabled),
      required: Boolean(field.required),
      readonly: Boolean(field.readOnly),
      autocomplete: field.getAttribute("autocomplete"),
      options: optionsOf(field),
      ...valuePayload
    }};
  }};
  const visibleFields = fields.filter(visible);
  const nodes = limited(fields).map(fieldInfo);
  return {{
    url: location.href,
    title: document.title,
    kind: "form",
    selector: rootSelector,
    include_hidden: includeHidden,
    node_count: nodes.length,
    field_count: fields.length,
    visible_count: visibleFields.length,
    truncated: maxNodes !== null && fields.length > nodes.length,
    fields: nodes
  }};
}}
""".strip()


def _wait_text_expression(
    *,
    text: str,
    selector: str | None,
    exact: bool,
    case_sensitive: bool,
    timeout_ms: float,
    poll_ms: float,
    include_hidden: bool,
) -> str:
    selector_source = "null" if selector is None else _js_literal(selector)
    return f"""
() => new Promise((resolve) => {{
{_dom_helpers_expression(include_hidden=include_hidden)}
  const requestedText = {_js_literal(text)};
  const selector = {selector_source};
  const exact = {_js_literal(exact)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const startedAt = Date.now();
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  const candidates = () => {{
    const roots = selector === null
      ? [document.body || document.documentElement].filter(Boolean)
      : [...document.querySelectorAll(selector)];
    return roots.filter(visible);
  }};
  const check = () => {{
    const nodes = candidates();
    const element = nodes.find((candidate) =>
      matchesText(textOf(candidate), requestedText, exact, caseSensitive) ||
      matchesText(accessibleName(candidate), requestedText, exact, caseSensitive)
    );
    const waitedMs = Date.now() - startedAt;
    if (element) {{
      resolve({{
        found: true,
        text: requestedText,
        selector,
        waited_ms: waitedMs,
        candidate_count: nodes.length,
        element: nodeInfo(element)
      }});
      return;
    }}
    if (waitedMs >= timeoutMs) {{
      resolve({{
        found: false,
        text: requestedText,
        selector,
        waited_ms: waitedMs,
        candidate_count: nodes.length
      }});
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _wait_count_expression(
    *,
    selector: str,
    count: int,
    comparison: str,
    timeout_ms: float,
    poll_ms: float,
    include_hidden: bool,
) -> str:
    return f"""
() => new Promise((resolve) => {{
{_dom_helpers_expression(include_hidden=include_hidden)}
  const selector = {_js_literal(selector)};
  const requestedCount = Math.max(0, {_js_literal(count)});
  const comparison = {_js_literal(comparison)};
  const startedAt = Date.now();
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  const compare = (actual, expected) => {{
    if (comparison === "eq") return actual === expected;
    if (comparison === "gt") return actual > expected;
    if (comparison === "gte") return actual >= expected;
    if (comparison === "lt") return actual < expected;
    if (comparison === "lte") return actual <= expected;
    return false;
  }};
  const check = () => {{
    const all = [...document.querySelectorAll(selector)];
    const visibleNodes = all.filter(visible);
    const matched = includeHidden ? all : visibleNodes;
    const waitedMs = Date.now() - startedAt;
    const reached = compare(matched.length, requestedCount);
    if (reached) {{
      resolve({{
        selector,
        found: true,
        count: matched.length,
        requested_count: requestedCount,
        comparison,
        include_hidden: includeHidden,
        total_count: all.length,
        visible_count: visibleNodes.length,
        waited_ms: waitedMs
      }});
      return;
    }}
    if (waitedMs >= timeoutMs) {{
      resolve({{
        selector,
        found: false,
        count: matched.length,
        requested_count: requestedCount,
        comparison,
        include_hidden: includeHidden,
        total_count: all.length,
        visible_count: visibleNodes.length,
        waited_ms: waitedMs
      }});
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _wait_state_expression(
    *,
    selector: str,
    state: str,
    timeout_ms: float,
    poll_ms: float,
) -> str:
    return f"""
() => new Promise((resolve) => {{
{_dom_helpers_expression()}
  const selector = {_js_literal(selector)};
  const requestedState = {_js_literal(state)};
  const startedAt = Date.now();
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  const ariaTrue = (element, name) => element.getAttribute(name) === "true";
  const ariaFalse = (element, name) => element.getAttribute(name) === "false";
  const elementState = (element) => {{
    if (!element) {{
      return {{
        found: false,
        element: null,
        state_values: {{
          attached: false,
          detached: true,
          visible: false,
          hidden: true,
          enabled: false,
          disabled: null,
          editable: false,
          readonly: null,
          checked: null,
          unchecked: null,
          focused: false,
          in_viewport: null,
          out_of_viewport: null
        }}
      }};
    }}
    const rect = element.getBoundingClientRect();
    const inViewport = rect.bottom >= 0 &&
      rect.right >= 0 &&
      rect.top <= window.innerHeight &&
      rect.left <= window.innerWidth;
    const disabled = Boolean(element.disabled) || ariaTrue(element, "aria-disabled");
    const readonly = Boolean(element.readOnly) || ariaTrue(element, "aria-readonly");
    let checked = null;
    if ("checked" in element) {{
      checked = Boolean(element.checked);
    }} else if (ariaTrue(element, "aria-checked")) {{
      checked = true;
    }} else if (ariaFalse(element, "aria-checked")) {{
      checked = false;
    }}
    const visibleState = visible(element);
    const tag = element.tagName.toLowerCase();
    const editable = Boolean(
      element.isContentEditable ||
      (["input", "textarea", "select"].includes(tag) && !disabled && !readonly)
    );
    return {{
      found: true,
      element: nodeInfo(element),
      state_values: {{
        attached: true,
        detached: false,
        visible: visibleState,
        hidden: !visibleState,
        enabled: !disabled,
        disabled,
        editable,
        readonly,
        checked,
        unchecked: checked === null ? null : !checked,
        focused: document.activeElement === element,
        in_viewport: inViewport,
        out_of_viewport: !inViewport
      }}
    }};
  }};
  const stateMatches = (stateValues) => {{
    const normalizedState = requestedState.replace(/-/g, "_");
    return Boolean(stateValues[normalizedState]);
  }};
  const check = () => {{
    const element = document.querySelector(selector);
    const result = elementState(element);
    const waitedMs = Date.now() - startedAt;
    const matched = stateMatches(result.state_values);
    if (matched || waitedMs >= timeoutMs) {{
      resolve({{
        selector,
        state: requestedState,
        found: result.found,
        matched,
        waited_ms: waitedMs,
        timeout_ms: timeoutMs,
        poll_ms: pollMs,
        state_values: result.state_values,
        element: result.element
      }});
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _wait_attribute_expression(
    *,
    selector: str,
    name: str,
    value: str | None,
    state: str,
    match: str,
    timeout_ms: float,
    poll_ms: float,
    case_sensitive: bool,
) -> str:
    value_source = "null" if value is None else _js_literal(value)
    return f"""
() => new Promise((resolve) => {{
{_dom_helpers_expression()}
  const selector = {_js_literal(selector)};
  const name = {_js_literal(name)};
  const requestedValue = {value_source};
  const requestedState = {_js_literal(state)};
  const matchMode = {_js_literal(match)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const startedAt = Date.now();
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  let pattern = null;
  if (requestedValue !== null && matchMode === "regex") {{
    try {{
      pattern = new RegExp(requestedValue, caseSensitive ? "" : "i");
    }} catch (error) {{
      resolve({{
        selector,
        name,
        found: false,
        state: requestedState,
        selector_found: Boolean(document.querySelector(selector)),
        attribute_found: null,
        value: null,
        requested_value: requestedValue,
        match: matchMode,
        waited_ms: 0,
        error: "invalid_regex",
        message: String(error.message || error)
      }});
      return;
    }}
  }}
  const matches = (currentValue) => {{
    if (requestedValue === null) return true;
    const candidate = String(currentValue ?? "");
    if (matchMode === "regex") return pattern.test(candidate);
    if (caseSensitive) {{
      return matchMode === "exact"
        ? candidate === requestedValue
        : candidate.includes(requestedValue);
    }}
    const haystack = candidate.toLowerCase();
    const needle = requestedValue.toLowerCase();
    return matchMode === "exact" ? haystack === needle : haystack.includes(needle);
  }};
  const check = () => {{
    const element = document.querySelector(selector);
    const waitedMs = Date.now() - startedAt;
    if (!element) {{
      if (waitedMs >= timeoutMs) {{
        resolve({{
          selector,
          name,
          found: false,
          state: requestedState,
          selector_found: false,
          attribute_found: null,
          value: null,
          requested_value: requestedValue,
          match: matchMode,
          waited_ms: waitedMs
        }});
        return;
      }}
      setTimeout(check, pollMs);
      return;
    }}
    const currentValue = element.getAttribute(name);
    const attributeFound = currentValue !== null;
    const reached = requestedState === "absent"
      ? !attributeFound
      : attributeFound && matches(currentValue);
    if (reached) {{
      resolve({{
        selector,
        name,
        found: true,
        state: requestedState,
        selector_found: true,
        attribute_found: attributeFound,
        value: currentValue,
        requested_value: requestedValue,
        match: matchMode,
        waited_ms: waitedMs,
        element: nodeInfo(element)
      }});
      return;
    }}
    if (waitedMs >= timeoutMs) {{
      resolve({{
        selector,
        name,
        found: false,
        state: requestedState,
        selector_found: true,
        attribute_found: attributeFound,
        value: currentValue,
        requested_value: requestedValue,
        match: matchMode,
        waited_ms: waitedMs,
        element: nodeInfo(element)
      }});
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _query_expression(
    *,
    selector: str,
    include_hidden: bool,
    max_nodes: int,
) -> str:
    return f"""
() => {{
{_dom_helpers_expression(include_hidden=include_hidden, max_nodes=max_nodes)}
  const selector = {_js_literal(selector)};
  const all = [...document.querySelectorAll(selector)];
  const visibleNodes = all.filter(visible);
  const matched = includeHidden ? all : visibleNodes;
  const nodes = limited(matched).map(nodeInfo);
  return {{
    selector,
    kind: "query",
    include_hidden: includeHidden,
    count: matched.length,
    total_count: all.length,
    visible_count: visibleNodes.length,
    node_count: nodes.length,
    truncated: maxNodes !== null && matched.length > nodes.length,
    nodes
  }};
}}
""".strip()


def _inspect_expression(
    *,
    selector: str,
    include_html: bool,
    max_html_chars: int,
    reveal_sensitive_values: bool,
) -> str:
    rect_object = _rect_object_expression("rect")
    return f"""
() => {{
{_dom_helpers_expression()}
{_form_value_helpers_expression()}
  const selector = {_js_literal(selector)};
  const includeHtml = {_js_literal(include_html)};
  const maxHtmlChars = Math.max(0, {_js_literal(max_html_chars)});
  const revealSensitiveValues = {_js_literal(reveal_sensitive_values)};
  const element = document.querySelector(selector);
  if (!element) {{
    return {{ selector, found: false }};
  }}
  const tag = element.tagName.toLowerCase();
  const type = String(element.getAttribute("type") || "").toLowerCase();
  const sensitiveField = tag === "input" && ["password", "hidden"].includes(type);
  const sensitiveAttributeName = (name) =>
    /api[-_]?key|authorization|password|secret|token/i.test(String(name || ""));
  const valuePayload = () => {{
    if (!("value" in element) && !element.isContentEditable) {{
      return {{ value: null, value_masked: false, value_length: null }};
    }}
    const raw = readFormValue(element);
    if (sensitiveField && !revealSensitiveValues) {{
      const value = raw.value === null || raw.value === "" ? raw.value : "***";
      return {{
        ...raw,
        value,
        value_masked: raw.value !== null && raw.value !== "",
        value_length: String(raw.value ?? "").length
      }};
    }}
    return {{ ...raw, value_masked: false, value_length: String(raw.value ?? "").length }};
  }};
  const attributeValue = (attribute) => {{
    if (!revealSensitiveValues && (
      sensitiveAttributeName(attribute.name) ||
      (sensitiveField && attribute.name.toLowerCase() === "value")
    )) {{
      return attribute.value === "" ? "" : "***";
    }}
    return attribute.value;
  }};
  const attributes = Object.fromEntries(
    [...element.attributes].map((attribute) => [
      attribute.name,
      attributeValue(attribute)
    ])
  );
  const selectedOptions = "selectedOptions" in element
    ? [...element.selectedOptions].map((option) => ({{
        value: option.value,
        text: textOf(option)
      }}))
    : null;
  const options = tag === "select"
    ? [...element.options].slice(0, 50).map((option) => ({{
        value: option.value,
        text: textOf(option),
        selected: Boolean(option.selected),
        disabled: Boolean(option.disabled)
      }}))
    : null;
  const rect = element.getBoundingClientRect();
  const center = {{
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2
  }};
  const inViewport = rect.bottom >= 0 &&
    rect.right >= 0 &&
    rect.top <= window.innerHeight &&
    rect.left <= window.innerWidth;
  const sanitizeHtml = (root) => {{
    const clone = root.cloneNode(true);
    const nodes = [clone, ...clone.querySelectorAll("*")];
    for (const node of nodes) {{
      const nodeTag = node.tagName.toLowerCase();
      const nodeType = String(node.getAttribute("type") || "").toLowerCase();
      const nodeSensitive = nodeTag === "input" && ["password", "hidden"].includes(nodeType);
      for (const attribute of [...node.attributes]) {{
        if (
          sensitiveAttributeName(attribute.name) ||
          (nodeSensitive && attribute.name.toLowerCase() === "value")
        ) {{
          node.setAttribute(attribute.name, attribute.value === "" ? "" : "***");
        }}
      }}
    }}
    return clone.outerHTML;
  }};
  let html = null;
  let htmlLength = null;
  let htmlTruncated = false;
  if (includeHtml) {{
    html = sanitizeHtml(element);
    htmlLength = html.length;
    htmlTruncated = html.length > maxHtmlChars;
    html = html.slice(0, maxHtmlChars);
  }}
  return {{
    selector,
    found: true,
    element: nodeInfo(element),
    attributes,
    state: {{
      visible: visible(element),
      focused: document.activeElement === element,
      disabled: Boolean(element.disabled),
      readonly: Boolean(element.readOnly),
      required: Boolean(element.required),
      checked: "checked" in element ? Boolean(element.checked) : null,
      selected: "selected" in element ? Boolean(element.selected) : null,
      multiple: "multiple" in element ? Boolean(element.multiple) : null,
      contenteditable: element.isContentEditable
    }},
    ...valuePayload(),
    selected_options: selectedOptions,
    options,
    option_count: tag === "select" ? element.options.length : null,
    visible: visible(element),
    in_viewport: inViewport,
    bounding_box: {rect_object},
    center,
    viewport: {{
      width: window.innerWidth,
      height: window.innerHeight,
      scroll_x: window.scrollX,
      scroll_y: window.scrollY
    }},
    html,
    html_length: htmlLength,
    html_truncated: htmlTruncated
  }};
}}
""".strip()


def _reload_expression() -> str:
    return """
() => {
  const beforeUrl = location.href;
  const beforeTitle = document.title;
  setTimeout(() => window.location.reload(), 0);
  return {
    action: "reload",
    navigation_requested: true,
    reloaded: true,
    before_url: beforeUrl,
    url: beforeUrl,
    title: beforeTitle
  };
}
""".strip()


def _history_expression(action: str) -> str:
    method = "back" if action == "back" else "forward"
    return f"""
() => {{
  const beforeUrl = location.href;
  const beforeTitle = document.title;
  const historyLength = history.length;
  setTimeout(() => history.{method}(), 0);
  return {{
    action: {_js_literal(action)},
    navigation_requested: true,
    before_url: beforeUrl,
    url: beforeUrl,
    title: beforeTitle,
    history_length: historyLength
  }};
}}
""".strip()


def _wait_url_expression(
    *,
    url: str,
    match: str,
    timeout_ms: float,
    poll_ms: float,
) -> str:
    return f"""
() => new Promise((resolve) => {{
  const requestedUrl = {_js_literal(url)};
  const matchMode = {_js_literal(match)};
  const startedAt = Date.now();
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  let pattern = null;
  if (matchMode === "regex") {{
    try {{
      pattern = new RegExp(requestedUrl);
    }} catch (error) {{
      resolve({{
        found: false,
        url: location.href,
        requested_url: requestedUrl,
        match: matchMode,
        waited_ms: 0,
        error: "invalid_regex",
        message: String(error.message || error)
      }});
      return;
    }}
  }}
  const matches = (candidate) => {{
    if (matchMode === "exact") return candidate === requestedUrl;
    if (matchMode === "regex") return pattern.test(candidate);
    return candidate.includes(requestedUrl);
  }};
  const check = () => {{
    const currentUrl = location.href;
    const waitedMs = Date.now() - startedAt;
    if (matches(currentUrl)) {{
      resolve({{
        found: true,
        url: currentUrl,
        requested_url: requestedUrl,
        match: matchMode,
        waited_ms: waitedMs
      }});
      return;
    }}
    if (waitedMs >= timeoutMs) {{
      resolve({{
        found: false,
        url: currentUrl,
        requested_url: requestedUrl,
        match: matchMode,
        waited_ms: waitedMs
      }});
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _wait_load_state_expression(
    *,
    state: str,
    timeout_ms: float,
    poll_ms: float,
) -> str:
    return f"""
() => new Promise((resolve) => {{
  const requestedState = {_js_literal(state)};
  const stateAliases = {{
    domcontentloaded: "interactive",
    load: "complete"
  }};
  const targetState = stateAliases[requestedState] || requestedState;
  const ranks = {{ loading: 0, interactive: 1, complete: 2 }};
  const startedAt = Date.now();
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  if (!(targetState in ranks)) {{
    resolve({{
      found: false,
      state: document.readyState,
      requested_state: requestedState,
      target_state: targetState,
      waited_ms: 0,
      error: "invalid_state"
    }});
    return;
  }}
  const check = () => {{
    const currentState = document.readyState;
    const waitedMs = Date.now() - startedAt;
    if (ranks[currentState] >= ranks[targetState]) {{
      resolve({{
        found: true,
        state: currentState,
        requested_state: requestedState,
        target_state: targetState,
        waited_ms: waitedMs
      }});
      return;
    }}
    if (waitedMs >= timeoutMs) {{
      resolve({{
        found: false,
        state: currentState,
        requested_state: requestedState,
        target_state: targetState,
        waited_ms: waitedMs
      }});
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _wait_network_idle_expression(
    *,
    idle_ms: float,
    timeout_ms: float,
    poll_ms: float,
    max_inflight: int,
) -> str:
    return f"""
() => new Promise((resolve) => {{
  const startedAt = Date.now();
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const idleMs = Math.max(0, {_js_literal(idle_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  const maxInflight = Math.max(0, {_js_literal(max_inflight)});
  let pendingRequests = 0;
  let observedRequestCount = 0;
  let observedResponseCount = 0;
  let observedFailureCount = 0;
  let observedResourceCount = 0;
  let lastActivityAt = Date.now();
  let observerAvailable = false;
  let fetchInstrumented = false;
  let xhrInstrumented = false;
  let observer = null;
  const originalFetch = window.fetch;
  const originalXhrSend = window.XMLHttpRequest?.prototype?.send;

  const markActivity = () => {{
    lastActivityAt = Date.now();
  }};
  const incrementPending = () => {{
    pendingRequests += 1;
    observedRequestCount += 1;
    markActivity();
  }};
  const decrementPending = (failed) => {{
    pendingRequests = Math.max(0, pendingRequests - 1);
    if (failed) observedFailureCount += 1;
    else observedResponseCount += 1;
    markActivity();
  }};
  const cleanup = () => {{
    if (observer) {{
      try {{ observer.disconnect(); }} catch (error) {{}}
    }}
    if (fetchInstrumented) {{
      window.fetch = originalFetch;
    }}
    if (xhrInstrumented) {{
      window.XMLHttpRequest.prototype.send = originalXhrSend;
    }}
  }};

  try {{
    if (typeof PerformanceObserver === "function") {{
      observer = new PerformanceObserver((list) => {{
        const entries = list.getEntries();
        if (entries.length) {{
          observedResourceCount += entries.length;
          markActivity();
        }}
      }});
      observer.observe({{ type: "resource", buffered: false }});
      observerAvailable = true;
    }}
  }} catch (error) {{
    observerAvailable = false;
  }}

  try {{
    if (typeof originalFetch === "function") {{
      window.fetch = (...args) => {{
        incrementPending();
        return originalFetch.apply(window, args).then(
          (response) => {{
            decrementPending(false);
            return response;
          }},
          (error) => {{
            decrementPending(true);
            throw error;
          }}
        );
      }};
      fetchInstrumented = true;
    }}
  }} catch (error) {{
    fetchInstrumented = false;
  }}

  try {{
    if (typeof originalXhrSend === "function") {{
      window.XMLHttpRequest.prototype.send = function(...args) {{
        incrementPending();
        this.addEventListener(
          "loadend",
          () => decrementPending(false),
          {{ once: true }}
        );
        try {{
          return originalXhrSend.apply(this, args);
        }} catch (error) {{
          decrementPending(true);
          throw error;
        }}
      }};
      xhrInstrumented = true;
    }}
  }} catch (error) {{
    xhrInstrumented = false;
  }}

  const finish = (found) => {{
    const now = Date.now();
    const waitedMs = now - startedAt;
    const quietMs = now - lastActivityAt;
    cleanup();
    resolve({{
      found,
      network_idle: found,
      idle_ms: idleMs,
      quiet_ms: quietMs,
      waited_ms: waitedMs,
      pending_requests: pendingRequests,
      max_inflight: maxInflight,
      observed_request_count: observedRequestCount,
      observed_response_count: observedResponseCount,
      observed_failure_count: observedFailureCount,
      observed_resource_count: observedResourceCount,
      observer_available: observerAvailable,
      fetch_instrumented: fetchInstrumented,
      xhr_instrumented: xhrInstrumented
    }});
  }};

  const check = () => {{
    const now = Date.now();
    const waitedMs = now - startedAt;
    const quietMs = now - lastActivityAt;
    if (pendingRequests <= maxInflight && quietMs >= idleMs) {{
      finish(true);
      return;
    }}
    if (waitedMs >= timeoutMs) {{
      finish(false);
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _focus_expression(*, selector: str, prevent_scroll: bool) -> str:
    return _selector_expression(
        selector,
        f"""
  element.focus({{ preventScroll: {_js_literal(prevent_scroll)} }});
  return {{
    selector,
    found: true,
    focused: document.activeElement === element,
    prevent_scroll: {_js_literal(prevent_scroll)}
  }};
""".rstrip(),
    )


def _form_value_helpers_expression() -> str:
    return """
  const readFormValue = (node) => {
    const tag = node.tagName.toLowerCase();
    const type = String(node.getAttribute("type") || "").toLowerCase();
    if (tag === "input" && ["checkbox", "radio"].includes(type)) {
      return {
        readable: true,
        value: Boolean(node.checked),
        value_type: "checked",
        checked: Boolean(node.checked)
      };
    }
    if (tag === "select") {
      const selectedOptions = [...node.selectedOptions].map((option) => ({
        value: option.value,
        text: textOf(option)
      }));
      return {
        readable: true,
        value: node.multiple ? selectedOptions.map((option) => option.value) : node.value,
        value_type: node.multiple ? "selected_values" : "value",
        selected_options: selectedOptions,
        multiple: Boolean(node.multiple)
      };
    }
    if (node.isContentEditable) {
      return {
        readable: true,
        value: node.textContent ?? "",
        value_type: "text_content"
      };
    }
    if ("value" in node) {
      return {
        readable: true,
        value: node.value ?? "",
        value_type: "value"
      };
    }
    if ("checked" in node) {
      return {
        readable: true,
        value: Boolean(node.checked),
        value_type: "checked",
        checked: Boolean(node.checked)
      };
    }
    return { readable: false, value: null, value_type: null };
  };
  const formValueText = (currentValue) => Array.isArray(currentValue)
    ? currentValue.join(",")
    : String(currentValue ?? "");
""".rstrip()


def _get_value_expression(selector: str) -> str:
    return f"""
() => {{
{_dom_helpers_expression()}
{_form_value_helpers_expression()}
  const selector = {_js_literal(selector)};
  const element = document.querySelector(selector);
  if (!element) {{
    return {{ selector, found: false, readable: false, value: null }};
  }}
  return {{
    selector,
    found: true,
    ...readFormValue(element),
    element: nodeInfo(element)
  }};
}}
""".strip()


def _wait_value_expression(
    *,
    selector: str,
    value: str,
    match: str,
    timeout_ms: float,
    poll_ms: float,
    case_sensitive: bool,
) -> str:
    return f"""
() => new Promise((resolve) => {{
{_dom_helpers_expression()}
{_form_value_helpers_expression()}
  const selector = {_js_literal(selector)};
  const requestedValue = {_js_literal(value)};
  const matchMode = {_js_literal(match)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const startedAt = Date.now();
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  let pattern = null;
  if (matchMode === "regex") {{
    try {{
      pattern = new RegExp(requestedValue, caseSensitive ? "" : "i");
    }} catch (error) {{
      resolve({{
        selector,
        found: false,
        selector_found: Boolean(document.querySelector(selector)),
        value: null,
        requested_value: requestedValue,
        match: matchMode,
        waited_ms: 0,
        error: "invalid_regex",
        message: String(error.message || error)
      }});
      return;
    }}
  }}
  const matches = (currentValue) => {{
    const candidate = formValueText(currentValue);
    if (matchMode === "regex") return pattern.test(candidate);
    if (caseSensitive) {{
      return matchMode === "exact"
        ? candidate === requestedValue
        : candidate.includes(requestedValue);
    }}
    const haystack = candidate.toLowerCase();
    const needle = requestedValue.toLowerCase();
    return matchMode === "exact" ? haystack === needle : haystack.includes(needle);
  }};
  const check = () => {{
    const element = document.querySelector(selector);
    const waitedMs = Date.now() - startedAt;
    if (!element) {{
      if (waitedMs >= timeoutMs) {{
        resolve({{
          selector,
          found: false,
          selector_found: false,
          value: null,
          requested_value: requestedValue,
          match: matchMode,
          waited_ms: waitedMs
        }});
        return;
      }}
      setTimeout(check, pollMs);
      return;
    }}
    const state = readFormValue(element);
    if (!state.readable) {{
      resolve({{
        selector,
        found: false,
        selector_found: true,
        ...state,
        requested_value: requestedValue,
        match: matchMode,
        waited_ms: waitedMs,
        element: nodeInfo(element)
      }});
      return;
    }}
    if (matches(state.value)) {{
      resolve({{
        selector,
        found: true,
        selector_found: true,
        ...state,
        requested_value: requestedValue,
        match: matchMode,
        waited_ms: waitedMs,
        element: nodeInfo(element)
      }});
      return;
    }}
    if (waitedMs >= timeoutMs) {{
      resolve({{
        selector,
        found: false,
        selector_found: true,
        ...state,
        requested_value: requestedValue,
        match: matchMode,
        waited_ms: waitedMs,
        element: nodeInfo(element)
      }});
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _blur_expression(selector: str) -> str:
    return _selector_expression(
        selector,
        """
  const wasFocused = document.activeElement === element;
  element.blur?.();
  return {
    selector,
    found: true,
    blurred: document.activeElement !== element,
    was_focused: wasFocused,
    focused: document.activeElement === element
  };
""".rstrip(),
    )


def _storage_area_expression(area: str) -> str:
    return f"""
  const area = {_js_literal(area)};
  const storageForArea = () => area === "session" ? window.sessionStorage : window.localStorage;
""".rstrip()


def _storage_get_expression(
    *,
    area: str,
    key: str | None,
    prefix: str | None,
    max_items: int,
) -> str:
    key_source = "null" if key is None else _js_literal(key)
    prefix_source = "null" if prefix is None else _js_literal(prefix)
    return f"""
() => {{
{_storage_area_expression(area)}
  const requestedKey = {key_source};
  const prefix = {prefix_source};
  const maxItems = Math.max(1, {_js_literal(max_items)});
  try {{
    const storage = storageForArea();
    if (requestedKey !== null) {{
      const value = storage.getItem(requestedKey);
      return {{
        area,
        key: requestedKey,
        found: value !== null,
        value,
        value_length: value === null ? null : value.length
      }};
    }}
    const keys = [];
    for (let index = 0; index < storage.length; index += 1) {{
      const candidate = storage.key(index);
      if (candidate !== null && (prefix === null || candidate.startsWith(prefix))) {{
        keys.push(candidate);
      }}
    }}
    const items = keys.slice(0, maxItems).map((candidate) => {{
      const value = storage.getItem(candidate);
      return {{
        key: candidate,
        value,
        value_length: value === null ? null : value.length
      }};
    }});
    return {{
      area,
      key: null,
      prefix,
      found: true,
      count: keys.length,
      item_count: items.length,
      max_items: maxItems,
      truncated: keys.length > items.length,
      items
    }};
  }} catch (error) {{
    return {{
      area,
      key: requestedKey,
      prefix,
      found: false,
      error: String(error.name || "Error"),
      message: String(error.message || error)
    }};
  }}
}}
""".strip()


def _storage_set_expression(*, area: str, key: str, value: str) -> str:
    return f"""
() => {{
{_storage_area_expression(area)}
  const key = {_js_literal(key)};
  const value = {_js_literal(value)};
  try {{
    const storage = storageForArea();
    const previousValue = storage.getItem(key);
    storage.setItem(key, value);
    const currentValue = storage.getItem(key);
    return {{
      area,
      key,
      set: currentValue === value,
      found: true,
      previous_value: previousValue,
      value: currentValue,
      value_length: currentValue === null ? null : currentValue.length
    }};
  }} catch (error) {{
    return {{
      area,
      key,
      set: false,
      found: false,
      value: null,
      error: String(error.name || "Error"),
      message: String(error.message || error)
    }};
  }}
}}
""".strip()


def _storage_remove_expression(*, area: str, key: str) -> str:
    return f"""
() => {{
{_storage_area_expression(area)}
  const key = {_js_literal(key)};
  try {{
    const storage = storageForArea();
    const previousValue = storage.getItem(key);
    storage.removeItem(key);
    return {{
      area,
      key,
      removed: storage.getItem(key) === null,
      had_key: previousValue !== null,
      found: previousValue !== null,
      previous_value: previousValue
    }};
  }} catch (error) {{
    return {{
      area,
      key,
      removed: false,
      found: false,
      error: String(error.name || "Error"),
      message: String(error.message || error)
    }};
  }}
}}
""".strip()


def _storage_clear_expression(*, area: str, prefix: str | None) -> str:
    prefix_source = "null" if prefix is None else _js_literal(prefix)
    return f"""
() => {{
{_storage_area_expression(area)}
  const prefix = {prefix_source};
  try {{
    const storage = storageForArea();
    const keys = [];
    for (let index = 0; index < storage.length; index += 1) {{
      const candidate = storage.key(index);
      if (candidate !== null && (prefix === null || candidate.startsWith(prefix))) {{
        keys.push(candidate);
      }}
    }}
    for (const key of keys) {{
      storage.removeItem(key);
    }}
    return {{
      area,
      prefix,
      cleared: true,
      cleared_count: keys.length,
      keys
    }};
  }} catch (error) {{
    return {{
      area,
      prefix,
      cleared: false,
      cleared_count: 0,
      error: String(error.name || "Error"),
      message: String(error.message || error)
    }};
  }}
}}
""".strip()


def _wait_storage_expression(
    *,
    area: str,
    key: str,
    value: str | None,
    state: str,
    match: str,
    timeout_ms: float,
    poll_ms: float,
    case_sensitive: bool,
) -> str:
    value_source = "null" if value is None else _js_literal(value)
    return f"""
() => new Promise((resolve) => {{
{_storage_area_expression(area)}
  const key = {_js_literal(key)};
  const requestedValue = {value_source};
  const requestedState = {_js_literal(state)};
  const matchMode = {_js_literal(match)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const startedAt = Date.now();
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  let pattern = null;
  if (requestedValue !== null && matchMode === "regex") {{
    try {{
      pattern = new RegExp(requestedValue, caseSensitive ? "" : "i");
    }} catch (error) {{
      resolve({{
        area,
        key,
        found: false,
        state: requestedState,
        exists: null,
        value: null,
        requested_value: requestedValue,
        match: matchMode,
        waited_ms: 0,
        error: "invalid_regex",
        message: String(error.message || error)
      }});
      return;
    }}
  }}
  const matches = (currentValue) => {{
    if (requestedValue === null) return true;
    const candidate = String(currentValue ?? "");
    if (matchMode === "regex") return pattern.test(candidate);
    if (caseSensitive) {{
      return matchMode === "exact"
        ? candidate === requestedValue
        : candidate.includes(requestedValue);
    }}
    const haystack = candidate.toLowerCase();
    const needle = requestedValue.toLowerCase();
    return matchMode === "exact" ? haystack === needle : haystack.includes(needle);
  }};
  const check = () => {{
    const waitedMs = Date.now() - startedAt;
    try {{
      const storage = storageForArea();
      const currentValue = storage.getItem(key);
      const exists = currentValue !== null;
      const reached = requestedState === "absent"
        ? !exists
        : exists && matches(currentValue);
      if (reached) {{
        resolve({{
          area,
          key,
          found: true,
          state: requestedState,
          exists,
          value: currentValue,
          requested_value: requestedValue,
          match: matchMode,
          waited_ms: waitedMs
        }});
        return;
      }}
      if (waitedMs >= timeoutMs) {{
        resolve({{
          area,
          key,
          found: false,
          state: requestedState,
          exists,
          value: currentValue,
          requested_value: requestedValue,
          match: matchMode,
          waited_ms: waitedMs
        }});
        return;
      }}
    }} catch (error) {{
      resolve({{
        area,
        key,
        found: false,
        state: requestedState,
        exists: null,
        value: null,
        requested_value: requestedValue,
        match: matchMode,
        waited_ms: waitedMs,
        error: String(error.name || "Error"),
        message: String(error.message || error)
      }});
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _cookie_helpers_expression() -> str:
    return """
  const documentCookieScope = "document.cookie";
  const decodePart = (value) => {
    try {
      return decodeURIComponent(value);
    } catch (error) {
      return value;
    }
  };
  const parseCookies = () => {
    const raw = document.cookie || "";
    if (!raw) return [];
    return raw
      .split(";")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const separator = part.indexOf("=");
        const rawName = separator === -1 ? part : part.slice(0, separator);
        const rawValue = separator === -1 ? "" : part.slice(separator + 1);
        const name = decodePart(rawName);
        const value = decodePart(rawValue);
        return {
          name,
          value,
          raw_name: rawName,
          raw_value: rawValue,
          value_length: value.length
        };
      });
  };
  const findCookie = (name) =>
    parseCookies().find((cookie) => cookie.name === name) || null;
  const cookieAssignment = ({
    name,
    value,
    path,
    domain,
    maxAge,
    expires,
    sameSite,
    secure
  }) => {
    const parts = [`${encodeURIComponent(name)}=${encodeURIComponent(value)}`];
    if (path) parts.push(`Path=${path}`);
    if (domain) parts.push(`Domain=${domain}`);
    if (Number.isFinite(maxAge)) parts.push(`Max-Age=${Math.trunc(maxAge)}`);
    if (expires) parts.push(`Expires=${expires}`);
    if (sameSite) {
      parts.push(`SameSite=${sameSite[0].toUpperCase()}${sameSite.slice(1).toLowerCase()}`);
    }
    if (secure) parts.push("Secure");
    return parts.join("; ");
  };
""".rstrip()


def _cookie_get_expression(
    *,
    name: str | None,
    prefix: str | None,
    max_items: int,
) -> str:
    name_source = "null" if name is None else _js_literal(name)
    prefix_source = "null" if prefix is None else _js_literal(prefix)
    return f"""
() => {{
{_cookie_helpers_expression()}
  const requestedName = {name_source};
  const prefix = {prefix_source};
  const maxItems = Math.max(1, {_js_literal(max_items)});
  try {{
    if (requestedName !== null) {{
      const cookie = findCookie(requestedName);
      return {{
        document_cookie_scope: documentCookieScope,
        name: requestedName,
        found: cookie !== null,
        value: cookie?.value ?? null,
        raw_value: cookie?.raw_value ?? null,
        value_length: cookie?.value_length ?? null
      }};
    }}
    const matched = parseCookies().filter((cookie) =>
      prefix === null || cookie.name.startsWith(prefix)
    );
    const items = matched.slice(0, maxItems);
    return {{
      document_cookie_scope: documentCookieScope,
      name: null,
      prefix,
      found: true,
      count: matched.length,
      item_count: items.length,
      max_items: maxItems,
      truncated: matched.length > items.length,
      items
    }};
  }} catch (error) {{
    return {{
      document_cookie_scope: documentCookieScope,
      name: requestedName,
      prefix,
      found: false,
      error: String(error.name || "Error"),
      message: String(error.message || error)
    }};
  }}
}}
""".strip()


def _cookie_set_expression(
    *,
    name: str,
    value: str,
    path: str | None,
    domain: str | None,
    max_age: int | None,
    expires: str | None,
    same_site: str | None,
    secure: bool,
) -> str:
    return f"""
() => {{
{_cookie_helpers_expression()}
  const name = {_js_literal(name)};
  const value = {_js_literal(value)};
  const path = {_js_literal(path)};
  const domain = {_js_literal(domain)};
  const maxAge = {_js_literal(max_age)};
  const expires = {_js_literal(expires)};
  const sameSite = {_js_literal(same_site)};
  const secure = {_js_literal(secure)};
  try {{
    const previousCookie = findCookie(name);
    const assignment = cookieAssignment({{
      name,
      value,
      path,
      domain,
      maxAge,
      expires,
      sameSite,
      secure
    }});
    document.cookie = assignment;
    const currentCookie = findCookie(name);
    return {{
      document_cookie_scope: documentCookieScope,
      name,
      set: currentCookie?.value === value,
      found: currentCookie !== null,
      previous_value: previousCookie?.value ?? null,
      value: currentCookie?.value ?? null,
      value_length: currentCookie?.value_length ?? null,
      path,
      domain,
      max_age: maxAge,
      expires,
      same_site: sameSite,
      secure
    }};
  }} catch (error) {{
    return {{
      document_cookie_scope: documentCookieScope,
      name,
      set: false,
      found: false,
      value: null,
      path,
      domain,
      max_age: maxAge,
      expires,
      same_site: sameSite,
      secure,
      error: String(error.name || "Error"),
      message: String(error.message || error)
    }};
  }}
}}
""".strip()


def _cookie_delete_expression(
    *,
    name: str,
    path: str | None,
    domain: str | None,
) -> str:
    return f"""
() => {{
{_cookie_helpers_expression()}
  const name = {_js_literal(name)};
  const path = {_js_literal(path)};
  const domain = {_js_literal(domain)};
  try {{
    const previousCookie = findCookie(name);
    document.cookie = cookieAssignment({{
      name,
      value: "",
      path,
      domain,
      maxAge: 0,
      expires: "Thu, 01 Jan 1970 00:00:00 GMT",
      sameSite: null,
      secure: false
    }});
    const currentCookie = findCookie(name);
    return {{
      document_cookie_scope: documentCookieScope,
      name,
      deleted: currentCookie === null,
      had_cookie: previousCookie !== null,
      found: previousCookie !== null,
      previous_value: previousCookie?.value ?? null,
      path,
      domain
    }};
  }} catch (error) {{
    return {{
      document_cookie_scope: documentCookieScope,
      name,
      deleted: false,
      found: false,
      path,
      domain,
      error: String(error.name || "Error"),
      message: String(error.message || error)
    }};
  }}
}}
""".strip()


def _cookie_clear_expression(
    *,
    prefix: str | None,
    path: str | None,
    domain: str | None,
) -> str:
    prefix_source = "null" if prefix is None else _js_literal(prefix)
    return f"""
() => {{
{_cookie_helpers_expression()}
  const prefix = {prefix_source};
  const path = {_js_literal(path)};
  const domain = {_js_literal(domain)};
  try {{
    const matched = parseCookies().filter((cookie) =>
      prefix === null || cookie.name.startsWith(prefix)
    );
    for (const cookie of matched) {{
      document.cookie = cookieAssignment({{
        name: cookie.name,
        value: "",
        path,
        domain,
        maxAge: 0,
        expires: "Thu, 01 Jan 1970 00:00:00 GMT",
        sameSite: null,
        secure: false
      }});
    }}
    const remainingNames = new Set(parseCookies().map((cookie) => cookie.name));
    const deletedNames = matched
      .map((cookie) => cookie.name)
      .filter((name) => !remainingNames.has(name));
    return {{
      document_cookie_scope: documentCookieScope,
      prefix,
      path,
      domain,
      cleared: deletedNames.length === matched.length,
      cleared_count: deletedNames.length,
      matched_count: matched.length,
      names: matched.map((cookie) => cookie.name),
      remaining_count: matched.length - deletedNames.length
    }};
  }} catch (error) {{
    return {{
      document_cookie_scope: documentCookieScope,
      prefix,
      path,
      domain,
      cleared: false,
      cleared_count: 0,
      matched_count: 0,
      error: String(error.name || "Error"),
      message: String(error.message || error)
    }};
  }}
}}
""".strip()


def _wait_cookie_expression(
    *,
    name: str,
    value: str | None,
    state: str,
    match: str,
    timeout_ms: float,
    poll_ms: float,
    case_sensitive: bool,
) -> str:
    value_source = "null" if value is None else _js_literal(value)
    return f"""
() => new Promise((resolve) => {{
{_cookie_helpers_expression()}
  const name = {_js_literal(name)};
  const requestedValue = {value_source};
  const requestedState = {_js_literal(state)};
  const matchMode = {_js_literal(match)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const startedAt = Date.now();
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  let pattern = null;
  if (requestedValue !== null && matchMode === "regex") {{
    try {{
      pattern = new RegExp(requestedValue, caseSensitive ? "" : "i");
    }} catch (error) {{
      resolve({{
        document_cookie_scope: documentCookieScope,
        name,
        found: false,
        state: requestedState,
        exists: null,
        value: null,
        requested_value: requestedValue,
        match: matchMode,
        waited_ms: 0,
        error: "invalid_regex",
        message: String(error.message || error)
      }});
      return;
    }}
  }}
  const matches = (currentValue) => {{
    if (requestedValue === null) return true;
    const candidate = String(currentValue ?? "");
    if (matchMode === "regex") return pattern.test(candidate);
    if (caseSensitive) {{
      return matchMode === "exact"
        ? candidate === requestedValue
        : candidate.includes(requestedValue);
    }}
    const haystack = candidate.toLowerCase();
    const needle = requestedValue.toLowerCase();
    return matchMode === "exact" ? haystack === needle : haystack.includes(needle);
  }};
  const check = () => {{
    const waitedMs = Date.now() - startedAt;
    try {{
      const cookie = findCookie(name);
      const exists = cookie !== null;
      const currentValue = cookie?.value ?? null;
      const reached = requestedState === "absent"
        ? !exists
        : exists && matches(currentValue);
      if (reached) {{
        resolve({{
          document_cookie_scope: documentCookieScope,
          name,
          found: true,
          state: requestedState,
          exists,
          value: currentValue,
          raw_value: cookie?.raw_value ?? null,
          requested_value: requestedValue,
          match: matchMode,
          waited_ms: waitedMs
        }});
        return;
      }}
      if (waitedMs >= timeoutMs) {{
        resolve({{
          document_cookie_scope: documentCookieScope,
          name,
          found: false,
          state: requestedState,
          exists,
          value: currentValue,
          raw_value: cookie?.raw_value ?? null,
          requested_value: requestedValue,
          match: matchMode,
          waited_ms: waitedMs
        }});
        return;
      }}
    }} catch (error) {{
      resolve({{
        document_cookie_scope: documentCookieScope,
        name,
        found: false,
        state: requestedState,
        exists: null,
        value: null,
        requested_value: requestedValue,
        match: matchMode,
        waited_ms: waitedMs,
        error: String(error.name || "Error"),
        message: String(error.message || error)
      }});
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _clear_expression(selector: str) -> str:
    return _event_expression(
        selector,
        """
  const previousValue = element.isContentEditable
    ? element.textContent
    : ("value" in element ? element.value : null);
  if (element.isContentEditable) {
    element.textContent = "";
  } else if ("value" in element) {
    element.value = "";
  } else {
    return {
      selector,
      found: true,
      clearable: false,
      cleared: false,
      previous_value: previousValue,
      value: null
    };
  }
  dispatch(new Event("input", { bubbles: true }));
  dispatch(new Event("change", { bubbles: true }));
  const value = element.isContentEditable ? element.textContent : element.value;
  return {
    selector,
    found: true,
    clearable: true,
    cleared: value === "",
    previous_value: previousValue,
    value
  };
""".rstrip(),
    )


def _set_value_expression(selector: str, value: str, *, dispatch_events: bool) -> str:
    return _event_expression(
        selector,
        f"""
  const requestedValue = {_js_literal(value)};
  const previousValue = element.isContentEditable
    ? element.textContent
    : ("value" in element ? element.value : null);
  if (element.isContentEditable) {{
    element.textContent = requestedValue;
  }} else if ("value" in element) {{
    element.value = requestedValue;
  }} else {{
    return {{
      selector,
      found: true,
      writable: false,
      set: false,
      previous_value: previousValue,
      value: null,
      requested_value: requestedValue,
      dispatched_events: []
    }};
  }}
  const dispatchedEvents = [];
  if ({_js_literal(dispatch_events)}) {{
    for (const type of ["input", "change"]) {{
      dispatch(new Event(type, {{ bubbles: true }}));
      dispatchedEvents.push(type);
    }}
  }}
  const currentValue = element.isContentEditable ? element.textContent : element.value;
  return {{
    selector,
    found: true,
    writable: true,
    set: currentValue === requestedValue,
    previous_value: previousValue,
    value: currentValue,
    requested_value: requestedValue,
    dispatched_events: dispatchedEvents
  }};
""".rstrip(),
    )


def _dispatch_event_expression(
    *,
    selector: str,
    events: list[str],
    bubbles: bool,
    cancelable: bool,
) -> str:
    return _event_expression(
        selector,
        f"""
  const requestedEvents = {_js_literal(events)};
  const bubbles = {_js_literal(bubbles)};
  const cancelable = {_js_literal(cancelable)};
  const results = [];
  for (const type of requestedEvents) {{
    let accepted = true;
    if (type === "focus" && typeof element.focus === "function") {{
      element.focus();
    }} else if (type === "blur" && typeof element.blur === "function") {{
      element.blur();
    }} else if (type === "click" && typeof element.click === "function") {{
      element.click();
    }} else {{
      accepted = dispatch(new Event(type, {{ bubbles, cancelable }}));
    }}
    results.push({{ type, accepted }});
  }}
  return {{
    selector,
    found: true,
    dispatched: true,
    requested_events: requestedEvents,
    events: results,
    focused: document.activeElement === element
  }};
""".rstrip(),
    )


def _submit_expression(*, selector: str, skip_validation: bool) -> str:
    return f"""
() => {{
{_dom_helpers_expression()}
  const selector = {_js_literal(selector)};
  const skipValidation = {_js_literal(skip_validation)};
  const element = document.querySelector(selector);
  if (!element) {{
    return {{ selector, found: false, form_found: false, submitted: false }};
  }}
  const form = element.matches("form") ? element : element.closest("form");
  if (!form) {{
    return {{
      selector,
      found: true,
      form_found: false,
      submitted: false,
      element: nodeInfo(element)
    }};
  }}
  const nativeRequestSubmit = HTMLFormElement.prototype.requestSubmit;
  const nativeSubmit = HTMLFormElement.prototype.submit;
  const useRequestSubmit = !skipValidation && typeof nativeRequestSubmit === "function";
  try {{
    if (useRequestSubmit) {{
      nativeRequestSubmit.call(form);
    }} else {{
      nativeSubmit.call(form);
    }}
  }} catch (error) {{
    return {{
      selector,
      found: true,
      form_found: true,
      submitted: false,
      skip_validation: skipValidation,
      used_request_submit: useRequestSubmit,
      error: String(error.name || "Error"),
      message: String(error.message || error),
      form: nodeInfo(form)
    }};
  }}
  return {{
    selector,
    found: true,
    form_found: true,
    submitted: true,
    skip_validation: skipValidation,
    used_request_submit: useRequestSubmit,
    form: nodeInfo(form)
  }};
}}
""".strip()


def _rect_object_expression(variable_name: str) -> str:
    return (
        "{ "
        f"x: {variable_name}.x, "
        f"y: {variable_name}.y, "
        f"top: {variable_name}.top, "
        f"right: {variable_name}.right, "
        f"bottom: {variable_name}.bottom, "
        f"left: {variable_name}.left, "
        f"width: {variable_name}.width, "
        f"height: {variable_name}.height "
        "}"
    )


def _bounding_box_expression(selector: str) -> str:
    rect_object = _rect_object_expression("rect")
    return f"""
() => {{
{_dom_helpers_expression()}
  const selector = {_js_literal(selector)};
  const element = document.querySelector(selector);
  if (!element) {{
    return {{ selector, found: false, visible: false, bounding_box: null }};
  }}
  const rect = element.getBoundingClientRect();
  const center = {{
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2
  }};
  const inViewport = rect.bottom >= 0 &&
    rect.right >= 0 &&
    rect.top <= window.innerHeight &&
    rect.left <= window.innerWidth;
  return {{
    selector,
    found: true,
    visible: visible(element),
    in_viewport: inViewport,
    bounding_box: {rect_object},
    center,
    viewport: {{
      width: window.innerWidth,
      height: window.innerHeight,
      scroll_x: window.scrollX,
      scroll_y: window.scrollY
    }},
    element: nodeInfo(element)
  }};
}}
""".strip()


def _scroll_into_view_expression(
    *,
    selector: str,
    block: str,
    inline: str,
    behavior: str,
) -> str:
    before_rect = _rect_object_expression("before")
    after_rect = _rect_object_expression("after")
    return f"""
() => {{
{_dom_helpers_expression()}
  const selector = {_js_literal(selector)};
  const block = {_js_literal(block)};
  const inline = {_js_literal(inline)};
  const behavior = {_js_literal(behavior)};
  const element = document.querySelector(selector);
  if (!element) {{
    return {{ selector, found: false, scrolled: false }};
  }}
  const before = element.getBoundingClientRect();
  element.scrollIntoView({{ block, inline, behavior }});
  const after = element.getBoundingClientRect();
  const inViewport = after.bottom >= 0 &&
    after.right >= 0 &&
    after.top <= window.innerHeight &&
    after.left <= window.innerWidth;
  return {{
    selector,
    found: true,
    scrolled: true,
    block,
    inline,
    behavior,
    before: {before_rect},
    after: {after_rect},
    in_viewport: inViewport,
    visible: visible(element),
    viewport: {{
      width: window.innerWidth,
      height: window.innerHeight,
      scroll_x: window.scrollX,
      scroll_y: window.scrollY
    }},
    element: nodeInfo(element)
  }};
}}
""".strip()


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


def cmd_action_reload(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(args, "action.reload", _reload_expression())


def cmd_action_go_back(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(args, "action.go-back", _history_expression("back"))


def cmd_action_go_forward(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.go-forward",
        _history_expression("forward"),
    )


def cmd_action_wait_url(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.wait-url",
        _wait_url_expression(
            url=args.url,
            match=args.match,
            timeout_ms=args.timeout_ms,
            poll_ms=args.poll_ms,
        ),
    )


def cmd_action_wait_load_state(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.wait-load-state",
        _wait_load_state_expression(
            state=args.state,
            timeout_ms=args.timeout_ms,
            poll_ms=args.poll_ms,
        ),
    )


def cmd_action_wait_network_idle(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.wait-network-idle",
        _wait_network_idle_expression(
            idle_ms=args.idle_ms,
            timeout_ms=args.timeout_ms,
            poll_ms=args.poll_ms,
            max_inflight=args.max_inflight,
        ),
    )


def cmd_action_get_text(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.get-text",
        _selector_expression(
            args.selector,
            """
  const text = element.innerText ?? element.textContent ?? "";
  return { selector, found: true, text };
""".rstrip(),
        ),
    )


def cmd_action_exists(args: argparse.Namespace) -> None:
    selector = _js_literal(args.selector)
    _run_eval_backed_action_command(
        args,
        "action.exists",
        f"""
() => {{
  const selector = {selector};
  return {{ selector, exists: Boolean(document.querySelector(selector)) }};
}}
""".strip(),
    )


def cmd_action_count(args: argparse.Namespace) -> None:
    expression = f"""
() => {{
{_dom_helpers_expression(include_hidden=args.include_hidden)}
  const selector = {_js_literal(args.selector)};
  const all = [...document.querySelectorAll(selector)];
  const visibleNodes = all.filter(visible);
  const matched = includeHidden ? all : visibleNodes;
  return {{
    selector,
    include_hidden: includeHidden,
    count: matched.length,
    total_count: all.length,
    visible_count: visibleNodes.length
  }};
}}
""".strip()
    _run_eval_backed_action_command(args, "action.count", expression)


def cmd_action_wait_count(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.wait-count",
        _wait_count_expression(
            selector=args.selector,
            count=args.count,
            comparison=args.comparison,
            timeout_ms=args.timeout_ms,
            poll_ms=args.poll_ms,
            include_hidden=args.include_hidden,
        ),
    )


def cmd_action_wait_state(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.wait-state",
        _wait_state_expression(
            selector=args.selector,
            state=args.state,
            timeout_ms=args.timeout_ms,
            poll_ms=args.poll_ms,
        ),
    )


def cmd_action_query(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.query",
        _query_expression(
            selector=args.selector,
            include_hidden=args.include_hidden,
            max_nodes=args.max_nodes,
        ),
    )


def cmd_action_inspect(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.inspect",
        _inspect_expression(
            selector=args.selector,
            include_html=args.include_html,
            max_html_chars=args.max_html_chars,
            reveal_sensitive_values=args.reveal_sensitive_values,
        ),
    )


def cmd_action_get_attribute(args: argparse.Namespace) -> None:
    attribute = _js_literal(args.name)
    _run_eval_backed_action_command(
        args,
        "action.get-attribute",
        _selector_expression(
            args.selector,
            f"""
  const name = {attribute};
  const attributeValue = element.getAttribute(name);
  let propertyValue = null;
  if (name in element) {{
    const raw = element[name];
    propertyValue = raw == null || ["string", "number", "boolean"].includes(typeof raw)
      ? raw
      : String(raw);
  }}
  return {{
    selector,
    found: true,
    name,
    value: attributeValue,
    attribute_value: attributeValue,
    property_value: propertyValue
  }};
""".rstrip(),
        ),
    )


def cmd_action_wait_attribute(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.wait-attribute",
        _wait_attribute_expression(
            selector=args.selector,
            name=args.name,
            value=args.value,
            state=args.state,
            match=args.match,
            timeout_ms=args.timeout_ms,
            poll_ms=args.poll_ms,
            case_sensitive=args.case_sensitive,
        ),
    )


def cmd_action_wait_text(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.wait-text",
        _wait_text_expression(
            text=args.text,
            selector=args.selector,
            exact=args.exact,
            case_sensitive=args.case_sensitive,
            timeout_ms=args.timeout_ms,
            poll_ms=args.poll_ms,
            include_hidden=args.include_hidden,
        ),
    )


def cmd_action_focus(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.focus",
        _focus_expression(
            selector=args.selector,
            prevent_scroll=args.prevent_scroll,
        ),
    )


def cmd_action_get_value(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.get-value",
        _get_value_expression(args.selector),
    )


def cmd_action_wait_value(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.wait-value",
        _wait_value_expression(
            selector=args.selector,
            value=args.value,
            match=args.match,
            timeout_ms=args.timeout_ms,
            poll_ms=args.poll_ms,
            case_sensitive=args.case_sensitive,
        ),
    )


def cmd_action_blur(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.blur",
        _blur_expression(args.selector),
    )


def cmd_action_storage_get(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.storage-get",
        _storage_get_expression(
            area=args.area,
            key=args.key,
            prefix=args.prefix,
            max_items=args.max_items,
        ),
    )


def cmd_action_storage_set(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.storage-set",
        _storage_set_expression(
            area=args.area,
            key=args.key,
            value=args.value,
        ),
    )


def cmd_action_storage_remove(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.storage-remove",
        _storage_remove_expression(
            area=args.area,
            key=args.key,
        ),
    )


def cmd_action_storage_clear(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.storage-clear",
        _storage_clear_expression(
            area=args.area,
            prefix=args.prefix,
        ),
    )


def cmd_action_wait_storage(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.wait-storage",
        _wait_storage_expression(
            area=args.area,
            key=args.key,
            value=args.value,
            state=args.state,
            match=args.match,
            timeout_ms=args.timeout_ms,
            poll_ms=args.poll_ms,
            case_sensitive=args.case_sensitive,
        ),
    )


def cmd_action_cookie_get(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.cookie-get",
        _cookie_get_expression(
            name=args.name,
            prefix=args.prefix,
            max_items=args.max_items,
        ),
    )


def cmd_action_cookie_set(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.cookie-set",
        _cookie_set_expression(
            name=args.name,
            value=args.value,
            path=args.path,
            domain=args.domain,
            max_age=args.max_age,
            expires=args.expires,
            same_site=args.same_site,
            secure=args.secure,
        ),
    )


def cmd_action_cookie_delete(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.cookie-delete",
        _cookie_delete_expression(
            name=args.name,
            path=args.path,
            domain=args.domain,
        ),
    )


def cmd_action_cookie_clear(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.cookie-clear",
        _cookie_clear_expression(
            prefix=args.prefix,
            path=args.path,
            domain=args.domain,
        ),
    )


def cmd_action_wait_cookie(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.wait-cookie",
        _wait_cookie_expression(
            name=args.name,
            value=args.value,
            state=args.state,
            match=args.match,
            timeout_ms=args.timeout_ms,
            poll_ms=args.poll_ms,
            case_sensitive=args.case_sensitive,
        ),
    )


def cmd_action_clear(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.clear",
        _clear_expression(args.selector),
    )


def cmd_action_set_value(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.set-value",
        _set_value_expression(
            args.selector,
            args.value,
            dispatch_events=not args.no_events,
        ),
    )


def cmd_action_dispatch_event(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.dispatch-event",
        _dispatch_event_expression(
            selector=args.selector,
            events=args.event,
            bubbles=not args.no_bubbles,
            cancelable=args.cancelable,
        ),
    )


def cmd_action_submit(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.submit",
        _submit_expression(
            selector=args.selector,
            skip_validation=args.skip_validation,
        ),
    )


def cmd_action_scroll(args: argparse.Namespace) -> None:
    selector = getattr(args, "selector", None)
    x = _js_literal(args.x)
    y = _js_literal(args.y)
    behavior = _js_literal(args.behavior)

    if selector:
        expression = _selector_expression(
            selector,
            f"""
  element.scrollBy({{ left: {x}, top: {y}, behavior: {behavior} }});
  return {{ selector, found: true, scrolled: true, x: {x}, y: {y} }};
""".rstrip(),
        )
    else:
        expression = f"""
() => {{
  window.scrollBy({{ left: {x}, top: {y}, behavior: {behavior} }});
  return {{
    selector: null,
    found: true,
    scrolled: true,
    x: {x},
    y: {y},
    scroll_x: window.scrollX,
    scroll_y: window.scrollY
  }};
}}
""".strip()

    _run_eval_backed_action_command(args, "action.scroll", expression)


def cmd_action_bounding_box(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.bounding-box",
        _bounding_box_expression(args.selector),
    )


def cmd_action_scroll_into_view(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.scroll-into-view",
        _scroll_into_view_expression(
            selector=args.selector,
            block=args.block,
            inline=args.inline,
            behavior=args.behavior,
        ),
    )


def cmd_action_select_option(args: argparse.Namespace) -> None:
    value = _js_literal(args.value)
    _run_eval_backed_action_command(
        args,
        "action.select-option",
        _event_expression(
            args.selector,
            f"""
  const requestedValue = {value};
  const previousValue = element.value;
  element.value = requestedValue;
  dispatch(new Event("input", {{ bubbles: true }}));
  dispatch(new Event("change", {{ bubbles: true }}));
  return {{
    selector,
    found: true,
    selected: element.value === requestedValue,
    value: element.value,
    requested_value: requestedValue,
    previous_value: previousValue
  }};
""".rstrip(),
        ),
    )


def cmd_action_check(args: argparse.Namespace) -> None:
    _run_checkbox_action(args, checked=True, command="action.check")


def cmd_action_uncheck(args: argparse.Namespace) -> None:
    _run_checkbox_action(args, checked=False, command="action.uncheck")


def cmd_action_check_label(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.check-label",
        _check_label_expression(
            label=args.label,
            checked=True,
            exact=args.exact,
            case_sensitive=args.case_sensitive,
        ),
    )


def cmd_action_uncheck_label(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.uncheck-label",
        _check_label_expression(
            label=args.label,
            checked=False,
            exact=args.exact,
            case_sensitive=args.case_sensitive,
        ),
    )


def cmd_action_select_label(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.select-label",
        _select_label_expression(
            label=args.label,
            value=args.value,
            option_label=args.option_label,
            exact=args.exact,
            case_sensitive=args.case_sensitive,
        ),
    )


def _run_checkbox_action(
    args: argparse.Namespace,
    *,
    checked: bool,
    command: str,
) -> None:
    checked_literal = _js_literal(checked)
    _run_eval_backed_action_command(
        args,
        command,
        _event_expression(
            args.selector,
            f"""
  if (!("checked" in element)) {{
    return {{
      selector,
      found: true,
      checkable: false,
      checked: Boolean(element.checked)
    }};
  }}
  element.checked = {checked_literal};
  dispatch(new Event("input", {{ bubbles: true }}));
  dispatch(new Event("change", {{ bubbles: true }}));
  return {{
    selector,
    found: true,
    checkable: true,
    checked: Boolean(element.checked)
  }};
""".rstrip(),
        ),
    )


def cmd_action_hover(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.hover",
        _event_expression(
            args.selector,
            """
  const init = {
    view: window,
    bubbles: true,
    cancelable: true,
    clientX: element.getBoundingClientRect().left,
    clientY: element.getBoundingClientRect().top
  };
  for (const type of ["mouseover", "mouseenter", "mousemove"]) {
    dispatch(new MouseEvent(type, init));
  }
  return { selector, found: true, hovered: true };
""".rstrip(),
        ),
    )


def cmd_action_press(args: argparse.Namespace) -> None:
    key = _js_literal(args.key)
    _run_eval_backed_action_command(
        args,
        "action.press",
        _event_expression(
            args.selector,
            f"""
  const key = {key};
  element.focus();
  const init = {{ key, code: key, bubbles: true, cancelable: true }};
  const keydownAccepted = dispatch(new KeyboardEvent("keydown", init));
  dispatch(new KeyboardEvent("keypress", init));
  dispatch(new KeyboardEvent("keyup", init));
  return {{
    selector,
    found: true,
    focused: document.activeElement === element,
    key,
    pressed: true,
    keydown_accepted: keydownAccepted
  }};
""".rstrip(),
        ),
    )


def cmd_action_click_text(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.click-text",
        _click_text_expression(
            text=args.text,
            selector=args.selector,
            exact=args.exact,
            case_sensitive=args.case_sensitive,
        ),
    )


def cmd_action_click_role(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.click-role",
        _click_role_expression(
            role=args.role,
            name=args.name,
            exact=args.exact,
            case_sensitive=args.case_sensitive,
        ),
    )


def cmd_action_click_index(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.click-index",
        _click_index_expression(
            selector=args.selector,
            index=args.index,
            include_hidden=args.include_hidden,
        ),
    )


def cmd_action_fill_label(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.fill-label",
        _fill_label_expression(
            label=args.label,
            text=args.text,
            exact=args.exact,
            case_sensitive=args.case_sensitive,
        ),
    )


def cmd_action_form_snapshot(args: argparse.Namespace) -> None:
    expression = _form_snapshot_expression(
        selector=args.selector,
        include_hidden=args.include_hidden,
        max_nodes=args.max_nodes,
        reveal_sensitive_values=args.reveal_sensitive_values,
    )
    _run_eval_backed_action_command(args, "action.form-snapshot", expression)


def cmd_action_accessibility_snapshot(args: argparse.Namespace) -> None:
    expression = f"""
() => {{
{_dom_helpers_expression(include_hidden=args.include_hidden, max_nodes=args.max_nodes)}
  const root = document.body || document.documentElement;
  const elements = root ? [...root.querySelectorAll("*")] : [];
  const interesting = elements.filter((element) => {{
    if (!visible(element)) return false;
    const info = nodeInfo(element);
    return Boolean(info.role || info.name || info.text);
  }});
  const nodes = limited(interesting).map(nodeInfo);
  return {{
    url: location.href,
    title: document.title,
    kind: "dom-accessibility",
    include_hidden: includeHidden,
    node_count: nodes.length,
    truncated: maxNodes !== null && interesting.length > nodes.length,
    nodes
  }};
}}
""".strip()
    _run_eval_backed_action_command(args, "action.accessibility-snapshot", expression)


def cmd_action_interactive_snapshot(args: argparse.Namespace) -> None:
    expression = f"""
() => {{
{_dom_helpers_expression(include_hidden=args.include_hidden, max_nodes=args.max_nodes)}
  const elements = [...document.querySelectorAll(interactiveSelector)].filter(visible);
  const nodes = limited(elements).map(nodeInfo);
  return {{
    url: location.href,
    title: document.title,
    kind: "interactive",
    include_hidden: includeHidden,
    node_count: nodes.length,
    truncated: maxNodes !== null && elements.length > nodes.length,
    nodes
  }};
}}
""".strip()
    _run_eval_backed_action_command(args, "action.interactive-snapshot", expression)


def cmd_doctor(args: argparse.Namespace) -> None:
    command = "doctor"
    checks: list[dict[str, Any]] = []

    browser_cli_version = _package_version("browser-cli")
    checks.append(
        _doctor_check(
            "browser_cli",
            "pass",
            "browser-cli import succeeded",
            version=browser_cli_version or "unknown",
            version_known=browser_cli_version is not None,
        )
    )

    runtime_version = _package_version("lex-browser-runtime")
    checks.append(
        _doctor_check(
            "lex_browser_runtime",
            "pass",
            "lex-browser-runtime import succeeded",
            version=runtime_version or "unknown",
            version_known=runtime_version is not None,
        )
    )

    api_key = os.environ.get("LEXMOUNT_API_KEY")
    project_id = os.environ.get("LEXMOUNT_PROJECT_ID")
    base_url = os.environ.get("LEXMOUNT_BASE_URL")
    region = os.environ.get("LEXMOUNT_REGION")

    checks.append(
        _doctor_check(
            "env.LEXMOUNT_API_KEY",
            "pass" if api_key else "fail",
            "LEXMOUNT_API_KEY is set" if api_key else "LEXMOUNT_API_KEY is required",
            present=api_key is not None,
            **({} if api_key else {"fix": _credential_doctor_fix("LEXMOUNT_API_KEY")}),
        )
    )
    checks.append(
        _doctor_check(
            "env.LEXMOUNT_PROJECT_ID",
            "pass" if project_id else "fail",
            "LEXMOUNT_PROJECT_ID is set"
            if project_id
            else "LEXMOUNT_PROJECT_ID is required",
            present=project_id is not None,
            **(
                {}
                if project_id
                else {"fix": _credential_doctor_fix("LEXMOUNT_PROJECT_ID")}
            ),
        )
    )
    checks.append(
        _doctor_check(
            "env.LEXMOUNT_BASE_URL",
            "pass",
            "LEXMOUNT_BASE_URL is set"
            if base_url
            else "LEXMOUNT_BASE_URL is not set; the default endpoint will be used",
            present=base_url is not None,
            value=base_url,
            default=DEFAULT_LEXMOUNT_BASE_URL,
        )
    )
    checks.append(
        _doctor_check(
            "env.LEXMOUNT_REGION",
            "pass",
            "LEXMOUNT_REGION is set"
            if region
            else "LEXMOUNT_REGION is not set; runtime defaults apply",
            present=region is not None,
            value=region,
        )
    )

    try:
        connect_url = build_direct_connect_url()
    except Exception as exc:
        checks.append(
            _doctor_check(
                "direct_url",
                "fail",
                _mask_sensitive_text(str(exc)),
                error=exc.__class__.__name__,
                fix=_doctor_fix(
                    "fix_direct_url_configuration",
                    env=[
                        "LEXMOUNT_API_KEY",
                        "LEXMOUNT_PROJECT_ID",
                        "LEXMOUNT_BASE_URL",
                    ],
                    commands=[
                        "browser-cli auth status",
                        "browser-cli auth export-env",
                        "browser-cli doctor",
                    ],
                    guidance=[
                        "Confirm required environment variables are set.",
                        "Unset LEXMOUNT_BASE_URL unless a custom API endpoint is required.",
                    ],
                ),
            )
        )
    else:
        checks.append(
            _doctor_check(
                "direct_url",
                "pass",
                "direct browser websocket URL can be built",
                **_masked_connect_url_payload(
                    connect_url,
                    reveal_connect_url=args.reveal_connect_url,
                ),
            )
        )

    if args.skip_api:
        checks.append(
            _doctor_check(
                "api_connectivity",
                "skipped",
                "API connectivity check skipped by --skip-api",
                fix=_doctor_fix(
                    "run_live_api_check",
                    commands=["browser-cli doctor"],
                    guidance=[
                        "Rerun doctor without --skip-api when live API access is available."
                    ],
                ),
            )
        )
    elif not api_key or not project_id:
        checks.append(
            _doctor_check(
                "api_connectivity",
                "skipped",
                "API connectivity check requires LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID",
                fix=_credential_doctor_fix(
                    "LEXMOUNT_API_KEY",
                    "LEXMOUNT_PROJECT_ID",
                ),
            )
        )
    else:
        try:
            result = LexmountBrowserAdmin().list_sessions(status=None)
        except Exception as exc:
            info = getattr(exc, "lexmount_error_info", None)
            error = (
                info.payload().get("error")
                if isinstance(info, LexmountErrorInfo)
                else exc.__class__.__name__
            )
            checks.append(
                _doctor_check(
                    "api_connectivity",
                    "fail",
                    _mask_sensitive_text(str(exc)),
                    error=error,
                    fix=_doctor_fix(
                        "verify_api_connectivity",
                        commands=[
                            "browser-cli auth status",
                            "browser-cli doctor",
                        ],
                        guidance=[
                            "Confirm Project ID and API key are valid for browser.lexmount.cn.",
                            "Check LEXMOUNT_BASE_URL only if a custom API endpoint is configured.",
                            "Create a new scoped API key if the current key was revoked or expired.",
                        ],
                    ),
                )
            )
        else:
            payload = _model_payload(result)
            checks.append(
                _doctor_check(
                    "api_connectivity",
                    "pass",
                    "Lexmount API is reachable",
                    session_count=payload.get("count"),
                    status_filter=payload.get("status_filter"),
                )
            )

    failed = [check for check in checks if check["status"] == "fail"]
    data: dict[str, Any] = {
        "ok": not failed,
        "command": command,
        "status": "ok" if not failed else "error",
        "checked": len(checks),
        "failed": len(failed),
        "checks": checks,
    }
    if failed:
        data.update(
            {
                "error": "doctor_failed",
                "message": "One or more doctor checks failed.",
            }
        )
    _json_dump(data, exit_code=1 if failed else 0)


def cmd_auth_status(args: argparse.Namespace) -> None:
    command = "auth.status"
    api_key = os.environ.get("LEXMOUNT_API_KEY")
    project_id = os.environ.get("LEXMOUNT_PROJECT_ID")
    configured = bool(api_key and project_id)

    _success(
        command,
        configured=configured,
        api_key=_env_value_status("LEXMOUNT_API_KEY", secret=True),
        project_id=_env_value_status("LEXMOUNT_PROJECT_ID"),
        base_url=_env_value_status(
            "LEXMOUNT_BASE_URL",
            default=DEFAULT_LEXMOUNT_BASE_URL,
        ),
        region=_env_value_status("LEXMOUNT_REGION"),
        next_steps=_auth_next_steps(configured=configured),
    )


def cmd_auth_export_env(args: argparse.Namespace) -> None:
    command = "auth.export-env"
    warnings: list[str] = []

    api_key = "<api-key>"
    api_key_source = "placeholder"
    if args.from_current and os.environ.get("LEXMOUNT_API_KEY"):
        api_key_source = "env"
        if args.reveal_secrets:
            api_key = os.environ["LEXMOUNT_API_KEY"]
        else:
            api_key = "<redacted-api-key>"
            warnings.append(
                "LEXMOUNT_API_KEY is masked. Rerun with --from-current "
                "--reveal-secrets only in a trusted local terminal to print a usable export."
            )

    project_id = "<project-id>"
    project_id_source = "placeholder"
    if args.from_current and os.environ.get("LEXMOUNT_PROJECT_ID"):
        project_id = os.environ["LEXMOUNT_PROJECT_ID"]
        project_id_source = "env"

    entries = [
        {
            "name": "LEXMOUNT_API_KEY",
            "value": api_key,
            "secret": True,
            "source": api_key_source,
            "usable": api_key not in {"<api-key>", "<redacted-api-key>"},
        },
        {
            "name": "LEXMOUNT_PROJECT_ID",
            "value": project_id,
            "secret": False,
            "source": project_id_source,
            "usable": project_id != "<project-id>",
        },
    ]

    if args.include_base_url:
        current_base_url = os.environ.get("LEXMOUNT_BASE_URL")
        base_url = (
            current_base_url
            if args.from_current and current_base_url
            else DEFAULT_LEXMOUNT_BASE_URL
        )
        entries.append(
            {
                "name": "LEXMOUNT_BASE_URL",
                "value": base_url,
                "secret": False,
                "source": "env"
                if args.from_current and current_base_url
                else "default",
                "usable": True,
            }
        )

    if args.include_region:
        current_region = os.environ.get("LEXMOUNT_REGION")
        region = current_region if args.from_current and current_region else "<region>"
        entries.append(
            {
                "name": "LEXMOUNT_REGION",
                "value": region,
                "secret": False,
                "source": "env"
                if args.from_current and current_region
                else "placeholder",
                "usable": region != "<region>",
            }
        )

    commands = [
        _export_command(str(entry["name"]), str(entry["value"]), args.shell)
        for entry in entries
    ]
    secrets_revealed = (
        args.reveal_secrets
        and api_key_source == "env"
        and api_key not in {"<api-key>", "<redacted-api-key>"}
    )
    _success(
        command,
        shell=args.shell,
        from_current=args.from_current,
        secrets_revealed=secrets_revealed,
        warnings=warnings,
        exports=entries,
        commands=commands,
        script="\n".join(commands),
        next_steps=[
            "Run the export commands in the local shell.",
            "Run `browser-cli doctor` to verify credentials.",
        ],
    )


def cmd_auth_login(args: argparse.Namespace) -> None:
    command = "auth.login"
    project_id, project_id_source = _auth_login_project_id(args)
    scopes = _auth_login_scopes(args)
    connect_url = _connect_from_codex_url(
        project_id=project_id,
        scopes=scopes,
        expires_in=args.expires_in,
    )
    _success(
        command,
        flow="manual_env",
        login_url=LEXMOUNT_CONSOLE_URL,
        device_code_available=False,
        connect_from_codex={
            "available": False,
            "url": connect_url,
            "project_id": project_id,
            "project_id_source": project_id_source,
            "requested_scopes": scopes,
            "requested_expires_in": args.expires_in,
            "expected_outputs": [
                "Project ID for the selected project",
                "Scoped API key or short-lived local token",
                "Copyable shell export commands",
                "`browser-cli doctor` verification guidance",
                "Revoke and expiration details",
            ],
            "browser_site_requirements": [
                "Implement /connect/codex on browser.lexmount.cn.",
                "Accept optional project_id, repeated scope, and expires_in query parameters.",
                "Show the selected Project ID before issuing credentials.",
                "Issue scoped credentials for browser sessions, contexts, and actions.",
                "Offer copyable env/install commands without exposing secrets in chat.",
                "Support revoke, expiration, and device-code or OAuth approval.",
            ],
            "fallback": "Use the manual_env steps until browser.lexmount.cn supports this flow.",
        },
        flows=[
            {
                "name": "manual_env",
                "available": True,
                "description": "User copies Project ID and API key from browser.lexmount.cn into the local shell.",
            },
            {
                "name": "connect_from_codex",
                "available": False,
                "url": connect_url,
                "description": "Planned browser.lexmount.cn flow for scoped agent credentials.",
            },
        ],
        message=(
            "Open browser.lexmount.cn, sign in, choose a project, create or copy "
            "an API key, then set local environment variables."
        ),
        steps=[
            "Open https://browser.lexmount.cn and sign in.",
            "Select the project you want Codex or the agent to control.",
            "Copy the Project ID from the console.",
            "Create or copy an API key intended for local agent use.",
            "Run `browser-cli auth export-env` for safe shell export templates.",
            "Set LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID in the local shell.",
            "Run `browser-cli doctor` to verify the setup.",
        ],
        commands=[
            "browser-cli auth export-env",
            "browser-cli auth status",
            "browser-cli doctor",
        ],
        browser_site_recommendations=[
            "Add /connect/codex with Project ID display and query parameters for project_id, scope, and expires_in.",
            "Offer scoped API keys with expiration, revoke, and permission labels.",
            "Show copyable env/install commands and a doctor verification step.",
            "Add device-code or OAuth authorization for short-lived local tokens.",
        ],
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
    parser.add_argument(
        "--context-metadata-json",
        dest="context_metadata_filter",
        type=_parse_filter_metadata_json,
        help=(
            "Pick a reusable context matching this metadata before creating "
            "the session."
        ),
    )
    parser.add_argument(
        "--create-context-if-missing",
        action="store_true",
        help="Create a context with --context-metadata-json when no reusable match exists.",
    )
    parser.add_argument(
        "--context-status",
        help="Optional status filter used while picking a reusable context.",
    )
    parser.add_argument(
        "--context-limit",
        type=int,
        default=20,
        help="Maximum contexts to inspect while picking a reusable context.",
    )


def _add_text_match_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--exact",
        action="store_true",
        help="Require an exact normalized text match. Default uses contains.",
    )
    parser.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Match text case-sensitively. Default is case-insensitive.",
    )


def _add_snapshot_filter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-nodes", type=int, default=100)
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden DOM nodes in the snapshot.",
    )


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

    context_status = context_subparsers.add_parser(
        "status",
        help="Check whether one context is reusable or locked",
    )
    context_status.add_argument("--context-id", required=True)
    context_status.set_defaults(func=cmd_context_status)

    context_pick = context_subparsers.add_parser(
        "pick",
        help="Pick the first reusable context, optionally creating one",
    )
    context_pick.add_argument(
        "--status",
        help="Optional server-side status filter before local reusable checks",
    )
    context_pick.add_argument("--limit", type=int, default=20)
    context_pick.add_argument(
        "--metadata-json",
        dest="metadata_filter",
        type=_parse_filter_metadata_json,
        default={},
        help="JSON object that must match top-level context metadata fields",
    )
    context_pick.add_argument(
        "--create-if-missing",
        action="store_true",
        help="Create a context with the metadata filter when none is reusable.",
    )
    context_pick.set_defaults(func=cmd_context_pick)

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

    action_reload = action_subparsers.add_parser(
        "reload",
        help="Reload the current page",
    )
    _add_session_target_args(action_reload)
    action_reload.set_defaults(func=cmd_action_reload)

    action_go_back = action_subparsers.add_parser(
        "go-back",
        help="Request browser history back navigation",
    )
    _add_session_target_args(action_go_back)
    action_go_back.set_defaults(func=cmd_action_go_back)

    action_go_forward = action_subparsers.add_parser(
        "go-forward",
        help="Request browser history forward navigation",
    )
    _add_session_target_args(action_go_forward)
    action_go_forward.set_defaults(func=cmd_action_go_forward)

    action_wait_url = action_subparsers.add_parser(
        "wait-url",
        help="Wait until the current URL matches text or a regex",
    )
    _add_session_target_args(action_wait_url)
    action_wait_url.add_argument("--url", required=True)
    action_wait_url.add_argument(
        "--match",
        choices=["contains", "exact", "regex"],
        default="contains",
    )
    action_wait_url.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_url.add_argument("--poll-ms", type=float, default=250)
    action_wait_url.set_defaults(func=cmd_action_wait_url)

    action_wait_load_state = action_subparsers.add_parser(
        "wait-load-state",
        help="Wait until document.readyState reaches a requested state",
    )
    _add_session_target_args(action_wait_load_state)
    action_wait_load_state.add_argument(
        "--state",
        choices=["loading", "interactive", "complete", "domcontentloaded", "load"],
        default="complete",
        help="Ready state to wait for. domcontentloaded maps to interactive; load maps to complete.",
    )
    action_wait_load_state.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_load_state.add_argument("--poll-ms", type=float, default=250)
    action_wait_load_state.set_defaults(func=cmd_action_wait_load_state)

    action_wait_network_idle = action_subparsers.add_parser(
        "wait-network-idle",
        help="Wait until observed fetch/XHR/resource activity is quiet",
    )
    _add_session_target_args(action_wait_network_idle)
    action_wait_network_idle.add_argument(
        "--idle-ms",
        type=float,
        default=500,
        help="Required quiet period before the page is considered idle.",
    )
    action_wait_network_idle.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_network_idle.add_argument("--poll-ms", type=float, default=100)
    action_wait_network_idle.add_argument(
        "--max-inflight",
        type=int,
        default=0,
        help="Maximum observed in-flight requests allowed during the quiet period.",
    )
    action_wait_network_idle.set_defaults(func=cmd_action_wait_network_idle)

    action_get_text = action_subparsers.add_parser(
        "get-text",
        help="Read visible text from a selector",
    )
    _add_session_target_args(action_get_text)
    action_get_text.add_argument("--selector", required=True)
    action_get_text.set_defaults(func=cmd_action_get_text)

    action_exists = action_subparsers.add_parser(
        "exists",
        help="Check whether a selector exists",
    )
    _add_session_target_args(action_exists)
    action_exists.add_argument("--selector", required=True)
    action_exists.set_defaults(func=cmd_action_exists)

    action_count = action_subparsers.add_parser(
        "count",
        help="Count selector matches",
    )
    _add_session_target_args(action_count)
    action_count.add_argument("--selector", required=True)
    action_count.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden DOM nodes in the count.",
    )
    action_count.set_defaults(func=cmd_action_count)

    action_wait_count = action_subparsers.add_parser(
        "wait-count",
        help="Wait until a selector count reaches a comparison",
    )
    _add_session_target_args(action_wait_count)
    action_wait_count.add_argument("--selector", required=True)
    action_wait_count.add_argument("--count", type=int, required=True)
    action_wait_count.add_argument(
        "--comparison",
        choices=["eq", "gt", "gte", "lt", "lte"],
        default="eq",
        help="How to compare the current count with --count.",
    )
    action_wait_count.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_count.add_argument("--poll-ms", type=float, default=250)
    action_wait_count.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden DOM nodes in the count.",
    )
    action_wait_count.set_defaults(func=cmd_action_wait_count)

    action_wait_state = action_subparsers.add_parser(
        "wait-state",
        help="Wait until a selector reaches a common DOM state",
    )
    _add_session_target_args(action_wait_state)
    action_wait_state.add_argument("--selector", required=True)
    action_wait_state.add_argument(
        "--state",
        required=True,
        choices=[
            "attached",
            "detached",
            "visible",
            "hidden",
            "enabled",
            "disabled",
            "editable",
            "readonly",
            "checked",
            "unchecked",
            "focused",
            "in-viewport",
            "out-of-viewport",
        ],
    )
    action_wait_state.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_state.add_argument("--poll-ms", type=float, default=250)
    action_wait_state.set_defaults(func=cmd_action_wait_state)

    action_query = action_subparsers.add_parser(
        "query",
        help="List selector matches with DOM-backed node metadata",
    )
    _add_session_target_args(action_query)
    action_query.add_argument("--selector", required=True)
    _add_snapshot_filter_args(action_query)
    action_query.set_defaults(func=cmd_action_query)

    action_inspect = action_subparsers.add_parser(
        "inspect",
        help="Inspect one selector match with state, attributes, value, and geometry",
    )
    _add_session_target_args(action_inspect)
    action_inspect.add_argument("--selector", required=True)
    action_inspect.add_argument(
        "--include-html",
        action="store_true",
        help="Include sanitized outerHTML for the matched element.",
    )
    action_inspect.add_argument("--max-html-chars", type=int, default=2000)
    action_inspect.add_argument(
        "--reveal-sensitive-values",
        action="store_true",
        help="Reveal password/hidden values and sensitive attributes in local output.",
    )
    action_inspect.set_defaults(func=cmd_action_inspect)

    action_get_attribute = action_subparsers.add_parser(
        "get-attribute",
        help="Read an attribute and simple reflected property from a selector",
    )
    _add_session_target_args(action_get_attribute)
    action_get_attribute.add_argument("--selector", required=True)
    action_get_attribute.add_argument("--name", required=True)
    action_get_attribute.set_defaults(func=cmd_action_get_attribute)

    action_wait_attribute = action_subparsers.add_parser(
        "wait-attribute",
        help="Wait until an attribute reaches a state or value",
    )
    _add_session_target_args(action_wait_attribute)
    action_wait_attribute.add_argument("--selector", required=True)
    action_wait_attribute.add_argument("--name", required=True)
    action_wait_attribute.add_argument("--value")
    action_wait_attribute.add_argument(
        "--state",
        choices=["present", "absent"],
        default="present",
        help="Attribute presence state to wait for. When --value is set, present also waits for the value match.",
    )
    action_wait_attribute.add_argument(
        "--match",
        choices=["contains", "exact", "regex"],
        default="contains",
        help="How to match --value.",
    )
    action_wait_attribute.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_attribute.add_argument("--poll-ms", type=float, default=250)
    action_wait_attribute.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Match values case-sensitively.",
    )
    action_wait_attribute.set_defaults(func=cmd_action_wait_attribute)

    action_wait_text = action_subparsers.add_parser(
        "wait-text",
        help="Wait until text appears in the page or an optional selector",
    )
    _add_session_target_args(action_wait_text)
    action_wait_text.add_argument("--text", required=True)
    action_wait_text.add_argument(
        "--selector",
        help="Optional selector used to scope text candidates",
    )
    action_wait_text.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_text.add_argument("--poll-ms", type=float, default=250)
    action_wait_text.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden DOM nodes while waiting.",
    )
    _add_text_match_args(action_wait_text)
    action_wait_text.set_defaults(func=cmd_action_wait_text)

    action_focus = action_subparsers.add_parser(
        "focus",
        help="Focus a selector",
    )
    _add_session_target_args(action_focus)
    action_focus.add_argument("--selector", required=True)
    action_focus.add_argument(
        "--prevent-scroll",
        action="store_true",
        help="Focus without scrolling the element into view.",
    )
    action_focus.set_defaults(func=cmd_action_focus)

    action_get_value = action_subparsers.add_parser(
        "get-value",
        help="Read the value, checked state, or selected options from a form field",
    )
    _add_session_target_args(action_get_value)
    action_get_value.add_argument("--selector", required=True)
    action_get_value.set_defaults(func=cmd_action_get_value)

    action_wait_value = action_subparsers.add_parser(
        "wait-value",
        help="Wait until a form field value matches text",
    )
    _add_session_target_args(action_wait_value)
    action_wait_value.add_argument("--selector", required=True)
    action_wait_value.add_argument("--value", required=True)
    action_wait_value.add_argument(
        "--match",
        choices=["contains", "exact", "regex"],
        default="contains",
        help="How to match the current value.",
    )
    action_wait_value.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_value.add_argument("--poll-ms", type=float, default=250)
    action_wait_value.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Match values case-sensitively.",
    )
    action_wait_value.set_defaults(func=cmd_action_wait_value)

    action_blur = action_subparsers.add_parser(
        "blur",
        help="Blur a selector to trigger focusout/change validation",
    )
    _add_session_target_args(action_blur)
    action_blur.add_argument("--selector", required=True)
    action_blur.set_defaults(func=cmd_action_blur)

    action_storage_get = action_subparsers.add_parser(
        "storage-get",
        help="Read localStorage or sessionStorage values",
    )
    _add_session_target_args(action_storage_get)
    action_storage_get.add_argument(
        "--area",
        choices=["local", "session"],
        default="local",
        help="Storage area to read.",
    )
    action_storage_get.add_argument("--key")
    action_storage_get.add_argument(
        "--prefix",
        help="Only list keys with this prefix when --key is omitted.",
    )
    action_storage_get.add_argument(
        "--max-items",
        type=int,
        default=50,
        help="Maximum number of key/value pairs to return when listing.",
    )
    action_storage_get.set_defaults(func=cmd_action_storage_get)

    action_storage_set = action_subparsers.add_parser(
        "storage-set",
        help="Set a localStorage or sessionStorage value",
    )
    _add_session_target_args(action_storage_set)
    action_storage_set.add_argument(
        "--area",
        choices=["local", "session"],
        default="local",
        help="Storage area to write.",
    )
    action_storage_set.add_argument("--key", required=True)
    action_storage_set.add_argument("--value", required=True)
    action_storage_set.set_defaults(func=cmd_action_storage_set)

    action_storage_remove = action_subparsers.add_parser(
        "storage-remove",
        help="Remove a localStorage or sessionStorage value",
    )
    _add_session_target_args(action_storage_remove)
    action_storage_remove.add_argument(
        "--area",
        choices=["local", "session"],
        default="local",
        help="Storage area to update.",
    )
    action_storage_remove.add_argument("--key", required=True)
    action_storage_remove.set_defaults(func=cmd_action_storage_remove)

    action_storage_clear = action_subparsers.add_parser(
        "storage-clear",
        help="Clear localStorage or sessionStorage values",
    )
    _add_session_target_args(action_storage_clear)
    action_storage_clear.add_argument(
        "--area",
        choices=["local", "session"],
        default="local",
        help="Storage area to clear.",
    )
    action_storage_clear.add_argument(
        "--prefix",
        help="Only clear keys with this prefix.",
    )
    action_storage_clear.set_defaults(func=cmd_action_storage_clear)

    action_wait_storage = action_subparsers.add_parser(
        "wait-storage",
        help="Wait until localStorage or sessionStorage reaches a key/value state",
    )
    _add_session_target_args(action_wait_storage)
    action_wait_storage.add_argument(
        "--area",
        choices=["local", "session"],
        default="local",
        help="Storage area to wait on.",
    )
    action_wait_storage.add_argument("--key", required=True)
    action_wait_storage.add_argument("--value")
    action_wait_storage.add_argument(
        "--state",
        choices=["present", "absent"],
        default="present",
        help="Presence state to wait for. When --value is set, present also waits for the value match.",
    )
    action_wait_storage.add_argument(
        "--match",
        choices=["contains", "exact", "regex"],
        default="contains",
        help="How to match --value.",
    )
    action_wait_storage.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_storage.add_argument("--poll-ms", type=float, default=250)
    action_wait_storage.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Match values case-sensitively.",
    )
    action_wait_storage.set_defaults(func=cmd_action_wait_storage)

    action_cookie_get = action_subparsers.add_parser(
        "cookie-get",
        help="Read document.cookie-visible cookies",
    )
    _add_session_target_args(action_cookie_get)
    action_cookie_get.add_argument("--name")
    action_cookie_get.add_argument(
        "--prefix",
        help="Only list cookie names with this prefix when --name is omitted.",
    )
    action_cookie_get.add_argument(
        "--max-items",
        type=int,
        default=50,
        help="Maximum number of cookies to return when listing.",
    )
    action_cookie_get.set_defaults(func=cmd_action_cookie_get)

    action_cookie_set = action_subparsers.add_parser(
        "cookie-set",
        help="Set a document.cookie-visible cookie",
    )
    _add_session_target_args(action_cookie_set)
    action_cookie_set.add_argument("--name", required=True)
    action_cookie_set.add_argument("--value", required=True)
    action_cookie_set.add_argument("--path")
    action_cookie_set.add_argument("--domain")
    action_cookie_set.add_argument("--max-age", type=int)
    action_cookie_set.add_argument(
        "--expires",
        help="Cookie Expires attribute, e.g. 'Wed, 21 Oct 2026 07:28:00 GMT'.",
    )
    action_cookie_set.add_argument(
        "--same-site",
        choices=["lax", "strict", "none"],
        help="Cookie SameSite attribute.",
    )
    action_cookie_set.add_argument(
        "--secure",
        action="store_true",
        help="Add the Secure attribute.",
    )
    action_cookie_set.set_defaults(func=cmd_action_cookie_set)

    action_cookie_delete = action_subparsers.add_parser(
        "cookie-delete",
        help="Delete one document.cookie-visible cookie",
    )
    _add_session_target_args(action_cookie_delete)
    action_cookie_delete.add_argument("--name", required=True)
    action_cookie_delete.add_argument("--path")
    action_cookie_delete.add_argument("--domain")
    action_cookie_delete.set_defaults(func=cmd_action_cookie_delete)

    action_cookie_clear = action_subparsers.add_parser(
        "cookie-clear",
        help="Clear document.cookie-visible cookies",
    )
    _add_session_target_args(action_cookie_clear)
    action_cookie_clear.add_argument(
        "--prefix",
        help="Only clear cookie names with this prefix.",
    )
    action_cookie_clear.add_argument("--path")
    action_cookie_clear.add_argument("--domain")
    action_cookie_clear.set_defaults(func=cmd_action_cookie_clear)

    action_wait_cookie = action_subparsers.add_parser(
        "wait-cookie",
        help="Wait until a document.cookie-visible cookie reaches a state",
    )
    _add_session_target_args(action_wait_cookie)
    action_wait_cookie.add_argument("--name", required=True)
    action_wait_cookie.add_argument("--value")
    action_wait_cookie.add_argument(
        "--state",
        choices=["present", "absent"],
        default="present",
        help="Presence state to wait for. When --value is set, present also waits for the value match.",
    )
    action_wait_cookie.add_argument(
        "--match",
        choices=["contains", "exact", "regex"],
        default="contains",
        help="How to match --value.",
    )
    action_wait_cookie.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_cookie.add_argument("--poll-ms", type=float, default=250)
    action_wait_cookie.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Match values case-sensitively.",
    )
    action_wait_cookie.set_defaults(func=cmd_action_wait_cookie)

    action_clear = action_subparsers.add_parser(
        "clear",
        help="Clear a form field or editable element",
    )
    _add_session_target_args(action_clear)
    action_clear.add_argument("--selector", required=True)
    action_clear.set_defaults(func=cmd_action_clear)

    action_set_value = action_subparsers.add_parser(
        "set-value",
        help="Set a form field or editable element value and dispatch input/change",
    )
    _add_session_target_args(action_set_value)
    action_set_value.add_argument("--selector", required=True)
    action_set_value.add_argument("--value", required=True)
    action_set_value.add_argument(
        "--no-events",
        action="store_true",
        help="Do not dispatch input/change after setting the value.",
    )
    action_set_value.set_defaults(func=cmd_action_set_value)

    action_dispatch_event = action_subparsers.add_parser(
        "dispatch-event",
        help="Dispatch common DOM events for a selector",
    )
    _add_session_target_args(action_dispatch_event)
    action_dispatch_event.add_argument("--selector", required=True)
    action_dispatch_event.add_argument(
        "--event",
        action="append",
        choices=COMMON_DOM_EVENT_NAMES,
        required=True,
        help="Event name to dispatch. May be repeated.",
    )
    action_dispatch_event.add_argument(
        "--no-bubbles",
        action="store_true",
        help="Dispatch synthetic Event objects with bubbles=false.",
    )
    action_dispatch_event.add_argument(
        "--cancelable",
        action="store_true",
        help="Dispatch synthetic Event objects with cancelable=true.",
    )
    action_dispatch_event.set_defaults(func=cmd_action_dispatch_event)

    action_submit = action_subparsers.add_parser(
        "submit",
        help="Submit the nearest form for a selector",
    )
    _add_session_target_args(action_submit)
    action_submit.add_argument("--selector", required=True)
    action_submit.add_argument(
        "--skip-validation",
        action="store_true",
        help="Use form.submit() instead of requestSubmit().",
    )
    action_submit.set_defaults(func=cmd_action_submit)

    action_scroll = action_subparsers.add_parser(
        "scroll",
        help="Scroll the page or one scrollable selector",
    )
    _add_session_target_args(action_scroll)
    action_scroll.add_argument("--selector")
    action_scroll.add_argument("--x", type=float, default=0)
    action_scroll.add_argument("--y", type=float, default=600)
    action_scroll.add_argument(
        "--behavior",
        choices=["auto", "smooth"],
        default="auto",
    )
    action_scroll.set_defaults(func=cmd_action_scroll)

    action_bounding_box = action_subparsers.add_parser(
        "bounding-box",
        help="Read selector geometry and viewport position",
    )
    _add_session_target_args(action_bounding_box)
    action_bounding_box.add_argument("--selector", required=True)
    action_bounding_box.set_defaults(func=cmd_action_bounding_box)

    action_scroll_into_view = action_subparsers.add_parser(
        "scroll-into-view",
        help="Scroll one selector into the viewport",
    )
    _add_session_target_args(action_scroll_into_view)
    action_scroll_into_view.add_argument("--selector", required=True)
    action_scroll_into_view.add_argument(
        "--block",
        choices=["start", "center", "end", "nearest"],
        default="center",
        help="Vertical alignment passed to element.scrollIntoView().",
    )
    action_scroll_into_view.add_argument(
        "--inline",
        choices=["start", "center", "end", "nearest"],
        default="nearest",
        help="Horizontal alignment passed to element.scrollIntoView().",
    )
    action_scroll_into_view.add_argument(
        "--behavior",
        choices=["auto", "smooth"],
        default="auto",
    )
    action_scroll_into_view.set_defaults(func=cmd_action_scroll_into_view)

    action_select_option = action_subparsers.add_parser(
        "select-option",
        help="Set the value of a select-like element",
    )
    _add_session_target_args(action_select_option)
    action_select_option.add_argument("--selector", required=True)
    action_select_option.add_argument("--value", required=True)
    action_select_option.set_defaults(func=cmd_action_select_option)

    action_select_label = action_subparsers.add_parser(
        "select-label",
        help="Select an option in a native select matched by label or accessible name",
    )
    _add_session_target_args(action_select_label)
    action_select_label.add_argument("--label", required=True)
    select_label_value = action_select_label.add_mutually_exclusive_group(required=True)
    select_label_value.add_argument("--value")
    select_label_value.add_argument("--option-label")
    _add_text_match_args(action_select_label)
    action_select_label.set_defaults(func=cmd_action_select_label)

    action_check = action_subparsers.add_parser(
        "check",
        help="Check a checkbox-like element",
    )
    _add_session_target_args(action_check)
    action_check.add_argument("--selector", required=True)
    action_check.set_defaults(func=cmd_action_check)

    action_uncheck = action_subparsers.add_parser(
        "uncheck",
        help="Uncheck a checkbox-like element",
    )
    _add_session_target_args(action_uncheck)
    action_uncheck.add_argument("--selector", required=True)
    action_uncheck.set_defaults(func=cmd_action_uncheck)

    action_check_label = action_subparsers.add_parser(
        "check-label",
        help="Check a checkbox, radio, or switch matched by label or accessible name",
    )
    _add_session_target_args(action_check_label)
    action_check_label.add_argument("--label", required=True)
    _add_text_match_args(action_check_label)
    action_check_label.set_defaults(func=cmd_action_check_label)

    action_uncheck_label = action_subparsers.add_parser(
        "uncheck-label",
        help="Uncheck a checkbox or switch matched by label or accessible name",
    )
    _add_session_target_args(action_uncheck_label)
    action_uncheck_label.add_argument("--label", required=True)
    _add_text_match_args(action_uncheck_label)
    action_uncheck_label.set_defaults(func=cmd_action_uncheck_label)

    action_hover = action_subparsers.add_parser(
        "hover",
        help="Dispatch hover events for a selector",
    )
    _add_session_target_args(action_hover)
    action_hover.add_argument("--selector", required=True)
    action_hover.set_defaults(func=cmd_action_hover)

    action_press = action_subparsers.add_parser(
        "press",
        help="Focus a selector and dispatch key events",
    )
    _add_session_target_args(action_press)
    action_press.add_argument("--selector", required=True)
    action_press.add_argument("--key", required=True)
    action_press.set_defaults(func=cmd_action_press)

    action_click_text = action_subparsers.add_parser(
        "click-text",
        help="Click the first visible interactive element matching text",
    )
    _add_session_target_args(action_click_text)
    action_click_text.add_argument("--text", required=True)
    action_click_text.add_argument(
        "--selector",
        help="Optional selector used to scope candidate elements",
    )
    _add_text_match_args(action_click_text)
    action_click_text.set_defaults(func=cmd_action_click_text)

    action_click_role = action_subparsers.add_parser(
        "click-role",
        help="Click the first visible element matching role and optional name",
    )
    _add_session_target_args(action_click_role)
    action_click_role.add_argument("--role", required=True)
    action_click_role.add_argument("--name")
    _add_text_match_args(action_click_role)
    action_click_role.set_defaults(func=cmd_action_click_role)

    action_click_index = action_subparsers.add_parser(
        "click-index",
        help="Click the visible selector match at a zero-based index",
    )
    _add_session_target_args(action_click_index)
    action_click_index.add_argument("--selector", required=True)
    action_click_index.add_argument("--index", required=True, type=_non_negative_int)
    action_click_index.add_argument(
        "--include-hidden",
        action="store_true",
        help="Allow hidden DOM nodes to be counted and clicked.",
    )
    action_click_index.set_defaults(func=cmd_action_click_index)

    action_fill_label = action_subparsers.add_parser(
        "fill-label",
        help="Fill a form field matched by label, aria-label, or placeholder",
    )
    _add_session_target_args(action_fill_label)
    action_fill_label.add_argument("--label", required=True)
    action_fill_label.add_argument("--text", required=True)
    _add_text_match_args(action_fill_label)
    action_fill_label.set_defaults(func=cmd_action_fill_label)

    action_form_snapshot = action_subparsers.add_parser(
        "form-snapshot",
        help="Capture form fields, labels, values, and select options",
    )
    _add_session_target_args(action_form_snapshot)
    action_form_snapshot.add_argument(
        "--selector",
        help="Optional form or container selector used to scope fields.",
    )
    _add_snapshot_filter_args(action_form_snapshot)
    action_form_snapshot.add_argument(
        "--reveal-sensitive-values",
        action="store_true",
        help="Reveal password and hidden field values. Default masks them.",
    )
    action_form_snapshot.set_defaults(func=cmd_action_form_snapshot)

    action_accessibility_snapshot = action_subparsers.add_parser(
        "accessibility-snapshot",
        help="Capture a DOM-backed accessibility-like snapshot",
    )
    _add_session_target_args(action_accessibility_snapshot)
    _add_snapshot_filter_args(action_accessibility_snapshot)
    action_accessibility_snapshot.set_defaults(func=cmd_action_accessibility_snapshot)

    action_interactive_snapshot = action_subparsers.add_parser(
        "interactive-snapshot",
        help="Capture visible interactive elements",
    )
    _add_session_target_args(action_interactive_snapshot)
    _add_snapshot_filter_args(action_interactive_snapshot)
    action_interactive_snapshot.set_defaults(func=cmd_action_interactive_snapshot)


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


def _add_auth_commands(subparsers: argparse._SubParsersAction[Any]) -> None:
    auth = subparsers.add_parser(
        "auth",
        help="Inspect and configure local Lexmount credentials",
    )
    auth_subparsers = auth.add_subparsers(dest="auth_command", required=True)

    auth_status = auth_subparsers.add_parser(
        "status",
        help="Show local Lexmount credential environment status without secrets",
    )
    auth_status.set_defaults(func=cmd_auth_status)

    auth_export_env = auth_subparsers.add_parser(
        "export-env",
        help="Print safe shell export commands for Lexmount credentials",
    )
    auth_export_env.add_argument(
        "--shell",
        choices=["posix", "fish", "powershell"],
        default="posix",
        help="Shell syntax to emit.",
    )
    auth_export_env.add_argument(
        "--from-current",
        action="store_true",
        help="Populate commands from current environment values where available.",
    )
    auth_export_env.add_argument(
        "--reveal-secrets",
        action="store_true",
        help="Print current LEXMOUNT_API_KEY in the generated commands. Use only locally.",
    )
    auth_export_env.add_argument(
        "--include-base-url",
        action="store_true",
        help="Include LEXMOUNT_BASE_URL in the generated commands.",
    )
    auth_export_env.add_argument(
        "--include-region",
        action="store_true",
        help="Include LEXMOUNT_REGION in the generated commands.",
    )
    auth_export_env.set_defaults(func=cmd_auth_export_env)

    auth_login = auth_subparsers.add_parser(
        "login",
        help="Show browser.lexmount.cn login and environment setup guidance",
    )
    auth_login.add_argument(
        "--project-id",
        help=(
            "Project ID to include in the planned Connect from Codex URL. "
            "Defaults to LEXMOUNT_PROJECT_ID when set."
        ),
    )
    auth_login.add_argument(
        "--scope",
        action="append",
        help=(
            "Requested Connect from Codex credential scope. May be repeated; "
            "defaults to browser session, context, and action scopes."
        ),
    )
    auth_login.add_argument(
        "--expires-in",
        default=DEFAULT_CODEX_CONNECT_EXPIRES_IN,
        help="Requested Connect from Codex credential lifetime, such as 7d or 24h.",
    )
    auth_login.set_defaults(func=cmd_auth_login)


def _add_doctor_command(subparsers: argparse._SubParsersAction[Any]) -> None:
    doctor = subparsers.add_parser(
        "doctor",
        help="Check browser-cli install, credentials, direct URL, and API connectivity",
    )
    doctor.add_argument(
        "--skip-api",
        action="store_true",
        help="Skip the live Lexmount API connectivity check.",
    )
    doctor.add_argument(
        "--reveal-connect-url",
        action="store_true",
        help="Print the full direct URL including api_key. Default output masks secrets.",
    )
    doctor.set_defaults(func=cmd_doctor)


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

    parser = JsonArgumentParser(
        description="Lexmount browser operation CLI",
        prog="browser-cli",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_session_commands(subparsers)
    _add_context_commands(subparsers)
    _add_action_commands(subparsers)
    _add_case_commands(subparsers)
    _add_auth_commands(subparsers)
    _add_doctor_command(subparsers)
    _add_alias_commands(subparsers)

    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the Lexmount browser operation CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
