"""Browser-skill compatible case validation and single-run execution."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from lex_browser_runtime.browser.actions import get_or_create_page
from lex_browser_runtime.browser.lexmount import (
    LexmountBrowserAdmin,
    build_direct_connect_url,
)
from lex_browser_runtime.browser.models import BrowserConfigError, BrowserRuntimeError

SUPPORTED_CASE_ACTIONS = {
    "open-url",
    "wait-selector",
    "click",
    "type",
    "screenshot",
    "eval",
    "snapshot",
}

REQUIRED_CASE_FIELDS = {
    "open-url": ("url",),
    "wait-selector": ("selector",),
    "click": ("selector",),
    "type": ("selector", "text"),
    "screenshot": tuple(),
    "eval": ("expression",),
    "snapshot": tuple(),
}


class CaseValidationResult(BaseModel):
    """Validation result for one browser case file."""

    file: str
    valid: bool
    errors: list[str] = Field(default_factory=list)
    step_count: int = 0


class CaseTargetResolution(BaseModel):
    """Resolved browser target for a case run."""

    connect_url: str
    session: dict[str, Any] | None = None
    created_session: bool = False


class CaseStepRunResult(BaseModel):
    """Result of one executed browser case step."""

    index: int
    action: str | None = None
    ok: bool
    duration_ms: float
    result: dict[str, Any] | None = None
    error: str | None = None
    message: str | None = None


class CaseRunSummary(BaseModel):
    """Summary returned by a browser case run."""

    ok: bool
    command: str = "case.run"
    file: str
    run_id: str
    artifacts_dir: str
    events_path: str
    connect_url: str
    session: dict[str, Any] | None = None
    steps: list[CaseStepRunResult] = Field(default_factory=list)


def case_now() -> str:
    """Return the compact UTC timestamp used by browser-skill case artifacts."""

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_case_file(path: str | Path) -> dict[str, Any]:
    """Load a browser case JSON/YAML file."""

    case_path = Path(path)
    try:
        raw = case_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BrowserConfigError(
            f"Failed to read case file {case_path}: {exc}"
        ) from exc

    suffix = case_path.suffix.lower()
    if suffix == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BrowserConfigError(
                f"Invalid JSON case file {case_path}: {exc}"
            ) from exc
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except Exception as exc:
            raise BrowserConfigError(
                "PyYAML is required to load YAML case files"
            ) from exc
        try:
            data = yaml.safe_load(raw)
        except Exception as exc:
            raise BrowserConfigError(
                f"Invalid YAML case file {case_path}: {exc}"
            ) from exc
    else:
        raise BrowserConfigError("Case file must use .json, .yaml, or .yml")

    if not isinstance(data, dict):
        raise BrowserConfigError("Case file root must be an object")
    return data


def validate_case_spec(spec: dict[str, Any]) -> list[str]:
    """Return validation errors for a browser-skill case object."""

    errors: list[str] = []
    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("steps must be a non-empty array")
        return errors

    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"steps[{index}] must be an object")
            continue
        action = step.get("action")
        if action not in SUPPORTED_CASE_ACTIONS:
            errors.append(
                f"steps[{index}].action must be one of {sorted(SUPPORTED_CASE_ACTIONS)}"
            )
            continue
        for field in REQUIRED_CASE_FIELDS[str(action)]:
            if field not in step:
                errors.append(f"steps[{index}] missing required field '{field}'")

    if "target" in spec and not isinstance(spec["target"], dict):
        errors.append("target must be an object when present")
    if "session" in spec and not isinstance(spec["session"], dict):
        errors.append("session must be an object when present")
    return errors


def validate_case_file(path: str | Path) -> CaseValidationResult:
    """Validate one browser case file."""

    spec = load_case_file(path)
    errors = validate_case_spec(spec)
    steps = spec.get("steps", [])
    return CaseValidationResult(
        file=str(path),
        valid=not errors,
        errors=errors,
        step_count=len(steps) if isinstance(steps, list) else 0,
    )


def append_event(log_path: Path, event_type: str, **payload: Any) -> None:
    """Append one JSONL event to a case run log."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
    }
    event.update(payload)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def resolve_case_target(
    admin: LexmountBrowserAdmin,
    spec: dict[str, Any],
) -> CaseTargetResolution:
    """Resolve the CDP target for a browser case run."""

    raw_target = spec.get("target")
    target: dict[str, Any] = raw_target if isinstance(raw_target, dict) else {}
    raw_session_spec = spec.get("session")
    session_spec: dict[str, Any] = (
        raw_session_spec if isinstance(raw_session_spec, dict) else {}
    )

    if session_spec.get("create"):
        result = admin.create_session(
            context_id=session_spec.get("context_id"),
            create_context=bool(session_spec.get("create_context")),
            context_mode=session_spec.get("context_mode", "read_write"),
            browser_mode=session_spec.get("browser_mode", "normal"),
            metadata=session_spec.get("metadata"),
        )
        connect_url = result.session.connect_url
        if not connect_url:
            raise BrowserRuntimeError("Created session did not expose connect_url")
        session_payload = result.model_dump(mode="json")["session"]
        session_payload["created_context"] = result.created_context
        session_payload["context_id"] = result.context_id
        return CaseTargetResolution(
            connect_url=connect_url,
            session=session_payload,
            created_session=True,
        )

    if target.get("connect_url"):
        return CaseTargetResolution(connect_url=str(target["connect_url"]))
    if target.get("direct_url"):
        return CaseTargetResolution(connect_url=build_direct_connect_url())
    if target.get("session_id"):
        session = admin.get_session(str(target["session_id"]))
        if not session.connect_url:
            raise BrowserRuntimeError("Resolved session does not expose connect_url")
        return CaseTargetResolution(
            connect_url=session.connect_url,
            session=session.model_dump(mode="json"),
        )

    raise BrowserConfigError(
        "Case file must provide target.connect_url, target.session_id, "
        "target.direct_url, or session.create=true."
    )


