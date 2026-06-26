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
    try:
        result = LexmountBrowserAdmin().create_session(
            context_id=args.context_id,
            create_context=args.create_context,
            context_mode=args.context_mode,
            browser_mode=args.browser_mode,
            metadata=args.metadata,
        )
    except Exception as exc:
        _failure_from_exception(command, exc)
    _success(command, **_model_payload(result))


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
  const textOf = (element) => normalize(element.innerText ?? element.textContent ?? "");
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
    element.value ||
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
  const requestedLabel = {_js_literal(label)};
  const text = {_js_literal(text)};
  const exact = {_js_literal(exact)};
  const caseSensitive = {_js_literal(case_sensitive)};
  const fieldSelector = "input:not([type=hidden]), textarea, select, [contenteditable='true']";
  const labelElements = [...document.querySelectorAll("label")].filter(visible);
  let element = null;
  let matchedLabel = null;
  for (const labelElement of labelElements) {{
    if (!matchesText(textOf(labelElement), requestedLabel, exact, caseSensitive)) {{
      continue;
    }}
    matchedLabel = nodeInfo(labelElement);
    if (labelElement.htmlFor) {{
      element = document.getElementById(labelElement.htmlFor);
    }}
    element ||= labelElement.querySelector(fieldSelector);
    if (element) break;
  }}
  if (!element) {{
    element = [...document.querySelectorAll(fieldSelector)]
      .filter(visible)
      .find((candidate) =>
        matchesText(accessibleName(candidate), requestedLabel, exact, caseSensitive)
      );
  }}
  if (!element) {{
    return {{ found: false, filled: false, label: requestedLabel, text }};
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
    text,
    previous_value: previousValue,
    value: element.isContentEditable ? element.textContent : element.value,
    label_element: matchedLabel,
    element: nodeInfo(element)
  }};
}}
""".strip()


def _wait_text_expression(
    *,
    text: str,
    selector: str | None,
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
    if (element) {{
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
    if (waitedMs >= timeoutMs) {{
      resolve({{
        found: false,
        text: requestedText,
        selector,
        waited_ms: waitedMs,
        candidate_count: nodes.length
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
    ...readFormValue(element),
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
          requested_value: requestedValue,
          match: matchMode,
          waited_ms: waitedMs
        }});
        return;
      }}
      setTimeout(check, pollMs);
      return;
    }}
    const state = readFormValue(element);
    if (!state.readable) {{
      resolve({{
        selector,
        found: false,
        selector_found: true,
        ...state,
        requested_value: requestedValue,
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
        ...state,
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
        found: false,
        selector_found: true,
        ...state,
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


def _clear_expression(selector: str) -> str:
    return _event_expression(
        selector,
        """
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
      previous_value: previousValue,
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
    previous_value: previousValue,
    value
  };
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


def cmd_action_wait_text(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.wait-text",
        _wait_text_expression(
            text=args.text,
            selector=args.selector,
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


def cmd_action_clear(args: argparse.Namespace) -> None:
    _run_eval_backed_action_command(
        args,
        "action.clear",
        _clear_expression(args.selector),
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
    _run_eval_backed_action_command(args, "action.interactive-snapshot", expression)


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

    action_query = action_subparsers.add_parser(
        "query",
        help="List selector matches with DOM-backed node metadata",
    )
    _add_session_target_args(action_query)
    action_query.add_argument("--selector", required=True)
    _add_snapshot_filter_args(action_query)
    action_query.set_defaults(func=cmd_action_query)

    action_get_attribute = action_subparsers.add_parser(
        "get-attribute",
        help="Read an attribute and simple reflected property from a selector",
    )
    _add_session_target_args(action_get_attribute)
    action_get_attribute.add_argument("--selector", required=True)
    action_get_attribute.add_argument("--name", required=True)
    action_get_attribute.set_defaults(func=cmd_action_get_attribute)

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
        "--include-hidden",
        action="store_true",
        help="Include hidden DOM nodes while waiting.",
    )
    _add_text_match_args(action_wait_text)
    action_wait_text.set_defaults(func=cmd_action_wait_text)

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

    action_clear = action_subparsers.add_parser(
        "clear",
        help="Clear a form field or editable element",
    )
    _add_session_target_args(action_clear)
    action_clear.add_argument("--selector", required=True)
    action_clear.set_defaults(func=cmd_action_clear)

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

    action_select_option = action_subparsers.add_parser(
        "select-option",
        help="Set the value of a select-like element",
    )
    _add_session_target_args(action_select_option)
    action_select_option.add_argument("--selector", required=True)
    action_select_option.add_argument("--value", required=True)
    action_select_option.set_defaults(func=cmd_action_select_option)

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

    action_fill_label = action_subparsers.add_parser(
        "fill-label",
        help="Fill a form field matched by label, aria-label, or placeholder",
    )
    _add_session_target_args(action_fill_label)
    action_fill_label.add_argument("--label", required=True)
    action_fill_label.add_argument("--text", required=True)
    _add_text_match_args(action_fill_label)
    action_fill_label.set_defaults(func=cmd_action_fill_label)

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
    action_interactive_snapshot.set_defaults(func=cmd_action_interactive_snapshot)


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
