"""Command-line entrypoint for Lexmount browser operations."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path
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

PACKAGE_NAME = "browser-cli"
DEFAULT_BROWSER_CONSOLE_URL = "https://browser.lexmount.cn"
DEFAULT_LEXMOUNT_BASE_URL = "https://api.lexmount.cn"
REQUIRED_AUTH_ENV_VARS = ("LEXMOUNT_API_KEY", "LEXMOUNT_PROJECT_ID")
SENSITIVE_ENV_VARS = (
    "LEXMOUNT_API_KEY",
    "LEXMOUNT_ACCESS_TOKEN",
    "LEXMOUNT_REFRESH_TOKEN",
    "LEXMOUNT_TOKEN",
)
SENSITIVE_FIELD_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "password",
    "refresh_token",
    "secret",
    "token",
}
SENSITIVE_QUERY_PARAMS = {
    "access_token",
    "api_key",
    "apikey",
    "auth_token",
    "refresh_token",
    "token",
}
SUBCOMMAND_GROUPS = {"action", "auth", "case", "context", "session"}
TOP_LEVEL_COMMANDS = SUBCOMMAND_GROUPS | {
    "close-session",
    "direct-url",
    "doctor",
    "list-contexts",
    "prepare",
}
_current_parse_argv: list[str] = []


def _mask_secret_value(value: str) -> str:
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _configured_secret_values() -> list[str]:
    return [
        value
        for name in SENSITIVE_ENV_VARS
        if (value := os.environ.get(name)) and len(value) >= 8
    ]


def _mask_url_query_secrets(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.query:
        return value

    changed = False
    query: list[tuple[str, str]] = []
    for key, raw_value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in SENSITIVE_QUERY_PARAMS and raw_value:
            query.append((key, "***"))
            changed = True
        else:
            query.append((key, raw_value))

    if not changed:
        return value
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query, safe="*"),
            parsed.fragment,
        )
    )


def _redact_string(value: str) -> str:
    redacted = _mask_url_query_secrets(value)
    for secret in _configured_secret_values():
        redacted = redacted.replace(secret, _mask_secret_value(secret))
    return redacted


def _redact_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.lower() in SENSITIVE_FIELD_NAMES:
                redacted[key] = (
                    _mask_secret_value(item) if isinstance(item, str) and item else item
                )
            else:
                redacted[key] = _redact_json_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_json_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_json_value(item) for item in value)
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _json_dump(
    payload: dict[str, Any],
    exit_code: int = 0,
    *,
    redact_secrets: bool = True,
    reveal_fields: set[str] | None = None,
) -> NoReturn:
    revealed = {
        field: payload[field] for field in reveal_fields or set() if field in payload
    }
    if redact_secrets:
        payload = _redact_json_value(payload)
    payload.update(revealed)
    print(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    )
    raise SystemExit(exit_code)


def _success(
    command: str,
    *,
    redact_secrets: bool = True,
    reveal_fields: set[str] | None = None,
    **payload: Any,
) -> NoReturn:
    data = {"ok": True, "command": command}
    data.update(payload)
    _json_dump(data, redact_secrets=redact_secrets, reveal_fields=reveal_fields)


def _failure(
    command: str,
    error: str,
    message: str,
    *,
    exit_code: int = 1,
    redact_secrets: bool = True,
    reveal_fields: set[str] | None = None,
    **payload: Any,
) -> NoReturn:
    data = {
        "ok": False,
        "command": command,
        "error": error,
        "message": message,
    }
    data.update(payload)
    _json_dump(
        data,
        exit_code=exit_code,
        redact_secrets=redact_secrets,
        reveal_fields=reveal_fields,
    )


def _command_from_argv(argv: list[str]) -> str:
    if not argv or argv[0].startswith("-"):
        return "browser-cli"

    root = argv[0]
    if root not in TOP_LEVEL_COMMANDS:
        return "browser-cli"
    if root in SUBCOMMAND_GROUPS:
        if len(argv) > 1 and not argv[1].startswith("-"):
            return f"{root}.{argv[1]}"
        return root
    return root


class BrowserCliArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        _failure(
            _command_from_argv(_current_parse_argv),
            "argument_error",
            message,
            exit_code=2,
            usage=self.format_usage().strip(),
        )


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


def _exception_brief(exc: Exception) -> dict[str, Any]:
    info = getattr(exc, "lexmount_error_info", None)
    if isinstance(info, LexmountErrorInfo):
        payload: dict[str, Any] = {
            "error": info.code,
            "exception_message": info.message,
        }
        if info.status_code is not None:
            payload["status_code"] = info.status_code
        return payload
    return {"error": exc.__class__.__name__, "exception_message": str(exc)}


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


def _package_version() -> str:
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        pass

    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except OSError:
        return "unknown"

    project = data.get("project")
    if isinstance(project, dict):
        version = project.get("version")
        if isinstance(version, str):
            return version
    return "unknown"


def _context_reuse_payload(context_payload: dict[str, Any]) -> dict[str, Any]:
    status = context_payload.get("status")
    normalized_status = str(status or "").lower()
    context_id = context_payload.get("context_id")

    if normalized_status == "available":
        command = None
        if context_id:
            command = (
                "browser-cli session create "
                f"--context-id {context_id} --context-mode read_write"
            )
        return {
            "can_reuse_now": True,
            "reason": "context_available",
            "recommended_context_mode": "read_write",
            "recommended_session_command": command,
            "next_steps": [
                "Create a session with this context_id to reuse cookies and storage."
            ],
        }

    if normalized_status == "locked":
        return {
            "can_reuse_now": False,
            "reason": "context_locked",
            "recommended_context_mode": None,
            "recommended_session_command": None,
            "next_steps": [
                "Close the active session using this context, then retry.",
                "Create a new context if the current session must stay open.",
            ],
        }

    return {
        "can_reuse_now": None,
        "reason": "context_status_unknown"
        if status is None
        else "context_not_available",
        "recommended_context_mode": None,
        "recommended_session_command": None,
        "next_steps": [
            "Inspect the context status before using it for persistent login state."
        ],
    }


def _context_payload(value: Any) -> dict[str, Any]:
    payload = dict(value) if isinstance(value, dict) else _model_payload(value)
    payload["reuse"] = _context_reuse_payload(payload)
    return payload


def _metadata_matches(
    context_payload: dict[str, Any],
    metadata_match: dict[str, Any] | None,
) -> bool:
    if not metadata_match:
        return True
    metadata = context_payload.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return all(metadata.get(key) == value for key, value in metadata_match.items())


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


def _browser_cli_version() -> str | None:
    version = _package_version()
    return None if version == "unknown" else version


def _uv_status() -> dict[str, Any]:
    path = shutil.which("uv")
    result: dict[str, Any] = {
        "available": bool(path),
        "path": path,
        "version": None,
    }
    if path is None:
        result["message"] = "uv was not found in PATH."
        return result

    try:
        completed = subprocess.run(
            [path, "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        result.update(
            {
                "available": False,
                "message": f"Failed to run uv --version: {exc}",
            }
        )
        return result

    version_text = (completed.stdout or completed.stderr).strip()
    result["version"] = version_text or None
    if completed.returncode != 0:
        result.update(
            {
                "available": False,
                "message": f"uv --version exited with {completed.returncode}.",
            }
        )
    return result


def _doctor_check(
    checks: list[dict[str, Any]],
    *,
    name: str,
    ok: bool,
    severity: str,
    message: str,
    **payload: Any,
) -> None:
    check = {
        "name": name,
        "ok": ok,
        "severity": "info" if ok else severity,
        "message": message,
    }
    check.update(payload)
    checks.append(check)


def _doctor_overall_ok(checks: list[dict[str, Any]]) -> bool:
    return all(check["ok"] or check["severity"] != "error" for check in checks)


def _doctor_status(checks: list[dict[str, Any]]) -> str:
    if not _doctor_overall_ok(checks):
        return "fail"
    if any(not check["ok"] for check in checks):
        return "warn"
    return "pass"


def _doctor_decision(checks: list[dict[str, Any]]) -> dict[str, Any]:
    checks_by_name = {check["name"]: check for check in checks}
    blocking_checks = [
        check["name"]
        for check in checks
        if not check["ok"] and check["severity"] == "error"
    ]
    warning_checks = [
        check["name"]
        for check in checks
        if not check["ok"] and check["severity"] == "warning"
    ]
    api_check = checks_by_name.get("api", {})
    session_smoke_check = checks_by_name.get("session-smoke")
    api_verified = bool(api_check.get("ok") and not api_check.get("skipped"))
    session_smoke_requested = session_smoke_check is not None
    session_smoke_verified = bool(session_smoke_check and session_smoke_check.get("ok"))
    ready_for_browser_work = not blocking_checks and api_verified

    recommended_action = "continue"
    next_command = "browser-cli session create"
    if blocking_checks:
        next_command = "browser-cli doctor --json"
        if "credentials" in blocking_checks or "direct-url" in blocking_checks:
            recommended_action = "fix_configuration"
        elif "api" in blocking_checks:
            recommended_action = "fix_api_access"
        elif "session-smoke" in blocking_checks:
            recommended_action = "fix_session_lifecycle"
            next_command = "browser-cli doctor --smoke-session --json"
        else:
            recommended_action = "fix_errors"
    elif not api_verified:
        recommended_action = "run_api_check"
        next_command = "browser-cli doctor --json"
    elif warning_checks:
        recommended_action = "continue_with_warnings"

    return {
        "ready_for_browser_work": ready_for_browser_work,
        "api_verified": api_verified,
        "session_smoke_requested": session_smoke_requested,
        "session_smoke_verified": session_smoke_verified,
        "blocking_checks": blocking_checks,
        "warning_checks": warning_checks,
        "recommended_action": recommended_action,
        "next_command": next_command,
    }


def _doctor_workflow(decision: dict[str, Any]) -> dict[str, Any]:
    recommended_action = decision["recommended_action"]
    blocking_checks = list(decision["blocking_checks"])
    warning_checks = list(decision["warning_checks"])
    can_start_session = bool(decision["ready_for_browser_work"])

    commands: list[str]
    next_step: str
    if can_start_session:
        next_step = "start_browser_session"
        commands = ["browser-cli session create"]
        if not decision["session_smoke_verified"]:
            commands.append("browser-cli doctor --smoke-session --json")
    elif recommended_action == "run_api_check":
        next_step = "verify_api"
        commands = ["browser-cli doctor --json"]
    elif recommended_action == "fix_configuration":
        next_step = "configure_credentials"
        commands = [
            "browser-cli auth bootstrap",
            "browser-cli auth login",
            "browser-cli auth status",
            "browser-cli doctor --json",
        ]
    elif recommended_action == "fix_api_access":
        next_step = "fix_api_access"
        commands = [
            "browser-cli auth status",
            "browser-cli doctor --json",
            "browser-cli session list",
        ]
    elif recommended_action == "fix_session_lifecycle":
        next_step = "debug_session_lifecycle"
        commands = [
            "browser-cli doctor --smoke-session --json",
            "browser-cli session list --status active",
        ]
    else:
        next_step = "fix_doctor_errors"
        commands = [decision["next_command"]]

    return {
        "next_step": next_step,
        "can_start_browser_work": can_start_session,
        "primary_command": commands[0],
        "commands": commands,
        "blocking_checks": blocking_checks,
        "warning_checks": warning_checks,
        "smoke_session_recommended": bool(
            can_start_session and not decision["session_smoke_verified"]
        ),
        "notes": [
            "Use primary_command first; parse its JSON before continuing.",
            "Run smoke-session only for onboarding or session lifecycle debugging.",
            "Do not ask the user to paste API keys into chat.",
        ],
    }


def _doctor_session_payload(session: Any) -> dict[str, Any]:
    payload = session.model_dump(mode="json")
    return {
        key: payload.get(key)
        for key in (
            "session_id",
            "status",
            "browser_mode",
            "project_id",
            "created_at",
            "inspect_url",
        )
        if payload.get(key) is not None
    }


def _doctor_session_smoke(
    admin: LexmountBrowserAdmin,
    *,
    browser_mode: str,
) -> tuple[bool, str, dict[str, Any]]:
    session_id: str | None = None
    payload: dict[str, Any] = {
        "browser_mode": browser_mode,
        "created": False,
        "closed": False,
    }
    try:
        result = admin.create_session(
            context_id=None,
            create_context=False,
            context_mode="read_write",
            browser_mode=browser_mode,
            metadata=None,
        )
        session = result.session
        session_id = session.session_id
        payload.update(
            {
                "created": True,
                "session": _doctor_session_payload(session),
                "session_id": session_id,
            }
        )
    except Exception as exc:
        payload.update(_exception_brief(exc))
        return False, "Browser session smoke test failed to create a session.", payload

    if not session_id:
        return (
            False,
            "Browser session smoke test created a session without a session_id.",
            payload,
        )

    try:
        admin.close_session(session_id)
    except Exception as exc:
        payload.update(
            {
                "closed": False,
                "close_error": _exception_brief(exc),
            }
        )
        return (
            False,
            "Browser session smoke test created a session but failed to close it.",
            payload,
        )

    payload["closed"] = True
    return True, "Browser session smoke test created and closed a session.", payload


def cmd_doctor(args: argparse.Namespace) -> None:
    command = "doctor"
    checks: list[dict[str, Any]] = []
    next_steps: list[str] = []

    version = _browser_cli_version()
    _doctor_check(
        checks,
        name="browser-cli",
        ok=version is not None,
        severity="warning",
        message=(
            f"browser-cli package version is {version}."
            if version
            else "browser-cli package metadata was not found."
        ),
        version=version,
    )

    _doctor_check(
        checks,
        name="python",
        ok=True,
        severity="info",
        message=f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        executable=sys.executable,
        version=sys.version.split()[0],
    )

    uv = _uv_status()
    uv_payload = {key: value for key, value in uv.items() if key != "message"}
    _doctor_check(
        checks,
        name="uv",
        ok=bool(uv["available"]),
        severity="warning",
        message=uv.get("message")
        or (
            f"uv is available: {uv['version']}" if uv["version"] else "uv is available."
        ),
        **uv_payload,
    )
    if not uv["available"]:
        next_steps.append("Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh")

    env_status = {
        "LEXMOUNT_API_KEY": {"set": bool(os.getenv("LEXMOUNT_API_KEY"))},
        "LEXMOUNT_PROJECT_ID": {"set": bool(os.getenv("LEXMOUNT_PROJECT_ID"))},
        "LEXMOUNT_BASE_URL": {
            "set": bool(os.getenv("LEXMOUNT_BASE_URL")),
            "value": os.getenv("LEXMOUNT_BASE_URL") or "https://api.lexmount.cn",
            "defaulted": not bool(os.getenv("LEXMOUNT_BASE_URL")),
        },
        "LEXMOUNT_REGION": {
            "set": bool(os.getenv("LEXMOUNT_REGION")),
            "value": os.getenv("LEXMOUNT_REGION"),
        },
    }
    missing_required = [
        name
        for name in ("LEXMOUNT_API_KEY", "LEXMOUNT_PROJECT_ID")
        if not env_status[name]["set"]
    ]
    _doctor_check(
        checks,
        name="credentials",
        ok=not missing_required,
        severity="error",
        message=(
            "Required Lexmount environment variables are set."
            if not missing_required
            else "Missing required Lexmount environment variables: "
            + ", ".join(missing_required)
        ),
        missing=missing_required,
    )
    if missing_required:
        next_steps.append(
            "Log in to https://browser.lexmount.cn and export LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID."
        )

    direct_url_payload: dict[str, Any] | None = None
    try:
        direct_url = build_direct_connect_url()
        direct_url_payload = {
            "connect_url": _mask_direct_url_secret(direct_url),
            "masked": True,
        }
        _doctor_check(
            checks,
            name="direct-url",
            ok=True,
            severity="info",
            message="Direct websocket URL can be built from configuration.",
            **direct_url_payload,
        )
    except Exception as exc:
        _doctor_check(
            checks,
            name="direct-url",
            ok=False,
            severity="error",
            message="Failed to build direct websocket URL.",
            **_exception_brief(exc),
        )

    api_payload: dict[str, Any] | None = None
    if args.skip_api:
        _doctor_check(
            checks,
            name="api",
            ok=False,
            severity="warning",
            message="API connectivity check was skipped.",
            skipped=True,
        )
        next_steps.append(
            "Run browser-cli doctor without --skip-api to verify API access."
        )
    elif missing_required:
        _doctor_check(
            checks,
            name="api",
            ok=False,
            severity="error",
            message="API connectivity check skipped because credentials are missing.",
            skipped=True,
        )
    else:
        try:
            sessions = LexmountBrowserAdmin().list_sessions(status=None)
            api_payload = {
                "session_count": sessions.count,
                "pagination": (
                    sessions.pagination.model_dump(mode="json")
                    if sessions.pagination is not None
                    else None
                ),
            }
            _doctor_check(
                checks,
                name="api",
                ok=True,
                severity="info",
                message="Lexmount API is reachable with current credentials.",
                **api_payload,
            )
        except Exception as exc:
            _doctor_check(
                checks,
                name="api",
                ok=False,
                severity="error",
                message="Lexmount API connectivity check failed.",
                **_exception_brief(exc),
            )
            next_steps.append(
                "Check credentials, project access, network connectivity, and LEXMOUNT_BASE_URL."
            )

    session_smoke_payload: dict[str, Any] | None = None
    if args.smoke_session:
        if missing_required:
            session_smoke_payload = {
                "skipped": True,
                "reason": "missing_credentials",
            }
            _doctor_check(
                checks,
                name="session-smoke",
                ok=False,
                severity="error",
                message="Browser session smoke test skipped because credentials are missing.",
                **session_smoke_payload,
            )
        else:
            smoke_ok, smoke_message, session_smoke_payload = _doctor_session_smoke(
                LexmountBrowserAdmin(),
                browser_mode=args.smoke_browser_mode,
            )
            _doctor_check(
                checks,
                name="session-smoke",
                ok=smoke_ok,
                severity="error",
                message=smoke_message,
                **session_smoke_payload,
            )
            if not smoke_ok:
                next_steps.append(
                    "Check browser quota, project access, active sessions, and whether any smoke-test session needs manual cleanup."
                )

    ok = _doctor_overall_ok(checks)
    decision = _doctor_decision(checks)
    data = {
        "ok": ok,
        "command": command,
        "status": _doctor_status(checks),
        "version": {"browser_cli": version},
        "configuration": {"environment": env_status},
        "checks": checks,
        "decision": decision,
        "workflow": _doctor_workflow(decision),
        "next_steps": next_steps,
    }
    if direct_url_payload is not None:
        data["direct_url"] = direct_url_payload
    if api_payload is not None:
        data["api"] = api_payload
    if session_smoke_payload is not None:
        data["session_smoke"] = session_smoke_payload
    _json_dump(data, exit_code=0 if ok else 1)


def cmd_session_create(args: argparse.Namespace) -> None:
    command = "session.create"
    if not args.resolve_context and args.metadata_match:
        _failure(
            command,
            "invalid_arguments",
            "--metadata-match-json requires --resolve-context.",
        )
    if not args.resolve_context and args.context_status:
        _failure(
            command,
            "invalid_arguments",
            "--context-status requires --resolve-context.",
        )
    if not args.resolve_context and args.context_limit != 20:
        _failure(
            command,
            "invalid_arguments",
            "--context-limit requires --resolve-context.",
        )
    try:
        admin = LexmountBrowserAdmin()
        context_id = args.context_id
        create_context = args.create_context
        metadata = args.metadata
        context_resolution = None
        if args.resolve_context:
            context_id, context_resolution = _resolve_context_for_session(
                command,
                admin,
                args,
            )
            create_context = False
            metadata = None

        result = admin.create_session(
            context_id=context_id,
            create_context=create_context,
            context_mode=args.context_mode,
            browser_mode=args.browser_mode,
            metadata=metadata,
        )
    except Exception as exc:
        _failure_from_exception(command, exc)
    payload = _model_payload(result)
    if context_resolution is not None:
        payload["context_resolution"] = context_resolution
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
    _success(command, context=_context_payload(context))


def cmd_context_list(args: argparse.Namespace) -> None:
    command = "context.list"
    try:
        result = LexmountBrowserAdmin().list_contexts(
            status=args.status,
            limit=args.limit,
        )
    except Exception as exc:
        _failure_from_exception(command, exc)
    payload = _model_payload(result)
    contexts = getattr(result, "contexts", payload.get("contexts", []))
    payload["contexts"] = [_context_payload(context) for context in contexts]
    _success(command, **payload)


def cmd_context_get(args: argparse.Namespace) -> None:
    command = "context.get"
    try:
        context = LexmountBrowserAdmin().get_context(args.context_id)
    except Exception as exc:
        _failure_from_exception(command, exc)
    _success(command, context=_context_payload(context))


def cmd_context_delete(args: argparse.Namespace) -> None:
    command = "context.delete"
    try:
        LexmountBrowserAdmin().delete_context(args.context_id)
    except Exception as exc:
        _failure_from_exception(command, exc)
    _success(command, context_id=args.context_id, deleted=True)


def _context_resolution_summary(
    contexts: list[Any],
    *,
    metadata_match: dict[str, Any] | None,
) -> dict[str, Any]:
    all_payloads = [_context_payload(context) for context in contexts]
    payloads = [
        payload
        for payload in all_payloads
        if _metadata_matches(payload, metadata_match)
    ]
    available = [
        payload for payload in payloads if payload["reuse"]["can_reuse_now"] is True
    ]
    locked = [
        payload
        for payload in payloads
        if payload.get("status") and str(payload["status"]).lower() == "locked"
    ]
    return {
        "available_count": len(available),
        "locked_count": len(locked),
        "considered_count": len(payloads),
        "considered_contexts": payloads,
        "metadata_match": metadata_match,
        "matched_count": len(payloads),
        "total_count": len(all_payloads),
        "unmatched_count": len(all_payloads) - len(payloads),
    }


def _context_reuse_decision(
    context_payload: dict[str, Any],
    *,
    created: bool,
) -> dict[str, Any]:
    reuse = context_payload["reuse"]
    can_reuse = reuse["can_reuse_now"] is True
    reason = "context_created" if created and can_reuse else reuse["reason"]

    if can_reuse:
        action = "start_session"
        should_create_context = False
        should_close_session = False
        selected_context_id = context_payload.get("context_id")
    elif reuse["reason"] == "context_locked":
        action = "close_or_create_context"
        should_create_context = True
        should_close_session = True
        selected_context_id = None
    elif reuse["reason"] == "context_not_available":
        action = "create_context"
        should_create_context = True
        should_close_session = False
        selected_context_id = None
    else:
        action = "inspect_context"
        should_create_context = False
        should_close_session = False
        selected_context_id = None

    return {
        "action": action,
        "reason": reason,
        "can_start_session": can_reuse,
        "should_create_context": should_create_context,
        "should_close_session": should_close_session,
        "selected_context_id": selected_context_id,
        "recommended_context_mode": reuse["recommended_context_mode"],
        "recommended_session_command": reuse["recommended_session_command"],
    }


def _context_missing_decision(summary: dict[str, Any]) -> dict[str, Any]:
    locked_count = summary["locked_count"]
    considered_count = summary["considered_count"]

    if locked_count:
        action = "close_or_create_context"
        reason = (
            "only_locked_contexts"
            if locked_count == considered_count
            else "no_available_context"
        )
    else:
        action = "create_context"
        if considered_count == 0 and summary.get("total_count", 0) == 0:
            reason = "no_contexts_found"
        elif considered_count == 0 and summary.get("metadata_match"):
            reason = "no_matching_contexts"
        else:
            reason = "no_available_context"

    return {
        "action": action,
        "reason": reason,
        "can_start_session": False,
        "should_create_context": True,
        "should_close_session": bool(locked_count),
        "selected_context_id": None,
        "recommended_context_mode": None,
        "recommended_session_command": None,
    }


def _context_metadata_mismatch_decision() -> dict[str, Any]:
    return {
        "action": "create_context",
        "reason": "metadata_mismatch",
        "can_start_session": False,
        "should_create_context": True,
        "should_close_session": False,
        "selected_context_id": None,
        "recommended_context_mode": None,
        "recommended_session_command": None,
    }


def _context_resolution_failure(
    command: str,
    message: str,
    *,
    decision: dict[str, Any],
    context: dict[str, Any] | None = None,
    **payload: Any,
) -> NoReturn:
    _failure(
        command,
        "context_not_reusable",
        message,
        context_resolution={
            "resolved": False,
            "created": False,
            "decision": decision,
            "context": context,
            "context_id": context.get("context_id") if context else None,
            **payload,
        },
    )


def _resolved_context_payload(
    context_payload: dict[str, Any],
    *,
    created: bool,
    metadata_match: dict[str, Any] | None,
    **payload: Any,
) -> dict[str, Any]:
    return {
        "resolved": context_payload["reuse"]["can_reuse_now"] is True,
        "created": created,
        "decision": _context_reuse_decision(context_payload, created=created),
        "context": context_payload,
        "context_id": context_payload.get("context_id"),
        "metadata_match": metadata_match,
        "recommended_session_command": context_payload["reuse"][
            "recommended_session_command"
        ],
        "next_steps": context_payload["reuse"]["next_steps"],
        **payload,
    }


def _resolve_context_for_session(
    command: str,
    admin: LexmountBrowserAdmin,
    args: argparse.Namespace,
) -> tuple[str | None, dict[str, Any]]:
    create_metadata = (
        args.metadata if args.metadata is not None else args.metadata_match
    )
    if (
        args.create_context
        and args.metadata_match
        and not _metadata_matches({"metadata": create_metadata}, args.metadata_match)
    ):
        _failure(
            command,
            "metadata_mismatch",
            "--metadata-json must contain every key and value from --metadata-match-json.",
            metadata_match=args.metadata_match,
            metadata=create_metadata,
        )

    if args.context_id:
        context_payload = _context_payload(admin.get_context(args.context_id))
        metadata_matches = _metadata_matches(context_payload, args.metadata_match)
        if not metadata_matches:
            _context_resolution_failure(
                command,
                "The explicit context metadata does not match --metadata-match-json.",
                decision=_context_metadata_mismatch_decision(),
                context=context_payload,
                metadata_match=args.metadata_match,
                metadata_matches=False,
                next_steps=[
                    "Use a context whose metadata matches the requested persistent login state.",
                    "Pass --create-context with --resolve-context to create a matching context.",
                ],
            )
        resolution = _resolved_context_payload(
            context_payload,
            created=False,
            metadata_match=args.metadata_match,
            metadata_matches=True,
        )
        if resolution["decision"]["can_start_session"] is not True:
            _context_resolution_failure(
                command,
                "The explicit context is not available for a new read/write session.",
                decision=resolution["decision"],
                context=context_payload,
                metadata_match=args.metadata_match,
                metadata_matches=True,
                next_steps=context_payload["reuse"]["next_steps"],
            )
        return context_payload.get("context_id"), resolution

    listed = admin.list_contexts(status=args.context_status, limit=args.context_limit)
    contexts = list(listed.contexts)
    summary = _context_resolution_summary(
        contexts,
        metadata_match=args.metadata_match,
    )
    resolution_summary = {key: value for key, value in summary.items()}
    resolution_summary.pop("metadata_match", None)
    selected = next(
        (
            payload
            for payload in summary["considered_contexts"]
            if payload["reuse"]["can_reuse_now"] is True
        ),
        None,
    )
    if selected:
        return selected.get("context_id"), _resolved_context_payload(
            selected,
            created=False,
            metadata_match=args.metadata_match,
            status_filter=args.context_status,
            limit=args.context_limit,
            **resolution_summary,
        )

    if args.create_context:
        context_payload = _context_payload(
            admin.create_context(metadata=create_metadata)
        )
        resolution = _resolved_context_payload(
            context_payload,
            created=True,
            metadata_match=args.metadata_match,
            status_filter=args.context_status,
            limit=args.context_limit,
            **resolution_summary,
        )
        if resolution["decision"]["can_start_session"] is not True:
            _context_resolution_failure(
                command,
                "The newly created context is not available for a new session.",
                decision=resolution["decision"],
                context=context_payload,
                status_filter=args.context_status,
                limit=args.context_limit,
                next_steps=context_payload["reuse"]["next_steps"],
                **summary,
            )
        return context_payload.get("context_id"), resolution

    decision = _context_missing_decision(summary)
    _context_resolution_failure(
        command,
        "No available context matched the requested persistent login state.",
        decision=decision,
        status_filter=args.context_status,
        limit=args.context_limit,
        next_steps=[
            "Pass --create-context with --resolve-context to create a reusable context.",
            "Run browser-cli context list to inspect locked and available contexts.",
        ],
        **summary,
    )


def cmd_context_resolve(args: argparse.Namespace) -> None:
    command = "context.resolve"
    admin = LexmountBrowserAdmin()
    create_metadata = (
        args.metadata if args.metadata is not None else args.metadata_match
    )
    if (
        args.create_if_missing
        and args.metadata_match
        and not _metadata_matches({"metadata": create_metadata}, args.metadata_match)
    ):
        _failure(
            command,
            "metadata_mismatch",
            "--metadata-json must contain every key and value from --metadata-match-json.",
            metadata_match=args.metadata_match,
            metadata=create_metadata,
        )
    try:
        if args.context_id:
            context = admin.get_context(args.context_id)
            context_payload = _context_payload(context)
            metadata_matches = _metadata_matches(context_payload, args.metadata_match)
            if not metadata_matches:
                _success(
                    command,
                    resolved=False,
                    created=False,
                    decision=_context_metadata_mismatch_decision(),
                    context=context_payload,
                    context_id=context_payload.get("context_id"),
                    metadata_match=args.metadata_match,
                    metadata_matches=False,
                    recommended_session_command=None,
                    next_steps=[
                        "Use a context whose metadata matches the requested persistent login state.",
                        "Run browser-cli context resolve --create-if-missing with the same --metadata-match-json.",
                    ],
                )
            resolved = context_payload["reuse"]["can_reuse_now"] is True
            decision = _context_reuse_decision(context_payload, created=False)
            _success(
                command,
                resolved=resolved,
                created=False,
                decision=decision,
                context=context_payload,
                context_id=context_payload.get("context_id"),
                metadata_match=args.metadata_match,
                metadata_matches=True,
                recommended_session_command=context_payload["reuse"][
                    "recommended_session_command"
                ],
                next_steps=context_payload["reuse"]["next_steps"],
            )

        listed = admin.list_contexts(status=args.status, limit=args.limit)
        contexts = list(listed.contexts)
        summary = _context_resolution_summary(
            contexts,
            metadata_match=args.metadata_match,
        )
        selected = next(
            (
                payload
                for payload in summary["considered_contexts"]
                if payload["reuse"]["can_reuse_now"] is True
            ),
            None,
        )
        if selected:
            decision = _context_reuse_decision(selected, created=False)
            _success(
                command,
                resolved=True,
                created=False,
                decision=decision,
                context=selected,
                context_id=selected.get("context_id"),
                status_filter=args.status,
                limit=args.limit,
                recommended_session_command=selected["reuse"][
                    "recommended_session_command"
                ],
                next_steps=selected["reuse"]["next_steps"],
                **summary,
            )

        if args.create_if_missing:
            context = admin.create_context(metadata=create_metadata)
            context_payload = _context_payload(context)
            decision = _context_reuse_decision(context_payload, created=True)
            _success(
                command,
                resolved=context_payload["reuse"]["can_reuse_now"] is True,
                created=True,
                decision=decision,
                context=context_payload,
                context_id=context_payload.get("context_id"),
                status_filter=args.status,
                limit=args.limit,
                recommended_session_command=context_payload["reuse"][
                    "recommended_session_command"
                ],
                next_steps=context_payload["reuse"]["next_steps"],
                **summary,
            )

    except Exception as exc:
        _failure_from_exception(command, exc)

    _success(
        command,
        resolved=False,
        created=False,
        decision=_context_missing_decision(summary),
        context=None,
        context_id=None,
        status_filter=args.status,
        limit=args.limit,
        recommended_session_command=None,
        next_steps=[
            "Run browser-cli context resolve --create-if-missing to create a reusable context.",
            "Run browser-cli context list to inspect locked and available contexts.",
        ],
        **summary,
    )


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
    reveal_connect_url = bool(getattr(args, "reveal_connect_url", False))
    _success(
        command,
        reveal_fields={"connect_url"} if reveal_connect_url else None,
        session_id=getattr(args, "session_id", None),
        **_masked_connect_url_payload(
            connect_url,
            reveal_connect_url=reveal_connect_url,
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
        reveal_fields={"connect_url"} if reveal_url else None,
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
        "--resolve-context",
        action="store_true",
        help=(
            "Resolve an available context before creating the session. "
            "With --create-context, create one when no match is available."
        ),
    )
    parser.add_argument(
        "--metadata-match-json",
        dest="metadata_match",
        type=_parse_metadata_json,
        help="JSON object that resolved context metadata must contain",
    )
    parser.add_argument(
        "--context-status",
        help="Optional context status filter used with --resolve-context",
    )
    parser.add_argument(
        "--context-limit",
        type=int,
        default=20,
        help="Maximum contexts to inspect when --resolve-context lists contexts",
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
        parser_class=BrowserCliArgumentParser,
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
        parser_class=BrowserCliArgumentParser,
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

    context_resolve = context_subparsers.add_parser(
        "resolve",
        help="Find or create an available context for persistent state reuse",
    )
    context_resolve.add_argument("--context-id", help="Inspect one explicit context")
    context_resolve.add_argument("--status", help="Optional status filter")
    context_resolve.add_argument("--limit", type=int, default=20)
    context_resolve.add_argument(
        "--create-if-missing",
        action="store_true",
        help="Create a new context when no available context is found",
    )
    context_resolve.add_argument(
        "--metadata-json",
        dest="metadata",
        type=_parse_metadata_json,
        help="JSON object used when --create-if-missing creates a context",
    )
    context_resolve.add_argument(
        "--metadata-match-json",
        dest="metadata_match",
        type=_parse_metadata_json,
        help="JSON object that candidate context metadata must contain",
    )
    context_resolve.set_defaults(func=cmd_context_resolve)

    context_delete = context_subparsers.add_parser("delete", help="Delete context")
    context_delete.add_argument("--context-id", required=True)
    context_delete.set_defaults(func=cmd_context_delete)


def _add_action_commands(subparsers: argparse._SubParsersAction[Any]) -> None:
    action = subparsers.add_parser("action", help="Run browser actions")
    action_subparsers = action.add_subparsers(
        dest="action_command",
        required=True,
        parser_class=BrowserCliArgumentParser,
    )

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
    case_subparsers = case.add_subparsers(
        dest="case_command",
        required=True,
        parser_class=BrowserCliArgumentParser,
    )

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
    doctor = subparsers.add_parser(
        "doctor",
        help="Check local browser-cli and Lexmount API configuration",
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output. JSON is the default for all browser-cli commands.",
    )
    doctor.add_argument(
        "--skip-api",
        action="store_true",
        help="Skip the live Lexmount API connectivity check.",
    )
    doctor.add_argument(
        "--smoke-session",
        action="store_true",
        help="Create and close a light browser session to verify session lifecycle.",
    )
    doctor.add_argument(
        "--smoke-browser-mode",
        default="light",
        type=_normalize_browser_mode,
        help="Browser mode used by --smoke-session. Default: light.",
    )
    doctor.set_defaults(func=cmd_doctor)

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

    parser = BrowserCliArgumentParser(
        prog="browser-cli",
        description="Lexmount browser operation CLI",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_package_version()}",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=BrowserCliArgumentParser,
    )

    _add_auth_commands(subparsers)
    _add_session_commands(subparsers)
    _add_context_commands(subparsers)
    _add_action_commands(subparsers)
    _add_case_commands(subparsers)
    _add_alias_commands(subparsers)

    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the Lexmount browser operation CLI."""

    global _current_parse_argv
    _current_parse_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
