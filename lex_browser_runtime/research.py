"""Multi-source Lexmount browser research runner."""

from __future__ import annotations

import concurrent.futures
import json
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import quote, urlparse

from pydantic import BaseModel, ConfigDict, Field

from lex_browser_runtime.auth_contexts import AuthContextEntry, load_auth_context_store
from lex_browser_runtime.browser.lexmount import LexmountBrowserAdmin
from lex_browser_runtime.browser.models import BrowserConfigError, BrowserRuntimeError
from lex_browser_runtime.config import get_default_research_concurrency
from lex_browser_runtime.registry.gov_portals import (
    GovPortalRecord,
    gov_portal_query_terms,
    load_gov_portals,
    match_gov_portals,
)

ResearchPreset = Literal["food", "web", "gov-policy"]
ResearchEventSink = Callable[[dict[str, Any]], None]


class ResearchSource(BaseModel):
    """One routeable public web source for runtime research."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    name: str
    url_template: str
    source_type: str = "search"
    notes: str | None = None


class ResearchJob(BaseModel):
    """One source-specific browser job derived from a natural-language query."""

    rank: int
    query: str
    preset: str
    source_id: str
    source_name: str
    source_type: str
    url: str
    extraction_goal: str


class ResearchRoute(BaseModel):
    """A deterministic research route that an outer agent can inspect."""

    ok: bool = True
    command: str = "research.route"
    query: str
    preset: str
    jobs: list[ResearchJob]


class ResearchLink(BaseModel):
    """A compact link extracted from one source page."""

    text: str
    href: str


class ResearchCandidate(BaseModel):
    """A compact visible result/card extracted from one source page."""

    text: str
    href: str | None = None


class ResearchJobResult(BaseModel):
    """Result from one browser-backed source job."""

    source_id: str
    source_name: str
    url: str
    ok: bool
    duration_ms: float
    session_id: str | None = None
    inspect_url: str | None = None
    auth_context_id: str | None = None
    auth_context_mode: str | None = None
    final_url: str | None = None
    title: str | None = None
    status: int | None = None
    text: str | None = None
    headings: list[str] = Field(default_factory=list)
    links: list[ResearchLink] = Field(default_factory=list)
    candidates: list[ResearchCandidate] = Field(default_factory=list)
    error: str | None = None
    message: str | None = None


class ResearchRunSummary(BaseModel):
    """Summary and artifact locations for one multi-source research run."""

    ok: bool
    command: str = "research.run"
    query: str
    preset: str
    run_id: str
    output_dir: str
    events_path: str
    sources_path: str
    routes_path: str
    summary_path: str
    success_count: int
    failure_count: int
    concurrency: int
    jobs: list[ResearchJob]
    results: list[ResearchJobResult]


@dataclass(slots=True)
class AllocatedResearchSession:
    """A Lexmount session already reserved for one research job."""

    admin: LexmountBrowserAdmin
    session_id: str | None
    inspect_url: str | None
    connect_url: str | None
    auth_context: AuthContextEntry | None = None
    browser_created_emitted: bool = False


GOOGLE_SOURCE = ResearchSource(
    source_id="google",
    name="Google web",
    url_template="https://www.google.com/search?q={query}",
    notes="General global web search.",
)


FOOD_SOURCES: tuple[ResearchSource, ...] = (
    ResearchSource(
        source_id="baidu",
        name="Baidu web",
        url_template="https://www.baidu.com/s?wd={query}",
        notes="Broad Chinese web results.",
    ),
    ResearchSource(
        source_id="bing",
        name="Bing web",
        url_template="https://www.bing.com/search?q={query}",
        notes="General search fallback.",
    ),
    ResearchSource(
        source_id="bilibili",
        name="Bilibili",
        url_template="https://search.bilibili.com/all?keyword={query}",
        notes="Video results and food creator recommendations.",
    ),
    ResearchSource(
        source_id="xiangha",
        name="Xiangha",
        url_template="https://www.xiangha.com/so/?q=caipu&s={query}",
        notes="Recipe and ingredient search.",
    ),
    ResearchSource(
        source_id="xiachufang",
        name="Xiachufang",
        url_template="https://m-stag-u.xiachufang.com/search/?q={query}",
        notes="Recipe and home-cooking food discovery.",
    ),
    ResearchSource(
        source_id="meishichina",
        name="Meishichina",
        url_template="https://home.meishichina.com/search/{query}/",
        notes="Recipe and Chinese food community search.",
    ),
    ResearchSource(
        source_id="lezuocai",
        name="Lezuocai",
        url_template="https://www.lezuocai.com/action/search?keyword={query}",
        notes="Home cooking recipes and step-by-step recipes.",
    ),
    ResearchSource(
        source_id="zidianwang-caipu",
        name="Zidianwang caipu",
        url_template="https://www.zidianwang.cn/caipu/?keyword={query}",
        notes="Simple recipe directory search.",
    ),
    ResearchSource(
        source_id="jucanw",
        name="Jucanw",
        url_template="https://www.baidu.com/s?wd=site%3Awww.jucanw.com%20{query}",
        notes="Jucanw recipes via public site search.",
    ),
    ResearchSource(
        source_id="xiangzuocai",
        name="Xiangzuocai",
        url_template="https://www.xiangzuocai.com/search/{query}/",
        notes="Home cooking, breakfast, baking, and dessert recipes.",
    ),
    ResearchSource(
        source_id="xiao688",
        name="Xiao688",
        url_template="https://xiao688.com/search/common.html?word={query}",
        notes="Home-style recipes, stir-fries, soups, and vegetable recipes.",
    ),
    ResearchSource(
        source_id="hao86-shipu",
        name="Hao86 shipu",
        url_template="https://shipu.hao86.com/search/?q={query}",
        notes="Recipe categories and recipe topic search.",
    ),
    GOOGLE_SOURCE,
)

WEB_SOURCES: tuple[ResearchSource, ...] = (
    FOOD_SOURCES[0],
    FOOD_SOURCES[1],
    GOOGLE_SOURCE,
)

PRESET_SOURCES: dict[str, tuple[ResearchSource, ...]] = {
    "food": FOOD_SOURCES,
    "web": WEB_SOURCES,
}
SUPPORTED_RESEARCH_PRESETS = ("food", "gov-policy", "web")
GOV_POLICY_MAX_SITES = 50
GOV_POLICY_MAX_CONCURRENCY = 12


def research_run_id() -> str:
    """Return the compact UTC timestamp used by research artifacts."""

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_sites(sites: str | list[str] | tuple[str, ...] | None) -> list[str] | None:
    if sites is None:
        return None
    if isinstance(sites, str):
        values = sites.split(",")
    else:
        values = list(sites)
    normalized = [value.strip() for value in values if value.strip()]
    return normalized or None


def _gov_policy_source_name(record: GovPortalRecord) -> str:
    label = record.area or record.name
    return f"{label} policy search"


GOV_POLICY_CITY_SCOPE_TERMS = (
    "全国各大城市",
    "全国城市",
    "各大城市",
    "各城市",
    "主要城市",
)

GOV_POLICY_SCOPE_TERMS = GOV_POLICY_CITY_SCOPE_TERMS + (
    "区县",
    "县级",
    "区级",
    "县级市",
    "省级",
    "全省",
)


def _gov_policy_requests_nationwide_city_scope(query: str) -> bool:
    return any(term in query for term in GOV_POLICY_CITY_SCOPE_TERMS)


def _filter_gov_policy_records(
    *,
    query: str,
    records: list[GovPortalRecord],
) -> list[GovPortalRecord]:
    if _gov_policy_requests_nationwide_city_scope(query):
        return list(load_gov_portals())
    return records


def _gov_policy_search_query(record: GovPortalRecord, query: str) -> str:
    keyword = query
    for term in GOV_POLICY_SCOPE_TERMS:
        keyword = keyword.replace(term, " ")
    for term in sorted(gov_portal_query_terms(record), key=len, reverse=True):
        keyword = keyword.replace(term, " ")
    keyword = " ".join(keyword.split())
    return keyword or query


def _route_gov_policy(
    *,
    query: str,
    sites: str | list[str] | tuple[str, ...] | None,
    max_sites: int,
) -> ResearchRoute:
    capped_max_sites = min(max_sites, GOV_POLICY_MAX_SITES)
    selected_sites = _parse_sites(sites)
    if selected_sites:
        by_id = {record.source_id: record for record in load_gov_portals()}
        unknown = [site for site in selected_sites if site not in by_id]
        if unknown:
            raise BrowserConfigError(
                "Unknown source id(s) for preset 'gov-policy': " + ", ".join(unknown)
            )
        records = [by_id[site] for site in selected_sites]
    else:
        records = match_gov_portals(query, limit=1000)
        records = _filter_gov_policy_records(query=query, records=records)

    jobs = [
        ResearchJob(
            rank=index + 1,
            query=_gov_policy_search_query(record, query),
            preset="gov-policy",
            source_id=record.source_id,
            source_name=_gov_policy_source_name(record),
            source_type="government_portal_search",
            url=record.url,
            extraction_goal=(
                "Open the matched official government portal, use its own search "
                "interface for the query, and capture policy pages, notices, "
                "service guides, and eligibility details."
            ),
        )
        for index, record in enumerate(records[:capped_max_sites])
    ]
    return ResearchRoute(query=query, preset="gov-policy", jobs=jobs)


def route_research(
    *,
    query: str,
    preset: str = "food",
    sites: str | list[str] | tuple[str, ...] | None = None,
    max_sites: int = 13,
) -> ResearchRoute:
    """Build source-specific browser jobs for a research query."""

    normalized_query = query.strip()
    if not normalized_query:
        raise BrowserConfigError("query must not be empty")
    if max_sites <= 0:
        raise BrowserConfigError("max_sites must be greater than 0")

    if preset == "gov-policy":
        return _route_gov_policy(
            query=normalized_query,
            sites=sites,
            max_sites=max_sites,
        )

    if preset not in PRESET_SOURCES:
        raise BrowserConfigError(
            f"Unknown research preset {preset!r}; supported presets: "
            + ", ".join(SUPPORTED_RESEARCH_PRESETS)
        )

    sources = list(PRESET_SOURCES[preset])
    selected_sites = _parse_sites(sites)
    if selected_sites:
        by_id = {source.source_id: source for source in sources}
        unknown = [site for site in selected_sites if site not in by_id]
        if unknown:
            raise BrowserConfigError(
                f"Unknown source id(s) for preset {preset!r}: {', '.join(unknown)}"
            )
        sources = [by_id[site] for site in selected_sites]

    encoded_query = quote(normalized_query, safe="")
    jobs = [
        ResearchJob(
            rank=index + 1,
            query=normalized_query,
            preset=preset,
            source_id=source.source_id,
            source_name=source.name,
            source_type=source.source_type,
            url=source.url_template.format(query=encoded_query),
            extraction_goal=(
                "Open the source search URL and capture visible result text, "
                "headings, links, and candidate cards for the outer agent to judge."
            ),
        )
        for index, source in enumerate(sources[:max_sites])
    ]
    return ResearchRoute(query=normalized_query, preset=preset, jobs=jobs)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    """Append one JSON object to a JSONL file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _event_payload(event_type: str, **payload: Any) -> dict[str, Any]:
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
    }
    event.update(payload)
    return event


