"""Playwright-backed browser actions used by the runtime CLI skill surface."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lex_browser_runtime.browser.lexmount import (
    LexmountBrowserAdmin,
    build_direct_connect_url,
)
from lex_browser_runtime.browser.models import BrowserConfigError, BrowserRuntimeError

WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]
SelectorState = Literal["attached", "detached", "hidden", "visible"]
BrowserActionName = Literal[
    "open-url",
    "wait-selector",
    "click",
    "type",
    "screenshot",
    "eval",
    "snapshot",
]


class BrowserActionTarget(BaseModel):
    """Target browser for a runtime action."""

    model_config = ConfigDict(extra="forbid")

    connect_url: str | None = None
    session_id: str | None = None
    direct_url: bool = False


class OpenUrlRequest(BaseModel):
    """Request for opening a URL in the target page."""

    url: str
    wait_until: WaitUntil = "load"
    timeout_ms: float = Field(default=30000, gt=0)


class WaitSelectorRequest(BaseModel):
    """Request for waiting on a selector."""

    selector: str
    state: SelectorState = "visible"
    timeout_ms: float = Field(default=30000, gt=0)


class ClickRequest(BaseModel):
    """Request for clicking a selector."""

    selector: str
    timeout_ms: float = Field(default=30000, gt=0)
    wait_after_ms: float = Field(default=0, ge=0)


class TypeRequest(BaseModel):
    """Request for filling a selector with text."""

    selector: str
    text: str
    timeout_ms: float = Field(default=30000, gt=0)
    press_enter: bool = False


class ScreenshotRequest(BaseModel):
    """Request for capturing a screenshot."""

    output: str | None = None
    full_page: bool = False
    timeout_ms: float = Field(default=30000, gt=0)


class EvalRequest(BaseModel):
    """Request for evaluating a JavaScript expression."""

    expression: str


class SnapshotRequest(BaseModel):
    """Request for capturing page HTML and body text."""

    timeout_ms: float = Field(default=30000, gt=0)
    max_chars: int = 8000


BrowserActionRequest = (
    OpenUrlRequest
    | WaitSelectorRequest
    | ClickRequest
    | TypeRequest
    | ScreenshotRequest
    | EvalRequest
    | SnapshotRequest
)


class BrowserActionResult(BaseModel):
    """Structured result returned after running a browser action."""

    action: BrowserActionName
    result: dict[str, Any]


def resolve_browser_action_connect_url(
    target: BrowserActionTarget,
    *,
    admin: LexmountBrowserAdmin | None = None,
) -> str:
    """Resolve a CDP connect URL from explicit URL, session id, or direct mode."""

    if target.connect_url:
        return target.connect_url
    if target.direct_url:
        return build_direct_connect_url()
    if target.session_id:
        resolved_admin = admin or LexmountBrowserAdmin()
        session = resolved_admin.get_session(target.session_id)
        if session.connect_url:
            return session.connect_url
        raise BrowserRuntimeError(
            f"Session '{target.session_id}' does not expose a connect URL."
        )
    raise BrowserRuntimeError(
        "Pass one of connect_url, session_id, or direct_url for browser action target."
    )


class _CdpConnection:
    """Tiny synchronous CDP client for read-only fallback actions."""

    def __init__(self, connect_url: str) -> None:
        try:
            from websocket import create_connection  # type: ignore[import-untyped]
        except Exception as exc:
            raise BrowserConfigError(
                "Failed to import websocket-client. Install "
                "browser-cli with browser support to enable CDP fallback actions."
            ) from exc

        self._ws = create_connection(connect_url, timeout=15)
        self._next_id = 0

    def close(self) -> None:
        self._ws.close()

    def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        self._next_id += 1
        message: dict[str, Any] = {"id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        if session_id is not None:
            message["sessionId"] = session_id
        self._ws.send(json.dumps(message))

        while True:
            raw = self._ws.recv()
            payload = json.loads(raw)
            if payload.get("id") != self._next_id:
                continue
            if "error" in payload:
                raise BrowserRuntimeError(f"CDP {method} failed: {payload['error']}")
            result = payload.get("result", {})
            return result if isinstance(result, dict) else {}


def _select_cdp_page_target(targets: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the best page target while ignoring workers and extension pages."""

    page_targets = [
        target
        for target in targets
        if target.get("type") == "page"
        and not str(target.get("url") or "").startswith("devtools://")
    ]
    if not page_targets:
        raise BrowserRuntimeError("CDP fallback could not find a page target")
    active_targets = [
        target
        for target in page_targets
        if str(target.get("url") or "") not in {"", "about:blank"}
    ]
    return (active_targets or page_targets)[-1]


