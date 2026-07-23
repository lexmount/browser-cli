"""Local auth context storage for reusable Lexmount login state."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lex_browser_runtime.browser.models import BrowserConfigError

AUTH_CONTEXTS_ENV = "LEX_BROWSER_AUTH_CONTEXTS_FILE"
AUTH_CONTEXTS_VERSION = 1


@dataclass(frozen=True)
class AuthContextEntry:
    """One saved Lexmount context for a research source."""

    context_id: str
    context_mode: str = "read_write"
    login_url: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.context_id:
            raise BrowserConfigError("auth context entry requires context_id")
        if self.context_mode not in {"read_only", "read_write"}:
            raise BrowserConfigError(
                "auth context entry context_mode must be read_only or read_write"
            )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AuthContextEntry":
        """Build an entry from a JSON object."""

        context_id = payload.get("context_id")
        if not isinstance(context_id, str):
            raise BrowserConfigError("auth context entry context_id must be a string")
        context_mode = payload.get("context_mode", "read_write")
        if not isinstance(context_mode, str):
            raise BrowserConfigError("auth context entry context_mode must be a string")
        login_url = payload.get("login_url")
        if login_url is not None and not isinstance(login_url, str):
            raise BrowserConfigError("auth context entry login_url must be a string")
        updated_at = payload.get("updated_at")
        if updated_at is not None and not isinstance(updated_at, str):
            raise BrowserConfigError("auth context entry updated_at must be a string")
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise BrowserConfigError("auth context entry metadata must be an object")
        return cls(
            context_id=context_id,
            context_mode=context_mode,
            login_url=login_url,
            updated_at=updated_at,
            metadata=dict(metadata),
        )

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        payload: dict[str, Any] = {
            "context_id": self.context_id,
            "context_mode": self.context_mode,
        }
        if self.login_url is not None:
            payload["login_url"] = self.login_url
        if self.updated_at is not None:
            payload["updated_at"] = self.updated_at
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload


@dataclass(frozen=True)
class AuthContextStore:
    """Auth context mappings keyed by research source id."""

    contexts: dict[str, AuthContextEntry]

    def get(self, source_id: str) -> AuthContextEntry | None:
        """Return the saved auth context for one research source."""

        return self.contexts.get(source_id)

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-serializable store payload."""

        return {
            "version": AUTH_CONTEXTS_VERSION,
            "contexts": {
                source_id: entry.to_payload()
                for source_id, entry in sorted(self.contexts.items())
            },
        }


def default_auth_contexts_path(env: dict[str, str] | None = None) -> Path:
    """Return the default local auth context file path."""

    source = env if env is not None else os.environ
    override = source.get(AUTH_CONTEXTS_ENV)
    if override:
        return Path(override).expanduser()
    home = Path(source.get("HOME") or str(Path.home())).expanduser()
    return home / ".lex-browser-runtime" / "auth-contexts.json"


def load_auth_context_store(path: str | Path | None = None) -> AuthContextStore:
    """Load saved auth contexts from disk.

    Missing files return an empty store. Malformed files fail fast because using
    the wrong browser account context would make research results misleading.
    """

    resolved_path = (
        Path(path).expanduser() if path is not None else default_auth_contexts_path()
    )
    if not resolved_path.exists():
        return AuthContextStore(contexts={})
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BrowserConfigError(
            f"auth contexts file is not valid JSON: {resolved_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise BrowserConfigError("auth contexts file must contain a JSON object")
    raw_contexts = payload.get("contexts", {})
    if not isinstance(raw_contexts, dict):
        raise BrowserConfigError("auth contexts file contexts must be an object")
    contexts: dict[str, AuthContextEntry] = {}
    for source_id, raw_entry in raw_contexts.items():
        if not isinstance(source_id, str):
            raise BrowserConfigError("auth context source id must be a string")
        if not isinstance(raw_entry, dict):
            raise BrowserConfigError(
                f"auth context entry for {source_id!r} must be an object"
            )
        contexts[source_id] = AuthContextEntry.from_payload(raw_entry)
    return AuthContextStore(contexts=contexts)


def write_auth_context_store(
    path: str | Path,
    store: AuthContextStore,
) -> None:
    """Write an auth context store atomically enough for local CLI use."""

    resolved_path = Path(path).expanduser()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(
        json.dumps(store.to_payload(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def save_auth_context_entry(
    path: str | Path,
    source_id: str,
    entry: AuthContextEntry,
) -> None:
    """Upsert one auth context entry in the local store."""

    if not source_id:
        raise BrowserConfigError("source_id is required")
    store = load_auth_context_store(path)
    contexts = dict(store.contexts)
    contexts[source_id] = entry
    write_auth_context_store(path, AuthContextStore(contexts=contexts))