def _extract_page_data(
    *,
    connect_url: str,
    url: str,
    timeout_ms: float,
    wait_after_ms: float,
    max_chars: int,
    source_type: str = "search",
    query: str | None = None,
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except Exception as exc:
        raise BrowserConfigError(
            "Failed to import Playwright. Install browser-cli with browser support "
            "or provide an environment that already includes playwright."
        ) from exc

    script = """
    ({ maxChars }) => {
      const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const absoluteHref = (element) => {
        const raw = element.getAttribute('href');
        if (!raw || raw.startsWith('javascript:')) return null;
        try { return new URL(raw, location.href).toString(); }
        catch { return raw; }
      };
      const links = Array.from(document.querySelectorAll('a'))
        .map((element) => ({ text: clean(element.innerText || element.textContent), href: absoluteHref(element) }))
        .filter((item) => item.text && item.href)
        .slice(0, 60);
      const headings = Array.from(document.querySelectorAll('h1,h2,h3,[role=heading]'))
        .map((element) => clean(element.innerText || element.textContent))
        .filter(Boolean)
        .slice(0, 30);
      const candidateSelector = [
        'article',
        '[role=listitem]',
        '[data-testid*=result]',
        '[class*=result]',
        '[class*=card]',
        '[class*=item]',
        'li'
      ].join(',');
      const seen = new Set();
      const candidates = Array.from(document.querySelectorAll(candidateSelector))
        .map((element) => {
          const text = clean(element.innerText || element.textContent);
          if (!text || text.length < 8 || seen.has(text)) return null;
          seen.add(text);
          const anchor = element.matches('a') ? element : element.querySelector('a');
          return { text: text.slice(0, 700), href: anchor ? absoluteHref(anchor) : null };
        })
        .filter(Boolean)
        .slice(0, 30);
      const bodyText = clean(document.body ? document.body.innerText : '');
      return {
        final_url: location.href,
        title: document.title || '',
        text: bodyText.slice(0, maxChars),
        headings,
        links,
        candidates
      };
    }
    """

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(connect_url)
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[-1] if context.pages else context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            original_url = url
            if source_type == "government_portal_search":
                _operate_government_portal_search(
                    page=page,
                    query=query,
                    timeout_ms=timeout_ms,
                    wait_after_ms=wait_after_ms,
                )
                response = None
            if wait_after_ms:
                page.wait_for_timeout(wait_after_ms)
            payload = page.evaluate(
                script,
                {"maxChars": max_chars},
            )
            if not isinstance(payload, dict):
                raise BrowserRuntimeError(
                    "research page extraction returned non-object"
                )
            if source_type == "government_portal_search" and not (
                _government_portal_search_confirmed(
                    payload=payload,
                    original_url=original_url,
                    query=query,
                )
            ):
                raise BrowserRuntimeError(
                    "government portal search result was not confirmed"
                )
            payload["status"] = response.status if response else None
            return payload
        finally:
            browser.close()


def _operate_government_portal_search(
    *,
    page: Any,
    query: str | None,
    timeout_ms: float,
    wait_after_ms: float,
) -> None:
    if not query:
        raise BrowserRuntimeError("government portal search requires a query")

    if _submit_government_portal_search(page=page, query=query):
        return

    if _open_government_portal_search_entry(page=page):
        if wait_after_ms:
            page.wait_for_timeout(wait_after_ms)
        if _submit_government_portal_search(page=page, query=query):
            return

    raise BrowserRuntimeError("government portal search input was not found")


def _submit_government_portal_search(*, page: Any, query: str) -> bool:
    script = """
    ({ query }) => {
      const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const lower = (value) => clean(value).toLowerCase();
      const visible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none'
          && style.visibility !== 'hidden'
          && rect.width > 0
          && rect.height > 0;
      };
      const textOf = (element) => [
        element.getAttribute('placeholder'),
        element.getAttribute('title'),
        element.getAttribute('aria-label'),
        element.getAttribute('name'),
        element.getAttribute('id'),
        element.className,
        element.innerText,
        element.textContent
      ].map(clean).join(' ');
      const looksSearch = (element) => {
        const text = lower(textOf(element));
        return text.includes('搜索')
          || text.includes('检索')
          || text.includes('search')
          || text.includes('keyword')
          || text.includes('query')
          || text.includes('关键词');
      };
      const blockedInputTypes = new Set([
        'hidden',
        'submit',
        'button',
        'reset',
        'image',
        'file',
        'checkbox',
        'radio'
      ]);
      const editableControls = Array.from(document.querySelectorAll(
        'input, textarea, [contenteditable=""], [contenteditable="true"]'
      )).filter((element) => {
        if (!visible(element)) return false;
        if (element.disabled || element.readOnly) return false;
        if (element.tagName.toLowerCase() === 'input') {
          const type = lower(element.getAttribute('type') || 'text');
          if (blockedInputTypes.has(type)) return false;
        }
        return looksSearch(element);
      });
      const input = editableControls[0];
      if (!input) {
        return { ok: false, reason: 'no_visible_editable_search_input' };
      }
      input.focus();
      if ('value' in input) {
        input.value = query;
      } else {
        input.textContent = query;
      }
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));

      const submitButtons = Array.from(document.querySelectorAll(
        'button, input[type="submit"], input[type="button"], [role="button"], a'
      )).filter((element) => visible(element) && looksSearch(element));
      const form = input.closest('form');
      if (form) {
        const formButton = submitButtons.find((button) => form.contains(button));
        if (formButton) {
          formButton.click();
          return { ok: true, method: 'form_button', selector: textOf(input) };
        }
        if (typeof form.requestSubmit === 'function') {
          form.requestSubmit();
          return { ok: true, method: 'request_submit', selector: textOf(input) };
        }
        form.submit();
        return { ok: true, method: 'form_submit', selector: textOf(input) };
      }

      const button = submitButtons[0];
      if (button) {
        button.click();
        return { ok: true, method: 'search_button', selector: textOf(input) };
      }
      input.dispatchEvent(new KeyboardEvent('keydown', {
        bubbles: true,
        cancelable: true,
        key: 'Enter',
        code: 'Enter',
        keyCode: 13,
        which: 13
      }));
      input.dispatchEvent(new KeyboardEvent('keyup', {
        bubbles: true,
        cancelable: true,
        key: 'Enter',
        code: 'Enter',
        keyCode: 13,
        which: 13
      }));
      return { ok: true, method: 'enter_events', selector: textOf(input) };
    }
    """
    result = page.evaluate(script, {"query": query})
    return isinstance(result, dict) and result.get("ok") is True


def _open_government_portal_search_entry(*, page: Any) -> bool:
    script = """
    ({ action }) => {
      const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const lower = (value) => clean(value).toLowerCase();
      const visible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none'
          && style.visibility !== 'hidden'
          && rect.width > 0
          && rect.height > 0;
      };
      const textOf = (element) => [
        element.getAttribute('title'),
        element.getAttribute('aria-label'),
        element.getAttribute('href'),
        element.getAttribute('id'),
        element.className,
        element.innerText,
        element.textContent
      ].map(clean).join(' ');
      const looksSearch = (element) => {
        const text = lower(textOf(element));
        return text.includes('搜索')
          || text.includes('检索')
          || text.includes('search')
          || text.includes('keyword')
          || text.includes('query')
          || text.includes('关键词');
      };
      const entries = Array.from(document.querySelectorAll(
        'a, button, input[type="button"], input[type="submit"], [role="button"]'
      )).filter((element) => visible(element) && looksSearch(element));
      const entry = entries[0];
      if (!entry) return { ok: false, reason: 'no_visible_search_entry', action };
      entry.click();
      return { ok: true, method: 'entry_click', selector: textOf(entry), action };
    }
    """
    result = page.evaluate(script, {"action": "open_search_entry"})
    return isinstance(result, dict) and result.get("ok") is True


def _government_portal_search_confirmed(
    *,
    payload: dict[str, Any],
    original_url: str,
    query: str | None,
) -> bool:
    final_url = str(payload.get("final_url") or "")
    original_parts = urlparse(original_url)
    final_parts = urlparse(final_url)
    original_location = (
        original_parts.netloc.lower(),
        original_parts.path.rstrip("/") or "/",
        original_parts.params,
        original_parts.query,
        original_parts.fragment,
    )
    final_location = (
        final_parts.netloc.lower(),
        final_parts.path.rstrip("/") or "/",
        final_parts.params,
        final_parts.query,
        final_parts.fragment,
    )
    if final_url and final_location != original_location:
        return True

    search_terms = {
        "搜索结果",
        "查询结果",
        "检索结果",
        "相关结果",
    }
    if query:
        search_terms.add(query)
        search_terms.update(part for part in query.split() if len(part) >= 2)

    values: list[str] = []
    for key in ("title", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            values.append(value)
    for key in ("headings", "links", "candidates"):
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, dict):
                values.extend(str(value) for value in item.values() if value)
    haystack = " ".join(values)
    return any(term and term in haystack for term in search_terms)


def _run_research_job(
    *,
    job: ResearchJob,
    admin_factory: Callable[[], LexmountBrowserAdmin],
    timeout_ms: float,
    wait_after_ms: float,
    max_chars: int,
    browser_mode: str,
    keep_sessions: bool,
    session_create_timeout_sec: float,
    auth_context: AuthContextEntry | None = None,
    allocated_session: AllocatedResearchSession | None = None,
    record_event: Callable[..., None] | None = None,
) -> ResearchJobResult:
    started_at = time.time()
    session_id: str | None = None
    inspect_url: str | None = None
    admin: LexmountBrowserAdmin | None = None
    try:
        if allocated_session is None:
            allocated = _allocate_research_session(
                job=job,
                auth_context=auth_context,
                admin_factory=admin_factory,
                browser_mode=browser_mode,
                session_create_timeout_sec=session_create_timeout_sec,
                record_event=record_event,
            )
            if isinstance(allocated, ResearchJobResult):
                return allocated
            allocated_session = allocated
        admin = allocated_session.admin
        session_id = allocated_session.session_id
        inspect_url = allocated_session.inspect_url
        auth_context = allocated_session.auth_context
        if not allocated_session.browser_created_emitted and record_event is not None:
            record_event(
                "browser_created",
                source_id=job.source_id,
                source_name=job.source_name,
                url=job.url,
                session_id=session_id,
                inspect_url=inspect_url,
                auth_context_id=(
                    auth_context.context_id if auth_context is not None else None
                ),
                auth_context_mode=(
                    auth_context.context_mode if auth_context is not None else None
                ),
            )
            allocated_session.browser_created_emitted = True
        if not allocated_session.connect_url:
            raise BrowserRuntimeError(
                "Created research session did not expose connect_url"
            )
        extracted = _extract_page_data(
            connect_url=allocated_session.connect_url,
            url=job.url,
            timeout_ms=timeout_ms,
            wait_after_ms=wait_after_ms,
            max_chars=max_chars,
            source_type=job.source_type,
            query=job.query,
        )
        return ResearchJobResult(
            source_id=job.source_id,
            source_name=job.source_name,
            url=job.url,
            ok=True,
            duration_ms=(time.time() - started_at) * 1000,
            session_id=session_id,
            inspect_url=inspect_url,
            auth_context_id=(
                auth_context.context_id if auth_context is not None else None
            ),
            auth_context_mode=(
                auth_context.context_mode if auth_context is not None else None
            ),
            final_url=extracted.get("final_url"),
            title=extracted.get("title"),
            status=extracted.get("status"),
            text=extracted.get("text"),
            headings=[
                heading
                for heading in extracted.get("headings", [])
                if isinstance(heading, str)
            ],
            links=[
                ResearchLink.model_validate(link)
                for link in extracted.get("links", [])
                if isinstance(link, dict)
            ],
            candidates=[
                ResearchCandidate.model_validate(candidate)
                for candidate in extracted.get("candidates", [])
                if isinstance(candidate, dict)
            ],
        )
    except Exception as exc:
        return ResearchJobResult(
            source_id=job.source_id,
            source_name=job.source_name,
            url=job.url,
            ok=False,
            duration_ms=(time.time() - started_at) * 1000,
            session_id=session_id,
            inspect_url=inspect_url,
            auth_context_id=(
                auth_context.context_id if auth_context is not None else None
            ),
            auth_context_mode=(
                auth_context.context_mode if auth_context is not None else None
            ),
            error=exc.__class__.__name__,
            message=str(exc),
        )
    finally:
        if session_id and admin is not None and not keep_sessions:
            try:
                admin.close_session(session_id)
                if record_event is not None:
                    record_event(
                        "browser_closed",
                        source_id=job.source_id,
                        source_name=job.source_name,
                        url=job.url,
                        session_id=session_id,
                        inspect_url=inspect_url,
                    )
            except Exception:
                # The job result already carries the primary failure/success.
                # Cleanup failure should not hide page evidence from the caller.
                pass


def _session_failure_result(
    *,
    job: ResearchJob,
    auth_context: AuthContextEntry | None,
    started_at: float,
    error: str,
    message: str,
) -> ResearchJobResult:
    return ResearchJobResult(
        source_id=job.source_id,
        source_name=job.source_name,
        url=job.url,
        ok=False,
        duration_ms=(time.time() - started_at) * 1000,
        auth_context_id=(auth_context.context_id if auth_context is not None else None),
        auth_context_mode=(
            auth_context.context_mode if auth_context is not None else None
        ),
        error=error,
        message=message,
    )


def _allocate_research_session(
    *,
    job: ResearchJob,
    auth_context: AuthContextEntry | None,
    admin_factory: Callable[[], LexmountBrowserAdmin],
    browser_mode: str,
    session_create_timeout_sec: float,
    record_event: Callable[..., None] | None = None,
    browser_ready_event_type: str = "browser_created",
) -> AllocatedResearchSession | ResearchJobResult:
    started_at = time.time()
    admin = admin_factory()
    session_id: str | None = None
    inspect_url: str | None = None
    auth_context_id = auth_context.context_id if auth_context is not None else None
    auth_context_mode = auth_context.context_mode if auth_context is not None else None
    try:
        if record_event is not None:
            record_event(
                "session_create_started",
                source_id=job.source_id,
                source_name=job.source_name,
                url=job.url,
                auth_context_id=auth_context_id,
                auth_context_mode=auth_context_mode,
            )
        session_kwargs: dict[str, Any] = {"browser_mode": browser_mode}
        if auth_context is not None:
            session_kwargs["context_id"] = auth_context.context_id
            session_kwargs["context_mode"] = auth_context.context_mode
        session_result = _create_session_with_timeout(
            admin,
            timeout_sec=session_create_timeout_sec,
            **session_kwargs,
        )
        session = session_result.session
        session_id = session.session_id
        inspect_url = session.inspect_url or session.inspect_url_dbg
        browser_created_emitted = False
        if record_event is not None:
            record_event(
                browser_ready_event_type,
                source_id=job.source_id,
                source_name=job.source_name,
                url=job.url,
                session_id=session_id,
                inspect_url=inspect_url,
                auth_context_id=auth_context_id,
                auth_context_mode=auth_context_mode,
            )
            browser_created_emitted = browser_ready_event_type == "browser_created"
        return AllocatedResearchSession(
            admin=admin,
            session_id=session_id,
            inspect_url=inspect_url,
            connect_url=session.connect_url,
            auth_context=auth_context,
            browser_created_emitted=browser_created_emitted,
        )
    except Exception as exc:
        message = f"Failed to create Lexmount session before research: {exc}"
        if record_event is not None:
            record_event(
                "session_create_failed",
                source_id=job.source_id,
                source_name=job.source_name,
                url=job.url,
                duration_ms=(time.time() - started_at) * 1000,
                session_id=session_id,
                inspect_url=inspect_url,
                auth_context_id=auth_context_id,
                auth_context_mode=auth_context_mode,
                error=exc.__class__.__name__,
                message=message,
            )
        return _session_failure_result(
            job=job,
            auth_context=auth_context,
            started_at=started_at,
            error=exc.__class__.__name__,
            message=message,
        )


def _create_session_with_timeout(
    admin: LexmountBrowserAdmin,
    *,
    timeout_sec: float,
    **session_kwargs: Any,
) -> Any:
    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def create_session() -> None:
        try:
            result_queue.put((True, admin.create_session(**session_kwargs)))
        except BaseException as exc:
            result_queue.put((False, exc))

    thread = threading.Thread(target=create_session, daemon=True)
    thread.start()
    thread.join(timeout_sec)
    if thread.is_alive():
        _close_session_created_after_timeout(admin, thread, result_queue)
        raise BrowserRuntimeError(
            f"Timed out creating Lexmount session after {timeout_sec:.1f}s"
        )
    ok, value = result_queue.get()
    if ok:
        return value
    raise value


def _close_session_created_after_timeout(
    admin: LexmountBrowserAdmin,
    thread: threading.Thread,
    result_queue: queue.Queue[tuple[bool, Any]],
) -> None:
    """Release a session if create_session finishes after the caller timed out."""

    def cleanup() -> None:
        thread.join()
        try:
            ok, value = result_queue.get_nowait()
        except queue.Empty:
            return
        if not ok:
            return
        session = getattr(value, "session", None)
        session_id = getattr(session, "session_id", None)
        if not session_id:
            return
        try:
            admin.close_session(str(session_id))
        except Exception:
            return

    threading.Thread(target=cleanup, daemon=True).start()


def run_research(
    *,
    query: str,
    preset: str = "food",
    sites: str | list[str] | tuple[str, ...] | None = None,
    max_sites: int = 13,
    concurrency: int | None = None,
    output_dir: str | Path | None = None,
    run_id: str | None = None,
    timeout_ms: float = 30000,
    wait_after_ms: float = 1000,
    max_chars: int = 6000,
    browser_mode: str = "normal",
    keep_sessions: bool = False,
    session_create_timeout_sec: float = 60.0,
    auth_contexts_file: str | Path | None = None,
    use_auth_contexts: bool = True,
    preallocate_auth_context_sessions: bool = True,
    admin_factory: Callable[[], LexmountBrowserAdmin] = LexmountBrowserAdmin,
    job_runner: Callable[[ResearchJob], ResearchJobResult] | None = None,
    on_event: ResearchEventSink | None = None,
) -> ResearchRunSummary:
    """Run source jobs concurrently in separate Lexmount sessions."""

    resolved_concurrency = (
        concurrency if concurrency is not None else get_default_research_concurrency()
    )
    if resolved_concurrency <= 0:
        raise BrowserConfigError("concurrency must be greater than 0")
    if timeout_ms <= 0:
        raise BrowserConfigError("timeout_ms must be greater than 0")
    if max_chars <= 0:
        raise BrowserConfigError("max_chars must be greater than 0")
    if session_create_timeout_sec <= 0:
        raise BrowserConfigError("session_create_timeout_sec must be greater than 0")

    route = route_research(
        query=query,
        preset=preset,
        sites=sites,
        max_sites=max_sites,
    )
    if route.preset == "gov-policy":
        resolved_concurrency = min(resolved_concurrency, GOV_POLICY_MAX_CONCURRENCY)
    auth_context_store = (
        load_auth_context_store(auth_contexts_file) if use_auth_contexts else None
    )
    resolved_run_id = run_id or research_run_id()
    root = Path(output_dir) if output_dir else Path.cwd() / "lexmount-research-runs"
    resolved_output_dir = root / resolved_run_id
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    events_path = resolved_output_dir / "events.jsonl"
    sources_path = resolved_output_dir / "sources.jsonl"
    routes_path = resolved_output_dir / "routes.json"
    summary_path = resolved_output_dir / "summary.json"
    routes_path.write_text(
        route.model_dump_json(indent=2, by_alias=True) + "\n",
        encoding="utf-8",
    )

    lock = threading.Lock()

    def record_event(event_type: str, **payload: Any) -> None:
        event = _event_payload(event_type, **payload)
        with lock:
            _append_jsonl(events_path, event)
            if on_event is not None:
                on_event(event)

    record_event(
        "research_started",
        query=route.query,
        preset=route.preset,
        job_count=len(route.jobs),
        concurrency=resolved_concurrency,
        jobs=[
            {
                "rank": job.rank,
                "source_id": job.source_id,
                "source_name": job.source_name,
                "url": job.url,
            }
            for job in route.jobs
        ],
    )

    auth_context_by_rank = {
        job.rank: (
            auth_context_store.get(job.source_id)
            if auth_context_store is not None
            else None
        )
        for job in route.jobs
    }
    results_by_rank: dict[int, ResearchJobResult] = {}
    allocated_sessions_by_rank: dict[int, AllocatedResearchSession] = {}

    if preallocate_auth_context_sessions and job_runner is None:
        for job in route.jobs:
            auth_context = auth_context_by_rank[job.rank]
            if auth_context is None:
                continue
            allocated = _allocate_research_session(
                job=job,
                auth_context=auth_context,
                admin_factory=admin_factory,
                browser_mode=browser_mode,
                session_create_timeout_sec=session_create_timeout_sec,
                record_event=record_event,
                browser_ready_event_type="browser_prepared",
            )
            if isinstance(allocated, ResearchJobResult):
                results_by_rank[job.rank] = allocated
                record_event(
                    "job_finished",
                    source_id=job.source_id,
                    source_name=job.source_name,
                    ok=allocated.ok,
                    duration_ms=allocated.duration_ms,
                    error=allocated.error,
                    message=allocated.message,
                )
                with lock:
                    _append_jsonl(
                        sources_path,
                        allocated.model_dump(mode="json"),
                    )
            else:
                allocated_sessions_by_rank[job.rank] = allocated

    def execute(job: ResearchJob) -> ResearchJobResult:
        record_event(
            "job_started",
            source_id=job.source_id,
            source_name=job.source_name,
            url=job.url,
        )
        if job_runner is not None:
            result = job_runner(job)
        else:
            result = _run_research_job(
                job=job,
                admin_factory=admin_factory,
                timeout_ms=timeout_ms,
                wait_after_ms=wait_after_ms,
                max_chars=max_chars,
                browser_mode=browser_mode,
                keep_sessions=keep_sessions,
                session_create_timeout_sec=session_create_timeout_sec,
                auth_context=auth_context_by_rank[job.rank],
                allocated_session=allocated_sessions_by_rank.get(job.rank),
                record_event=record_event,
            )
        record_event(
            "job_finished",
            source_id=job.source_id,
            source_name=job.source_name,
            ok=result.ok,
            duration_ms=result.duration_ms,
            error=result.error,
            message=result.message,
        )
        with lock:
            _append_jsonl(sources_path, result.model_dump(mode="json"))
        return result

    jobs_to_run = [job for job in route.jobs if job.rank not in results_by_rank]
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=resolved_concurrency,
    ) as executor:
        for job, result in zip(
            jobs_to_run,
            executor.map(execute, jobs_to_run),
            strict=True,
        ):
            results_by_rank[job.rank] = result

    results = [results_by_rank[job.rank] for job in route.jobs]

    success_count = sum(1 for result in results if result.ok)
    failure_count = len(results) - success_count
    summary = ResearchRunSummary(
        ok=success_count > 0,
        query=route.query,
        preset=route.preset,
        run_id=resolved_run_id,
        output_dir=str(resolved_output_dir),
        events_path=str(events_path),
        sources_path=str(sources_path),
        routes_path=str(routes_path),
        summary_path=str(summary_path),
        success_count=success_count,
        failure_count=failure_count,
        concurrency=resolved_concurrency,
        jobs=route.jobs,
        results=results,
    )
    record_event(
        "research_finished",
        ok=summary.ok,
        success_count=success_count,
        failure_count=failure_count,
    )
    summary_path.write_text(
        summary.model_dump_json(indent=2, by_alias=True) + "\n",
        encoding="utf-8",
    )
    return summary


__all__ = [
    "ResearchCandidate",
    "ResearchJob",
    "ResearchJobResult",
    "ResearchLink",
    "ResearchRoute",
    "ResearchRunSummary",
    "ResearchSource",
    "research_run_id",
    "route_research",
    "run_research",
]