def _cdp_eval_expression(expression: str) -> str:
    """Match Playwright page.evaluate semantics for common function strings."""

    source = expression.strip()
    if (
        source.startswith("() =>")
        or source.startswith("async () =>")
        or source.startswith("function")
    ):
        return f"({source})()"
    return source


def _format_cdp_runtime_exception(details: dict[str, Any]) -> str:
    text = str(details.get("text") or "JavaScript execution failed")
    exception = details.get("exception")
    if isinstance(exception, dict):
        description = exception.get("description")
        value = exception.get("value")
        class_name = exception.get("className")
        detail = description or value or class_name
        if detail:
            return f"{text}: {detail}"
    return text


def _raise_for_cdp_runtime_exception(evaluated: dict[str, Any]) -> None:
    details = evaluated.get("exceptionDetails")
    if isinstance(details, dict):
        raise BrowserRuntimeError(
            "CDP Runtime.evaluate JavaScript error: "
            f"{_format_cdp_runtime_exception(details)}"
        )


def _run_readonly_action_via_cdp(
    *,
    connect_url: str,
    action: BrowserActionName,
    request: BrowserActionRequest,
) -> BrowserActionResult:
    """Run eval/snapshot directly through CDP without attaching to every target."""

    cdp = _CdpConnection(connect_url)
    session_id: str | None = None
    try:
        targets = cdp.send("Target.getTargets").get("targetInfos", [])
        if not isinstance(targets, list):
            raise BrowserRuntimeError("CDP Target.getTargets returned invalid data")
        target = _select_cdp_page_target(targets)
        attached = cdp.send(
            "Target.attachToTarget",
            {"targetId": target["targetId"], "flatten": True},
        )
        session_id = str(attached["sessionId"])

        if action == "eval":
            if not isinstance(request, EvalRequest):
                raise TypeError("eval action requires EvalRequest")
            evaluated = cdp.send(
                "Runtime.evaluate",
                {
                    "expression": _cdp_eval_expression(request.expression),
                    "returnByValue": True,
                    "awaitPromise": True,
                },
                session_id=session_id,
            )
            _raise_for_cdp_runtime_exception(evaluated)
            result = evaluated.get("result", {})
            value = result.get("value") if isinstance(result, dict) else None
            return BrowserActionResult(
                action=action,
                result={
                    "url": target.get("url", ""),
                    "expression": request.expression,
                    "value": value,
                    "fallback": "cdp",
                },
            )

        if action == "snapshot":
            if not isinstance(request, SnapshotRequest):
                raise TypeError("snapshot action requires SnapshotRequest")
            evaluated = cdp.send(
                "Runtime.evaluate",
                {
                    "expression": """
(() => ({
  url: location.href,
  title: document.title,
  html: document.documentElement ? document.documentElement.outerHTML : "",
  text: document.body ? document.body.innerText : ""
}))()
""".strip(),
                    "returnByValue": True,
                    "awaitPromise": True,
                },
                session_id=session_id,
            )
            _raise_for_cdp_runtime_exception(evaluated)
            result = evaluated.get("result", {})
            value = result.get("value") if isinstance(result, dict) else {}
            if not isinstance(value, dict):
                value = {}
            html = str(value.get("html") or "")
            body_text = str(value.get("text") or "")
            if request.max_chars > 0:
                html = html[: request.max_chars]
                body_text = body_text[: request.max_chars]
            return BrowserActionResult(
                action=action,
                result={
                    "url": str(value.get("url") or target.get("url") or ""),
                    "title": str(value.get("title") or ""),
                    "html": html,
                    "text": body_text,
                    "fallback": "cdp",
                },
            )

        raise ValueError(f"CDP fallback does not support browser action: {action}")
    finally:
        if session_id is not None:
            try:
                cdp.send("Target.detachFromTarget", {"sessionId": session_id})
            except Exception:
                pass
        cdp.close()


def get_or_create_page(context: Any) -> Any:
    """Return the newest page in a Playwright context, creating one if needed."""

    pages = getattr(context, "pages", None)
    if pages:
        return pages[-1]
    return context.new_page()


