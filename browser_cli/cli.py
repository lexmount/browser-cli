"""Command-line entrypoint for Lexmount browser operations."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import shlex
import sys
import webbrowser
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from browser_cli import __version__
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
AGENT_DOCTOR_COMMAND = "browser-cli doctor --json"
DEFAULT_FILE_INPUT_MAX_BYTES = 10 * 1024 * 1024
DEVICE_TOKEN_CREDENTIALS_FILE_ENV = "LEXMOUNT_BROWSER_CREDENTIALS_FILE"
DEVICE_TOKEN_REFRESH_WINDOW_SECONDS = 300
DOCTOR_REQUIRED_COMMANDS = (
    "commands",
    "version",
    "doctor",
    "auth.status",
    "auth.login",
    "auth.export-env",
    "context.pick",
    "context.status",
    "session.create",
    "session.close",
    "action.open-url",
    "action.wait-selector",
    "action.click",
    "action.type",
    "action.screenshot",
    "action.eval",
    "action.snapshot",
    "action.page-info",
    "action.get-text",
    "action.exists",
    "action.wait-state",
    "action.scroll",
    "action.select-option",
    "action.check",
    "action.uncheck",
    "action.hover",
    "action.press",
    "action.click-text",
    "action.click-role",
    "action.fill-label",
    "action.accessibility-snapshot",
    "action.interactive-snapshot",
    "action.interactive-only-snapshot",
    "action.wait-dialog",
    "action.wait-frame",
    "action.network-snapshot",
    "action.wait-network",
    "action.console-snapshot",
    "action.wait-console",
)
DOCTOR_REQUIRED_WORKFLOWS = (
    "setup_and_verify",
    "connect_from_codex_auth",
    "one_off_page_task",
    "persistent_login_state",
)
DOCTOR_REQUIRED_WORKFLOW_STEPS = {
    "setup_and_verify": (
        "auth_status",
        "doctor",
    ),
    "connect_from_codex_auth": (
        "auth_status",
        "auth_login",
        "export_env",
        "doctor",
    ),
    "one_off_page_task": (
        "create_session",
        "open_url",
        "find_targets",
        "close_session",
    ),
    "persistent_login_state": (
        "dry_run_context_pick",
        "create_session_with_context",
        "close_session",
    ),
}
COMMAND_ALIASES = {
    "action.interactive-only-snapshot": "action.interactive-snapshot",
}
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
CONTEXT_REUSABLE_STATUSES = {"available", "ready", "idle", "free", "unlocked"}
CONTEXT_LOCKED_STATUSES = {
    "locked",
    "busy",
    "in_use",
    "active",
    "running",
    "reserved",
    "leased",
    "occupied",
}
CONTEXT_UNAVAILABLE_STATUSES = {
    "unavailable",
    "failed",
    "error",
    "closed",
    "deleted",
    "deleting",
    "expired",
    "disabled",
    "archived",
    "terminated",
    "stopped",
}
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


def _parser_has_option(parser: argparse.ArgumentParser, option: str) -> bool:
    return any(option in action.option_strings for action in parser._actions)


def _add_json_compatibility_flag(parser: argparse.ArgumentParser) -> None:
    if not _parser_has_option(parser, "--json"):
        parser.add_argument(
            "--json",
            action="store_true",
            help=argparse.SUPPRESS,
        )
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                _add_json_compatibility_flag(subparser)


def _subparser_actions(
    parser: argparse.ArgumentParser,
) -> list[argparse._SubParsersAction[Any]]:
    return [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]


def _preferred_option(action: argparse.Action) -> str | None:
    if not action.option_strings or action.help is argparse.SUPPRESS:
        return None
    for option in action.option_strings:
        if option.startswith("--"):
            return option
    return action.option_strings[0]


def _catalog_default(value: Any) -> Any:
    if value is argparse.SUPPRESS:
        return None
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def _catalog_option(action: argparse.Action) -> dict[str, Any] | None:
    if not action.option_strings or action.help is argparse.SUPPRESS:
        return None
    if isinstance(action, argparse._HelpAction):
        return None

    takes_value = not isinstance(
        action,
        argparse._StoreTrueAction | argparse._StoreFalseAction,
    )
    option: dict[str, Any] = {
        "flags": list(action.option_strings),
        "dest": action.dest,
        "required": bool(getattr(action, "required", False)),
        "takes_value": takes_value,
    }
    if action.help:
        option["help"] = str(action.help)
    choices = getattr(action, "choices", None)
    if choices is not None:
        option["choices"] = [str(choice) for choice in choices]
    default = _catalog_default(getattr(action, "default", None))
    if default is not None:
        option["default"] = default
    nargs = getattr(action, "nargs", None)
    if nargs not in (None, 0):
        option["nargs"] = nargs
    if isinstance(action, argparse._AppendAction):
        option["repeatable"] = True
    return option


def _catalog_required_one_of(
    parser: argparse.ArgumentParser,
) -> list[list[str]]:
    groups: list[list[str]] = []
    for group in parser._mutually_exclusive_groups:
        if not group.required:
            continue
        options = [
            option
            for action in group._group_actions
            if (option := _preferred_option(action)) is not None
        ]
        if options:
            groups.append(options)
    return groups


def _catalog_leaf_commands(
    parser: argparse.ArgumentParser,
) -> list[dict[str, Any]]:
    subparser_actions = _subparser_actions(parser)
    if subparser_actions:
        commands: list[dict[str, Any]] = []
        for subparser_action in subparser_actions:
            for subparser in subparser_action.choices.values():
                commands.extend(_catalog_leaf_commands(subparser))
        return commands

    name = _command_from_prog(parser.prog)
    path = name.split(".")
    options = [
        option
        for action in parser._actions
        if (option := _catalog_option(action)) is not None
    ]
    required_options = [
        option
        for action in parser._actions
        if getattr(action, "required", False)
        and (option := _preferred_option(action)) is not None
    ]
    target_options = {
        "--session-id",
        "--connect-url",
        "--direct-url",
    }
    option_flags = {
        flag
        for option in options
        for flag in option.get("flags", [])
        if isinstance(flag, str)
    }
    command: dict[str, Any] = {
        "name": name,
        "path": path,
        "group": path[0],
        "usage": parser.format_usage().strip(),
        "options": options,
        "required_options": required_options,
        "required_one_of": _catalog_required_one_of(parser),
    }
    if target_options.intersection(option_flags):
        command["browser_target"] = {
            "required": True,
            "exactly_one_of": sorted(target_options.intersection(option_flags)),
        }
    if name in COMMAND_ALIASES:
        command["alias_of"] = COMMAND_ALIASES[name]
        command["canonical_name"] = COMMAND_ALIASES[name]
    aliases = sorted(
        alias for alias, canonical in COMMAND_ALIASES.items() if canonical == name
    )
    if aliases:
        command["aliases"] = aliases
    return [command]


def _command_catalog() -> dict[str, Any]:
    parser = build_parser()
    commands = _catalog_leaf_commands(parser)
    groups = _dedupe_preserving_order([str(command["group"]) for command in commands])
    return {
        "schema_version": 1,
        "groups": groups,
        "command_count": len(commands),
        "commands": commands,
        "json_output": {
            "always_json": True,
            "json_flag": "accepted as a compatibility no-op at the top level and after subcommands",
            "argument_errors": "emit JSON with error=argument_error and usage",
        },
        "secret_policy": {
            "default_masking": True,
            "never_paste": [
                "LEXMOUNT_API_KEY",
                "access_token",
                "refresh_token",
                "full direct connect URLs containing api_key",
            ],
        },
        "agent_entrypoints": {
            "setup": [
                "browser-cli auth status",
                "browser-cli auth refresh",
                "browser-cli auth login",
                "browser-cli auth export-env",
                AGENT_DOCTOR_COMMAND,
                "browser-cli doctor --smoke-session",
            ],
            "connect_from_codex_auth": [
                "browser-cli auth status",
                "browser-cli auth login",
                "browser-cli auth login --open",
                "browser-cli auth export-env",
                AGENT_DOCTOR_COMMAND,
            ],
            "one_off_page_task": [
                "browser-cli session create",
                "browser-cli action open-url --session-id <session_id> --url <url>",
                "browser-cli action page-info --session-id <session_id>",
                "browser-cli action interactive-snapshot --session-id <session_id>",
                "browser-cli session close --session-id <session_id>",
            ],
            "persistent_login_state": [
                'browser-cli context pick --metadata-json \'{"purpose":"codex-login"}\' --create-if-missing --dry-run',
                'browser-cli session create --context-metadata-json \'{"purpose":"codex-login"}\' --create-context-if-missing --context-mode read_write',
            ],
        },
        "agent_workflows": {
            "setup_and_verify": {
                "purpose": "Verify local credentials and browser action readiness before the first browser action.",
                "steps": [
                    {
                        "id": "auth_status",
                        "command": "browser-cli auth status",
                        "read": [
                            "configured",
                            "auth_source",
                            "runtime_auth_usable",
                            "device_token.valid",
                        ],
                    },
                    {
                        "id": "doctor",
                        "command": AGENT_DOCTOR_COMMAND,
                        "success_condition": "ok=true and ready_for_browser_actions=true",
                        "on_failure_read": [
                            "failed_checks",
                            "warning_checks",
                            "repair_plan.commands",
                            "repair_plan.connect_from_codex.url",
                        ],
                    },
                    {
                        "id": "smoke_session",
                        "command": "browser-cli doctor --smoke-session",
                        "optional": True,
                        "success_condition": "browser_smoke_session.status=pass",
                        "read": [
                            "browser_smoke_session.created",
                            "browser_smoke_session.closed",
                        ],
                    },
                ],
            },
            "connect_from_codex_auth": {
                "purpose": "Guide a local user through Connect from Codex credentials and verify browser readiness.",
                "steps": [
                    {
                        "id": "auth_status",
                        "command": "browser-cli auth status",
                        "read": [
                            "configured",
                            "auth_source",
                            "runtime_auth_usable",
                            "device_token.valid",
                        ],
                    },
                    {
                        "id": "auth_login",
                        "command": "browser-cli auth login",
                        "read": [
                            "connect_from_codex.url",
                            "connect_from_codex.requested_scope_details",
                            "handoff.setup_blocks",
                            "handoff.verification.doctor_command",
                        ],
                        "on_user_action": "Open connect_from_codex.url or run browser-cli auth login --open, then paste generated env commands into the local shell only.",
                    },
                    {
                        "id": "export_env",
                        "command": "browser-cli auth export-env",
                        "local_shell_only": True,
                        "secret_handling": "Do not paste revealed API keys into chat, logs, docs, or commits.",
                    },
                    {
                        "id": "doctor",
                        "command": AGENT_DOCTOR_COMMAND,
                        "success_condition": "ok=true and ready_for_browser_actions=true",
                        "on_failure_read": [
                            "failed_checks",
                            "repair_plan.commands",
                            "repair_plan.connect_from_codex.url",
                        ],
                    },
                ],
            },
            "one_off_page_task": {
                "purpose": "Create a temporary browser session, inspect or operate one page, then close the session.",
                "steps": [
                    {
                        "id": "create_session",
                        "command": "browser-cli session create",
                        "read": ["session_id"],
                    },
                    {
                        "id": "open_url",
                        "command": "browser-cli action open-url --session-id <session_id> --url <url>",
                        "success_condition": "ok=true",
                    },
                    {
                        "id": "inspect_page",
                        "command": "browser-cli action page-info --session-id <session_id>",
                        "read": ["title", "url"],
                    },
                    {
                        "id": "find_targets",
                        "command": "browser-cli action interactive-snapshot --session-id <session_id>",
                        "read": ["interactive_elements", "title", "url"],
                    },
                    {
                        "id": "close_session",
                        "command": "browser-cli session close --session-id <session_id>",
                        "cleanup": True,
                    },
                ],
            },
            "persistent_login_state": {
                "purpose": "Reuse or create a persistent context for login state, cookies, and storage.",
                "steps": [
                    {
                        "id": "dry_run_context_pick",
                        "command": 'browser-cli context pick --metadata-json \'{"purpose":"codex-login"}\' --create-if-missing --dry-run',
                        "read": [
                            "selection_summary.recommended_next_action",
                            "selection_summary.decision_reason",
                            "selection_summary.locked_matches",
                            "selection_summary.would_create",
                        ],
                    },
                    {
                        "id": "create_session_with_context",
                        "command": 'browser-cli session create --context-metadata-json \'{"purpose":"codex-login"}\' --create-context-if-missing --context-mode read_write',
                        "read": [
                            "session_id",
                            "context_reuse.selected",
                            "context_reuse.created",
                            "context_reuse.selection_summary.recommended_next_action",
                        ],
                    },
                    {
                        "id": "close_session",
                        "command": "browser-cli session close --session-id <session_id>",
                        "cleanup": True,
                    },
                ],
            },
        },
    }


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
        return distribution_version(distribution)
    except PackageNotFoundError:
        return None


def _browser_cli_version() -> tuple[str, str]:
    installed_version = _package_version("browser-cli")
    if installed_version is not None:
        return installed_version, "package_metadata"
    return __version__, "package_fallback"


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
    connect_from_codex: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fix: dict[str, Any] = {"code": code}
    if commands:
        fix["commands"] = commands
    if env:
        fix["env"] = env
    if guidance:
        fix["guidance"] = guidance
    if connect_from_codex:
        fix["connect_from_codex"] = connect_from_codex
    return fix


def _doctor_check_names(
    checks: list[dict[str, Any]],
    *,
    status: str,
) -> list[str]:
    return [str(check["name"]) for check in checks if check.get("status") == status]


def _doctor_repair_plan(checks: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = {"fail", "warn", "skipped"}
    commands: list[str] = []
    env: list[str] = []
    guidance: list[str] = []
    fixes: list[dict[str, Any]] = []
    connect_from_codex: dict[str, Any] | None = None

    for check in checks:
        if check.get("status") not in statuses:
            continue
        fix = check.get("fix")
        if not isinstance(fix, dict):
            continue

        item: dict[str, Any] = {
            "check": check.get("name"),
            "status": check.get("status"),
            "code": fix.get("code"),
        }
        for key in ("commands", "env", "guidance"):
            values = fix.get(key)
            if isinstance(values, list):
                item[key] = values
        fix_connect_from_codex = fix.get("connect_from_codex")
        if isinstance(fix_connect_from_codex, dict):
            item["connect_from_codex"] = fix_connect_from_codex
            if connect_from_codex is None:
                connect_from_codex = fix_connect_from_codex
        fixes.append(item)

        commands.extend(str(value) for value in fix.get("commands", []))
        env.extend(str(value) for value in fix.get("env", []))
        guidance.extend(str(value) for value in fix.get("guidance", []))

    repair_plan: dict[str, Any] = {
        "required": bool(_doctor_check_names(checks, status="fail")),
        "recommended": bool(fixes),
        "commands": _dedupe_preserving_order(commands),
        "env": _dedupe_preserving_order(env),
        "guidance": _dedupe_preserving_order(guidance),
        "fixes": fixes,
    }
    if connect_from_codex:
        repair_plan["connect_from_codex"] = connect_from_codex
    return repair_plan


def _doctor_workflow_step_names(workflow: Any) -> set[str]:
    if not isinstance(workflow, dict):
        return set()
    steps = workflow.get("steps")
    if not isinstance(steps, list):
        return set()
    return {
        str(step.get("id"))
        for step in steps
        if isinstance(step, dict) and step.get("id")
    }


def _doctor_command_catalog_check() -> dict[str, Any]:
    try:
        catalog = _command_catalog()
    except Exception as exc:
        return _doctor_check(
            "command_catalog",
            "warn",
            "Command catalog could not be built.",
            error=exc.__class__.__name__,
            fix=_doctor_fix(
                "verify_command_catalog",
                commands=[
                    "browser-cli commands --names-only",
                    "uv tool install git+https://github.com/lexmount/browser-cli.git",
                ],
                guidance=[
                    "The Codex Skill relies on command discovery before writing custom JavaScript.",
                    "Upgrade or reinstall browser-cli if command discovery fails.",
                ],
            ),
        )

    command_names = {
        str(command.get("name"))
        for command in catalog.get("commands", [])
        if isinstance(command, dict)
    }
    workflows = catalog.get("agent_workflows")
    workflow_names = set(workflows) if isinstance(workflows, dict) else set()
    missing_commands = [
        command for command in DOCTOR_REQUIRED_COMMANDS if command not in command_names
    ]
    missing_workflows = [
        workflow
        for workflow in DOCTOR_REQUIRED_WORKFLOWS
        if workflow not in workflow_names
    ]
    required_workflow_steps = {
        workflow: list(steps)
        for workflow, steps in DOCTOR_REQUIRED_WORKFLOW_STEPS.items()
    }
    missing_workflow_steps: dict[str, list[str]] = {}
    if isinstance(workflows, dict):
        for workflow, required_steps in DOCTOR_REQUIRED_WORKFLOW_STEPS.items():
            if workflow not in workflow_names:
                continue
            step_names = _doctor_workflow_step_names(workflows.get(workflow))
            missing_steps = [step for step in required_steps if step not in step_names]
            if missing_steps:
                missing_workflow_steps[workflow] = missing_steps

    if missing_commands or missing_workflows or missing_workflow_steps:
        return _doctor_check(
            "command_catalog",
            "warn",
            "Command catalog is missing commands, workflows, or workflow steps expected by the Codex Skill.",
            schema_version=catalog.get("schema_version"),
            command_count=len(command_names),
            workflow_count=len(workflow_names),
            required_commands=list(DOCTOR_REQUIRED_COMMANDS),
            missing_required_commands=missing_commands,
            required_workflows=list(DOCTOR_REQUIRED_WORKFLOWS),
            missing_required_workflows=missing_workflows,
            required_workflow_steps=required_workflow_steps,
            missing_required_workflow_steps=missing_workflow_steps,
            fix=_doctor_fix(
                "upgrade_browser_cli_command_surface",
                commands=[
                    "browser-cli commands --names-only",
                    "browser-cli commands",
                    "uv tool install git+https://github.com/lexmount/browser-cli.git",
                ],
                guidance=[
                    "Upgrade browser-cli before relying on the full Codex Skill workflow.",
                    "Use `browser-cli commands --group action --names-only` to inspect available actions.",
                    "Use `browser-cli commands` to inspect structured agent_workflows and their steps.",
                ],
            ),
        )

    return _doctor_check(
        "command_catalog",
        "pass",
        "Command catalog includes the commands expected by the Codex Skill.",
        schema_version=catalog.get("schema_version"),
        command_count=len(command_names),
        workflow_count=len(workflow_names),
        required_commands=list(DOCTOR_REQUIRED_COMMANDS),
        missing_required_commands=[],
        required_workflows=list(DOCTOR_REQUIRED_WORKFLOWS),
        missing_required_workflows=[],
        required_workflow_steps=required_workflow_steps,
        missing_required_workflow_steps={},
    )


def _doctor_error_name(exc: Exception) -> str:
    info = getattr(exc, "lexmount_error_info", None)
    if isinstance(info, LexmountErrorInfo):
        error = info.payload().get("error")
        if error:
            return str(error)
    return exc.__class__.__name__


def _doctor_session_id(payload: dict[str, Any]) -> str | None:
    session_id = payload.get("session_id")
    if session_id:
        return str(session_id)
    session = payload.get("session")
    if isinstance(session, dict) and session.get("session_id"):
        return str(session["session_id"])
    return None


def _doctor_smoke_session_check(admin: Any) -> dict[str, Any]:
    session_id: str | None = None
    try:
        result = admin.create_session(
            context_id=None,
            create_context=False,
            context_mode="read_write",
            browser_mode="normal",
            metadata={"purpose": "browser-cli-doctor-smoke"},
        )
    except Exception as exc:
        return _doctor_check(
            "browser_smoke_session",
            "fail",
            _mask_sensitive_text(str(exc)),
            stage="create",
            created=False,
            closed=False,
            error=_doctor_error_name(exc),
            fix=_doctor_fix(
                "verify_browser_session_creation",
                commands=[
                    "browser-cli auth status",
                    "browser-cli doctor --smoke-session",
                ],
                guidance=[
                    "Confirm the API key can create browser sessions for this project.",
                    "Check project session quotas and key scopes in browser.lexmount.cn.",
                ],
            ),
        )

    payload = _model_payload(result)
    session_id = _doctor_session_id(payload)
    if not session_id:
        return _doctor_check(
            "browser_smoke_session",
            "fail",
            "Smoke session was created, but the response did not include a session_id to close.",
            stage="response",
            created=True,
            closed=False,
            session=_sanitize_failure_value(payload.get("session")),
            fix=_doctor_fix(
                "verify_browser_session_response",
                commands=[
                    "browser-cli doctor --smoke-session",
                ],
                guidance=[
                    "Confirm the browser session API response includes session.session_id.",
                ],
            ),
        )

    try:
        admin.close_session(session_id)
    except Exception as exc:
        return _doctor_check(
            "browser_smoke_session",
            "fail",
            f"Smoke session was created but could not be closed automatically: {_mask_sensitive_text(str(exc))}",
            stage="close",
            created=True,
            closed=False,
            session_id=session_id,
            error=_doctor_error_name(exc),
            fix=_doctor_fix(
                "close_smoke_session",
                commands=[
                    f"browser-cli session close --session-id {session_id}",
                    "browser-cli doctor --smoke-session",
                ],
                guidance=[
                    "Close the temporary smoke-test session manually, then rerun doctor.",
                ],
            ),
        )

    return _doctor_check(
        "browser_smoke_session",
        "pass",
        "Temporary browser session can be created and closed",
        stage="closed",
        created=True,
        closed=True,
        session_id=session_id,
    )


def _normalize_status(value: Any) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"[\s\-]+", "_", str(value).strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or None


def _context_reuse_state(context: dict[str, Any]) -> dict[str, Any]:
    status = _normalize_status(context.get("status"))
    if status in CONTEXT_REUSABLE_STATUSES:
        return {
            "status": context.get("status"),
            "normalized_status": status,
            "availability": "available",
            "reusable": True,
            "locked": False,
            "reason": "status_reusable",
        }
    if status in CONTEXT_LOCKED_STATUSES:
        return {
            "status": context.get("status"),
            "normalized_status": status,
            "availability": "locked",
            "reusable": False,
            "locked": True,
            "reason": "status_locked",
        }
    if status in CONTEXT_UNAVAILABLE_STATUSES:
        return {
            "status": context.get("status"),
            "normalized_status": status,
            "availability": "unavailable",
            "reusable": False,
            "locked": False,
            "reason": "status_unavailable",
        }
    if status is None:
        return {
            "status": None,
            "normalized_status": None,
            "availability": "unknown",
            "reusable": False,
            "locked": False,
            "reason": "status_missing",
        }
    return {
        "status": context.get("status"),
        "normalized_status": status,
        "availability": "unknown",
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
        "normalized_status": reuse["normalized_status"],
        "availability": reuse["availability"],
        "metadata_match": metadata_match,
        "reusable": reuse["reusable"],
        "locked": reuse["locked"],
        "reason": reuse["reason"] if metadata_match else "metadata_mismatch",
    }


def _context_selection_decision(
    candidates: list[dict[str, Any]],
    *,
    selected_context_id: Any = None,
    created: bool = False,
    create_if_missing: bool = False,
    dry_run: bool = False,
) -> tuple[str, str]:
    if selected_context_id is not None:
        if created:
            return "use_created_context", "created_context_selected"
        return "use_selected_context", "reusable_context_selected"

    metadata_matches = [
        candidate for candidate in candidates if candidate.get("metadata_match") is True
    ]
    locked_matches = [
        candidate for candidate in metadata_matches if candidate.get("locked") is True
    ]
    if dry_run and create_if_missing:
        return "rerun_without_dry_run_to_create", "dry_run_create_if_missing"
    if locked_matches:
        return "wait_or_choose_different_context", "locked_context_matches"
    if candidates and not metadata_matches:
        return "adjust_metadata_filter", "no_metadata_matches"
    if create_if_missing:
        return "create_context", "create_if_missing"
    return "rerun_with_create_if_missing", "no_reusable_context"


def _context_selection_summary(
    candidates: list[dict[str, Any]],
    *,
    selected_context_id: Any = None,
    created: bool = False,
    create_if_missing: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    availability_counts = Counter(
        str(candidate.get("availability", "unknown")) for candidate in candidates
    )
    reason_counts = Counter(
        str(candidate.get("reason", "unknown")) for candidate in candidates
    )
    metadata_matches = [
        candidate for candidate in candidates if candidate.get("metadata_match") is True
    ]
    reusable_matches = [
        candidate for candidate in metadata_matches if candidate.get("reusable") is True
    ]
    locked_matches = [
        candidate for candidate in metadata_matches if candidate.get("locked") is True
    ]
    unavailable_matches = [
        candidate
        for candidate in metadata_matches
        if candidate.get("availability") == "unavailable"
    ]
    unknown_matches = [
        candidate
        for candidate in metadata_matches
        if candidate.get("availability") == "unknown"
    ]
    recommended_next_action, decision_reason = _context_selection_decision(
        candidates,
        selected_context_id=selected_context_id,
        created=created,
        create_if_missing=create_if_missing,
        dry_run=dry_run,
    )
    return {
        "checked": len(candidates),
        "selected_context_id": selected_context_id,
        "recommended_next_action": recommended_next_action,
        "decision_reason": decision_reason,
        "metadata_matches": len(metadata_matches),
        "metadata_mismatches": len(candidates) - len(metadata_matches),
        "reusable_matches": len(reusable_matches),
        "locked_matches": len(locked_matches),
        "unavailable_matches": len(unavailable_matches),
        "unknown_matches": len(unknown_matches),
        "availability_counts": dict(sorted(availability_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "create_if_missing": bool(create_if_missing),
        "dry_run": bool(dry_run),
        "would_create": bool(
            dry_run and selected_context_id is None and create_if_missing
        ),
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
            context_id = context.get("context_id")
            reuse = _context_reuse_state(context)
            return {
                "selected": True,
                "created": False,
                "context_id": context_id,
                "context": context,
                "normalized_status": reuse["normalized_status"],
                "availability": reuse["availability"],
                "reusable": reuse["reusable"],
                "locked": reuse["locked"],
                "reuse_reason": reuse["reason"],
                "reuse": reuse,
                "checked": len(contexts),
                "candidates": candidates,
                "selection_summary": _context_selection_summary(
                    candidates,
                    selected_context_id=context_id,
                    create_if_missing=create_if_missing,
                ),
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
        reuse = _context_reuse_state(created_context)
        return {
            "selected": True,
            "created": True,
            "context_id": created_context.get("context_id"),
            "context": created_context,
            "normalized_status": reuse["normalized_status"],
            "availability": reuse["availability"],
            "reusable": reuse["reusable"],
            "locked": reuse["locked"],
            "reuse_reason": reuse["reason"],
            "reuse": reuse,
            "checked": len(contexts),
            "candidates": candidates,
            "selection_summary": _context_selection_summary(
                candidates,
                selected_context_id=created_context.get("context_id"),
                created=True,
                create_if_missing=create_if_missing,
            ),
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
        selection_summary=_context_selection_summary(
            candidates,
            create_if_missing=create_if_missing,
        ),
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


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _device_token_credentials_path(raw_path: str | None = None) -> tuple[Path, str]:
    if raw_path:
        return Path(raw_path).expanduser(), "argument"
    env_path = os.environ.get(DEVICE_TOKEN_CREDENTIALS_FILE_ENV)
    if env_path:
        return Path(env_path).expanduser(), "env"
    return (
        Path.home() / ".config" / "lexmount" / "browser-cli" / "credentials.json",
        "default",
    )


def _parse_datetime_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _posix_file_mode(path: Path) -> tuple[str | None, bool | None]:
    if os.name != "posix":
        return None, None
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return None, None
    return oct(mode), mode == 0o600


def _local_device_token_status(raw_path: str | None = None) -> dict[str, Any]:
    path, path_source = _device_token_credentials_path(raw_path)
    status: dict[str, Any] = {
        "present": False,
        "path": str(path),
        "path_source": path_source,
        "kind": None,
        "valid": False,
        "expired": None,
        "refresh_needed": None,
        "usable_for_runtime": False,
        "warnings": [],
    }
    if not path.exists():
        return status

    status["present"] = True
    file_mode, file_mode_ok = _posix_file_mode(path)
    if file_mode is not None:
        status["file_mode"] = file_mode
        status["file_mode_ok"] = file_mode_ok
        if file_mode_ok is False:
            status["warnings"].append(
                "Credential file permissions should be 0600 on POSIX systems."
            )

    try:
        raw_data = path.read_text(encoding="utf-8")
    except OSError as exc:
        status.update({"readable": False, "error": "read_error", "message": str(exc)})
        return status

    status["readable"] = True
    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        status.update(
            {
                "error": "invalid_json",
                "message": f"Invalid credentials JSON: {exc}",
            }
        )
        return status
    if not isinstance(data, dict):
        status.update(
            {
                "error": "invalid_credentials",
                "message": "Credentials JSON must contain an object.",
            }
        )
        return status

    kind = data.get("kind")
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_at = data.get("expires_at")
    expires_at_dt = _parse_datetime_utc(expires_at)
    expires_in_seconds: int | None = None
    expired: bool | None = None
    refresh_needed: bool | None = None
    if expires_at_dt is not None:
        expires_in_seconds = int((expires_at_dt - _now_utc()).total_seconds())
        expired = expires_in_seconds <= 0
        refresh_needed = (
            expired or expires_in_seconds <= DEVICE_TOKEN_REFRESH_WINDOW_SECONDS
        )
    scopes = data.get("scopes")
    if not isinstance(scopes, list):
        scopes = []
    scopes = [str(scope) for scope in scopes]

    status.update(
        {
            "kind": kind,
            "valid": (
                kind == "device_token"
                and isinstance(access_token, str)
                and bool(access_token)
                and isinstance(data.get("project_id"), str)
                and bool(data.get("project_id"))
                and expires_at_dt is not None
                and expired is False
            ),
            "expired": expired,
            "refresh_needed": refresh_needed,
            "expires_at": expires_at,
            "expires_in_seconds": expires_in_seconds,
            "project_id": data.get("project_id"),
            "api_base_url": data.get("api_base_url") or DEFAULT_LEXMOUNT_BASE_URL,
            "scopes": scopes,
            "scope_count": len(scopes),
            "token_id": data.get("token_id"),
            "has_access_token": isinstance(access_token, str) and bool(access_token),
            "has_refresh_token": isinstance(refresh_token, str) and bool(refresh_token),
        }
    )
    if kind != "device_token":
        status["warnings"].append("Unsupported credential kind.")
    if not status["has_access_token"]:
        status["warnings"].append("Device token is missing access_token.")
    if not status.get("project_id"):
        status["warnings"].append("Device token is missing project_id.")
    if expires_at_dt is None:
        status["warnings"].append("Device token expires_at is missing or invalid.")
    elif expired:
        status["warnings"].append("Device token is expired.")
    status["usable_for_runtime"] = False
    status["runtime_note"] = (
        "Device-token bearer auth is not enabled in browser-cli runtime yet; "
        "use LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID for browser actions."
    )
    return status


def _auth_source(
    *,
    env_configured: bool,
    device_token_status: dict[str, Any],
) -> str:
    if env_configured:
        return "env"
    if device_token_status.get("present"):
        return "device_token"
    return "missing"


def _device_token_scope_check(
    device_token_status: dict[str, Any],
    required_scopes: list[str] | None,
) -> dict[str, Any]:
    required = _dedupe_preserving_order(required_scopes or [])
    available = [
        str(scope)
        for scope in device_token_status.get("scopes", [])
        if isinstance(scope, str)
    ]
    missing = [scope for scope in required if scope not in available]
    return {
        "required_scopes": required,
        "available_scopes": available,
        "missing_scopes": missing,
        "satisfied": not missing,
    }


def _auth_next_steps(
    *,
    configured: bool,
    device_token_status: dict[str, Any] | None = None,
) -> list[str]:
    if configured:
        return [
            f"Run `{AGENT_DOCTOR_COMMAND}` to verify live API connectivity.",
            "Create a session with `browser-cli session create`.",
        ]
    if device_token_status and device_token_status.get("present"):
        if device_token_status.get("valid"):
            return [
                "Device token metadata is present, but browser actions still require env API-key credentials until bearer-token support lands.",
                "Set LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID in the local shell.",
                f"Run `{AGENT_DOCTOR_COMMAND}` after setting credentials.",
            ]
        return [
            "Local device-token metadata is present but not currently valid.",
            "Run `browser-cli auth login` for browser.lexmount.cn setup guidance.",
            "Set LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID in the local shell.",
        ]
    return [
        "Run `browser-cli auth login` for browser.lexmount.cn setup guidance.",
        "Set LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID in the local shell.",
        f"Run `{AGENT_DOCTOR_COMMAND}` after setting credentials.",
    ]


def _auth_token_info_next_steps(
    *,
    device_token_status: dict[str, Any],
    scope_check: dict[str, Any],
) -> list[str]:
    if not device_token_status.get("present"):
        return [
            "No local device-token metadata was found.",
            "Run `browser-cli auth login` for browser.lexmount.cn setup guidance.",
            "Use LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID for browser actions until bearer-token runtime support lands.",
        ]
    if not device_token_status.get("valid"):
        return [
            "Local device-token metadata is present but not currently valid.",
            "Run `browser-cli auth login` to request fresh credentials when device-code login is available.",
            "Use env API-key credentials for browser actions today.",
        ]
    if not scope_check.get("satisfied"):
        return [
            "Local device-token metadata is valid but missing one or more requested scopes.",
            "Request a scoped credential that includes the missing scopes.",
            "Use env API-key credentials for browser actions today.",
        ]
    return [
        "Local device-token metadata is valid for the requested scope check.",
        "Bearer-token runtime auth is not enabled yet, so browser actions still require env API-key credentials.",
    ]


def _auth_refresh_reason(
    device_token_status: dict[str, Any],
    *,
    force: bool,
) -> str:
    if not device_token_status.get("present"):
        return "missing_credentials_file"
    if device_token_status.get("error"):
        return "invalid_credentials_file"
    if device_token_status.get("kind") != "device_token":
        return "unsupported_credentials_kind"
    if not device_token_status.get("has_refresh_token"):
        return "missing_refresh_token"
    if not force and device_token_status.get("refresh_needed") is False:
        return "refresh_not_needed"
    return "remote_refresh_unavailable"


def _auth_refresh_next_steps(
    *,
    reason: str,
    device_token_status: dict[str, Any],
) -> list[str]:
    if reason == "missing_credentials_file":
        return [
            "No local device-token metadata was found.",
            "Run `browser-cli auth login` for browser.lexmount.cn setup guidance.",
            "Use LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID for browser actions until bearer-token runtime support lands.",
        ]
    if reason in {"invalid_credentials_file", "unsupported_credentials_kind"}:
        return [
            "Local token metadata cannot be refreshed in its current form.",
            "Run `browser-cli auth logout` to remove local metadata if it is stale.",
            "Run `browser-cli auth login` for browser.lexmount.cn setup guidance.",
        ]
    if reason == "missing_refresh_token":
        return [
            "Local device-token metadata does not include a refresh token.",
            "Run `browser-cli auth login` to request fresh credentials when device-code login is available.",
            "Use env API-key credentials for browser actions today.",
        ]
    if reason == "refresh_not_needed":
        return [
            "Local device-token metadata does not currently need refresh.",
            "Bearer-token runtime auth is not enabled yet, so browser actions still require env API-key credentials.",
        ]
    steps = [
        "Remote token refresh is not implemented in browser-cli yet.",
        "Run `browser-cli auth login` to request fresh credentials when browser.lexmount.cn supports device-code login.",
        "Use env API-key credentials for browser actions today.",
    ]
    if device_token_status.get("expired"):
        steps.insert(0, "Local device-token metadata is expired.")
    return steps


def _auth_logout_next_steps(*, deleted: bool, revoke_requested: bool) -> list[str]:
    steps = [
        "Run `browser-cli auth status` to verify local credential state.",
        "Use LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID for browser actions until bearer-token runtime support lands.",
    ]
    if deleted:
        steps.insert(0, "Local device-token metadata was removed.")
    else:
        steps.insert(0, "No local device-token metadata file was removed.")
    if revoke_requested:
        steps.append(
            "Remote revoke is not implemented in browser-cli yet; revoke the token from browser.lexmount.cn if needed."
        )
    return steps


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


def _scope_detail(scope: str) -> dict[str, Any]:
    known_scopes: dict[str, dict[str, Any]] = {
        "browser:sessions": {
            "label": "Browser sessions",
            "description": "Create, list, inspect, keep alive, and close browser sessions.",
            "permissions": [
                "browser.sessions:create",
                "browser.sessions:list",
                "browser.sessions:read",
                "browser.sessions:close",
            ],
            "risk": "medium",
            "destructive": False,
        },
        "browser:contexts": {
            "label": "Persistent browser contexts",
            "description": "Create, list, inspect, reuse, and delete persistent browser contexts.",
            "permissions": [
                "browser.contexts:create",
                "browser.contexts:list",
                "browser.contexts:read",
                "browser.contexts:delete",
            ],
            "risk": "medium",
            "destructive": True,
        },
        "browser:actions": {
            "label": "Browser actions",
            "description": "Open pages and operate browser pages with clicks, typing, snapshots, screenshots, and DOM-backed actions.",
            "permissions": [
                "browser.actions:run",
            ],
            "risk": "high",
            "destructive": False,
        },
    }
    detail = known_scopes.get(scope)
    if detail is None:
        return {
            "scope": scope,
            "known": False,
            "label": scope,
            "description": "Custom or future scope requested by the caller.",
            "permissions": [scope],
            "risk": "unknown",
            "destructive": None,
        }
    return {
        "scope": scope,
        "known": True,
        **detail,
    }


def _scope_details(scopes: list[str]) -> list[dict[str, Any]]:
    return [_scope_detail(scope) for scope in scopes]


def _connect_from_codex_url(
    *,
    project_id: str | None,
    scopes: list[str],
    expires_in: str,
    response: str = "env",
) -> str:
    query: list[tuple[str, str]] = [
        ("source", "browser-cli"),
        ("intent", "agent-browser-control"),
        ("response", response),
        ("expires_in", expires_in),
    ]
    if project_id:
        query.append(("project_id", project_id))
    query.extend(("scope", scope) for scope in scopes)
    return f"{LEXMOUNT_CODEX_CONNECT_URL}?{urlencode(query)}"


def _connect_from_codex_site_capabilities() -> list[dict[str, Any]]:
    return [
        {
            "id": "project_id_display",
            "available": False,
            "required_for": ["manual_env", "scoped_api_key", "device_code"],
            "browser_site_action": (
                "Show the selected Project ID, project name, API host, and region "
                "before issuing agent credentials."
            ),
        },
        {
            "id": "scoped_api_key",
            "available": False,
            "required_for": ["manual_env", "scoped_api_key"],
            "browser_site_action": (
                "Create a local-agent API key with explicit browser session, "
                "context, and action permissions."
            ),
        },
        {
            "id": "copy_install_and_env",
            "available": False,
            "required_for": ["manual_env", "scoped_api_key"],
            "browser_site_action": (
                "Provide copyable uv install, auth export-env, local shell export, "
                "and browser-cli doctor --json commands."
            ),
        },
        {
            "id": "doctor_verification",
            "available": False,
            "required_for": ["manual_env", "support"],
            "browser_site_action": (
                "Explain browser-cli doctor --json success criteria and map "
                "repair_plan commands, env, and guidance to troubleshooting text."
            ),
        },
        {
            "id": "scoped_key_lifecycle",
            "available": False,
            "required_for": ["scoped_api_key", "security"],
            "browser_site_action": (
                "Show permission labels, expiration, status, masked preview, "
                "revoke, and rotation controls for agent keys."
            ),
        },
        {
            "id": "device_code_oauth",
            "available": False,
            "required_for": ["device_code", "scoped_local_token"],
            "browser_site_action": (
                "Expose device-code or OAuth approval endpoints and issue "
                "project-bound, scoped, time-limited local tokens."
            ),
        },
    ]


def _connect_from_codex_site_capability_status(
    capabilities: list[dict[str, Any]],
) -> dict[str, Any]:
    missing = [
        str(capability["id"])
        for capability in capabilities
        if not capability.get("available")
    ]
    return {
        "available": not missing,
        "available_count": len(capabilities) - len(missing),
        "missing_count": len(missing),
        "missing": missing,
    }


def _auth_login_setup_blocks(project_id: str | None) -> list[dict[str, Any]]:
    project_id_value = project_id or "<project-id>"
    return [
        {
            "id": "install",
            "label": "Install browser-cli",
            "commands": [
                "uv tool install git+https://github.com/lexmount/browser-cli.git",
                "browser-cli --help",
                "browser-cli --version",
            ],
            "contains_secret_values": False,
            "contains_secret_placeholders": False,
            "safe_to_paste_in_chat": True,
            "local_shell_only": False,
        },
        {
            "id": "open_connect",
            "label": "Open Connect from Codex",
            "commands": [
                "browser-cli auth login --open",
            ],
            "contains_secret_values": False,
            "contains_secret_placeholders": False,
            "safe_to_paste_in_chat": True,
            "local_shell_only": False,
        },
        {
            "id": "local_env",
            "label": "Configure local shell",
            "commands": [
                "browser-cli auth export-env",
                "export LEXMOUNT_API_KEY='<api-key>'",
                f"export LEXMOUNT_PROJECT_ID={shlex.quote(project_id_value)}",
            ],
            "secret_env": ["LEXMOUNT_API_KEY"],
            "contains_secret_values": False,
            "contains_secret_placeholders": True,
            "safe_to_paste_in_chat": False,
            "local_shell_only": True,
        },
        {
            "id": "verify",
            "label": "Verify local setup",
            "commands": [
                "browser-cli auth status",
                AGENT_DOCTOR_COMMAND,
                "browser-cli doctor --smoke-session",
            ],
            "contains_secret_values": False,
            "contains_secret_placeholders": False,
            "safe_to_paste_in_chat": True,
            "local_shell_only": False,
        },
    ]


def _auth_login_handoff(
    *,
    connect_url: str,
    project_id: str | None,
    project_id_source: str,
    scopes: list[str],
    expires_in: str,
) -> dict[str, Any]:
    return {
        "recommended_flow": "manual_env",
        "login_url": LEXMOUNT_CONSOLE_URL,
        "connect_from_codex_url": connect_url,
        "connect_from_codex_available": False,
        "open_command": "browser-cli auth login --open",
        "open_url": connect_url,
        "install_command": "uv tool install git+https://github.com/lexmount/browser-cli.git",
        "setup_blocks": _auth_login_setup_blocks(project_id),
        "copyable_commands": [
            "browser-cli auth status",
            "browser-cli auth login",
            "browser-cli auth export-env",
            AGENT_DOCTOR_COMMAND,
        ],
        "local_env": [
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
                "value": project_id,
                "value_source": project_id_source,
            },
        ],
        "requested_scopes": scopes,
        "requested_scope_details": _scope_details(scopes),
        "requested_expires_in": expires_in,
        "verification": {
            "status_command": "browser-cli auth status",
            "doctor_command": AGENT_DOCTOR_COMMAND,
            "success_condition": "auth.status configured is true and doctor ok is true",
        },
        "secret_policy": {
            "do_not_paste_in_chat": [
                "LEXMOUNT_API_KEY",
                "full direct connect URLs containing api_key",
                "auth export-env output produced with --reveal-secrets",
            ],
            "safe_to_share": [
                "browser-cli auth status output",
                "browser-cli doctor output with default masking",
                "browser-cli auth export-env output without --reveal-secrets",
            ],
        },
    }


def _doctor_connect_from_codex_fix() -> dict[str, Any]:
    project_id = os.environ.get("LEXMOUNT_PROJECT_ID") or None
    project_id_source = "env" if project_id else "unset"
    scopes = list(DEFAULT_CODEX_CONNECT_SCOPES)
    expires_in = DEFAULT_CODEX_CONNECT_EXPIRES_IN
    connect_url = _connect_from_codex_url(
        project_id=project_id,
        scopes=scopes,
        expires_in=expires_in,
    )
    return {
        "available": False,
        "url": connect_url,
        "open_command": "browser-cli auth login --open",
        "auth_login_command": "browser-cli auth login",
        "project_id": project_id,
        "project_id_source": project_id_source,
        "requested_scopes": scopes,
        "requested_scope_details": _scope_details(scopes),
        "requested_expires_in": expires_in,
        "setup_blocks": _auth_login_setup_blocks(project_id),
        "verification": {
            "status_command": "browser-cli auth status",
            "doctor_command": AGENT_DOCTOR_COMMAND,
            "success_condition": "auth.status configured is true and doctor ok is true",
        },
    }


def _credential_doctor_fix(*env: str) -> dict[str, Any]:
    return _doctor_fix(
        "configure_credentials",
        env=list(env),
        commands=[
            "browser-cli auth login",
            "browser-cli auth export-env",
            "browser-cli auth status",
            AGENT_DOCTOR_COMMAND,
        ],
        guidance=[
            "Get Project ID and API key from https://browser.lexmount.cn.",
            "Set credentials only in the local shell, not in chat.",
            "Run doctor again after exporting credentials.",
        ],
        connect_from_codex=_doctor_connect_from_codex_fix(),
    )


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
        normalized_status=reuse["normalized_status"],
        availability=reuse["availability"],
        reusable=reuse["reusable"],
        locked=reuse["locked"],
        reuse_reason=reuse["reason"],
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
    selected_context_id = (
        selected_context.get("context_id") if selected_context is not None else None
    )
    selection_summary = _context_selection_summary(
        candidates,
        selected_context_id=selected_context_id,
        create_if_missing=args.create_if_missing,
        dry_run=args.dry_run,
    )

    if selected_context is not None:
        reuse = _context_reuse_state(selected_context)
        _success(
            command,
            selected=True,
            created=False,
            dry_run=args.dry_run,
            context_id=selected_context.get("context_id"),
            context=selected_context,
            normalized_status=reuse["normalized_status"],
            availability=reuse["availability"],
            reusable=reuse["reusable"],
            locked=reuse["locked"],
            reuse_reason=reuse["reason"],
            reuse=reuse,
            checked=len(contexts),
            candidates=candidates,
            selection_summary=selection_summary,
            metadata_filter=metadata_filter,
        )

    if args.dry_run:
        _success(
            command,
            selected=False,
            created=False,
            dry_run=True,
            would_create=selection_summary["would_create"],
            context_id=None,
            context=None,
            reuse=None,
            checked=len(contexts),
            candidates=candidates,
            selection_summary=selection_summary,
            metadata_filter=metadata_filter,
            message=(
                "No reusable context matched. A non-dry-run context pick with "
                "--create-if-missing would create a context."
                if selection_summary["would_create"]
                else "No reusable context matched the requested filters."
            ),
        )

    if args.create_if_missing:
        try:
            context = admin.create_context(metadata=metadata_filter or None)
        except Exception as exc:
            _failure_from_exception(command, exc)
        created_context = _model_payload(context)
        created_context_id = created_context.get("context_id")
        reuse = _context_reuse_state(created_context)
        _success(
            command,
            selected=True,
            created=True,
            dry_run=False,
            context_id=created_context_id,
            context=created_context,
            normalized_status=reuse["normalized_status"],
            availability=reuse["availability"],
            reusable=reuse["reusable"],
            locked=reuse["locked"],
            reuse_reason=reuse["reason"],
            reuse=reuse,
            checked=len(contexts),
            candidates=candidates,
            selection_summary=_context_selection_summary(
                candidates,
                selected_context_id=created_context_id,
                created=True,
                create_if_missing=args.create_if_missing,
            ),
            metadata_filter=metadata_filter,
        )

    _failure(
        command,
        "no_available_context",
        "No reusable context matched the requested filters.",
        selected=False,
        created=False,
        dry_run=False,
        checked=len(contexts),
        candidates=candidates,
        selection_summary=selection_summary,
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


def _read_file_input_payloads(
    *,
    command: str,
    files: list[str],
    max_bytes: int,
) -> list[dict[str, Any]]:
    if max_bytes < 0:
        _failure(
            command,
            "argument_error",
            "--max-bytes must be zero or greater.",
            exit_code=2,
            max_bytes=max_bytes,
        )

    payloads: list[dict[str, Any]] = []
    total_bytes = 0
    for raw_file in files:
        path = Path(raw_file).expanduser()
        if not path.is_file():
            _failure(
                command,
                "file_not_found",
                "File input path does not exist or is not a file.",
                exit_code=2,
                file=raw_file,
            )
        try:
            data = path.read_bytes()
            stat = path.stat()
        except OSError as exc:
            _failure(
                command,
                "file_read_error",
                str(exc),
                exit_code=2,
                file=raw_file,
            )
        total_bytes += len(data)
        if total_bytes > max_bytes:
            _failure(
                command,
                "file_payload_too_large",
                "Total file input payload exceeds --max-bytes.",
                exit_code=2,
                max_bytes=max_bytes,
                total_bytes=total_bytes,
            )
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        payloads.append(
            {
                "name": path.name,
                "type": mime_type,
                "size": len(data),
                "last_modified": int(stat.st_mtime * 1000),
                "data_base64": base64.b64encode(data).decode("ascii"),
            }
        )
    return payloads


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


def _page_info_expression() -> str:
    return """
