"""Browser backend implementations."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import uuid
from typing import Any, Protocol
from urllib.parse import quote, urlparse

from lex_browser_runtime.browser.models import (
    BrowserAuthError,
    BrowserConfigError,
    BrowserParallelLimitError,
    BrowserRuntimeError,
    BrowserSessionInfo,
    CreateBrowserRequest,
)
from lex_browser_runtime.browser.lexmount import normalize_lexmount_sdk_error

logger = logging.getLogger(__name__)


class BrowserBackend(Protocol):
    """Protocol implemented by browser lifecycle backends."""

    async def create_browser(
        self,
        request: CreateBrowserRequest | None = None,
    ) -> BrowserSessionInfo:
        """Create or attach to a browser session."""

    async def close_browser(self, session_id: str | None = None) -> None:
        """Close a browser session if this backend owns it."""


class ExistingCdpBackend:
    """Backend that wraps an existing CDP URL without owning the browser."""

    def __init__(
        self,
        cdp_url: str,
        *,
        session_id: str | None = None,
        inspect_url: str | None = None,
    ) -> None:
        self._cdp_url = cdp_url
        self._session_id = session_id or f"existing-cdp-{uuid.uuid4().hex[:12]}"
        self._inspect_url = inspect_url

    async def create_browser(
        self,
        request: CreateBrowserRequest | None = None,
    ) -> BrowserSessionInfo:
        """Return the existing CDP session descriptor."""

        del request
        return BrowserSessionInfo(
            id=self._session_id,
            status="connected",
            cdp_url=self._cdp_url,
            browser_type="chromium",
            inspect_url=self._inspect_url,
            backend="existing-cdp",
        )

    async def close_browser(self, session_id: str | None = None) -> None:
        """Do nothing because this backend does not own the browser."""

        del session_id


def _accepts_keyword_arg(callable_obj: Any, keyword: str) -> bool:
    """Return whether a callable accepts a specific keyword argument."""

    signature = inspect.signature(callable_obj)
    if keyword in signature.parameters:
        return True
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


class LexmountBackend:
    """Async wrapper around the sync Lexmount SDK."""

    def __init__(self) -> None:
        self._state_lock = threading.RLock()
        self.current_session_id: str | None = None
        self.current_inspect_url: str | None = None
        self.current_forked_context_id: str | None = None
        self.current_delete_context_on_close: bool = True
        self._client: Any | None = None
        self._session: Any | None = None

    async def create_browser(
        self,
        request: CreateBrowserRequest | None = None,
    ) -> BrowserSessionInfo:
        """Create a Lexmount browser session and return a CDP descriptor."""

        browser_request = request or CreateBrowserRequest.from_env()
        try:
            return await asyncio.to_thread(self._create_browser_sync, browser_request)
        except BrowserRuntimeError:
            raise
        except Exception as exc:
            raise BrowserRuntimeError(
                f"Unexpected error creating Lexmount browser: {exc}",
            ) from exc

    def create_browser_sync(
        self,
        request: CreateBrowserRequest | None = None,
    ) -> BrowserSessionInfo:
        """Synchronous wrapper for hosts that do not own an event loop."""

        browser_request = request or CreateBrowserRequest.from_env()
        try:
            return self._create_browser_sync(browser_request)
        except BrowserRuntimeError:
            raise
        except Exception as exc:
            raise BrowserRuntimeError(
                f"Unexpected error creating Lexmount browser: {exc}",
            ) from exc

    def _create_browser_sync(self, request: CreateBrowserRequest) -> BrowserSessionInfo:
        with self._state_lock:
            if (
                self._client is not None
                or self._session is not None
                or self.current_session_id is not None
            ):
                raise BrowserRuntimeError(
                    "LexmountBackend already owns an active browser session; "
                    "close it before creating another one.",
                )
            return self._create_browser_sync_unlocked(request)

    def _create_browser_sync_unlocked(
        self,
        request: CreateBrowserRequest,
    ) -> BrowserSessionInfo:
        import lexmount as lexmount_sdk  # type: ignore[import-not-found, import-untyped]
        from lexmount import (
            APIError,
            AuthenticationError,
            Lexmount,
            NetworkError,
            TimeoutError,
            ValidationError,
        )  # type: ignore[import-not-found, import-untyped]

        client = None
        session = None
        try:
            self.current_forked_context_id = None
            client_kwargs: dict[str, Any] = {
                key: value
                for key, value in {
                    "api_key": request.api_key,
                    "project_id": request.project_id,
                    "base_url": request.base_url,
                    "timeout": request.timeout,
                }.items()
                if value is not None
            }
            if request.region is not None and _accepts_keyword_arg(Lexmount, "region"):
                client_kwargs["region"] = request.region
            client = Lexmount(**client_kwargs)
            if request.verify_ssl is False:
                self._disable_ssl_verification(client)
            self.current_delete_context_on_close = request.delete_context_on_close
            runtime_session_proxy_cls = getattr(
                lexmount_sdk, "SessionProxyConfig", None
            )
            if runtime_session_proxy_cls is None:
                try:
                    from lexmount._sessions import (  # type: ignore[import-not-found, import-untyped]
                        SessionProxyConfig as ImportedSessionProxyConfig,
                    )

                    runtime_session_proxy_cls = ImportedSessionProxyConfig
                except Exception:
                    runtime_session_proxy_cls = None
            proxy = (
                runtime_session_proxy_cls(**request.proxy)
                if request.proxy is not None and runtime_session_proxy_cls is not None
                else None
            )
            context = self._resolve_session_context(client, request)
            create_kwargs: dict[str, Any] = {
                "browser_mode": request.browser_mode,
            }
            if context is not None:
                create_kwargs["context"] = context
            if request.downloads_enabled:
                create_kwargs["downloads"] = {"enabled": True}
            if request.recording_persistent:
                create_kwargs["recording"] = {"persistent": True}
            optional_params = {
                "extension_ids": request.extension_ids,
                "proxy": proxy,
                "weak_lock": request.weak_lock,
                "async_create": request.async_create,
                "poll_interval_sec": request.poll_interval_sec,
                "poll_timeout_sec": request.poll_timeout_sec,
            }
            for key, value in optional_params.items():
                if _accepts_keyword_arg(client.sessions.create, key):
                    create_kwargs[key] = value
            session = client.sessions.create(**create_kwargs)
            session_id = str(
                getattr(session, "id", None)
                or getattr(session, "session_id", None)
                or ""
            )
            if not session_id:
                raise BrowserRuntimeError("Lexmount session did not provide an id")
            connect_url = getattr(session, "connect_url", None) or getattr(
                session,
                "ws",
                None,
            )
            inspect_url = getattr(session, "inspect_url", None) or getattr(
                session,
                "inspect_url_dbg",
                None,
            )
            if not connect_url:
                connect_url, inspect_url = self._resolve_session_urls(
                    client,
                    session_id,
                    inspect_url,
                )
            if not inspect_url:
                inspect_url = self._build_debug_url(
                    base_url=str(
                        getattr(client, "base_url", None) or request.base_url or ""
                    ),
                    session_id=session_id,
                )
            if not connect_url:
                raise BrowserRuntimeError(
                    f"Lexmount session {session_id} did not provide a CDP URL",
                )
        except AuthenticationError as exc:
            self._cleanup_failed_create(client, session, request)
            raise BrowserAuthError(f"Lexmount authentication failed: {exc}") from exc
        except ValidationError as exc:
            self._cleanup_failed_create(client, session, request)
            raise BrowserConfigError(
                f"Lexmount configuration is invalid: {exc}",
            ) from exc
        except TimeoutError as exc:
            self._cleanup_failed_create(client, session, request)
            raise BrowserRuntimeError(
                f"Timeout while creating Lexmount browser: {exc}",
            ) from exc
        except NetworkError as exc:
            self._cleanup_failed_create(client, session, request)
            raise BrowserRuntimeError(
                f"Network error while creating Lexmount browser: {exc}",
            ) from exc
        except APIError as exc:
            self._cleanup_failed_create(client, session, request)
            normalized = normalize_lexmount_sdk_error(exc)
            if normalized.code == "browser_parallel_limit_reached":
                raise BrowserParallelLimitError(normalized.message) from exc
            raise BrowserRuntimeError(
                f"Lexmount API error while creating browser: {normalized.message}",
            ) from exc
        except Exception:
            self._cleanup_failed_create(client, session, request)
            raise

        self._client = client
        self._session = session
        self.current_session_id = session_id
        self.current_inspect_url = inspect_url

        return BrowserSessionInfo(
            id=session_id,
            status=str(getattr(session, "status", "active") or "active"),
            cdp_url=connect_url,
            browser_type=str(
                getattr(session, "browser_type", "chromium") or "chromium"
            ),
            inspect_url=inspect_url,
            created_at=getattr(session, "created_at", None),
            backend="lexmount",
            metadata={
                "base_context_id": request.base_context_id,
                "forked_context_id": self.current_forked_context_id,
                "delete_context_on_close": request.delete_context_on_close,
                "browser_mode": request.browser_mode,
            },
        )

    def _cleanup_failed_create(
        self,
        client: Any | None,
        session: Any | None,
        request: CreateBrowserRequest,
    ) -> None:
        """Best-effort cleanup for any failed browser creation path."""

        if session is not None:
            try:
                session.close()
            except Exception as exc:
                logger.debug("Failed to close session after create error: %s", exc)
        if (
            client is not None
            and self.current_forked_context_id
            and request.delete_context_on_close
        ):
            try:
                client.contexts.delete(self.current_forked_context_id)
            except Exception as exc:
                logger.debug(
                    "Failed to delete forked context %s after create error: %s",
                    self.current_forked_context_id,
                    exc,
                )
        if client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception as exc:
                logger.debug("Failed to close client after create error: %s", exc)
        self.current_forked_context_id = None
        self.current_delete_context_on_close = True

    @staticmethod
    def _disable_ssl_verification(client: Any) -> None:
        import httpx

        http_client = getattr(client, "_http_client", None)
        timeout = getattr(http_client, "timeout", None)
        client._http_client = httpx.Client(
            **({"timeout": timeout} if timeout is not None else {}),
            verify=False,
        )

    def _resolve_session_context(
        self,
        client: Any,
        request: CreateBrowserRequest,
    ) -> dict[str, Any] | None:
        if request.context is not None:
            return request.context
        if request.context_id:
            return {"id": request.context_id, "mode": request.context_mode}
        if not request.base_context_id:
            return None
        forked = client.contexts.fork(request.base_context_id)
        forked_context_id = str(getattr(forked, "id", "") or "").strip()
        if not forked_context_id:
            raise BrowserRuntimeError(
                f"Lexmount contexts.fork({request.base_context_id}) returned no id",
            )
        self.current_forked_context_id = forked_context_id
        return {"id": forked_context_id, "mode": request.context_mode}

    @staticmethod
    def _build_debug_url(base_url: str, session_id: str) -> str | None:
        if not base_url or not session_id:
            return None
        parsed = urlparse(base_url)
        api_host = parsed.hostname or ""
        if not api_host:
            return None
        viewer_host = api_host
        if api_host.startswith("api."):
            viewer_host = "browser." + api_host[4:]
        elif api_host.startswith("api") and len(api_host) > 3:
            viewer_host = api_host[3:]
        port = f":{parsed.port}" if parsed.port is not None else ""
        api_host_port = (
            f"{api_host}:{parsed.port}" if parsed.port is not None else api_host
        )
        scheme = parsed.scheme or "https"
        return (
            f"{scheme}://{viewer_host}{port}/browser_dev/index.html"
            f"?session_id={quote(session_id, safe='')}#api_host={quote(api_host_port, safe='')}"
        )

    def _resolve_session_urls(
        self,
        client: Any,
        session_id: str,
        fallback_inspect_url: str | None = None,
    ) -> tuple[str | None, str | None]:
        """Resolve active Lexmount session URLs when create response is incomplete."""

        response = client.sessions.list(status="active")
        for listed_session in getattr(response, "sessions", []):
            if getattr(listed_session, "id", None) != session_id:
                continue
            connect_url = getattr(listed_session, "connect_url", None) or getattr(
                listed_session,
                "ws",
                None,
            )
            inspect_url = (
                getattr(listed_session, "inspect_url", None)
                or getattr(listed_session, "inspect_url_dbg", None)
                or fallback_inspect_url
            )
            return connect_url, inspect_url
        return None, fallback_inspect_url

    async def close_browser(self, session_id: str | None = None) -> None:
        """Stop the active Lexmount browser session."""

        try:
            await asyncio.to_thread(self.close_browser_sync, session_id)
        except BrowserRuntimeError:
            raise
        except Exception as exc:
            raise BrowserRuntimeError(
                f"Unexpected error stopping Lexmount browser: {exc}",
            ) from exc

    def close_browser_sync(self, session_id: str | None = None) -> None:
        """Synchronous wrapper for hosts that do not own an event loop."""

        with self._state_lock:
            target_session_id = session_id or self.current_session_id
            if not target_session_id:
                return
            try:
                self._stop_browser_sync(target_session_id)
            except BrowserRuntimeError:
                raise
            except Exception as exc:
                raise BrowserRuntimeError(
                    f"Unexpected error stopping Lexmount browser: {exc}",
                ) from exc
            finally:
                self._close_sync()

    def _stop_browser_sync(self, session_id: str) -> None:
        from lexmount import (
            APIError,
            AuthenticationError,
            NetworkError,
            TimeoutError,
            ValidationError,
        )  # type: ignore[import-not-found]

        if (
            self._session is not None
            and str(
                getattr(self._session, "id", None)
                or getattr(self._session, "session_id", None)
                or ""
            )
            == session_id
        ):
            try:
                self._session.close()
            except Exception as exc:
                raise BrowserRuntimeError(
                    f"Failed to close Lexmount session {session_id}: {exc}",
                ) from exc
        if self._client is None:
            return
        try:
            self._client.sessions.delete(session_id=session_id)
        except AuthenticationError as exc:
            raise BrowserAuthError(
                f"Lexmount authentication failed while stopping browser: {exc}",
            ) from exc
        except ValidationError as exc:
            raise BrowserConfigError(
                f"Lexmount configuration is invalid: {exc}",
            ) from exc
        except TimeoutError as exc:
            raise BrowserRuntimeError(
                f"Timeout while stopping Lexmount browser: {exc}",
            ) from exc
        except NetworkError as exc:
            raise BrowserRuntimeError(
                f"Network error while stopping Lexmount browser: {exc}",
            ) from exc
        except APIError as exc:
            normalized = normalize_lexmount_sdk_error(exc)
            if normalized.code == "browser_parallel_limit_reached":
                raise BrowserParallelLimitError(normalized.message) from exc
            raise BrowserRuntimeError(
                f"Lexmount API error while stopping browser: {normalized.message}",
            ) from exc
        if self.current_forked_context_id and self.current_delete_context_on_close:
            try:
                self._client.contexts.delete(self.current_forked_context_id)
            except Exception as exc:
                raise BrowserRuntimeError(
                    f"Failed to delete forked Lexmount context {self.current_forked_context_id}: {exc}",
                ) from exc

    async def close(self) -> None:
        """Close the underlying SDK client and clear local session state."""

        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        """Close the underlying SDK client and clear local session state."""

        with self._state_lock:
            self._close_sync_unlocked()

    def _close_sync_unlocked(self) -> None:
        """Close the underlying SDK client and clear local session state."""

        client = self._client
        self._client = None
        self._session = None
        self.current_session_id = None
        self.current_inspect_url = None
        self.current_forked_context_id = None
        self.current_delete_context_on_close = True
        if client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception as exc:
                logger.debug("Failed to close Lexmount client cleanly: %s", exc)
