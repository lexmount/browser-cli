#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=15.0.1"]
# ///
"""Singleton browser CDP owner used by cdp.py. Not a public CLI."""

from __future__ import annotations

import argparse
import asyncio
import copy
import contextlib
from collections import Counter
from difflib import SequenceMatcher
import fcntl
import json
import math
import os
import signal
import struct
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect

from cdp import (
    DEFAULT_CDP_HOST,
    DEFAULT_CDP_PORT,
    MAX_IPC_BYTES,
    AceProtoError,
    EndpointConfig,
    RuntimePaths,
    TransportError,
    UsageError,
    append_log,
    endpoint_config,
    error_payload,
    json_bytes,
    runtime_paths,
    sanitize,
    write_state,
)


CDP_HOST = DEFAULT_CDP_HOST
CDP_PORT = DEFAULT_CDP_PORT
PAGE_CONTENT_METHOD = "Page.getAIPageContent"
PERFORM_ACTION_METHOD = "Page.performDOMAction"
ACTION_CHANGES_METHOD = "Page.getAIPageActionChanges"
NAVIGATE_METHOD = "Page.navigate"
ACTION_CDP_TIMEOUT = 30.0
NAVIGATE_POLL_INTERVAL = 0.5
SESSION_LOCK_WAIT_TIMEOUT = 1.0
BLOCKED_METHODS = {
    "Browser.close": "this skill must never close the browser",
    "Browser.crash": "this skill must never terminate the browser",
    "Target.exposeDevToolsProtocol": (
        "it would create a second CDP channel outside the singleton daemon"
    ),
}


class PolicyError(AceProtoError):
    kind = "policy"


class SessionError(AceProtoError):
    kind = "session"


class ActionResultError(AceProtoError):
    kind = "action-result"


class NavigateTimeoutError(AceProtoError):
    kind = "navigate-timeout"

    def __init__(
        self,
        timeout: float,
        url: str,
        navigate_response: dict[str, Any] | None,
        last_ready: bool | None,
    ):
        self.data: dict[str, Any] = {
            "url": url,
            "lastReady": last_ready,
        }
        if navigate_response is not None:
            self.data["navigateResponse"] = copy.deepcopy(navigate_response)
        super().__init__(
            f"Navigation timed out after {timeout:g}s waiting for ready=true"
        )


class _CdpRequestTimeoutError(RuntimeError):
    pass


class CdpProtocolError(AceProtoError):
    kind = "cdp"

    def __init__(self, method: str, error: Any):
        self.code = error.get("code") if isinstance(error, dict) else None
        self.data = error.get("data") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else str(error)
        super().__init__(f"{method} failed: {message}")


@dataclass(frozen=True)
class OwnedSession:
    session_id: str
    target_id: str

    def as_json(self) -> dict[str, str]:
        return {"sessionId": self.session_id, "targetId": self.target_id}


@dataclass(frozen=True)
class PendingRequest:
    future: asyncio.Future[dict[str, Any]]
    session_id: str | None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class _NoWebSocketRedirectConnect(connect):
    def process_redirect(self, exc: Exception) -> Exception | str:
        redirect = super().process_redirect(exc)
        if isinstance(redirect, str):
            return RuntimeError("WebSocket redirects are disabled")
        return redirect


def _discover_browser_websocket(host: str, port: int) -> str:
    version_url = f"http://{host}:{port}/json/version"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirectHandler()
    )
    request = urllib.request.Request(
        version_url, headers={"Accept": "application/json"}
    )
    try:
        with opener.open(request, timeout=3) as response:
            raw = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"Cannot discover CDP at {version_url}: HTTP {error.code}"
        ) from error
    except OSError as error:
        raise RuntimeError(
            f"Cannot connect to the browser at {host}:{port}: {error}"
        ) from error
    if len(raw) > 1024 * 1024:
        raise RuntimeError("The CDP version response exceeds 1 MiB")
    try:
        payload = json.loads(raw)
        advertised = payload["webSocketDebuggerUrl"]
        parsed = urlsplit(advertised)
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError(
            "The CDP version response has no valid browser WebSocket"
        ) from error
    if (
        not isinstance(advertised, str)
        or parsed.scheme != "ws"
        or parsed.username
        or parsed.password
        or not parsed.path.startswith("/devtools/browser/")
    ):
        raise RuntimeError("The CDP version response has an invalid browser WebSocket")
    # Never trust or follow the advertised authority. Only reuse the browser path
    # while fixing the actual connection to this daemon's loopback host and port.
    return urlunsplit(("ws", f"{host}:{port}", parsed.path, parsed.query, ""))


def _normalize_params(method: str, params: Any, depth: int = 0) -> dict[str, Any]:
    if depth > 8:
        raise PolicyError("Nested CDP message depth exceeds the policy limit")
    if not isinstance(params, dict):
        raise UsageError("CDP params must be a JSON object")
    normalized = dict(params)
    if method in BLOCKED_METHODS:
        raise PolicyError(f"{method} is blocked because {BLOCKED_METHODS[method]}")
    if method == PAGE_CONTENT_METHOD:
        if "includeDebugInfo" not in normalized:
            normalized["includeDebugInfo"] = False
        elif normalized["includeDebugInfo"] is not False:
            raise PolicyError(
                "Page.getAIPageContent requires includeDebugInfo to be exactly false"
            )
    if method == "Target.sendMessageToTarget" and isinstance(
        normalized.get("message"), str
    ):
        try:
            nested = json.loads(normalized["message"])
        except ValueError as error:
            raise UsageError(
                "Target.sendMessageToTarget message must be valid JSON"
            ) from error
        if not isinstance(nested, dict) or not isinstance(nested.get("method"), str):
            raise UsageError(
                "Target.sendMessageToTarget message must contain a CDP method"
            )
        original = nested.get("params", {})
        nested_params = _normalize_params(nested["method"], original, depth + 1)
        if nested_params != original:
            nested["params"] = nested_params
            normalized["message"] = json.dumps(
                nested, ensure_ascii=False, separators=(",", ":")
            )
    return normalized