() => {
  const bodyText = document.body ? (document.body.innerText || document.body.textContent || "") : "";
  const html = document.documentElement ? document.documentElement.outerHTML || "" : "";
  return {
    url: location.href,
    title: document.title,
    ready_state: document.readyState,
    visibility_state: document.visibilityState,
    language: document.documentElement ? document.documentElement.lang || null : null,
    referrer: document.referrer || null,
    body_text_length: bodyText.length,
    html_length: html.length,
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      device_pixel_ratio: window.devicePixelRatio
    },
    scroll: {
      x: window.scrollX,
      y: window.scrollY
    }
  };
}
""".strip()


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


def _sensitive_value_helpers_expression() -> str:
    return """
  const sensitiveNamePattern =
    /api[-_]?key|apikey|authorization|bearer|credential|password|passwd|secret|token/i;
  const sensitiveText = (value) => sensitiveNamePattern.test(String(value ?? ""));
  const sensitiveElement = (element) => {
    const tag = element.tagName.toLowerCase();
    const type = String(element.getAttribute("type") || "").toLowerCase();
    if (tag === "input" && ["password", "hidden", "file"].includes(type)) {
      return true;
    }
    return [
      "name",
      "id",
      "autocomplete",
      "aria-label",
      "placeholder",
      "title",
      "data-testid",
      "data-test",
      "data-cy"
    ].some((attribute) => sensitiveNamePattern.test(element.getAttribute(attribute) || ""));
  };
  const shouldMaskValue = (element, value) =>
    sensitiveElement(element) && String(value ?? "") !== "";
  const maskValue = (element, value) => shouldMaskValue(element, value) ? "***" : value;
  const publicRequestedValue = (element, value) => {
    const masked = shouldMaskValue(element, value);
    return {
      value: masked ? "***" : value,
      value_masked: masked,
      value_length: masked ? String(value ?? "").length : null
    };
  };
  const publicSelectorRequestedValue = (selector, value) => {
    const masked = sensitiveText(selector) && String(value ?? "") !== "";
    return {
      value: masked ? "***" : value,
      value_masked: masked,
      value_length: masked ? String(value ?? "").length : null
    };
  };