def execute_browser_action_on_page(
    page: Any,
    action: BrowserActionName,
    request: BrowserActionRequest,
) -> BrowserActionResult:
    """Execute one runtime browser action against an existing Playwright page."""

    if action == "open-url":
        if not isinstance(request, OpenUrlRequest):
            raise TypeError("open-url action requires OpenUrlRequest")
        response = page.goto(
            request.url,
            wait_until=request.wait_until,
            timeout=request.timeout_ms,
        )
        return BrowserActionResult(
            action=action,
            result={
                "url": page.url,
                "title": page.title(),
                "status": response.status if response else None,
            },
        )

    if action == "wait-selector":
        if not isinstance(request, WaitSelectorRequest):
            raise TypeError("wait-selector action requires WaitSelectorRequest")
        locator = page.wait_for_selector(
            request.selector,
            state=request.state,
            timeout=request.timeout_ms,
        )
        return BrowserActionResult(
            action=action,
            result={
                "url": page.url,
                "selector": request.selector,
                "state": request.state,
                "text": locator.text_content() if locator else None,
            },
        )

    if action == "click":
        if not isinstance(request, ClickRequest):
            raise TypeError("click action requires ClickRequest")
        page.click(request.selector, timeout=request.timeout_ms)
        if request.wait_after_ms:
            page.wait_for_timeout(request.wait_after_ms)
        return BrowserActionResult(
            action=action,
            result={"url": page.url, "selector": request.selector, "clicked": True},
        )

    if action == "type":
        if not isinstance(request, TypeRequest):
            raise TypeError("type action requires TypeRequest")
        page.fill(request.selector, request.text, timeout=request.timeout_ms)
        if request.press_enter:
            page.press(request.selector, "Enter", timeout=request.timeout_ms)
        return BrowserActionResult(
            action=action,
            result={
                "url": page.url,
                "selector": request.selector,
                "typed": True,
                "press_enter": request.press_enter,
            },
        )

    if action == "screenshot":
        if not isinstance(request, ScreenshotRequest):
            raise TypeError("screenshot action requires ScreenshotRequest")
        output_path = request.output or str(
            Path("/tmp") / f"lexmount-screenshot-{int(time.time())}.png"
        )
        page.screenshot(
            path=output_path,
            full_page=request.full_page,
            timeout=request.timeout_ms,
        )
        return BrowserActionResult(
            action=action,
            result={
                "url": page.url,
                "path": output_path,
                "full_page": request.full_page,
            },
        )

    if action == "eval":
        if not isinstance(request, EvalRequest):
            raise TypeError("eval action requires EvalRequest")
        value = page.evaluate(request.expression)
        return BrowserActionResult(
            action=action,
            result={
                "url": page.url,
                "expression": request.expression,
                "value": value,
            },
        )

    if action == "snapshot":
        if not isinstance(request, SnapshotRequest):
            raise TypeError("snapshot action requires SnapshotRequest")
        html = page.content()
        body_text = page.locator("body").inner_text(timeout=request.timeout_ms)
        if request.max_chars > 0:
            html = html[: request.max_chars]
            body_text = body_text[: request.max_chars]
        return BrowserActionResult(
            action=action,
            result={
                "url": page.url,
                "title": page.title(),
                "html": html,
                "text": body_text,
            },
        )

    raise ValueError(f"Unsupported browser action: {action}")


def run_browser_action(
    *,
    connect_url: str,
    action: BrowserActionName,
    request: BrowserActionRequest,
) -> BrowserActionResult:
    """Connect to a browser over CDP and execute one action."""

    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except Exception as exc:
        if action in {"eval", "snapshot"}:
            try:
                return _run_readonly_action_via_cdp(
                    connect_url=connect_url,
                    action=action,
                    request=request,
                )
            except Exception as fallback_exc:
                raise BrowserRuntimeError(
                    f"{action} failed without Playwright and CDP fallback failed: {fallback_exc}"
                ) from exc
        raise BrowserConfigError(
            "Failed to import Playwright. Install browser-cli with browser support "
            "or provide an environment that already includes playwright."
        ) from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(connect_url)
            try:
                context = (
                    browser.contexts[0] if browser.contexts else browser.new_context()
                )
                page = get_or_create_page(context)
                return execute_browser_action_on_page(page, action, request)
            finally:
                browser.close()
    except Exception as exc:
        if action in {"eval", "snapshot"}:
            try:
                return _run_readonly_action_via_cdp(
                    connect_url=connect_url,
                    action=action,
                    request=request,
                )
            except Exception as fallback_exc:
                raise BrowserRuntimeError(
                    f"{action} failed via Playwright and CDP fallback: {fallback_exc}"
                ) from exc
        raise
