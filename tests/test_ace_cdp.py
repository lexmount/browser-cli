from __future__ import annotations

import asyncio
import base64
import contextlib
import fcntl
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from typing import Any, Callable


SKILL_ROOT = Path(__file__).resolve().parents[1]
CLI = SKILL_ROOT / "scripts" / "cdp.py"
DAEMON = SKILL_ROOT / "scripts" / "cdp_daemon.py"
UV = shutil.which("uv") or "uv"
NETNS_MARKER = "ACE_PROTO_TEST_NETNS"
IN_TEST_NETNS = os.environ.get(NETNS_MARKER) == "1"
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _read_exact(stream: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("websocket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_frame(stream: Any) -> tuple[int, bytes]:
    first, second = _read_exact(stream, 2)
    opcode = first & 0x0F
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", _read_exact(stream, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _read_exact(stream, 8))[0]
    mask = _read_exact(stream, 4) if second & 0x80 else None
    payload = _read_exact(stream, length)
    if mask:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return opcode, payload


def _frame(opcode: int, payload: bytes = b"") -> bytes:
    first = 0x80 | opcode
    size = len(payload)
    if size < 126:
        header = bytes((first, size))
    elif size <= 0xFFFF:
        header = bytes((first, 126)) + struct.pack("!H", size)
    else:
        header = bytes((first, 127)) + struct.pack("!Q", size)
    return header + payload


class _WebSocketPeer:
    def __init__(self, server: "FakeCDPServer", connection: socket.socket, number: int):
        self.server = server
        self.connection = connection
        self.number = number
        self.closed = False
        self._write_lock = threading.Lock()

    def send_json(self, value: dict[str, Any]) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        with self._write_lock:
            if self.closed:
                return
            try:
                self.connection.sendall(_frame(0x1, payload))
            except OSError:
                self.closed = True

    def send_frame(self, opcode: int, payload: bytes = b"") -> None:
        with self._write_lock:
            if self.closed:
                return
            try:
                self.connection.sendall(_frame(opcode, payload))
            except OSError:
                self.closed = True

    def close(self) -> None:
        self.closed = True
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.connection.close()
        except OSError:
            pass


class FakeCDPServer(http.server.ThreadingHTTPServer):
    """A browser-level CDP endpoint, optionally on an ephemeral test port."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int = 9222) -> None:
        super().__init__(("127.0.0.1", port), _FakeCDPHandler)
        self.targets = [
            self.target("page-a", "page", "First page"),
            self.target("worker-a", "worker", "Worker"),
        ]
        self.commands: list[dict[str, Any]] = []
        self.http_paths: list[str] = []
        self.peers: dict[int, _WebSocketPeer] = {}
        self.connection_count = 0
        self.session_counter = 0
        self.sessions: dict[str, str] = {}
        self.flat_sessions: set[str] = set()
        self.ignored_methods: set[str] = set()
        self.scripted_responses: dict[str, list[dict[str, Any]]] = {}
        self.browser_close_received = False
        self.browser_crash_received = False
        self.expose_protocol_received = False
        self._condition = threading.Condition()
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._listening = False

    @staticmethod
    def target(target_id: str, target_type: str, title: str) -> dict[str, Any]:
        return {
            "targetId": target_id,
            "type": target_type,
            "title": title,
            "url": f"https://example.test/{target_id}",
            "attached": False,
            "canAccessOpener": False,
        }

    @property
    def websocket_url(self) -> str:
        return f"ws://127.0.0.1:{self.server_address[1]}/devtools/browser/fake-guid"

    def start(self) -> "FakeCDPServer":
        self._listening = True
        self._thread.start()
        return self

    def stop_listener(self) -> None:
        """Close port 9222 while intentionally leaving accepted WebSockets alive."""
        if not self._listening:
            return
        self._listening = False
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=3)

    def close_peers(self) -> None:
        with self._condition:
            peers = list(self.peers.values())
        for peer in peers:
            peer.close()

    def stop(self) -> None:
        self.stop_listener()
        self.close_peers()

    def register_http(self, path: str) -> None:
        with self._condition:
            self.http_paths.append(path)
            self._condition.notify_all()

    def register_peer(self, connection: socket.socket) -> _WebSocketPeer:
        with self._condition:
            self.connection_count += 1
            number = self.connection_count
            peer = _WebSocketPeer(self, connection, number)
            self.peers[number] = peer
            self._condition.notify_all()
            return peer

    def unregister_peer(self, peer: _WebSocketPeer) -> None:
        peer.closed = True
        with self._condition:
            self.peers.pop(peer.number, None)
            self._condition.notify_all()

    def register_command(self, peer: _WebSocketPeer, request: dict[str, Any]) -> None:
        record = dict(request)
        record["_connection"] = peer.number
        record["_received_at"] = time.monotonic()
        with self._condition:
            self.commands.append(record)
            self._condition.notify_all()

    def add_target(self, target_id: str, target_type: str = "page") -> None:
        with self._condition:
            self.targets.append(
                self.target(target_id, target_type, f"Target {target_id}")
            )
            self._condition.notify_all()

    def queue_result(
        self,
        method: str,
        value: dict[str, Any],
        *,
        delay: float = 0,
    ) -> None:
        with self._condition:
            self.scripted_responses.setdefault(method, []).append(
                {"result": value, "delay": delay}
            )

    def queue_error(
        self,
        method: str,
        message: str = "intentional scripted failure",
        *,
        code: int = -32000,
        delay: float = 0,
    ) -> None:
        with self._condition:
            self.scripted_responses.setdefault(method, []).append(
                {"error": {"code": code, "message": message}, "delay": delay}
            )

    def _take_scripted_response(self, method: str) -> dict[str, Any] | None:
        with self._condition:
            queued = self.scripted_responses.get(method)
            if not queued:
                return None
            scripted = queued.pop(0)
            if not queued:
                self.scripted_responses.pop(method, None)
            return scripted

    def emit_event(
        self,
        method: str,
        params: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> None:
        with self._condition:
            if method == "Target.detachedFromTarget":
                detached = params.get("sessionId")
                if isinstance(detached, str):
                    self.sessions.pop(detached, None)
                    self.flat_sessions.discard(detached)
            elif method == "Target.targetDestroyed":
                target_id = params.get("targetId")
                if isinstance(target_id, str):
                    self.targets = [
                        target
                        for target in self.targets
                        if target.get("targetId") != target_id
                    ]
                    removed = [
                        attached_session
                        for attached_session, attached_target in self.sessions.items()
                        if attached_target == target_id
                    ]
                    for attached_session in removed:
                        self.sessions.pop(attached_session, None)
                        self.flat_sessions.discard(attached_session)
            peers = list(self.peers.values())
        event: dict[str, Any] = {"method": method, "params": params}
        if session_id is not None:
            event["sessionId"] = session_id
        for peer in peers:
            peer.send_json(event)

    def matching_commands(self, method: str) -> list[dict[str, Any]]:
        with self._condition:
            return [
                command for command in self.commands if command.get("method") == method
            ]

    def wait_for(
        self,
        predicate: Callable[["FakeCDPServer"], bool],
        timeout: float = 8.0,
    ) -> None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while not predicate(self):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(
                        f"condition not met; commands={self.commands!r}, "
                        f"connections={self.connection_count}, active={len(self.peers)}"
                    )
                self._condition.wait(remaining)

    def dispatch(self, peer: _WebSocketPeer, request: dict[str, Any]) -> None:
        self.register_command(peer, request)
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        session_id = request.get("sessionId")

        if method in self.ignored_methods:
            return

        def result(value: dict[str, Any]) -> None:
            response: dict[str, Any] = {"id": request_id, "result": value}
            if session_id is not None:
                response["sessionId"] = session_id
            peer.send_json(response)

        if session_id is not None and session_id not in self.flat_sessions:
            peer.send_json(
                {
                    "id": request_id,
                    "sessionId": session_id,
                    "error": {
                        "code": -32001,
                        "message": "Session is not available through flat routing",
                    },
                }
            )
            return

        scripted = self._take_scripted_response(str(method))
        if scripted is not None:

            def send_scripted() -> None:
                delay = float(scripted.get("delay", 0))
                if delay:
                    time.sleep(delay)
                response: dict[str, Any] = {"id": request_id}
                if "error" in scripted:
                    response["error"] = scripted["error"]
                else:
                    response["result"] = scripted.get("result", {})
                if session_id is not None:
                    response["sessionId"] = session_id
                peer.send_json(response)

            if scripted.get("delay"):
                threading.Thread(target=send_scripted, daemon=True).start()
            else:
                send_scripted()
            return

        if method == "Target.getTargets":
            result({"targetInfos": self.targets})
            return
        if method == "Target.attachToTarget":
            target_id = params.get("targetId")
            if not any(item["targetId"] == target_id for item in self.targets):
                peer.send_json(
                    {
                        "id": request_id,
                        "error": {"code": -32602, "message": "No such target"},
                    }
                )
                return
            self.session_counter += 1
            attached_session = f"session-{target_id}-{self.session_counter}"
            self.sessions[attached_session] = str(target_id)
            if params.get("flatten") is True:
                self.flat_sessions.add(attached_session)
            event: dict[str, Any] = {
                "method": "Target.attachedToTarget",
                "params": {
                    "sessionId": attached_session,
                    "targetInfo": next(
                        item for item in self.targets if item["targetId"] == target_id
                    ),
                    "waitingForDebugger": False,
                },
            }
            if session_id is not None:
                event["sessionId"] = session_id
            peer.send_json(event)
            result({"sessionId": attached_session})
            return
        if method == "Target.detachFromTarget":
            detached_session = params.get("sessionId")
            if detached_session:
                self.sessions.pop(str(detached_session), None)
                self.flat_sessions.discard(str(detached_session))
            result({})
            return
        if method == "Target.setAutoAttach":
            if params.get("autoAttach") is True:
                self.session_counter += 1
                attached_session = f"auto-session-{self.session_counter}"
                target_id = f"auto-target-{self.session_counter}"
                self.sessions[attached_session] = target_id
                if params.get("flatten") is True:
                    self.flat_sessions.add(attached_session)
                event: dict[str, Any] = {
                    "method": "Target.attachedToTarget",
                    "params": {
                        "sessionId": attached_session,
                        "targetInfo": self.target(target_id, "worker", "Auto worker"),
                        "waitingForDebugger": False,
                    },
                }
                if session_id is not None:
                    event["sessionId"] = session_id
                # Chromium may emit attachedToTarget before acknowledging the
                # setAutoAttach command. Exercise that ordering explicitly.
                peer.send_json(event)
            result({})
            return
        if method == "Test.delayedEcho":
            delay = float(params.get("delay", 0))

            def respond_later() -> None:
                time.sleep(delay)
                result({"tag": params.get("tag")})

            threading.Thread(target=respond_later, daemon=True).start()
            return
        if method == "Test.neverRespond":
            return
        if method == "Test.protocolError":
            response = {
                "id": request_id,
                "error": {"code": -32000, "message": "intentional failure"},
            }
            if session_id is not None:
                response["sessionId"] = session_id
            peer.send_json(response)
            return
        if method == "Page.getAIPageContent":
            result(
                {
                    "ready": True,
                    "content": {"role": "RootWebArea", "children": []},
                }
            )
            return
        if method == "Page.navigate":
            result({"frameId": "frame-page-a", "loaderId": "loader-page-a"})
            return
        if method == "Page.performDOMAction":
            result({"ok": True})
            return
        if method == "Page.getAIPageActionChanges":
            result({"effects": []})
            return
        if method == "Browser.close":
            self.browser_close_received = True
            result({})
            return
        if method == "Browser.crash":
            self.browser_crash_received = True
            result({})
            return
        if method == "Target.exposeDevToolsProtocol":
            self.expose_protocol_received = True
            result({})
            return

        result({"echo": {"method": method, "params": params}})


class _FakeCDPHandler(http.server.BaseHTTPRequestHandler):
    server: FakeCDPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *args: Any) -> None:
        del args

    def do_GET(self) -> None:
        self.server.register_http(self.path)
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self._serve_websocket()
            return
        if self.path.rstrip("/") == "/json/version":
            self._send_json(
                {
                    "Browser": "FakeChrome/1.0",
                    "Protocol-Version": "1.3",
                    "webSocketDebuggerUrl": self.server.websocket_url,
                }
            )
            return
        self.send_error(404)

    def _send_json(self, value: Any) -> None:
        payload = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_websocket(self) -> None:
        if self.path.partition("?")[0] != "/devtools/browser/fake-guid":
            self.send_error(404)
            return
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400)
            return
        accept = base64.b64encode(
            hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
        ).decode("ascii")
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()

        peer = self.server.register_peer(self.connection)
        try:
            while True:
                opcode, payload = _read_frame(self.rfile)
                if opcode == 0x8:
                    peer.send_frame(0x8, payload)
                    break
                if opcode == 0x9:
                    peer.send_frame(0xA, payload)
                    continue
                if opcode != 0x1:
                    continue
                request = json.loads(payload)
                self.server.dispatch(peer, request)
        except (EOFError, OSError, json.JSONDecodeError):
            pass
        finally:
            self.server.unregister_peer(peer)


class StaticContractTests(unittest.TestCase):
    def test_scripts_and_runtime_dependencies_exist(self) -> None:
        self.assertTrue(CLI.is_file(), CLI)
        self.assertTrue(DAEMON.is_file(), DAEMON)
        sources = [path.read_text(encoding="utf-8") for path in (CLI, DAEMON)]
        for source in sources:
            self.assertRegex(source, r'requires-python\s*=\s*["\']>=3\.11["\']')
            self.assertRegex(source, r'["\']websockets>=15\.0\.1["\']')
            self.assertNotIn("jsonpatch", source)
        self.assertRegex(sources[0], r'["\']jq>=1\.12\.0["\']')

    def test_cli_json_writer_emits_one_compact_value_without_whitespace(self) -> None:
        program = (
            "import runpy;"
            f"module=runpy.run_path({str(CLI)!r});"
            'module["print_json"]({"text":"含 空格","items":[1,True]})'
        )
        completed = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            completed.stdout,
            '{"text":"含 空格","items":[1,true]}',
        )

    def test_python_jq_binding_is_path_independent_and_preserves_emissions(
        self,
    ) -> None:
        module = runpy.run_path(str(CLI))
        compile_jq_context = module["compile_jq_context"]
        jq_context_payload = module["jq_context_payload"]
        jq_context_error = module["JqContextError"]
        content = {"children": [{"id": 7}, {"id": 8}]}
        response = {"result": {"content": content}}

        original_path = os.environ.get("PATH")
        with tempfile.TemporaryDirectory() as empty_path:
            os.environ["PATH"] = empty_path
            try:
                multiple = jq_context_payload(
                    response,
                    compile_jq_context(".children[], empty"),
                )
            finally:
                if original_path is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = original_path

        self.assertEqual(multiple["contexts"], [{"id": 7}, {"id": 8}])
        self.assertEqual(
            multiple["fullJsonBytes"],
            len(json.dumps(response, separators=(",", ":")).encode("utf-8")),
        )
        self.assertEqual(
            jq_context_payload(response, compile_jq_context("empty"))["contexts"],
            [],
        )
        with self.assertRaises(jq_context_error):
            compile_jq_context("[(")
        with self.assertRaises(jq_context_error):
            jq_context_payload(
                response,
                compile_jq_context('., error("runtime boom")'),
            )

    def test_public_parser_accepts_ws_and_wss_endpoint_urls(self) -> None:
        module = runpy.run_path(str(CLI))
        parser = module["build_parser"]()
        for websocket_url in (
            "ws://browser.example.test:9222/devtools/browser/example",
            "wss://user:password@browser.example.test:443/browser?token=value",
        ):
            with self.subTest(websocket_url=websocket_url):
                args = parser.parse_args(["--websocket-url", websocket_url, "sessions"])
                self.assertEqual(args.websocket_url, websocket_url)

    def test_public_parser_accepts_lexmount_session_only_for_selection(self) -> None:
        module = runpy.run_path(str(CLI))
        parser = module["build_parser"]()

        for command in (["sessions"], ["attach", "target-1"]):
            with self.subTest(command=command):
                args = parser.parse_args(
                    ["--lexmount-session-id", "lexmount-1", *command]
                )
                self.assertEqual(args.lexmount_session_id, "lexmount-1")

        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "--websocket-url",
                    "ws://browser.example.test/browser/one",
                    "--lexmount-session-id",
                    "lexmount-1",
                    "sessions",
                ]
            )
        with self.assertRaises(SystemExit):
            module["main"](
                [
                    "--lexmount-session-id",
                    "lexmount-1",
                    "content",
                    "ace-1",
                    "--format",
                    "outline",
                ]
            )

    def test_lexmount_bridge_resolves_active_session_without_printing_url(self) -> None:
        module = runpy.run_path(str(CLI))
        connect_url = "wss://browser.example.test/devtools/browser/private-guid"
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "session": {
                        "session_id": "lexmount-1",
                        "status": "active",
                        "connect_url": connect_url,
                    },
                }
            ),
            stderr="",
        )

        with mock.patch.object(module["subprocess"], "run", return_value=completed) as run:
            endpoint = module["lexmount_endpoint_config"]("lexmount-1")

        self.assertEqual(endpoint.websocket_url, connect_url)
        self.assertNotIn(connect_url, endpoint.display)
        self.assertEqual(
            run.call_args.args[0],
            [
                "browser-cli",
                "session",
                "get",
                "--session-id",
                "lexmount-1",
                "--reveal-connect-url",
            ],
        )
        self.assertNotIn(connect_url, repr(run.call_args))

    def test_lexmount_bridge_rejects_missing_invalid_and_inactive_sessions(self) -> None:
        module = runpy.run_path(str(CLI))
        bridge = module["lexmount_endpoint_config"]
        transport_error = module["TransportError"]
        private_url = "wss://browser.example.test/devtools/browser/private-guid"
        cases = (
            subprocess.CompletedProcess([], 1, '{"ok":false,"error":"denied"}', private_url),
            subprocess.CompletedProcess([], 0, f"not-json {private_url}", private_url),
            subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    {
                        "ok": True,
                        "session": {
                            "session_id": "different",
                            "status": "active",
                            "connect_url": private_url,
                        },
                    }
                ),
                "",
            ),
            subprocess.CompletedProcess(
                [],
                0,
                json.dumps(
                    {
                        "ok": True,
                        "session": {
                            "session_id": "lexmount-1",
                            "status": "closed",
                            "connect_url": private_url,
                        },
                    }
                ),
                "",
            ),
        )
        for completed in cases:
            with self.subTest(stdout=completed.stdout[:20]):
                with mock.patch.object(
                    module["subprocess"], "run", return_value=completed
                ):
                    with self.assertRaises(transport_error) as raised:
                        bridge("lexmount-1")
                self.assertNotIn(private_url, str(raised.exception))

        with mock.patch.object(
            module["subprocess"], "run", side_effect=FileNotFoundError
        ):
            with self.assertRaises(transport_error) as raised:
                bridge("lexmount-1")
        self.assertNotIn(private_url, str(raised.exception))

        with mock.patch.object(
            module["subprocess"],
            "run",
            side_effect=subprocess.TimeoutExpired(["browser-cli"], 30),
        ):
            with self.assertRaises(transport_error):
                bridge("lexmount-1")

        invalid_url = subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "ok": True,
                    "session": {
                        "session_id": "lexmount-1",
                        "status": "active",
                        "connect_url": "https://not-a-websocket.example.test",
                    },
                }
            ),
            "",
        )
        with mock.patch.object(
            module["subprocess"], "run", return_value=invalid_url
        ):
            with self.assertRaises(module["UsageError"]):
                bridge("lexmount-1")

    def test_daemon_spawn_passes_websocket_url_only_through_inherited_pipe(
        self,
    ) -> None:
        module = runpy.run_path(str(CLI))
        connect_url = "wss://browser.example.test/devtools/browser/private-guid"
        endpoint = module["endpoint_config"](connect_url)
        observed: dict[str, Any] = {}

        class FakeProcess:
            def __init__(self, command: list[str], **kwargs: Any) -> None:
                observed["command"] = command
                observed["pass_fds"] = kwargs["pass_fds"]
                observed["process"] = self
                read_fd = os.dup(kwargs["pass_fds"][0])

                def read_secret() -> None:
                    try:
                        observed["pipe_value"] = os.read(read_fd, 64 * 1024).decode()
                    finally:
                        os.close(read_fd)

                self.reader = threading.Thread(target=read_secret)
                self.reader.start()

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            paths = module["RuntimePaths"](
                directory=directory,
                socket=directory / "daemon.sock",
                startup_lock=directory / "startup.lock",
                instance_lock=directory / "instance.lock",
                state=directory / "state.json",
                log=directory / "daemon.log",
            )
            ready = {"status": {"endpointId": endpoint.endpoint_id}}
            spawn_daemon = module["_spawn_daemon"]
            with (
                mock.patch.object(module["subprocess"], "Popen", FakeProcess),
                mock.patch.dict(
                    spawn_daemon.__globals__, {"ping": lambda _paths: ready}
                ),
            ):
                self.assertEqual(spawn_daemon(paths, endpoint), ready)

            observed_process = observed.get("process")
            if observed_process is not None:
                observed_process.reader.join(timeout=2)
            command_text = " ".join(observed["command"])
            self.assertNotIn(connect_url, command_text)
            self.assertIn("--websocket-url-fd", observed["command"])
            self.assertEqual(observed["pipe_value"], connect_url)
            self.assertNotIn(connect_url, paths.log.read_text())
            self.assertFalse(paths.state.exists())

    def test_ready_endpoint_id_requires_an_explicit_valid_identity(self) -> None:
        module = runpy.run_path(str(CLI))
        ready_endpoint_id = module["ready_endpoint_id"]
        endpoint_id = "a" * 64

        self.assertEqual(
            ready_endpoint_id({"status": {"endpointId": endpoint_id}}),
            endpoint_id,
        )
        for status in (
            {},
            {"endpoint": "http://127.0.0.1:9222"},
            {"endpointId": ""},
            {"endpointId": "not-a-sha256"},
            {"endpointId": 1},
        ):
            with self.subTest(status=status):
                self.assertIsNone(ready_endpoint_id({"status": status}))

    def test_websocket_url_validation_accepts_ws_and_wss_and_rejects_unsafe_urls(
        self,
    ) -> None:
        module = runpy.run_path(str(CLI))
        normalize = module["normalize_websocket_url"]
        usage_error = module["UsageError"]
        self.assertEqual(
            normalize("WS://Browser.Example.Test:80/devtools/browser/example"),
            "ws://browser.example.test/devtools/browser/example",
        )
        self.assertEqual(
            normalize(
                "wss://user:password@Browser.Example.Test:443/browser?token=value"
            ),
            "wss://user:password@browser.example.test/browser?token=value",
        )

        invalid_urls = (
            "",
            "http://browser.example.test/devtools/browser/example",
            "/devtools/browser/example",
            "ws:///devtools/browser/example",
            "ws://browser.example.test:not-a-port/browser",
            "ws://browser.example.test:0/browser",
            "ws://browser.example.test/browser#fragment",
            " ws://browser.example.test/browser",
            "ws://browser.example.test/browser\ninjected",
        )
        for websocket_url in invalid_urls:
            with self.subTest(websocket_url=websocket_url):
                with self.assertRaises(usage_error):
                    normalize(websocket_url)

    def test_outline_formatter_emits_the_exact_depth_first_text(self) -> None:
        module = runpy.run_path(str(CLI))
        response = {
            "result": {
                "content": {
                    "children": [
                        {"text": "标题"},
                        {
                            "id": 0,
                            "label": "搜索",
                            "placeholder": "输入关键词",
                            "action": ["focus", "input"],
                        },
                        {
                            "children": [
                                {
                                    "id": 7,
                                    "text": "保存",
                                    "label": "Save",
                                    "action": ["click"],
                                }
                            ]
                        },
                    ]
                }
            }
        }
        self.assertEqual(
            module["page_content_outline"](response),
            "标题\n[0|focus,input]搜索 | 输入关键词\n[7|click]保存 | Save\n",
        )
        self.assertEqual(
            module["page_content_outline"](
                {
                    "result": {
                        "content": {
                            "children": [
                                {"id": 1, "action": ["click"]},
                                {"text": "尾部"},
                            ]
                        }
                    }
                }
            ),
            "[1|click]\n尾部\n",
        )

    def test_action_observation_errors_preserve_completed_action_metadata(self) -> None:
        module = runpy.run_path(str(CLI))
        payload = {
            "perform": {"ok": True},
            "changes": {"effects": [{"effect": "same_page_mutation"}]},
        }
        with self.assertRaises(module["TransportError"]) as raised:
            module["add_action_content_observation"](
                payload,
                {"result": {"content": {"children": "invalid"}}},
                outline=True,
                jq_program=None,
            )
        self.assertEqual(raised.exception.data, payload)

        with self.assertRaises(module["JqContextError"]) as jq_raised:
            module["add_action_content_observation"](
                payload,
                {"result": {"content": {"children": []}}},
                outline=False,
                jq_program=module["compile_jq_context"](
                    '., error("runtime boom")'
                ),
            )
        self.assertEqual(jq_raised.exception.data, payload)

    def test_skill_documents_no_debug_true_usage(self) -> None:
        documents = [
            SKILL_ROOT / "SKILL.md",
            *sorted((SKILL_ROOT / "references").glob("*")),
        ]
        text = "\n".join(
            path.read_text(encoding="utf-8") for path in documents if path.is_file()
        )
        self.assertIsNone(
            re.search(
                r"includeDebugInfo(?:\s*[=:]\s*|\s*['\"]\s*:\s*)true",
                text,
                flags=re.IGNORECASE,
            ),
            text,
        )


class NetworkNamespaceSuite(unittest.TestCase):
    """Run production fixed-port code without touching the host's real Chrome."""

    def test_transport_suite_in_private_network_namespace(self) -> None:
        if not shutil.which("unshare") or not shutil.which("ip"):
            self.skipTest("Linux unshare and ip are required for fixed-port isolation")
        env = os.environ.copy()
        env[NETNS_MARKER] = "1"
        command = [
            UV,
            "run",
            "--with",
            "websockets>=15.0.1",
            "--with",
            "jq>=1.12.0",
            "unshare",
            "--user",
            "--map-root-user",
            "--net",
            "bash",
            "-ceu",
            'ip link set lo up\nexec python "$1" -v',
            "ace-proto-netns",
            str(Path(__file__).resolve()),
        ]
        # Separate network namespaces still share /tmp. Serialize independent
        # test runners so their fixed per-UID-in-netns path (/tmp/ace-proto-0)
        # cannot be removed by another suite's setUp or tearDown.
        lock_path = Path("/tmp") / f"ace-proto-netns-tests-{os.getuid()}.lock"
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            completed = subprocess.run(
                command,
                cwd=SKILL_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=420,
            )
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        if completed.returncode:
            self.fail(
                f"isolated transport suite failed ({completed.returncode})\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        self.assertRegex(completed.stderr, r"Ran [1-9][0-9]* tests?")
        self.assertIn(
            "test_call_routes_exact_session_and_preserves_params_and_raw_responses",
            completed.stderr,
        )
        self.assertIn(
            "test_content_reads_fresh_data_and_filters_jq_context",
            completed.stderr,
        )


class CDPDaemonBlackBoxTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        if not IN_TEST_NETNS:
            self.skipTest("black-box tests must run in their private network namespace")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="ace-proto-test-")
        runtime = Path(self.temp_dir.name) / "runtime"
        runtime.mkdir(mode=0o700)
        self.env = os.environ.copy()
        self.env["XDG_RUNTIME_DIR"] = str(runtime)
        self.env["PYTHONUNBUFFERED"] = "1"
        # The production singleton path intentionally ignores TMPDIR and XDG.
        # unshare --map-root-user gives this isolated suite UID 0, so this path
        # cannot collide with the host user's singleton.
        self.runtime_base = Path("/tmp") / f"ace-proto-{os.getuid()}"
        self.runtime_root = self.runtime_base / "singleton"
        for pid in self.daemon_pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._wait_until(lambda: not self.daemon_pids(), timeout=3, required=False)
        shutil.rmtree(self.runtime_base, ignore_errors=True)
        self.extra_servers: list[FakeCDPServer] = []
        self.server = FakeCDPServer().start()

    def tearDown(self) -> None:
        for server in reversed(self.extra_servers):
            server.stop()
        self.server.stop()
        self._wait_until(lambda: not self.daemon_pids(), timeout=8, required=False)
        for pid in self.daemon_pids():
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self._wait_until(lambda: not self.daemon_pids(), timeout=3, required=False)
        for pid in self.daemon_pids():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._wait_until(lambda: not self.daemon_pids(), timeout=3, required=False)
        shutil.rmtree(self.runtime_base, ignore_errors=True)
        self.temp_dir.cleanup()

    def run_cli(
        self,
        *arguments: str,
        check: bool = True,
        timeout: float = 20,
    ) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(CLI), *arguments]
        completed = subprocess.run(
            command,
            cwd=SKILL_ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        outline_output = (
            completed.returncode == 0
            and arguments[0:1] in {("content",), ("navigate",)}
            and "--no-content" not in arguments
            and (
                "--format=outline" in arguments
                or any(
                    argument == "--format"
                    and index + 1 < len(arguments)
                    and arguments[index + 1] == "outline"
                    for index, argument in enumerate(arguments)
                )
            )
        )
        if not outline_output and completed.stdout.lstrip().startswith(("{", "[")):
            self.assert_compact_json(completed.stdout, command)
        if check and completed.returncode:
            self.fail(
                f"command failed ({completed.returncode}): {command!r}\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        return completed

    def assert_compact_json(
        self,
        output: str,
        command: list[str] | None = None,
    ) -> Any:
        try:
            value = json.loads(output)
        except json.JSONDecodeError as error:
            self.fail(
                f"command emitted invalid JSON ({error}): {command!r}\n"
                f"stdout:{output!r}"
            )
        expected = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.assertEqual(
            output,
            expected,
            f"command did not emit compact JSON: {command!r}",
        )
        return value

    def popen_cli(self, *arguments: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [sys.executable, str(CLI), *arguments],
            cwd=SKILL_ROOT,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def payload(self, *arguments: str) -> dict[str, Any]:
        value = json.loads(self.run_cli(*arguments).stdout)
        self.assertIsInstance(value, dict)
        return value

    def start_server(self, port: int = 0) -> FakeCDPServer:
        server = FakeCDPServer(port).start()
        self.extra_servers.append(server)
        return server

    def attach(
        self,
        target_id: str,
        websocket_url: str | None = None,
    ) -> dict[str, Any]:
        arguments = (
            ["attach", target_id]
            if websocket_url is None
            else ["--websocket-url", websocket_url, "attach", target_id]
        )
        payload = self.payload(*arguments)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["session"]["targetId"], target_id)
        self.assertTrue(payload["session"]["sessionId"])
        return payload

    @staticmethod
    def page_result(
        marker: str,
        *,
        children: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "url": f"https://example.test/page-a#{marker}",
            "content": {
                "role": "RootWebArea",
                "text": marker,
                "children": children or [],
            },
        }

    def runtime_sockets(self) -> list[Path]:
        if not self.runtime_root.exists():
            return []
        return sorted(self.runtime_root.rglob("*.sock"))

    def daemon_pids(self) -> list[int]:
        expected_netns = os.stat("/proc/self/ns/net").st_ino
        daemon_path = str(DAEMON.resolve())
        found: list[int] = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                if os.stat(entry / "ns" / "net").st_ino != expected_netns:
                    continue
                arguments = (entry / "cmdline").read_bytes().split(b"\0")
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            decoded = [os.fsdecode(argument) for argument in arguments if argument]
            if any(
                os.path.abspath(argument) == daemon_path
                for argument in decoded
                if argument.endswith("cdp_daemon.py")
            ):
                found.append(int(entry.name))
        return sorted(found)

    def _wait_until(
        self,
        predicate: Callable[[], bool],
        timeout: float = 8,
        required: bool = True,
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        if required:
            self.fail("condition was not met before timeout")
        return False

    def assert_rejected_before_browser(
        self,
        session_id: str,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        before = len(self.server.matching_commands(method))
        arguments = ["call", session_id, method]
        if params is not None:
            arguments.extend(("--params", json.dumps(params)))
        completed = self.run_cli(*arguments, check=False)
        self.assertNotEqual(
            completed.returncode, 0, completed.stdout + completed.stderr
        )
        time.sleep(0.05)
        self.assertEqual(len(self.server.matching_commands(method)), before)
        return completed

    def test_public_cli_keeps_legacy_commands_and_help_is_side_effect_free(
        self,
    ) -> None:
        help_result = self.run_cli("--help")
        daemon_help = subprocess.run(
            [sys.executable, str(DAEMON), "--help"],
            cwd=SKILL_ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(
            daemon_help.returncode,
            0,
            daemon_help.stdout + daemon_help.stderr,
        )
        content_help = self.run_cli("content", "--help")
        navigate_help = self.run_cli("navigate", "--help")
        action_help = self.run_cli("action", "--help")
        self.assertEqual(self.server.connection_count, 0)
        self.assertFalse(self.daemon_pids())
        self.assertFalse(self.runtime_sockets())
        usage = help_result.stdout
        for command in (
            "sessions",
            "attach",
            "detach",
            "navigate",
            "call",
            "content",
            "action",
        ):
            self.assertRegex(usage, rf"\b{command}\b")
        for removed in (
            "--name",
            "--endpoint",
            "--active-port-file",
            "--browser",
            "use-target",
            "events",
            "status",
        ):
            self.assertNotIn(removed, usage)
        self.assertIn("--websocket-url WEBSOCKET_URL", usage)
        self.assertIn("--websocket-url-fd WEBSOCKET_URL_FD", daemon_help.stdout)
        self.assertNotIn("--websocket-url WEBSOCKET_URL", daemon_help.stdout)
        self.assertIn("--format {outline,json}", content_help.stdout)
        self.assertIn("--jq-context JQ_EXPRESSION", content_help.stdout)
        self.assertIn("--format {outline,json}", navigate_help.stdout)
        self.assertIn("--no-content", navigate_help.stdout)
        self.assertIn("--jq-context JQ_EXPRESSION", navigate_help.stdout)
        self.assertIn("--timeout TIMEOUT", navigate_help.stdout)
        self.assertIn("--diff", action_help.stdout)
        self.assertIn("--outline", action_help.stdout)
        self.assertIn("--jq-context JQ_EXPRESSION", action_help.stdout)
        self.assertIn("--value VALUE", action_help.stdout)
        self.assertIn("--values [VALUE ...]", action_help.stdout)

    def test_websocket_url_rejects_invalid_values_before_browser_access(self) -> None:
        invalid_urls = (
            "http://127.0.0.1:9222/devtools/browser/example",
            "ftp://127.0.0.1:9222/devtools/browser/example",
            "/devtools/browser/example",
            "ws:///devtools/browser/example",
            "ws://127.0.0.1:not-a-port/devtools/browser/example",
            "ws://127.0.0.1:9222/devtools/browser/example#fragment",
            " ws://127.0.0.1:9222/devtools/browser/example",
            "ws://127.0.0.1:9222/devtools/browser/example\ninjected",
        )
        for websocket_url in invalid_urls:
            with self.subTest(websocket_url=websocket_url):
                failed = self.run_cli(
                    "--websocket-url",
                    websocket_url,
                    "sessions",
                    check=False,
                )
                self.assertNotEqual(
                    failed.returncode,
                    0,
                    failed.stdout + failed.stderr,
                )
                self._wait_until(
                    lambda: not self.daemon_pids(),
                    timeout=3,
                    required=False,
                )

        self.assertEqual(self.server.connection_count, 0)
        self.assertFalse(self.daemon_pids())
        self.assertFalse(self.runtime_sockets())

    def test_content_format_is_required_and_validated_before_daemon_access(
        self,
    ) -> None:
        missing = self.run_cli("content", "old-session", check=False)
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertIn("--format", missing.stderr)

        invalid = self.run_cli("content", "old-session", "--format=yaml", check=False)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, "")
        self.assertIn("invalid choice", invalid.stderr)

        incompatible = self.run_cli(
            "content",
            "old-session",
            "--format=outline",
            "--jq-context",
            ".",
            check=False,
        )
        self.assertEqual(incompatible.returncode, 2)
        self.assertEqual(incompatible.stdout, "")
        self.assertIn("--jq-context requires --format=json", incompatible.stderr)

        self.assertEqual(self.server.connection_count, 0)
        self.assertFalse(self.daemon_pids())
        self.assertFalse(self.runtime_sockets())

    def test_navigate_arguments_are_validated_before_daemon_access(self) -> None:
        cases = (
            (("navigate", "old-session", "https://example.test/next"), "--format"),
            (
                (
                    "navigate",
                    "old-session",
                    "https://example.test/next",
                    "--format=yaml",
                ),
                "invalid choice",
            ),
            (
                (
                    "navigate",
                    "old-session",
                    "https://example.test/next",
                    "--format=outline",
                    "--jq-context",
                    ".",
                ),
                "--jq-context requires --format=json",
            ),
            (
                (
                    "navigate",
                    "old-session",
                    "https://example.test/next",
                    "--no-content",
                    "--jq-context",
                    ".",
                ),
                "--jq-context requires --format=json",
            ),
            (
                (
                    "navigate",
                    "old-session",
                    "https://example.test/next",
                    "--no-content",
                    "--format=json",
                ),
                "not allowed with argument",
            ),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                failed = self.run_cli(*arguments, check=False)
                self.assertEqual(failed.returncode, 2)
                self.assertEqual(failed.stdout, "")
                self.assertIn(expected, failed.stderr)

        self.assertEqual(self.server.connection_count, 0)
        self.assertFalse(self.daemon_pids())
        self.assertFalse(self.runtime_sockets())

    def test_action_value_and_values_are_mutually_exclusive_before_daemon_access(
        self,
    ) -> None:
        incompatible = self.run_cli(
            "action",
            "old-session",
            "7",
            "select",
            "--value",
            "books",
            "--values",
            "music",
            check=False,
        )
        self.assertEqual(incompatible.returncode, 2)
        self.assertEqual(incompatible.stdout, "")
        self.assertIn("not allowed with argument", incompatible.stderr)
        self.assertEqual(self.server.connection_count, 0)
        self.assertFalse(self.daemon_pids())
        self.assertFalse(self.runtime_sockets())

    def test_session_commands_do_not_start_a_missing_daemon(self) -> None:
        self.assertFalse(self.daemon_pids())
        self.assertFalse(self.runtime_sockets())
        self.assertEqual(self.server.connection_count, 0)

        for arguments in (
            ("detach", "old-session"),
            (
                "navigate",
                "old-session",
                "https://example.test/next",
                "--format=json",
            ),
            ("call", "old-session", "Runtime.evaluate"),
            ("content", "old-session", "--format=json"),
            ("action", "old-session", "7", "click"),
        ):
            failed = self.run_cli(*arguments, check=False)
            self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
            payload = json.loads(failed.stdout)
            self.assertFalse(payload["ok"])
            self.assertIn("not running", payload["error"]["message"].lower())
            self.assertFalse(self.daemon_pids())
            self.assertFalse(self.runtime_sockets())
            self.assertEqual(self.server.connection_count, 0)

    def test_first_command_starts_one_daemon_and_all_clients_reuse_one_websocket(
        self,
    ) -> None:
        self.assertFalse(self.daemon_pids())
        self.assertFalse(self.runtime_sockets())

        first = self.payload("sessions")
        self.assertTrue(first["ok"])
        self.server.wait_for(lambda server: server.connection_count == 1)
        self._wait_until(lambda: len(self.daemon_pids()) == 1)
        self._wait_until(lambda: len(self.runtime_sockets()) == 1)
        daemon_pid = self.daemon_pids()[0]
        self.assertIn("/json/version", self.server.http_paths)
        self.assertFalse(self.server.matching_commands("Target.attachToTarget"))

        self.payload("sessions")
        self.attach("page-a")
        session_id = self.payload("sessions")["sessions"][0]["sessionId"]
        self.run_cli("call", session_id, "Test.reusedConnection", "--params", "{}")

        self.assertEqual(self.server.connection_count, 1)
        self.assertEqual(self.daemon_pids(), [daemon_pid])
        self.assertEqual(
            {command["_connection"] for command in self.server.commands},
            {1},
        )
        state = json.loads(
            (self.runtime_root / "state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["endpointMode"], "default")
        self.assertRegex(state["endpointId"], r"^[0-9a-f]{64}$")

    def test_explicit_websocket_connects_directly_and_reuses_the_daemon(self) -> None:
        custom = self.start_server()
        websocket_url = custom.websocket_url

        first = self.payload("--websocket-url", websocket_url, "sessions")
        self.assertTrue(first["ok"])
        self.assertEqual(
            {target["targetId"] for target in first["targets"]},
            {"page-a", "worker-a"},
        )
        custom.wait_for(lambda server: server.connection_count == 1)
        self._wait_until(lambda: len(self.daemon_pids()) == 1)
        daemon_pid = self.daemon_pids()[0]

        self.assertNotIn("/json/version", custom.http_paths)
        self.assertEqual(self.server.http_paths, [])
        self.assertEqual(self.server.connection_count, 0)

        self.payload("--websocket-url", websocket_url, "sessions")
        attached = self.attach("page-a", websocket_url)
        session_id = attached["session"]["sessionId"]
        call = self.payload("call", session_id, "Test.withoutRepeatedEndpoint")
        self.assertEqual(call["sessionId"], session_id)
        self.assertEqual(custom.connection_count, 1)
        self.assertEqual(self.daemon_pids(), [daemon_pid])

        # Explicit WebSocket mode must not inherit the default 9222 listener
        # monitor. Session-bound commands without a URL keep using this daemon.
        self.server.stop_listener()
        time.sleep(1.2)
        after_default_closed = self.payload(
            "call",
            session_id,
            "Test.afterDefaultListenerClosed",
        )
        self.assertEqual(after_default_closed["sessionId"], session_id)
        self.assertEqual(self.daemon_pids(), [daemon_pid])

        state = json.loads(
            (self.runtime_root / "state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["endpointMode"], "websocket")
        self.assertEqual(
            state["endpointId"],
            hashlib.sha256(websocket_url.encode("utf-8")).hexdigest(),
        )
        self.assertIsInstance(state["endpoint"], str)

    def test_sessions_and_attach_switch_explicit_websocket_daemons(self) -> None:
        first_server = self.start_server()
        second_server = self.start_server()

        first = self.attach("page-a", first_server.websocket_url)
        old_session = first["session"]["sessionId"]
        self._wait_until(lambda: len(self.daemon_pids()) == 1)
        first_pid = self.daemon_pids()[0]

        switched = self.payload(
            "--websocket-url",
            second_server.websocket_url,
            "sessions",
        )
        self.assertTrue(switched["ok"])
        self.assertEqual(switched["sessions"], [])
        second_server.wait_for(lambda server: server.connection_count == 1)
        self._wait_until(
            lambda: len(self.daemon_pids()) == 1 and self.daemon_pids()[0] != first_pid,
        )
        self._wait_until(lambda: not first_server.peers)

        stale = self.run_cli(
            "call",
            old_session,
            "Test.staleSessionMustNotRun",
            check=False,
        )
        self.assertNotEqual(stale.returncode, 0, stale.stdout + stale.stderr)
        self.assertFalse(second_server.matching_commands("Test.staleSessionMustNotRun"))

        switched_back = self.attach("worker-a", first_server.websocket_url)
        self.assertEqual(switched_back["session"]["targetId"], "worker-a")
        first_server.wait_for(lambda server: server.connection_count == 2)
        self._wait_until(lambda: not second_server.peers)
        self.assertFalse(first_server.browser_close_received)
        self.assertFalse(second_server.browser_close_received)

    def test_session_commands_reject_an_explicit_endpoint_mismatch_without_switching(
        self,
    ) -> None:
        active_server = self.start_server()
        other_server = self.start_server()
        attached = self.attach("page-a", active_server.websocket_url)
        session_id = attached["session"]["sessionId"]
        self._wait_until(lambda: len(self.daemon_pids()) == 1)
        daemon_pid = self.daemon_pids()[0]
        active_endpoint_id = json.loads(
            (self.runtime_root / "state.json").read_text(encoding="utf-8")
        )["endpointId"]

        commands = (
            ("detach", session_id),
            ("call", session_id, "Test.mustNotReachBrowser"),
            ("content", session_id, "--format=json"),
            (
                "navigate",
                session_id,
                "https://example.test/must-not-reach-browser",
                "--format=json",
            ),
            ("action", session_id, "7", "click"),
        )
        for command in commands:
            with self.subTest(command=command[0]):
                failed = self.run_cli(
                    "--websocket-url",
                    other_server.websocket_url,
                    *command,
                    check=False,
                )
                self.assertNotEqual(
                    failed.returncode,
                    0,
                    failed.stdout + failed.stderr,
                )
                self.assertIn(
                    "not the requested",
                    (failed.stdout + failed.stderr).lower(),
                )

        self.assertEqual(self.daemon_pids(), [daemon_pid])
        self.assertEqual(active_server.connection_count, 1)
        self.assertEqual(other_server.connection_count, 0)
        self.assertFalse(active_server.matching_commands("Test.mustNotReachBrowser"))
        self.assertEqual(
            json.loads((self.runtime_root / "state.json").read_text(encoding="utf-8"))[
                "endpointId"
            ],
            active_endpoint_id,
        )

        reused = self.payload("call", session_id, "Test.endpointOmitted")
        self.assertEqual(reused["sessionId"], session_id)
        self.assertEqual(active_server.connection_count, 1)

    def test_websocket_credentials_query_and_browser_guid_are_not_persisted(
        self,
    ) -> None:
        custom = self.start_server()
        port = custom.server_address[1]
        websocket_url = (
            f"ws://ace-user:ultra-secret@127.0.0.1:{port}"
            "/devtools/browser/fake-guid?token=query-secret"
        )

        completed = self.run_cli("--websocket-url", websocket_url, "sessions")
        listed = json.loads(completed.stdout)
        self.assertTrue(listed["ok"])
        custom.wait_for(lambda server: server.connection_count == 1)

        state_path = self.runtime_root / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["endpointMode"], "websocket")
        self.assertEqual(
            state["endpointId"],
            hashlib.sha256(websocket_url.encode("utf-8")).hexdigest(),
        )
        self.assertIsInstance(state["endpoint"], str)

        persisted = completed.stdout + completed.stderr
        persisted += "\n" + state_path.read_text(encoding="utf-8")
        log_path = self.runtime_root / "daemon.log"
        if log_path.exists():
            persisted += "\n" + log_path.read_text(encoding="utf-8")
        for secret in (
            "ace-user",
            "ultra-secret",
            "query-secret",
            "fake-guid",
        ):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, persisted)

    def test_concurrent_different_websocket_selections_use_the_requested_endpoint(
        self,
    ) -> None:
        first_server = self.start_server()
        second_server = self.start_server()
        first_server.targets = [
            first_server.target("only-first", "page", "First endpoint")
        ]
        second_server.targets = [
            second_server.target("only-second", "page", "Second endpoint")
        ]

        requests = (
            (first_server.websocket_url, "only-first"),
            (second_server.websocket_url, "only-second"),
        )
        clients = [
            self.popen_cli("--websocket-url", websocket_url, "sessions")
            for websocket_url, _expected_target in requests
        ]
        results = [client.communicate(timeout=40) for client in clients]

        for client, (stdout, stderr), (_websocket_url, expected_target) in zip(
            clients,
            results,
            requests,
        ):
            self.assertEqual(client.returncode, 0, stdout + stderr)
            payload = self.assert_compact_json(stdout)
            self.assertEqual(
                {target["targetId"] for target in payload["targets"]},
                {expected_target},
            )

        self._wait_until(lambda: len(self.daemon_pids()) == 1)
        self._wait_until(
            lambda: len(first_server.peers) + len(second_server.peers) == 1
        )
        self.assertEqual(self.server.connection_count, 0)
        self.assertFalse(first_server.browser_close_received)
        self.assertFalse(second_server.browser_close_received)

    def test_concurrent_first_commands_start_exactly_one_daemon(self) -> None:
        clients = [self.popen_cli("sessions") for _ in range(6)]
        results = [client.communicate(timeout=30) for client in clients]
        for client, (stdout, stderr) in zip(clients, results):
            self.assertEqual(client.returncode, 0, stdout + stderr)
            self.assertTrue(self.assert_compact_json(stdout)["ok"])
        self.server.wait_for(lambda server: server.connection_count >= 1)
        self.assertEqual(self.server.connection_count, 1)
        self._wait_until(lambda: len(self.daemon_pids()) == 1)
        self.assertEqual(len(self.runtime_sockets()), 1)

    def test_a_second_direct_daemon_is_rejected_by_the_lifetime_lock(self) -> None:
        self.payload("sessions")
        self.server.wait_for(lambda server: server.connection_count == 1)
        self._wait_until(lambda: len(self.daemon_pids()) == 1)
        original_pid = self.daemon_pids()[0]

        duplicate = subprocess.run(
            [sys.executable, str(DAEMON)],
            cwd=SKILL_ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertEqual(self.daemon_pids(), [original_pid])
        self.assertEqual(self.server.connection_count, 1)
        self.assertEqual(len(self.runtime_sockets()), 1)

        # A losing daemon must not disturb the winner's socket or browser link.
        listed = self.payload("sessions")
        self.assertTrue(listed["ok"])
        self.assertEqual(self.daemon_pids(), [original_pid])
        self.assertEqual(self.server.connection_count, 1)

    def test_sessions_attach_idempotently_and_detach_only_one_session(self) -> None:
        initial = self.payload("sessions")
        self.assertEqual(
            {target["targetId"] for target in initial["targets"]},
            {"page-a", "worker-a"},
        )
        self.assertEqual(initial["sessions"], [])

        page = self.attach("page-a")
        page_session = page["session"]["sessionId"]
        self.assertFalse(page["reused"])
        repeated = self.attach("page-a")
        self.assertTrue(repeated["reused"])
        self.assertEqual(repeated["session"]["sessionId"], page_session)
        self.assertEqual(
            len(self.server.matching_commands("Target.attachToTarget")),
            1,
        )

        worker = self.attach("worker-a")
        worker_session = worker["session"]["sessionId"]
        listed = self.payload("sessions")
        self.assertEqual(
            {(item["sessionId"], item["targetId"]) for item in listed["sessions"]},
            {(page_session, "page-a"), (worker_session, "worker-a")},
        )

        detached = self.payload("detach", page_session)
        self.assertEqual(
            detached,
            {
                "ok": True,
                "detached": True,
                "sessionId": page_session,
                "targetId": "page-a",
            },
        )
        detach_command = self.server.matching_commands("Target.detachFromTarget")[-1]
        self.assertEqual(detach_command["params"], {"sessionId": page_session})
        remaining = self.payload("sessions")["sessions"]
        self.assertEqual(
            remaining,
            [{"sessionId": worker_session, "targetId": "worker-a"}],
        )
        self.assertEqual(self.server.connection_count, 1)
        self.assertEqual(len(self.daemon_pids()), 1)

        call = self.payload("call", worker_session, "Test.afterOtherDetach")
        self.assertEqual(call["sessionId"], worker_session)

    def test_sessions_only_register_routable_flat_auto_attached_children(self) -> None:
        parent = self.attach("page-a")["session"]["sessionId"]

        non_flat = self.payload(
            "call",
            parent,
            "Target.setAutoAttach",
            "--params",
            json.dumps(
                {
                    "autoAttach": True,
                    "waitForDebuggerOnStart": False,
                    "flatten": False,
                }
            ),
        )
        self.assertEqual(non_flat["sessionId"], parent)
        after_non_flat = self.payload("sessions")["sessions"]
        self.assertEqual(after_non_flat, [{"sessionId": parent, "targetId": "page-a"}])

        flat = self.payload(
            "call",
            parent,
            "Target.setAutoAttach",
            "--params",
            json.dumps(
                {
                    "autoAttach": True,
                    "waitForDebuggerOnStart": False,
                    "flatten": True,
                }
            ),
        )
        self.assertEqual(flat["sessionId"], parent)
        sessions = self.payload("sessions")["sessions"]
        children = [item for item in sessions if item["sessionId"] != parent]
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["targetId"], "auto-target-3")

        routed = self.payload("call", children[0]["sessionId"], "Test.autoChild")
        self.assertEqual(routed["sessionId"], children[0]["sessionId"])

        # A legacy explicit attach has an indistinguishable attachedToTarget
        # event. It must not be exposed as a top-level routable session even
        # while flat auto-attach remains enabled on the same parent.
        legacy = self.payload(
            "call",
            parent,
            "Target.attachToTarget",
            "--params",
            json.dumps({"targetId": "worker-a", "flatten": False}),
        )
        legacy_session = legacy["result"]["sessionId"]
        self.assertNotIn(
            legacy_session,
            {item["sessionId"] for item in self.payload("sessions")["sessions"]},
        )

    def test_call_routes_exact_session_and_preserves_params_and_raw_responses(
        self,
    ) -> None:
        first_session = self.attach("page-a")["session"]["sessionId"]
        second_session = self.attach("worker-a")["session"]["sessionId"]
        params = {
            "string": "value",
            "number": 17,
            "boolean": False,
            "nothing": None,
            "nested": {"array": [1, "two", {"three": 3}]},
        }
        response = self.payload(
            "call",
            first_session,
            "Experimental.futureCommand",
            "--params",
            json.dumps(params),
        )
        self.assertNotIn("ok", response)
        self.assertEqual(response["sessionId"], first_session)
        self.assertEqual(
            response["result"]["echo"],
            {"method": "Experimental.futureCommand", "params": params},
        )
        sent = self.server.matching_commands("Experimental.futureCommand")[0]
        self.assertEqual(sent["sessionId"], first_session)
        self.assertEqual(sent["params"], params)

        params_file = Path(self.temp_dir.name) / "params.json"
        params_file.write_text(json.dumps({"fromFile": [1, 2, 3]}), encoding="utf-8")
        file_response = self.payload(
            "call",
            second_session,
            "Experimental.fromFile",
            "--params-file",
            str(params_file),
        )
        self.assertEqual(file_response["sessionId"], second_session)
        file_command = self.server.matching_commands("Experimental.fromFile")[0]
        self.assertEqual(file_command["sessionId"], second_session)
        self.assertEqual(file_command["params"], {"fromFile": [1, 2, 3]})

        failed = self.run_cli("call", second_session, "Test.protocolError", check=False)
        self.assertEqual(failed.returncode, 2, failed.stdout + failed.stderr)
        error_response = json.loads(failed.stdout)
        self.assertEqual(error_response["sessionId"], second_session)
        self.assertEqual(error_response["error"]["code"], -32000)

    def test_content_reads_fresh_data_and_filters_jq_context(self) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        first = self.page_result("first")
        second = self.page_result("second")
        self.server.queue_result("Page.getAIPageContent", first)
        self.server.queue_result("Page.getAIPageContent", second)

        first_response = self.payload("content", session_id, "--format=json")
        second_response = self.payload("content", session_id, "--format=json")
        self.assertEqual(first_response["result"], first)
        self.assertEqual(second_response["result"], second)
        self.assertNotEqual(first_response["result"], second_response["result"])
        content_commands = self.server.matching_commands("Page.getAIPageContent")
        self.assertEqual(len(content_commands), 2)
        self.assertTrue(
            all(
                command["params"] == {"includeDebugInfo": False}
                for command in content_commands
            )
        )

        direct_text = {"role": "StaticText", "text": "直接保存"}
        direct_button = {
            "role": "button",
            "id": 21,
            "action": ["click"],
            "children": [direct_text],
        }
        deep_text = {"role": "StaticText", "text": "深层保存"}
        deep_button = {
            "role": "button",
            "id": 22,
            "action": ["click"],
            "children": [
                {
                    "role": "generic",
                    "children": [
                        {"role": "generic", "children": [deep_text]},
                    ],
                }
            ],
        }
        group = {
            "role": "group",
            "text": "设置",
            "children": [direct_button, deep_button],
        }
        nested = self.page_result("nested", children=[group])
        self.server.queue_result("Page.getAIPageContent", nested)
        full = self.run_cli("content", session_id, "--format=json")
        full_payload = json.loads(full.stdout)
        self.assertEqual(full_payload["result"], nested)

        def jq_context(expression: str) -> dict[str, Any]:
            self.server.queue_result("Page.getAIPageContent", nested)
            filtered = self.payload(
                "content",
                session_id,
                "--format=json",
                "--jq-context",
                expression,
            )
            self.assertEqual(set(filtered), {"contexts", "fullJsonBytes"})
            command = self.server.matching_commands("Page.getAIPageContent")[-1]
            raw_response = {
                "id": command["id"],
                "result": nested,
                "sessionId": session_id,
            }
            expected_full_json = json.dumps(
                raw_response,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            self.assertEqual(
                filtered["fullJsonBytes"],
                len(expected_full_json.encode("utf-8")),
            )
            return filtered

        direct_match = jq_context("recurse(.children[]?) | select(.id? == 22)")
        self.assertEqual(direct_match["contexts"], [deep_button])

        explicit_parent = jq_context(
            "recurse(.children[]?) | select(any(.children[]?; .id? == 22))"
        )
        self.assertEqual(explicit_parent["contexts"], [group])

        interactive_ancestor = (
            "recurse(.children[]?) | "
            'select((.action? // []) | index("click")) | '
            "select(any(recurse(.children[]?); .text? == $text))"
        )
        direct_ancestor = jq_context(
            interactive_ancestor.replace("$text", '"直接保存"')
        )
        self.assertEqual(direct_ancestor["contexts"], [direct_button])
        deep_ancestor = jq_context(interactive_ancestor.replace("$text", '"深层保存"'))
        self.assertEqual(deep_ancestor["contexts"], [deep_button])

        self.assertEqual(jq_context("empty")["contexts"], [])
        multiple = jq_context(".children[0], .children[0].children[1], .children[0]")
        self.assertEqual(multiple["contexts"], [group, deep_button, group])
        array_value = jq_context("[.children[0].children[]]")
        self.assertEqual(array_value["contexts"], [[direct_button, deep_button]])
        scalars = jq_context('"label", 7, true, null')
        self.assertEqual(scalars["contexts"], ["label", 7, True, None])

        before = len(self.server.matching_commands("Page.getAIPageContent"))
        rejected_call = self.run_cli(
            "call",
            session_id,
            "Page.getAIPageContent",
            "--jq-context",
            ".",
            check=False,
        )
        self.assertNotEqual(rejected_call.returncode, 0)
        self.assertEqual(
            len(self.server.matching_commands("Page.getAIPageContent")), before
        )

    def test_navigate_polls_until_ready_and_matches_content_output_modes(
        self,
    ) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        url = "https://example.test/navigated"

        def page_with_readiness(
            marker: str,
            ready: bool,
            *,
            children: list[dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            return {
                **self.page_result(marker, children=children),
                "ready": ready,
            }

        navigate_result = {"frameId": "frame-new", "loaderId": "loader-new"}
        first_not_ready = page_with_readiness("first-not-ready", False)
        second_not_ready = page_with_readiness("second-not-ready", False)
        ready_page = page_with_readiness("ready", True)
        self.server.queue_result("Page.navigate", navigate_result)
        for page in (first_not_ready, second_not_ready, ready_page):
            self.server.queue_result("Page.getAIPageContent", page)

        full_json = self.payload(
            "navigate",
            session_id,
            url,
            "--format=json",
            "--timeout",
            "3",
        )
        self.assertEqual(full_json["result"], ready_page)
        self.assertIs(full_json["result"]["ready"], True)
        self.assertNotIn("readyState", full_json["result"])
        session_commands = [
            command
            for command in self.server.commands
            if command.get("sessionId") == session_id
            and command.get("method")
            in {"Page.navigate", "Page.getAIPageContent"}
        ]
        self.assertEqual(
            [command["method"] for command in session_commands],
            [
                "Page.navigate",
                "Page.getAIPageContent",
                "Page.getAIPageContent",
                "Page.getAIPageContent",
            ],
        )
        self.assertEqual(session_commands[0]["params"], {"url": url})
        self.assertTrue(
            all(
                command["params"] == {"includeDebugInfo": False}
                for command in session_commands[1:]
            )
        )

        outline_page = page_with_readiness(
            "outline",
            True,
            children=[
                {
                    "role": "button",
                    "id": 7,
                    "text": "Save",
                    "action": ["click"],
                }
            ],
        )
        self.server.queue_result("Page.navigate", {"frameId": "frame-outline"})
        self.server.queue_result("Page.getAIPageContent", outline_page)
        outlined = self.run_cli(
            "navigate",
            session_id,
            f"{url}/outline",
            "--format=outline",
        )
        self.assertEqual(outlined.stdout, "outline\n[7|click]Save\n")
        self.assertEqual(outlined.stderr, "")

        button = {
            "role": "button",
            "id": 55,
            "text": "Continue",
            "action": ["click"],
        }
        jq_page = page_with_readiness("jq", True, children=[button])
        self.server.queue_result("Page.navigate", {"frameId": "frame-jq"})
        self.server.queue_result("Page.getAIPageContent", jq_page)
        filtered = self.payload(
            "navigate",
            session_id,
            f"{url}/jq",
            "--format=json",
            "--jq-context",
            "recurse(.children[]?) | select(.id? == 55)",
        )
        self.assertEqual(set(filtered), {"contexts", "fullJsonBytes"})
        self.assertEqual(filtered["contexts"], [button])
        self.assertNotIn("ready", filtered)
        content_command = self.server.matching_commands("Page.getAIPageContent")[-1]
        raw_content_response = {
            "id": content_command["id"],
            "result": jq_page,
            "sessionId": session_id,
        }
        self.assertEqual(
            filtered["fullJsonBytes"],
            len(
                json.dumps(
                    raw_content_response,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
        )

        added = {"role": "status", "text": "after navigation"}
        after = page_with_readiness("jq", True, children=[button, added])
        self.server.queue_result("Page.performDOMAction", {"ok": True})
        self.server.queue_result(
            "Page.getAIPageActionChanges",
            {"effects": [{"effect": "same_page_mutation"}]},
        )
        self.server.queue_result("Page.getAIPageContent", after)
        observed = self.payload(
            "action",
            session_id,
            "55",
            "click",
            "--diff",
            "--timeout",
            "1",
        )
        self.assertEqual(observed["contentDiff"], [added])

        navigate_count = len(self.server.matching_commands("Page.navigate"))
        invalid_jq = self.run_cli(
            "navigate",
            session_id,
            f"{url}/must-not-run",
            "--format=json",
            "--jq-context",
            "[",
            check=False,
        )
        self.assertNotEqual(invalid_jq.returncode, 0)
        self.assertEqual(json.loads(invalid_jq.stdout)["error"]["type"], "jq-context")
        self.assertEqual(
            len(self.server.matching_commands("Page.navigate")), navigate_count
        )

    def test_navigate_terminal_results_skip_content_and_clear_the_baseline(
        self,
    ) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        url = "https://example.test/terminal"

        def establish_baseline(marker: str) -> None:
            self.server.queue_result(
                "Page.getAIPageContent",
                {**self.page_result(marker), "ready": True},
            )
            self.payload("content", session_id, "--format=json")

        def assert_no_baseline() -> None:
            perform_count = len(
                self.server.matching_commands("Page.performDOMAction")
            )
            failed = self.run_cli(
                "action",
                session_id,
                "7",
                "click",
                "--timeout",
                "1",
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(json.loads(failed.stdout)["error"]["type"], "usage")
            self.assertEqual(
                len(self.server.matching_commands("Page.performDOMAction")),
                perform_count,
            )

        establish_baseline("before-no-content")
        content_count = len(self.server.matching_commands("Page.getAIPageContent"))
        no_content_result = {"frameId": "frame-raw", "loaderId": "loader-raw"}
        self.server.queue_result("Page.navigate", no_content_result)
        no_content = self.payload(
            "navigate",
            session_id,
            url,
            "--no-content",
        )
        self.assertEqual(no_content["result"], no_content_result)
        self.assertEqual(
            len(self.server.matching_commands("Page.getAIPageContent")),
            content_count,
        )
        assert_no_baseline()

        for marker, terminal_result in (
            (
                "error-text",
                {
                    "frameId": "frame-error",
                    "errorText": "net::ERR_NAME_NOT_RESOLVED",
                },
            ),
            (
                "download",
                {"frameId": "frame-download", "isDownload": True},
            ),
        ):
            with self.subTest(marker=marker):
                establish_baseline(f"before-{marker}")
                content_count = len(
                    self.server.matching_commands("Page.getAIPageContent")
                )
                self.server.queue_result("Page.navigate", terminal_result)
                terminal = self.payload(
                    "navigate",
                    session_id,
                    f"{url}/{marker}",
                    "--format=json",
                )
                self.assertEqual(terminal["result"], terminal_result)
                self.assertEqual(
                    len(self.server.matching_commands("Page.getAIPageContent")),
                    content_count,
                )
                assert_no_baseline()

        establish_baseline("before-cdp-error")
        content_count = len(self.server.matching_commands("Page.getAIPageContent"))
        self.server.queue_error("Page.navigate", "navigation failed")
        failed_navigation = self.run_cli(
            "navigate",
            session_id,
            f"{url}/cdp-error",
            "--format=json",
            check=False,
        )
        self.assertEqual(failed_navigation.returncode, 2)
        self.assertEqual(
            json.loads(failed_navigation.stdout)["error"]["message"],
            "navigation failed",
        )
        self.assertEqual(
            len(self.server.matching_commands("Page.getAIPageContent")),
            content_count,
        )
        assert_no_baseline()

    def test_navigate_timeout_and_invalid_content_never_cache_or_leak_it(
        self,
    ) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        url = "https://example.test/slow"
        self.server.queue_result(
            "Page.getAIPageContent",
            {**self.page_result("old-baseline"), "ready": True},
        )
        self.payload("content", session_id, "--format=json")

        secret = "incomplete-content-must-not-leak"
        self.server.queue_result("Page.navigate", {"frameId": "frame-slow"})
        self.server.queue_result(
            "Page.getAIPageContent",
            {**self.page_result(secret), "ready": False},
        )
        timed_out = self.run_cli(
            "navigate",
            session_id,
            url,
            "--format=json",
            "--timeout",
            "0.2",
            check=False,
        )
        self.assertEqual(timed_out.returncode, 1)
        timeout_payload = json.loads(timed_out.stdout)
        self.assertEqual(timeout_payload["error"]["type"], "navigate-timeout")
        self.assertEqual(timeout_payload["error"]["data"]["url"], url)
        self.assertIs(timeout_payload["error"]["data"]["lastReady"], False)
        self.assertIn("navigateResponse", timeout_payload["error"]["data"])
        self.assertNotIn(secret, timed_out.stdout)

        perform_count = len(self.server.matching_commands("Page.performDOMAction"))
        no_timeout_baseline = self.run_cli(
            "action",
            session_id,
            "7",
            "click",
            "--timeout",
            "1",
            check=False,
        )
        self.assertNotEqual(no_timeout_baseline.returncode, 0)
        self.assertEqual(
            len(self.server.matching_commands("Page.performDOMAction")),
            perform_count,
        )

        invalid_pages = {
            "missing-ready": self.page_result("missing-ready"),
            "null-ready": {**self.page_result("null-ready"), "ready": None},
            "string-ready": {**self.page_result("string-ready"), "ready": "true"},
            "numeric-ready": {**self.page_result("numeric-ready"), "ready": 1},
        }
        for marker, invalid_page in invalid_pages.items():
            with self.subTest(marker=marker):
                self.server.queue_result(
                    "Page.navigate", {"frameId": f"frame-{marker}"}
                )
                self.server.queue_result("Page.getAIPageContent", invalid_page)
                invalid = self.run_cli(
                    "navigate",
                    session_id,
                    f"{url}/{marker}",
                    "--format=json",
                    check=False,
                )
                self.assertEqual(invalid.returncode, 1)
                invalid_payload = json.loads(invalid.stdout)
                self.assertEqual(invalid_payload["error"]["type"], "transport")
                self.assertIn(
                    "invalid ready value", invalid_payload["error"]["message"]
                )
                self.assertNotIn(marker, invalid.stdout)

        self.server.queue_result("Page.navigate", {"frameId": "frame-content-error"})
        self.server.queue_error("Page.getAIPageContent", "content failed")
        content_error = self.run_cli(
            "navigate",
            session_id,
            f"{url}/content-error",
            "--format=json",
            check=False,
        )
        self.assertEqual(content_error.returncode, 2)
        self.assertEqual(
            json.loads(content_error.stdout)["error"]["message"], "content failed"
        )

    def test_content_outline_uses_preorder_dfs_and_refreshes_the_baseline(
        self,
    ) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        outline_result = {
            "url": "https://example.test/page-a#outline",
            "content": {
                "role": "RootWebArea",
                "children": [
                    {
                        "role": "group",
                        "children": [
                            {"role": "heading", "text": "标题"},
                            {
                                "role": "textbox",
                                "id": 0,
                                "label": "搜索",
                                "placeholder": "输入关键词",
                                "action": ["focus", "input"],
                            },
                            {
                                "role": "button",
                                "id": 7,
                                "text": "保存",
                                "label": "Save",
                                "placeholder": "",
                                "action": ["click", "focus"],
                            },
                            {
                                "role": "presentation",
                                "children": [
                                    {"role": "status", "text": "完成"},
                                    {"role": "button", "id": 8},
                                ],
                            },
                        ],
                    }
                ],
            },
        }
        self.server.queue_result("Page.getAIPageContent", outline_result)
        outlined = self.run_cli("content", session_id, "--format=outline")
        self.assertEqual(outlined.stderr, "")
        self.assertEqual(
            outlined.stdout,
            "标题\n"
            "[0|focus,input]搜索 | 输入关键词\n"
            "[7|click,focus]保存 | Save\n"
            "完成\n"
            "[8|]\n",
        )
        self.assertEqual(
            self.server.matching_commands("Page.getAIPageContent")[-1]["params"],
            {"includeDebugInfo": False},
        )

        added = {"role": "status", "text": "更新"}
        after = {
            **outline_result,
            "content": {
                **outline_result["content"],
                "children": [*outline_result["content"]["children"], added],
            },
        }
        changes = {"effects": [{"effect": "same_page_mutation"}]}
        self.server.queue_result("Page.performDOMAction", {"ok": True})
        self.server.queue_result("Page.getAIPageActionChanges", changes)
        self.server.queue_result("Page.getAIPageContent", after)
        observed = self.payload(
            "action",
            session_id,
            "0",
            "click",
            "--diff",
            "--timeout",
            "1",
        )
        self.assertEqual(observed["contentDiff"], [added])

        text_nodes = {
            "url": "https://example.test/page-a#text-only",
            "content": {
                "role": "RootWebArea",
                "children": [
                    {"text": "A"},
                    {"children": [{"label": "B"}]},
                ],
            },
        }
        self.server.queue_result("Page.getAIPageContent", text_nodes)
        text_only = self.run_cli("content", session_id, "--format", "outline")
        self.assertEqual(text_only.stdout, "A\nB\n")

        self.server.queue_result(
            "Page.getAIPageContent",
            {
                "url": "https://example.test/page-a#empty",
                "content": {"role": "RootWebArea"},
            },
        )
        empty = self.run_cli("content", session_id, "--format=outline")
        self.assertEqual(empty.stdout, "")

        self.server.queue_error("Page.getAIPageContent", "outline failed")
        failed = self.run_cli("content", session_id, "--format=outline", check=False)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(
            json.loads(failed.stdout)["error"]["message"], "outline failed"
        )

    def test_action_default_observes_fresh_content_without_returning_it(self) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        stable_button = {"role": "button", "id": 22, "text": "save"}
        first_notice = {"role": "status", "text": "first mutation"}
        second_notice = {"role": "status", "text": "second mutation"}
        baseline = self.page_result("stable", children=[stable_button])
        after_default = self.page_result(
            "stable", children=[stable_button, first_notice]
        )
        after_diff = self.page_result(
            "stable", children=[stable_button, first_notice, second_notice]
        )
        self.server.queue_result("Page.getAIPageContent", baseline)
        self.payload("content", session_id, "--format=json")

        default_changes = {"effects": [{"effect": "same_page_mutation"}]}
        self.server.queue_result("Page.performDOMAction", {"ok": True})
        self.server.queue_result("Page.getAIPageActionChanges", default_changes)
        self.server.queue_result("Page.getAIPageContent", after_default)
        content_count = len(self.server.matching_commands("Page.getAIPageContent"))
        default_action = self.payload(
            "action", session_id, "22", "click", "--timeout", "1"
        )
        self.assertEqual(
            default_action,
            {"perform": {"ok": True}, "changes": default_changes},
        )
        self.assertEqual(
            len(self.server.matching_commands("Page.getAIPageContent")),
            content_count + 1,
        )
        self.assertEqual(
            self.server.matching_commands("Page.getAIPageContent")[-1]["params"],
            {"includeDebugInfo": False},
        )

        diff_changes = {"effects": [{"effect": "navigation"}]}
        self.server.queue_result("Page.performDOMAction", {"ok": True})
        self.server.queue_result("Page.getAIPageActionChanges", diff_changes)
        self.server.queue_result("Page.getAIPageContent", after_diff)
        diff_action = self.payload(
            "action",
            session_id,
            "22",
            "click",
            "--diff",
            "--timeout",
            "1",
        )
        self.assertEqual(
            diff_action,
            {
                "perform": {"ok": True},
                "changes": diff_changes,
                "contentDiff": [second_notice],
            },
        )

    def test_action_forwards_select_value_and_values(self) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        select = {
            "role": "select",
            "id": 77,
            "text": "Department",
            "action": ["click", "select"],
            "value": "all",
            "options": [],
        }
        content = self.page_result("select", children=[select])
        self.server.queue_result("Page.getAIPageContent", content)
        self.payload("content", session_id, "--format=json")

        def forwarded_params(*arguments: str) -> dict[str, Any]:
            self.server.queue_result("Page.performDOMAction", {"ok": True})
            self.server.queue_result(
                "Page.getAIPageActionChanges",
                {"effects": [{"effect": "value_changed"}]},
            )
            self.server.queue_result("Page.getAIPageContent", content)
            self.payload(
                "action",
                session_id,
                "77",
                "select",
                *arguments,
                "--timeout",
                "1",
            )
            return self.server.matching_commands("Page.performDOMAction")[-1]["params"]

        self.assertEqual(
            forwarded_params("--value", "books"),
            {"id": 77, "action": "select", "value": "books"},
        )
        self.assertEqual(
            forwarded_params("--value", ""),
            {"id": 77, "action": "select", "value": ""},
        )
        self.assertEqual(
            forwarded_params("--values", "books", "music and audio", "books"),
            {
                "id": 77,
                "action": "select",
                "values": ["books", "music and audio", "books"],
            },
        )
        self.assertEqual(
            forwarded_params("--values"),
            {"id": 77, "action": "select", "values": []},
        )

    def test_action_outline_reuses_fresh_content_and_refreshes_baseline(self) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        button = {
            "role": "button",
            "id": 22,
            "text": "Save",
            "action": ["click"],
        }
        first_notice = {"role": "status", "text": "Saved"}
        second_notice = {"role": "note", "text": "Ready"}
        baseline = self.page_result("before", children=[button])
        after_outline = self.page_result("after", children=[button, first_notice])
        after_diff = self.page_result(
            "after", children=[button, first_notice, second_notice]
        )
        self.server.queue_result("Page.getAIPageContent", baseline)
        self.payload("content", session_id, "--format=json")

        changes = {"effects": [{"effect": "same_page_mutation"}]}
        self.server.queue_result("Page.performDOMAction", {"ok": True})
        self.server.queue_result("Page.getAIPageActionChanges", changes)
        self.server.queue_result("Page.getAIPageContent", after_outline)
        content_count = len(self.server.matching_commands("Page.getAIPageContent"))
        observed = self.payload(
            "action",
            session_id,
            "22",
            "click",
            "--outline",
            "--timeout",
            "1",
        )
        self.assertEqual(
            set(observed),
            {"perform", "changes", "outline", "fullJsonBytes"},
        )
        self.assertEqual(observed["perform"], {"ok": True})
        self.assertEqual(observed["changes"], changes)
        self.assertEqual(
            observed["outline"],
            "after\n[22|click]Save\nSaved\n",
        )
        self.assertEqual(
            len(self.server.matching_commands("Page.getAIPageContent")),
            content_count + 1,
        )
        command = self.server.matching_commands("Page.getAIPageContent")[-1]
        raw_response = {
            "id": command["id"],
            "result": after_outline,
            "sessionId": session_id,
        }
        self.assertEqual(
            observed["fullJsonBytes"],
            len(
                json.dumps(
                    raw_response,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
        )
        self.assertNotIn("contentDiff", observed)
        self.assertNotIn("contexts", observed)

        self.server.queue_result("Page.performDOMAction", {"ok": True})
        self.server.queue_result("Page.getAIPageActionChanges", changes)
        self.server.queue_result("Page.getAIPageContent", after_diff)
        next_action = self.payload(
            "action",
            session_id,
            "22",
            "click",
            "--diff",
            "--timeout",
            "1",
        )
        self.assertEqual(next_action["contentDiff"], [second_notice])

    def test_action_outline_content_errors_preserve_action_metadata(self) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        button = {
            "role": "button",
            "id": 22,
            "text": "Save",
            "action": ["click"],
        }
        self.server.queue_result(
            "Page.getAIPageContent",
            self.page_result("baseline", children=[button]),
        )
        self.payload("content", session_id, "--format=json")

        perform = {"ok": True}
        changes = {"effects": [{"effect": "same_page_mutation"}]}
        invalid_after = {
            "url": "https://example.test/page-a#invalid",
            "content": {
                "role": "RootWebArea",
                "children": "not-a-node-list",
            },
        }
        self.server.queue_result("Page.performDOMAction", perform)
        self.server.queue_result("Page.getAIPageActionChanges", changes)
        self.server.queue_result("Page.getAIPageContent", invalid_after)
        failed = self.run_cli(
            "action",
            session_id,
            "22",
            "click",
            "--outline",
            "--timeout",
            "1",
            check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        error = json.loads(failed.stdout)["error"]
        self.assertEqual(error["type"], "internal")
        self.assertIn("invalid content tree", error["message"])
        self.assertEqual(
            error["data"],
            {"perform": perform, "changes": changes},
        )

    def test_action_jq_context_queries_the_complete_fresh_page(self) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        unchanged_label = {"role": "heading", "text": "Settings"}
        before_button = {
            "role": "button",
            "id": 42,
            "text": "Save",
            "action": ["click"],
        }
        after_button = {**before_button, "text": "Saved"}
        unchanged_sibling = {"role": "note", "text": "Unchanged help"}
        before_group = {
            "role": "group",
            "children": [unchanged_label, before_button, unchanged_sibling],
        }
        after_group = {
            "role": "group",
            "children": [unchanged_label, after_button, unchanged_sibling],
        }
        baseline = self.page_result("stable", children=[before_group])
        after = self.page_result("stable", children=[after_group])
        self.server.queue_result("Page.getAIPageContent", baseline)
        self.payload("content", session_id, "--format=json")

        changes = {"effects": [{"effect": "same_page_mutation"}]}

        def jq_action(expression: str) -> dict[str, Any]:
            self.server.queue_result("Page.performDOMAction", {"ok": True})
            self.server.queue_result("Page.getAIPageActionChanges", changes)
            self.server.queue_result("Page.getAIPageContent", after)
            return self.payload(
                "action",
                session_id,
                "42",
                "click",
                "--jq-context",
                expression,
                "--timeout",
                "1",
            )

        parent = jq_action(
            "recurse(.children[]?) | select(any(.children[]?; .id? == 42))"
        )
        self.assertEqual(
            set(parent), {"perform", "changes", "contexts", "fullJsonBytes"}
        )
        self.assertEqual(parent["perform"], {"ok": True})
        self.assertEqual(parent["changes"], changes)
        self.assertEqual(parent["contexts"], [after_group])
        self.assertIn(unchanged_label, parent["contexts"][0]["children"])
        self.assertIn(unchanged_sibling, parent["contexts"][0]["children"])
        self.assertNotIn("contentDiff", parent)

        command = self.server.matching_commands("Page.getAIPageContent")[-1]
        raw_response = {
            "id": command["id"],
            "result": after,
            "sessionId": session_id,
        }
        expected_full_json = json.dumps(
            raw_response,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.assertEqual(
            parent["fullJsonBytes"], len(expected_full_json.encode("utf-8"))
        )

        empty = jq_action("empty")
        self.assertEqual(empty["contexts"], [])
        self.assertEqual(
            set(empty), {"perform", "changes", "contexts", "fullJsonBytes"}
        )

        mixed = jq_action(
            '.children[0], "label", 7, true, null, [.children[0].children[]]'
        )
        self.assertEqual(
            mixed["contexts"],
            [
                after_group,
                "label",
                7,
                True,
                None,
                [unchanged_label, after_button, unchanged_sibling],
            ],
        )

    def test_action_jq_preflight_and_python_binding_are_path_independent(
        self,
    ) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        self.server.queue_result("Page.getAIPageContent", self.page_result("baseline"))
        self.payload("content", session_id, "--format=json")
        perform_count = len(self.server.matching_commands("Page.performDOMAction"))

        for incompatible in (
            ("--diff", "--outline"),
            ("--diff", "--jq-context", "."),
            ("--outline", "--jq-context", "."),
        ):
            with self.subTest(incompatible=incompatible):
                mutually_exclusive = self.run_cli(
                    "action",
                    session_id,
                    "7",
                    "click",
                    *incompatible,
                    check=False,
                )
                self.assertEqual(mutually_exclusive.returncode, 2)
                self.assertIn("not allowed with argument", mutually_exclusive.stderr)

        syntax_error = self.run_cli(
            "action",
            session_id,
            "7",
            "click",
            "--jq-context",
            "[(",
            check=False,
        )
        self.assertNotEqual(syntax_error.returncode, 0)
        syntax_payload = json.loads(syntax_error.stdout)
        self.assertEqual(set(syntax_payload), {"ok", "error"})
        self.assertEqual(syntax_payload["error"]["type"], "jq-context")
        self.assertNotIn("data", syntax_payload["error"])

        empty_path = Path(self.temp_dir.name) / "path-without-jq"
        empty_path.mkdir()
        after = self.page_result("after-path-independent-jq")
        changes = {"effects": [{"effect": "same_page_mutation"}]}
        self.server.queue_result("Page.performDOMAction", {"ok": True})
        self.server.queue_result("Page.getAIPageActionChanges", changes)
        self.server.queue_result("Page.getAIPageContent", after)
        original_path = self.env.get("PATH")
        self.env["PATH"] = str(empty_path)
        try:
            without_jq_binary = self.run_cli(
                "action",
                session_id,
                "7",
                "click",
                "--jq-context",
                ".",
                "--timeout",
                "1",
            )
        finally:
            if original_path is None:
                self.env.pop("PATH", None)
            else:
                self.env["PATH"] = original_path
        path_independent_payload = json.loads(without_jq_binary.stdout)
        self.assertEqual(path_independent_payload["perform"], {"ok": True})
        self.assertEqual(path_independent_payload["changes"], changes)
        self.assertEqual(path_independent_payload["contexts"], [after])
        self.assertEqual(
            len(self.server.matching_commands("Page.performDOMAction")),
            perform_count + 1,
        )

    def test_action_jq_runtime_errors_preserve_metadata_and_refresh_baseline(
        self,
    ) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        button = {"role": "button", "id": 7, "text": "save"}
        baseline = self.page_result("stable", children=[button])
        self.server.queue_result("Page.getAIPageContent", baseline)
        self.payload("content", session_id, "--format=json")

        perform = {"ok": True}
        changes = {"effects": [{"effect": "same_page_mutation"}]}
        current_children = [button]

        def failed_jq_action(expression: str, after: dict[str, Any]) -> dict[str, Any]:
            self.server.queue_result("Page.performDOMAction", perform)
            self.server.queue_result("Page.getAIPageActionChanges", changes)
            self.server.queue_result("Page.getAIPageContent", after)
            completed = self.run_cli(
                "action",
                session_id,
                "7",
                "click",
                "--jq-context",
                expression,
                "--timeout",
                "1",
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            payload = json.loads(completed.stdout)
            self.assertEqual(set(payload), {"ok", "error"})
            self.assertEqual(payload["error"]["type"], "jq-context")
            self.assertEqual(
                payload["error"]["data"],
                {"perform": perform, "changes": changes},
            )
            return payload

        def prove_latest_baseline(new_notice: dict[str, Any]) -> None:
            nonlocal current_children
            after = self.page_result("stable", children=[*current_children, new_notice])
            self.server.queue_result("Page.performDOMAction", perform)
            self.server.queue_result("Page.getAIPageActionChanges", changes)
            self.server.queue_result("Page.getAIPageContent", after)
            observed = self.payload(
                "action",
                session_id,
                "7",
                "click",
                "--diff",
                "--timeout",
                "1",
            )
            self.assertEqual(observed["contentDiff"], [new_notice])
            current_children = [*current_children, new_notice]

        runtime_notice = {"role": "status", "text": "runtime after"}
        runtime_after = self.page_result(
            "stable", children=[*current_children, runtime_notice]
        )
        failed_jq_action('error("runtime boom")', runtime_after)
        current_children.append(runtime_notice)
        prove_latest_baseline({"role": "note", "text": "runtime baseline proof"})

        partial_notice = {"role": "status", "text": "partial after"}
        partial_after = self.page_result(
            "stable", children=[*current_children, partial_notice]
        )
        partial_error = failed_jq_action('., error("partial boom")', partial_after)
        self.assertNotIn("contexts", partial_error["error"]["data"])
        current_children.append(partial_notice)
        prove_latest_baseline({"role": "note", "text": "partial baseline proof"})

    def test_jq_context_empty_and_errors_refresh_full_action_baseline(self) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        self.server.queue_result(
            "Page.getAIPageContent", self.page_result("old-baseline")
        )
        self.payload("content", session_id, "--format=json")

        stable_button = {
            "role": "button",
            "id": 22,
            "text": "save",
            "action": ["click"],
        }
        empty_baseline = self.page_result("empty-baseline", children=[stable_button])
        self.server.queue_result("Page.getAIPageContent", empty_baseline)
        empty_context = self.payload(
            "content", session_id, "--format=json", "--jq-context", "empty"
        )
        self.assertEqual(empty_context["contexts"], [])

        added_after_empty = {"role": "status", "text": "after empty"}
        after_empty = self.page_result(
            "empty-baseline", children=[stable_button, added_after_empty]
        )
        self.server.queue_result("Page.performDOMAction", {"ok": True})
        self.server.queue_result(
            "Page.getAIPageActionChanges",
            {"effects": [{"effect": "same_page_mutation"}]},
        )
        self.server.queue_result("Page.getAIPageContent", after_empty)
        empty_action = self.payload(
            "action", session_id, "22", "click", "--diff", "--timeout", "1"
        )
        self.assertEqual(empty_action["contentDiff"], [added_after_empty])

        error_cases = [
            ("syntax-error", "[("),
            ("runtime-error", 'error("runtime boom")'),
            ("partial-error-baseline", '1, error("partial boom")'),
        ]
        for marker, expression in error_cases:
            with self.subTest(expression=expression):
                baseline = self.page_result(marker, children=[stable_button])
                self.server.queue_result("Page.getAIPageContent", baseline)
                completed = self.run_cli(
                    "content",
                    session_id,
                    "--format=json",
                    "--jq-context",
                    expression,
                    check=False,
                )
                self.assertNotEqual(completed.returncode, 0)
                error_payload = json.loads(completed.stdout)
                self.assertEqual(set(error_payload), {"ok", "error"})
                self.assertFalse(error_payload["ok"])
                self.assertEqual(error_payload["error"]["type"], "jq-context")

        added_after_error = {"role": "status", "text": "after failed jq"}
        after_error = self.page_result(
            "partial-error-baseline",
            children=[stable_button, added_after_error],
        )
        self.server.queue_result("Page.performDOMAction", {"ok": True})
        self.server.queue_result(
            "Page.getAIPageActionChanges",
            {"effects": [{"effect": "same_page_mutation"}]},
        )
        self.server.queue_result("Page.getAIPageContent", after_error)
        error_action = self.payload(
            "action", session_id, "22", "click", "--diff", "--timeout", "1"
        )
        self.assertEqual(error_action["contentDiff"], [added_after_error])

    def test_latest_successful_content_is_the_action_diff_baseline(self) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        initial = self.page_result("initial")
        raw_baseline = self.page_result(
            "stable",
            children=[{"role": "textbox", "id": 42, "text": "raw-baseline-secret"}],
        )
        after = self.page_result(
            "stable",
            children=[
                {"role": "textbox", "id": 42, "text": "after"},
                {"role": "status", "text": "saved"},
            ],
        )
        self.server.queue_result("Page.getAIPageContent", initial)
        self.server.queue_result("Page.getAIPageContent", raw_baseline)
        self.server.queue_error("Page.getAIPageContent", "content failed")
        self.server.queue_result("Page.getAIPageContent", after)
        self.payload("content", session_id, "--format=json")

        raw = self.payload("call", session_id, "Page.getAIPageContent")
        self.assertEqual(raw["result"], raw_baseline)
        failed = self.run_cli("call", session_id, "Page.getAIPageContent", check=False)
        self.assertEqual(failed.returncode, 2, failed.stdout + failed.stderr)

        effect = {
            "action": "click",
            "targetNodeId": 42,
            "effect": "navigation",
            "pageState": "settled",
        }
        self.server.queue_result("Page.performDOMAction", {"ok": True})
        self.server.queue_result("Page.getAIPageActionChanges", {"effects": [effect]})
        action = self.payload(
            "action", session_id, "42", "click", "--diff", "--timeout", "1"
        )
        self.assertEqual(action["perform"], {"ok": True})
        self.assertEqual(action["changes"], {"effects": [effect]})
        self.assertEqual(
            action["contentDiff"],
            [
                {"role": "textbox", "id": 42, "text": "after"},
                {"role": "status", "text": "saved"},
            ],
        )
        self.assertNotIn("raw-baseline-secret", json.dumps(action))
        self.assertTrue(
            all(
                not {"op", "path", "from", "value"}.intersection(subtree)
                for subtree in action["contentDiff"]
            )
        )

        page_methods = [
            command["method"]
            for command in self.server.commands
            if command.get("method", "").startswith("Page.")
        ]
        perform_index = page_methods.index("Page.performDOMAction")
        self.assertEqual(
            page_methods[perform_index : perform_index + 3],
            [
                "Page.performDOMAction",
                "Page.getAIPageActionChanges",
                "Page.getAIPageContent",
            ],
        )
        self.assertEqual(
            self.server.matching_commands("Page.performDOMAction")[-1]["params"],
            {"id": 42, "action": "click"},
        )
        self.assertEqual(
            self.server.matching_commands("Page.getAIPageContent")[-1]["params"],
            {"includeDebugInfo": False},
        )

        marker = b"raw-baseline-secret"
        for path in self.runtime_root.rglob("*"):
            if path.is_file():
                self.assertNotIn(marker, path.read_bytes(), path)

    def test_action_requires_a_baseline_and_stops_on_unsuccessful_perform(self) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        missing_baseline = self.run_cli(
            "action", session_id, "7", "click", "--timeout", "1", check=False
        )
        self.assertNotEqual(missing_baseline.returncode, 0)
        missing_payload = json.loads(missing_baseline.stdout)
        self.assertFalse(missing_payload["ok"])
        self.assertEqual(len(self.server.matching_commands("Page.performDOMAction")), 0)

        self.server.queue_result(
            "Page.getAIPageContent", self.page_result("has-baseline")
        )
        self.payload("content", session_id, "--format=json")
        self.server.queue_result(
            "Page.performDOMAction", {"ok": False, "reason": "node disappeared"}
        )
        unsuccessful = self.run_cli(
            "action", session_id, "7", "click", "--timeout", "1", check=False
        )
        self.assertNotEqual(unsuccessful.returncode, 0)
        unsuccessful_payload = json.loads(unsuccessful.stdout)
        self.assertFalse(unsuccessful_payload["ok"])
        self.assertEqual(unsuccessful_payload["error"]["type"], "action-result")
        self.assertEqual(unsuccessful_payload["error"]["message"], "node disappeared")
        self.assertEqual(
            len(self.server.matching_commands("Page.getAIPageActionChanges")), 0
        )
        self.assertEqual(len(self.server.matching_commands("Page.getAIPageContent")), 1)

    def test_new_target_action_skips_old_content_and_needs_new_baseline(self) -> None:
        self.server.add_target("page-b")
        old_session = self.attach("page-a")["session"]["sessionId"]
        self.server.queue_result(
            "Page.getAIPageContent", self.page_result("old-baseline")
        )
        self.payload("content", old_session, "--format=json")
        content_count = len(self.server.matching_commands("Page.getAIPageContent"))
        effects = [
            {
                "action": "click",
                "targetNodeId": 8,
                "effect": "same_page_mutation",
                "pageState": "settled",
            },
            {
                "action": "click",
                "targetNodeId": 8,
                "effect": "new_target",
                "newTargetId": "page-b",
                "pageState": "loading",
            },
        ]
        for observation_args in (
            (),
            ("--diff",),
            ("--outline",),
            ("--jq-context", "."),
        ):
            with self.subTest(observation_args=observation_args):
                self.server.queue_result("Page.performDOMAction", {"ok": True})
                self.server.queue_result(
                    "Page.getAIPageActionChanges", {"effects": effects}
                )
                action = self.payload(
                    "action",
                    old_session,
                    "8",
                    "click",
                    *observation_args,
                    "--timeout",
                    "1",
                )
                self.assertEqual(
                    action,
                    {
                        "perform": {"ok": True},
                        "changes": {"effects": effects},
                    },
                )
        self.assertEqual(
            len(self.server.matching_commands("Page.getAIPageContent")), content_count
        )

        new_session = self.attach("page-b")["session"]["sessionId"]
        perform_count = len(self.server.matching_commands("Page.performDOMAction"))
        without_content = self.run_cli(
            "action", new_session, "9", "click", "--timeout", "1", check=False
        )
        self.assertNotEqual(without_content.returncode, 0)
        self.assertEqual(
            len(self.server.matching_commands("Page.performDOMAction")), perform_count
        )
        new_content = {
            "url": "https://example.test/page-b",
            "content": {"role": "RootWebArea", "text": "new page", "children": []},
        }
        self.server.queue_result("Page.getAIPageContent", new_content)
        self.assertEqual(
            self.payload("content", new_session, "--format=json")["result"],
            new_content,
        )
        self.assertEqual(
            self.server.matching_commands("Page.getAIPageContent")[-1]["sessionId"],
            new_session,
        )

    def test_detached_event_clears_only_that_session_and_its_baseline(self) -> None:
        page_session = self.attach("page-a")["session"]["sessionId"]
        worker_session = self.attach("worker-a")["session"]["sessionId"]
        self.server.queue_result(
            "Page.getAIPageContent", self.page_result("detached-baseline")
        )
        self.payload("content", page_session, "--format=json")
        self.server.emit_event("Target.detachedFromTarget", {"sessionId": page_session})
        self.server.emit_event("Target.detachedFromTarget", {"sessionId": page_session})
        time.sleep(0.1)

        sessions = self.payload("sessions")["sessions"]
        self.assertNotIn(page_session, {item["sessionId"] for item in sessions})
        self.assertIn(worker_session, {item["sessionId"] for item in sessions})
        rejected = self.run_cli(
            "call", page_session, "Test.afterDetachEvent", check=False
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(
            self.payload("call", worker_session, "Test.otherTargetSurvives")[
                "sessionId"
            ],
            worker_session,
        )

        replacement = self.attach("page-a")["session"]["sessionId"]
        self.assertNotEqual(replacement, page_session)
        perform_count = len(self.server.matching_commands("Page.performDOMAction"))
        no_leaked_baseline = self.run_cli(
            "action", replacement, "7", "click", "--timeout", "1", check=False
        )
        self.assertNotEqual(no_leaked_baseline.returncode, 0)
        self.assertEqual(
            len(self.server.matching_commands("Page.performDOMAction")), perform_count
        )

    def test_target_destroyed_cleans_all_sessions_and_fails_pending_calls(self) -> None:
        page_session = self.attach("page-a")["session"]["sessionId"]
        worker_session = self.attach("worker-a")["session"]["sessionId"]
        extra = self.payload(
            "call",
            page_session,
            "Target.attachToTarget",
            "--params",
            json.dumps({"targetId": "page-a", "flatten": True}),
        )["result"]["sessionId"]
        self.assertIn(
            extra,
            {item["sessionId"] for item in self.payload("sessions")["sessions"]},
        )

        pending = self.popen_cli("call", extra, "Test.neverRespond", "--timeout", "10")
        self.server.wait_for(
            lambda server: bool(server.matching_commands("Test.neverRespond"))
        )
        self.server.emit_event("Target.targetDestroyed", {"targetId": "page-a"})
        self.server.emit_event("Target.targetDestroyed", {"targetId": "page-a"})
        self.server.emit_event("Target.detachedFromTarget", {"sessionId": extra})
        try:
            stdout, stderr = pending.communicate(timeout=4)
        except subprocess.TimeoutExpired:
            pending.kill()
            stdout, stderr = pending.communicate(timeout=2)
            self.fail(f"pending session call did not fail promptly\n{stdout}\n{stderr}")
        self.assertNotEqual(pending.returncode, 0, stdout + stderr)
        self.assertFalse(self.assert_compact_json(stdout)["ok"])

        sessions = self.payload("sessions")["sessions"]
        session_ids = {item["sessionId"] for item in sessions}
        self.assertNotIn(page_session, session_ids)
        self.assertNotIn(extra, session_ids)
        self.assertIn(worker_session, session_ids)
        healthy = self.payload("call", worker_session, "Test.afterTargetDestroyed")
        self.assertEqual(healthy["sessionId"], worker_session)
        self.assertEqual(self.server.connection_count, 1)

    def test_concurrent_out_of_order_calls_and_timeout_keep_connection_usable(
        self,
    ) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        slow = self.popen_cli(
            "call",
            session_id,
            "Test.delayedEcho",
            "--params",
            json.dumps({"delay": 0.25, "tag": "slow"}),
        )
        fast = self.popen_cli(
            "call",
            session_id,
            "Test.delayedEcho",
            "--params",
            json.dumps({"delay": 0.02, "tag": "fast"}),
        )
        fast_out, fast_err = fast.communicate(timeout=10)
        slow_out, slow_err = slow.communicate(timeout=10)
        self.assertEqual(fast.returncode, 0, fast_out + fast_err)
        self.assertEqual(slow.returncode, 0, slow_out + slow_err)
        self.assertEqual(self.assert_compact_json(fast_out)["result"]["tag"], "fast")
        self.assertEqual(self.assert_compact_json(slow_out)["result"]["tag"], "slow")

        timed_out = self.run_cli(
            "call",
            session_id,
            "Test.neverRespond",
            "--timeout",
            "0.05",
            check=False,
        )
        self.assertNotEqual(timed_out.returncode, 0)
        healthy = self.payload("call", session_id, "Test.afterTimeout")
        self.assertEqual(healthy["sessionId"], session_id)
        self.assertEqual(self.server.connection_count, 1)

    def test_page_content_policy_is_enforced_directly_and_in_nested_messages(
        self,
    ) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        self.payload("call", session_id, "Page.getAIPageContent")
        self.payload(
            "call",
            session_id,
            "Page.getAIPageContent",
            "--params",
            json.dumps({"includeDebugInfo": False}),
        )
        direct = self.server.matching_commands("Page.getAIPageContent")
        self.assertEqual(len(direct), 2)
        self.assertEqual(direct[0]["params"], {"includeDebugInfo": False})
        self.assertEqual(direct[1]["params"], {"includeDebugInfo": False})

        for value in (True, None, 0, 1, "false", "", [], {}):
            self.assert_rejected_before_browser(
                session_id,
                "Page.getAIPageContent",
                {"includeDebugInfo": value},
            )

        def nested_call(
            nested_params: dict[str, Any],
        ) -> subprocess.CompletedProcess[str]:
            message = json.dumps(
                {
                    "id": 7,
                    "method": "Page.getAIPageContent",
                    "params": nested_params,
                }
            )
            return self.run_cli(
                "call",
                session_id,
                "Target.sendMessageToTarget",
                "--params",
                json.dumps({"sessionId": "legacy", "message": message}),
                check=False,
            )

        nested_ok = nested_call({})
        self.assertEqual(nested_ok.returncode, 0, nested_ok.stdout + nested_ok.stderr)
        outer = self.server.matching_commands("Target.sendMessageToTarget")[-1]
        nested_message = json.loads(outer["params"]["message"])
        self.assertEqual(nested_message["params"], {"includeDebugInfo": False})

        before = len(self.server.matching_commands("Target.sendMessageToTarget"))
        nested_bad = nested_call({"includeDebugInfo": True})
        self.assertNotEqual(nested_bad.returncode, 0)
        self.assertEqual(
            len(self.server.matching_commands("Target.sendMessageToTarget")),
            before,
        )

    def test_browser_termination_commands_are_rejected_without_stopping_daemon(
        self,
    ) -> None:
        session_id = self.attach("page-a")["session"]["sessionId"]
        for method in (
            "Browser.close",
            "Browser.crash",
            "Target.exposeDevToolsProtocol",
        ):
            self.assert_rejected_before_browser(session_id, method, {})
        self.assertFalse(self.server.browser_close_received)
        self.assertFalse(self.server.browser_crash_received)
        self.assertFalse(self.server.expose_protocol_received)
        self.assertEqual(self.server.connection_count, 1)
        self.assertEqual(len(self.daemon_pids()), 1)

    def test_daemon_exits_when_the_9222_listener_disappears(self) -> None:
        self.payload("sessions")
        self.server.wait_for(lambda server: server.connection_count == 1)
        self._wait_until(lambda: len(self.daemon_pids()) == 1)
        daemon_pid = self.daemon_pids()[0]
        self.assertEqual(len(self.server.peers), 1)
        peer = next(iter(self.server.peers.values()))
        self.assertFalse(peer.closed)

        # Keep the established browser WebSocket open. The daemon must notice
        # that the production listener itself disappeared, not merely react to
        # an already-closed WebSocket.
        self.server.stop_listener()
        probe = socket.socket()
        try:
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", 9222)), 0)
        finally:
            probe.close()
        self.assertFalse(peer.closed)

        self._wait_until(lambda: daemon_pid not in self.daemon_pids(), timeout=10)
        self._wait_until(lambda: not self.runtime_sockets(), timeout=5)
        self._wait_until(lambda: peer.closed, timeout=5)

        failed = self.run_cli("sessions", check=False, timeout=10)
        self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        self._wait_until(lambda: not self.daemon_pids(), timeout=5)


class ActionPollingUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_ping_ipc_requires_the_current_endpoint_identity(self) -> None:
        scripts = str(SKILL_ROOT / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import cdp_daemon

        with tempfile.TemporaryDirectory(
            prefix="ace-proto-endpoint-id-unit-"
        ) as directory:
            root = Path(directory)
            daemon = cdp_daemon.CdpDaemon(
                cdp_daemon.RuntimePaths(
                    directory=root,
                    socket=root / "daemon.sock",
                    startup_lock=root / "startup.lock",
                    instance_lock=root / "instance.lock",
                    state=root / "state.json",
                    log=root / "daemon.log",
                )
            )

            ping = await daemon.handle_request({"op": "ping"})
            self.assertTrue(ping["pong"])

            for endpoint_id in (None, 1):
                request = {"op": "list_sessions"}
                if endpoint_id is not None:
                    request["endpointId"] = endpoint_id
                with self.subTest(endpoint_id=endpoint_id):
                    with self.assertRaisesRegex(
                        cdp_daemon.UsageError,
                        "endpointId is required and must be a string",
                    ):
                        await daemon.handle_request(request)

            with self.assertRaisesRegex(
                cdp_daemon.UsageError,
                "endpointId is required and must be a string",
            ):
                await daemon.handle_request(
                    {"op": "list_sessions", "endpointId": None}
                )

            with self.assertRaisesRegex(
                cdp_daemon.TransportError,
                "endpoint changed before this command ran",
            ):
                await daemon.handle_request(
                    {"op": "list_sessions", "endpointId": "wrong"}
                )

            with self.assertRaisesRegex(RuntimeError, "disconnected"):
                await daemon.handle_request(
                    {
                        "op": "list_sessions",
                        "endpointId": daemon.endpoint.endpoint_id,
                    }
                )

    async def test_action_request_validates_select_value_and_values(self) -> None:
        scripts = str(SKILL_ROOT / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import cdp_daemon

        with tempfile.TemporaryDirectory(
            prefix="ace-proto-action-params-unit-"
        ) as directory:
            root = Path(directory)
            daemon = cdp_daemon.CdpDaemon(
                cdp_daemon.RuntimePaths(
                    directory=root,
                    socket=root / "daemon.sock",
                    startup_lock=root / "startup.lock",
                    instance_lock=root / "instance.lock",
                    state=root / "state.json",
                    log=root / "daemon.log",
                )
            )
            daemon.connected = True
            captured: list[dict[str, Any]] = []

            async def capture_action(
                session_id: str,
                params: dict[str, Any],
                poll_timeout: float,
                observation: str,
            ) -> dict[str, Any]:
                self.assertEqual(session_id, "session")
                self.assertEqual(poll_timeout, 1)
                self.assertEqual(observation, "none")
                captured.append(params)
                return {"perform": {"ok": True}, "changes": {"effects": []}}

            daemon.action_session = capture_action  # type: ignore[method-assign]

            def request(params: dict[str, Any]) -> dict[str, Any]:
                return {
                    "op": "action",
                    "endpointId": daemon.endpoint.endpoint_id,
                    "sessionId": "session",
                    "params": params,
                    "pollTimeout": 1,
                    "observation": "none",
                }

            accepted = [
                {"id": 7, "action": "select", "value": ""},
                {"id": 7, "action": "select", "values": []},
                {"id": 7, "action": "select", "values": ["a", "b"]},
            ]
            for params in accepted:
                response = await daemon.handle_request(request(params))
                self.assertTrue(response["ok"])
            self.assertEqual(captured, accepted)

            invalid = [
                (
                    {"id": 7, "action": "select", "value": 1},
                    "action value must be a string",
                ),
                (
                    {"id": 7, "action": "select", "values": "a"},
                    "action values must be an array of strings",
                ),
                (
                    {"id": 7, "action": "select", "values": ["a", 2]},
                    "action values must be an array of strings",
                ),
                (
                    {
                        "id": 7,
                        "action": "select",
                        "value": "a",
                        "values": ["a"],
                    },
                    "action value and values are mutually exclusive",
                ),
            ]
            for params, message in invalid:
                with self.assertRaisesRegex(cdp_daemon.UsageError, message):
                    await daemon.handle_request(request(params))
            self.assertEqual(captured, accepted)

    def test_changed_content_subtrees_only_returns_new_minimal_subtrees(self) -> None:
        scripts = str(SKILL_ROOT / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import cdp_daemon

        unchanged_header = {"role": "heading", "text": "Settings"}
        unchanged_tail = {"role": "contentinfo", "text": "Footer"}
        before = {
            "url": "https://example.test/before",
            "content": {
                "role": "RootWebArea",
                "children": [
                    unchanged_header,
                    {"role": "status", "text": "old-only-secret"},
                    {
                        "role": "group",
                        "children": [
                            {
                                "role": "textbox",
                                "id": 11,
                                "text": "before",
                                "children": [{"role": "note", "text": "context"}],
                            },
                            {"role": "note", "text": "removed"},
                        ],
                    },
                    unchanged_tail,
                ],
            },
        }
        notice = {"role": "alert", "text": "New notice"}
        changed_textbox = {
            "role": "textbox",
            "id": 11,
            "text": "after",
            "children": [{"role": "note", "text": "context"}],
        }
        added_note = {
            "role": "note",
            "text": "added",
            "children": [{"role": "strong", "text": "complete subtree"}],
        }
        after = {
            "url": "https://example.test/after",
            "content": {
                "role": "RootWebArea",
                "children": [
                    notice,
                    unchanged_header,
                    {
                        "role": "group",
                        "children": [changed_textbox, added_note],
                    },
                    unchanged_tail,
                ],
            },
        }

        changed = cdp_daemon.changed_content_subtrees(before, after)
        self.assertEqual(changed, [notice, changed_textbox, added_note])
        self.assertNotIn("old-only-secret", json.dumps(changed))
        self.assertNotIn("removed", json.dumps(changed))

        same_content_new_url = {**before, "url": "https://example.test/next"}
        self.assertEqual(
            cdp_daemon.changed_content_subtrees(before, same_content_new_url), []
        )

        deletion_only = {
            "url": before["url"],
            "content": {
                "role": "RootWebArea",
                "children": [unchanged_header, unchanged_tail],
            },
        }
        self.assertEqual(cdp_daemon.changed_content_subtrees(before, deletion_only), [])

        ancestor_changed = {
            "url": before["url"],
            "content": {
                "role": "RootWebArea",
                "children": [
                    {
                        "role": "group",
                        "text": "after",
                        "children": [{"role": "status", "text": "also changed"}],
                    }
                ],
            },
        }
        ancestor_before = {
            "url": before["url"],
            "content": {
                "role": "RootWebArea",
                "children": [
                    {
                        "role": "group",
                        "text": "before",
                        "children": [{"role": "status", "text": "old"}],
                    }
                ],
            },
        }
        self.assertEqual(
            cdp_daemon.changed_content_subtrees(ancestor_before, ancestor_changed),
            [ancestor_changed["content"]["children"][0]],
        )

        first = {"role": "button", "id": 1, "text": "First"}
        second = {"role": "button", "id": 2, "text": "Second"}
        reordered = {
            "url": before["url"],
            "content": {"role": "RootWebArea", "children": [second, first]},
        }
        original_order = {
            "url": before["url"],
            "content": {"role": "RootWebArea", "children": [first, second]},
        }
        self.assertEqual(
            cdp_daemon.changed_content_subtrees(original_order, reordered), [second]
        )

        ambiguous_before = {
            "content": {
                "role": "RootWebArea",
                "children": [
                    {
                        "role": "group",
                        "children": [{"role": "textbox", "id": 1, "text": "old"}],
                    }
                ],
            }
        }
        inserted_group = {"role": "group", "children": []}
        updated_group = {
            "role": "group",
            "children": [{"role": "textbox", "id": 1, "text": "new"}],
        }
        ambiguous_after = {
            "content": {
                "role": "RootWebArea",
                "children": [inserted_group, updated_group],
            }
        }
        self.assertEqual(
            cdp_daemon.changed_content_subtrees(ambiguous_before, ambiguous_after),
            [inserted_group, updated_group],
        )

        equal_size_ambiguous_before = {
            "content": {
                "role": "RootWebArea",
                "children": [
                    {
                        "role": "group",
                        "children": [{"role": "textbox", "id": 1, "text": "old one"}],
                    },
                    {
                        "role": "group",
                        "children": [{"role": "textbox", "id": 2, "text": "old two"}],
                    },
                ],
            }
        }
        equal_size_ambiguous_after = {
            "content": {
                "role": "RootWebArea",
                "children": [inserted_group, updated_group],
            }
        }
        self.assertEqual(
            cdp_daemon.changed_content_subtrees(
                equal_size_ambiguous_before, equal_size_ambiguous_after
            ),
            [inserted_group, updated_group],
        )

    async def test_busy_lock_requests_fail_without_late_cdp_side_effects(self) -> None:
        scripts = str(SKILL_ROOT / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import cdp_daemon

        with tempfile.TemporaryDirectory(prefix="ace-proto-lock-unit-") as directory:
            root = Path(directory)
            daemon = cdp_daemon.CdpDaemon(
                cdp_daemon.RuntimePaths(
                    directory=root,
                    socket=root / "daemon.sock",
                    startup_lock=root / "startup.lock",
                    instance_lock=root / "instance.lock",
                    state=root / "state.json",
                    log=root / "daemon.log",
                )
            )
            daemon.session_lock_wait_timeout = 0.01
            daemon._register_session("session", "target")
            daemon.content_baselines["session"] = {"content": {"text": "before"}}
            sent: list[str] = []

            async def unexpected_send(
                method: str,
                params: dict[str, Any] | None = None,
                session_id: str | None = None,
                timeout: float = 30,
            ) -> dict[str, Any]:
                del params, session_id, timeout
                sent.append(method)
                return {"result": {}}

            daemon.send_cdp = unexpected_send  # type: ignore[method-assign]
            session_lock = daemon.session_locks["session"]
            await session_lock.acquire()
            with self.assertRaises(cdp_daemon.SessionError):
                await daemon.call_session("session", "Test.mustNotRun", {}, 30)
            with self.assertRaises(cdp_daemon.SessionError):
                await daemon.navigate_session(
                    "session", "https://example.test/must-not-run", True, 30
                )
            with self.assertRaises(cdp_daemon.SessionError):
                await daemon.action_session("session", {"id": 1, "action": "click"}, 1)
            session_lock.release()
            await asyncio.sleep(0.02)
            self.assertEqual(sent, [])
            self.assertEqual(
                daemon.content_baselines["session"],
                {"content": {"text": "before"}},
            )

            await daemon.management_lock.acquire()
            with self.assertRaises(cdp_daemon.SessionError):
                await daemon.attach_session("other-target")
            daemon.management_lock.release()
            await asyncio.sleep(0.02)
            self.assertEqual(sent, [])
            self.assertEqual(set(daemon.sessions), {"session"})

    async def test_navigate_polling_uses_half_second_intervals_and_total_deadline(
        self,
    ) -> None:
        scripts = str(SKILL_ROOT / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import cdp_daemon

        def page(ready: Any, marker: str) -> dict[str, Any]:
            return {
                "ready": ready,
                "url": f"https://example.test/{marker}",
                "content": {
                    "role": "RootWebArea",
                    "text": marker,
                    "children": [],
                },
            }

        class ClockNavigateDaemon(cdp_daemon.CdpDaemon):
            def __init__(
                self,
                content_results: list[dict[str, Any]],
                *,
                navigate_delay: float = 0.0,
            ):
                unused = Path("/tmp/ace-proto-navigate-unit")
                super().__init__(
                    cdp_daemon.RuntimePaths(
                        directory=unused,
                        socket=unused / "daemon.sock",
                        startup_lock=unused / "startup.lock",
                        instance_lock=unused / "instance.lock",
                        state=unused / "state.json",
                        log=unused / "daemon.log",
                    )
                )
                self.now = 0.0
                self.navigate_delay = navigate_delay
                self.content_results = list(content_results)
                self.sent: list[tuple[float, str]] = []
                self._register_session("session", "target")
                self.content_baselines["session"] = {
                    "content": {"text": "old baseline"}
                }

            def _action_time(self) -> float:
                return self.now

            async def _action_sleep(self, delay: float) -> None:
                self.now += delay

            async def send_cdp(
                self,
                method: str,
                params: dict[str, Any] | None = None,
                session_id: str | None = None,
                timeout: float = 30,
            ) -> dict[str, Any]:
                del params, timeout
                self.sent.append((self.now, method))
                response: dict[str, Any] = {
                    "id": len(self.sent),
                    "sessionId": session_id,
                }
                if method == "Page.navigate":
                    self.now += self.navigate_delay
                    response["result"] = {
                        "frameId": "frame",
                        "loaderId": "loader",
                    }
                elif method == "Page.getAIPageContent":
                    response["result"] = self.content_results.pop(0)
                else:  # pragma: no cover - navigate_session owns the method set
                    raise AssertionError(method)
                return response

        first_not_ready = page(False, "first-not-ready")
        second_not_ready = page(False, "second-not-ready")
        ready_page = page(True, "ready")
        successful = ClockNavigateDaemon(
            [first_not_ready, second_not_ready, ready_page]
        )
        result = await successful.navigate_session(
            "session", "https://example.test/ready", True, 2
        )
        self.assertEqual(result["contentResponse"]["result"], ready_page)
        self.assertEqual(
            successful.sent,
            [
                (0.0, "Page.navigate"),
                (0.0, "Page.getAIPageContent"),
                (0.5, "Page.getAIPageContent"),
                (1.0, "Page.getAIPageContent"),
            ],
        )
        self.assertEqual(successful.content_baselines["session"], ready_page)

        timed_out = ClockNavigateDaemon(
            [first_not_ready, first_not_ready],
            navigate_delay=0.75,
        )
        with self.assertRaises(cdp_daemon.NavigateTimeoutError) as raised:
            await timed_out.navigate_session(
                "session", "https://example.test/timeout", True, 1
            )
        self.assertEqual(
            timed_out.sent,
            [
                (0.0, "Page.navigate"),
                (0.75, "Page.getAIPageContent"),
            ],
        )
        self.assertIs(raised.exception.data["lastReady"], False)
        self.assertNotIn("session", timed_out.content_baselines)

        timed_out_before_read = ClockNavigateDaemon([], navigate_delay=1.25)
        with self.assertRaises(cdp_daemon.NavigateTimeoutError) as raised:
            await timed_out_before_read.navigate_session(
                "session", "https://example.test/timeout-before-read", True, 1
            )
        self.assertEqual(
            timed_out_before_read.sent,
            [(0.0, "Page.navigate")],
        )
        self.assertIsNone(raised.exception.data["lastReady"])
        self.assertIn("navigateResponse", raised.exception.data)
        self.assertNotIn("session", timed_out_before_read.content_baselines)

        invalid = ClockNavigateDaemon([page("true", "invalid")])
        with self.assertRaisesRegex(
            cdp_daemon.TransportError, "invalid ready value"
        ):
            await invalid.navigate_session(
                "session", "https://example.test/invalid", True, 2
            )
        self.assertNotIn("session", invalid.content_baselines)

    async def test_action_polling_uses_one_second_deadlines_without_real_sleep(
        self,
    ) -> None:
        scripts = str(SKILL_ROOT / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import cdp_daemon

        no_op = {"effects": [{"effect": "no_op"}]}

        class ClockDaemon(cdp_daemon.CdpDaemon):
            def __init__(self, changes: list[dict[str, Any]]):
                unused = Path("/tmp/ace-proto-action-unit")
                super().__init__(
                    cdp_daemon.RuntimePaths(
                        directory=unused,
                        socket=unused / "daemon.sock",
                        startup_lock=unused / "startup.lock",
                        instance_lock=unused / "instance.lock",
                        state=unused / "state.json",
                        log=unused / "daemon.log",
                    )
                )
                self.now = 0.0
                self.changes = list(changes)
                self.sent: list[tuple[float, str]] = []
                self._register_session("session", "target")
                self.content_baselines["session"] = {"content": {"text": "before"}}

            def _action_time(self) -> float:
                return self.now

            async def _action_sleep(self, delay: float) -> None:
                self.now += delay

            async def send_cdp(
                self,
                method: str,
                params: dict[str, Any] | None = None,
                session_id: str | None = None,
                timeout: float = 30,
            ) -> dict[str, Any]:
                del params, timeout
                self.sent.append((self.now, method))
                response: dict[str, Any] = {
                    "id": len(self.sent),
                    "sessionId": session_id,
                }
                if method == "Page.performDOMAction":
                    response["result"] = {"ok": True}
                elif method == "Page.getAIPageActionChanges":
                    response["result"] = self.changes.pop(0)
                elif method == "Page.getAIPageContent":
                    response["result"] = {"content": {"text": "after"}}
                else:  # pragma: no cover - action_session owns the method set
                    raise AssertionError(method)
                return response

        default_window = ClockDaemon([no_op, no_op, no_op])
        result = await default_window.action_session(
            "session", {"id": 1, "action": "click"}, 3
        )
        self.assertEqual(result["changes"], no_op)
        self.assertEqual(
            [
                at
                for at, method in default_window.sent
                if method == "Page.getAIPageActionChanges"
            ],
            [1.0, 2.0, 3.0],
        )

        long_window = ClockDaemon([no_op, no_op, no_op, no_op])
        await long_window.action_session("session", {"id": 1, "action": "click"}, 4)
        self.assertEqual(
            [
                at
                for at, method in long_window.sent
                if method == "Page.getAIPageActionChanges"
            ],
            [1.0, 2.0, 3.0, 4.0],
        )

        navigation = {"effects": [{"effect": "navigation"}]}
        stops_early = ClockDaemon([navigation, no_op])
        await stops_early.action_session("session", {"id": 1, "action": "click"}, 3)
        self.assertEqual(
            [
                at
                for at, method in stops_early.sent
                if method == "Page.getAIPageActionChanges"
            ],
            [1.0],
        )


def load_tests(
    loader: unittest.TestLoader,
    _standard_tests: unittest.TestSuite,
    _pattern: str | None,
) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    if IN_TEST_NETNS:
        suite.addTests(loader.loadTestsFromTestCase(ActionPollingUnitTests))
        suite.addTests(loader.loadTestsFromTestCase(CDPDaemonBlackBoxTests))
    else:
        suite.addTests(loader.loadTestsFromTestCase(StaticContractTests))
        suite.addTests(loader.loadTestsFromTestCase(NetworkNamespaceSuite))
    return suite


if __name__ == "__main__":
    unittest.main()