def _protocol_result(response: dict[str, Any], method: str) -> dict[str, Any]:
    if "error" in response:
        raise CdpProtocolError(method, response["error"])
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"{method} returned no result object")
    return result


def _node_identity(node: dict[str, Any]) -> tuple[str, Any] | None:
    node_id = node.get("id")
    if isinstance(node_id, bool) or not isinstance(node_id, (int, str)):
        return None
    return (type(node_id).__name__, node_id)


def _json_signature(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _page_node_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    children = node.get("children", [])
    if not isinstance(children, list) or not all(
        isinstance(child, dict) for child in children
    ):
        raise RuntimeError("Page.getAIPageContent returned an invalid content tree")
    return children


def _validate_page_content_tree(node: dict[str, Any]) -> None:
    for child in _page_node_children(node):
        _validate_page_content_tree(child)


def _exact_alignment_tokens(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> tuple[list[tuple[str, Any]], list[tuple[str, Any]]]:
    before_ids = Counter(
        identity for node in before if (identity := _node_identity(node)) is not None
    )
    after_ids = Counter(
        identity for node in after if (identity := _node_identity(node)) is not None
    )
    shared_unique_ids = {
        identity
        for identity, count in before_ids.items()
        if count == 1 and after_ids.get(identity) == 1
    }

    def token(node: dict[str, Any]) -> tuple[str, Any]:
        identity = _node_identity(node)
        if identity in shared_unique_ids:
            return ("id", identity)
        return ("content", _json_signature(node))

    return [token(node) for node in before], [token(node) for node in after]


def _reliable_field_alignment_tokens(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> tuple[list[tuple[str, Any]], list[tuple[str, Any]]]:
    def fields(node: dict[str, Any]) -> str:
        return _json_signature(
            {key: value for key, value in node.items() if key != "children"}
        )

    before_fields = Counter(fields(node) for node in before)
    after_fields = Counter(fields(node) for node in after)
    shared_unique_fields = {
        signature
        for signature, count in before_fields.items()
        if count == 1 and after_fields.get(signature) == 1
    }

    def tokens(nodes: list[dict[str, Any]], side: str) -> list[tuple[str, Any]]:
        result: list[tuple[str, Any]] = []
        for index, node in enumerate(nodes):
            signature = fields(node)
            if signature in shared_unique_fields:
                result.append(("fields", signature))
            else:
                result.append((side, index))
        return result

    return tokens(before, "before"), tokens(after, "after")


def _changed_replacement_subtrees(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    before_tokens, after_tokens = _reliable_field_alignment_tokens(before, after)
    matcher = SequenceMatcher(None, before_tokens, after_tokens, autojunk=False)
    changed: list[dict[str, Any]] = []
    for (
        operation,
        before_start,
        before_end,
        after_start,
        after_end,
    ) in matcher.get_opcodes():
        if operation == "delete":
            continue
        if operation == "insert":
            changed.extend(copy.deepcopy(after[after_start:after_end]))
            continue

        before_block = before[before_start:before_end]
        after_block = after[after_start:after_end]
        if operation == "replace":
            # Without a reliable one-to-one alignment, every new-side root is
            # potentially an insertion, even when both blocks have equal size.
            # Returning all of them never loses new content as a pure deletion.
            changed.extend(copy.deepcopy(after_block))
            continue
        for before_node, after_node in zip(before_block, after_block):
            changed.extend(_changed_node_subtrees(before_node, after_node))
    return changed


def _changed_node_subtrees(
    before: dict[str, Any], after: dict[str, Any]
) -> list[dict[str, Any]]:
    if before == after:
        return []

    before_fields = {key: value for key, value in before.items() if key != "children"}
    after_fields = {key: value for key, value in after.items() if key != "children"}
    if before_fields != after_fields:
        return [copy.deepcopy(after)]

    before_children = _page_node_children(before)
    after_children = _page_node_children(after)
    before_tokens, after_tokens = _exact_alignment_tokens(
        before_children, after_children
    )
    matcher = SequenceMatcher(
        None,
        before_tokens,
        after_tokens,
        autojunk=False,
    )
    changed: list[dict[str, Any]] = []
    for (
        operation,
        before_start,
        before_end,
        after_start,
        after_end,
    ) in matcher.get_opcodes():
        if operation == "delete":
            continue
        if operation == "insert":
            changed.extend(copy.deepcopy(after_children[after_start:after_end]))
            continue

        changed.extend(
            _changed_replacement_subtrees(
                before_children[before_start:before_end],
                after_children[after_start:after_end],
            )
        )
    return changed


def changed_content_subtrees(
    before: dict[str, Any], after: dict[str, Any]
) -> list[dict[str, Any]]:
    before_content = before.get("content")
    after_content = after.get("content")
    if not isinstance(after_content, dict):
        raise RuntimeError("Page.getAIPageContent returned no content tree")
    _page_node_children(after_content)
    if not isinstance(before_content, dict):
        return [copy.deepcopy(after_content)]
    _page_node_children(before_content)
    return _changed_node_subtrees(before_content, after_content)


async def _read_packet(reader: asyncio.StreamReader) -> Any:
    header = await reader.readexactly(4)
    (length,) = struct.unpack("!I", header)
    if length > MAX_IPC_BYTES:
        raise RuntimeError("IPC request exceeds 512 MiB")
    try:
        return json.loads(await reader.readexactly(length))
    except ValueError as error:
        raise UsageError("IPC request is not valid JSON") from error


async def _write_packet(writer: asyncio.StreamWriter, value: Any) -> None:
    data = json_bytes(value)
    if len(data) > MAX_IPC_BYTES:
        data = json_bytes(error_payload(RuntimeError("IPC response exceeds 512 MiB")))
    writer.write(struct.pack("!I", len(data)) + data)
    await writer.drain()


class CdpDaemon:
    def __init__(
        self,
        paths: RuntimePaths,
        host: str = CDP_HOST,
        port: int = CDP_PORT,
        port_check_interval: float = 0.5,
        *,
        websocket_url: str | None = None,
    ):
        self.paths = paths
        self.host = host
        self.port = port
        self.endpoint: EndpointConfig = endpoint_config(websocket_url, host, port)
        self.port_check_interval = port_check_interval
        self.ws: Any = None
        self.connected = False
        self.shutting_down = False
        self.last_error: str | None = None
        self.stop_event = asyncio.Event()
        self.reader_task: asyncio.Task[Any] | None = None
        self.port_task: asyncio.Task[Any] | None = None
        self.writer_lock = asyncio.Lock()
        self.pending: dict[int, PendingRequest] = {}
        self.next_request_id = 1
        self.sessions: dict[str, OwnedSession] = {}
        self.session_locks: dict[str, asyncio.Lock] = {}
        self.detaching: set[str] = set()
        self.session_probe_tasks: dict[str, asyncio.Task[Any]] = {}
        self.session_probe_targets: dict[str, str] = {}
        self.content_baselines: dict[str, dict[str, Any]] = {}
        self.lifecycle_generation = 0
        self.session_lock_wait_timeout = SESSION_LOCK_WAIT_TIMEOUT
        self.management_lock = asyncio.Lock()

    def status(self, running: bool = True) -> dict[str, Any]:
        return {
            "running": running,
            "connected": self.connected,
            "pid": os.getpid(),
            "endpoint": self.endpoint.display,
            "endpointMode": self.endpoint.mode,
            "endpointId": self.endpoint.endpoint_id,
            "sessionCount": len(self.sessions),
            "lastError": self.last_error,
        }

    def persist(self, running: bool = True) -> None:
        write_state(self.paths, self.status(running))

    async def start(self) -> None:
        ws_url = self.endpoint.websocket_url
        if ws_url is None:
            ws_url = await asyncio.to_thread(
                _discover_browser_websocket, self.host, self.port
            )
        self.ws = await _NoWebSocketRedirectConnect(
            ws_url,
            origin=None,
            compression=None,
            proxy=None,
            open_timeout=10,
            ping_interval=10,
            ping_timeout=10,
            close_timeout=1,
            max_size=None,
            max_queue=64,
            user_agent_header="ace-proto-singleton/2",
        )
        self.connected = True
        self.reader_task = asyncio.create_task(self._reader_loop(), name="cdp-reader")
        # Do not auto-attach any target. This command only verifies the browser
        # connection before the Unix socket becomes visible to clients.
        await self.get_targets()
        self.persist(running=True)

    async def _reader_loop(self) -> None:
        failure: Exception | None = None
        try:
            async for raw in self.ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                message = json.loads(raw)
                if not isinstance(message, dict):
                    continue
                request_id = message.get("id")
                if isinstance(request_id, int):
                    pending = self.pending.pop(request_id, None)
                    if pending is not None and not pending.future.done():
                        pending.future.set_result(message)
                    continue
                self._observe_event(message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failure = error
        finally:
            self.connected = False
            if not self.shutting_down:
                self.last_error = sanitize(
                    str(failure or "Browser CDP WebSocket closed"),
                    self.endpoint.websocket_url,
                )
            error = RuntimeError(self.last_error or "CDP daemon is shutting down")
            for pending in list(self.pending.values()):
                if not pending.future.done():
                    pending.future.set_exception(error)
            self.pending.clear()
            with contextlib.suppress(Exception):
                self.persist(running=not self.shutting_down)
            self.stop_event.set()

    def _register_session(self, session_id: str, target_id: str) -> OwnedSession:
        self._cancel_session_probe(session_id)
        previous = self.sessions.get(session_id)
        if previous is not None and previous.target_id == target_id:
            return previous
        if previous is not None and previous.target_id != target_id:
            self.content_baselines.pop(session_id, None)
        session = OwnedSession(session_id, target_id)
        self.sessions[session_id] = session
        self.session_locks.setdefault(session_id, asyncio.Lock())
        return session

    def _unregister_session(self, session_id: str) -> OwnedSession | None:
        self.detaching.discard(session_id)
        self._cancel_session_probe(session_id)
        self._fail_session_pending(
            session_id, f"Session {session_id!r} is no longer attached"
        )
        self.content_baselines.pop(session_id, None)
        self.session_locks.pop(session_id, None)
        return self.sessions.pop(session_id, None)

    def _cancel_session_probe(self, session_id: str) -> None:
        task = self.session_probe_tasks.pop(session_id, None)
        self.session_probe_targets.pop(session_id, None)
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    def _schedule_session_probe(self, session_id: str, target_id: str) -> None:
        if session_id in self.sessions or session_id in self.session_probe_tasks:
            return
        self.session_probe_targets[session_id] = target_id
        task = asyncio.create_task(
            self._probe_flat_session(session_id, target_id),
            name="cdp-session-route-probe",
        )
        self.session_probe_tasks[session_id] = task

        def discard(completed: asyncio.Task[Any]) -> None:
            if self.session_probe_tasks.get(session_id) is completed:
                self.session_probe_tasks.pop(session_id, None)
                self.session_probe_targets.pop(session_id, None)

        task.add_done_callback(discard)

    async def _probe_flat_session(self, session_id: str, target_id: str) -> None:
        try:
            # attachedToTarget doesn't expose whether the child uses flat
            # routing. A harmless command addressed through the top-level
            # sessionId distinguishes flattened children from legacy ones.
            response = await self.send_cdp(
                "Target.getTargetInfo", {}, session_id=session_id, timeout=5
            )
        except Exception:
            return
        if (
            "error" not in response
            and self.session_probe_targets.get(session_id) == target_id
        ):
            self._register_session(session_id, target_id)

    def _observe_event(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = (
            message.get("params") if isinstance(message.get("params"), dict) else {}
        )
        if method == "Target.attachedToTarget":
            session_id = params.get("sessionId")
            target_info = params.get("targetInfo")
            target_id = (
                target_info.get("targetId") if isinstance(target_info, dict) else None
            )
            if isinstance(session_id, str) and isinstance(target_id, str):
                self._schedule_session_probe(session_id, target_id)
        elif method == "Target.detachedFromTarget":
            session_id = params.get("sessionId")
            if isinstance(session_id, str):
                self.lifecycle_generation += 1
                self._unregister_session(session_id)
        elif method == "Target.targetDestroyed":
            target_id = params.get("targetId")
            if isinstance(target_id, str):
                self.lifecycle_generation += 1
                for session_id, pending_target in list(
                    self.session_probe_targets.items()
                ):
                    if pending_target == target_id:
                        self._fail_session_pending(
                            session_id, f"Target {target_id!r} was destroyed"
                        )
                        self._cancel_session_probe(session_id)
                for session_id, session in list(self.sessions.items()):
                    if session.target_id == target_id:
                        self._unregister_session(session_id)

    def _fail_pending(self, reason: str) -> None:
        error = RuntimeError(reason)
        for pending in list(self.pending.values()):
            if not pending.future.done():
                pending.future.set_exception(error)
        self.pending.clear()

    def _fail_session_pending(self, session_id: str, reason: str) -> None:
        error = SessionError(reason)
        for request_id, pending in list(self.pending.items()):
            if pending.session_id != session_id:
                continue
            self.pending.pop(request_id, None)
            if not pending.future.done():
                pending.future.set_exception(error)

    async def send_cdp(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        if not self.connected or self.ws is None:
            raise RuntimeError(
                self.last_error or "Browser CDP WebSocket is disconnected"
            )
        normalized = _normalize_params(method, params or {})
        request_id = self.next_request_id
        self.next_request_id += 1
        request: dict[str, Any] = {
            "id": request_id,
            "method": method,
            "params": normalized,
        }
        if session_id is not None:
            request["sessionId"] = session_id
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = PendingRequest(future, session_id)
        try:
            async with self.writer_lock:
                await self.ws.send(
                    json.dumps(request, ensure_ascii=False, separators=(",", ":"))
                )
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as error:
            self.pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            raise _CdpRequestTimeoutError(
                f"timeout after {timeout:g}s waiting for {method}"
            ) from error
        except (Exception, asyncio.CancelledError):
            self.pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            raise

    def _cache_page_content(self, session_id: str, response: dict[str, Any]) -> None:
        if "error" in response:
            return
        result = response.get("result")
        if isinstance(result, dict):
            self.content_baselines[session_id] = copy.deepcopy(result)

    def _require_current_session(self, session_id: str, session: OwnedSession) -> None:
        if self.sessions.get(session_id) is not session or session_id in self.detaching:
            raise SessionError(f"Session {session_id!r} is no longer attached")

    async def _acquire_session_lock(self, session_id: str, lock: asyncio.Lock) -> None:
        try:
            await asyncio.wait_for(
                lock.acquire(), timeout=self.session_lock_wait_timeout
            )
        except TimeoutError as error:
            raise SessionError(
                f"Session {session_id!r} is busy; retry after the current command finishes"
            ) from error

    def _action_time(self) -> float:
        return asyncio.get_running_loop().time()

    async def _action_sleep(self, delay: float) -> None:
        await asyncio.sleep(delay)

    async def navigate_session(
        self,
        session_id: str,
        url: str,
        include_content: bool,
        timeout: float,
    ) -> dict[str, Any]:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionError(
                f"Unknown session {session_id!r}; run sessions and attach the target first"
            )
        if session_id in self.detaching:
            raise SessionError(f"Session {session_id!r} is detaching")
        lock = self.session_locks[session_id]
        await self._acquire_session_lock(session_id, lock)
        try:
            self._require_current_session(session_id, session)
            self.content_baselines.pop(session_id, None)

            started = self._action_time()
            deadline = started + timeout
            navigate_response: dict[str, Any] | None = None
            last_ready: bool | None = None

            try:
                navigate_response = await self.send_cdp(
                    NAVIGATE_METHOD,
                    {"url": url},
                    session_id,
                    timeout=max(0.0, deadline - self._action_time()),
                )
            except _CdpRequestTimeoutError as error:
                self._require_current_session(session_id, session)
                raise NavigateTimeoutError(
                    timeout, url, navigate_response, last_ready
                ) from error
            self._require_current_session(session_id, session)
            if self._action_time() > deadline:
                raise NavigateTimeoutError(
                    timeout, url, navigate_response, last_ready
                )

            payload = {"navigateResponse": navigate_response}
            if "error" in navigate_response:
                return payload
            navigate_result = navigate_response.get("result")
            if not isinstance(navigate_result, dict):
                raise TransportError("Page.navigate returned no result object")
            error_text = navigate_result.get("errorText")
            if (
                (isinstance(error_text, str) and bool(error_text))
                or navigate_result.get("isDownload") is True
                or not include_content
            ):
                return payload

            while True:
                remaining = deadline - self._action_time()
                if remaining <= 0:
                    raise NavigateTimeoutError(
                        timeout, url, navigate_response, last_ready
                    )
                try:
                    content_response = await self.send_cdp(
                        PAGE_CONTENT_METHOD,
                        {"includeDebugInfo": False},
                        session_id,
                        timeout=remaining,
                    )
                except _CdpRequestTimeoutError as error:
                    self._require_current_session(session_id, session)
                    raise NavigateTimeoutError(
                        timeout, url, navigate_response, last_ready
                    ) from error
                self._require_current_session(session_id, session)
                if self._action_time() > deadline:
                    raise NavigateTimeoutError(
                        timeout, url, navigate_response, last_ready
                    )

                payload["contentResponse"] = content_response
                if "error" in content_response:
                    return payload
                content_result = content_response.get("result")
                if not isinstance(content_result, dict):
                    raise TransportError(
                        "Page.getAIPageContent returned no result object"
                    )
                ready = content_result.get("ready")
                if not isinstance(ready, bool):
                    raise TransportError(
                        "Page.getAIPageContent returned an invalid ready value; "
                        "expected a boolean"
                    )
                last_ready = ready
                if ready:
                    content = content_result.get("content")
                    if not isinstance(content, dict):
                        raise TransportError(
                            "Page.getAIPageContent returned no content tree"
                        )
                    _validate_page_content_tree(content)
                    self.content_baselines[session_id] = copy.deepcopy(content_result)
                    return payload

                remaining = deadline - self._action_time()
                if remaining <= 0:
                    raise NavigateTimeoutError(
                        timeout, url, navigate_response, last_ready
                    )
                await self._action_sleep(min(NAVIGATE_POLL_INTERVAL, remaining))
                self._require_current_session(session_id, session)
        finally:
            lock.release()

    async def action_session(
        self,
        session_id: str,
        params: dict[str, Any],
        poll_timeout: float,
        observation: str = "none",
    ) -> dict[str, Any]:
        if not isinstance(observation, str) or observation not in {
            "none",
            "diff",
            "content",
        }:
            raise UsageError("action observation must be one of: none, diff, content")
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionError(
                f"Unknown session {session_id!r}; run sessions and attach the target first"
            )
        if session_id in self.detaching:
            raise SessionError(f"Session {session_id!r} is detaching")
        lock = self.session_locks[session_id]
        await self._acquire_session_lock(session_id, lock)
        try:
            self._require_current_session(session_id, session)
            baseline = self.content_baselines.get(session_id)
            if baseline is None:
                raise UsageError(
                    "action requires a content baseline; run content for this session first"
                )
            before = copy.deepcopy(baseline) if observation == "diff" else None

            perform_response = await self.send_cdp(
                PERFORM_ACTION_METHOD,
                params,
                session_id,
                timeout=ACTION_CDP_TIMEOUT,
            )
            self._require_current_session(session_id, session)
            perform = _protocol_result(perform_response, PERFORM_ACTION_METHOD)
            if perform.get("ok") is not True:
                reason = perform.get("reason")
                raise ActionResultError(
                    reason
                    if isinstance(reason, str) and reason
                    else "action returned ok:false"
                )

            started = self._action_time()
            deadline = started + poll_timeout
            scheduled = started + 1.0
            last_changes: dict[str, Any] | None = None
            new_target = False

            while scheduled <= deadline:
                if last_changes is not None and self._action_time() > deadline:
                    break
                delay = scheduled - self._action_time()
                if delay > 0:
                    await self._action_sleep(delay)
                self._require_current_session(session_id, session)
                changes_response = await self.send_cdp(
                    ACTION_CHANGES_METHOD,
                    {},
                    session_id,
                    timeout=ACTION_CDP_TIMEOUT,
                )
                self._require_current_session(session_id, session)
                changes = _protocol_result(changes_response, ACTION_CHANGES_METHOD)
                effects = changes.get("effects")
                if not isinstance(effects, list):
                    raise RuntimeError(
                        "Page.getAIPageActionChanges returned no effects array"
                    )
                last_changes = changes
                new_target = any(
                    isinstance(effect, dict)
                    and (
                        effect.get("effect") == "new_target"
                        or isinstance(effect.get("newTargetId"), str)
                    )
                    for effect in effects
                )
                if new_target:
                    break
                if not effects or not all(
                    isinstance(effect, dict) and effect.get("effect") == "no_op"
                    for effect in effects
                ):
                    break
                if self._action_time() > deadline:
                    break
                scheduled = self._action_time() + 1.0

            if last_changes is None:  # poll_timeout >= 1 always schedules one read
                raise RuntimeError("action changes polling produced no response")

            payload: dict[str, Any] = {
                "perform": perform,
                "changes": last_changes,
            }
            if new_target:
                return payload

            try:
                self._require_current_session(session_id, session)
                content_response = await self.send_cdp(
                    PAGE_CONTENT_METHOD,
                    {"includeDebugInfo": False},
                    session_id,
                    timeout=ACTION_CDP_TIMEOUT,
                )
                self._require_current_session(session_id, session)
                after = _protocol_result(content_response, PAGE_CONTENT_METHOD)
                after_content = after.get("content")
                if not isinstance(after_content, dict):
                    raise RuntimeError("Page.getAIPageContent returned no content tree")
                _validate_page_content_tree(after_content)
                if observation == "diff":
                    assert before is not None
                    payload["contentDiff"] = changed_content_subtrees(before, after)
                self.content_baselines[session_id] = copy.deepcopy(after)
                if observation == "content":
                    payload["contentResponse"] = content_response
                return payload
            except Exception as error:
                error_data = copy.deepcopy(payload)
                cause_data = getattr(error, "data", None)
                if cause_data is not None:
                    error_data["causeData"] = cause_data
                error.data = error_data
                raise
        finally:
            lock.release()

    async def get_targets(self) -> list[dict[str, Any]]:
        response = await self.send_cdp("Target.getTargets", {}, timeout=10)
        result = _protocol_result(response, "Target.getTargets")
        target_infos = result.get("targetInfos")
        if not isinstance(target_infos, list):
            raise RuntimeError("Target.getTargets returned no targetInfos array")
        return [target for target in target_infos if isinstance(target, dict)]

    async def list_sessions(self) -> dict[str, Any]:
        targets = await self.get_targets()
        probes = list(self.session_probe_tasks.values())
        if probes:
            await asyncio.gather(*probes, return_exceptions=True)
        sessions = [
            session.as_json()
            for session in sorted(
                self.sessions.values(), key=lambda item: item.session_id
            )
        ]
        return {"ok": True, "targets": targets, "sessions": sessions}

    async def attach_session(self, target_id: str) -> dict[str, Any]:
        try:
            await asyncio.wait_for(
                self.management_lock.acquire(), timeout=self.session_lock_wait_timeout
            )
        except TimeoutError as error:
            raise SessionError(
                f"Target {target_id!r} is busy; retry after the current attach finishes"
            ) from error
        try:
            existing = next(
                (
                    session
                    for session in self.sessions.values()
                    if session.target_id == target_id
                    and session.session_id not in self.detaching
                ),
                None,
            )
            if existing is not None:
                return {"ok": True, "session": existing.as_json(), "reused": True}
            targets = await self.get_targets()
            if not any(target.get("targetId") == target_id for target in targets):
                raise SessionError(f"No browser target exists with id {target_id!r}")
            lifecycle_generation = self.lifecycle_generation
            response = await self.send_cdp(
                "Target.attachToTarget",
                {"targetId": target_id, "flatten": True},
                timeout=10,
            )
            result = _protocol_result(response, "Target.attachToTarget")
            session_id = result.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise RuntimeError("Target.attachToTarget returned no sessionId")
            while lifecycle_generation != self.lifecycle_generation:
                lifecycle_generation = self.lifecycle_generation
                verification = await self.send_cdp(
                    "Target.getTargetInfo", {}, session_id=session_id, timeout=5
                )
                _protocol_result(verification, "Target.getTargetInfo")
            session = self._register_session(session_id, target_id)
            self.persist(running=True)
            return {"ok": True, "session": session.as_json(), "reused": False}
        finally:
            self.management_lock.release()

    async def detach_session(self, session_id: str) -> dict[str, Any]:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionError(
                f"Unknown session {session_id!r}; it may belong to another daemon instance"
            )
        if session_id in self.detaching:
            raise SessionError(f"Session {session_id!r} is already detaching")
        self.detaching.add(session_id)
        lock = self.session_locks[session_id]
        try:
            await self._acquire_session_lock(session_id, lock)
            try:
                response = await self.send_cdp(
                    "Target.detachFromTarget", {"sessionId": session_id}, timeout=10
                )
                _protocol_result(response, "Target.detachFromTarget")
            finally:
                lock.release()
            self._unregister_session(session_id)
            self.persist(running=True)
            return {
                "ok": True,
                "detached": True,
                "sessionId": session_id,
                "targetId": session.target_id,
            }
        finally:
            self.detaching.discard(session_id)

    async def call_session(
        self,
        session_id: str,
        method: str,
        params: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        session = self.sessions.get(session_id)
        if session is None:
            raise SessionError(
                f"Unknown session {session_id!r}; run sessions and attach the target first"
            )
        if session_id in self.detaching:
            raise SessionError(f"Session {session_id!r} is detaching")
        lock = self.session_locks[session_id]
        await self._acquire_session_lock(session_id, lock)
        try:
            self._require_current_session(session_id, session)
            lifecycle_generation = self.lifecycle_generation
            response = await self.send_cdp(method, params, session_id, timeout)
            self._require_current_session(session_id, session)
            if "error" not in response:
                await self._observe_lifecycle_result(
                    method, params, response, lifecycle_generation
                )
                self._require_current_session(session_id, session)
                if method == PAGE_CONTENT_METHOD:
                    self._cache_page_content(session_id, response)
            return response
        finally:
            lock.release()

    async def _observe_lifecycle_result(
        self,
        method: str,
        params: dict[str, Any],
        response: dict[str, Any],
        lifecycle_generation: int,
    ) -> None:
        result = (
            response.get("result") if isinstance(response.get("result"), dict) else {}
        )
        if method == "Target.attachToTarget" and params.get("flatten") is True:
            session_id = result.get("sessionId")
            target_id = params.get("targetId")
            if isinstance(session_id, str) and isinstance(target_id, str):
                while lifecycle_generation != self.lifecycle_generation:
                    lifecycle_generation = self.lifecycle_generation
                    verification = await self.send_cdp(
                        "Target.getTargetInfo", {}, session_id=session_id, timeout=5
                    )
                    _protocol_result(verification, "Target.getTargetInfo")
                self._register_session(session_id, target_id)
                self.persist(running=True)
        elif method == "Target.attachToBrowserTarget":
            session_id = result.get("sessionId")
            if isinstance(session_id, str):
                while lifecycle_generation != self.lifecycle_generation:
                    lifecycle_generation = self.lifecycle_generation
                    verification = await self.send_cdp(
                        "Target.getTargetInfo", {}, session_id=session_id, timeout=5
                    )
                    _protocol_result(verification, "Target.getTargetInfo")
                self._register_session(session_id, "browser")
                self.persist(running=True)
        elif method == "Target.detachFromTarget":
            detached = params.get("sessionId")
            if isinstance(detached, str):
                self._unregister_session(detached)
                self.persist(running=True)

    async def handle_request(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise UsageError("IPC request must be an object")
        operation = request.get("op")
        if operation == "ping":
            return {
                "ok": True,
                "pong": True,
                "connected": self.connected,
                "status": self.status(running=True),
            }
        expected_endpoint_id = request.get("endpointId")
        if not isinstance(expected_endpoint_id, str):
            raise UsageError("endpointId is required and must be a string")
        if expected_endpoint_id != self.endpoint.endpoint_id:
            raise TransportError(
                "The CDP daemon endpoint changed before this command ran; "
                "run sessions and attach again."
            )
        if not self.connected:
            raise RuntimeError(
                self.last_error or "Browser CDP WebSocket is disconnected"
            )
        if operation == "list_sessions":
            return await self.list_sessions()
        if operation == "attach_session":
            target_id = request.get("targetId")
            if not isinstance(target_id, str) or not target_id:
                raise UsageError("attach requires a non-empty target id")
            return await self.attach_session(target_id)
        if operation == "detach_session":
            session_id = request.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise UsageError("detach requires a non-empty session id")
            return await self.detach_session(session_id)
        if operation == "cdp":
            session_id = request.get("sessionId")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(session_id, str) or not session_id:
                raise UsageError("call requires a non-empty session id")
            if not isinstance(method, str) or "." not in method:
                raise UsageError("call requires a CDP method such as Runtime.evaluate")
            if not isinstance(params, dict):
                raise UsageError("CDP params must be a JSON object")
            timeout = float(request.get("timeout", 30))
            if not 0 < timeout <= 3600:
                raise UsageError(
                    "timeout must be greater than 0 and at most 3600 seconds"
                )
            response = await self.call_session(session_id, method, params, timeout)
            return {"ok": True, "response": response}
        if operation == "navigate":
            session_id = request.get("sessionId")
            url = request.get("url")
            include_content = request.get("includeContent")
            if not isinstance(session_id, str) or not session_id:
                raise UsageError("navigate requires a non-empty session id")
            if not isinstance(url, str) or not url:
                raise UsageError("navigate requires a non-empty URL")
            if not isinstance(include_content, bool):
                raise UsageError("navigate includeContent must be a boolean")
            try:
                timeout = float(request.get("timeout", 30))
            except (TypeError, ValueError) as error:
                raise UsageError("navigate timeout must be a number") from error
            if not math.isfinite(timeout) or not 0 < timeout <= 3600:
                raise UsageError(
                    "navigate timeout must be greater than 0 and at most 3600 seconds"
                )
            response = await self.navigate_session(
                session_id, url, include_content, timeout
            )
            return {"ok": True, "response": response}
        if operation == "action":
            session_id = request.get("sessionId")
            params = request.get("params")
            observation = request.get("observation")
            if not isinstance(session_id, str) or not session_id:
                raise UsageError("action requires a non-empty session id")
            if not isinstance(params, dict):
                raise UsageError("action requires a params object")
            if not isinstance(observation, str) or observation not in {
                "none",
                "diff",
                "content",
            }:
                raise UsageError(
                    "action observation must be one of: none, diff, content"
                )
            allowed = {"id", "action", "text", "key", "value", "values"}
            if set(params) - allowed:
                raise UsageError("action params contain unsupported fields")
            if "value" in params and "values" in params:
                raise UsageError("action value and values are mutually exclusive")
            node_id = params.get("id")
            action = params.get("action")
            if not isinstance(node_id, int) or isinstance(node_id, bool):
                raise UsageError("action requires an integer node id")
            if not isinstance(action, str) or not action:
                raise UsageError("action requires a non-empty action name")
            for optional in ("text", "key", "value"):
                if optional in params and not isinstance(params[optional], str):
                    raise UsageError(f"action {optional} must be a string")
            if "values" in params and (
                not isinstance(params["values"], list)
                or not all(isinstance(value, str) for value in params["values"])
            ):
                raise UsageError("action values must be an array of strings")
            try:
                poll_timeout = float(request.get("pollTimeout", 3))
            except (TypeError, ValueError) as error:
                raise UsageError("pollTimeout must be a number") from error
            if not math.isfinite(poll_timeout) or poll_timeout < 1:
                raise UsageError(
                    "pollTimeout must be a finite value of at least 1 second"
                )
            response = await self.action_session(
                session_id, params, poll_timeout, observation
            )
            return {"ok": True, "response": response}
        raise UsageError(f"Unknown daemon operation: {operation}")

    async def serve_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = await _read_packet(reader)
            try:
                response = await self.handle_request(request)
            except Exception as error:
                response = error_payload(error)
            await _write_packet(writer, response)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def monitor_port(self) -> None:
        failures = 0
        while not self.shutting_down:
            await asyncio.sleep(self.port_check_interval)
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), timeout=1
                )
                del reader
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                failures = 0
            except (OSError, TimeoutError):
                failures += 1
                if failures >= 2:
                    self.last_error = f"Browser CDP port {self.host}:{self.port} closed"
                    self.connected = False
                    self._fail_pending(self.last_error)
                    self.stop_event.set()
                    return

    async def shutdown(self) -> None:
        self.shutting_down = True
        probes = list(self.session_probe_tasks.values())
        self.session_probe_tasks.clear()
        self.session_probe_targets.clear()
        for task in probes:
            task.cancel()
        if probes:
            await asyncio.gather(*probes, return_exceptions=True)
        if self.port_task is not None:
            self.port_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.port_task
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.close()
        if self.reader_task is not None:
            if not self.reader_task.done():
                self.reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.reader_task
        self.connected = False
        self.sessions.clear()
        self.session_locks.clear()
        self.detaching.clear()
        self.content_baselines.clear()
        self._fail_pending("CDP daemon is shutting down")
        self.persist(running=False)


async def run_daemon(
    paths: RuntimePaths,
    host: str = CDP_HOST,
    port: int = CDP_PORT,
    port_check_interval: float = 0.5,
    *,
    websocket_url: str | None = None,
) -> int:
    daemon = CdpDaemon(
        paths,
        host,
        port,
        port_check_interval,
        websocket_url=websocket_url,
    )
    server: asyncio.AbstractServer | None = None
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, daemon.stop_event.set)
    try:
        write_state(paths, daemon.status(running=True))
        await daemon.start()
        with contextlib.suppress(FileNotFoundError):
            paths.socket.unlink()
        server = await asyncio.start_unix_server(
            daemon.serve_client, path=str(paths.socket)
        )
        os.chmod(paths.socket, 0o600)
        if daemon.endpoint.mode == "default":
            daemon.port_task = asyncio.create_task(
                daemon.monitor_port(), name="cdp-port-monitor"
            )
        append_log(paths, f"daemon ready on {daemon.endpoint.display}")
        daemon.persist(running=True)
        await daemon.stop_event.wait()
        server.close()
        await server.wait_closed()
        server = None
        await daemon.shutdown()
        append_log(paths, "daemon stopped")
        return 0
    except Exception as error:
        daemon.last_error = sanitize(str(error), daemon.endpoint.websocket_url)
        append_log(paths, f"daemon failed: {type(error).__name__}: {daemon.last_error}")
        if server is not None:
            server.close()
            await server.wait_closed()
        with contextlib.suppress(Exception):
            await daemon.shutdown()
        return 1
    finally:
        with contextlib.suppress(FileNotFoundError):
            paths.socket.unlink()


def _acquire_instance_lock(paths: RuntimePaths) -> int:
    fd = os.open(paths.instance_lock, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(fd, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise RuntimeError("Another ace-proto CDP daemon is already running")
    return fd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the private ACE Proto singleton CDP daemon."
    )
    parser.add_argument(
        "--websocket-url-fd",
        type=int,
        help="private inherited file descriptor containing the browser WebSocket URL",
    )
    return parser


def _read_websocket_url_fd(fd: int) -> str:
    if fd < 0:
        raise UsageError("--websocket-url-fd must be a non-negative descriptor")
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            total += len(chunk)
            if total > 64 * 1024:
                raise UsageError("Browser WebSocket URL exceeds 64 KiB")
            chunks.append(chunk)
    except OSError as error:
        raise UsageError("Cannot read the private browser WebSocket URL") from error
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as error:
        raise UsageError("Browser WebSocket URL is not valid UTF-8") from error


def main(argv: list[str] | None = None) -> int:
    if os.name != "posix":
        return 1
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        websocket_url = (
            _read_websocket_url_fd(args.websocket_url_fd)
            if args.websocket_url_fd is not None
            else None
        )
        selected_endpoint = endpoint_config(websocket_url)
    except UsageError as error:
        parser.error(str(error))
    paths = runtime_paths()
    try:
        lock_fd = _acquire_instance_lock(paths)
    except RuntimeError:
        return 3
    try:
        with contextlib.suppress(FileNotFoundError):
            paths.socket.unlink()
        return asyncio.run(
            run_daemon(paths, websocket_url=selected_endpoint.websocket_url)
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