""".rstrip()


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
  const sensitiveNamePattern =
    /api[-_]?key|apikey|authorization|bearer|credential|password|passwd|secret|token/i;
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
  const sensitiveText = (value) => sensitiveNamePattern.test(String(value ?? ""));
  const sensitiveElement = (element) => {{
    const tag = element.tagName.toLowerCase();
    const type = String(element.getAttribute("type") || "").toLowerCase();
    if (tag === "input" && ["password", "hidden", "file"].includes(type)) {{
      return true;
    }}
    return [
      "name",
      "id",
      "autocomplete",
      "aria-label",
      "placeholder",
      "title",
      "data-testid",
      "data-test",
      "data-cy"
    ].some((attribute) => sensitiveNamePattern.test(element.getAttribute(attribute) || ""));
  }};
  const shouldMaskValue = (element, value) =>
    sensitiveElement(element) && String(value ?? "") !== "";
  const maskValue = (element, value) => shouldMaskValue(element, value) ? "***" : value;
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
  const rawTextOf = (element) => normalize(element.innerText ?? element.textContent ?? "");
  const textOf = (element) => maskValue(element, rawTextOf(element));
  const valueNameOf = (element) => {{
    if (!("value" in element)) return "";
    return normalize(maskValue(element, element.value));
  }};
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
    valueNameOf(element) ||
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
  const labelSuggestsSensitive = sensitiveText(requestedLabel);
  const labelSafeText = labelSuggestsSensitive && text !== "" ? "***" : text;
  const exact = {_js_literal(exact)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const fieldSelector = "input:not([type=hidden]), textarea, select, [contenteditable='true']";
  const match = findFieldByLabel(requestedLabel, exact, caseSensitive, fieldSelector);
  const element = match.element;
  if (!element) {{
    return {{
      found: false,
      filled: false,
      label: requestedLabel,
      text: labelSafeText,
      text_masked: labelSafeText !== text,
      text_length: labelSafeText !== text ? String(text ?? "").length : null
    }};
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
    text: maskValue(element, text),
    text_masked: shouldMaskValue(element, text),
    text_length: shouldMaskValue(element, text) ? String(text ?? "").length : null,
    previous_value: maskValue(element, previousValue),
    previous_value_masked: shouldMaskValue(element, previousValue),
    value: maskValue(
      element,
      element.isContentEditable ? element.textContent : element.value
    ),
    value_masked: shouldMaskValue(
      element,
      element.isContentEditable ? element.textContent : element.value
    ),
    value_length: shouldMaskValue(
      element,
      element.isContentEditable ? element.textContent : element.value
    )
      ? String((element.isContentEditable ? element.textContent : element.value) ?? "").length
      : null,
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
        value: sensitiveElement(field) && option.value !== "" ? "***" : option.value,
        value_masked: sensitiveElement(field) && option.value !== "",
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
    const sensitive = sensitiveElement(field);
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


def _link_snapshot_expression(
    *,
    selector: str | None,
    include_hidden: bool,
    max_nodes: int,
    include_empty: bool,
    same_origin_only: bool,
) -> str:
    selector_source = "null" if selector is None else _js_literal(selector)
    return f"""
