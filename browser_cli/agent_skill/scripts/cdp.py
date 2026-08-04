#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=15.0.1", "jq>=1.12.0"]
# ///
"""Thin CLI and IPC client for the ACE Proto singleton CDP daemon."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib
import json
import math
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

MAX_IPC_BYTES = 512 * 1024 * 1024
STARTUP_TIMEOUT = 20.0
DEFAULT_CDP_HOST = "127.0.0.1"
DEFAULT_CDP_PORT = 9222
WS_GUID_RE = re.compile(r"(wss?://[^/\s]+/devtools/browser/)[^\s]+")


class AceProtoError(RuntimeError):
    kind = "ace-proto"


class UsageError(AceProtoError):
    kind = "usage"


class TransportError(AceProtoError):
    kind = "transport"


class JqContextError(AceProtoError):
    kind = "jq-context"


@dataclass(frozen=True)
class EndpointConfig:
    mode: str
    endpoint_id: str
    display: str
    websocket_url: str | None


@dataclass(frozen=True)
class RuntimePaths:
    directory: Path
    socket: Path
    startup_lock: Path
    instance_lock: Path
    state: Path
    log: Path


def normalize_websocket_url(raw: str) -> str:
    if not raw:
        raise UsageError("--websocket-url requires a non-empty ws:// or wss:// URL")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in raw
    ):
        raise UsageError(
            "--websocket-url must not contain whitespace or control characters"
        )
    if "#" in raw:
        raise UsageError("--websocket-url must not include a fragment")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise UsageError("--websocket-url has an invalid hostname or port") from error
    scheme = parsed.scheme.lower()
    if scheme not in {"ws", "wss"}:
        raise UsageError("--websocket-url must use the ws:// or wss:// scheme")
    if not parsed.netloc or not parsed.hostname:
        raise UsageError("--websocket-url must include a hostname")
    host_port = parsed.netloc.rpartition("@")[2]
    if host_port.endswith(":"):
        raise UsageError("--websocket-url must include a port after ':'")
    if port == 0:
        raise UsageError("--websocket-url port must be between 1 and 65535")

    hostname = parsed.hostname.lower()
    authority = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 80 if scheme == "ws" else 443
    if port is not None and port != default_port:
        authority = f"{authority}:{port}"
    if parsed.username is not None:
        credentials = parsed.username
        if parsed.password is not None:
            credentials = f"{credentials}:{parsed.password}"
        authority = f"{credentials}@{authority}"
    return urlunsplit((scheme, authority, parsed.path or "/", parsed.query, ""))


def websocket_display(websocket_url: str) -> str:
    parsed = urlsplit(websocket_url)
    hostname = parsed.hostname or "<invalid-host>"
    authority = f"[{hostname}]" if ":" in hostname else hostname
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        authority = f"{authority}:{port}"
    return f"{parsed.scheme.lower()}://{authority}/<redacted>"


def endpoint_config(
    websocket_url: str | None,
    default_host: str = DEFAULT_CDP_HOST,
    default_port: int = DEFAULT_CDP_PORT,
) -> EndpointConfig:
    if websocket_url is None:
        mode = "default"
        connection = f"http://{default_host}:{default_port}"
        display = connection
        normalized = None
    else:
        mode = "websocket"
        normalized = normalize_websocket_url(websocket_url)
        connection = normalized
        display = websocket_display(normalized)
    endpoint_id = hashlib.sha256(connection.encode("utf-8")).hexdigest()
    return EndpointConfig(mode, endpoint_id, display, normalized)


def _validate_lexmount_session_id(session_id: str) -> str:
    if not session_id:
        raise UsageError("--lexmount-session-id requires a non-empty session ID")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in session_id
    ):
        raise UsageError(
            "--lexmount-session-id must not contain whitespace or control characters"
        )
    return session_id


def lexmount_endpoint_config(session_id: str) -> EndpointConfig:
    """Resolve one Lexmount session without exposing its CDP URL."""

    selected_session_id = _validate_lexmount_session_id(session_id)
    command = [
        "browser-cli",
        "session",
        "get",
        "--session-id",
        selected_session_id,
        "--reveal-connect-url",
    ]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as error:
        raise TransportError(
            "browser-cli is unavailable; install it before using "
            "--lexmount-session-id"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise TransportError(
            "browser-cli session get timed out while resolving the Lexmount session"
        ) from error
    except OSError as error:
        raise TransportError(
            "browser-cli could not resolve the Lexmount session"
        ) from error

    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError) as error:
        raise TransportError(
            "browser-cli session get returned invalid JSON"
        ) from error
    if not isinstance(payload, dict):
        raise TransportError("browser-cli session get returned a non-object result")
    if completed.returncode != 0 or payload.get("ok") is not True:
        error_code = payload.get("error")
        suffix = (
            f" ({error_code})"
            if isinstance(error_code, str)
            and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", error_code)
            else ""
        )
        raise TransportError(
            f"browser-cli could not resolve the Lexmount session{suffix}"
        )

    session = payload.get("session")
    if not isinstance(session, dict):
        raise TransportError("browser-cli session get returned no session object")
    returned_session_id = session.get("session_id")
    if returned_session_id != selected_session_id:
        raise TransportError(
            "browser-cli session get returned a different Lexmount session ID"
        )
    status = session.get("status")
    if not isinstance(status, str) or status.lower() != "active":
        raise TransportError("The Lexmount session is not active")
    websocket_url = session.get("connect_url")
    if not isinstance(websocket_url, str):
        raise TransportError(
            "browser-cli session get returned no revealed CDP WebSocket URL"
        )
    return endpoint_config(websocket_url)


def sanitize(text: str, websocket_url: str | None = None) -> str:
    if websocket_url:
        text = text.replace(websocket_url, websocket_display(websocket_url))
    return WS_GUID_RE.sub(r"\1<redacted>", text)


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_bytes(value: Any) -> bytes:
    return compact_json(value).encode("utf-8")


def print_json(value: Any) -> None:
    sys.stdout.write(compact_json(value))


def error_payload(error: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": getattr(error, "kind", "internal"),
        "message": sanitize(str(error)),
    }
    code = getattr(error, "code", None)
    if code is not None:
        payload["code"] = code
    data = getattr(error, "data", None)
    if data is not None:
        payload["data"] = data
    return {"ok": False, "error": payload}


def private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    stat = path.lstat()
    if not path.is_dir() or path.is_symlink():
        raise TransportError(f"Runtime path is not a real directory: {path}")
    if stat.st_uid != os.getuid():
        raise TransportError(f"Runtime path is not owned by the current user: {path}")
    os.chmod(path, 0o700)
    return path


def runtime_paths() -> RuntimePaths:
    # Deliberately ignore XDG/runtime environment variables. A fixed per-UID path
    # is what makes the daemon a singleton across independent shells.
    root = private_directory(Path("/tmp") / f"ace-proto-{os.getuid()}")
    directory = private_directory(root / "singleton")
    socket_path = directory / "daemon.sock"
    if len(os.fsencode(socket_path)) >= 100:
        raise TransportError(f"Unix socket path is too long: {socket_path}")
    return RuntimePaths(
        directory=directory,
        socket=socket_path,
        startup_lock=directory / "startup.lock",
        instance_lock=directory / "instance.lock",
        state=directory / "state.json",
        log=directory / "daemon.log",
    )


@contextlib.contextmanager
def startup_lock(paths: RuntimePaths) -> Iterator[None]:
    fd = os.open(paths.startup_lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def instance_lock_is_held(paths: RuntimePaths) -> bool:
    fd = os.open(paths.instance_lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def read_state(paths: RuntimePaths) -> dict[str, Any] | None:
    try:
        state = json.loads(paths.state.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def write_state(paths: RuntimePaths, state: dict[str, Any]) -> None:
    temporary = paths.state.with_name(f"state.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        os.write(fd, json_bytes(state))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, paths.state)
    os.chmod(paths.state, 0o600)


def append_log(paths: RuntimePaths, message: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {sanitize(message)}\n"
    fd = os.open(paths.log, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        os.write(fd, line.encode("utf-8", errors="replace"))
    finally:
        os.close(fd)


def _recv_exact(connection: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = connection.recv(min(remaining, 1024 * 1024))
        if not chunk:
            raise TransportError("Daemon closed the IPC connection early")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def ipc_request(
    paths: RuntimePaths,
    request: dict[str, Any],
    timeout: float = 5.0,
) -> dict[str, Any]:
    body = json_bytes(request)
    if len(body) > MAX_IPC_BYTES:
        raise TransportError("IPC request exceeds 512 MiB")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(timeout)
    except OverflowError:
        # action intentionally has no product-level maximum polling window.
        # A blocking socket is safer than timing out before a huge valid window.
        connection.settimeout(None)
    try:
        connection.connect(str(paths.socket))
        connection.sendall(struct.pack("!I", len(body)) + body)
        header = _recv_exact(connection, 4)
        (length,) = struct.unpack("!I", header)
        if length > MAX_IPC_BYTES:
            raise TransportError("IPC response exceeds 512 MiB")
        response = json.loads(_recv_exact(connection, length))
    except (OSError, TimeoutError, ValueError) as error:
        if isinstance(error, AceProtoError):
            raise
        raise TransportError(
            f"Cannot communicate with the CDP daemon: {error}"
        ) from error
    finally:
        connection.close()
    if not isinstance(response, dict):
        raise TransportError("Daemon returned a non-object response")
    return response


def ping(paths: RuntimePaths, timeout: float = 0.75) -> dict[str, Any] | None:
    try:
        response = ipc_request(paths, {"op": "ping"}, timeout)
    except AceProtoError:
        return None
    if (
        response.get("ok") is True
        and response.get("pong") is True
        and response.get("connected") is True
    ):
        return response
    return None


def _ready_status(ready: dict[str, Any]) -> dict[str, Any]:
    status = ready.get("status")
    return status if isinstance(status, dict) else {}


def ready_endpoint_id(ready: dict[str, Any]) -> str | None:
    endpoint_id = _ready_status(ready).get("endpointId")
    if isinstance(endpoint_id, str) and re.fullmatch(r"[0-9a-f]{64}", endpoint_id):
        return endpoint_id
    return None


def ready_endpoint_display(ready: dict[str, Any]) -> str:
    endpoint = _ready_status(ready).get("endpoint")
    return endpoint if isinstance(endpoint, str) else "<unknown endpoint>"


def endpoint_matches(ready: dict[str, Any], endpoint: EndpointConfig) -> bool:
    return ready_endpoint_id(ready) == endpoint.endpoint_id


def require_endpoint_match(ready: dict[str, Any], endpoint: EndpointConfig) -> None:
    if endpoint_matches(ready, endpoint):
        return
    raise TransportError(
        "The CDP daemon is connected to "
        f"{ready_endpoint_display(ready)}, not the requested {endpoint.display}. "
        "Run sessions or attach with that --websocket-url to switch daemons."
    )


def _terminate_startup_process(process: subprocess.Popen[Any]) -> None:
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=2)


def _spawn_daemon(paths: RuntimePaths, endpoint: EndpointConfig) -> dict[str, Any]:
    daemon_script = Path(__file__).with_name("cdp_daemon.py")
    if not daemon_script.is_file():
        raise TransportError(f"Bundled daemon script is missing: {daemon_script}")
    command = [sys.executable, str(daemon_script)]
    read_fd: int | None = None
    write_fd: int | None = None
    pass_fds: tuple[int, ...] = ()
    if endpoint.websocket_url is not None:
        read_fd, write_fd = os.pipe()
        os.set_inheritable(read_fd, True)
        command.extend(("--websocket-url-fd", str(read_fd)))
        pass_fds = (read_fd,)
    log_fd: int | None = None
    try:
        log_fd = os.open(paths.log, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=log_fd,
                start_new_session=True,
                close_fds=True,
                pass_fds=pass_fds,
            )
        finally:
            if read_fd is not None:
                os.close(read_fd)
                read_fd = None
    except BaseException:
        if write_fd is not None:
            os.close(write_fd)
            write_fd = None
        raise
    finally:
        if log_fd is not None:
            os.close(log_fd)

    if write_fd is not None:
        try:
            secret = endpoint.websocket_url.encode("utf-8")
            while secret:
                written = os.write(write_fd, secret)
                secret = secret[written:]
        except BaseException:
            _terminate_startup_process(process)
            raise
        finally:
            os.close(write_fd)
            write_fd = None

    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if ready := ping(paths):
            return ready
        if process.poll() is not None:
            # A manually started daemon may have won the lifetime lock race.
            if ready := ping(paths):
                return ready
            if instance_lock_is_held(paths):
                time.sleep(0.05)
                continue
            state = read_state(paths) or {}
            raise TransportError(
                str(state.get("lastError") or "CDP daemon exited during startup")
            )
        time.sleep(0.05)

    _terminate_startup_process(process)
    # Never unlink a socket that may belong to a manually started winner.
    if not instance_lock_is_held(paths):
        with contextlib.suppress(FileNotFoundError):
            paths.socket.unlink()
    raise TransportError("CDP daemon did not become ready within 20 seconds")


def _daemon_pid(ready: dict[str, Any]) -> int:
    pid = _ready_status(ready).get("pid")
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 1
        or pid == os.getpid()
    ):
        raise TransportError("The running CDP daemon reported an invalid process ID")
    return pid


def _stop_daemon_for_switch(paths: RuntimePaths, ready: dict[str, Any]) -> None:
    pid = _daemon_pid(ready)
    expected_endpoint_id = ready_endpoint_id(ready)
    refreshed = ping(paths)
    if refreshed is None:
        return
    if (
        _daemon_pid(refreshed) != pid
        or ready_endpoint_id(refreshed) != expected_endpoint_id
    ):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as error:
        raise TransportError(f"Cannot stop the current CDP daemon: {error}") from error

    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        current = ping(paths)
        if current is not None and _daemon_pid(current) != pid:
            return
        if current is None and not instance_lock_is_held(paths):
            return
        time.sleep(0.05)
    raise TransportError("The current CDP daemon did not stop within 20 seconds")


def _wait_for_daemon_or_unlock(
    paths: RuntimePaths,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if ready := ping(paths):
            return ready
        if not instance_lock_is_held(paths):
            return None
        time.sleep(0.05)
    if instance_lock_is_held(paths):
        raise TransportError(
            "The singleton CDP daemon is running but its IPC socket is unresponsive"
        )
    return None


def _ensure_daemon_locked(
    paths: RuntimePaths, endpoint: EndpointConfig
) -> dict[str, Any]:
    for _attempt in range(4):
        ready = ping(paths)
        if ready is None and instance_lock_is_held(paths):
            ready = _wait_for_daemon_or_unlock(paths)
        if ready is not None:
            if endpoint_matches(ready, endpoint):
                return ready
            _stop_daemon_for_switch(paths, ready)
            continue

        with contextlib.suppress(FileNotFoundError):
            paths.socket.unlink()
        ready = _spawn_daemon(paths, endpoint)
        if endpoint_matches(ready, endpoint):
            return ready
        _stop_daemon_for_switch(paths, ready)
    raise TransportError(
        "The selected CDP endpoint kept changing during daemon startup"
    )


def ensure_daemon(
    paths: RuntimePaths, endpoint: EndpointConfig | None = None
) -> dict[str, Any]:
    selected = endpoint or endpoint_config(None)
    with startup_lock(paths):
        return _ensure_daemon_locked(paths, selected)


def require_daemon(paths: RuntimePaths) -> dict[str, Any]:
    ready = ping(paths)
    if ready is None:
        raise TransportError(
            "The CDP daemon is not running; previous session IDs are invalid. "
            "Run sessions, then attach the target again."
        )
    return ready


def guarded_ipc_request(
    paths: RuntimePaths,
    endpoint_id: str,
    request: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    guarded = dict(request)
    guarded["endpointId"] = endpoint_id
    return ipc_request(paths, guarded, timeout)


def run_selection_command(
    paths: RuntimePaths,
    command: str,
    target_id: str | None,
    endpoint: EndpointConfig,
) -> tuple[dict[str, Any], int]:
    with startup_lock(paths):
        _ensure_daemon_locked(paths, endpoint)
        if command == "sessions":
            payload = guarded_ipc_request(
                paths,
                endpoint.endpoint_id,
                {"op": "list_sessions"},
                timeout=15,
            )
        else:
            assert command == "attach" and target_id is not None
            payload = guarded_ipc_request(
                paths,
                endpoint.endpoint_id,
                {
                    "op": "attach_session",
                    "targetId": target_id,
                },
                timeout=30,
            )
    return payload, 0 if payload.get("ok") else 1


def load_params(raw: str | None, params_file: str | None) -> dict[str, Any]:
    if raw is None and params_file is None:
        return {}
    try:
        if params_file is not None:
            text = (
                sys.stdin.read()
                if params_file == "-"
                else Path(params_file).read_text(encoding="utf-8")
            )
        else:
            assert raw is not None
            text = raw
        value = json.loads(text)
    except (OSError, ValueError) as error:
        raise UsageError(f"Cannot read CDP params JSON: {error}") from error
    if not isinstance(value, dict):
        raise UsageError("CDP params must be a JSON object")
    return value


def compile_jq_context(expression: str) -> Any:
    try:
        jq_module = importlib.import_module("jq")
    except ImportError as error:
        raise JqContextError("Python jq package is unavailable") from error
    try:
        return jq_module.compile(expression)
    except ValueError as error:
        message = str(error).strip() or "jq context compilation failed"
        raise JqContextError(message) from error


def jq_context_payload(response: dict[str, Any], program: Any) -> dict[str, Any]:
    result = response.get("result")
    if not isinstance(result, dict) or "content" not in result:
        raise JqContextError("Page.getAIPageContent returned no result.content")
    content = result["content"]
    try:
        contexts = program.input_value(content).all()
    except ValueError as error:
        message = str(error).strip() or "jq context evaluation failed"
        raise JqContextError(message) from error

    return {
        "contexts": contexts,
        "fullJsonBytes": len(json_bytes(response)),
    }


def page_content_outline(response: dict[str, Any]) -> str:
    result = response.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("content"), dict):
        raise TransportError("Page.getAIPageContent returned no result.content tree")

    fragments: list[str] = []
    stack = [result["content"]]
    while stack:
        node = stack.pop()
        element_text = " | ".join(
            value
            for field in ("text", "label", "placeholder")
            if isinstance((value := node.get(field)), str) and value
        )
        node_id = node.get("id")
        if node_id is not None:
            actions = node.get("action")
            if isinstance(actions, list):
                action_text = ",".join(str(action) for action in actions)
            elif actions is None:
                action_text = ""
            else:
                action_text = str(actions)
            fragments.append(f"[{node_id}|{action_text}]{element_text}\n")
        elif element_text:
            fragments.append(f"{element_text}\n")

        children = node.get("children", [])
        if not isinstance(children, list) or not all(
            isinstance(child, dict) for child in children
        ):
            raise TransportError(
                "Page.getAIPageContent returned an invalid content tree"
            )
        stack.extend(reversed(children))

    return "".join(fragments)


def add_action_content_observation(
    payload: dict[str, Any],
    response: Any,
    *,
    outline: bool,
    jq_program: Any | None,
) -> None:
    try:
        if not isinstance(response, dict):
            if outline:
                raise TransportError(
                    "Page.getAIPageContent returned a non-object response"
                )
            raise JqContextError("Page.getAIPageContent returned a non-object response")
        if outline:
            payload["outline"] = page_content_outline(response)
            payload["fullJsonBytes"] = len(json_bytes(response))
        else:
            assert jq_program is not None
            payload.update(jq_context_payload(response, jq_program))
    except AceProtoError as error:
        error.data = payload
        raise


def _validate_call_timeout(timeout: float) -> None:
    if not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise UsageError("--timeout must be greater than 0 and at most 3600 seconds")


def _validate_poll_timeout(timeout: float) -> None:
    if not math.isfinite(timeout) or timeout < 1:
        raise UsageError("--timeout must be a finite value of at least 1 second")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Send CDP operations through one daemon connected to an explicit "
            "browser WebSocket or, by default, 127.0.0.1:9222"
        )
    )
    endpoint_selection = parser.add_mutually_exclusive_group()
    endpoint_selection.add_argument(
        "--websocket-url",
        help=(
            "browser-level ws:// or wss:// URL; place before the command "
            "(sessions and attach may switch daemons)"
        ),
    )
    endpoint_selection.add_argument(
        "--lexmount-session-id",
        help=(
            "resolve a Lexmount session's browser WebSocket locally through "
            "browser-cli; valid only for sessions and attach"
        ),
    )
    commands = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{sessions,attach,detach,call,content,navigate,action}",
    )
    commands.add_parser(
        "sessions", help="list browser targets and daemon-owned sessions"
    )

    attach = commands.add_parser(
        "attach", help="attach a flattened session to a target"
    )
    attach.add_argument("target_id")

    detach = commands.add_parser("detach", help="detach one daemon-owned session")
    detach.add_argument("session_id")

    call = commands.add_parser(
        "call", help="send an arbitrary CDP command to a session"
    )
    call.add_argument("session_id")
    call.add_argument("method")
    params = call.add_mutually_exclusive_group()
    params.add_argument("--params", help="CDP params JSON object")
    params.add_argument("--params-file", help="JSON file, or - for stdin")
    call.add_argument("--timeout", type=float, default=30.0)

    content = commands.add_parser("content", help="read fresh ACE page content")
    content.add_argument("session_id")
    content.add_argument(
        "--format",
        choices=("outline", "json"),
        required=True,
        help="required output format",
    )
    content.add_argument(
        "--jq-context",
        dest="jq_context",
        metavar="JQ_EXPRESSION",
        help="jq expression for --format=json",
    )
    content.add_argument("--timeout", type=float, default=30.0)

    navigate = commands.add_parser(
        "navigate", help="navigate and optionally read loaded ACE page content"
    )
    navigate.add_argument("session_id")
    navigate.add_argument("url")
    navigate_output = navigate.add_mutually_exclusive_group(required=True)
    navigate_output.add_argument(
        "--format",
        choices=("outline", "json"),
        help="output format for the loaded page content",
    )
    navigate_output.add_argument(
        "--no-content",
        action="store_true",
        help="return only the raw Page.navigate response",
    )
    navigate.add_argument(
        "--jq-context",
        dest="jq_context",
        metavar="JQ_EXPRESSION",
        help="jq expression for --format=json",
    )
    navigate.add_argument("--timeout", type=float, default=30.0)

    action = commands.add_parser(
        "action", help="perform and observe one atomic ACE page action"
    )
    action.add_argument("session_id")
    action.add_argument("node_id", type=int)
    action.add_argument("action")
    action.add_argument("--text")
    action.add_argument("--key")
    action_selection = action.add_mutually_exclusive_group()
    action_selection.add_argument("--value")
    action_selection.add_argument("--values", nargs="*", metavar="VALUE")
    action_observation = action.add_mutually_exclusive_group()
    action_observation.add_argument("--diff", action="store_true")
    action_observation.add_argument(
        "--outline",
        action="store_true",
        help="return a compact outline from the fresh post-action page",
    )
    action_observation.add_argument(
        "--jq-context", dest="jq_context", metavar="JQ_EXPRESSION"
    )
    action.add_argument("--timeout", type=float, default=3.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    if os.name != "posix":
        print_json(error_payload(UsageError("ace-proto requires POSIX Unix sockets")))
        return 1
    parser = build_parser()
    args = parser.parse_args(argv)
    if (
        args.lexmount_session_id is not None
        and args.command not in {"sessions", "attach"}
    ):
        parser.error("--lexmount-session-id is valid only for sessions and attach")
    if (
        args.command in {"content", "navigate"}
        and args.jq_context is not None
        and args.format != "json"
    ):
        parser.error("--jq-context requires --format=json")
    try:
        output_text: str | None = None
        requested_endpoint = (
            lexmount_endpoint_config(args.lexmount_session_id)
            if args.lexmount_session_id is not None
            else endpoint_config(args.websocket_url)
        )
        paths = runtime_paths()
        if args.command in {"sessions", "attach"}:
            payload, exit_code = run_selection_command(
                paths,
                args.command,
                getattr(args, "target_id", None),
                requested_endpoint,
            )
            print_json(payload)
            return exit_code

        ready = require_daemon(paths)
        if args.websocket_url is not None:
            require_endpoint_match(ready, requested_endpoint)
        expected_endpoint_id = ready_endpoint_id(ready)
        if expected_endpoint_id is None:
            raise TransportError(
                "The running CDP daemon does not report its endpoint identity; "
                "run sessions and attach again."
            )

        if args.command == "detach":
            payload = guarded_ipc_request(
                paths,
                expected_endpoint_id,
                {
                    "op": "detach_session",
                    "sessionId": args.session_id,
                },
                timeout=15,
            )
            exit_code = 0 if payload.get("ok") else 1
        elif args.command == "call":
            _validate_call_timeout(args.timeout)
            response = guarded_ipc_request(
                paths,
                expected_endpoint_id,
                {
                    "op": "cdp",
                    "sessionId": args.session_id,
                    "method": args.method,
                    "params": load_params(args.params, args.params_file),
                    "timeout": args.timeout,
                },
                timeout=args.timeout + 5,
            )
            if response.get("ok") is True:
                payload = response["response"]
                exit_code = 2 if isinstance(payload, dict) and "error" in payload else 0
            else:
                payload = response
                exit_code = 1
        elif args.command == "content":
            _validate_call_timeout(args.timeout)
            response = guarded_ipc_request(
                paths,
                expected_endpoint_id,
                {
                    "op": "cdp",
                    "sessionId": args.session_id,
                    "method": "Page.getAIPageContent",
                    "params": {"includeDebugInfo": False},
                    "timeout": args.timeout,
                },
                timeout=args.timeout + 5,
            )
            if response.get("ok") is True:
                payload = response["response"]
                if isinstance(payload, dict) and "error" in payload:
                    exit_code = 2
                else:
                    if args.format == "outline":
                        if not isinstance(payload, dict):
                            raise TransportError(
                                "Page.getAIPageContent returned a non-object response"
                            )
                        output_text = page_content_outline(payload)
                    elif args.jq_context is not None:
                        if not isinstance(payload, dict):
                            raise JqContextError(
                                "Page.getAIPageContent returned a non-object response"
                            )
                        payload = jq_context_payload(
                            payload, compile_jq_context(args.jq_context)
                        )
                    exit_code = 0
            else:
                payload = response
                exit_code = 1
        elif args.command == "navigate":
            _validate_call_timeout(args.timeout)
            jq_program = (
                compile_jq_context(args.jq_context)
                if args.jq_context is not None
                else None
            )
            response = guarded_ipc_request(
                paths,
                expected_endpoint_id,
                {
                    "op": "navigate",
                    "sessionId": args.session_id,
                    "url": args.url,
                    "includeContent": not args.no_content,
                    "timeout": args.timeout,
                },
                timeout=args.timeout + 5,
            )
            if response.get("ok") is True:
                navigate_response = response["response"]
                if not isinstance(navigate_response, dict):
                    raise TransportError("The navigate response is not a JSON object")
                if "navigateResponse" not in navigate_response:
                    raise TransportError(
                        "The navigate response contains no Page.navigate response"
                    )
                if "contentResponse" in navigate_response:
                    payload = navigate_response["contentResponse"]
                    if isinstance(payload, dict) and "error" in payload:
                        exit_code = 2
                    else:
                        if args.format == "outline":
                            if not isinstance(payload, dict):
                                raise TransportError(
                                    "Page.getAIPageContent returned a non-object "
                                    "response"
                                )
                            output_text = page_content_outline(payload)
                        elif args.jq_context is not None:
                            if not isinstance(payload, dict):
                                raise JqContextError(
                                    "Page.getAIPageContent returned a non-object "
                                    "response"
                                )
                            assert jq_program is not None
                            payload = jq_context_payload(payload, jq_program)
                        exit_code = 0
                else:
                    payload = navigate_response["navigateResponse"]
                    exit_code = (
                        2
                        if isinstance(payload, dict) and "error" in payload
                        else 0
                    )
            else:
                payload = response
                exit_code = 1
        elif args.command == "action":
            _validate_poll_timeout(args.timeout)
            if args.jq_context is not None:
                jq_program = compile_jq_context(args.jq_context)
                observation = "content"
            elif args.outline:
                jq_program = None
                observation = "content"
            elif args.diff:
                jq_program = None
                observation = "diff"
            else:
                jq_program = None
                observation = "none"
            params: dict[str, Any] = {"id": args.node_id, "action": args.action}
            if args.text is not None:
                params["text"] = args.text
            if args.key is not None:
                params["key"] = args.key
            if args.value is not None:
                params["value"] = args.value
            if args.values is not None:
                params["values"] = args.values
            response = guarded_ipc_request(
                paths,
                expected_endpoint_id,
                {
                    "op": "action",
                    "sessionId": args.session_id,
                    "params": params,
                    "pollTimeout": args.timeout,
                    "observation": observation,
                },
                timeout=args.timeout + 95,
            )
            if response.get("ok") is True:
                action_response = response["response"]
                if not isinstance(action_response, dict):
                    raise TransportError("The action response is not a JSON object")
                payload = {
                    key: action_response[key]
                    for key in ("perform", "changes")
                    if key in action_response
                }
                if observation == "diff":
                    if "contentDiff" in action_response:
                        payload["contentDiff"] = action_response["contentDiff"]
                elif observation == "content" and "contentResponse" in action_response:
                    content_response = action_response["contentResponse"]
                    add_action_content_observation(
                        payload,
                        content_response,
                        outline=args.outline,
                        jq_program=jq_program,
                    )
                exit_code = 0
            else:
                payload = response
                exit_code = 1
        else:  # pragma: no cover - argparse enforces the command set
            raise UsageError(f"Unknown command: {args.command}")
        if output_text is None:
            print_json(payload)
        else:
            sys.stdout.write(output_text)
        return exit_code
    except KeyboardInterrupt:
        print_json(error_payload(TransportError("Interrupted")))
        return 130
    except Exception as error:
        print_json(error_payload(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
