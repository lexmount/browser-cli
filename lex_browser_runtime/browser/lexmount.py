"""Lexmount SDK lifecycle helpers shared by SDK callers and CLI tools."""

from __future__ import annotations

import inspect
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, NoReturn
from urllib.parse import quote_plus

from pydantic import BaseModel, Field

from lex_browser_runtime.browser.models import (
    BrowserConfigError,
    BrowserParallelLimitError,
    BrowserRuntimeError,
    CreateBrowserRequest,
)

REQUIRED_LEXMOUNT_ENV_VARS = ("LEXMOUNT_API_KEY", "LEXMOUNT_PROJECT_ID")


class LexmountPaginationInfo(BaseModel):
    """Normalized pagination metadata returned by the Lexmount SDK."""

    current_page: int | None = None
    page_size: int | None = None
    total_count: int | None = None
    total_pages: int | None = None
    active_count: int | None = None
    closed_count: int | None = None


class LexmountSessionRecord(BaseModel):
    """Normalized Lexmount session descriptor."""

    session_id: str | None = None
    status: str | None = None
    browser_mode: str | None = None
    project_id: str | None = None
    created_at: Any | None = None
    inspect_url: str | None = None
    inspect_url_dbg: str | None = None
    container_id: str | None = None
    connect_url: str | None = None


class LexmountContextRecord(BaseModel):
    """Normalized Lexmount context descriptor."""

    context_id: str | None = None
    status: str | None = None
    created_at: Any | None = None
    updated_at: Any | None = None
    metadata: dict[str, Any] | None = None
    region_id: str | None = None
    regionId: str | None = None
    description: str | None = None
    display_name: str | None = None
    displayName: str | None = None


class LexmountErrorInfo(BaseModel):
    """Structured Lexmount SDK error that can be emitted as JSON."""

    code: str
    message: str
    status_code: int | None = None
    response: Any | None = None

    def payload(self) -> dict[str, Any]:
        """Return a JSON-friendly error payload without null optional fields."""

        payload: dict[str, Any] = {
            "error": self.code,
            "message": self.message,
        }
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.response is not None:
            payload["response"] = self.response
        return payload


class SessionCreateResult(BaseModel):
    """Result returned after creating a Lexmount browser session."""

    mode: str = "sdk"
    base_url: str | None = None
    project_id: str | None = None
    context_id: str | None = None
    created_context: bool = False
    context_mode: str = "read_write"
    browser_mode: str = "normal"
    session: LexmountSessionRecord


class SessionListResult(BaseModel):
    """Result returned after listing Lexmount browser sessions."""

    count: int
    status_filter: str | None = None
    sessions: list[LexmountSessionRecord] = Field(default_factory=list)
    pagination: LexmountPaginationInfo | None = None


class ContextListResult(BaseModel):
    """Result returned after listing Lexmount contexts."""

    count: int
    status_filter: str | None = None
    limit: int | None = None
    contexts: list[LexmountContextRecord] = Field(default_factory=list)


@dataclass(slots=True)
class LexmountModules:
    """Imported Lexmount SDK module objects."""

    lexmount_cls: type[Any]
    lexmount_error_cls: type[Exception] | tuple[type[Exception], ...]
    validation_error_cls: type[Exception] | tuple[type[Exception], ...]


def missing_lexmount_env_vars(env: dict[str, str] | None = None) -> list[str]:
    """Return required Lexmount environment variables that are unset."""

    source = env if env is not None else os.environ
    return [name for name in REQUIRED_LEXMOUNT_ENV_VARS if not source.get(name)]