() => {{
{_dom_helpers_expression(include_hidden=include_hidden, max_nodes=max_nodes)}
  const rootSelector = {selector_source};
  const includeEmpty = {_js_literal(include_empty)};
  const sameOriginOnly = {_js_literal(same_origin_only)};
  const linkSelector = "a[href], area[href]";
  const sensitiveUrlParamName =
    /^(api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)$/i;
  const sensitiveUrlParamPattern =
    /([?&](?:api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)=)[^&#]*/gi;
  const maskUrlText = (value) => String(value ?? "").replace(
    sensitiveUrlParamPattern,
    "$1***"
  );
  const maskedParsedUrl = (value) => {{
    const raw = String(value ?? "");
    if (!raw) {{
      return {{
        absolute_url: raw,
        absolute_url_masked: false,
        origin: null,
        pathname: null,
        search: null,
        hash: null,
        same_origin: null,
        url_parse_error: null
      }};
    }}
    try {{
      const parsed = new URL(raw, location.href);
      let masked = false;
      if (parsed.username) {{
        parsed.username = "***";
        masked = true;
      }}
      if (parsed.password) {{
        parsed.password = "***";
        masked = true;
      }}
      for (const key of [...parsed.searchParams.keys()]) {{
        if (sensitiveUrlParamName.test(key)) {{
          parsed.searchParams.set(key, "***");
          masked = true;
        }}
      }}
      return {{
        absolute_url: parsed.href,
        absolute_url_masked: masked,
        origin: parsed.origin,
        pathname: parsed.pathname,
        search: parsed.search || null,
        hash: parsed.hash || null,
        same_origin: parsed.origin === location.origin,
        url_parse_error: null
      }};
    }} catch (error) {{
      const maskedRaw = maskUrlText(raw);
      return {{
        absolute_url: maskedRaw,
        absolute_url_masked: maskedRaw !== raw,
        origin: null,
        pathname: null,
        search: null,
        hash: null,
        same_origin: null,
        url_parse_error: String(error.message || error)
      }};
    }}
  }};
  const roots = rootSelector === null
    ? [document.body || document.documentElement].filter(Boolean)
    : [...document.querySelectorAll(rootSelector)];
  const seen = new Set();
  const allLinks = [];
  for (const root of roots) {{
    const candidates = [
      ...(root.matches?.(linkSelector) ? [root] : []),
      ...root.querySelectorAll(linkSelector)
    ];
    for (const candidate of candidates) {{
      if (!seen.has(candidate)) {{
        seen.add(candidate);
        allLinks.push(candidate);
      }}
    }}
  }}
  const visibleLinks = allLinks.filter(visible);
  const candidateLinks = includeHidden ? allLinks : visibleLinks;
  const linkInfo = (element) => {{
    const rawHref = element.getAttribute("href") || "";
    const maskedHref = maskUrlText(rawHref);
    const parsed = maskedParsedUrl(rawHref);
    const info = nodeInfo(element);
    return {{
      ...info,
      href: maskedHref,
      href_masked: maskedHref !== rawHref,
      ...parsed,
      external: parsed.same_origin === null ? null : !parsed.same_origin,
      target: element.getAttribute("target"),
      rel: element.getAttribute("rel"),
      download: element.hasAttribute("download")
        ? element.getAttribute("download") || true
        : null,
      hreflang: element.getAttribute("hreflang"),
      type: element.getAttribute("type")
    }};
  }};
  const usefulLinks = candidateLinks
    .map(linkInfo)
    .filter((link) => includeEmpty || link.name || link.text);
  const filteredLinks = sameOriginOnly
    ? usefulLinks.filter((link) => link.same_origin === true)
    : usefulLinks;
  const nodes = limited(filteredLinks);
  return {{
    url: location.href,
    title: document.title,
    kind: "links",
    selector: rootSelector,
    include_hidden: includeHidden,
    include_empty: includeEmpty,
    same_origin_only: sameOriginOnly,
    link_count: filteredLinks.length,
    node_count: nodes.length,
    total_count: allLinks.length,
    visible_count: visibleLinks.length,
    truncated: maxNodes !== null && filteredLinks.length > nodes.length,
    links: nodes
  }};
}}
""".strip()


def _table_snapshot_expression(
    *,
    selector: str | None,
    include_hidden: bool,
    max_nodes: int,
    max_rows: int,
    max_cells: int,
) -> str:
    selector_source = "null" if selector is None else _js_literal(selector)
    return f"""
() => {{
{_dom_helpers_expression(include_hidden=include_hidden, max_nodes=max_nodes)}
  const rootSelector = {selector_source};
  const maxRows = Math.max(0, {_js_literal(max_rows)});
  const maxCells = Math.max(0, {_js_literal(max_cells)});
  const tableSelector = "table,[role~='table'],[role~='grid']";
  const rowSelector = "tr,[role~='row']";
  const cellSelector = [
    "th",
    "td",
    "[role~='cell']",
    "[role~='gridcell']",
    "[role~='columnheader']",
    "[role~='rowheader']"
  ].join(",");
  const linkSelector = "a[href], area[href]";
  const sensitiveUrlParamName =
    /^(api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)$/i;
  const sensitiveUrlParamPattern =
    /([?&](?:api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)=)[^&#]*/gi;
  const maskUrlText = (value) => String(value ?? "").replace(
    sensitiveUrlParamPattern,
    "$1***"
  );
  const maskedParsedUrl = (value) => {{
    const raw = String(value ?? "");
    if (!raw) {{
      return {{ absolute_url: raw, absolute_url_masked: false }};
    }}
    try {{
      const parsed = new URL(raw, location.href);
      let masked = false;
      if (parsed.username) {{
        parsed.username = "***";
        masked = true;
      }}
      if (parsed.password) {{
        parsed.password = "***";
        masked = true;
      }}
      for (const key of [...parsed.searchParams.keys()]) {{
        if (sensitiveUrlParamName.test(key)) {{
          parsed.searchParams.set(key, "***");
          masked = true;
        }}
      }}
      return {{ absolute_url: parsed.href, absolute_url_masked: masked }};
    }} catch (error) {{
      const maskedRaw = maskUrlText(raw);
      return {{
        absolute_url: maskedRaw,
        absolute_url_masked: maskedRaw !== raw
      }};
    }}
  }};
  const roots = rootSelector === null
    ? [document.body || document.documentElement].filter(Boolean)
    : [...document.querySelectorAll(rootSelector)];
  const seen = new Set();
  const allTables = [];
  for (const root of roots) {{
    const candidates = [
      ...(root.matches?.(tableSelector) ? [root] : []),
      ...root.querySelectorAll(tableSelector)
    ];
    for (const candidate of candidates) {{
      if (!seen.has(candidate)) {{
        seen.add(candidate);
        allTables.push(candidate);
      }}
    }}
  }}
  const linkInfo = (link) => {{
    const rawHref = link.getAttribute("href") || "";
    const maskedHref = maskUrlText(rawHref);
    return {{
      text: textOf(link),
      href: maskedHref,
      href_masked: maskedHref !== rawHref,
      ...maskedParsedUrl(rawHref)
    }};
  }};
  const numericAttribute = (element, name, fallback) => {{
    const value = Number(element.getAttribute(name));
    return Number.isFinite(value) && value > 0 ? value : fallback;
  }};
  const cellInfo = (cell, cellIndex) => {{
    const tag = cell.tagName.toLowerCase();
    const role = roleOf(cell);
    const links = [...cell.querySelectorAll(linkSelector)].map(linkInfo);
    return {{
      column_index: cellIndex,
      selector: nodeInfo(cell).selector,
      tag,
      role: role || null,
      header: tag === "th" || role === "columnheader" || role === "rowheader",
      scope: cell.getAttribute("scope"),
      text: textOf(cell),
      colspan: numericAttribute(cell, "colspan", numericAttribute(cell, "aria-colspan", 1)),
      rowspan: numericAttribute(cell, "rowspan", numericAttribute(cell, "aria-rowspan", 1)),
      links
    }};
  }};
  const rowInfo = (row, rowIndex) => {{
    const rawCells = "cells" in row ? [...row.cells] : [...row.querySelectorAll(cellSelector)];
    const visibleCells = rawCells.filter(visible);
    const candidateCells = includeHidden ? rawCells : visibleCells;
    const cells = candidateCells.slice(0, maxCells).map(cellInfo);
    return {{
      row_index: rowIndex,
      selector: nodeInfo(row).selector,
      cell_count: candidateCells.length,
      visible_cell_count: visibleCells.length,
      node_count: cells.length,
      truncated: candidateCells.length > cells.length,
      cells
    }};
  }};
  const tableInfo = (table, tableIndex) => {{
    const nativeTable = table.tagName.toLowerCase() === "table";
    const rawRows = nativeTable ? [...table.rows] : [...table.querySelectorAll(rowSelector)];
    const visibleRows = rawRows.filter(visible);
    const candidateRows = includeHidden ? rawRows : visibleRows;
    const rows = candidateRows.slice(0, maxRows).map(rowInfo);
    const captionElement = nativeTable
      ? table.caption
      : table.querySelector("caption,[role~='caption']");
    const headerRow = rows.find((row) => row.cells.some((cell) => cell.header));
    return {{
      table_index: tableIndex,
      ...nodeInfo(table),
      caption: captionElement ? textOf(captionElement) : null,
      headers: headerRow ? headerRow.cells.map((cell) => cell.text) : [],
      row_count: candidateRows.length,
      visible_row_count: visibleRows.length,
      node_count: rows.length,
      truncated: candidateRows.length > rows.length,
      rows
    }};
  }};
  const visibleTables = allTables.filter(visible);
  const candidateTables = includeHidden ? allTables : visibleTables;
  const tables = limited(candidateTables).map(tableInfo);
  return {{
    url: location.href,
    title: document.title,
    kind: "tables",
    selector: rootSelector,
    include_hidden: includeHidden,
    max_rows: maxRows,
    max_cells: maxCells,
    table_count: candidateTables.length,
    node_count: tables.length,
    total_count: allTables.length,
    visible_count: visibleTables.length,
    truncated: maxNodes !== null && candidateTables.length > tables.length,
    tables
  }};
}}
""".strip()


def _list_snapshot_expression(
    *,
    selector: str | None,
    include_hidden: bool,
    max_nodes: int,
    max_items: int,
) -> str:
    selector_source = "null" if selector is None else _js_literal(selector)
    return f"""
() => {{
{_dom_helpers_expression(include_hidden=include_hidden, max_nodes=max_nodes)}
  const rootSelector = {selector_source};
  const maxItems = Math.max(0, {_js_literal(max_items)});
  const listSelector = [
    "ul",
    "ol",
    "menu",
    "[role~='list']",
    "[role~='listbox']",
    "[role~='menu']",
    "[role~='tree']"
  ].join(",");
  const itemSelector = [
    "li",
    "[role~='listitem']",
    "[role~='option']",
    "[role~='menuitem']",
    "[role~='menuitemcheckbox']",
    "[role~='menuitemradio']",
    "[role~='treeitem']"
  ].join(",");
  const linkSelector = "a[href], area[href]";
  const sensitiveUrlParamName =
    /^(api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)$/i;
  const sensitiveUrlParamPattern =
    /([?&](?:api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)=)[^&#]*/gi;
  const maskUrlText = (value) => String(value ?? "").replace(
    sensitiveUrlParamPattern,
    "$1***"
  );
  const maskedParsedUrl = (value) => {{
    const raw = String(value ?? "");
    if (!raw) {{
      return {{ absolute_url: raw, absolute_url_masked: false }};
    }}
    try {{
      const parsed = new URL(raw, location.href);
      let masked = false;
      if (parsed.username) {{
        parsed.username = "***";
        masked = true;
      }}
      if (parsed.password) {{
        parsed.password = "***";
        masked = true;
      }}
      for (const key of [...parsed.searchParams.keys()]) {{
        if (sensitiveUrlParamName.test(key)) {{
          parsed.searchParams.set(key, "***");
          masked = true;
        }}
      }}
      return {{
        absolute_url: parsed.href,
        absolute_url_masked: masked,
        same_origin: parsed.origin === location.origin
      }};
    }} catch (error) {{
      const maskedRaw = maskUrlText(raw);
      return {{
        absolute_url: maskedRaw,
        absolute_url_masked: maskedRaw !== raw,
        same_origin: null
      }};
    }}
  }};
  const roots = rootSelector === null
    ? [document.body || document.documentElement].filter(Boolean)
    : [...document.querySelectorAll(rootSelector)];
  const seen = new Set();
  const allLists = [];
  for (const root of roots) {{
    const candidates = [
      ...(root.matches?.(listSelector) ? [root] : []),
      ...root.querySelectorAll(listSelector)
    ];
    for (const candidate of candidates) {{
      if (!seen.has(candidate)) {{
        seen.add(candidate);
        allLists.push(candidate);
      }}
    }}
  }}
  const directItemsOf = (list) => {{
    const items = [...list.querySelectorAll(itemSelector)];
    const direct = items.filter((item) => item.closest(listSelector) === list);
    return direct.length ? direct : items;
  }};
  const selectedState = (item) => {{
    const ariaSelected = item.getAttribute("aria-selected");
    if (ariaSelected === "true") return true;
    if (ariaSelected === "false") return false;
    return "selected" in item ? Boolean(item.selected) : null;
  }};
  const checkedState = (item) => {{
    const ariaChecked = item.getAttribute("aria-checked");
    if (ariaChecked === "true") return true;
    if (ariaChecked === "false") return false;
    if (ariaChecked === "mixed") return "mixed";
    const input = item.matches?.("input[type=checkbox],input[type=radio]")
      ? item
      : item.querySelector("input[type=checkbox],input[type=radio]");
    return input ? Boolean(input.checked) : null;
  }};
  const expandedState = (item) => {{
    const ariaExpanded = item.getAttribute("aria-expanded");
    if (ariaExpanded === "true") return true;
    if (ariaExpanded === "false") return false;
    return null;
  }};
  const disabledState = (item) =>
    Boolean(item.disabled) || item.getAttribute("aria-disabled") === "true";
  const itemLevel = (item, list) => {{
    let level = 1;
    let current = item.parentElement;
    while (current && current !== list) {{
      if (current.matches?.(listSelector)) level += 1;
      current = current.parentElement;
    }}
    return level;
  }};
  const linkInfo = (link) => {{
    const rawHref = link.getAttribute("href") || "";
    const maskedHref = maskUrlText(rawHref);
    return {{
      text: textOf(link),
      href: maskedHref,
      href_masked: maskedHref !== rawHref,
      ...maskedParsedUrl(rawHref)
    }};
  }};
  const linksOf = (item) => {{
    const links = [
      ...(item.matches?.(linkSelector) ? [item] : []),
      ...item.querySelectorAll(linkSelector)
    ];
    return links.map(linkInfo);
  }};
  const itemInfo = (item, itemIndex, list) => {{
    const info = nodeInfo(item);
    return {{
      item_index: itemIndex,
      ...info,
      level: itemLevel(item, list),
      selected: selectedState(item),
      checked: checkedState(item),
      expanded: expandedState(item),
      disabled: disabledState(item),
      links: linksOf(item)
    }};
  }};
  const listInfo = (list, listIndex) => {{
    const tag = list.tagName.toLowerCase();
    const rawItems = directItemsOf(list);
    const visibleItems = rawItems.filter(visible);
    const candidateItems = includeHidden ? rawItems : visibleItems;
    const items = candidateItems
      .slice(0, maxItems)
      .map((item, itemIndex) => itemInfo(item, itemIndex, list));
    return {{
      list_index: listIndex,
      ...nodeInfo(list),
      ordered: tag === "ol",
      start: tag === "ol" ? Number(list.getAttribute("start") || 1) : null,
      reversed: tag === "ol" ? Boolean(list.reversed) : null,
      item_count: candidateItems.length,
      visible_item_count: visibleItems.length,
      node_count: items.length,
      truncated: candidateItems.length > items.length,
      items
    }};
  }};
  const visibleLists = allLists.filter(visible);
  const candidateLists = includeHidden ? allLists : visibleLists;
  const lists = limited(candidateLists).map(listInfo);
  return {{
    url: location.href,
    title: document.title,
    kind: "lists",
    selector: rootSelector,
    include_hidden: includeHidden,
    max_items: maxItems,
    list_count: candidateLists.length,
    node_count: lists.length,
    total_count: allLists.length,
    visible_count: visibleLists.length,
    truncated: maxNodes !== null && candidateLists.length > lists.length,
    lists
  }};
}}
""".strip()


def _text_snapshot_expression(
    *,
    selector: str | None,
    include_hidden: bool,
    max_nodes: int,
    max_chars: int,
) -> str:
    selector_source = "null" if selector is None else _js_literal(selector)
    return f"""
() => {{
{_dom_helpers_expression(include_hidden=include_hidden, max_nodes=max_nodes)}
  const rootSelector = {selector_source};
  const maxChars = Math.max(0, {_js_literal(max_chars)});
  const textSelector = [
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "blockquote",
    "pre",
    "code",
    "label",
    "legend",
    "summary",
    "figcaption",
    "caption",
    "dt",
    "dd",
    "[role~='heading']",
    "[role~='alert']",
    "[role~='status']",
    "[role~='log']",
    "[role~='note']",
    "[aria-live]"
  ].join(",");
  const roots = rootSelector === null
    ? [document.body || document.documentElement].filter(Boolean)
    : [...document.querySelectorAll(rootSelector)];
  const seen = new Set();
  const allTextBlocks = [];
  for (const root of roots) {{
    const candidates = [
      ...(root.matches?.(textSelector) ? [root] : []),
      ...root.querySelectorAll(textSelector)
    ];
    for (const candidate of candidates) {{
      if (!seen.has(candidate)) {{
        seen.add(candidate);
        allTextBlocks.push(candidate);
      }}
    }}
  }}
  const headingLevel = (element) => {{
    const tag = element.tagName.toLowerCase();
    if (/^h[1-6]$/.test(tag)) return Number(tag.slice(1));
    const value = Number(element.getAttribute("aria-level"));
    return Number.isFinite(value) && value > 0 ? value : null;
  }};
  const textKind = (element) => {{
    const role = roleOf(element);
    if (role === "heading" || headingLevel(element) !== null) return "heading";
    if (["alert", "status", "log"].includes(role)) return "live-region";
    if (element.hasAttribute("aria-live")) return "live-region";
    const tag = element.tagName.toLowerCase();
    if (["label", "legend", "caption", "figcaption"].includes(tag)) return "label";
    if (["pre", "code"].includes(tag)) return "code";
    if (["dt", "dd"].includes(tag)) return "definition";
    if (tag === "blockquote") return "quote";
    return "text";
  }};
  const truncateText = (value) => {{
    const text = String(value ?? "");
    if (text.length <= maxChars) {{
      return {{ text, text_truncated: false }};
    }}
    return {{
      text: text.slice(0, maxChars),
      text_truncated: true
    }};
  }};
  const textInfo = (element, index) => {{
    const info = nodeInfo(element);
    const rawText = info.text;
    return {{
      index,
      selector: info.selector,
      tag: info.tag,
      role: info.role,
      name: info.name,
      kind: textKind(element),
      level: headingLevel(element),
      aria_live: element.getAttribute("aria-live"),
      aria_atomic: element.getAttribute("aria-atomic"),
      text_length: rawText.length,
      ...truncateText(rawText),
      visible: info.visible
    }};
  }};
  const visibleTextBlocks = allTextBlocks.filter(visible);
  const candidateTextBlocks = includeHidden ? allTextBlocks : visibleTextBlocks;
  const nonEmptyTextBlocks = candidateTextBlocks.filter((element) => textOf(element));
  const texts = limited(nonEmptyTextBlocks).map(textInfo);
  return {{
    url: location.href,
    title: document.title,
    kind: "text",
    selector: rootSelector,
    include_hidden: includeHidden,
    max_chars: maxChars,
    text_count: nonEmptyTextBlocks.length,
    node_count: texts.length,
    total_count: allTextBlocks.length,
    visible_count: visibleTextBlocks.length,
    truncated: maxNodes !== null && nonEmptyTextBlocks.length > texts.length,
    texts
  }};
}}
""".strip()


def _dialog_snapshot_expression(
    *,
    selector: str | None,
    include_hidden: bool,
    max_nodes: int,
    max_controls: int,
    max_chars: int,
) -> str:
    selector_source = "null" if selector is None else _js_literal(selector)
    return f"""
() => {{
{_dom_helpers_expression(include_hidden=include_hidden, max_nodes=max_nodes)}
  const rootSelector = {selector_source};
  const maxControls = Math.max(0, {_js_literal(max_controls)});
  const maxChars = Math.max(0, {_js_literal(max_chars)});
  const dialogSelector = [
    "dialog",
    "[role~='dialog']",
    "[role~='alertdialog']",
    "[aria-modal='true']",
    ".modal",
    "[data-modal='true']"
  ].join(",");
  const linkSelector = "a[href], area[href]";
  const sensitiveUrlParamName =
    /^(api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)$/i;
  const sensitiveUrlParamPattern =
    /([?&](?:api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)=)[^&#]*/gi;
  const maskUrlText = (value) => String(value ?? "").replace(
    sensitiveUrlParamPattern,
    "$1***"
  );
  const maskedParsedUrl = (value) => {{
    const raw = String(value ?? "");
    if (!raw) {{
      return {{ absolute_url: raw, absolute_url_masked: false }};
    }}
    try {{
      const parsed = new URL(raw, location.href);
      let masked = false;
      if (parsed.username) {{
        parsed.username = "***";
        masked = true;
      }}
      if (parsed.password) {{
        parsed.password = "***";
        masked = true;
      }}
      for (const key of [...parsed.searchParams.keys()]) {{
        if (sensitiveUrlParamName.test(key)) {{
          parsed.searchParams.set(key, "***");
          masked = true;
        }}
      }}
      return {{
        absolute_url: parsed.href,
        absolute_url_masked: masked,
        same_origin: parsed.origin === location.origin
      }};
    }} catch (error) {{
      const maskedRaw = maskUrlText(raw);
      return {{
        absolute_url: maskedRaw,
        absolute_url_masked: maskedRaw !== raw,
        same_origin: null
      }};
    }}
  }};
  const roots = rootSelector === null
    ? [document.body || document.documentElement].filter(Boolean)
    : [...document.querySelectorAll(rootSelector)];
  const seen = new Set();
  const allDialogs = [];
  for (const root of roots) {{
    const candidates = [
      ...(root.matches?.(dialogSelector) ? [root] : []),
      ...root.querySelectorAll(dialogSelector)
    ];
    for (const candidate of candidates) {{
      if (!seen.has(candidate)) {{
        seen.add(candidate);
        allDialogs.push(candidate);
      }}
    }}
  }}
  const idsText = (value) => normalize(
    String(value || "")
      .split(/\\s+/)
      .map((id) => document.getElementById(id)?.innerText ?? "")
      .join(" ")
  );
  const firstText = (dialog, selector) => {{
    const element = dialog.querySelector(selector);
    return element ? textOf(element) : "";
  }};
  const truncateText = (value) => {{
    const text = String(value ?? "");
    if (text.length <= maxChars) {{
      return {{ text, text_truncated: false }};
    }}
    return {{
      text: text.slice(0, maxChars),
      text_truncated: true
    }};
  }};
  const disabledState = (element) =>
    Boolean(element.disabled) || element.getAttribute("aria-disabled") === "true";
  const checkedState = (element) => {{
    const ariaChecked = element.getAttribute("aria-checked");
    if (ariaChecked === "true") return true;
    if (ariaChecked === "false") return false;
    if (ariaChecked === "mixed") return "mixed";
    return "checked" in element ? Boolean(element.checked) : null;
  }};
  const selectedState = (element) => {{
    const ariaSelected = element.getAttribute("aria-selected");
    if (ariaSelected === "true") return true;
    if (ariaSelected === "false") return false;
    return "selected" in element ? Boolean(element.selected) : null;
  }};
  const linkPayload = (element) => {{
    if (!element.matches?.(linkSelector)) {{
      return {{}};
    }}
    const rawHref = element.getAttribute("href") || "";
    const maskedHref = maskUrlText(rawHref);
    return {{
      href: maskedHref,
      href_masked: maskedHref !== rawHref,
      ...maskedParsedUrl(rawHref)
    }};
  }};
  const controlInfo = (element, controlIndex) => {{
    const info = nodeInfo(element);
    return {{
      control_index: controlIndex,
      selector: info.selector,
      tag: info.tag,
      role: info.role,
      name: info.name,
      text: info.text,
      type: element.getAttribute("type"),
      disabled: disabledState(element),
      checked: checkedState(element),
      selected: selectedState(element),
      visible: info.visible,
      ...linkPayload(element)
    }};
  }};
  const dialogTitle = (dialog) =>
    idsText(dialog.getAttribute("aria-labelledby")) ||
    firstText(dialog, "h1,h2,h3,h4,h5,h6,[role~='heading'],header");
  const dialogDescription = (dialog) =>
    idsText(dialog.getAttribute("aria-describedby")) ||
    firstText(dialog, "p,[role~='document'],section,article");
  const dialogInfo = (dialog, dialogIndex) => {{
    const info = nodeInfo(dialog);
    const rawControls = [...dialog.querySelectorAll(interactiveSelector)];
    const visibleControls = rawControls.filter(visible);
    const candidateControls = includeHidden ? rawControls : visibleControls;
    const controls = candidateControls
      .slice(0, maxControls)
      .map(controlInfo);
    const truncatedText = truncateText(info.text);
    return {{
      dialog_index: dialogIndex,
      selector: info.selector,
      tag: info.tag,
      role: info.role,
      name: info.name,
      title: dialogTitle(dialog),
      description: dialogDescription(dialog),
      modal: dialog.getAttribute("aria-modal") === "true" ||
        (dialog.tagName.toLowerCase() === "dialog" && Boolean(dialog.open)),
      open: dialog.tagName.toLowerCase() === "dialog"
        ? Boolean(dialog.open)
        : null,
      text_length: info.text.length,
      ...truncatedText,
      visible: info.visible,
      control_count: candidateControls.length,
      visible_control_count: visibleControls.length,
      node_count: controls.length,
      controls_truncated: candidateControls.length > controls.length,
      controls
    }};
  }};
  const visibleDialogs = allDialogs.filter(visible);
  const candidateDialogs = includeHidden ? allDialogs : visibleDialogs;
  const dialogs = limited(candidateDialogs).map(dialogInfo);
  return {{
    url: location.href,
    title: document.title,
    kind: "dialogs",
    selector: rootSelector,
    include_hidden: includeHidden,
    max_controls: maxControls,
    max_chars: maxChars,
    dialog_count: candidateDialogs.length,
    node_count: dialogs.length,
    total_count: allDialogs.length,
    visible_count: visibleDialogs.length,
    truncated: maxNodes !== null && candidateDialogs.length > dialogs.length,
    dialogs
  }};
}}
""".strip()


def _wait_dialog_expression(
    *,
    selector: str | None,
    include_hidden: bool,
    max_nodes: int,
    max_controls: int,
    max_chars: int,
    text: str | None,
    match: str,
    case_sensitive: bool,
    modal_only: bool,
    timeout_ms: float,
    poll_ms: float,
) -> str:
    text_source = "null" if text is None else _js_literal(text)
    return f"""
() => new Promise((resolve) => {{
  const collectDialogs = {
        _dialog_snapshot_expression(
            selector=selector,
            include_hidden=include_hidden,
            max_nodes=max_nodes,
            max_controls=max_controls,
            max_chars=max_chars,
        )
    };
  const requestedText = {text_source};
  const matchMode = {_js_literal(match)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const modalOnly = {_js_literal(modal_only)};
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  const startedAt = Date.now();
  const requestedTextString = requestedText === null ? null : String(requestedText);
  const hasTextFilter = requestedTextString !== null && requestedTextString.length > 0;
  const normalizeForMatch = (value) => caseSensitive
    ? String(value ?? "")
    : String(value ?? "").toLowerCase();
  let pattern = null;
  if (hasTextFilter && matchMode === "regex") {{
    try {{
      pattern = new RegExp(requestedTextString, caseSensitive ? "" : "i");
    }} catch (error) {{
      const snapshot = collectDialogs();
      resolve({{
        url: snapshot.url,
        title: snapshot.title,
        kind: "dialog_wait",
        found: false,
        matched: false,
        timed_out: false,
        requested_text: requestedText,
        match: matchMode,
        case_sensitive: caseSensitive,
        modal_only: modalOnly,
        timeout_ms: timeoutMs,
        poll_ms: pollMs,
        waited_ms: Date.now() - startedAt,
        dialog_count: 0,
        total_dialog_count: snapshot.dialog_count,
        dialog: null,
        dialogs: [],
        error: "invalid_regex",
        message: String(error.message || error)
      }});
      return;
    }}
  }}
  const dialogText = (dialog) => [
    dialog.name,
    dialog.title,
    dialog.description,
    dialog.text,
    ...(dialog.controls || []).flatMap((control) => [control.name, control.text])
  ].filter(Boolean).join(" ");
  const textMatches = (dialog) => {{
    if (!hasTextFilter) return true;
    const candidate = dialogText(dialog);
    if (matchMode === "regex") return pattern.test(candidate);
    const candidateComparable = normalizeForMatch(candidate);
    const requestedComparable = normalizeForMatch(requestedTextString);
    if (matchMode === "exact") return candidateComparable === requestedComparable;
    return candidateComparable.includes(requestedComparable);
  }};
  const matchingDialogs = (snapshot) => (snapshot.dialogs || [])
    .filter((dialog) => !modalOnly || dialog.modal === true)
    .filter(textMatches);
  const finish = (found, snapshot, dialogs) => {{
    resolve({{
      url: snapshot.url,
      title: snapshot.title,
      kind: "dialog_wait",
      found,
      matched: found,
      timed_out: !found,
      requested_text: requestedText,
      match: matchMode,
      case_sensitive: caseSensitive,
      modal_only: modalOnly,
      timeout_ms: timeoutMs,
      poll_ms: pollMs,
      waited_ms: Date.now() - startedAt,
      dialog_count: dialogs.length,
      total_dialog_count: snapshot.dialog_count,
      dialog: dialogs.length ? dialogs[0] : null,
      dialogs: dialogs.slice(0, 5)
    }});
  }};
  const check = () => {{
    const snapshot = collectDialogs();
    const dialogs = matchingDialogs(snapshot);
    if (dialogs.length > 0) {{
      finish(true, snapshot, dialogs);
      return;
    }}
    if (Date.now() - startedAt >= timeoutMs) {{
      finish(false, snapshot, dialogs);
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _frame_snapshot_expression(
    *,
    selector: str | None,
    include_hidden: bool,
    max_nodes: int,
    max_chars: int,
) -> str:
    selector_source = "null" if selector is None else _js_literal(selector)
    return f"""
() => {{
{_dom_helpers_expression(include_hidden=include_hidden, max_nodes=max_nodes)}
  const rootSelector = {selector_source};
  const maxChars = Math.max(0, {_js_literal(max_chars)});
  const frameSelector = "iframe,frame";
  const sensitiveUrlParamName =
    /^(api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)$/i;
  const sensitiveUrlParamPattern =
    /([?&](?:api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)=)[^&#]*/gi;
  const maskUrlText = (value) => String(value ?? "").replace(
    sensitiveUrlParamPattern,
    "$1***"
  );
  const maskedParsedUrl = (value) => {{
    const raw = String(value ?? "");
    if (!raw) {{
      return {{
        absolute_url: raw,
        absolute_url_masked: false,
        origin: null,
        pathname: null,
        search: null,
        hash: null,
        same_origin: null,
        url_parse_error: null
      }};
    }}
    try {{
      const parsed = new URL(raw, location.href);
      let masked = false;
      if (parsed.username) {{
        parsed.username = "***";
        masked = true;
      }}
      if (parsed.password) {{
        parsed.password = "***";
        masked = true;
      }}
      for (const key of [...parsed.searchParams.keys()]) {{
        if (sensitiveUrlParamName.test(key)) {{
          parsed.searchParams.set(key, "***");
          masked = true;
        }}
      }}
      return {{
        absolute_url: parsed.href,
        absolute_url_masked: masked,
        origin: parsed.origin,
        pathname: parsed.pathname,
        search: parsed.search || null,
        hash: parsed.hash || null,
        same_origin: parsed.origin === location.origin,
        url_parse_error: null
      }};
    }} catch (error) {{
      const maskedRaw = maskUrlText(raw);
      return {{
        absolute_url: maskedRaw,
        absolute_url_masked: maskedRaw !== raw,
        origin: null,
        pathname: null,
        search: null,
        hash: null,
        same_origin: null,
        url_parse_error: String(error.message || error)
      }};
    }}
  }};
  const truncateText = (value) => {{
    const text = String(value ?? "");
    if (text.length <= maxChars) {{
      return {{ body_text: text, body_text_truncated: false }};
    }}
    return {{
      body_text: text.slice(0, maxChars),
      body_text_truncated: true
    }};
  }};
  const rectInfo = (element) => {{
    const rect = element.getBoundingClientRect();
    return {{
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
      top: rect.top,
      left: rect.left,
      bottom: rect.bottom,
      right: rect.right,
      in_viewport: rect.bottom > 0 &&
        rect.right > 0 &&
        rect.top < window.innerHeight &&
        rect.left < window.innerWidth
    }};
  }};
  const readableFrameInfo = (frame) => {{
    try {{
      const doc = frame.contentDocument;
      if (!doc) {{
        return {{
          readable: false,
          read_error: "contentDocument unavailable"
        }};
      }}
      const frameUrl = doc.location ? doc.location.href : "";
      const maskedFrameUrl = maskedParsedUrl(frameUrl);
      const bodyText = doc.body
        ? normalize(doc.body.innerText ?? doc.body.textContent ?? "")
        : "";
      return {{
        readable: true,
        read_error: null,
        frame_url: maskedFrameUrl.absolute_url,
        frame_url_masked: maskedFrameUrl.absolute_url_masked,
        frame_same_origin: maskedFrameUrl.same_origin,
        frame_title: doc.title || null,
        ready_state: doc.readyState,
        body_text_length: bodyText.length,
        ...truncateText(bodyText)
      }};
    }} catch (error) {{
      return {{
        readable: false,
        read_error: String(error.message || error)
      }};
    }}
  }};
  const roots = rootSelector === null
    ? [document.body || document.documentElement].filter(Boolean)
    : [...document.querySelectorAll(rootSelector)];
  const seen = new Set();
  const allFrames = [];
  for (const root of roots) {{
    const candidates = [
      ...(root.matches?.(frameSelector) ? [root] : []),
      ...root.querySelectorAll(frameSelector)
    ];
    for (const candidate of candidates) {{
      if (!seen.has(candidate)) {{
        seen.add(candidate);
        allFrames.push(candidate);
      }}
    }}
  }}
  const frameInfo = (frame, frameIndex) => {{
    const info = nodeInfo(frame);
    const rawSrc = frame.getAttribute("src") || "";
    const maskedSrc = maskUrlText(rawSrc);
    return {{
      frame_index: frameIndex,
      selector: info.selector,
      tag: info.tag,
      role: info.role,
      name: info.name,
      text: info.text,
      id: frame.id || null,
      name_attribute: frame.getAttribute("name"),
      title_attribute: frame.getAttribute("title"),
      src: maskedSrc,
      src_masked: maskedSrc !== rawSrc,
      ...maskedParsedUrl(rawSrc),
      sandbox: frame.getAttribute("sandbox"),
      allow: frame.getAttribute("allow"),
      loading: frame.getAttribute("loading"),
      referrer_policy: frame.getAttribute("referrerpolicy"),
      visible: info.visible,
      bounding_box: rectInfo(frame),
      ...readableFrameInfo(frame)
    }};
  }};
  const visibleFrames = allFrames.filter(visible);
  const candidateFrames = includeHidden ? allFrames : visibleFrames;
  const frames = limited(candidateFrames).map(frameInfo);
  return {{
    url: location.href,
    title: document.title,
    kind: "frames",
    selector: rootSelector,
    include_hidden: includeHidden,
    max_chars: maxChars,
    frame_count: candidateFrames.length,
    node_count: frames.length,
    total_count: allFrames.length,
    visible_count: visibleFrames.length,
    truncated: maxNodes !== null && candidateFrames.length > frames.length,
    frames
  }};
}}
""".strip()


def _wait_frame_expression(
    *,
    selector: str | None,
    include_hidden: bool,
    max_nodes: int,
    max_chars: int,
    url: str | None,
    url_match: str,
    text: str | None,
    text_match: str,
    case_sensitive: bool,
    readable_only: bool,
    same_origin_only: bool,
    timeout_ms: float,
    poll_ms: float,
) -> str:
    url_source = "null" if url is None else _js_literal(url)
    text_source = "null" if text is None else _js_literal(text)
    return f"""
() => new Promise((resolve) => {{
  const collectFrames = {
        _frame_snapshot_expression(
            selector=selector,
            include_hidden=include_hidden,
            max_nodes=max_nodes,
            max_chars=max_chars,
        )
    };
  const requestedUrl = {url_source};
  const urlMatchMode = {_js_literal(url_match)};
  const requestedText = {text_source};
  const textMatchMode = {_js_literal(text_match)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const readableOnly = {_js_literal(readable_only)};
  const sameOriginOnly = {_js_literal(same_origin_only)};
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  const startedAt = Date.now();
  const requestedUrlString = requestedUrl === null ? null : String(requestedUrl);
  const requestedTextString = requestedText === null ? null : String(requestedText);
  const hasUrlFilter = requestedUrlString !== null && requestedUrlString.length > 0;
  const hasTextFilter = requestedTextString !== null && requestedTextString.length > 0;
  const normalizeForMatch = (value) => caseSensitive
    ? String(value ?? "")
    : String(value ?? "").toLowerCase();
  let resolved = false;
  const finishInvalidRegex = (invalidFilter, error) => {{
    const snapshot = collectFrames();
    resolved = true;
    resolve({{
      url: snapshot.url,
      title: snapshot.title,
      kind: "frame_wait",
      found: false,
      matched: false,
      timed_out: false,
      requested_url: requestedUrl,
      url_match: urlMatchMode,
      requested_text: requestedText,
      text_match: textMatchMode,
      case_sensitive: caseSensitive,
      readable_only: readableOnly,
      same_origin_only: sameOriginOnly,
      timeout_ms: timeoutMs,
      poll_ms: pollMs,
      waited_ms: Date.now() - startedAt,
      frame_count: 0,
      total_frame_count: snapshot.frame_count,
      frame: null,
      frames: [],
      error: "invalid_regex",
      invalid_filter: invalidFilter,
      message: String(error.message || error)
    }});
  }};
  const compilePattern = (value, mode, invalidFilter) => {{
    if (value === null || value.length === 0 || mode !== "regex") return null;
    try {{
      return new RegExp(value, caseSensitive ? "" : "i");
    }} catch (error) {{
      finishInvalidRegex(invalidFilter, error);
      return null;
    }}
  }};
  const urlPattern = compilePattern(requestedUrlString, urlMatchMode, "url");
  if (resolved) return;
  const textPattern = compilePattern(requestedTextString, textMatchMode, "text");
  if (resolved) return;
  const valueMatches = (candidate, requested, mode, pattern) => {{
    if (requested === null || requested.length === 0) return true;
    if (mode === "regex") return pattern.test(candidate);
    const candidateComparable = normalizeForMatch(candidate);
    const requestedComparable = normalizeForMatch(requested);
    if (mode === "exact") return candidateComparable === requestedComparable;
    return candidateComparable.includes(requestedComparable);
  }};
  const frameUrlText = (frame) => [
    frame.src,
    frame.absolute_url,
    frame.frame_url,
    frame.origin,
    frame.pathname
  ].filter(Boolean).join(" ");
  const frameText = (frame) => [
    frame.name,
    frame.text,
    frame.name_attribute,
    frame.title_attribute,
    frame.frame_title,
    frame.body_text
  ].filter(Boolean).join(" ");
  const isSameOrigin = (frame) =>
    frame.same_origin === true || frame.frame_same_origin === true;
  const matchingFrames = (snapshot) => (snapshot.frames || [])
    .filter((frame) => !readableOnly || frame.readable === true)
    .filter((frame) => !sameOriginOnly || isSameOrigin(frame))
    .filter((frame) => !hasUrlFilter ||
      valueMatches(frameUrlText(frame), requestedUrlString, urlMatchMode, urlPattern)
    )
    .filter((frame) => !hasTextFilter ||
      valueMatches(frameText(frame), requestedTextString, textMatchMode, textPattern)
    );
  const finish = (found, snapshot, frames) => {{
    resolve({{
      url: snapshot.url,
      title: snapshot.title,
      kind: "frame_wait",
      found,
      matched: found,
      timed_out: !found,
      requested_url: requestedUrl,
      url_match: urlMatchMode,
      requested_text: requestedText,
      text_match: textMatchMode,
      case_sensitive: caseSensitive,
      readable_only: readableOnly,
      same_origin_only: sameOriginOnly,
      timeout_ms: timeoutMs,
      poll_ms: pollMs,
      waited_ms: Date.now() - startedAt,
      frame_count: frames.length,
      total_frame_count: snapshot.frame_count,
      frame: frames.length ? frames[0] : null,
      frames: frames.slice(0, 5)
    }});
  }};
  const check = () => {{
    const snapshot = collectFrames();
    const frames = matchingFrames(snapshot);
    if (frames.length > 0) {{
      finish(true, snapshot, frames);
      return;
    }}
    if (Date.now() - startedAt >= timeoutMs) {{
      finish(false, snapshot, frames);
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _performance_snapshot_expression(
    *,
    max_resources: int,
    initiator_type: str | None,
    min_duration_ms: float,
) -> str:
    initiator_type_source = (
        "null" if initiator_type is None else _js_literal(initiator_type)
    )
    return f"""
() => {{
  const maxResources = Math.max(0, {_js_literal(max_resources)});
  const requestedInitiatorType = {initiator_type_source};
  const minDurationMs = Math.max(0, {_js_literal(min_duration_ms)});
  const sensitiveUrlParamName =
    /^(api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)$/i;
  const sensitiveUrlParamPattern =
    /([?&](?:api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)=)[^&#]*/gi;
  const maskUrlText = (value) => String(value ?? "").replace(
    sensitiveUrlParamPattern,
    "$1***"
  );
  const numberOrNull = (value) => Number.isFinite(value) ? value : null;
  const maskedParsedUrl = (value) => {{
    const raw = String(value ?? "");
    if (!raw) {{
      return {{
        absolute_url: raw,
        absolute_url_masked: false,
        origin: null,
        pathname: null,
        search: null,
        hash: null,
        same_origin: null,
        url_parse_error: null
      }};
    }}
    try {{
      const parsed = new URL(raw, location.href);
      let masked = false;
      if (parsed.username) {{
        parsed.username = "***";
        masked = true;
      }}
      if (parsed.password) {{
        parsed.password = "***";
        masked = true;
      }}
      for (const key of [...parsed.searchParams.keys()]) {{
        if (sensitiveUrlParamName.test(key)) {{
          parsed.searchParams.set(key, "***");
          masked = true;
        }}
      }}
      return {{
        absolute_url: parsed.href,
        absolute_url_masked: masked,
        origin: parsed.origin,
        pathname: parsed.pathname,
        search: parsed.search || null,
        hash: parsed.hash || null,
        same_origin: parsed.origin === location.origin,
        url_parse_error: null
      }};
    }} catch (error) {{
      const maskedRaw = maskUrlText(raw);
      return {{
        absolute_url: maskedRaw,
        absolute_url_masked: maskedRaw !== raw,
        origin: null,
        pathname: null,
        search: null,
        hash: null,
        same_origin: null,
        url_parse_error: String(error.message || error)
      }};
    }}
  }};
  const urlPayload = (value) => {{
    const raw = String(value ?? "");
    const maskedName = maskUrlText(raw);
    return {{
      name: maskedName,
      name_masked: maskedName !== raw,
      ...maskedParsedUrl(raw)
    }};
  }};
  const responseStatus = (entry) =>
    "responseStatus" in entry ? numberOrNull(entry.responseStatus) : null;
  const timingPayload = (entry) => ({{
    start_time: numberOrNull(entry.startTime),
    duration: numberOrNull(entry.duration),
    fetch_start: numberOrNull(entry.fetchStart),
    domain_lookup_start: numberOrNull(entry.domainLookupStart),
    domain_lookup_end: numberOrNull(entry.domainLookupEnd),
    connect_start: numberOrNull(entry.connectStart),
    connect_end: numberOrNull(entry.connectEnd),
    secure_connection_start: numberOrNull(entry.secureConnectionStart),
    request_start: numberOrNull(entry.requestStart),
    response_start: numberOrNull(entry.responseStart),
    response_end: numberOrNull(entry.responseEnd),
    transfer_size: numberOrNull(entry.transferSize),
    encoded_body_size: numberOrNull(entry.encodedBodySize),
    decoded_body_size: numberOrNull(entry.decodedBodySize),
    next_hop_protocol: entry.nextHopProtocol || null,
    response_status: responseStatus(entry)
  }});
  const navigationEntry = performance.getEntriesByType("navigation")[0] || null;
  const navigation = navigationEntry ? {{
    ...urlPayload(navigationEntry.name),
    entry_type: navigationEntry.entryType,
    type: navigationEntry.type || null,
    redirect_count: numberOrNull(navigationEntry.redirectCount),
    worker_start: numberOrNull(navigationEntry.workerStart),
    dom_interactive: numberOrNull(navigationEntry.domInteractive),
    dom_content_loaded_event_start: numberOrNull(navigationEntry.domContentLoadedEventStart),
    dom_content_loaded_event_end: numberOrNull(navigationEntry.domContentLoadedEventEnd),
    dom_complete: numberOrNull(navigationEntry.domComplete),
    load_event_start: numberOrNull(navigationEntry.loadEventStart),
    load_event_end: numberOrNull(navigationEntry.loadEventEnd),
    activation_start: numberOrNull(navigationEntry.activationStart),
    ...timingPayload(navigationEntry)
  }} : null;
  const allResources = performance.getEntriesByType("resource");
  const candidateResources = allResources
    .filter((entry) =>
      requestedInitiatorType === null || entry.initiatorType === requestedInitiatorType
    )
    .filter((entry) => entry.duration >= minDurationMs);
  const resourceInfo = (entry, index) => ({{
    index,
    ...urlPayload(entry.name),
    entry_type: entry.entryType,
    initiator_type: entry.initiatorType || null,
    render_blocking_status: entry.renderBlockingStatus || null,
    delivery_type: "deliveryType" in entry ? entry.deliveryType || null : null,
    worker_start: numberOrNull(entry.workerStart),
    redirect_start: numberOrNull(entry.redirectStart),
    redirect_end: numberOrNull(entry.redirectEnd),
    ...timingPayload(entry)
  }});
  const resources = candidateResources
    .slice(0, maxResources)
    .map(resourceInfo);
  const initiatorTypes = [...new Set(allResources.map((entry) => entry.initiatorType || ""))]
    .filter(Boolean)
    .sort();
  return {{
    url: location.href,
    title: document.title,
    kind: "performance",
    time_origin: performance.timeOrigin,
    now: performance.now(),
    requested_initiator_type: requestedInitiatorType,
    min_duration_ms: minDurationMs,
    max_resources: maxResources,
    navigation,
    resource_count: candidateResources.length,
    node_count: resources.length,
    total_count: allResources.length,
    initiator_types: initiatorTypes,
    truncated: candidateResources.length > resources.length,
    resources
  }};
}}
""".strip()


def _network_snapshot_expression(
    *,
    max_entries: int,
    clear: bool,
    install_only: bool,
    source: str | None,
    method: str | None,
    failed_only: bool,
) -> str:
    source_filter = "null" if source is None else _js_literal(source)
    method_filter = "null" if method is None else _js_literal(method.upper())
    return f"""
() => {{
  const maxEntries = Math.max(0, {_js_literal(max_entries)});
  const clearRequested = {_js_literal(clear)};
  const installOnly = {_js_literal(install_only)};
  const requestedSource = {source_filter};
  const requestedMethod = {method_filter};
  const failedOnly = {_js_literal(failed_only)};
  const stateKey = "__browserCliNetworkSnapshot";
  const sensitiveUrlParamName =
    /^(api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)$/i;
  const sensitiveUrlParamPattern =
    /([?&](?:api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)=)[^&#]*/gi;
  const maskUrlText = (value) => String(value ?? "").replace(
    sensitiveUrlParamPattern,
    "$1***"
  );
  const numberOrNull = (value) => Number.isFinite(value) ? value : null;
  const maskedParsedUrl = (value) => {{
    const raw = String(value ?? "");
    if (!raw) {{
      return {{
        absolute_url: raw,
        absolute_url_masked: false,
        origin: null,
        pathname: null,
        search: null,
        hash: null,
        same_origin: null,
        url_parse_error: null
      }};
    }}
    try {{
      const parsed = new URL(raw, location.href);
      let masked = false;
      if (parsed.username) {{
        parsed.username = "***";
        masked = true;
      }}
      if (parsed.password) {{
        parsed.password = "***";
        masked = true;
      }}
      for (const key of [...parsed.searchParams.keys()]) {{
        if (sensitiveUrlParamName.test(key)) {{
          parsed.searchParams.set(key, "***");
          masked = true;
        }}
      }}
      return {{
        absolute_url: parsed.href,
        absolute_url_masked: masked,
        origin: parsed.origin,
        pathname: parsed.pathname,
        search: parsed.search || null,
        hash: parsed.hash || null,
        same_origin: parsed.origin === location.origin,
        url_parse_error: null
      }};
    }} catch (error) {{
      const maskedRaw = maskUrlText(raw);
      return {{
        absolute_url: maskedRaw,
        absolute_url_masked: maskedRaw !== raw,
        origin: null,
        pathname: null,
        search: null,
        hash: null,
        same_origin: null,
        url_parse_error: String(error.message || error)
      }};
    }}
  }};
  const urlPayload = (value) => {{
    const raw = String(value ?? "");
    const maskedUrl = maskUrlText(raw);
    return {{
      url: maskedUrl,
      url_masked: maskedUrl !== raw,
      ...maskedParsedUrl(raw)
    }};
  }};
  const state = window[stateKey] || {{
    installed: false,
    installed_at: Date.now(),
    next_index: 0,
    buffer_limit: 500,
    entries: [],
    originals: {{}}
  }};
  window[stateKey] = state;
  const push = (entry) => {{
    const payload = {{
      index: state.next_index++,
      timestamp_ms: Date.now(),
      elapsed_ms: performance.now(),
      ...entry
    }};
    state.entries.push(payload);
    if (state.entries.length > state.buffer_limit) {{
      state.entries.splice(0, state.entries.length - state.buffer_limit);
    }}
  }};
  const requestUrlFromFetchArgs = (input) => {{
    if (typeof input === "string") return input;
    if (input instanceof URL) return input.href;
    if (input && typeof input === "object" && "url" in input) return input.url;
    return String(input ?? "");
  }};
  const requestMethodFromFetchArgs = (input, init) => {{
    if (init && init.method) return String(init.method).toUpperCase();
    if (input && typeof input === "object" && "method" in input && input.method) {{
      return String(input.method).toUpperCase();
    }}
    return "GET";
  }};
  const safeStatus = (xhr) => {{
    try {{ return xhr.status || 0; }} catch (error) {{ return 0; }}
  }};
  const safeStatusText = (xhr) => {{
    try {{ return xhr.statusText || ""; }} catch (error) {{ return ""; }}
  }};
  const install = () => {{
    if (state.installed) return false;
    if (typeof window.fetch === "function") {{
      const originalFetch = window.fetch;
      state.originals.fetch = originalFetch;
      window.fetch = function (...args) {{
        const input = args[0];
        const init = args[1] || null;
        const rawUrl = requestUrlFromFetchArgs(input);
        const method = requestMethodFromFetchArgs(input, init);
        const startedAt = performance.now();
        const startedWall = Date.now();
        const requestHasBody = Boolean(init && init.body);
        return originalFetch.apply(this, args).then(
          (response) => {{
            const completedAt = Date.now();
            push({{
              source: "fetch",
              method,
              ...urlPayload(rawUrl),
              status: response.status,
              status_text: response.statusText || "",
              ok: Boolean(response.ok),
              redirected: Boolean(response.redirected),
              response_type: response.type || null,
              failed: false,
              request_has_body: requestHasBody,
              duration_ms: numberOrNull(performance.now() - startedAt),
              started_at: startedWall,
              completed_at: completedAt
            }});
            return response;
          }},
          (error) => {{
            const completedAt = Date.now();
            push({{
              source: "fetch",
              method,
              ...urlPayload(rawUrl),
              status: null,
              status_text: "",
              ok: false,
              redirected: null,
              response_type: null,
              failed: true,
              error_name: error && error.name ? String(error.name) : "Error",
              error_message: String(error && error.message ? error.message : error),
              request_has_body: requestHasBody,
              duration_ms: numberOrNull(performance.now() - startedAt),
              started_at: startedWall,
              completed_at: completedAt
            }});
            throw error;
          }}
        );
      }};
    }}
    if (window.XMLHttpRequest?.prototype) {{
      const originalOpen = window.XMLHttpRequest.prototype.open;
      const originalSend = window.XMLHttpRequest.prototype.send;
      if (typeof originalOpen === "function" && typeof originalSend === "function") {{
        state.originals.xhrOpen = originalOpen;
        state.originals.xhrSend = originalSend;
        window.XMLHttpRequest.prototype.open = function(method, url, ...rest) {{
          this.__browserCliNetworkRequest = {{
            method: String(method || "GET").toUpperCase(),
            url: String(url ?? "")
          }};
          return originalOpen.call(this, method, url, ...rest);
        }};
        window.XMLHttpRequest.prototype.send = function(body) {{
          const meta = this.__browserCliNetworkRequest || {{
            method: "GET",
            url: ""
          }};
          const startedAt = performance.now();
          const startedWall = Date.now();
          let recorded = false;
          const record = (failed, error = null) => {{
            if (recorded) return;
            recorded = true;
            const status = failed && error ? null : safeStatus(this);
            push({{
              source: "xhr",
              method: meta.method,
              ...urlPayload(meta.url),
              status,
              status_text: failed && error ? "" : safeStatusText(this),
              ok: status !== null ? status >= 200 && status < 400 : false,
              failed,
              error_name: error && error.name ? String(error.name) : null,
              error_message: error ? String(error.message || error) : null,
              request_has_body: body !== undefined && body !== null,
              duration_ms: numberOrNull(performance.now() - startedAt),
              started_at: startedWall,
              completed_at: Date.now()
            }});
          }};
          this.addEventListener("loadend", () => record(false), {{ once: true }});
          this.addEventListener("error", () => record(true, new Error("xhr_error")), {{ once: true }});
          this.addEventListener("timeout", () => record(true, new Error("xhr_timeout")), {{ once: true }});
          this.addEventListener("abort", () => record(true, new Error("xhr_abort")), {{ once: true }});
          try {{
            return originalSend.call(this, body);
          }} catch (error) {{
            record(true, error);
            throw error;
          }}
        }};
      }}
    }}
    state.installed = true;
    state.installed_at = Date.now();
    return true;
  }};
  const newlyInstalled = install();
  const matchesFilters = (entry) => {{
    if (requestedSource !== null && entry.source !== requestedSource) return false;
    if (requestedMethod !== null && entry.method !== requestedMethod) return false;
    if (failedOnly && !entry.failed) return false;
    return true;
  }};
  const bufferedCount = state.entries.length;
  const matchedEntries = state.entries.filter(matchesFilters);
  const entries = installOnly ? [] : matchedEntries.slice(-maxEntries);
  const truncated = !installOnly && matchedEntries.length > entries.length;
  if (clearRequested) {{
    state.entries = [];
  }}
  const maskedLocation = maskUrlText(location.href);
  return {{
    url: maskedLocation,
    url_masked: maskedLocation !== location.href,
    title: document.title,
    kind: "network",
    installed: state.installed,
    newly_installed: newlyInstalled,
    installed_at: state.installed_at,
    install_only: installOnly,
    clear: clearRequested,
    max_entries: maxEntries,
    requested_source: requestedSource,
    requested_method: requestedMethod,
    failed_only: failedOnly,
    fetch_instrumented: typeof state.originals.fetch === "function",
    xhr_instrumented: typeof state.originals.xhrSend === "function",
    entry_count: entries.length,
    matched_count: matchedEntries.length,
    buffered_count: bufferedCount,
    buffered_count_after: state.entries.length,
    truncated,
    entries
  }};
}}
""".strip()


def _network_capture_bootstrap_expression() -> str:
    expression = _network_snapshot_expression(
        max_entries=0,
        clear=False,
        install_only=True,
        source=None,
        method=None,
        failed_only=False,
    )
    start_marker = '  const stateKey = "__browserCliNetworkSnapshot";'
    end_marker = "  const newlyInstalled = install();"
    return expression[
        expression.index(start_marker) : expression.index(end_marker)
    ].rstrip()


def _wait_network_expression(
    *,
    url: str | None,
    url_match: str,
    source: str | None,
    method: str | None,
    status: int | None,
    failed_only: bool,
    case_sensitive: bool,
    after_index: int | None,
    timeout_ms: float,
    poll_ms: float,
) -> str:
    url_source = "null" if url is None else _js_literal(url)
    source_filter = "null" if source is None else _js_literal(source)
    method_filter = "null" if method is None else _js_literal(method.upper())
    status_filter = "null" if status is None else _js_literal(status)
    after_index_source = "null" if after_index is None else _js_literal(after_index)
    return f"""
() => new Promise((resolve) => {{
{_network_capture_bootstrap_expression()}
  const requestedUrl = {url_source};
  const urlMatchMode = {_js_literal(url_match)};
  const requestedSource = {source_filter};
  const requestedMethod = {method_filter};
  const requestedStatus = {status_filter};
  const failedOnly = {_js_literal(failed_only)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const afterIndex = {after_index_source};
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  const startedAt = Date.now();
  const newlyInstalled = install();
  const requestedUrlString = requestedUrl === null ? null : String(requestedUrl);
  const hasUrlFilter = requestedUrlString !== null && requestedUrlString.length > 0;
  const normalizeForMatch = (value) => caseSensitive
    ? String(value ?? "")
    : String(value ?? "").toLowerCase();
  let pattern = null;
  if (hasUrlFilter && urlMatchMode === "regex") {{
    try {{
      pattern = new RegExp(requestedUrlString, caseSensitive ? "" : "i");
    }} catch (error) {{
      const maskedLocation = maskUrlText(location.href);
      resolve({{
        url: maskedLocation,
        url_masked: maskedLocation !== location.href,
        title: document.title,
        kind: "network_wait",
        found: false,
        matched: false,
        timed_out: false,
        requested_url: requestedUrl,
        url_match: urlMatchMode,
        case_sensitive: caseSensitive,
        requested_source: requestedSource,
        requested_method: requestedMethod,
        requested_status: requestedStatus,
        failed_only: failedOnly,
        after_index: afterIndex,
        timeout_ms: timeoutMs,
        poll_ms: pollMs,
        waited_ms: Date.now() - startedAt,
        installed: state.installed,
        newly_installed: newlyInstalled,
        installed_at: state.installed_at,
        entry_count: 0,
        buffered_count: state.entries.length,
        entry: null,
        entries: [],
        error: "invalid_regex",
        message: String(error.message || error)
      }});
      return;
    }}
  }}
  const entryUrl = (entry) => entry.absolute_url || entry.url || "";
  const urlMatches = (entry) => {{
    if (!hasUrlFilter) return true;
    const candidate = entryUrl(entry);
    if (urlMatchMode === "regex") return pattern.test(candidate);
    const candidateComparable = normalizeForMatch(candidate);
    const requestedComparable = normalizeForMatch(requestedUrlString);
    if (urlMatchMode === "exact") return candidateComparable === requestedComparable;
    return candidateComparable.includes(requestedComparable);
  }};
  const entryMatches = (entry) => {{
    if (afterIndex !== null && Number(entry.index) <= afterIndex) return false;
    if (requestedSource !== null && entry.source !== requestedSource) return false;
    if (requestedMethod !== null && entry.method !== requestedMethod) return false;
    if (requestedStatus !== null && entry.status !== requestedStatus) return false;
    if (failedOnly && !entry.failed) return false;
    return urlMatches(entry);
  }};
  const matchingEntries = () => state.entries.filter(entryMatches);
  const finish = (found) => {{
    const entries = matchingEntries();
    const maskedLocation = maskUrlText(location.href);
    const waitedMs = Date.now() - startedAt;
    resolve({{
      url: maskedLocation,
      url_masked: maskedLocation !== location.href,
      title: document.title,
      kind: "network_wait",
      found,
      matched: found,
      timed_out: !found,
      requested_url: requestedUrl,
      url_match: urlMatchMode,
      case_sensitive: caseSensitive,
      requested_source: requestedSource,
      requested_method: requestedMethod,
      requested_status: requestedStatus,
      failed_only: failedOnly,
      after_index: afterIndex,
      timeout_ms: timeoutMs,
      poll_ms: pollMs,
      waited_ms: waitedMs,
      installed: state.installed,
      newly_installed: newlyInstalled,
      installed_at: state.installed_at,
      entry_count: entries.length,
      buffered_count: state.entries.length,
      entry: entries.length ? entries[0] : null,
      entries: entries.slice(0, 5)
    }});
  }};
  const check = () => {{
    if (matchingEntries().length > 0) {{
      finish(true);
      return;
    }}
    if (Date.now() - startedAt >= timeoutMs) {{
      finish(false);
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _console_snapshot_expression(
    *,
    max_entries: int,
    clear: bool,
    install_only: bool,
) -> str:
    return f"""
() => {{
  const maxEntries = Math.max(0, {_js_literal(max_entries)});
  const clearRequested = {_js_literal(clear)};
  const installOnly = {_js_literal(install_only)};
  const stateKey = "__browserCliConsoleSnapshot";
  const sensitiveNamePattern =
    /api[-_]?key|apikey|authorization|bearer|credential|password|passwd|secret|token|code/i;
  const sensitiveUrlParamPattern =
    /([?&](?:api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)=)[^&#]*/gi;
  const sensitivePairPattern =
    /((?:api[-_]?key|apikey|key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|auth|authorization|code|secret|password|passwd|credential|bearer)\\s*[:=]\\s*)(?:"[^"]*"|'[^']*'|[^\\s,;&}}]+)/gi;
  const normalize = (value) => String(value ?? "").replace(/\\s+/g, " ").trim();
  const truncate = (value, maxLength = 1000) => {{
    const text = String(value ?? "");
    if (text.length <= maxLength) {{
      return {{ text, truncated: false }};
    }}
    return {{ text: text.slice(0, maxLength), truncated: true }};
  }};
  const maskText = (value) => String(value ?? "")
    .replace(sensitiveUrlParamPattern, "$1***")
    .replace(sensitivePairPattern, "$1***");
  const maskedTextPayload = (value, maxLength = 1000) => {{
    const raw = String(value ?? "");
    const masked = maskText(raw);
    const truncated = truncate(masked, maxLength);
    return {{
      text: truncated.text,
      text_masked: masked !== raw,
      text_truncated: truncated.truncated,
      text_length: raw.length
    }};
  }};
  const elementPayload = (element) => ({{
    tag: element.tagName.toLowerCase(),
    id: element.id || null,
    class_name: element.className || null,
    role: element.getAttribute("role"),
    name: element.getAttribute("aria-label") || element.getAttribute("title") || null
  }});
  const state = window[stateKey] || {{
    installed: false,
    installed_at: Date.now(),
    next_index: 0,
    buffer_limit: 500,
    entries: [],
    originals: {{}},
    listeners: {{}}
  }};
  window[stateKey] = state;
  const push = (entry) => {{
    const payload = {{
      index: state.next_index++,
      timestamp_ms: Date.now(),
      elapsed_ms: performance.now(),
      ...entry
    }};
    state.entries.push(payload);
    if (state.entries.length > state.buffer_limit) {{
      state.entries.splice(0, state.entries.length - state.buffer_limit);
    }}
  }};
  const sanitizeValue = (value, depth = 0, seen = new WeakSet()) => {{
    if (value === null) return null;
    const type = typeof value;
    if (type === "string") {{
      const masked = maskText(value);
      return masked.length > 500 ? `${{masked.slice(0, 500)}}...` : masked;
    }}
    if (["number", "boolean", "undefined", "bigint"].includes(type)) {{
      return String(value);
    }}
    if (type === "function") return "[Function]";
    if (value instanceof Error) {{
      return {{
        name: value.name || "Error",
        message: maskText(value.message || ""),
        stack: maskText(value.stack || "")
      }};
    }}
    if (value instanceof Element) return elementPayload(value);
    if (depth >= 2) return `[${{Object.prototype.toString.call(value)}}]`;
    if (seen.has(value)) return "[Circular]";
    seen.add(value);
    if (Array.isArray(value)) {{
      return value.slice(0, 10).map((item) => sanitizeValue(item, depth + 1, seen));
    }}
    const result = {{}};
    for (const [key, nestedValue] of Object.entries(value).slice(0, 20)) {{
      result[key] = sensitiveNamePattern.test(key)
        ? "***"
        : sanitizeValue(nestedValue, depth + 1, seen);
    }}
    return result;
  }};
  const argPayload = (value) => {{
    const type = value === null
      ? "null"
      : Array.isArray(value)
        ? "array"
        : typeof value;
    let sanitized;
    try {{
      sanitized = sanitizeValue(value);
    }} catch (error) {{
      sanitized = `[Unserializable: ${{String(error.message || error)}}]`;
    }}
    const textValue = type === "string"
      ? value
      : value instanceof Error
        ? `${{value.name || "Error"}}: ${{value.message || ""}}`
        : (() => {{
            try {{
              return JSON.stringify(sanitized);
            }} catch (error) {{
              return String(value);
            }}
          }})();
    return {{
      type,
      value: sanitized,
      ...maskedTextPayload(textValue, 1000)
    }};
  }};
  const entryText = (args) => normalize(args.map((arg) => arg.text).join(" "));
  const entryTextPayload = (args) => ({{
    text: entryText(args),
    text_masked: args.some((arg) => Boolean(arg.text_masked)),
    text_truncated: args.some((arg) => Boolean(arg.text_truncated))
  }});
  const install = () => {{
    if (state.installed) return false;
    for (const method of ["debug", "log", "info", "warn", "error"]) {{
      const original = console[method];
      state.originals[method] = original;
      console[method] = function (...args) {{
        const argPayloads = args.map(argPayload);
        push({{
          source: "console",
          level: method === "log" ? "info" : method,
          method,
          ...entryTextPayload(argPayloads),
          args: argPayloads
        }});
        return original.apply(this, args);
      }};
    }}
    state.listeners.error = (event) => {{
      const error = event.error || null;
      const argPayloads = error ? [argPayload(error)] : [argPayload(event.message || "")];
      const textPayload = maskedTextPayload(event.message || entryText(argPayloads));
      const maskedFilename = maskText(event.filename || "");
      push({{
        source: "pageerror",
        level: "error",
        method: "error",
        ...textPayload,
        args: argPayloads,
        filename: maskedFilename,
        filename_masked: maskedFilename !== String(event.filename || ""),
        lineno: event.lineno || null,
        colno: event.colno || null
      }});
    }};
    state.listeners.unhandledrejection = (event) => {{
      const argPayloads = [argPayload(event.reason)];
      push({{
        source: "unhandledrejection",
        level: "error",
        method: "unhandledrejection",
        ...entryTextPayload(argPayloads),
        args: argPayloads
      }});
    }};
    window.addEventListener("error", state.listeners.error);
    window.addEventListener("unhandledrejection", state.listeners.unhandledrejection);
    state.installed = true;
    state.installed_at = Date.now();
    return true;
  }};
  const newlyInstalled = install();
  const maskedLocation = maskText(location.href);
  const bufferedCount = state.entries.length;
  const entries = installOnly ? [] : state.entries.slice(-maxEntries);
  const truncated = !installOnly && bufferedCount > entries.length;
  if (clearRequested) {{
    state.entries = [];
  }}
  return {{
    url: maskedLocation,
    url_masked: maskedLocation !== location.href,
    title: document.title,
    kind: "console",
    installed: state.installed,
    newly_installed: newlyInstalled,
    installed_at: state.installed_at,
    install_only: installOnly,
    clear: clearRequested,
    max_entries: maxEntries,
    entry_count: entries.length,
    buffered_count: bufferedCount,
    buffered_count_after: state.entries.length,
    truncated,
    entries
  }};
}}
""".strip()


def _console_capture_bootstrap_expression() -> str:
    expression = _console_snapshot_expression(
        max_entries=0,
        clear=False,
        install_only=True,
    )
    start_marker = '  const stateKey = "__browserCliConsoleSnapshot";'
    end_marker = "  const newlyInstalled = install();"
    return expression[
        expression.index(start_marker) : expression.index(end_marker)
    ].rstrip()


def _wait_console_expression(
    *,
    text: str | None,
    match: str,
    source: str | None,
    level: str | None,
    case_sensitive: bool,
    after_index: int | None,
    timeout_ms: float,
    poll_ms: float,
) -> str:
    text_source = "null" if text is None else _js_literal(text)
    source_source = "null" if source is None else _js_literal(source)
    level_source = "null" if level is None else _js_literal(level)
    after_index_source = "null" if after_index is None else _js_literal(after_index)
    return f"""
() => new Promise((resolve) => {{
{_console_capture_bootstrap_expression()}
  const requestedText = {text_source};
  const matchMode = {_js_literal(match)};
  const requestedSource = {source_source};
  const requestedLevel = {level_source};
  const caseSensitive = {_js_literal(case_sensitive)};
  const afterIndex = {after_index_source};
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  const startedAt = Date.now();
  const newlyInstalled = install();
  const requestedTextString = requestedText === null ? null : String(requestedText);
  const hasTextFilter = requestedTextString !== null && requestedTextString.length > 0;
  const normalizeForMatch = (value) => caseSensitive
    ? String(value ?? "")
    : String(value ?? "").toLowerCase();
  let pattern = null;
  if (hasTextFilter && matchMode === "regex") {{
    try {{
      pattern = new RegExp(requestedTextString, caseSensitive ? "" : "i");
    }} catch (error) {{
      const maskedLocation = maskText(location.href);
      resolve({{
        url: maskedLocation,
        url_masked: maskedLocation !== location.href,
        title: document.title,
        kind: "console_wait",
        found: false,
        matched: false,
        timed_out: false,
        requested_text: requestedText,
        match: matchMode,
        case_sensitive: caseSensitive,
        requested_source: requestedSource,
        requested_level: requestedLevel,
        after_index: afterIndex,
        timeout_ms: timeoutMs,
        poll_ms: pollMs,
        waited_ms: Date.now() - startedAt,
        installed: state.installed,
        newly_installed: newlyInstalled,
        installed_at: state.installed_at,
        entry_count: 0,
        buffered_count: state.entries.length,
        entry: null,
        entries: [],
        error: "invalid_regex",
        message: String(error.message || error)
      }});
      return;
    }}
  }}
  const textMatches = (entry) => {{
    if (!hasTextFilter) return true;
    const candidate = String(entry.text || "");
    if (matchMode === "regex") return pattern.test(candidate);
    const candidateComparable = normalizeForMatch(candidate);
    const requestedComparable = normalizeForMatch(requestedTextString);
    if (matchMode === "exact") return candidateComparable === requestedComparable;
    return candidateComparable.includes(requestedComparable);
  }};
  const entryMatches = (entry) => {{
    if (afterIndex !== null && Number(entry.index) <= afterIndex) return false;
    if (requestedSource !== null && entry.source !== requestedSource) return false;
    if (requestedLevel !== null && entry.level !== requestedLevel) return false;
    return textMatches(entry);
  }};
  const matchingEntries = () => state.entries.filter(entryMatches);
  const finish = (found) => {{
    const entries = matchingEntries();
    const maskedLocation = maskText(location.href);
    const waitedMs = Date.now() - startedAt;
    resolve({{
      url: maskedLocation,
      url_masked: maskedLocation !== location.href,
      title: document.title,
      kind: "console_wait",
      found,
      matched: found,
      timed_out: !found,
      requested_text: requestedText,
      match: matchMode,
      case_sensitive: caseSensitive,
      requested_source: requestedSource,
      requested_level: requestedLevel,
      after_index: afterIndex,
      timeout_ms: timeoutMs,
      poll_ms: pollMs,
      waited_ms: waitedMs,
      installed: state.installed,
      newly_installed: newlyInstalled,
      installed_at: state.installed_at,
      entry_count: entries.length,
      buffered_count: state.entries.length,
      entry: entries.length ? entries[0] : null,
      entries: entries.slice(0, 5)
    }});
  }};
  const check = () => {{
    if (matchingEntries().length > 0) {{
      finish(true);
      return;
    }}
    if (Date.now() - startedAt >= timeoutMs) {{
      finish(false);
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _outline_snapshot_expression(
    *,
    selector: str | None,
    include_hidden: bool,
    max_nodes: int,
) -> str:
    selector_source = "null" if selector is None else _js_literal(selector)
    return f"""
() => {{
{_dom_helpers_expression(include_hidden=include_hidden, max_nodes=max_nodes)}
  const rootSelector = {selector_source};
  const outlineSelector = [
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "[role~='heading']",
    "main",
    "nav",
    "header",
    "footer",
    "aside",
    "section",
    "article",
    "form",
    "search",
    "[role~='main']",
    "[role~='navigation']",
    "[role~='banner']",
    "[role~='contentinfo']",
    "[role~='complementary']",
    "[role~='region']",
    "[role~='search']",
    "[role~='form']"
  ].join(",");
  const roots = rootSelector === null
    ? [document.body || document.documentElement].filter(Boolean)
    : [...document.querySelectorAll(rootSelector)];
  const seen = new Set();
  const candidates = [];
  const explicitRoleOf = (element) => normalize(element.getAttribute("role")).split(" ")[0];
  const semanticLandmarkRole = (element) => {{
    const explicitRole = explicitRoleOf(element);
    if ([
      "main",
      "navigation",
      "banner",
      "contentinfo",
      "complementary",
      "region",
      "search",
      "form"
    ].includes(explicitRole)) {{
      return explicitRole;
    }}
    const tag = element.tagName.toLowerCase();
    if (tag === "main") return "main";
    if (tag === "nav") return "navigation";
    if (tag === "header") return "banner";
    if (tag === "footer") return "contentinfo";
    if (tag === "aside") return "complementary";
    if (tag === "form") return "form";
    if (tag === "search") return "search";
    if (["section", "article"].includes(tag) && accessibleName(element)) {{
      return "region";
    }}
    return "";
  }};
  const headingLevel = (element) => {{
    const tag = element.tagName.toLowerCase();
    if (/^h[1-6]$/.test(tag)) return Number(tag.slice(1));
    const value = Number(element.getAttribute("aria-level"));
    return Number.isFinite(value) && value > 0 ? value : null;
  }};
  for (const root of roots) {{
    const nodes = [
      ...(root.matches?.(outlineSelector) ? [root] : []),
      ...root.querySelectorAll(outlineSelector)
    ];
    for (const node of nodes) {{
      if (!seen.has(node)) {{
        seen.add(node);
        candidates.push(node);
      }}
    }}
  }}
  const visibleNodes = candidates.filter(visible);
  const outlineNodes = (includeHidden ? candidates : visibleNodes)
    .map((element, index) => {{
      const tag = element.tagName.toLowerCase();
      const role = roleOf(element) || semanticLandmarkRole(element) || null;
      const level = role === "heading" ? headingLevel(element) : null;
      const nodeType = role === "heading" || level !== null
        ? "heading"
        : "landmark";
      const info = nodeInfo(element);
      return {{
        index,
        node_type: nodeType,
        selector: info.selector,
        tag,
        role,
        level,
        name: info.name,
        text: info.text,
        visible: info.visible
      }};
    }})
    .filter((node) => node.node_type === "heading" || node.role);
  const headings = outlineNodes.filter((node) => node.node_type === "heading");
  const landmarks = outlineNodes.filter((node) => node.node_type === "landmark");
  const nodes = limited(outlineNodes);
  return {{
    url: location.href,
    title: document.title,
    kind: "outline",
    selector: rootSelector,
    include_hidden: includeHidden,
    node_count: nodes.length,
    total_count: candidates.length,
    visible_count: visibleNodes.length,
    outline_count: outlineNodes.length,
    heading_count: headings.length,
    landmark_count: landmarks.length,
    truncated: maxNodes !== null && outlineNodes.length > nodes.length,
    headings: limited(headings),
    landmarks: limited(landmarks),
    nodes
  }};
}}
""".strip()


def _wait_text_expression(
    *,
    text: str,
    selector: str | None,
    state: str,
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
  const requestedState = {_js_literal(state)};
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
    const matched = Boolean(element);
    const reached = requestedState === "absent" ? !matched : matched;
    if (reached) {{
      if (requestedState === "present") {{
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
      resolve({{
        found: matched,
        matched,
        state: requestedState,
        text: requestedText,
        selector,
        waited_ms: waitedMs,
        candidate_count: nodes.length,
        element: element ? nodeInfo(element) : null
      }});
      return;
    }}
    if (waitedMs >= timeoutMs) {{
      if (requestedState === "present") {{
        resolve({{
          found: false,
          text: requestedText,
          selector,
          waited_ms: waitedMs,
          candidate_count: nodes.length
        }});
        return;
      }}
      resolve({{
        found: matched,
        matched,
        state: requestedState,
        text: requestedText,
        selector,
        waited_ms: waitedMs,
        candidate_count: nodes.length,
        element: element ? nodeInfo(element) : null
      }});
      return;
    }}
    setTimeout(check, pollMs);
  }};
  check();
}})
""".strip()


def _wait_role_expression(
    *,
    role: str,
    name: str | None,
    exact: bool,
    case_sensitive: bool,
    timeout_ms: float,
    poll_ms: float,
    include_hidden: bool,
) -> str:
    name_source = "null" if name is None else _js_literal(name)
    return f"""
() => new Promise((resolve) => {{
{_dom_helpers_expression(include_hidden=include_hidden)}
  const requestedRole = {_js_literal(role)};
  const requestedName = {name_source};
  const exact = {_js_literal(exact)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const startedAt = Date.now();
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  const candidates = () => [...document.querySelectorAll(interactiveSelector)].filter(visible);
  const check = () => {{
    const nodes = candidates();
    const roleMatches = nodes.filter((candidate) => roleOf(candidate) === requestedRole);
    const element = roleMatches.find((candidate) =>
      requestedName === null ||
      matchesText(accessibleName(candidate), requestedName, exact, caseSensitive)
    );
    const waitedMs = Date.now() - startedAt;
    if (element) {{
      resolve({{
        found: true,
        role: requestedRole,
        name: requestedName,
        include_hidden: includeHidden,
        waited_ms: waitedMs,
        timeout_ms: timeoutMs,
        poll_ms: pollMs,
        candidate_count: roleMatches.length,
        total_candidate_count: nodes.length,
        element: nodeInfo(element)
      }});
      return;
    }}
    if (waitedMs >= timeoutMs) {{
      resolve({{
        found: false,
        role: requestedRole,
        name: requestedName,
        include_hidden: includeHidden,
        waited_ms: waitedMs,
        timeout_ms: timeoutMs,
        poll_ms: pollMs,
        candidate_count: roleMatches.length,
        total_candidate_count: nodes.length,
        candidates: roleMatches.slice(0, 20).map(nodeInfo)
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
  const sensitiveAttributeName = (name) =>
    /api[-_]?key|apikey|authorization|bearer|credential|password|passwd|secret|token/i.test(String(name || ""));
  const valuePayload = () => {{
    if (!("value" in element) && !element.isContentEditable) {{
      return {{ value: null, value_masked: false, value_length: null }};
    }}
    const raw = readFormValue(element);
    if (sensitiveElement(element) && !revealSensitiveValues) {{
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
      (sensitiveElement(element) && attribute.name.toLowerCase() === "value")
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
        value: sensitiveElement(element) && option.value !== "" ? "***" : option.value,
        value_masked: sensitiveElement(element) && option.value !== "",
        text: textOf(option)
      }}))
    : null;
  const options = tag === "select"
    ? [...element.options].slice(0, 50).map((option) => ({{
        value: sensitiveElement(element) && option.value !== "" ? "***" : option.value,
        value_masked: sensitiveElement(element) && option.value !== "",
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
      const nodeSensitive = sensitiveElement(node);
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


def _wait_title_expression(
    *,
    title: str,
    match: str,
    case_sensitive: bool,
    timeout_ms: float,
    poll_ms: float,
) -> str:
    return f"""
() => new Promise((resolve) => {{
  const requestedTitle = {_js_literal(title)};
  const matchMode = {_js_literal(match)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const startedAt = Date.now();
  const timeoutMs = Math.max(0, {_js_literal(timeout_ms)});
  const pollMs = Math.max(25, {_js_literal(poll_ms)});
  let pattern = null;
  if (matchMode === "regex") {{
    try {{
      pattern = new RegExp(requestedTitle, caseSensitive ? "" : "i");
    }} catch (error) {{
      resolve({{
        found: false,
        title: document.title,
        requested_title: requestedTitle,
        match: matchMode,
        case_sensitive: caseSensitive,
        waited_ms: 0,
        error: "invalid_regex",
        message: String(error.message || error)
      }});
      return;
    }}
  }}
  const normalize = (value) => caseSensitive ? value : String(value).toLowerCase();
  const requestedComparable = normalize(requestedTitle);
  const matches = (candidate) => {{
    const comparable = normalize(candidate);
    if (matchMode === "exact") return comparable === requestedComparable;
    if (matchMode === "regex") return pattern.test(candidate);
    return comparable.includes(requestedComparable);
  }};
  const check = () => {{
    const currentTitle = document.title;
    const waitedMs = Date.now() - startedAt;
    if (matches(currentTitle)) {{
      resolve({{
        found: true,
        title: currentTitle,
        requested_title: requestedTitle,
        match: matchMode,
        case_sensitive: caseSensitive,
        waited_ms: waitedMs
      }});
      return;
    }}
    if (waitedMs >= timeoutMs) {{
      resolve({{
        found: false,
        title: currentTitle,
        requested_title: requestedTitle,
        match: matchMode,
        case_sensitive: caseSensitive,
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
  const hasPrintableValue = (value) => Array.isArray(value)
    ? value.some((item) => String(item ?? "") !== "")
    : String(value ?? "") !== "";
  const maskedPublicValue = (value) => Array.isArray(value)
    ? value.map((item) => String(item ?? "") === "" ? item : "***")
    : (String(value ?? "") === "" ? value : "***");
  const valueLength = (value) => Array.isArray(value)
    ? value.map((item) => String(item ?? "").length)
    : String(value ?? "").length;
  const publicValue = (node, state) => {
    const masked = sensitiveElement(node) && hasPrintableValue(state.value);
    if (!masked) {
      return { ...state, value_masked: false };
    }
    const selectedOptions = Array.isArray(state.selected_options)
      ? state.selected_options.map((option) => ({
          ...option,
          value: String(option.value ?? "") === "" ? option.value : "***",
          value_masked: String(option.value ?? "") !== ""
        }))
      : state.selected_options;
    return {
      ...state,
      value: maskedPublicValue(state.value),
      selected_options: selectedOptions,
      value_masked: true,
      value_length: valueLength(state.value)
    };
  };
  const publicRequestedValue = (node, value) => {
    const masked = shouldMaskValue(node, value);
    return {
      value: masked ? "***" : value,
      value_masked: masked,
      value_length: masked ? String(value ?? "").length : null
    };
  };
  const publicSelectorRequestedValue = (selector, value) => {
    const masked = sensitiveText(selector) && String(value ?? "") !== "";
    return {
      value: masked ? "***" : value,
      value_masked: masked,
      value_length: masked ? String(value ?? "").length : null
    };
  };
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
    ...publicValue(element, readFormValue(element)),
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
  const selectorRequestedValue = publicSelectorRequestedValue(selector, requestedValue);
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
        requested_value: selectorRequestedValue.value,
        requested_value_masked: selectorRequestedValue.value_masked,
        requested_value_length: selectorRequestedValue.value_length,
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
          requested_value: selectorRequestedValue.value,
          requested_value_masked: selectorRequestedValue.value_masked,
          requested_value_length: selectorRequestedValue.value_length,
          match: matchMode,
          waited_ms: waitedMs
        }});
        return;
      }}
      setTimeout(check, pollMs);
      return;
    }}
    const state = readFormValue(element);
    const outputState = publicValue(element, state);
    const outputRequestedValue = publicRequestedValue(element, requestedValue);
    if (!state.readable) {{
      resolve({{
        selector,
        found: false,
        selector_found: true,
        ...outputState,
        requested_value: outputRequestedValue.value,
        requested_value_masked: outputRequestedValue.value_masked,
        requested_value_length: outputRequestedValue.value_length,
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
        ...outputState,
        requested_value: outputRequestedValue.value,
        requested_value_masked: outputRequestedValue.value_masked,
        requested_value_length: outputRequestedValue.value_length,
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
        ...outputState,
        requested_value: outputRequestedValue.value,
        requested_value_masked: outputRequestedValue.value_masked,
        requested_value_length: outputRequestedValue.value_length,
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
        _sensitive_value_helpers_expression()
        + "\n"
        + """
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
      previous_value: maskValue(element, previousValue),
      previous_value_masked: shouldMaskValue(element, previousValue),
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
    previous_value: maskValue(element, previousValue),
    previous_value_masked: shouldMaskValue(element, previousValue),
    value: maskValue(element, value),
    value_masked: shouldMaskValue(element, value),
    value_length: shouldMaskValue(element, value) ? String(value ?? "").length : null
  };
""".rstrip(),
    )


def _set_value_expression(selector: str, value: str, *, dispatch_events: bool) -> str:
    return _event_expression(
        selector,
        _sensitive_value_helpers_expression()
        + "\n"
        + f"""
  const requestedValue = {_js_literal(value)};
  const selectorRequestedValue = publicSelectorRequestedValue(selector, requestedValue);
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
      previous_value: maskValue(element, previousValue),
      previous_value_masked: shouldMaskValue(element, previousValue),
      value: null,
      requested_value: selectorRequestedValue.value,
      requested_value_masked: selectorRequestedValue.value_masked,
      requested_value_length: selectorRequestedValue.value_length,
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
  const outputRequestedValue = publicRequestedValue(element, requestedValue);
  return {{
    selector,
    found: true,
    writable: true,
    set: currentValue === requestedValue,
    previous_value: maskValue(element, previousValue),
    previous_value_masked: shouldMaskValue(element, previousValue),
    value: maskValue(element, currentValue),
    value_masked: shouldMaskValue(element, currentValue),
    value_length: shouldMaskValue(element, currentValue)
      ? String(currentValue ?? "").length
      : null,
    requested_value: outputRequestedValue.value,
    requested_value_masked: outputRequestedValue.value_masked,
    requested_value_length: outputRequestedValue.value_length,
    dispatched_events: dispatchedEvents
  }};
""".rstrip(),
    )


def _set_file_input_expression(
    *,
    selector: str,
    files: list[dict[str, Any]],
    dispatch_events: bool,
) -> str:
    file_payloads = [
        {
            "name": file["name"],
            "type": file["type"],
            "size": file["size"],
            "last_modified": file["last_modified"],
            "data_base64": file["data_base64"],
        }
        for file in files
    ]
    return f"""
() => {{
{_dom_helpers_expression()}
  const selector = {_js_literal(selector)};
  const requestedFiles = {_js_literal(file_payloads)};
  const dispatchEvents = {_js_literal(dispatch_events)};
  const element = document.querySelector(selector);
  const publicFileInfo = (file) => ({{
    name: file.name,
    type: file.type,
    size: file.size,
    last_modified: file.lastModified ?? null
  }});
  const requestedFileInfo = requestedFiles.map((file) => ({{
    name: file.name,
    type: file.type,
    size: file.size,
    last_modified: file.last_modified
  }}));
  if (!element) {{
    return {{
      selector,
      found: false,
      file_input: false,
      set: false,
      requested_count: requestedFiles.length,
      requested_files: requestedFileInfo,
      files: []
    }};
  }}
  const fileInput = element.tagName.toLowerCase() === "input" &&
    String(element.type || "").toLowerCase() === "file";
  if (!fileInput) {{
    return {{
      selector,
      found: true,
      file_input: false,
      set: false,
      requested_count: requestedFiles.length,
      requested_files: requestedFileInfo,
      files: [],
      element: nodeInfo(element)
    }};
  }}
  if (requestedFiles.length > 1 && !element.multiple) {{
    return {{
      selector,
      found: true,
      file_input: true,
      set: false,
      multiple: Boolean(element.multiple),
      requested_count: requestedFiles.length,
      requested_files: requestedFileInfo,
      previous_count: element.files?.length ?? 0,
      file_count: element.files?.length ?? 0,
      files: [...(element.files || [])].map(publicFileInfo),
      error: "multiple_not_allowed",
      message: "Input does not allow multiple files."
    }};
  }}
  if (typeof DataTransfer !== "function" || typeof File !== "function") {{
    return {{
      selector,
      found: true,
      file_input: true,
      set: false,
      multiple: Boolean(element.multiple),
      requested_count: requestedFiles.length,
      requested_files: requestedFileInfo,
      previous_count: element.files?.length ?? 0,
      file_count: element.files?.length ?? 0,
      files: [...(element.files || [])].map(publicFileInfo),
      error: "file_api_unavailable",
      message: "DataTransfer or File constructor is unavailable in this page."
    }};
  }}
  const decodeBase64 = (value) => {{
    const binary = atob(value);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {{
      bytes[index] = binary.charCodeAt(index);
    }}
    return bytes;
  }};
  const previousCount = element.files?.length ?? 0;
  try {{
    const transfer = new DataTransfer();
    for (const payload of requestedFiles) {{
      const file = new File(
        [decodeBase64(payload.data_base64)],
        payload.name,
        {{
          type: payload.type || "application/octet-stream",
          lastModified: payload.last_modified || Date.now()
        }}
      );
      transfer.items.add(file);
    }}
    element.files = transfer.files;
  }} catch (error) {{
    return {{
      selector,
      found: true,
      file_input: true,
      set: false,
      multiple: Boolean(element.multiple),
      requested_count: requestedFiles.length,
      requested_files: requestedFileInfo,
      previous_count: previousCount,
      file_count: element.files?.length ?? 0,
      files: [...(element.files || [])].map(publicFileInfo),
      error: String(error.name || "Error"),
      message: String(error.message || error)
    }};
  }}
  const dispatchedEvents = [];
  if (dispatchEvents) {{
    for (const type of ["input", "change"]) {{
      element.dispatchEvent(new Event(type, {{ bubbles: true }}));
      dispatchedEvents.push(type);
    }}
  }}
  const files = [...(element.files || [])].map(publicFileInfo);
  const set = files.length === requestedFiles.length &&
    files.every((file, index) =>
      file.name === requestedFiles[index].name &&
      file.size === requestedFiles[index].size &&
      file.type === (requestedFiles[index].type || "")
    );
  return {{
    selector,
    found: true,
    file_input: true,
    set,
    multiple: Boolean(element.multiple),
    requested_count: requestedFiles.length,
    requested_files: requestedFileInfo,
    previous_count: previousCount,
    file_count: files.length,
    files,
    value: element.value ? "***" : "",
    value_masked: Boolean(element.value),
    dispatched_events: dispatchedEvents,
    element: nodeInfo(element)
  }};
}}
""".strip()


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


def cmd_action_page_info(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.page-info",
        _page_info_expression(),
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


def cmd_action_wait_title(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.wait-title",
        _wait_title_expression(
            title=args.title,
            match=args.match,
            case_sensitive=args.case_sensitive,
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
            state=args.state,
            exact=args.exact,
            case_sensitive=args.case_sensitive,
            timeout_ms=args.timeout_ms,
            poll_ms=args.poll_ms,
            include_hidden=args.include_hidden,
        ),
    )


def cmd_action_wait_role(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.wait-role",
        _wait_role_expression(
            role=args.role,
            name=args.name,
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


def cmd_action_set_file_input(args: argparse.Namespace) -> None:
    command = "action.set-file-input"
    files = _read_file_input_payloads(
        command=command,
        files=args.file,
        max_bytes=args.max_bytes,
    )
    _run_eval_backed_action_command(
        args,
        command,
        _set_file_input_expression(
            selector=args.selector,
            files=files,
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


def _press_key_expression(
    *,
    key: str,
    code: str | None,
    alt_key: bool,
    ctrl_key: bool,
    meta_key: bool,
    shift_key: bool,
) -> str:
    key_literal = _js_literal(key)
    code_literal = _js_literal(code or key)
    return f"""
() => {{
  const key = {key_literal};
  const code = {code_literal};
  const modifiers = {{
    alt_key: {_js_literal(alt_key)},
    ctrl_key: {_js_literal(ctrl_key)},
    meta_key: {_js_literal(meta_key)},
    shift_key: {_js_literal(shift_key)}
  }};
  const activeElement = document.activeElement;
  let target = activeElement || document.body || document.documentElement || window;
  let targetKind = "active_element";
  if (target === document.body) {{
    targetKind = "body";
  }} else if (target === document.documentElement) {{
    target = document.body || document.documentElement || window;
    targetKind = target === window ? "window" : (
      target === document.body ? "body" : "document_element"
    );
  }} else if (target === window) {{
    targetKind = "window";
  }}
  const describeElement = (element) => {{
    if (!element || !(element instanceof Element)) {{
      return null;
    }}
    return {{
      tag_name: element.tagName.toLowerCase(),
      id: element.id || null,
      name: element.getAttribute("name") || null,
      role: element.getAttribute("role") || null,
      type: element.getAttribute("type") || null,
      contenteditable: element.getAttribute("contenteditable") || null
    }};
  }};
  const init = {{
    key,
    code,
    altKey: modifiers.alt_key,
    ctrlKey: modifiers.ctrl_key,
    metaKey: modifiers.meta_key,
    shiftKey: modifiers.shift_key,
    bubbles: true,
    cancelable: true
  }};
  const events = [];
  for (const type of ["keydown", "keypress", "keyup"]) {{
    try {{
      events.push({{
        type,
        accepted: target.dispatchEvent(new KeyboardEvent(type, init))
      }});
    }} catch (error) {{
      events.push({{
        type,
        accepted: false,
        error: String(error.message || error)
      }});
    }}
  }}
  return {{
    key,
    code,
    pressed: events.every((event) => !event.error),
    target: targetKind,
    target_info: describeElement(target),
    modifiers,
    events,
    keydown_accepted: events[0] ? Boolean(events[0].accepted) : null
  }};
}}
""".strip()


def cmd_action_press_key(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.press-key",
        _press_key_expression(
            key=args.key,
            code=args.code,
            alt_key=args.alt_key,
            ctrl_key=args.ctrl_key,
            meta_key=args.meta_key,
            shift_key=args.shift_key,
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


def cmd_action_link_snapshot(args: argparse.Namespace) -> None:
    expression = _link_snapshot_expression(
        selector=args.selector,
        include_hidden=args.include_hidden,
        max_nodes=args.max_nodes,
        include_empty=args.include_empty,
        same_origin_only=args.same_origin_only,
    )
    _run_eval_backed_action_command(args, "action.link-snapshot", expression)


def cmd_action_table_snapshot(args: argparse.Namespace) -> None:
    expression = _table_snapshot_expression(
        selector=args.selector,
        include_hidden=args.include_hidden,
        max_nodes=args.max_nodes,
        max_rows=args.max_rows,
        max_cells=args.max_cells,
    )
    _run_eval_backed_action_command(args, "action.table-snapshot", expression)


def cmd_action_list_snapshot(args: argparse.Namespace) -> None:
    expression = _list_snapshot_expression(
        selector=args.selector,
        include_hidden=args.include_hidden,
        max_nodes=args.max_nodes,
        max_items=args.max_items,
    )
    _run_eval_backed_action_command(args, "action.list-snapshot", expression)


def cmd_action_text_snapshot(args: argparse.Namespace) -> None:
    expression = _text_snapshot_expression(
        selector=args.selector,
        include_hidden=args.include_hidden,
        max_nodes=args.max_nodes,
        max_chars=args.max_chars,
    )
    _run_eval_backed_action_command(args, "action.text-snapshot", expression)


def cmd_action_dialog_snapshot(args: argparse.Namespace) -> None:
    expression = _dialog_snapshot_expression(
        selector=args.selector,
        include_hidden=args.include_hidden,
        max_nodes=args.max_nodes,
        max_controls=args.max_controls,
        max_chars=args.max_chars,
    )
    _run_eval_backed_action_command(args, "action.dialog-snapshot", expression)


def cmd_action_wait_dialog(args: argparse.Namespace) -> None:
    expression = _wait_dialog_expression(
        selector=args.selector,
        include_hidden=args.include_hidden,
        max_nodes=args.max_nodes,
        max_controls=args.max_controls,
        max_chars=args.max_chars,
        text=args.text,
        match=args.match,
        case_sensitive=args.case_sensitive,
        modal_only=args.modal_only,
        timeout_ms=args.timeout_ms,
        poll_ms=args.poll_ms,
    )
    _run_eval_backed_action_command(args, "action.wait-dialog", expression)


def cmd_action_frame_snapshot(args: argparse.Namespace) -> None:
    expression = _frame_snapshot_expression(
        selector=args.selector,
        include_hidden=args.include_hidden,
        max_nodes=args.max_nodes,
        max_chars=args.max_chars,
    )
    _run_eval_backed_action_command(args, "action.frame-snapshot", expression)


def cmd_action_wait_frame(args: argparse.Namespace) -> None:
    expression = _wait_frame_expression(
        selector=args.selector,
        include_hidden=args.include_hidden,
        max_nodes=args.max_nodes,
        max_chars=args.max_chars,
        url=args.url,
        url_match=args.url_match,
        text=args.text,
        text_match=args.text_match,
        case_sensitive=args.case_sensitive,
        readable_only=args.readable_only,
        same_origin_only=args.same_origin_only,
        timeout_ms=args.timeout_ms,
        poll_ms=args.poll_ms,
    )
    _run_eval_backed_action_command(args, "action.wait-frame", expression)


def cmd_action_performance_snapshot(args: argparse.Namespace) -> None:
    expression = _performance_snapshot_expression(
        max_resources=args.max_resources,
        initiator_type=args.initiator_type,
        min_duration_ms=args.min_duration_ms,
    )
    _run_eval_backed_action_command(args, "action.performance-snapshot", expression)


def cmd_action_network_snapshot(args: argparse.Namespace) -> None:
    expression = _network_snapshot_expression(
        max_entries=args.max_entries,
        clear=args.clear,
        install_only=args.install_only,
        source=args.source,
        method=args.method,
        failed_only=args.failed_only,
    )
    _run_eval_backed_action_command(args, "action.network-snapshot", expression)


def cmd_action_wait_network(args: argparse.Namespace) -> None:
    expression = _wait_network_expression(
        url=args.url,
        url_match=args.url_match,
        source=args.source,
        method=args.method,
        status=args.status,
        failed_only=args.failed_only,
        case_sensitive=args.case_sensitive,
        after_index=args.after_index,
        timeout_ms=args.timeout_ms,
        poll_ms=args.poll_ms,
    )
    _run_eval_backed_action_command(args, "action.wait-network", expression)


def cmd_action_console_snapshot(args: argparse.Namespace) -> None:
    expression = _console_snapshot_expression(
        max_entries=args.max_entries,
        clear=args.clear,
        install_only=args.install_only,
    )
    _run_eval_backed_action_command(args, "action.console-snapshot", expression)


def cmd_action_wait_console(args: argparse.Namespace) -> None:
    expression = _wait_console_expression(
        text=args.text,
        match=args.match,
        source=args.source,
        level=args.level,
        case_sensitive=args.case_sensitive,
        after_index=args.after_index,
        timeout_ms=args.timeout_ms,
        poll_ms=args.poll_ms,
    )
    _run_eval_backed_action_command(args, "action.wait-console", expression)


def cmd_action_outline_snapshot(args: argparse.Namespace) -> None:
    expression = _outline_snapshot_expression(
        selector=args.selector,
        include_hidden=args.include_hidden,
        max_nodes=args.max_nodes,
    )
    _run_eval_backed_action_command(args, "action.outline-snapshot", expression)


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
    _run_eval_backed_action_command(
        args,
        getattr(args, "action_command_name", "action.interactive-snapshot"),
        expression,
    )


def cmd_doctor(args: argparse.Namespace) -> None:
    command = "doctor"
    checks: list[dict[str, Any]] = []

    checks.append(
        _doctor_check(
            "python_runtime",
            "pass",
            "Python runtime is available",
            executable=sys.executable,
            version=sys.version.split()[0],
            platform=sys.platform,
        )
    )

    browser_cli_path = shutil.which("browser-cli")
    if browser_cli_path:
        checks.append(
            _doctor_check(
                "browser_cli_executable",
                "pass",
                "browser-cli executable is available on PATH",
                path=browser_cli_path,
            )
        )
    else:
        checks.append(
            _doctor_check(
                "browser_cli_executable",
                "warn",
                (
                    "browser-cli executable was not found on PATH; the current "
                    "process is running, but future shell commands may fail."
                ),
                fix=_doctor_fix(
                    "install_browser_cli_on_path",
                    commands=[
                        "uv tool install git+https://github.com/lexmount/browser-cli.git",
                        "browser-cli --help",
                        "browser-cli --version",
                        "browser-cli doctor",
                    ],
                    guidance=[
                        "Install browser-cli as a uv tool or add its executable directory to PATH.",
                        "In local development, use `uv run browser-cli ...` from the repository.",
                    ],
                ),
            )
        )

    browser_cli_version, browser_cli_version_source = _browser_cli_version()
    checks.append(
        _doctor_check(
            "browser_cli",
            "pass",
            "browser-cli import succeeded",
            version=browser_cli_version,
            version_known=True,
            version_source=browser_cli_version_source,
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

    checks.append(_doctor_command_catalog_check())

    api_key = os.environ.get("LEXMOUNT_API_KEY")
    project_id = os.environ.get("LEXMOUNT_PROJECT_ID")
    base_url = os.environ.get("LEXMOUNT_BASE_URL")
    region = os.environ.get("LEXMOUNT_REGION")
    env_configured = bool(api_key and project_id)
    api_admin: Any | None = None
    device_token_status = _local_device_token_status(
        getattr(args, "credentials_file", None)
    )
    auth_source = _auth_source(
        env_configured=env_configured,
        device_token_status=device_token_status,
    )

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
    if device_token_status.get("present"):
        device_token_check_status = (
            "pass"
            if device_token_status.get("valid")
            and not device_token_status.get("warnings")
            else "warn"
        )
        checks.append(
            _doctor_check(
                "local_device_token",
                device_token_check_status,
                (
                    "Local device token metadata is valid but bearer-token runtime auth is pending."
                    if device_token_check_status == "pass"
                    else "Local device token metadata needs attention."
                ),
                device_token=device_token_status,
                fix=_doctor_fix(
                    "use_env_credentials_until_bearer_auth_lands",
                    commands=[
                        "browser-cli auth status",
                        "browser-cli auth login",
                        "browser-cli auth export-env",
                    ],
                    guidance=[
                        "Device-token status is reported for forward compatibility.",
                        "Use LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID for browser actions until bearer-token runtime support is enabled.",
                    ],
                ),
            )
        )
    elif getattr(args, "credentials_file", None):
        checks.append(
            _doctor_check(
                "local_device_token",
                "warn",
                "Requested local device token credentials file was not found.",
                device_token=device_token_status,
                fix=_doctor_fix(
                    "run_auth_login_or_use_env_credentials",
                    commands=[
                        "browser-cli auth login",
                        "browser-cli auth export-env",
                    ],
                    guidance=[
                        "Use env API-key credentials today, or rerun once device-code login is available."
                    ],
                ),
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
            api_admin = LexmountBrowserAdmin()
            result = api_admin.list_sessions(status=None)
        except Exception as exc:
            checks.append(
                _doctor_check(
                    "api_connectivity",
                    "fail",
                    _mask_sensitive_text(str(exc)),
                    error=_doctor_error_name(exc),
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

    api_connectivity = next(
        (check for check in checks if check.get("name") == "api_connectivity"),
        None,
    )
    if args.smoke_session:
        if args.skip_api:
            checks.append(
                _doctor_check(
                    "browser_smoke_session",
                    "skipped",
                    "Browser smoke session skipped by --skip-api",
                    fix=_doctor_fix(
                        "run_browser_smoke_session",
                        commands=["browser-cli doctor --smoke-session"],
                        guidance=[
                            "Rerun doctor with --smoke-session and without --skip-api when live browser session creation can be tested."
                        ],
                    ),
                )
            )
        elif not api_key or not project_id:
            checks.append(
                _doctor_check(
                    "browser_smoke_session",
                    "skipped",
                    "Browser smoke session requires LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID",
                    fix=_credential_doctor_fix(
                        "LEXMOUNT_API_KEY",
                        "LEXMOUNT_PROJECT_ID",
                    ),
                )
            )
        elif not (
            isinstance(api_connectivity, dict)
            and api_connectivity.get("status") == "pass"
            and api_admin is not None
        ):
            checks.append(
                _doctor_check(
                    "browser_smoke_session",
                    "skipped",
                    "Browser smoke session requires a passing API connectivity check",
                    fix=_doctor_fix(
                        "fix_api_connectivity_before_smoke_session",
                        commands=[
                            "browser-cli auth status",
                            "browser-cli doctor",
                            "browser-cli doctor --smoke-session",
                        ],
                        guidance=[
                            "Repair API connectivity before validating browser session creation."
                        ],
                    ),
                )
            )
        else:
            checks.append(_doctor_smoke_session_check(api_admin))

    failed = [check for check in checks if check["status"] == "fail"]
    warnings = [check for check in checks if check["status"] == "warn"]
    api_connectivity = next(
        (check for check in checks if check.get("name") == "api_connectivity"),
        None,
    )
    data: dict[str, Any] = {
        "ok": not failed,
        "command": command,
        "status": "error" if failed else "warning" if warnings else "ok",
        "checked": len(checks),
        "failed": len(failed),
        "warnings": len(warnings),
        "failed_checks": _doctor_check_names(checks, status="fail"),
        "warning_checks": _doctor_check_names(checks, status="warn"),
        "skipped_checks": _doctor_check_names(checks, status="skipped"),
        "auth_source": auth_source,
        "runtime_auth_usable": env_configured,
        "device_token": device_token_status,
        "ready_for_browser_actions": (
            not failed
            and isinstance(api_connectivity, dict)
            and api_connectivity.get("status") == "pass"
        ),
        "repair_plan": _doctor_repair_plan(checks),
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
    missing_env = [
        name
        for name, value in (
            ("LEXMOUNT_API_KEY", api_key),
            ("LEXMOUNT_PROJECT_ID", project_id),
        )
        if not value
    ]
    device_token_status = _local_device_token_status(args.credentials_file)
    auth_source = _auth_source(
        env_configured=configured,
        device_token_status=device_token_status,
    )

    payload: dict[str, Any] = {
        "configured": configured,
        "auth_source": auth_source,
        "runtime_auth_usable": configured,
        "missing_env": missing_env,
        "api_key": _env_value_status("LEXMOUNT_API_KEY", secret=True),
        "project_id": _env_value_status("LEXMOUNT_PROJECT_ID"),
        "base_url": _env_value_status(
            "LEXMOUNT_BASE_URL",
            default=DEFAULT_LEXMOUNT_BASE_URL,
        ),
        "region": _env_value_status("LEXMOUNT_REGION"),
        "device_token": device_token_status,
        "next_steps": _auth_next_steps(
            configured=configured,
            device_token_status=device_token_status,
        ),
    }
    if missing_env:
        payload["fix"] = _credential_doctor_fix(*missing_env)
    _success(command, **payload)


def cmd_auth_token_info(args: argparse.Namespace) -> None:
    command = "auth.token-info"
    device_token_status = _local_device_token_status(args.credentials_file)
    scope_check = _device_token_scope_check(
        device_token_status,
        args.required_scope,
    )

    _success(
        command,
        present=device_token_status.get("present", False),
        valid=device_token_status.get("valid", False),
        expired=device_token_status.get("expired"),
        refresh_needed=device_token_status.get("refresh_needed"),
        runtime_auth_usable=False,
        device_token=device_token_status,
        scope_check=scope_check,
        next_steps=_auth_token_info_next_steps(
            device_token_status=device_token_status,
            scope_check=scope_check,
        ),
    )


def cmd_auth_refresh(args: argparse.Namespace) -> None:
    command = "auth.refresh"
    device_token_status = _local_device_token_status(args.credentials_file)
    reason = _auth_refresh_reason(device_token_status, force=bool(args.force))
    warnings = list(device_token_status.get("warnings", []))
    if reason == "remote_refresh_unavailable":
        warnings.append(
            "Remote token refresh is not implemented yet; request fresh credentials from browser.lexmount.cn when device-code login is available."
        )

    _success(
        command,
        credentials_file=device_token_status.get("path"),
        path_source=device_token_status.get("path_source"),
        present=device_token_status.get("present", False),
        valid=device_token_status.get("valid", False),
        expired=device_token_status.get("expired"),
        refresh_needed=device_token_status.get("refresh_needed"),
        has_refresh_token=device_token_status.get("has_refresh_token", False),
        force_requested=bool(args.force),
        refresh_requested=reason != "refresh_not_needed" or bool(args.force),
        refresh_available=False,
        refreshed=False,
        reason=reason,
        runtime_auth_usable=False,
        warnings=warnings,
        device_token=device_token_status,
        next_steps=_auth_refresh_next_steps(
            reason=reason,
            device_token_status=device_token_status,
        ),
    )


def cmd_auth_logout(args: argparse.Namespace) -> None:
    command = "auth.logout"
    path, path_source = _device_token_credentials_path(args.credentials_file)
    device_token_before = _local_device_token_status(args.credentials_file)
    present_before = bool(device_token_before.get("present"))
    warnings: list[str] = []
    deleted = False

    if args.revoke:
        warnings.append(
            "Remote token revoke is not implemented yet; remove local metadata and revoke from browser.lexmount.cn if needed."
        )

    if path.exists():
        if not path.is_file():
            _failure(
                command,
                "invalid_credentials_path",
                "Credentials path exists but is not a file.",
                exit_code=1,
                credentials_file=str(path),
            )
        try:
            path.unlink()
        except OSError as exc:
            _failure(
                command,
                "credential_delete_error",
                str(exc),
                exit_code=1,
                credentials_file=str(path),
            )
        deleted = True

    present_after = path.exists()
    _success(
        command,
        credentials_file=str(path),
        path_source=path_source,
        present_before=present_before,
        present_after=present_after,
        deleted=deleted,
        env_unchanged=True,
        revoke_requested=bool(args.revoke),
        revoke_available=False,
        warnings=warnings,
        device_token_before=device_token_before,
        next_steps=_auth_logout_next_steps(
            deleted=deleted,
            revoke_requested=bool(args.revoke),
        ),
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
    unusable_exports = [
        str(entry["name"]) for entry in entries if not bool(entry.get("usable"))
    ]
    next_steps = [
        "Run the export commands in the local shell."
        if not unusable_exports
        else "Replace placeholder or redacted export values before running the commands in the local shell.",
        f"Run `{AGENT_DOCTOR_COMMAND}` to verify credentials.",
    ]
    _success(
        command,
        shell=args.shell,
        from_current=args.from_current,
        secrets_revealed=secrets_revealed,
        usable=not unusable_exports,
        unusable_exports=unusable_exports,
        warnings=warnings,
        exports=entries,
        commands=commands,
        script="\n".join(commands),
        next_steps=next_steps,
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
    device_connect_url = _connect_from_codex_url(
        project_id=project_id,
        scopes=scopes,
        expires_in=args.expires_in,
        response="device_code",
    )
    handoff = _auth_login_handoff(
        connect_url=connect_url,
        project_id=project_id,
        project_id_source=project_id_source,
        scopes=scopes,
        expires_in=args.expires_in,
    )
    site_capabilities = _connect_from_codex_site_capabilities()
    site_capability_status = _connect_from_codex_site_capability_status(
        site_capabilities
    )
    scope_details = _scope_details(scopes)
    setup_blocks = handoff["setup_blocks"]
    open_url = device_connect_url if args.device_code else connect_url
    open_result: dict[str, Any] = {
        "requested": bool(args.open),
        "url": open_url,
        "opened": False,
    }
    warnings: list[str] = []
    if args.open:
        try:
            open_result["opened"] = bool(webbrowser.open(open_url))
        except Exception as exc:
            open_result["error"] = _mask_sensitive_text(str(exc))
            warnings.append(
                "Failed to open the Connect from Codex URL automatically; copy the URL manually."
            )
        else:
            if not open_result["opened"]:
                warnings.append(
                    "The system browser did not confirm opening the Connect from Codex URL; copy the URL manually."
                )
    if args.device_code:
        warnings.append(
            "Device-code login is not available yet; use the manual_env fallback until browser.lexmount.cn exposes device-code endpoints."
        )
        _success(
            command,
            flow="device_code",
            selected_flow="device_code",
            available=False,
            manual_env_available=True,
            login_url=LEXMOUNT_CONSOLE_URL,
            device_code_available=False,
            reason="browser_site_endpoint_missing",
            device_code={
                "available": False,
                "reason": "browser_site_endpoint_missing",
                "verification_uri": LEXMOUNT_CODEX_CONNECT_URL,
                "connect_from_codex_url": device_connect_url,
                "project_id": project_id,
                "project_id_source": project_id_source,
                "requested_scopes": scopes,
                "requested_scope_details": scope_details,
                "requested_expires_in": args.expires_in,
                "required_endpoints": [
                    "POST /api/auth/device/code",
                    "POST /api/auth/device/token",
                ],
                "required_browser_site_support": [
                    "Show user_code approval UI on /connect/codex.",
                    "Issue scoped, project-bound, short-lived access tokens.",
                    "Issue refresh tokens with revoke and expiration metadata.",
                    "Expose token refresh and revoke endpoints for browser-cli.",
                    "Enable browser runtime bearer-token authentication.",
                ],
            },
            handoff=handoff,
            open_result=open_result,
            warnings=warnings,
            fallback_flow="manual_env",
            fallback_handoff=handoff,
            connect_from_codex={
                "available": False,
                "url": device_connect_url,
                "project_id": project_id,
                "project_id_source": project_id_source,
                "requested_scopes": scopes,
                "requested_scope_details": scope_details,
                "requested_expires_in": args.expires_in,
                "response": "device_code",
                "setup_blocks": setup_blocks,
                "site_capability_status": site_capability_status,
                "site_capabilities": site_capabilities,
                "fallback": "Use the manual_env steps until browser.lexmount.cn supports device-code login.",
            },
            flows=[
                {
                    "name": "device_code",
                    "available": False,
                    "reason": "browser_site_endpoint_missing",
                    "description": "Planned browser approval flow for scoped local credentials.",
                },
                {
                    "name": "manual_env",
                    "available": True,
                    "description": "User copies Project ID and API key from browser.lexmount.cn into the local shell.",
                },
            ],
            message=(
                "Device-code login is not available yet. Use the returned "
                "manual_env fallback handoff until browser.lexmount.cn exposes "
                "device-code endpoints."
            ),
            next_steps=[
                "Use `browser-cli auth login` or `browser-cli auth login --open` for the manual Connect from Codex handoff today.",
                "Set LEXMOUNT_API_KEY and LEXMOUNT_PROJECT_ID in the local shell.",
                f"Run `{AGENT_DOCTOR_COMMAND}` to verify the setup.",
                "Implement browser.lexmount.cn device-code endpoints before treating this flow as available.",
            ],
        )
        return
    _success(
        command,
        flow="manual_env",
        selected_flow="manual_env",
        available=True,
        manual_env_available=True,
        login_url=LEXMOUNT_CONSOLE_URL,
        device_code_available=False,
        handoff=handoff,
        open_result=open_result,
        warnings=warnings,
        connect_from_codex={
            "available": False,
            "url": connect_url,
            "project_id": project_id,
            "project_id_source": project_id_source,
            "requested_scopes": scopes,
            "requested_scope_details": scope_details,
            "requested_expires_in": args.expires_in,
            "setup_blocks": setup_blocks,
            "site_capability_status": site_capability_status,
            "site_capabilities": site_capabilities,
            "expected_outputs": [
                "Project ID for the selected project",
                "Scoped API key or short-lived local token",
                "Copyable shell export commands",
                f"`{AGENT_DOCTOR_COMMAND}` verification guidance",
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
            f"Run `{AGENT_DOCTOR_COMMAND}` to verify the setup.",
        ],
        commands=[
            "browser-cli auth export-env",
            "browser-cli auth status",
            AGENT_DOCTOR_COMMAND,
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


def cmd_commands(args: argparse.Namespace) -> None:
    command = "commands"
    catalog = _command_catalog()
    commands = catalog["commands"]
    if args.group:
        available_groups = [str(group) for group in catalog["groups"]]
        if str(args.group) not in available_groups:
            _failure(
                command,
                "unknown_group",
                f"Unknown command group: {args.group}",
                group=args.group,
                available_groups=available_groups,
                fix=_doctor_fix(
                    "inspect_available_command_groups",
                    commands=[
                        "browser-cli commands",
                        "browser-cli commands --names-only",
                    ],
                    guidance=[
                        "Choose one of available_groups, then rerun commands with that --group value."
                    ],
                ),
            )
        commands = [
            item for item in commands if str(item.get("group")) == str(args.group)
        ]
        catalog["groups"] = _dedupe_preserving_order(
            [str(item["group"]) for item in commands]
        )
    catalog["commands"] = commands
    catalog["command_count"] = len(commands)

    if args.workflow:
        workflow = catalog["agent_workflows"].get(args.workflow)
        if workflow is None:
            _failure(
                command,
                "unknown_workflow",
                f"Unknown agent workflow: {args.workflow}",
                workflow=args.workflow,
                available_workflows=sorted(catalog["agent_workflows"]),
                fix=_doctor_fix(
                    "inspect_available_agent_workflows",
                    commands=["browser-cli commands --workflows-only"],
                    guidance=[
                        "Choose one of available_workflows, then rerun commands with that --workflow value."
                    ],
                ),
            )
        _success(
            command,
            schema_version=catalog["schema_version"],
            group=args.group,
            workflow_id=args.workflow,
            workflow=workflow,
            agent_entrypoints=catalog["agent_entrypoints"],
            json_output=catalog["json_output"],
            secret_policy=catalog["secret_policy"],
        )

    if args.workflows_only:
        _success(
            command,
            schema_version=catalog["schema_version"],
            group=args.group,
            workflow_count=len(catalog["agent_workflows"]),
            agent_workflows=catalog["agent_workflows"],
            agent_entrypoints=catalog["agent_entrypoints"],
            json_output=catalog["json_output"],
            secret_policy=catalog["secret_policy"],
        )

    if args.names_only:
        _success(
            command,
            schema_version=catalog["schema_version"],
            group=args.group,
            command_count=len(commands),
            commands=[str(item["name"]) for item in commands],
        )

    _success(command, group=args.group, **catalog)


def cmd_version(args: argparse.Namespace) -> None:
    cli_version, version_source = _browser_cli_version()
    runtime_version = _package_version("lex-browser-runtime")
    _success(
        "version",
        package="browser-cli",
        version=cli_version,
        version_source=version_source,
        lex_browser_runtime_version=runtime_version or "unknown",
        lex_browser_runtime_version_known=runtime_version is not None,
        python_version=sys.version.split()[0],
        executable=sys.executable,
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
    context_pick.add_argument(
        "--dry-run",
        action="store_true",
        help="Only inspect candidates and report whether a context would be selected or created.",
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

    action_page_info = action_subparsers.add_parser(
        "page-info",
        help="Read page URL, title, ready state, viewport, and text/html lengths",
    )
    _add_session_target_args(action_page_info)
    action_page_info.set_defaults(func=cmd_action_page_info)

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

    action_wait_title = action_subparsers.add_parser(
        "wait-title",
        help="Wait until document.title matches text or a regex",
    )
    _add_session_target_args(action_wait_title)
    action_wait_title.add_argument("--title", required=True)
    action_wait_title.add_argument(
        "--match",
        choices=["contains", "exact", "regex"],
        default="contains",
    )
    action_wait_title.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_title.add_argument("--poll-ms", type=float, default=250)
    action_wait_title.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Match the title case-sensitively.",
    )
    action_wait_title.set_defaults(func=cmd_action_wait_title)

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
        "--state",
        choices=["present", "absent"],
        default="present",
        help="Text state to wait for.",
    )
    action_wait_text.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden DOM nodes while waiting.",
    )
    _add_text_match_args(action_wait_text)
    action_wait_text.set_defaults(func=cmd_action_wait_text)

    action_wait_role = action_subparsers.add_parser(
        "wait-role",
        help="Wait until an interactive element with role and optional name appears",
    )
    _add_session_target_args(action_wait_role)
    action_wait_role.add_argument("--role", required=True)
    action_wait_role.add_argument("--name")
    action_wait_role.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_role.add_argument("--poll-ms", type=float, default=250)
    action_wait_role.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden DOM nodes while waiting.",
    )
    _add_text_match_args(action_wait_role)
    action_wait_role.set_defaults(func=cmd_action_wait_role)

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

    action_set_file_input = action_subparsers.add_parser(
        "set-file-input",
        help="Set local files on an input[type=file] without opening a file picker",
    )
    _add_session_target_args(action_set_file_input)
    action_set_file_input.add_argument("--selector", required=True)
    action_set_file_input.add_argument(
        "--file",
        action="append",
        required=True,
        help="Local file path to attach. May be repeated for multiple file inputs.",
    )
    action_set_file_input.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_FILE_INPUT_MAX_BYTES,
        help="Maximum total bytes to embed in the browser action payload.",
    )
    action_set_file_input.add_argument(
        "--no-events",
        action="store_true",
        help="Do not dispatch input/change after setting files.",
    )
    action_set_file_input.set_defaults(func=cmd_action_set_file_input)

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

    action_press_key = action_subparsers.add_parser(
        "press-key",
        help="Dispatch key events to the active element or page",
    )
    _add_session_target_args(action_press_key)
    action_press_key.add_argument("--key", required=True)
    action_press_key.add_argument(
        "--code",
        help="KeyboardEvent.code value. Defaults to --key.",
    )
    action_press_key.add_argument("--alt-key", action="store_true")
    action_press_key.add_argument("--ctrl-key", action="store_true")
    action_press_key.add_argument("--meta-key", action="store_true")
    action_press_key.add_argument("--shift-key", action="store_true")
    action_press_key.set_defaults(func=cmd_action_press_key)

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

    action_link_snapshot = action_subparsers.add_parser(
        "link-snapshot",
        help="Capture page links with text, href, absolute URL, and target metadata",
    )
    _add_session_target_args(action_link_snapshot)
    action_link_snapshot.add_argument(
        "--selector",
        help="Optional link or container selector used to scope links.",
    )
    _add_snapshot_filter_args(action_link_snapshot)
    action_link_snapshot.add_argument(
        "--include-empty",
        action="store_true",
        help="Include links without visible text or accessible name.",
    )
    action_link_snapshot.add_argument(
        "--same-origin-only",
        action="store_true",
        help="Only return links whose resolved URL has the same origin as the current page.",
    )
    action_link_snapshot.set_defaults(func=cmd_action_link_snapshot)

    action_table_snapshot = action_subparsers.add_parser(
        "table-snapshot",
        help="Capture page tables with headers, rows, cells, and cell links",
    )
    _add_session_target_args(action_table_snapshot)
    action_table_snapshot.add_argument(
        "--selector",
        help="Optional table or container selector used to scope tables.",
    )
    _add_snapshot_filter_args(action_table_snapshot)
    action_table_snapshot.add_argument(
        "--max-rows",
        type=_non_negative_int,
        default=50,
        help="Maximum rows to return per table.",
    )
    action_table_snapshot.add_argument(
        "--max-cells",
        type=_non_negative_int,
        default=20,
        help="Maximum cells to return per row.",
    )
    action_table_snapshot.set_defaults(func=cmd_action_table_snapshot)

    action_list_snapshot = action_subparsers.add_parser(
        "list-snapshot",
        help="Capture native and ARIA lists, menus, listboxes, and tree items",
    )
    _add_session_target_args(action_list_snapshot)
    action_list_snapshot.add_argument(
        "--selector",
        help="Optional list or container selector used to scope lists.",
    )
    _add_snapshot_filter_args(action_list_snapshot)
    action_list_snapshot.add_argument(
        "--max-items",
        type=_non_negative_int,
        default=50,
        help="Maximum items to return per list.",
    )
    action_list_snapshot.set_defaults(func=cmd_action_list_snapshot)

    action_text_snapshot = action_subparsers.add_parser(
        "text-snapshot",
        help="Capture bounded readable text blocks, headings, and live regions",
    )
    _add_session_target_args(action_text_snapshot)
    action_text_snapshot.add_argument(
        "--selector",
        help="Optional text block or container selector used to scope text.",
    )
    _add_snapshot_filter_args(action_text_snapshot)
    action_text_snapshot.add_argument(
        "--max-chars",
        type=_non_negative_int,
        default=500,
        help="Maximum characters to return per text block.",
    )
    action_text_snapshot.set_defaults(func=cmd_action_text_snapshot)

    action_dialog_snapshot = action_subparsers.add_parser(
        "dialog-snapshot",
        help="Capture modal/dialog structure, readable text, and controls",
    )
    _add_session_target_args(action_dialog_snapshot)
    action_dialog_snapshot.add_argument(
        "--selector",
        help="Optional dialog or container selector used to scope dialogs.",
    )
    _add_snapshot_filter_args(action_dialog_snapshot)
    action_dialog_snapshot.add_argument(
        "--max-controls",
        type=_non_negative_int,
        default=30,
        help="Maximum interactive controls to return per dialog.",
    )
    action_dialog_snapshot.add_argument(
        "--max-chars",
        type=_non_negative_int,
        default=1000,
        help="Maximum characters to return per dialog text body.",
    )
    action_dialog_snapshot.set_defaults(func=cmd_action_dialog_snapshot)

    action_wait_dialog = action_subparsers.add_parser(
        "wait-dialog",
        help="Wait for a modal/dialog entry and return its structure and controls",
    )
    _add_session_target_args(action_wait_dialog)
    action_wait_dialog.add_argument(
        "--selector",
        help="Optional dialog or container selector used to scope dialogs.",
    )
    _add_snapshot_filter_args(action_wait_dialog)
    action_wait_dialog.add_argument(
        "--max-controls",
        type=_non_negative_int,
        default=30,
        help="Maximum interactive controls to return per dialog.",
    )
    action_wait_dialog.add_argument(
        "--max-chars",
        type=_non_negative_int,
        default=1000,
        help="Maximum characters to return per dialog text body.",
    )
    action_wait_dialog.add_argument(
        "--text",
        help="Optional text to match against dialog title, description, body, or controls.",
    )
    action_wait_dialog.add_argument(
        "--match",
        choices=["contains", "exact", "regex"],
        default="contains",
        help="How --text should match dialog text.",
    )
    action_wait_dialog.add_argument(
        "--modal-only",
        action="store_true",
        help="Only match dialogs that report modal=true.",
    )
    action_wait_dialog.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_dialog.add_argument("--poll-ms", type=float, default=100)
    action_wait_dialog.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Make text matching case-sensitive.",
    )
    action_wait_dialog.set_defaults(func=cmd_action_wait_dialog)

    action_frame_snapshot = action_subparsers.add_parser(
        "frame-snapshot",
        help="Capture iframe/frame metadata, URLs, geometry, and readable same-origin text",
    )
    _add_session_target_args(action_frame_snapshot)
    action_frame_snapshot.add_argument(
        "--selector",
        help="Optional iframe/frame or container selector used to scope frames.",
    )
    _add_snapshot_filter_args(action_frame_snapshot)
    action_frame_snapshot.add_argument(
        "--max-chars",
        type=_non_negative_int,
        default=500,
        help="Maximum readable body text characters to return for same-origin frames.",
    )
    action_frame_snapshot.set_defaults(func=cmd_action_frame_snapshot)

    action_wait_frame = action_subparsers.add_parser(
        "wait-frame",
        help="Wait for an iframe/frame entry and return matching frame metadata",
    )
    _add_session_target_args(action_wait_frame)
    action_wait_frame.add_argument(
        "--selector",
        help="Optional iframe/frame or container selector used to scope frames.",
    )
    _add_snapshot_filter_args(action_wait_frame)
    action_wait_frame.add_argument(
        "--max-chars",
        type=_non_negative_int,
        default=500,
        help="Maximum readable body text characters to return for same-origin frames.",
    )
    action_wait_frame.add_argument(
        "--url",
        help="Optional URL text to match against src, absolute_url, or readable frame_url.",
    )
    action_wait_frame.add_argument(
        "--url-match",
        choices=["contains", "exact", "regex"],
        default="contains",
        help="How --url should match frame URLs.",
    )
    action_wait_frame.add_argument(
        "--text",
        help="Optional text to match against frame name, title, or readable body text.",
    )
    action_wait_frame.add_argument(
        "--text-match",
        choices=["contains", "exact", "regex"],
        default="contains",
        help="How --text should match frame text.",
    )
    action_wait_frame.add_argument(
        "--readable-only",
        action="store_true",
        help="Only match frames whose document is same-origin readable.",
    )
    action_wait_frame.add_argument(
        "--same-origin-only",
        action="store_true",
        help="Only match frames whose src or readable document is same-origin.",
    )
    action_wait_frame.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_frame.add_argument("--poll-ms", type=float, default=100)
    action_wait_frame.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Make URL and text matching case-sensitive.",
    )
    action_wait_frame.set_defaults(func=cmd_action_wait_frame)

    action_performance_snapshot = action_subparsers.add_parser(
        "performance-snapshot",
        help="Capture navigation and resource timing entries with masked URLs",
    )
    _add_session_target_args(action_performance_snapshot)
    action_performance_snapshot.add_argument(
        "--max-resources",
        type=_non_negative_int,
        default=50,
        help="Maximum resource timing entries to return.",
    )
    action_performance_snapshot.add_argument(
        "--initiator-type",
        help="Only return resource entries with this initiatorType, such as fetch, xmlhttprequest, script, css, img, or iframe.",
    )
    action_performance_snapshot.add_argument(
        "--min-duration-ms",
        type=float,
        default=0,
        help="Only return resource entries whose duration is at least this many milliseconds.",
    )
    action_performance_snapshot.set_defaults(func=cmd_action_performance_snapshot)

    action_network_snapshot = action_subparsers.add_parser(
        "network-snapshot",
        help="Install and read a fetch/XHR network event buffer with masked URLs",
    )
    _add_session_target_args(action_network_snapshot)
    action_network_snapshot.add_argument(
        "--max-entries",
        type=_non_negative_int,
        default=50,
        help="Maximum buffered network entries to return.",
    )
    action_network_snapshot.add_argument(
        "--source",
        choices=["fetch", "xhr"],
        help="Only return entries captured from this network source.",
    )
    action_network_snapshot.add_argument(
        "--method",
        help="Only return entries with this HTTP method, such as GET or POST.",
    )
    action_network_snapshot.add_argument(
        "--failed-only",
        action="store_true",
        help="Only return entries whose request failed before an HTTP response.",
    )
    action_network_snapshot.add_argument(
        "--clear",
        action="store_true",
        help="Clear the page network event buffer after reading it.",
    )
    action_network_snapshot.add_argument(
        "--install-only",
        action="store_true",
        help="Install the fetch/XHR network listener without returning buffered entries.",
    )
    action_network_snapshot.set_defaults(func=cmd_action_network_snapshot)

    action_wait_network = action_subparsers.add_parser(
        "wait-network",
        help="Wait for a buffered or future fetch/XHR network entry",
    )
    _add_session_target_args(action_wait_network)
    action_wait_network.add_argument(
        "--url",
        help="Optional text to match against the masked absolute request URL.",
    )
    action_wait_network.add_argument(
        "--url-match",
        choices=["contains", "exact", "regex"],
        default="contains",
        help="How --url should match network entry URLs.",
    )
    action_wait_network.add_argument(
        "--source",
        choices=["fetch", "xhr"],
        help="Only match entries captured from this network source.",
    )
    action_wait_network.add_argument(
        "--method",
        help="Only match entries with this HTTP method, such as GET or POST.",
    )
    action_wait_network.add_argument(
        "--status",
        type=_non_negative_int,
        help="Only match entries with this HTTP response status.",
    )
    action_wait_network.add_argument(
        "--failed-only",
        action="store_true",
        help="Only match entries whose request failed before an HTTP response.",
    )
    action_wait_network.add_argument(
        "--after-index",
        type=_non_negative_int,
        help="Only match entries whose network buffer index is greater than this value.",
    )
    action_wait_network.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_network.add_argument("--poll-ms", type=float, default=100)
    action_wait_network.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Make URL matching case-sensitive.",
    )
    action_wait_network.set_defaults(func=cmd_action_wait_network)

    action_console_snapshot = action_subparsers.add_parser(
        "console-snapshot",
        help="Install and read a page console/error buffer with masked values",
    )
    _add_session_target_args(action_console_snapshot)
    action_console_snapshot.add_argument(
        "--max-entries",
        type=_non_negative_int,
        default=50,
        help="Maximum buffered console/error entries to return.",
    )
    action_console_snapshot.add_argument(
        "--clear",
        action="store_true",
        help="Clear the page console/error buffer after reading it.",
    )
    action_console_snapshot.add_argument(
        "--install-only",
        action="store_true",
        help="Install the page console/error listener without returning buffered entries.",
    )
    action_console_snapshot.set_defaults(func=cmd_action_console_snapshot)

    action_wait_console = action_subparsers.add_parser(
        "wait-console",
        help="Wait for a buffered or future console/page error entry",
    )
    _add_session_target_args(action_wait_console)
    action_wait_console.add_argument(
        "--text",
        help="Optional text to match against the masked console entry text.",
    )
    action_wait_console.add_argument(
        "--match",
        choices=["contains", "exact", "regex"],
        default="contains",
        help="How --text should match console entry text.",
    )
    action_wait_console.add_argument(
        "--source",
        choices=["console", "pageerror", "unhandledrejection"],
        help="Only match entries from this source.",
    )
    action_wait_console.add_argument(
        "--level",
        choices=["debug", "info", "warn", "error"],
        help="Only match entries with this normalized level.",
    )
    action_wait_console.add_argument(
        "--after-index",
        type=_non_negative_int,
        help="Only match entries whose console buffer index is greater than this value.",
    )
    action_wait_console.add_argument("--timeout-ms", type=float, default=30000)
    action_wait_console.add_argument("--poll-ms", type=float, default=100)
    action_wait_console.add_argument(
        "--case-sensitive",
        action="store_true",
        help="Make text matching case-sensitive.",
    )
    action_wait_console.set_defaults(func=cmd_action_wait_console)

    action_outline_snapshot = action_subparsers.add_parser(
        "outline-snapshot",
        help="Capture page headings and landmark regions for navigation",
    )
    _add_session_target_args(action_outline_snapshot)
    action_outline_snapshot.add_argument(
        "--selector",
        help="Optional section or container selector used to scope the outline.",
    )
    _add_snapshot_filter_args(action_outline_snapshot)
    action_outline_snapshot.set_defaults(func=cmd_action_outline_snapshot)

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
    action_interactive_snapshot.set_defaults(
        func=cmd_action_interactive_snapshot,
        action_command_name="action.interactive-snapshot",
    )

    action_interactive_only_snapshot = action_subparsers.add_parser(
        "interactive-only-snapshot",
        help="Alias for interactive-snapshot; capture visible interactive elements",
    )
    _add_session_target_args(action_interactive_only_snapshot)
    _add_snapshot_filter_args(action_interactive_only_snapshot)
    action_interactive_only_snapshot.set_defaults(
        func=cmd_action_interactive_snapshot,
        action_command_name="action.interactive-only-snapshot",
    )


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
    auth_status.add_argument(
        "--credentials-file",
        help=(
            "Read local device-token metadata from this JSON file. Defaults to "
            f"{DEVICE_TOKEN_CREDENTIALS_FILE_ENV} or ~/.config/lexmount/browser-cli/credentials.json."
        ),
    )
    auth_status.set_defaults(func=cmd_auth_status)

    auth_token_info = auth_subparsers.add_parser(
        "token-info",
        help="Inspect local device-token metadata without revealing token values",
    )
    auth_token_info.add_argument(
        "--credentials-file",
        help=(
            "Read local device-token metadata from this JSON file. Defaults to "
            f"{DEVICE_TOKEN_CREDENTIALS_FILE_ENV} or ~/.config/lexmount/browser-cli/credentials.json."
        ),
    )
    auth_token_info.add_argument(
        "--required-scope",
        action="append",
        help="Scope that should be present in the local device token. May be repeated.",
    )
    auth_token_info.set_defaults(func=cmd_auth_token_info)

    auth_refresh = auth_subparsers.add_parser(
        "refresh",
        help="Inspect local device-token refresh state without revealing token values",
    )
    auth_refresh.add_argument(
        "--credentials-file",
        help=(
            "Read local device-token metadata from this JSON file. Defaults to "
            f"{DEVICE_TOKEN_CREDENTIALS_FILE_ENV} or ~/.config/lexmount/browser-cli/credentials.json."
        ),
    )
    auth_refresh.add_argument(
        "--force",
        action="store_true",
        help="Request refresh even when local metadata does not need it. Current implementation reports remote refresh as pending.",
    )
    auth_refresh.set_defaults(func=cmd_auth_refresh)

    auth_logout = auth_subparsers.add_parser(
        "logout",
        help="Remove local device-token metadata without touching env credentials",
    )
    auth_logout.add_argument(
        "--credentials-file",
        help=(
            "Remove local device-token metadata from this JSON file. Defaults to "
            f"{DEVICE_TOKEN_CREDENTIALS_FILE_ENV} or ~/.config/lexmount/browser-cli/credentials.json."
        ),
    )
    auth_logout.add_argument(
        "--revoke",
        action="store_true",
        help="Request remote revoke when available. Current implementation removes local metadata only.",
    )
    auth_logout.set_defaults(func=cmd_auth_logout)

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
    auth_login.add_argument(
        "--open",
        action="store_true",
        help="Open the Connect from Codex URL in the default browser.",
    )
    auth_login.add_argument(
        "--device-code",
        action="store_true",
        help=(
            "Request the planned device-code login contract. Currently returns "
            "available=false until browser.lexmount.cn exposes device-code endpoints."
        ),
    )
    auth_login.set_defaults(func=cmd_auth_login)


def _add_doctor_command(subparsers: argparse._SubParsersAction[Any]) -> None:
    doctor = subparsers.add_parser(
        "doctor",
        help="Check browser-cli install, credentials, direct URL, and API connectivity",
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        help="Accepted for compatibility; browser-cli output is always JSON.",
    )
    doctor.add_argument(
        "--skip-api",
        action="store_true",
        help="Skip the live Lexmount API connectivity check.",
    )
    doctor.add_argument(
        "--smoke-session",
        action="store_true",
        help="Create and close a temporary browser session after API connectivity passes.",
    )
    doctor.add_argument(
        "--reveal-connect-url",
        action="store_true",
        help="Print the full direct URL including api_key. Default output masks secrets.",
    )
    doctor.add_argument(
        "--credentials-file",
        help=(
            "Read local device-token metadata from this JSON file. Defaults to "
            f"{DEVICE_TOKEN_CREDENTIALS_FILE_ENV} or ~/.config/lexmount/browser-cli/credentials.json."
        ),
    )
    doctor.set_defaults(func=cmd_doctor)


def _add_commands_command(subparsers: argparse._SubParsersAction[Any]) -> None:
    commands = subparsers.add_parser(
        "commands",
        help="Print a machine-readable browser-cli command catalog",
    )
    commands.add_argument(
        "--group",
        help="Only include commands from one group, such as action, auth, or session.",
    )
    output = commands.add_mutually_exclusive_group()
    output.add_argument(
        "--names-only",
        action="store_true",
        help="Return only command names for compact agent discovery.",
    )
    output.add_argument(
        "--workflows-only",
        action="store_true",
        help="Return only structured agent workflows for compact agent setup.",
    )
    output.add_argument(
        "--workflow",
        help="Return a single structured agent workflow by id.",
    )
    commands.set_defaults(func=cmd_commands)


def _add_version_command(subparsers: argparse._SubParsersAction[Any]) -> None:
    version_parser = subparsers.add_parser(
        "version",
        help="Print browser-cli and runtime versions as JSON",
    )
    version_parser.set_defaults(func=cmd_version)


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
    parser.add_argument(
        "--json",
        action="store_true",
        help="Accepted for compatibility; browser-cli output is always JSON.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        dest="show_version",
        help="Print browser-cli and runtime versions as JSON.",
    )
    subparsers = parser.add_subparsers(dest="command")

    _add_version_command(subparsers)
    _add_session_commands(subparsers)
    _add_context_commands(subparsers)
    _add_action_commands(subparsers)
    _add_case_commands(subparsers)
    _add_auth_commands(subparsers)
    _add_doctor_command(subparsers)
    _add_commands_command(subparsers)
    _add_alias_commands(subparsers)
    _add_json_compatibility_flag(parser)

    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the Lexmount browser operation CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "show_version", False):
        cmd_version(args)
    if not hasattr(args, "func"):
        parser.error("the following arguments are required: command")
    args.func(args)


if __name__ == "__main__":
    main()
