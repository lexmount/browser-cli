"""Command-line entrypoint for Lexmount browser operations."""

from __future__ import annotations

import argparse
import json
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