def build_direct_connect_url(
    *,
    api_key: str | None = None,
    project_id: str | None = None,
    base_url: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Build the direct shared-browser Lexmount websocket URL.

    The current Lexmount direct websocket protocol carries credentials in query
    parameters, so callers should treat the returned URL as secret material and
    avoid writing it to persistent logs.

    Raises:
        BrowserConfigError: if credentials are missing or the base URL is invalid.
    """

    source = env if env is not None else os.environ
    resolved_api_key = api_key or source.get("LEXMOUNT_API_KEY")
    resolved_project_id = project_id or source.get("LEXMOUNT_PROJECT_ID")
    missing = []
    if not resolved_api_key:
        missing.append("LEXMOUNT_API_KEY")
    if not resolved_project_id:
        missing.append("LEXMOUNT_PROJECT_ID")
    if missing:
        raise BrowserConfigError(
            "Missing required Lexmount environment variables: " + ", ".join(missing)
        )
    assert resolved_api_key is not None
    assert resolved_project_id is not None

    resolved_base_url = (
        base_url or source.get("LEXMOUNT_BASE_URL") or "https://api.lexmount.cn"
    ).rstrip("/")
    if resolved_base_url.startswith("https://"):
        ws_base = "wss://" + resolved_base_url[len("https://") :]
    elif resolved_base_url.startswith("http://"):
        ws_base = "ws://" + resolved_base_url[len("http://") :]
    elif resolved_base_url.startswith(("ws://", "wss://")):
        ws_base = resolved_base_url
    else:
        raise BrowserConfigError(
            "LEXMOUNT_BASE_URL must start with http://, https://, ws://, or wss://"
        )

    return (
        f"{ws_base}/connection?project_id={quote_plus(resolved_project_id)}"
        f"&api_key={quote_plus(resolved_api_key)}"
    )


def normalize_lexmount_sdk_error(exc: Exception) -> LexmountErrorInfo:
    """Normalize SDK exceptions into stable, skill-compatible error fields."""

    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    message = str(exc)
    code = exc.__class__.__name__

    if status_code == 429:
        response_text = ""
        if isinstance(response, dict):
            response_text = str(response)
        elif response is not None:
            response_text = str(response)
        combined = f"{message} {response_text}".lower()
        if (
            "active session limit reached" in combined
            or "parallel" in combined
            or "并行额度" in combined
            or "额度到达上限" in combined
            or "额度已达上限" in combined
        ):
            code = "browser_parallel_limit_reached"
            message = "浏览器并行额度已达上限，当前无法创建新的 browser，请先关闭部分 session 后重试。"

    return LexmountErrorInfo(
        code=code,
        message=message,
        status_code=status_code if isinstance(status_code, int) else None,
        response=response,
    )


def raise_normalized_lexmount_error(exc: Exception) -> NoReturn:
    """Raise a runtime exception from a normalized Lexmount SDK error."""

    error = normalize_lexmount_sdk_error(exc)
    runtime_error: BrowserRuntimeError
    if error.code == "browser_parallel_limit_reached":
        runtime_error = BrowserParallelLimitError(error.message)
    else:
        runtime_error = BrowserRuntimeError(error.message)
    setattr(runtime_error, "lexmount_error_info", error)
    raise runtime_error from exc


def load_lexmount_modules() -> LexmountModules:
    """Import the Lexmount SDK and return the classes used by runtime helpers."""

    try:
        from lexmount import Lexmount  # type: ignore[import-not-found, import-untyped]

        try:
            from lexmount.exceptions import (  # type: ignore[import-not-found, import-untyped]
                LexmountError,
                ValidationError,
            )
        except ImportError:
            from lexmount import ValidationError  # type: ignore[import-not-found, import-untyped]

            LexmountError = Exception
    except ImportError as exc:
        raise BrowserConfigError(
            "Failed to import the lexmount SDK. Install browser-cli "
            "or provide an environment that already includes lexmount."
        ) from exc

    return LexmountModules(
        lexmount_cls=Lexmount,
        lexmount_error_cls=LexmountError,
        validation_error_cls=ValidationError,
    )


def accepts_keyword_arg(callable_obj: Any, keyword: str) -> bool:
    """Return whether a callable accepts a specific keyword argument."""

    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return True
    if keyword in signature.parameters:
        return True
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


def _client_kwargs_from_request(request: CreateBrowserRequest) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "api_key": request.api_key,
            "project_id": request.project_id,
            "base_url": request.base_url,
            "timeout": request.timeout,
        }.items()
        if value is not None
    }


def build_lexmount_client(request: CreateBrowserRequest | None = None) -> Any:
    """Build a Lexmount SDK client from an explicit request or environment."""

    resolved_request = request or CreateBrowserRequest.from_env()
    missing = missing_lexmount_env_vars(
        {
            "LEXMOUNT_API_KEY": resolved_request.api_key or "",
            "LEXMOUNT_PROJECT_ID": resolved_request.project_id or "",
        }
    )
    if missing:
        raise BrowserConfigError(
            "Missing required Lexmount environment variables: " + ", ".join(missing)
        )

    modules = load_lexmount_modules()
    kwargs = _client_kwargs_from_request(resolved_request)
    if resolved_request.region is not None and accepts_keyword_arg(
        modules.lexmount_cls,
        "region",
    ):
        kwargs["region"] = resolved_request.region
    try:
        client = modules.lexmount_cls(**kwargs)
    except modules.validation_error_cls as exc:
        raise BrowserConfigError(str(exc)) from exc
    except Exception as exc:
        raise_normalized_lexmount_error(exc)
    return client


def serialize_session(session: Any) -> LexmountSessionRecord:
    """Serialize a Lexmount SDK session object into a stable model."""

    return LexmountSessionRecord(
        session_id=getattr(session, "id", None) or getattr(session, "session_id", None),
        status=getattr(session, "status", None),
        browser_mode=getattr(session, "browser_type", None),
        project_id=getattr(session, "project_id", None),
        created_at=getattr(session, "created_at", None),
        inspect_url=getattr(session, "inspect_url", None),
        inspect_url_dbg=getattr(session, "inspect_url_dbg", None),
        container_id=getattr(session, "container_id", None),
        connect_url=getattr(session, "connect_url", None)
        or getattr(session, "ws", None),
    )


def serialize_context(context: Any) -> LexmountContextRecord:
    """Serialize a Lexmount SDK context object into a stable model."""

    context_id = getattr(context, "id", None) or getattr(context, "context_id", None)
    region_id = getattr(context, "region_id", None) or getattr(
        context, "regionId", None
    )
    display_name = (
        getattr(context, "display_name", None)
        or getattr(context, "displayName", None)
        or getattr(context, "description", None)
        or context_id
    )
    return LexmountContextRecord(
        context_id=context_id,
        status=getattr(context, "status", None),
        created_at=getattr(context, "created_at", None),
        updated_at=getattr(context, "updated_at", None),
        metadata=getattr(context, "metadata", None),
        region_id=region_id,
        regionId=region_id,
        description=getattr(context, "description", None),
        display_name=display_name,
        displayName=display_name,
    )


def _iter_items(result: Any, attr: str) -> list[Any]:
    if hasattr(result, attr):
        value = getattr(result, attr)
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
            return list(value)
    if isinstance(result, Iterable) and not isinstance(result, (str, bytes, dict)):
        return list(result)
    return []


def _pagination_from_result(result: Any) -> LexmountPaginationInfo | None:
    pagination = getattr(result, "pagination", None)
    if pagination is None:
        return None
    return LexmountPaginationInfo(
        current_page=getattr(pagination, "current_page", None),
        page_size=getattr(pagination, "page_size", None),
        total_count=getattr(pagination, "total_count", None),
        total_pages=getattr(pagination, "total_pages", None),
        active_count=getattr(pagination, "active_count", None),
        closed_count=getattr(pagination, "closed_count", None),
    )


def _call_with_optional_kwargs(callable_obj: Any, **kwargs: Any) -> Any:
    supported = {
        key: value
        for key, value in kwargs.items()
        if value is not None and accepts_keyword_arg(callable_obj, key)
    }
    return callable_obj(**supported)


def _add_note(exc: BaseException, note: str) -> None:
    if hasattr(exc, "add_note"):
        exc.add_note(note)


class LexmountBrowserAdmin:
    """Synchronous Lexmount SDK admin wrapper used by CLIs and tools."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        """Return the underlying Lexmount client, constructing it lazily."""

        if self._client is None:
            self._client = build_lexmount_client()
        return self._client

    def create_session(
        self,
        *,
        context_id: str | None = None,
        create_context: bool = False,
        context_mode: str = "read_write",
        browser_mode: str = "normal",
        metadata: dict[str, Any] | None = None,
        downloads: dict[str, bool] | None = None,
        recording: dict[str, bool] | None = None,
    ) -> SessionCreateResult:
        """Create a Lexmount session, optionally creating or reusing a context."""

        created_context = False
        resolved_context_id = context_id
        try:
            if create_context and not resolved_context_id:
                context = self.client.contexts.create(metadata=metadata)
                resolved_context_id = str(context.id)
                created_context = True

            session_kwargs: dict[str, Any] = {"browser_mode": browser_mode}
            if resolved_context_id:
                session_kwargs["context"] = {
                    "id": resolved_context_id,
                    "mode": context_mode,
                }
            if downloads is not None:
                session_kwargs["downloads"] = downloads
            if recording is not None:
                session_kwargs["recording"] = recording
            try:
                session = self.client.sessions.create(**session_kwargs)
            except Exception as exc:
                if created_context and resolved_context_id:
                    try:
                        self.client.contexts.delete(resolved_context_id)
                    except Exception as cleanup_exc:
                        _add_note(
                            exc,
                            (
                                "Also failed to delete newly created Lexmount "
                                f"context {resolved_context_id}: {cleanup_exc}"
                            ),
                        )
                raise
        except Exception as exc:
            raise_normalized_lexmount_error(exc)

        return SessionCreateResult(
            base_url=getattr(self.client, "base_url", None),
            project_id=getattr(self.client, "project_id", None),
            context_id=resolved_context_id,
            created_context=created_context,
            context_mode=context_mode,
            browser_mode=browser_mode,
            session=serialize_session(session),
        )

    def list_sessions(
        self,
        *,
        status: str | None = None,
        page: int | None = None,
        limit: int | None = None,
    ) -> SessionListResult:
        """List Lexmount sessions for the current project."""

        try:
            result = _call_with_optional_kwargs(
                self.client.sessions.list,
                status=status,
                page=page,
                limit=limit,
                page_size=limit,
            )
        except Exception as exc:
            raise_normalized_lexmount_error(exc)

        sessions = [serialize_session(item) for item in _iter_items(result, "sessions")]
        return SessionListResult(
            count=len(sessions),
            status_filter=status,
            sessions=sessions,
            pagination=_pagination_from_result(result),
        )

    def get_session(self, session_id: str) -> LexmountSessionRecord:
        """Resolve one session by id using SDK get or paginated list endpoints."""

        get_method = getattr(self.client.sessions, "get", None)
        if callable(get_method):
            try:
                if accepts_keyword_arg(get_method, "session_id"):
                    return serialize_session(get_method(session_id=session_id))
                return serialize_session(get_method(session_id))
            except TypeError:
                # Older SDKs may expose a get attribute with an incompatible
                # signature; fall through to list pagination.
                pass
            except Exception as exc:
                raise_normalized_lexmount_error(exc)

        seen_pages: set[int] = set()
        page: int | None = None
        while True:
            listed = self.list_sessions(page=page)
            for session in listed.sessions:
                if session.session_id == session_id:
                    return session

            pagination = listed.pagination
            if pagination is None or pagination.current_page is None:
                break
            current_page = pagination.current_page
            total_pages = pagination.total_pages
            if current_page in seen_pages:
                break
            seen_pages.add(current_page)
            if total_pages is None or current_page >= total_pages:
                break
            page = current_page + 1

        raise BrowserRuntimeError(
            f"Session '{session_id}' was not found in sessions.list()."
        )

    def close_session(self, session_id: str) -> None:
        """Close a Lexmount session by id."""

        try:
            self.client.sessions.delete(session_id=session_id)
        except Exception as exc:
            raise_normalized_lexmount_error(exc)

    def keepalive_session(
        self,
        *,
        session_id: str,
        interval: float = 5.0,
        duration: float = 60.0,
        stop_on_inactive: bool = False,
    ) -> dict[str, Any]:
        """Poll a session and return the observed status snapshots."""

        started_at = time.time()
        snapshots: list[dict[str, Any]] = []
        single_check = duration <= 0
        deadline = started_at + duration if duration > 0 else None

        while True:
            session = self.get_session(session_id)
            snapshot = {
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "session": session.model_dump(mode="json"),
            }
            snapshots.append(snapshot)

            if session.status != "active" and stop_on_inactive:
                break
            if single_check:
                break
            if deadline is not None and time.time() >= deadline:
                break
            time.sleep(interval)

        final_session = snapshots[-1]["session"] if snapshots else None
        return {
            "session_id": session_id,
            "interval_seconds": interval,
            "duration_seconds": duration,
            "checks": len(snapshots),
            "final_status": final_session.get("status") if final_session else None,
            "snapshots": snapshots,
        }

    def create_context(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> LexmountContextRecord:
        """Create a persistent Lexmount browser context."""

        try:
            kwargs: dict[str, Any] = {"metadata": metadata}
            if description is not None:
                kwargs["description"] = description
            context = self.client.contexts.create(**kwargs)
        except Exception as exc:
            raise_normalized_lexmount_error(exc)
        return serialize_context(context)

    def list_contexts(
        self,
        *,
        status: str | None = None,
        limit: int | None = 20,
    ) -> ContextListResult:
        """List persistent Lexmount browser contexts."""

        try:
            result = _call_with_optional_kwargs(
                self.client.contexts.list,
                status=status,
                limit=limit,
            )
        except Exception as exc:
            raise_normalized_lexmount_error(exc)

        contexts = [serialize_context(item) for item in _iter_items(result, "contexts")]
        return ContextListResult(
            count=len(contexts),
            status_filter=status,
            limit=limit,
            contexts=contexts,
        )

    def get_context(self, context_id: str) -> LexmountContextRecord:
        """Get one persistent Lexmount browser context."""

        try:
            context = self.client.contexts.get(context_id)
        except Exception as exc:
            raise_normalized_lexmount_error(exc)
        return serialize_context(context)

    def delete_context(self, context_id: str) -> None:
        """Delete a persistent Lexmount browser context."""

        try:
            self.client.contexts.delete(context_id)
        except Exception as exc:
            raise_normalized_lexmount_error(exc)