def _case_step_output_path(
    step: dict[str, Any], artifacts_dir: Path, index: int
) -> str:
    output = step.get("output")
    if output:
        output_path = Path(str(output))
        if not output_path.is_absolute():
            output_path = artifacts_dir / output_path
    else:
        output_path = artifacts_dir / f"step-{index:02d}-screenshot.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return str(output_path)


def run_case_step(
    page: Any,
    step: dict[str, Any],
    artifacts_dir: Path,
    index: int,
) -> dict[str, Any]:
    """Run one browser-skill case step on an existing Playwright page."""

    action = step["action"]
    timeout_ms = step.get("timeout_ms", 30000)

    if action == "open-url":
        response = page.goto(
            step["url"],
            wait_until=step.get("wait_until", "load"),
            timeout=timeout_ms,
        )
        return {
            "url": page.url,
            "title": page.title(),
            "status": response.status if response else None,
        }
    if action == "wait-selector":
        locator = page.wait_for_selector(
            step["selector"],
            state=step.get("state", "visible"),
            timeout=timeout_ms,
        )
        return {
            "selector": step["selector"],
            "state": step.get("state", "visible"),
            "text": locator.text_content() if locator else None,
            "url": page.url,
        }
    if action == "click":
        page.click(step["selector"], timeout=timeout_ms)
        if step.get("wait_after_ms"):
            page.wait_for_timeout(step["wait_after_ms"])
        return {"selector": step["selector"], "clicked": True, "url": page.url}
    if action == "type":
        page.fill(step["selector"], step["text"], timeout=timeout_ms)
        if step.get("press_enter"):
            page.press(step["selector"], "Enter", timeout=timeout_ms)
        return {
            "selector": step["selector"],
            "typed": True,
            "press_enter": bool(step.get("press_enter")),
            "url": page.url,
        }
    if action == "screenshot":
        output_path = _case_step_output_path(step, artifacts_dir, index)
        page.screenshot(
            path=output_path,
            full_page=bool(step.get("full_page")),
            timeout=timeout_ms,
        )
        return {
            "path": output_path,
            "full_page": bool(step.get("full_page")),
            "url": page.url,
        }
    if action == "eval":
        value = page.evaluate(step["expression"])
        return {"expression": step["expression"], "value": value, "url": page.url}
    if action == "snapshot":
        max_chars = int(step.get("max_chars", 8000))
        html = page.content()
        text = page.locator("body").inner_text(timeout=timeout_ms)
        if max_chars > 0:
            html = html[:max_chars]
            text = text[:max_chars]
        snapshot_path = step.get("output")
        if snapshot_path:
            output_path = _case_step_output_path(step, artifacts_dir, index)
            Path(output_path).write_text(
                json.dumps(
                    {
                        "html": html,
                        "text": text,
                        "url": page.url,
                        "title": page.title(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        return {"url": page.url, "title": page.title(), "html": html, "text": text}

    raise ValueError(f"Unsupported action: {action}")


def run_case_file(
    *,
    file: str | Path,
    run_id: str | None = None,
    artifacts_dir: str | Path | None = None,
    stop_on_error: bool = False,
    close_created_session: bool = False,
) -> CaseRunSummary:
    """Run one browser case file and persist browser-skill compatible artifacts."""

    spec = load_case_file(file)
    errors = validate_case_spec(spec)
    if errors:
        raise BrowserConfigError(f"Case validation failed: {errors}")

    admin = LexmountBrowserAdmin()
    target = resolve_case_target(admin, spec)
    resolved_run_id = (
        run_id or spec.get("run_id") or f"case-{case_now()}-{time.time_ns()}"
    )
    resolved_artifacts_dir = Path(
        artifacts_dir or f"/tmp/lexmount-runs/{resolved_run_id}"
    )
    resolved_artifacts_dir.mkdir(parents=True, exist_ok=True)
    event_log = resolved_artifacts_dir / "events.jsonl"

    append_event(
        event_log,
        "case_started",
        run_id=resolved_run_id,
        file=str(file),
        artifacts_dir=str(resolved_artifacts_dir),
    )
    append_event(
        event_log,
        "session_resolved",
        run_id=resolved_run_id,
        created_session=target.created_session,
        session=target.session,
        connect_url=target.connect_url,
    )

    results: list[CaseStepRunResult] = []
    created_session_id = target.session.get("session_id") if target.session else None
    should_close_created = close_created_session or bool(
        spec.get("close_created_session")
    )

    try:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        except Exception as exc:
            raise BrowserConfigError(
                "Failed to import Playwright. Install browser-cli with browser support "
                "or provide an environment that already includes playwright."
            ) from exc

        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(target.connect_url)
            try:
                context = (
                    browser.contexts[0] if browser.contexts else browser.new_context()
                )
                page = get_or_create_page(context)
                for index, step in enumerate(spec["steps"]):
                    started_at = time.time()
                    append_event(
                        event_log,
                        "step_started",
                        run_id=resolved_run_id,
                        index=index,
                        action=step.get("action"),
                        step=step,
                    )
                    try:
                        result = run_case_step(
                            page, step, resolved_artifacts_dir, index
                        )
                        duration_ms = round((time.time() - started_at) * 1000, 2)
                        step_result = CaseStepRunResult(
                            index=index,
                            action=step["action"],
                            ok=True,
                            duration_ms=duration_ms,
                            result=result,
                        )
                        results.append(step_result)
                        append_event(
                            event_log,
                            "step_finished",
                            run_id=resolved_run_id,
                            index=index,
                            action=step["action"],
                            ok=True,
                            duration_ms=duration_ms,
                            result=result,
                        )
                    except Exception as exc:
                        duration_ms = round((time.time() - started_at) * 1000, 2)
                        step_result = CaseStepRunResult(
                            index=index,
                            action=step.get("action"),
                            ok=False,
                            duration_ms=duration_ms,
                            error=exc.__class__.__name__,
                            message=str(exc),
                        )
                        results.append(step_result)
                        append_event(
                            event_log,
                            "step_finished",
                            run_id=resolved_run_id,
                            index=index,
                            action=step.get("action"),
                            ok=False,
                            duration_ms=duration_ms,
                            error=exc.__class__.__name__,
                            message=str(exc),
                        )
                        if stop_on_error:
                            break
            finally:
                browser.close()
                append_event(event_log, "browser_closed", run_id=resolved_run_id)
    finally:
        if target.created_session and created_session_id and should_close_created:
            try:
                admin.close_session(str(created_session_id))
                if target.session is not None:
                    target.session["closed_after_run"] = True
                append_event(
                    event_log,
                    "session_closed",
                    run_id=resolved_run_id,
                    session_id=created_session_id,
                    ok=True,
                )
            except Exception as exc:
                if target.session is not None:
                    target.session["close_after_run_error"] = str(exc)
                append_event(
                    event_log,
                    "session_closed",
                    run_id=resolved_run_id,
                    session_id=created_session_id,
                    ok=False,
                    error=exc.__class__.__name__,
                    message=str(exc),
                )

    summary = CaseRunSummary(
        ok=all(item.ok for item in results),
        file=str(file),
        run_id=resolved_run_id,
        artifacts_dir=str(resolved_artifacts_dir),
        events_path=str(event_log),
        connect_url=target.connect_url,
        session=target.session,
        steps=results,
    )
    (resolved_artifacts_dir / "summary.json").write_text(
        json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    append_event(
        event_log,
        "case_finished",
        run_id=resolved_run_id,
        ok=summary.ok,
        steps_total=len(results),
        steps_ok=sum(1 for item in results if item.ok),
        steps_failed=sum(1 for item in results if not item.ok),
    )
    return summary
