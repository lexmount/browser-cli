"""Runtime API fetch, compaction, and summary helpers.

This module is agent-agnostic: callers provide an approved URL/response and get
compact runtime observations back without importing browser-use.
"""

# mypy: ignore-errors

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_MAX_API_RESPONSE_CHARS = 8000
_API_PRUNE_DEPTH = 4
_API_DEEP_SCALAR_KEYS = {
    "author",
    "brand",
    "brandName",
    "brandSize",
    "colour",
    "current",
    "displaySizeText",
    "id",
    "isAvailable",
    "name",
    "price",
    "pubTime",
    "publishTime",
    "source",
    "text",
    "title",
    "value",
}
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(?P<data>.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_HTML_RE = re.compile(
    r"<(?:!doctype\s+html|html|head|body|article|main|a\s)", re.IGNORECASE
)
_HTML_TITLE_RE = re.compile(
    r"<title[^>]*>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL
)
_HTML_META_RE = re.compile(
    r'<meta\b(?=[^>]*(?:name|property)=["\'](?P<name>description|og:title|og:description|article:published_time|pubdate|publishdate)["\'])[^>]*>',
    re.IGNORECASE | re.DOTALL,
)
_HTML_CONTENT_ATTR_RE = re.compile(
    r'\bcontent=["\'](?P<content>.*?)["\']', re.IGNORECASE | re.DOTALL
)
_HTML_ANCHOR_RE = re.compile(
    r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", re.IGNORECASE | re.DOTALL
)
_HTML_HREF_RE = re.compile(r'\bhref=["\'](?P<href>.*?)["\']', re.IGNORECASE | re.DOTALL)
_HTML_DATE_RE = re.compile(
    r"(20\d{2}[-/.年]\s*\d{1,2}[-/.月]\s*\d{1,2}(?:日)?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)"
)
_HTML_SOURCE_RE = re.compile(r"(?:来源|Source)\s*[:：]\s*(?P<source>[^\s<｜|]{2,40})")
_SINA_HQ_RE = re.compile(
    r'var\s+hq_str_(?P<symbol>[A-Za-z0-9_]+)\s*=\s*"(?P<payload>.*?)";', re.DOTALL
)
_SINA_FOREX_KLINE_RE = re.compile(
    r'var\s+_[A-Za-z0-9_]+\s*=\s*\("(?P<payload>.*?)"\);', re.DOTALL
)
_BLOCKED_HTTP_FETCH_HOSTS = {"localhost", "localhost.localdomain"}
_DNS_HOST_RE = re.compile(
    r"^(?=.{1,253}\.?$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.?$"
)
_LEGACY_IPV4_PART_RE = re.compile(r"(?:0x[0-9a-f]+|[0-9]+)", re.IGNORECASE)
_MAX_HTTP_REDIRECTS = 5


def _looks_like_legacy_ipv4_literal(host: str) -> bool:
    """Return whether a host is a legacy IPv4 form accepted by OS resolvers."""

    parts = host.rstrip(".").split(".")
    return 1 <= len(parts) <= 4 and all(
        bool(_LEGACY_IPV4_PART_RE.fullmatch(part)) for part in parts
    )


def _is_public_http_url(url: str) -> bool:
    """Return whether a URL is safe for adapter-gated external HTTP fetch."""

    try:
        parsed = urlsplit(url)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.strip("[]").lower()
    if host in _BLOCKED_HTTP_FETCH_HOSTS or host.endswith(".localhost"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if _looks_like_legacy_ipv4_literal(host):
            return False
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return False
        return bool(_DNS_HOST_RE.fullmatch(ascii_host))
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _site_suffix(hostname: str | None) -> str:
    """Return a lightweight site suffix for same-site API origin checks."""

    if not hostname:
        return ""
    parts = hostname.lower().strip(".").split(".")
    if len(parts) <= 2:
        return ".".join(parts)
    return ".".join(parts[-2:])


def _same_site_or_subdomain(current_url: str, target_url: str) -> bool:
    """Return whether two URLs share a host or simple registrable-site suffix."""

    current = urlsplit(current_url)
    target = urlsplit(target_url)
    if current.scheme not in {"http", "https"} or target.scheme not in {
        "http",
        "https",
    }:
        return False
    current_host = (current.hostname or "").lower()
    target_host = (target.hostname or "").lower()
    if not current_host or not target_host:
        return False
    return current_host == target_host or _site_suffix(current_host) == _site_suffix(
        target_host
    )


def _known_blocked_page_message(url: str, text: str | None = None) -> str | None:
    """Return a compact stop instruction for known benchmark block pages."""

    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    lower_text = (text or "").lower()
    if host.endswith("finance.yahoo.com") and (
        "edge: too many requests" in lower_text or "too many requests" in lower_text
    ):
        return (
            'Yahoo Finance is rate-limited on this session: the allowed site returned "Edge: Too Many Requests". '
            "Stop now and report that the requested finance.yahoo.com data is unavailable instead of trying more "
            "Yahoo quote subpages or same-domain API routes."
        )
    if (
        host == "callback.58.com"
        or (host.endswith("58.com") and "antibot" in path)
        or (
            host.endswith("58.com")
            and any(
                marker in (text or "")
                for marker in ["疑似使用网页抓取工具", "系统检测到", "anti-bot"]
            )
        )
    ):
        return (
            "58.com blocked list access with its anti-bot page. Stop now and report that the requested 58 listing "
            "data is unavailable due anti-bot protection instead of opening more city/category/filter pages."
        )
    return None


async def _detect_known_blocked_page(
    browser_session: Any, fallback_url: str
) -> str | None:
    """Cheaply detect site-specific blocked pages immediately after navigation/API fetch."""

    current_url = fallback_url
    try:
        if hasattr(browser_session, "get_current_page_url"):
            current_url = await browser_session.get_current_page_url()
    except Exception:
        current_url = fallback_url

    url_only_message = _known_blocked_page_message(current_url)
    if url_only_message is not None:
        return url_only_message

    host = (urlsplit(current_url).hostname or "").lower()
    if not (host.endswith("finance.yahoo.com") or host.endswith("58.com")):
        return None
    try:
        page = await browser_session.must_get_current_page()
        text = await asyncio.wait_for(
            page.evaluate(
                """() => {
					const title = document.title || '';
					const body = document.body && document.body.innerText ? document.body.innerText : '';
					return (title + '\\n' + body).slice(0, 2000);
				}"""
            ),
            timeout=2.5,
        )
    except Exception:
        return None
    return _known_blocked_page_message(current_url, str(text))


def _is_cqvip_newsite_api(url: str) -> bool:
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""
    return hostname.endswith("cqvip.com") and parsed.path in {
        "/newsite/search",
        "/newsite/search-ags",
    }


_CQVIP_RESULT_FIELDS = [
    "newspaperInfo",
    "mediaName",
    "providerSource",
    "appNo",
    "pubNo",
    "otherOrganInfo",
    "cqvipIsOa",
]

_CQVIP_CORE_RANGE_PARAMS = {
    "BDHX": [
        "BDHX2004",
        "BDHX2008",
        "BDHX2011",
        "BDHX2014",
        "BDHX2000",
        "BDHX1992",
        "BDHX1996",
        "BDHX2017",
        "BDHX2020",
        "BDHX2023",
    ]
}


def _normalize_cqvip_search_body(url: str, body: str | None) -> str | None:
    if (
        not _is_cqvip_newsite_api(url)
        or urlsplit(url).path != "/newsite/search"
        or not body
    ):
        return body
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return body
    if not isinstance(payload, dict):
        return body
    payload.setdefault("language", "zh")
    payload.setdefault("openForceTranslate", True)
    payload.setdefault("indexSearch", True)
    payload.setdefault("resultField", _CQVIP_RESULT_FIELDS)
    payload.setdefault(
        "agsList",
        [
            {"code": "type", "size": 15},
            {"code": "conferenceLevel", "size": 10},
            {"code": "thesisType", "size": 10},
            {"code": "standardType", "size": 10},
            {"code": "language", "size": 10},
            {"code": "HQLXY", "size": 10},
            {"code": "Y", "size": 5},
            {"code": "C", "size": 5},
            {"code": "range", "size": 5, "params": [_CQVIP_CORE_RANGE_PARAMS]},
        ],
    )
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


async def _fetch_api_via_http(
    url: str, method: str, headers: dict[str, str], body: str | None
) -> dict[str, Any]:
    """Fetch an adapter-approved URL outside the browser context."""

    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
    }
    request_headers.update(headers)
    current_url = url
    request_method = method.upper()
    request_body = body
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        for _ in range(_MAX_HTTP_REDIRECTS + 1):
            response = await client.request(
                request_method,
                current_url,
                headers=request_headers,
                content=request_body,
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            location = response.headers.get("location")
            if not location:
                break
            next_url = urljoin(str(response.url), location)
            if not _is_public_http_url(next_url):
                raise ValueError("Adapter HTTP fetch redirected to a non-public URL")
            current_url = next_url
            if response.status_code in {301, 302, 303} and request_method not in {
                "GET",
                "HEAD",
            }:
                request_method = "GET"
                request_body = None
        else:
            raise ValueError("Adapter HTTP fetch exceeded redirect limit")
    if "hq.sinajs.cn" in (urlsplit(url).hostname or ""):
        response.encoding = "gb18030"
    text = response.text
    try:
        data = response.json()
    except ValueError:
        data = text
    return {"ok": response.is_success, "status": response.status_code, "data": data}


def _prune_deep_json(obj, depth: int = 0, parent_key: str | None = None):
    if depth >= _API_PRUNE_DEPTH:
        if isinstance(obj, dict):
            kept = {
                k: _prune_deep_json(v, depth + 1, k)
                for k, v in obj.items()
                if k in _API_DEEP_SCALAR_KEYS and not isinstance(v, list)
            }
            if kept:
                omitted = len(obj) - len(kept)
                if omitted > 0:
                    kept["..."] = f"{omitted} omitted keys"
                return kept
            return f"{{...{len(obj)} keys}}"
        if isinstance(obj, list):
            return f"[...{len(obj)} items]"
        return obj
    if isinstance(obj, dict):
        return {k: _prune_deep_json(v, depth + 1, k) for k, v in obj.items()}
    if isinstance(obj, list):
        limit = (
            20
            if parent_key in {"products", "data", "items"}
            else 12
            if parent_key == "variants"
            else 5
        )
        head = [_prune_deep_json(v, depth + 1, parent_key) for v in obj[:limit]]
        if len(obj) > limit:
            head.append(f"...and {len(obj) - limit} more items")
        return head
    if isinstance(obj, str) and len(obj) > 800:
        return obj[:800] + f"... [{len(obj)} chars total]"
    return obj


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _compact_price(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        current = value.get("current")
        if isinstance(current, dict):
            return _compact_price(current.get("text") or current.get("value"))
        return _compact_price(
            value.get("text") or value.get("formatted") or value.get("value")
        )
    return None


def _extract_products_for_api_memory(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        for key in ("products", "data", "items", "results", "list"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        for key in ("data", "result", "payload"):
            value = data.get(key)
            if isinstance(value, (dict, list)):
                nested_items = _extract_products_for_api_memory(value)
                if nested_items:
                    return nested_items
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _extract_content_detail_for_api_memory(
    data: Any, depth: int = 0
) -> dict[str, Any] | None:
    if depth > 8:
        return None
    if isinstance(data, dict):
        if isinstance(data.get("contentDetail"), dict):
            return data["contentDetail"]
        if any(key in data for key in ("name", "title")) and any(
            key in data for key in ("source", "pubTime", "publishTime")
        ):
            return data
        for key in ("detailData", "pageProps", "props", "data", "detail", "article"):
            value = data.get(key)
            if isinstance(value, (dict, list)):
                detail = _extract_content_detail_for_api_memory(value, depth + 1)
                if detail:
                    return detail
    if isinstance(data, list):
        for item in data[:5]:
            detail = _extract_content_detail_for_api_memory(item, depth + 1)
            if detail:
                return detail
    return None


def _compact_api_text(value: Any, max_length: int = 180) -> str | None:
    if value is None:
        return None
    text = str(value)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length] if text else None


def _format_epoch_date(value: Any) -> str | None:
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return _compact_api_text(value, max_length=40)
    if seconds <= 0:
        return None
    if seconds > 10_000_000_000:
        seconds //= 1000
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):
        return str(value)


def _extract_jsonp_payload(value: str) -> Any:
    text = value.strip()
    match = re.match(r"^[A-Za-z_$][\w$]*\((.*)\)\s*;?\s*$", text, flags=re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


def _extract_next_data_from_html(value: str) -> dict[str, Any] | None:
    match = _NEXT_DATA_RE.search(value)
    if not match:
        return None
    try:
        return json.loads(html.unescape(match.group("data")).strip())
    except Exception:
        return None


def _extract_js_object_assignment(value: str, variable_name: str) -> str | None:
    match = re.search(rf"\b{re.escape(variable_name)}\s*=", value)
    if not match:
        return None
    start = value.find("{", match.end())
    if start == -1:
        return None
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(value)):
        char = value[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return value[start : index + 1]
    return None


def _extract_you163_json_data(value: str) -> dict[str, Any] | None:
    raw = _extract_js_object_assignment(value, "JSON_DATA_FROMFTL")
    if not raw:
        return None
    cleaned = html.unescape(raw)
    cleaned = re.sub(r":\s*''", ': ""', cleaned)
    cleaned = re.sub(r":\s*undefined\b", ": null", cleaned)
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    try:
        data = json.loads(cleaned)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _looks_like_html(value: str) -> bool:
    return bool(_HTML_RE.search(value[:4096]))


def _strip_html(value: str) -> str:
    value = re.sub(r"<!--.*?-->", " ", value, flags=re.DOTALL)
    value = re.sub(
        r"<(script|style|noscript|svg|template)\b[^>]*>.*?</\1>",
        " ",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(
        r"</(p|div|li|h[1-6]|tr|section|article|main)>",
        "\n",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _extract_html_meta(value: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for match in _HTML_META_RE.finditer(value):
        name = match.group("name").lower()
        content_match = _HTML_CONTENT_ATTR_RE.search(match.group(0))
        if not content_match:
            continue
        key = {
            "og:title": "title",
            "og:description": "description",
            "article:published_time": "pubTime",
            "publishdate": "pubTime",
            "pubdate": "pubTime",
        }.get(name, name)
        content = _compact_api_text(
            html.unescape(content_match.group("content")), max_length=500
        )
        if content and key not in meta:
            meta[key] = content
    return meta


def _extract_html_links(value: str, base_url: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in _HTML_ANCHOR_RE.finditer(value):
        href_match = _HTML_HREF_RE.search(match.group("attrs"))
        if not href_match:
            continue
        text = _compact_api_text(_strip_html(match.group("body")), max_length=220)
        href = html.unescape(href_match.group("href")).strip()
        if not href or href.startswith(("javascript:", "#", "mailto:")):
            continue
        if not text:
            continue
        url = urljoin(base_url, href)
        key = (url, text)
        if key in seen:
            continue
        seen.add(key)
        score = 0
        if len(text) >= 8:
            score += 10
        if len(text) >= 20:
            score += 12
        if re.search(
            r"20\d{2}|最新|补丁|更新|价格|评分|reviews?|rating|¥|￥|\$|€|£",
            text,
            re.IGNORECASE,
        ):
            score += 18
        if re.search(
            r"/news/\d+/\d+\.html|/newsDetail_|/p/|/product|\.html?(?:[?#]|$)",
            url,
            re.IGNORECASE,
        ):
            score += 18
        if re.search(
            r"login|register|privacy|cookie|javascript|share|footer|header",
            text + " " + url,
            re.IGNORECASE,
        ):
            score -= 25
        items.append({"title": text, "url": url, "_score": str(score)})
    indexed_items = list(enumerate(items))
    indexed_items.sort(key=lambda pair: (-int(pair[1]["_score"]), pair[0]))
    items = [item for _, item in indexed_items]
    for item in items:
        item.pop("_score", None)
    return items[:30]


def _extract_readable_data_from_html(
    value: str, base_url: str
) -> dict[str, Any] | None:
    """Turn ordinary HTML responses into compact data for browser_api_call."""

    if not _looks_like_html(value):
        return None
    title_match = _HTML_TITLE_RE.search(value)
    meta = _extract_html_meta(value)
    text = _strip_html(value)
    if not text and not title_match and not meta:
        return None
    data: dict[str, Any] = {"html": True}
    title = meta.get("title")
    if not title and title_match:
        title = _compact_api_text(
            _strip_html(title_match.group("title")), max_length=240
        )
    if title:
        data["title"] = title
    description = meta.get("description")
    if description:
        data["description"] = description
    pub_time = meta.get("pubTime")
    if not pub_time:
        date_match = _HTML_DATE_RE.search(text)
        if date_match:
            pub_time = (
                re.sub(r"\s+", " ", date_match.group(1))
                .replace("年", "-")
                .replace("月", "-")
                .replace("日", "")
            )
    if pub_time:
        data["pubTime"] = pub_time
    source_match = _HTML_SOURCE_RE.search(text)
    if source_match:
        data["source"] = source_match.group("source")
    links = _extract_html_links(value, base_url)
    if links:
        data["items"] = links
    if text:
        data["text"] = text[:6000] + (
            f"... [{len(text)} chars total]" if len(text) > 6000 else ""
        )
    return data


def _is_3dm_mod_api(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.hostname or ""
    ).lower() == "mod.3dmgame.com" and parsed.path.startswith("/api/")


def _compact_3dm_mod_item(
    item: dict[str, Any], *, include_resources: bool = False
) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    field_map = (
        ("id", "id"),
        ("gameId", "game_id"),
        ("gameName", "game_name"),
        ("gamePath", "game_path"),
        ("title", "mods_title"),
        ("description", "mods_desc"),
        ("content", "mods_content"),
        ("version", "mods_version"),
        ("type", "mods_type_name"),
        ("author", "mods_author"),
        ("user", "user_nickName"),
        ("downloadCount", "mods_download_cnt"),
        ("clickCount", "mods_click_cnt"),
        ("markCount", "mods_mark_cnt"),
        ("updateTime", "mods_updateTime"),
        ("createTime", "mods_createTime"),
        ("supportGmm", "support_gmm"),
        ("recommended", "mods_isRecommend"),
        ("adultContent", "mods_adult_content"),
    )
    for target, source in field_map:
        value = item.get(source)
        if value in (None, "", []):
            continue
        if target in {"title", "description"}:
            value = _compact_api_text(value, max_length=260)
        elif target == "content":
            value = _compact_api_text(value, max_length=700)
        compact[target] = value
    mod_id = compact.get("id")
    if mod_id is not None:
        compact["detailUrl"] = f"https://mod.3dmgame.com/mod/{mod_id}"
    if include_resources:
        resources = []
        for resource in item.get("mods_resource") or []:
            if not isinstance(resource, dict):
                continue
            resource_item: dict[str, Any] = {}
            for target, source in (
                ("name", "mods_resource_name"),
                ("url", "mods_resource_url"),
                ("size", "mods_resource_size"),
                ("format", "mods_resource_formart"),
                ("version", "mods_resource_version"),
            ):
                value = resource.get(source)
                if value not in (None, ""):
                    resource_item[target] = (
                        _compact_api_text(value, max_length=220)
                        if target == "name"
                        else value
                    )
            if resource_item:
                resources.append(resource_item)
            if len(resources) >= 5:
                break
        if resources:
            compact["resources"] = resources
    return compact


def _compact_3dm_mod_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact 3DM Mod search/detail API responses into reusable runtime state."""

    if not _is_3dm_mod_api(url) or not isinstance(data, dict):
        return None
    payload = data.get("data") if isinstance(data.get("data"), (dict, list)) else data
    path = urlsplit(url).path
    if path == "/api/search/getModlist" and isinstance(payload, dict):
        raw_mods = payload.get("mods")
        if not isinstance(raw_mods, list):
            return None
        items = [
            _compact_3dm_mod_item(item) for item in raw_mods if isinstance(item, dict)
        ]
        items = [item for item in items if item]
        return {
            "threeDmModSearch": True,
            "count": len(items),
            "items": items[:30],
        }
    if re.fullmatch(r"/api/mods/\d+", path) and isinstance(payload, dict):
        detail = _compact_3dm_mod_item(payload, include_resources=True)
        if not detail:
            return None
        detail["threeDmModDetail"] = True
        return detail
    return None


def _summarize_3dm_mod_data(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    if data.get("threeDmModSearch"):
        chunks = ["3dm_mod_search"]
        if data.get("count") is not None:
            chunks.append(f"count={data['count']}")
        items = []
        for index, item in enumerate(data.get("items") or [], 1):
            if not isinstance(item, dict):
                continue
            parts = [f"#{index}"]
            for label, key in (
                ("id", "id"),
                ("game", "gameName"),
                ("downloads", "downloadCount"),
                ("updated", "updateTime"),
                ("type", "type"),
                ("title", "title"),
                ("url", "detailUrl"),
            ):
                value = item.get(key)
                if value not in (None, ""):
                    parts.append(f"{label}={value}")
            items.append("; ".join(parts))
            if len(items) >= 10:
                break
        if items:
            chunks.append("items: " + " | ".join(items))
        return "; ".join(chunks)[:4000]
    if data.get("threeDmModDetail"):
        chunks = ["3dm_mod_detail"]
        for label, key in (
            ("id", "id"),
            ("game", "gameName"),
            ("downloads", "downloadCount"),
            ("updated", "updateTime"),
            ("version", "version"),
            ("type", "type"),
            ("title", "title"),
            ("desc", "description"),
            ("url", "detailUrl"),
        ):
            value = data.get(key)
            if value not in (None, ""):
                chunks.append(f"{label}={value}")
        resources = data.get("resources")
        if isinstance(resources, list) and resources:
            resource_parts = []
            for resource in resources[:3]:
                if not isinstance(resource, dict):
                    continue
                name = resource.get("name")
                url = resource.get("url")
                size = resource.get("size")
                bits = []
                if name:
                    bits.append(str(name))
                if size:
                    bits.append(str(size))
                if url:
                    bits.append(str(url))
                if bits:
                    resource_parts.append(" / ".join(bits))
            if resource_parts:
                chunks.append("resources=" + " | ".join(resource_parts))
        return "; ".join(chunks)[:4000]
    return None


def _is_bilibili_api(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return host == "api.bilibili.com" or host.endswith(".bilibili.com")


def _compact_bilibili_video_item(item: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    owner_raw = item.get("owner")
    owner: dict[str, Any] = owner_raw if isinstance(owner_raw, dict) else {}
    stat_raw = item.get("stat")
    stat: dict[str, Any] = stat_raw if isinstance(stat_raw, dict) else {}
    field_map = (
        ("bvid", "bvid"),
        ("aid", "aid"),
        ("aid", "id"),
        ("title", "title"),
        ("uploader", "author"),
        ("description", "description"),
        ("description", "desc"),
        ("duration", "duration"),
        ("arcurl", "arcurl"),
    )
    for target, source in field_map:
        if target in compact:
            continue
        value = item.get(source)
        if value in (None, "", []):
            continue
        if target in {"title", "uploader", "description"}:
            value = _compact_api_text(
                value, max_length=360 if target == "description" else 220
            )
        compact[target] = value
    if owner:
        if owner.get("name") and not compact.get("uploader"):
            compact["uploader"] = _compact_api_text(owner.get("name"), max_length=120)
        if owner.get("mid") is not None:
            compact["mid"] = owner.get("mid")
    for target, source in (
        ("play", "view"),
        ("play", "play"),
        ("danmaku", "danmaku"),
        ("danmaku", "video_review"),
        ("likes", "like"),
        ("coins", "coin"),
        ("favorites", "favorite"),
        ("shares", "share"),
    ):
        if target in compact:
            continue
        value = stat.get(source) if source in stat else item.get(source)
        if value not in (None, ""):
            compact[target] = value
    pubdate = item.get("pubdate") or item.get("created")
    publish_date = _format_epoch_date(pubdate)
    if publish_date:
        compact["publishDate"] = publish_date
    return compact


def _compact_bilibili_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact Bilibili search/detail APIs into video cards with stable stats."""

    if not _is_bilibili_api(url):
        return None
    if isinstance(data, str):
        jsonp = _extract_jsonp_payload(data)
        if isinstance(jsonp, dict):
            data = jsonp
    if not isinstance(data, dict):
        return None
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    path = urlsplit(url).path
    if path == "/x/web-interface/search/type" and isinstance(payload, dict):
        raw_items = payload.get("result")
        if not isinstance(raw_items, list):
            return None
        items = [
            _compact_bilibili_video_item(item)
            for item in raw_items
            if isinstance(item, dict)
        ]
        items = [item for item in items if item]
        query = parse_qs(urlsplit(url).query)
        return {
            "bilibiliVideoSearch": True,
            "keyword": (query.get("keyword") or [""])[0],
            "order": (query.get("order") or [""])[0],
            "count": len(items),
            "items": items[:20],
        }
    if path == "/x/web-interface/view" and isinstance(payload, dict):
        detail = _compact_bilibili_video_item(payload)
        if not detail:
            return None
        detail["bilibiliVideoDetail"] = True
        return detail
    return None


def _summarize_bilibili_data(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    if data.get("bilibiliVideoSearch"):
        chunks = ["bilibili_video_search"]
        for label, key in (
            ("keyword", "keyword"),
            ("order", "order"),
            ("count", "count"),
        ):
            value = data.get(key)
            if value not in (None, ""):
                chunks.append(f"{label}={value}")
        items = []
        for index, item in enumerate(data.get("items") or [], 1):
            if not isinstance(item, dict):
                continue
            parts = [f"#{index}"]
            for label, key in (
                ("bvid", "bvid"),
                ("uploader", "uploader"),
                ("plays", "play"),
                ("danmaku", "danmaku"),
                ("likes", "likes"),
                ("coins", "coins"),
                ("duration", "duration"),
                ("pub", "publishDate"),
                ("title", "title"),
            ):
                value = item.get(key)
                if value not in (None, ""):
                    parts.append(f"{label}={value}")
            items.append("; ".join(parts))
            if len(items) >= 10:
                break
        if items:
            chunks.append("items: " + " | ".join(items))
        return "; ".join(chunks)[:4000]
    if data.get("bilibiliVideoDetail"):
        chunks = ["bilibili_video_detail"]
        for label, key in (
            ("bvid", "bvid"),
            ("uploader", "uploader"),
            ("plays", "play"),
            ("likes", "likes"),
            ("coins", "coins"),
            ("favorites", "favorites"),
            ("shares", "shares"),
            ("danmaku", "danmaku"),
            ("duration", "duration"),
            ("pub", "publishDate"),
            ("title", "title"),
        ):
            value = data.get(key)
            if value not in (None, ""):
                chunks.append(f"{label}={value}")
        return "; ".join(chunks)[:4000]
    return None


class YouTubeSearchRequest(BaseModel):
    """One YouTube search request for runtime batch collection."""

    query: str = Field(
        description='YouTube search query, for example "Python programming tutorial"'
    )
    file_name: str | None = Field(
        default=None,
        description="Optional JSON filename to save this query result. Defaults to a sanitized query-based name.",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Number of video results to collect for this query",
    )
    sort: Literal["relevance", "upload_date", "view_count", "rating"] = Field(
        default="relevance",
        description="YouTube search sort order. Use view_count for popularity/view-count tasks.",
    )
    upload_date: Literal[
        "any", "last_hour", "today", "this_week", "this_month", "this_year"
    ] = Field(
        default="any",
        description="YouTube upload date filter.",
    )
    duration: Literal["any", "short", "medium", "long"] = Field(
        default="any",
        description="YouTube duration filter: short <4 minutes, medium 4-20 minutes, long >20 minutes.",
    )


class YouTubeSearchBatchAction(BaseModel):
    """Batch YouTube search action schema for browser agents."""

    searches: list[YouTubeSearchRequest] = Field(
        min_length=1,
        max_length=20,
        description="Batch of YouTube searches to collect and save. Use one action for multi-topic/multi-category tasks.",
    )
    save_files: bool = Field(
        default=True, description="Write one JSON file per search result batch"
    )


@dataclass(frozen=True)
class YouTubeFetchedPage:
    """Fetched YouTube search page returned by an upper-layer browser callback."""

    status: int
    url: str
    text: str


YouTubePageFetcher = Callable[
    [str], Awaitable[YouTubeFetchedPage | tuple[int, str, str] | dict[str, Any]]
]
YouTubeJsonWriter = Callable[[str, str], Awaitable[str]]


def _extract_balanced_json_object(text: str, start: int) -> Any | None:
    """Extract a JSON object from text starting at or before the opening brace."""

    brace_start = text.find("{", start)
    if brace_start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(brace_start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                raw = text[brace_start : index + 1]
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return None
    return None


def _extract_youtube_initial_data_from_html(value: str) -> dict[str, Any] | None:
    for marker in (
        "ytInitialData =",
        "var ytInitialData =",
        'window["ytInitialData"] =',
    ):
        start = value.find(marker)
        if start >= 0:
            payload = _extract_balanced_json_object(value, start + len(marker))
            return payload if isinstance(payload, dict) else None
    return None


def _youtube_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return _compact_api_text(value, max_length=300)
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        parts = [_youtube_text(item) for item in value]
        return _compact_api_text(
            "".join(part for part in parts if part), max_length=300
        )
    if not isinstance(value, dict):
        return None
    for key in ("simpleText", "text", "label"):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return _compact_api_text(text, max_length=300)
    runs = value.get("runs")
    if isinstance(runs, list):
        parts = []
        for run in runs:
            if isinstance(run, dict) and isinstance(run.get("text"), str):
                parts.append(run["text"])
        if parts:
            return _compact_api_text("".join(parts), max_length=300)
    accessibility = value.get("accessibility") or value.get("accessibilityData")
    if isinstance(accessibility, dict):
        return _youtube_text(accessibility.get("accessibilityData") or accessibility)
    return None


def _youtube_endpoint_url(endpoint: Any) -> str | None:
    if not isinstance(endpoint, dict):
        return None
    command = endpoint.get("commandMetadata")
    if isinstance(command, dict):
        web = command.get("webCommandMetadata")
        if isinstance(web, dict) and isinstance(web.get("url"), str):
            return web["url"]
    url_endpoint = endpoint.get("urlEndpoint")
    if isinstance(url_endpoint, dict) and isinstance(url_endpoint.get("url"), str):
        return url_endpoint["url"]
    watch = endpoint.get("watchEndpoint")
    if isinstance(watch, dict) and isinstance(watch.get("videoId"), str):
        return f"/watch?v={watch['videoId']}"
    return None


def _iter_json_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_json_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_json_dicts(child)


def _compact_youtube_video_renderer(renderer: dict[str, Any]) -> dict[str, Any] | None:
    video_id = renderer.get("videoId")
    if not isinstance(video_id, str) or not video_id:
        return None
    title = _youtube_text(renderer.get("title"))
    if not title:
        return None
    endpoint_url = _youtube_endpoint_url(renderer.get("navigationEndpoint"))
    url = (
        urljoin("https://www.youtube.com", endpoint_url)
        if endpoint_url
        else f"https://www.youtube.com/watch?v={video_id}"
    )
    item: dict[str, Any] = {"videoId": video_id, "title": title, "url": url}
    for target, source in (
        ("channel", "ownerText"),
        ("channel", "longBylineText"),
        ("views", "viewCountText"),
        ("published", "publishedTimeText"),
        ("duration", "lengthText"),
        ("description", "descriptionSnippet"),
    ):
        if target in item:
            continue
        text = _youtube_text(renderer.get(source))
        if text:
            item[target] = text
    return item


def _extract_youtube_video_items(data: Any, limit: int = 50) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in _iter_json_dicts(data):
        renderer = node.get("videoRenderer")
        if not isinstance(renderer, dict):
            continue
        item = _compact_youtube_video_renderer(renderer)
        if not item:
            continue
        video_id = str(item.get("videoId") or "")
        if video_id in seen:
            continue
        seen.add(video_id)
        items.append(item)
        if len(items) >= limit:
            break
    return items


def _extract_youtube_filter_options(data: Any) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for node in _iter_json_dicts(data):
        renderer = node.get("searchFilterRenderer")
        if not isinstance(renderer, dict):
            continue
        label = _youtube_text(
            renderer.get("label") or renderer.get("tooltip") or renderer.get("title")
        )
        endpoint_url = _youtube_endpoint_url(renderer.get("navigationEndpoint"))
        if not label or not endpoint_url:
            continue
        absolute_url = urljoin("https://www.youtube.com", endpoint_url)
        key = (label.lower(), absolute_url)
        if key in seen:
            continue
        seen.add(key)
        option = {"label": label, "url": absolute_url}
        status = renderer.get("status")
        if isinstance(status, str) and status:
            option["status"] = status
        options.append(option)
    return options


def _normalize_youtube_filter_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _youtube_filter_labels(kind: str, value: str) -> list[str]:
    labels = {
        "sort": {
            "upload_date": ["Upload date"],
            "view_count": ["View count"],
            "rating": ["Rating"],
            "relevance": ["Relevance"],
        },
        "upload_date": {
            "last_hour": ["Last hour"],
            "today": ["Today"],
            "this_week": ["This week"],
            "this_month": ["This month"],
            "this_year": ["This year"],
        },
        "duration": {
            "short": ["Under 4 minutes", "Short (< 4 minutes)", "Short"],
            "medium": [
                "4 - 20 minutes",
                "4-20 minutes",
                "Medium (4 - 20 minutes)",
                "Medium",
            ],
            "long": ["Over 20 minutes", "Long (> 20 minutes)", "Long"],
        },
    }
    return labels.get(kind, {}).get(value, [])


_YOUTUBE_SINGLE_FILTER_SP = {
    ("sort", "upload_date"): "CAI%3D",
    ("sort", "view_count"): "CAM%3D",
    ("sort", "rating"): "CAE%3D",
    ("upload_date", "last_hour"): "EgIIAQ%3D%3D",
    ("upload_date", "today"): "EgIIAg%3D%3D",
    ("upload_date", "this_week"): "EgIIAw%3D%3D",
    ("upload_date", "this_month"): "EgIIBA%3D%3D",
    ("upload_date", "this_year"): "EgIIBQ%3D%3D",
    ("duration", "short"): "EgIYAQ%3D%3D",
    ("duration", "long"): "EgIYAg%3D%3D",
}


def _youtube_url_has_sp(url: str) -> bool:
    return bool(parse_qs(urlsplit(url).query).get("sp"))


def _youtube_single_filter_url(url: str, kind: str, value: str) -> str | None:
    sp = _YOUTUBE_SINGLE_FILTER_SP.get((kind, value))
    if not sp or _youtube_url_has_sp(url):
        return None
    parsed = urlsplit(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["sp"] = [sp]
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query, doseq=True),
            parsed.fragment,
        )
    )


def _find_youtube_filter_url(data: Any, labels: list[str]) -> str | None:
    targets = {_normalize_youtube_filter_label(label) for label in labels}
    options = (
        data.get("filterOptions")
        if isinstance(data, dict) and isinstance(data.get("filterOptions"), list)
        else None
    )
    if options is None:
        options = _extract_youtube_filter_options(data)
    for option in options:
        if not isinstance(option, dict):
            continue
        label = option.get("label")
        url = option.get("url")
        if (
            isinstance(label, str)
            and isinstance(url, str)
            and _normalize_youtube_filter_label(label) in targets
        ):
            return url
    return None


def _youtube_search_url(query: str) -> str:
    return f"https://www.youtube.com/results?search_query={quote(query, safe='')}"


def _is_youtube_search_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return host.endswith("youtube.com") and parsed.path == "/results"


def _compact_youtube_search_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact YouTube search HTML/ytInitialData into stable video cards and filter links."""

    if not _is_youtube_search_url(url):
        return None
    if isinstance(data, str):
        data = _extract_youtube_initial_data_from_html(data)
    if not isinstance(data, dict):
        return None
    items = _extract_youtube_video_items(data, limit=50)
    if not items:
        return None
    query = parse_qs(urlsplit(url).query)
    return {
        "youtubeSearchResults": True,
        "query": (query.get("search_query") or [""])[0],
        "sp": (query.get("sp") or [""])[0],
        "count": len(items),
        "items": items,
        "filterOptions": _extract_youtube_filter_options(data)[:40],
    }


def _summarize_youtube_search_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("youtubeSearchResults"):
        return None
    chunks = ["youtube_search"]
    for label, key in (("query", "query"), ("count", "count")):
        value = data.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    items = []
    for index, item in enumerate(data.get("items") or [], 1):
        if not isinstance(item, dict):
            continue
        parts = [f"#{index}"]
        for label, key in (
            ("id", "videoId"),
            ("title", "title"),
            ("channel", "channel"),
            ("views", "views"),
            ("published", "published"),
            ("duration", "duration"),
            ("url", "url"),
        ):
            value = item.get(key)
            if value not in (None, ""):
                parts.append(f"{label}={value}")
        items.append("; ".join(parts))
        if len(items) >= 10:
            break
    if items:
        chunks.append("items: " + " | ".join(items))
    return "; ".join(chunks)[:4000]


def _coerce_youtube_fetched_page(
    value: YouTubeFetchedPage | tuple[int, str, str] | dict[str, Any],
    requested_url: str,
) -> YouTubeFetchedPage:
    if isinstance(value, YouTubeFetchedPage):
        return value
    if isinstance(value, tuple) and len(value) == 3:
        status, final_url, text = value
        return YouTubeFetchedPage(
            status=int(status or 0),
            url=str(final_url or requested_url),
            text=str(text or ""),
        )
    if isinstance(value, dict):
        return YouTubeFetchedPage(
            status=int(value.get("status") or 0),
            url=str(value.get("url") or requested_url),
            text=str(value.get("text") or ""),
        )
    return YouTubeFetchedPage(status=0, url=requested_url, text="")


async def collect_youtube_search_results(
    params: YouTubeSearchBatchAction,
    fetch_page: YouTubePageFetcher,
    write_json_file: YouTubeJsonWriter | None = None,
) -> dict[str, Any]:
    """Collect YouTube search pages in batch using caller-provided browser/file callbacks."""

    async def fetch_search_page(url: str) -> tuple[int, str, dict[str, Any] | None]:
        fetched = _coerce_youtube_fetched_page(await fetch_page(url), url)
        compact = _compact_youtube_search_data(fetched.url, fetched.text)
        return fetched.status, fetched.url, compact

    async def apply_filter(url: str, kind: str, value: str) -> tuple[str, str | None]:
        if value in ("", "any", "relevance"):
            return url, None
        labels = _youtube_filter_labels(kind, value)
        if not labels:
            return url, None
        _status, final_url, compact = await fetch_search_page(url)
        if not compact:
            return final_url, None
        target_url = _find_youtube_filter_url(
            {"filterOptions": compact.get("filterOptions", [])}, labels
        )
        if target_url:
            return target_url, "/".join(labels)
        direct_url = _youtube_single_filter_url(final_url, kind, value)
        if direct_url:
            return direct_url, "/".join(labels)
        return final_url, None

    results: list[dict[str, Any]] = []
    written_files: list[str] = []
    for request in params.searches:
        search_url = _youtube_search_url(request.query)
        applied_filters: list[str] = []
        for kind, value in (
            ("upload_date", request.upload_date),
            ("duration", request.duration),
            ("sort", request.sort),
        ):
            next_url, applied = await apply_filter(search_url, kind, value)
            search_url = next_url
            if applied:
                applied_filters.append(f"{kind}={value}")
        status, final_url, compact = await fetch_search_page(search_url)
        items = (compact or {}).get("items") if isinstance(compact, dict) else []
        if not isinstance(items, list):
            items = []
        limited_items = items[: request.limit]
        payload = {
            "query": request.query,
            "requested": {
                "limit": request.limit,
                "sort": request.sort,
                "upload_date": request.upload_date,
                "duration": request.duration,
            },
            "url": final_url,
            "status": status,
            "filtersApplied": applied_filters,
            "count": len(limited_items),
            "items": limited_items,
        }
        file_name = request.file_name
        if not file_name:
            base = (
                re.sub(r"[^A-Za-z0-9_-]+", "_", request.query.strip())
                .strip("_")
                .lower()
                or "youtube"
            )
            file_name = f"{base}_youtube_results.json"
        saved_file_name: str | None = None
        if params.save_files and write_json_file is not None:
            saved_file_name = await write_json_file(
                file_name, json.dumps(payload, ensure_ascii=False, indent=2)
            )
            written_files.append(saved_file_name)
        results.append({**payload, "fileName": saved_file_name})

    return {"youtubeSearchBatch": True, "savedFiles": written_files, "results": results}


def _is_suning_review_satisfy_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.hostname or ""
    ).lower() == "review.suning.com" and "/ajax/review_satisfy/" in parsed.path


def _compact_suning_review_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact Suning review_satisfy JSONP into product good-rate rows."""

    if not _is_suning_review_satisfy_url(url):
        return None
    if isinstance(data, str):
        jsonp = _extract_jsonp_payload(data)
        if isinstance(jsonp, dict):
            data = jsonp
    if not isinstance(data, dict):
        return None
    raw_counts = data.get("reviewCounts")
    if not isinstance(raw_counts, list):
        return None
    items: list[dict[str, Any]] = []
    for raw in raw_counts:
        if not isinstance(raw, dict):
            continue
        item: dict[str, Any] = {}
        for target, source in (
            ("commodityCode", "commodityCode"),
            ("shopCode", "shopCode"),
            ("totalCount", "totalCount"),
            ("goodRate", "goodRate"),
            ("qualityStar", "qualityStar"),
            ("fiveStarCount", "fiveStarCount"),
            ("fourStarCount", "fourStarCount"),
            ("threeStarCount", "threeStarCount"),
            ("goodLabelName", "goodLabelName"),
        ):
            value = raw.get(source)
            if value not in (None, ""):
                item[target] = (
                    _compact_api_text(value, max_length=120)
                    if target == "goodLabelName"
                    else value
                )
        if item:
            items.append(item)
    if not items:
        return None
    return {
        "suningReviewSatisfy": True,
        "returnCode": data.get("returnCode"),
        "count": len(items),
        "items": items,
    }


def _summarize_suning_review_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("suningReviewSatisfy"):
        return None
    chunks = ["suning_review_satisfy"]
    items = []
    for index, item in enumerate(data.get("items") or [], 1):
        if not isinstance(item, dict):
            continue
        parts = [f"#{index}"]
        for label, key in (
            ("commodity", "commodityCode"),
            ("shop", "shopCode"),
            ("goodRate", "goodRate"),
            ("total", "totalCount"),
            ("qualityStar", "qualityStar"),
            ("fiveStar", "fiveStarCount"),
            ("label", "goodLabelName"),
        ):
            value = item.get(key)
            if value not in (None, ""):
                parts.append(f"{label}={value}")
        items.append("; ".join(parts))
        if len(items) >= 10:
            break
    if items:
        chunks.append("items: " + " | ".join(items))
    return "; ".join(chunks)[:4000]


def _is_ctrip_trainbooking_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    return host == "trains.ctrip.com" and parsed.path.startswith("/trainbooking/")


def _number_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _ctrip_second_class_price(train: dict[str, Any]) -> float | None:
    raw_seats = train.get("seatItemInfoList")
    if not isinstance(raw_seats, list):
        return None
    for seat in raw_seats:
        if not isinstance(seat, dict):
            continue
        seat_name = str(
            seat.get("seatName")
            or seat.get("seatTypeName")
            or seat.get("seatNameForBooking")
            or ""
        )
        if "二等" not in seat_name:
            continue
        for key in ("seatPrice", "price", "showSeatPrice"):
            price = _number_or_none(seat.get(key))
            if price is not None:
                return price
    return _number_or_none(train.get("secondClassPrice"))


def _compact_ctrip_train_item(train: dict[str, Any]) -> dict[str, Any]:
    item: dict[str, Any] = {}
    for target, source in (
        ("trainNumber", "trainNumber"),
        ("runMinutes", "runTime"),
        ("duration", "duration"),
        ("departureStationName", "departureStationName"),
        ("arrivalStationName", "arrivalStationName"),
        ("departureTime", "departureTime"),
        ("arrivalTime", "arrivalTime"),
        ("startPrice", "startPrice"),
    ):
        value = train.get(source)
        if value not in (None, ""):
            item[target] = value
    second_class_price = _ctrip_second_class_price(train)
    if second_class_price is not None:
        item["secondClassPrice"] = second_class_price
    return item


def _ctrip_time_minutes(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"(\d{1,2}):(\d{2})", value)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def _compact_ctrip_train_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact Ctrip train route pages into route-level fastest/cheapest rows."""

    if not _is_ctrip_trainbooking_url(url):
        return None
    if isinstance(data, str):
        next_data = _extract_next_data_from_html(data)
        if next_data is None:
            return None
        data = next_data
    if not isinstance(data, dict):
        return None
    try:
        train_info = data["props"]["pageProps"]["initialState"]["trainSearchInfo"]
    except (KeyError, TypeError):
        return None
    raw_trains = (
        train_info.get("trainInfoList") if isinstance(train_info, dict) else None
    )
    if not isinstance(raw_trains, list):
        return None
    trains = [train for train in raw_trains if isinstance(train, dict)]
    compact_trains = [_compact_ctrip_train_item(train) for train in trains]
    priced = [
        item for item in compact_trains if item.get("secondClassPrice") is not None
    ]
    with_runtime = [
        item for item in compact_trains if item.get("runMinutes") is not None
    ]
    fastest = (
        min(
            with_runtime,
            key=lambda item: _number_or_none(item.get("runMinutes")) or math.inf,
        )
        if with_runtime
        else None
    )
    cheapest = (
        min(
            priced,
            key=lambda item: _number_or_none(item.get("secondClassPrice")) or math.inf,
        )
        if priced
        else None
    )
    morning_trains = [
        item
        for item in with_runtime
        if (depart_minutes := _ctrip_time_minutes(item.get("departureTime")))
        is not None
        and 8 * 60 <= depart_minutes < 12 * 60
    ]
    fastest_morning = (
        min(
            morning_trains,
            key=lambda item: _number_or_none(item.get("runMinutes")) or math.inf,
        )
        if morning_trains
        else None
    )
    parsed = urlsplit(url)
    route_slug = parsed.path.rsplit("/", 1)[-1]
    query = parse_qs(parsed.query)
    return {
        "ctripTrainRoute": True,
        "route": route_slug,
        "date": (query.get("date") or [""])[0],
        "trainCount": len(trains),
        "pricedSecondClassCount": len(priced),
        "fastest": fastest,
        "cheapest": cheapest,
        "fastestMorning08To12": fastest_morning,
        "trains": compact_trains[:12],
        "url": url,
    }


def _summarize_ctrip_train_item(label: str, item: dict[str, Any]) -> str:
    parts = [label]
    for item_label, key in (
        ("train", "trainNumber"),
        ("runMinutes", "runMinutes"),
        ("duration", "duration"),
        ("from", "departureStationName"),
        ("to", "arrivalStationName"),
        ("depart", "departureTime"),
        ("arrive", "arrivalTime"),
        ("secondClassPrice", "secondClassPrice"),
    ):
        value = item.get(key)
        if value not in (None, ""):
            parts.append(f"{item_label}={value}")
    return " ".join(parts)


def _summarize_ctrip_train_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("ctripTrainRoute"):
        return None
    chunks = ["ctrip_train_route"]
    for label, key in (
        ("route", "route"),
        ("date", "date"),
        ("trainCount", "trainCount"),
        ("pricedSecondClassCount", "pricedSecondClassCount"),
    ):
        value = data.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    for label in ("fastest", "cheapest", "fastestMorning08To12"):
        item = data.get(label)
        if not isinstance(item, dict):
            continue
        chunks.append(_summarize_ctrip_train_item(label, item))
    trains = data.get("trains")
    if isinstance(trains, list) and trains:
        samples = [
            _summarize_ctrip_train_item(f"#{index}", item)
            for index, item in enumerate(trains[:8], 1)
            if isinstance(item, dict)
        ]
        if samples:
            chunks.append("sampleTrains=" + " | ".join(samples))
    return "; ".join(chunks)[:4000]


def _compact_eastmoney_quote_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact Eastmoney stock quote responses into named quote fields."""

    parsed = urlsplit(url)
    if (
        not (parsed.hostname or "").endswith("eastmoney.com")
        or parsed.path != "/api/qt/stock/get"
    ):
        return None
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None
    payload = data.get("data")
    if not isinstance(payload, dict):
        return None

    def field(name: str) -> Any:
        value = payload.get(name)
        return None if value in (None, "", "-") else value

    price = _number_or_none(field("f43"))
    previous_close = _number_or_none(field("f60"))
    result: dict[str, Any] = {
        "eastmoneyQuote": True,
        "secid": (parse_qs(parsed.query).get("secid") or [""])[0],
        "code": field("f57"),
        "name": _compact_api_text(field("f58"), max_length=80),
        "price": price if price is not None else field("f43"),
        "open": field("f46"),
        "high": field("f44"),
        "low": field("f45"),
        "previousClose": previous_close if previous_close is not None else field("f60"),
        "volume": field("f47"),
        "url": url,
    }
    if price is not None and previous_close:
        change = price - previous_close
        result["change"] = round(change, 4)
        result["changePct"] = f"{(change / previous_close) * 100:.2f}%"
    timestamp = _number_or_none(field("f86"))
    if timestamp is not None and timestamp > 0:
        result["timestamp"] = int(timestamp)
        result["timeUtc"] = datetime.fromtimestamp(
            int(timestamp), timezone.utc
        ).isoformat()
    return {key: value for key, value in result.items() if value not in (None, "")}


def _summarize_eastmoney_quote_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("eastmoneyQuote"):
        return None
    chunks = ["eastmoney_quote"]
    for label, key in (
        ("secid", "secid"),
        ("code", "code"),
        ("name", "name"),
        ("price", "price"),
        ("open", "open"),
        ("high", "high"),
        ("low", "low"),
        ("previousClose", "previousClose"),
        ("change", "change"),
        ("changePct", "changePct"),
        ("volume", "volume"),
        ("timeUtc", "timeUtc"),
    ):
        value = data.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    return "; ".join(chunks)[:4000]


def _is_douban_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "douban.com" or host.endswith(".douban.com")


def _compact_douban_suggest_data(url: str, data: Any) -> dict[str, Any] | None:
    if not _is_douban_url(url) or "/j/subject_suggest" not in urlsplit(url).path:
        return None
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    if not isinstance(data, list):
        return None
    items: list[dict[str, Any]] = []
    for raw in data[:20]:
        if not isinstance(raw, dict):
            continue
        item: dict[str, Any] = {}
        for target, source in (
            ("id", "id"),
            ("type", "type"),
            ("title", "title"),
            ("author", "author_name"),
            ("year", "year"),
            ("url", "url"),
        ):
            value = raw.get(source)
            if value not in (None, ""):
                item[target] = _compact_api_text(value, max_length=220)
        if item:
            items.append(item)
    if not items:
        return None
    query = parse_qs(urlsplit(url).query)
    return {
        "doubanSubjectSuggest": True,
        "query": (query.get("q") or query.get("search_text") or [""])[0],
        "count": len(items),
        "items": items,
    }


def _douban_info_block(html_text: str) -> str | None:
    match = re.search(
        r'<div id="info"[^>]*>(?P<body>.*?)</div>', html_text, flags=re.S | re.I
    )
    return match.group("body") if match else None


def _douban_info_field(info_html: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        match = re.search(
            rf'<span class="pl">\s*{re.escape(label)}\s*:?\s*</span>\s*(?P<value>.*?)(?:<br\s*/?>|</span>\s*<br\s*/?>)',
            info_html,
            flags=re.S | re.I,
        )
        if match:
            value = _compact_api_text(_strip_html(match.group("value")), max_length=260)
            return re.sub(r"^[：:\s]+", "", value) if value else None
        inline_match = re.search(
            rf'<span class="pl">\s*{re.escape(label)}\s*:?\s*(?P<value>.*?)</span>',
            info_html,
            flags=re.S | re.I,
        )
        if inline_match:
            value = _compact_api_text(
                _strip_html(inline_match.group("value")), max_length=260
            )
            value = re.sub(r"^[：:\s]+", "", value) if value else None
            if value:
                return value
    return None


def _compact_douban_subject_detail_data(url: str, data: Any) -> dict[str, Any] | None:
    if not _is_douban_url(url) or not re.search(r"/subject/\d+/?$", urlsplit(url).path):
        return None
    if not isinstance(data, str) or not _looks_like_html(data):
        return None
    compact: dict[str, Any] = {"doubanSubjectDetail": True, "url": url}
    id_match = re.search(r"/subject/(\d+)", urlsplit(url).path)
    if id_match:
        compact["id"] = id_match.group(1)
    title_match = re.search(
        r"<h1[^>]*>.*?<span[^>]*>(?P<title>.*?)</span>.*?</h1>", data, flags=re.S | re.I
    )
    if not title_match:
        title_match = _HTML_TITLE_RE.search(data)
    if title_match:
        compact["title"] = _compact_api_text(
            _strip_html(title_match.group("title")), max_length=220
        )
    info_html = _douban_info_block(data)
    if info_html:
        for target, labels in (
            ("author", ("作者", "表演者", "导演")),
            ("publisher", ("出版社", "出版者", "唱片公司")),
            ("publishYear", ("出版年", "发行时间", "上映日期")),
            ("isbn", ("ISBN", "条形码")),
            ("pages", ("页数",)),
            ("genre", ("流派", "类型")),
        ):
            value = _douban_info_field(info_html, labels)
            if value:
                compact[target] = value
    rating_match = re.search(
        r'<strong[^>]+class="[^"]*rating_num[^"]*"[^>]*>\s*(?P<rating>[^<]+)',
        data,
        flags=re.S | re.I,
    )
    if rating_match:
        compact["rating"] = _compact_api_text(
            rating_match.group("rating"), max_length=40
        )
    votes_match = re.search(
        r'property=["\']v:votes["\'][^>]*>\s*(?P<votes>[\d,]+)\s*</span>',
        data,
        flags=re.S | re.I,
    )
    if not votes_match:
        votes_match = re.search(r"(?P<votes>[\d,]+)\s*人评价", data)
    if votes_match:
        compact["ratingCount"] = votes_match.group("votes").replace(",", "")
    return compact if len(compact) > 2 else None


def _compact_douban_review_items(html_text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    blocks = re.split(r"<div data-cid=", html_text)
    for raw_block in blocks[1:]:
        block = "<div data-cid=" + raw_block
        item: dict[str, Any] = {}
        id_match = re.search(r'data-cid=["\']?(\d+)', block)
        if id_match:
            item["id"] = id_match.group(1)
        name_match = re.search(
            r'<a[^>]+class="name"[^>]*>(?P<name>.*?)</a>', block, flags=re.S | re.I
        )
        if name_match:
            item["user"] = _compact_api_text(
                _strip_html(name_match.group("name")), max_length=120
            )
        title_match = re.search(
            r'<h2>\s*<a[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>',
            block,
            flags=re.S | re.I,
        )
        if title_match:
            item["title"] = _compact_api_text(
                _strip_html(title_match.group("title")), max_length=220
            )
            item["url"] = html.unescape(title_match.group("url"))
        rating_match = re.search(
            r'class="allstar(?P<stars>\d+)[^"]*main-title-rating"[^>]+title="(?P<title>[^"]*)"',
            block,
        )
        if rating_match:
            item["ratingTitle"] = _compact_api_text(
                rating_match.group("title"), max_length=40
            )
        date_match = re.search(
            r'class="main-meta"[^>]*>\s*(?P<date>[^<]+)', block, flags=re.S | re.I
        )
        if date_match:
            item["date"] = _compact_api_text(date_match.group("date"), max_length=40)
        content_match = re.search(
            r'<div class="short-content">\s*(?P<content>.*?)\s*(?:&nbsp;|\n\s*</div>)',
            block,
            flags=re.S | re.I,
        )
        if content_match:
            item["text"] = _compact_api_text(
                _strip_html(content_match.group("content")), max_length=420
            )
        up_match = re.search(
            r'class="action-btn up"[^>]*>\s*(?P<votes>\d+)', block, flags=re.S | re.I
        )
        if up_match:
            item["usefulCount"] = up_match.group("votes")
        if item:
            items.append(item)
        if len(items) >= 20:
            break
    return items


def _compact_douban_comment_items(html_text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    blocks = re.split(r'<li class="comment-item"', html_text)
    for raw_block in blocks[1:]:
        block = '<li class="comment-item"' + raw_block
        item: dict[str, Any] = {}
        name_match = re.search(
            r'<span class="comment-info">.*?<a[^>]*>(?P<name>.*?)</a>',
            block,
            flags=re.S | re.I,
        )
        if name_match:
            item["user"] = _compact_api_text(
                _strip_html(name_match.group("name")), max_length=120
            )
        rating_match = re.search(
            r'<span class="user-stars allstar(?P<stars>\d+)[^"]*" title="(?P<title>[^"]*)"',
            block,
        )
        if rating_match:
            item["ratingTitle"] = _compact_api_text(
                rating_match.group("title"), max_length=40
            )
        date_match = re.search(
            r'<span class="comment-time[^"]*"[^>]*title="(?P<date>[^"]+)"',
            block,
            flags=re.S | re.I,
        )
        if date_match:
            item["date"] = _compact_api_text(date_match.group("date"), max_length=40)
        content_match = re.search(
            r'<span class="short">\s*(?P<content>.*?)</span>', block, flags=re.S | re.I
        )
        if content_match:
            item["text"] = _compact_api_text(
                _strip_html(content_match.group("content")), max_length=360
            )
        vote_match = re.search(
            r'<span class="votes vote-count">\s*(?P<votes>\d+)',
            block,
            flags=re.S | re.I,
        )
        if vote_match:
            item["voteCount"] = vote_match.group("votes")
        if item:
            items.append(item)
        if len(items) >= 30:
            break
    return items


def _compact_douban_review_data(url: str, data: Any) -> dict[str, Any] | None:
    if (
        not _is_douban_url(url)
        or not isinstance(data, str)
        or not _looks_like_html(data)
    ):
        return None
    path = urlsplit(url).path
    if "/reviews" in path:
        items = _compact_douban_review_items(data)
        kind = "review"
    elif "/comments/" in path:
        items = _compact_douban_comment_items(data)
        kind = "comment"
    else:
        return None
    if not items:
        return None
    query = parse_qs(urlsplit(url).query)
    return {
        "doubanReviewList": True,
        "kind": kind,
        "start": (query.get("start") or ["0"])[0],
        "count": len(items),
        "items": items,
        "url": url,
    }


def _summarize_douban_data(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    if data.get("doubanSubjectSuggest"):
        chunks = ["douban_subject_suggest"]
        if data.get("query"):
            chunks.append(f"query={data['query']}")
        items = []
        for index, item in enumerate(data.get("items") or [], 1):
            if not isinstance(item, dict):
                continue
            parts = [f"#{index}"]
            for label, key in (
                ("id", "id"),
                ("type", "type"),
                ("title", "title"),
                ("author", "author"),
                ("year", "year"),
                ("url", "url"),
            ):
                value = item.get(key)
                if value not in (None, ""):
                    parts.append(f"{label}={value}")
            items.append("; ".join(parts))
            if len(items) >= 8:
                break
        if items:
            chunks.append("items: " + " | ".join(items))
        return "; ".join(chunks)[:4000]
    if data.get("doubanSubjectDetail"):
        chunks = ["douban_subject_detail"]
        for label, key in (
            ("id", "id"),
            ("title", "title"),
            ("author", "author"),
            ("publisher", "publisher"),
            ("publishYear", "publishYear"),
            ("rating", "rating"),
            ("ratingCount", "ratingCount"),
            ("isbn", "isbn"),
        ):
            value = data.get(key)
            if value not in (None, ""):
                chunks.append(f"{label}={value}")
        return "; ".join(chunks)[:4000]
    if data.get("doubanReviewList"):
        chunks = [f"douban_{data.get('kind') or 'review'}_list"]
        for label, key in (("start", "start"), ("count", "count")):
            value = data.get(key)
            if value not in (None, ""):
                chunks.append(f"{label}={value}")
        items = []
        for index, item in enumerate(data.get("items") or [], 1):
            if not isinstance(item, dict):
                continue
            parts = [f"#{index}"]
            for label, key in (
                ("user", "user"),
                ("title", "title"),
                ("rating", "ratingTitle"),
                ("votes", "usefulCount"),
                ("text", "text"),
            ):
                value = item.get(key)
                if value not in (None, ""):
                    parts.append(f"{label}={value}")
            items.append("; ".join(parts))
            if len(items) >= 10:
                break
        if items:
            chunks.append("items: " + " | ".join(items))
        return "; ".join(chunks)[:4000]
    return None


def _archive_scalar(value: Any) -> Any:
    if isinstance(value, list):
        for item in value:
            if item not in (None, ""):
                return item
        return None
    if isinstance(value, dict):
        return None
    return value


def _archive_first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = _archive_scalar(mapping.get(key))
        if value not in (None, ""):
            return value
    return None


def _archive_duration_from_files(files: Any) -> tuple[str | None, list[dict[str, Any]]]:
    if not isinstance(files, list):
        return None, []
    file_summaries: list[dict[str, Any]] = []
    best_seconds: float | None = None
    for item in files:
        if not isinstance(item, dict):
            continue
        name = _archive_scalar(item.get("name"))
        format_name = _archive_scalar(item.get("format"))
        length = _archive_scalar(
            item.get("length") or item.get("runtime") or item.get("duration")
        )
        summary: dict[str, Any] = {}
        if name:
            summary["name"] = name
        if format_name:
            summary["format"] = format_name
        if length:
            summary["length_seconds"] = length
        if summary and len(file_summaries) < 8:
            file_summaries.append(summary)
        try:
            seconds = float(length)
        except (TypeError, ValueError):
            continue
        if seconds > 0 and (best_seconds is None or seconds > best_seconds):
            best_seconds = seconds
    if best_seconds is None:
        return None, file_summaries
    minutes, seconds = divmod(int(round(best_seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        duration = f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        duration = f"{minutes}:{seconds:02d}"
    return duration, file_summaries


def _compact_archive_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact Archive.org search/metadata responses into stable task state."""

    parsed_url = urlsplit(url)
    if parsed_url.hostname not in {"archive.org", "www.archive.org"} or not isinstance(
        data, dict
    ):
        return None
    if parsed_url.path.endswith("/advancedsearch.php"):
        response = data.get("response")
        docs = response.get("docs") if isinstance(response, dict) else None
        if not isinstance(docs, list):
            return None
        items: list[dict[str, Any]] = []
        for doc in docs[:20]:
            if not isinstance(doc, dict):
                continue
            identifier = _archive_first_present(doc, ("identifier",))
            item: dict[str, Any] = {}
            if identifier:
                item["identifier"] = identifier
                item["url"] = f"https://archive.org/details/{identifier}"
                item["metadata_url"] = f"https://archive.org/metadata/{identifier}"
            title = _archive_first_present(doc, ("title",))
            if title:
                item["title"] = title
            date = _archive_first_present(
                doc, ("date", "publicdate", "addeddate", "year")
            )
            if date:
                item["upload_date"] = date
            views = _archive_first_present(doc, ("downloads", "views", "item_views"))
            if views is not None:
                item["views_or_downloads"] = views
            duration = _archive_first_present(doc, ("runtime", "length", "duration"))
            if duration:
                item["duration"] = duration
            collection = doc.get("collection")
            if isinstance(collection, list):
                item["collection"] = [str(value) for value in collection[:5]]
            elif collection:
                item["collection"] = collection
            if item:
                items.append(item)
        result: dict[str, Any] = {"archiveAdvancedSearch": True, "items": items}
        if isinstance(response, dict):
            num_found = _archive_first_present(response, ("numFound", "total"))
            if num_found is not None:
                result["numFound"] = num_found
        return result
    if parsed_url.path.startswith("/metadata/"):
        raw_metadata = data.get("metadata")
        metadata: dict[str, Any] = (
            raw_metadata if isinstance(raw_metadata, dict) else {}
        )
        files = data.get("files")
        duration, file_summaries = _archive_duration_from_files(files)
        identifier = (
            _archive_first_present(metadata, ("identifier",))
            or parsed_url.path.rsplit("/", 1)[-1]
        )
        result = {
            "archiveMetadata": True,
            "identifier": identifier,
            "url": f"https://archive.org/details/{identifier}",
        }
        title = _archive_first_present(metadata, ("title",))
        if title:
            result["title"] = title
        upload_date = _archive_first_present(
            metadata, ("date", "publicdate", "addeddate", "year")
        )
        if upload_date:
            result["upload_date"] = upload_date
        views = _archive_first_present(metadata, ("downloads", "views", "item_views"))
        if views is not None:
            result["views_or_downloads"] = views
        runtime = _archive_first_present(metadata, ("runtime", "length", "duration"))
        if runtime:
            result["duration"] = runtime
        elif duration:
            result["duration"] = duration
        creator = _archive_first_present(metadata, ("creator", "publisher"))
        if creator:
            result["creator"] = creator
        mediatype = _archive_first_present(metadata, ("mediatype",))
        if mediatype:
            result["mediatype"] = mediatype
        if file_summaries:
            result["files"] = file_summaries
        return result
    return None


def _summarize_archive_data(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    if data.get("archiveMetadata"):
        chunks = ["archive_metadata"]
        for label, key in (
            ("id", "identifier"),
            ("title", "title"),
            ("upload_date", "upload_date"),
            ("views", "views_or_downloads"),
            ("duration", "duration"),
            ("url", "url"),
        ):
            value = data.get(key)
            if value not in (None, ""):
                chunks.append(f"{label}={value}")
        return "; ".join(chunks)
    if data.get("archiveAdvancedSearch") and isinstance(data.get("items"), list):
        chunks = ["archive_search"]
        if data.get("numFound") is not None:
            chunks.append(f"count={data['numFound']}")
        items = []
        for index, item in enumerate(data["items"][:10], 1):
            if not isinstance(item, dict):
                continue
            item_chunks = [f"#{index}"]
            for label, key in (
                ("id", "identifier"),
                ("title", "title"),
                ("date", "upload_date"),
                ("views", "views_or_downloads"),
                ("duration", "duration"),
                ("url", "url"),
                ("metadata", "metadata_url"),
            ):
                value = item.get(key)
                if value not in (None, ""):
                    item_chunks.append(f"{label}={value}")
            items.append("; ".join(item_chunks))
        if items:
            chunks.append("items: " + " | ".join(items))
        return " ; ".join(chunks)[:4000]
        return None


def _sina_number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace("%", "").strip())
    except ValueError:
        return None


def _sina_quote_field(parts: list[str], index: int) -> str | None:
    if index >= len(parts):
        return None
    value = parts[index].strip()
    return value or None


def _compact_sina_hq_data(url: str, data: Any) -> dict[str, Any] | None:
    """Parse Sina Finance quote JavaScript into stable quote objects."""

    hostname = urlsplit(url).hostname or ""
    if not hostname.endswith("sinajs.cn") or not isinstance(data, str):
        return None

    quotes: list[dict[str, Any]] = []
    for match in _SINA_HQ_RE.finditer(data):
        symbol = match.group("symbol")
        parts = [part.strip() for part in match.group("payload").split(",")]
        if not symbol or not parts:
            continue

        quote: dict[str, Any] = {"symbol": symbol}
        if symbol.startswith("hf_"):
            name = _sina_quote_field(parts, len(parts) - 1)
            price = _sina_quote_field(parts, 0)
            previous = _sina_quote_field(parts, 7)
            quote.update(
                {
                    "name": name,
                    "price": price,
                    "time": _sina_quote_field(parts, 6),
                    "date": _sina_quote_field(parts, 12),
                    "previous_close": previous,
                }
            )
            price_num = _sina_number(price)
            previous_num = _sina_number(previous)
            if price_num is not None and previous_num:
                quote["change"] = round(price_num - previous_num, 4)
                quote["change_pct"] = (
                    f"{((price_num - previous_num) / previous_num) * 100:.2f}%"
                )
        elif symbol.startswith("SGE_"):
            quote.update(
                {
                    "name": _sina_quote_field(parts, 2) or _sina_quote_field(parts, 1),
                    "price": _sina_quote_field(parts, 3),
                    "high": _sina_quote_field(parts, 6),
                    "low": _sina_quote_field(parts, 8),
                    "time": _sina_quote_field(parts, 16),
                    "change_pct": _sina_quote_field(parts, 17),
                }
            )
        elif symbol.startswith("fx_"):
            quote.update(
                {
                    "time": _sina_quote_field(parts, 0),
                    "price": _sina_quote_field(parts, 1),
                    "bid": _sina_quote_field(parts, 2),
                    "ask": _sina_quote_field(parts, 3),
                    "open": _sina_quote_field(parts, 5),
                    "high": _sina_quote_field(parts, 6),
                    "low": _sina_quote_field(parts, 7),
                    "previous_close": _sina_quote_field(parts, 8),
                    "name": _sina_quote_field(parts, 9),
                    "change": _sina_quote_field(parts, 10),
                    "change_pct": _sina_quote_field(parts, 11),
                    "amplitude_pct": _sina_quote_field(parts, 12),
                    "date": _sina_quote_field(parts, len(parts) - 1),
                }
            )
        else:
            quote["fields"] = parts[:20]

        quote = {key: value for key, value in quote.items() if value not in (None, "")}
        quotes.append(quote)

    if not quotes:
        return None
    return {"sinaHq": True, "quotes": quotes}


def _compact_sina_forex_kline_data(url: str, data: Any) -> dict[str, Any] | None:
    """Parse Sina Finance forex daily K-line JavaScript into a one-week trend."""

    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    if (
        not hostname.endswith("finance.sina.com.cn")
        or "NewForexService.getDayKLine" not in url
        or not isinstance(data, str)
    ):
        return None

    query = parse_qs(parsed_url.query)
    symbol = (query.get("symbol") or [""])[0]
    match = _SINA_FOREX_KLINE_RE.search(data)
    if not symbol or not match:
        return None

    rows: list[dict[str, Any]] = []
    for raw_row in match.group("payload").split("|"):
        parts = [part.strip() for part in raw_row.split(",")]
        if len(parts) < 5 or not parts[0]:
            continue
        open_value = _sina_number(parts[1])
        low_value = _sina_number(parts[2])
        high_value = _sina_number(parts[3])
        close_value = _sina_number(parts[4])
        if close_value is None:
            continue
        row: dict[str, Any] = {
            "date": parts[0],
            "open": open_value,
            "low": low_value,
            "high": high_value,
            "close": close_value,
        }
        if len(parts) > 5 and parts[5]:
            row["volume"] = _sina_number(parts[5])
        rows.append({key: value for key, value in row.items() if value is not None})

    if not rows:
        return None

    window = rows[-7:]
    first = window[0]
    last = window[-1]
    first_close = _sina_number(first.get("close"))
    last_close = _sina_number(last.get("close"))
    trend: dict[str, Any] = {
        "basis": "last_7_trading_days_close",
        "fromDate": first.get("date"),
        "toDate": last.get("date"),
        "firstClose": first.get("close"),
        "lastClose": last.get("close"),
    }
    if first_close is not None and last_close is not None:
        change = last_close - first_close
        trend["change"] = round(change, 6)
        if first_close:
            trend["changePct"] = f"{(change / first_close) * 100:.3f}%"
        if abs(change) < 0.0001:
            trend["direction"] = "flat"
        elif change > 0:
            trend["direction"] = "up"
        else:
            trend["direction"] = "down"

    return {
        "sinaForexKline": True,
        "symbol": symbol,
        "dayCount": len(rows),
        "latest": last,
        "lastSevenTradingDays": window,
        "trend": trend,
    }


def _sina_gold_analysis_stance(title: str) -> str | None:
    if re.search(r"偏弱|下跌|看跌|打压|回落|走低|瀑布|利空|承压|空头", title):
        return "bearish"
    if re.search(r"看涨|上涨|上行|反弹|走高|利好|新高|强势|多头", title):
        return "bullish"
    if re.search(r"震荡|多空|整理|区间|观望", title):
        return "neutral"
    return None


def _compact_sina_gold_analysis_data(url: str, data: Any) -> dict[str, Any] | None:
    """Extract compact analyst-outlook links from Sina Finance gold-analysis pages."""

    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    if not hostname.endswith("sina.com.cn") or not isinstance(data, str):
        return None
    is_gold_roll = parsed_url.path == "/roll/c/57085.shtml"
    if (
        not is_gold_roll
        and "cid=57085" not in parsed_url.query
        and "gold" not in parsed_url.path
        and "gjspl" not in parsed_url.path
    ):
        return None

    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in _extract_html_links(data, url):
        title = link.get("title", "")
        if not re.search(
            r"黄金|金价|现货金|贵金属|金银|张尧浠|黄力晨|刘智辛|美联储", title
        ):
            continue
        key = title
        if key in seen:
            continue
        seen.add(key)
        item: dict[str, str] = {
            "title": title,
            "url": link.get("url", ""),
        }
        stance = _sina_gold_analysis_stance(title)
        if stance:
            item["stance"] = stance
        items.append({key: value for key, value in item.items() if value})
        if len(items) >= 12:
            break

    if not items:
        return None
    return {"sinaGoldAnalysis": True, "items": items}


def _compact_you163_detail_data(url: str, data: Any) -> dict[str, Any] | None:
    """Parse NetEase Yanxuan detail HTML into product fields."""

    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    if not hostname.endswith("you.163.com") or parsed_url.path != "/item/detail":
        return None
    if isinstance(data, dict):
        payload = data
    elif isinstance(data, str):
        payload = _extract_you163_json_data(data)
    else:
        return None
    if not payload:
        return None
    item = payload.get("item")
    if not isinstance(item, dict):
        return None

    attrs: dict[str, Any] = {}
    for attr in item.get("attrList") or []:
        if (
            isinstance(attr, dict)
            and attr.get("attrName")
            and attr.get("attrValue") not in (None, "")
        ):
            attrs[str(attr["attrName"])] = attr["attrValue"]

    specs: list[dict[str, Any]] = []
    for spec in item.get("skuSpecList") or []:
        if not isinstance(spec, dict):
            continue
        values = []
        for raw_value in spec.get("skuSpecValueList") or []:
            if isinstance(raw_value, dict) and raw_value.get("value") not in (None, ""):
                values.append(str(raw_value["value"]))
        if spec.get("name") and values:
            specs.append({"name": spec["name"], "values": values[:10]})

    sku_samples: list[dict[str, Any]] = []
    for sku in item.get("skuList") or []:
        if not isinstance(sku, dict):
            continue
        spec_list = []
        for spec in sku.get("specList") or []:
            if (
                isinstance(spec, dict)
                and spec.get("specName")
                and spec.get("specValue") not in (None, "")
            ):
                spec_list.append(f"{spec['specName']}={spec['specValue']}")
        sku_samples.append(
            {
                "id": sku.get("id"),
                "retailPrice": sku.get("retailPrice"),
                "sellVolume": sku.get("sellVolume") or sku.get("noActivitySellVolume"),
                "specs": spec_list,
            }
        )
        if len(sku_samples) >= 8:
            break

    name = item.get("name") or ""
    simple_desc = item.get("simpleDesc") or ""
    thread_source = " ".join(
        str(part) for part in [name, simple_desc, *attrs.values()] if part
    )
    thread_match = re.search(r"\d+\s*[sS](?![A-Za-z0-9])|\d+\s*支", thread_source)

    result: dict[str, Any] = {
        "you163ProductDetail": True,
        "url": url,
        "id": item.get("id"),
        "name": name,
    }
    for key in ("retailPrice", "counterPrice", "sellVolume"):
        if item.get(key) not in (None, ""):
            result[key] = item[key]
    if payload.get("commentCount") not in (None, ""):
        result["commentCount"] = payload["commentCount"]
    if payload.get("commentGoodRates") not in (None, ""):
        result["commentGoodRates"] = payload["commentGoodRates"]
    if simple_desc:
        result["simpleDesc"] = simple_desc
    if attrs:
        result["attrs"] = attrs
        if attrs.get("面料"):
            result["material"] = attrs["面料"]
    if thread_match:
        result["thread_count"] = thread_match.group(0).replace(" ", "")
    if specs:
        result["specs"] = specs
    if sku_samples:
        result["sku_samples"] = sku_samples
    return result


def _compact_you163_search_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact NetEase Yanxuan search API results into ranked product candidates."""

    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    if not hostname.endswith("you.163.com") or parsed_url.path not in {
        "/xhr/search/search",
        "/xhr/search/search.json",
    }:
        return None
    if not isinstance(data, dict):
        return None
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    directly = payload.get("directly") if isinstance(payload, dict) else None
    if not isinstance(directly, dict):
        return None
    searcher_result = directly.get("searcherResult")
    if not isinstance(searcher_result, dict):
        return None
    raw_items = searcher_result.get("result")
    if not isinstance(raw_items, list):
        return None

    items: list[dict[str, Any]] = []
    for raw_item in raw_items[:80]:
        if not isinstance(raw_item, dict):
            continue
        attrs: dict[str, Any] = {}
        for attr in raw_item.get("attrList") or []:
            if (
                isinstance(attr, dict)
                and attr.get("attrName")
                and attr.get("attrValue") not in (None, "")
            ):
                attrs[str(attr["attrName"])] = attr["attrValue"]
        specs: list[dict[str, Any]] = []
        for spec in raw_item.get("skuSpecList") or []:
            if not isinstance(spec, dict):
                continue
            values = [
                str(value["value"])
                for value in spec.get("skuSpecValueList") or []
                if isinstance(value, dict) and value.get("value") not in (None, "")
            ]
            if spec.get("name") and values:
                specs.append({"name": spec["name"], "values": values[:8]})
        name = raw_item.get("name") or ""
        simple_desc = raw_item.get("simpleDesc") or ""
        thread_source = " ".join(
            str(part) for part in [name, simple_desc, *attrs.values()] if part
        )
        thread_match = re.search(r"\d+\s*[sS](?![A-Za-z0-9])|\d+\s*支", thread_source)
        item = {
            "id": raw_item.get("id"),
            "name": name,
            "retailPrice": raw_item.get("retailPrice"),
            "counterPrice": raw_item.get("counterPrice"),
            "sellVolume": raw_item.get("sellVolume"),
            "simpleDesc": simple_desc,
            "detail_url": f"https://you.163.com/item/detail?id={raw_item.get('id')}",
        }
        if attrs:
            item["attrs"] = attrs
            if attrs.get("面料"):
                item["material"] = attrs["面料"]
            elif attrs.get("材质"):
                item["material"] = attrs["材质"]
        if thread_match:
            item["thread_count"] = thread_match.group(0).replace(" ", "")
        if specs:
            item["specs"] = specs
        items.append(
            {key: value for key, value in item.items() if value not in (None, "")}
        )

    raw_pagination = searcher_result.get("pagination")
    pagination: dict[str, Any] = (
        raw_pagination if isinstance(raw_pagination, dict) else {}
    )
    result: dict[str, Any] = {
        "you163SearchResult": True,
        "url": url,
        "total": pagination.get("total"),
        "page": pagination.get("page"),
        "totalPage": pagination.get("totalPage"),
        "items": items,
    }
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def _compact_you163_comment_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact NetEase Yanxuan comment APIs into rating and keyword evidence."""

    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    if not hostname.endswith("you.163.com") or not parsed_url.path.startswith(
        "/xhr/comment/"
    ):
        return None
    if not isinstance(data, dict):
        return None
    payload = data.get("data") if isinstance(data.get("data"), (dict, list)) else data
    result: dict[str, Any] = {"you163CommentData": True, "url": url}
    item_id_match = re.search(r"(?:^|[?&])itemId=(\d+)", parsed_url.query)
    if item_id_match:
        result["itemId"] = item_id_match.group(1)

    if parsed_url.path.rstrip("/").endswith(
        ("itemGoodRates", "itemGoodRates.json")
    ) and isinstance(payload, dict):
        for source_key, target_key in (
            ("goodCmtRate", "goodRate"),
            ("star", "star"),
            ("defGoodCmtCnt", "defaultGoodCount"),
        ):
            value = payload.get(source_key)
            if value not in (None, ""):
                result[target_key] = value
        return result if len(result) > 2 else None

    if parsed_url.path.rstrip("/").endswith(("tags", "tags.json")) and isinstance(
        payload, list
    ):
        tags = []
        for tag in payload[:10]:
            if isinstance(tag, dict) and tag.get("name"):
                chunk = {"name": tag.get("name")}
                if tag.get("strCount") not in (None, ""):
                    chunk["count"] = tag.get("strCount")
                tags.append(chunk)
        if tags:
            result["tags"] = tags
        return result if len(result) > 2 else None

    if not isinstance(payload, dict):
        return None
    raw_comments = payload.get("result")
    if raw_comments is None:
        raw_comments = payload.get("commentList")
    if not isinstance(raw_comments, list):
        return None
    pagination = (
        payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
    )
    comments: list[dict[str, Any]] = []
    stars: list[float] = []
    keyword_candidates: list[str] = []
    for raw_comment in raw_comments[:12]:
        if not isinstance(raw_comment, dict):
            continue
        comment: dict[str, Any] = {}
        if raw_comment.get("star") not in (None, ""):
            comment["star"] = raw_comment["star"]
            try:
                stars.append(float(raw_comment["star"]))
            except (TypeError, ValueError):
                pass
        content = _compact_api_text(
            str(raw_comment.get("content") or ""), max_length=180
        )
        if content:
            comment["content"] = content
            for part in re.split(r"[\s,，。；;、：:\n]+", content):
                part = part.strip()
                if 2 <= len(part) <= 12 and not re.search(r"^\d+$", part):
                    keyword_candidates.append(part)
        sku_info = raw_comment.get("skuInfo")
        if isinstance(sku_info, list) and sku_info:
            comment["skuInfo"] = [str(value) for value in sku_info[:4]]
        if comment:
            comments.append(comment)
    if pagination:
        result["total"] = pagination.get("total")
        result["page"] = pagination.get("page")
    if stars:
        result["avgStar"] = round(sum(stars) / len(stars), 2)
    if comments:
        result["comments"] = comments
    if keyword_candidates:
        seen: set[str] = set()
        keywords: list[str] = []
        for keyword in keyword_candidates:
            if keyword in seen:
                continue
            seen.add(keyword)
            keywords.append(keyword)
            if len(keywords) >= 12:
                break
        result["keywords"] = keywords
    return result if len(result) > 2 else None


def _dangdang_match_text(
    pattern: str, data: str, *, max_length: int = 240
) -> str | None:
    match = re.search(pattern, data, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return _compact_api_text(_strip_html(match.group(1)), max_length=max_length)


def _dangdang_clean_labeled_text(value: str | None, *labels: str) -> str | None:
    if not value:
        return None
    label_pattern = "|".join(re.escape(label) for label in labels)
    cleaned = re.sub(rf"^(?:{label_pattern})\s*[:：]\s*", "", value).strip()
    return cleaned or None


def _dangdang_percent(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("%"):
        return text
    try:
        number = float(text)
    except ValueError:
        return text
    if 0 <= number <= 1:
        number *= 100
    if number.is_integer():
        return f"{int(number)}%"
    return f"{number:g}%"


def _extract_dangdang_spu_info(value: str) -> dict[str, Any] | None:
    raw = _extract_js_object_assignment(value, "prodSpuInfo")
    if not raw:
        return None
    try:
        data = json.loads(html.unescape(raw))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _compact_dangdang_detail_data(url: str, data: Any) -> dict[str, Any] | None:
    """Parse Dangdang product detail HTML into compact product fields."""

    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    if not hostname.endswith("product.dangdang.com") or not re.search(
        r"/\d+\.html$", parsed_url.path
    ):
        return None
    if not isinstance(data, str):
        return None
    product_id_match = re.search(r"/(\d+)\.html$", parsed_url.path)
    spu_info = _extract_dangdang_spu_info(data) or {}
    product_id = spu_info.get("productId") or (
        product_id_match.group(1) if product_id_match else None
    )

    title = spu_info.get("productName")
    if not title:
        title = _dangdang_match_text(
            r'<h1\b[^>]*title=["\'](.*?)["\']', data, max_length=300
        )
    if not title:
        title = _dangdang_match_text(r"<title[^>]*>(.*?)</title>", data, max_length=300)
        if title:
            title = re.sub(r"_当当网\s*$", "", title).strip()
    author = _dangdang_clean_labeled_text(
        _dangdang_match_text(
            r'<span[^>]*\bid=["\']author["\'][^>]*>(.*?)</span>', data
        ),
        "作者",
    )
    publisher = _dangdang_clean_labeled_text(
        _dangdang_match_text(
            r'<span[^>]*dd_name=["\']出版社["\'][^>]*>(.*?)</span>', data
        ),
        "出版社",
    )
    publish_time = _dangdang_clean_labeled_text(
        _dangdang_match_text(
            r"<span[^>]*>\s*出版时间\s*[:：]\s*(.*?)</span>", data, max_length=80
        ),
        "出版时间",
    )
    if publish_time:
        publish_time = publish_time.replace("\xa0", " ").strip()
    age_source = " ".join(
        str(part)
        for part in (title, spu_info.get("productSubName"), spu_info.get("pathName"))
        if part
    )
    age_ranges = []
    for match in re.finditer(
        r"\d+\s*(?:[-~至到]|—|－)\s*\d+\s*岁|\d+\s*岁", age_source
    ):
        value = re.sub(r"\s+", "", match.group(0))
        if value not in age_ranges:
            age_ranges.append(value)
        if len(age_ranges) >= 5:
            break

    result: dict[str, Any] = {
        "dangdangProductDetail": True,
        "url": url,
        "id": product_id,
        "mainProductId": spu_info.get("mainProductId"),
        "title": title,
        "author": author,
        "publisher": publisher,
        "publishTime": publish_time,
        "categoryPath": spu_info.get("categoryPath"),
    }
    if age_ranges:
        result["ageHints"] = age_ranges
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def _compact_dangdang_comment_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact Dangdang comment/list API into rating evidence."""

    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    query = parse_qs(parsed_url.query)
    route = (query.get("r") or [""])[0]
    if (
        not hostname.endswith("product.dangdang.com")
        or parsed_url.path != "/index.php"
        or route != "comment/list"
    ):
        return None
    if not isinstance(data, dict):
        return None
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    comment_list = payload.get("list") if isinstance(payload, dict) else None
    summary = comment_list.get("summary") if isinstance(comment_list, dict) else None
    if not isinstance(summary, dict):
        return None
    product_id = (query.get("productId") or [None])[0]
    main_product_id = (query.get("mainProductId") or [None])[0]
    result: dict[str, Any] = {
        "dangdangCommentData": True,
        "url": url,
        "productId": product_id,
        "mainProductId": main_product_id or summary.get("main_product_id"),
        "goodRate": _dangdang_percent(
            summary.get("goodRate") or summary.get("favorable_rate")
        ),
        "totalCommentCount": summary.get("total_comment_num"),
        "goodCommentCount": summary.get("total_crazy_count"),
        "neutralCommentCount": summary.get("total_indifferent_count"),
        "badCommentCount": summary.get("total_detest_count"),
        "defaultGoodCount": summary.get("total_auto_count") or summary.get("autoCount"),
        "averageScore": summary.get("average_score"),
        "averageScoreWithoutDefault": summary.get("average_score_eliminate_default"),
    }
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def _is_academia_profile_works_api(url: str) -> bool:
    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    return (
        hostname == "api.academia.edu"
        and parsed_url.path.rstrip("/") == "/v0/profiles/works"
    )


def _is_academia_views_api(url: str) -> bool:
    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    return (
        hostname == "api.academia.edu"
        and parsed_url.path.rstrip("/") == "/v0/works/views"
    )


def _academia_extract_work_json(value: str) -> dict[str, Any] | None:
    marker = "workJSON:"
    start = value.find(marker)
    if start == -1:
        return None
    raw = html.unescape(value[start + len(marker) :]).lstrip()
    try:
        parsed, _ = json.JSONDecoder().raw_decode(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _academia_html_text(
    pattern: str, value: str, *, max_length: int = 240
) -> str | None:
    match = re.search(pattern, value, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return _compact_api_text(_strip_html(match.group(1)), max_length=max_length)


def _academia_publication_year(metadata: dict[str, Any], fragment: str) -> Any:
    raw_publication_date = metadata.get("publication_date")
    publication_date: dict[str, Any] = (
        raw_publication_date if isinstance(raw_publication_date, dict) else {}
    )
    year = publication_date.get("year")
    if year not in (None, ""):
        return year
    for key in ("year", "publication_year"):
        year = metadata.get(key)
        if year not in (None, ""):
            return year
    visible_text = _strip_html(fragment)
    match = re.search(r"\b(?:19|20)\d{2}\b", visible_text)
    return match.group(0) if match else None


def _compact_academia_profile_works_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact Academia profile works into paper metadata suitable for ranking."""

    if not _is_academia_profile_works_api(url) or not isinstance(data, dict):
        return None
    works = data.get("works")
    if not isinstance(works, list):
        return None
    query = parse_qs(urlsplit(url).query)
    items: list[dict[str, Any]] = []
    for raw_work in works[:60]:
        if not isinstance(raw_work, dict):
            continue
        fragment = raw_work.get("html")
        if not isinstance(fragment, str):
            continue
        raw_work_json = _academia_extract_work_json(fragment)
        work_json: dict[str, Any] = (
            raw_work_json if isinstance(raw_work_json, dict) else {}
        )
        raw_metadata = work_json.get("metadata")
        metadata: dict[str, Any] = (
            raw_metadata if isinstance(raw_metadata, dict) else {}
        )
        work_id = work_json.get("id")
        if work_id in (None, ""):
            id_match = re.search(r'data-work-id=["\'](\d+)["\']', fragment)
            if id_match:
                work_id = id_match.group(1)
        title = work_json.get("title") or _academia_html_text(
            r'<a\b[^>]*class=["\'][^"\']*\bjs-work-strip-work-link\b[^"\']*["\'][^>]*>(.*?)</a>',
            fragment,
            max_length=300,
        )
        abstract = (
            metadata.get("grobid_abstract")
            or metadata.get("abstract")
            or metadata.get("ai_abstract")
            or work_json.get("summary")
            or work_json.get("translated_abstract")
            or _academia_html_text(
                r'<span\b[^>]*class=["\'][^"\']*\bjs-work-more-abstract-untruncated\b[^"\']*["\'][^>]*>(.*?)</span>',
                fragment,
                max_length=700,
            )
        )
        item: dict[str, Any] = {
            "id": str(work_id) if work_id not in (None, "") else None,
            "title": _compact_api_text(title, max_length=300),
            "year": _academia_publication_year(metadata, fragment),
            "publication": _compact_api_text(
                metadata.get("publication_name") or metadata.get("publisher"),
                max_length=180,
            ),
            "abstract": _compact_api_text(abstract, max_length=700),
            "url": work_json.get("internal_url")
            or _first_present(work_json, ("translated_internal_url", "preview_url")),
            "section": raw_work.get("section_name"),
        }
        item = {
            key: value for key, value in item.items() if value not in (None, "", [])
        }
        if item.get("id") or item.get("title"):
            items.append(item)
    if not items:
        return None
    result: dict[str, Any] = {
        "academiaProfileWorks": True,
        "url": url,
        "userId": (query.get("user_id") or [None])[0],
        "offset": (query.get("offset") or [None])[0],
        "perPage": (query.get("per_page") or [None])[0],
        "workIds": [item["id"] for item in items if item.get("id")],
        "items": items,
    }
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def _compact_academia_views_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact Academia view-count API into id/count pairs."""

    if not _is_academia_views_api(url) or not isinstance(data, dict):
        return None
    views = data.get("views")
    if not isinstance(views, dict):
        return None
    items = []
    for work_id, view_count in views.items():
        if view_count in (None, ""):
            continue
        items.append({"id": str(work_id), "views": view_count})

    def view_sort_key(item: dict[str, Any]) -> float:
        try:
            views = item.get("views")
            return float(views) if views not in (None, "") else -1
        except (TypeError, ValueError):
            return -1

    items.sort(key=view_sort_key, reverse=True)
    if not items:
        return None
    return {"academiaWorkViews": True, "url": url, "items": items}


def _academia_views_url(work_ids: list[Any]) -> str | None:
    ids = [str(work_id) for work_id in work_ids if work_id not in (None, "")]
    if not ids:
        return None
    query = "&".join(f"work_ids[]={quote(work_id)}" for work_id in ids[:60])
    return f"https://api.academia.edu/v0/works/views?{query}"


def _merge_academia_view_counts(
    profile_data: dict[str, Any], views_data: dict[str, Any]
) -> None:
    if not profile_data.get("academiaProfileWorks") or not isinstance(
        profile_data.get("items"), list
    ):
        return
    if not views_data.get("academiaWorkViews") or not isinstance(
        views_data.get("items"), list
    ):
        return
    view_counts = {
        str(item.get("id")): item.get("views")
        for item in views_data["items"]
        if isinstance(item, dict)
        and item.get("id") not in (None, "")
        and item.get("views") not in (None, "")
    }
    if not view_counts:
        return
    for item in profile_data["items"]:
        if isinstance(item, dict) and str(item.get("id")) in view_counts:
            item["views"] = view_counts[str(item.get("id"))]
    profile_data["viewsFetched"] = True
    profile_data["viewsUrl"] = views_data.get("url")


def _is_zalando_listing_url(url: str) -> bool:
    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    if not hostname.endswith("zalando.de"):
        return False
    if parsed_url.path.endswith(".html"):
        return False
    return bool(parsed_url.path.strip("/"))


def _is_zalando_product_detail_url(url: str) -> bool:
    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    return hostname.endswith("zalando.de") and parsed_url.path.endswith(".html")


def _zalando_hydration_payloads(value: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for match in re.finditer(r"window\.__hydrationDataConsume\(", value):
        raw = html.unescape(value[match.end() :]).lstrip()
        try:
            payload, _ = json.JSONDecoder().raw_decode(raw)
        except Exception:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    for match in re.finditer(
        r'<script[^>]+id=["\']re-concurrent-data-hydrate["\'][^>]*>(.*?)</script>',
        value,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        raw = html.unescape(match.group(1)).strip()
        prefix = "window.__hydrationDataConsume("
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
        try:
            payload, _ = json.JSONDecoder().raw_decode(raw)
        except Exception:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _zalando_graphql_entries(
    payload: dict[str, Any],
) -> list[tuple[dict[str, Any], Any]]:
    cache = (
        payload.get("graphqlCache")
        if isinstance(payload.get("graphqlCache"), dict)
        else payload
    )
    entries: list[tuple[dict[str, Any], Any]] = []
    if not isinstance(cache, dict):
        return entries
    for raw_key, value in cache.items():
        context: dict[str, Any] = {}
        if isinstance(raw_key, str):
            try:
                decoded_key = json.loads(raw_key)
            except Exception:
                decoded_key = None
            if isinstance(decoded_key, dict):
                raw_variables = decoded_key.get("variables")
                variables: dict[str, Any] = (
                    raw_variables if isinstance(raw_variables, dict) else {}
                )
                module_input = variables.get("moduleInput")
                context = {
                    "id": variables.get("id"),
                    "isRatingEnabled": variables.get("isRatingEnabled"),
                    "module": module_input.get("module")
                    if isinstance(module_input, dict)
                    else None,
                }
        entries.append((context, value))
    return entries


def _zalando_walk_products(
    value: Any, *, context: dict[str, Any], items: list[dict[str, Any]]
) -> None:
    if isinstance(value, dict):
        product = value.get("product")
        if isinstance(product, dict):
            _zalando_append_product(product, context=context, items=items)
        for nested in value.values():
            _zalando_walk_products(nested, context=context, items=items)
    elif isinstance(value, list):
        for nested in value[:120]:
            _zalando_walk_products(nested, context=context, items=items)


def _zalando_nested_texts(value: Any, *, limit: int = 80) -> list[str]:
    texts: list[str] = []

    def walk(node: Any) -> None:
        if len(texts) >= limit:
            return
        if isinstance(node, str):
            text = _compact_api_text(node, max_length=160)
            if text:
                texts.append(text)
            return
        if isinstance(node, dict):
            for nested in node.values():
                walk(nested)
        elif isinstance(node, list):
            for nested in node[:60]:
                walk(nested)

    walk(value)
    return texts


def _zalando_price_text(product: dict[str, Any]) -> str | None:
    for source in (
        product.get("displayPriceModule"),
        product.get("price"),
        product.get("priceRange"),
        product,
    ):
        for text in _zalando_nested_texts(source, limit=40):
            match = re.search(r"[€£$]\s?\d+(?:[.,]\d{2})?", text)
            if match:
                return match.group(0).replace(" ", "")
    return None


def _zalando_rating_text(
    product: dict[str, Any], context: dict[str, Any]
) -> str | None:
    for key in ("averageRating", "rating", "reviewRating", "reviewsAverage", "stars"):
        value = product.get(key)
        if isinstance(value, (int, float, str)) and str(value).strip():
            return str(value)
    review_summary = (
        product.get("reviewSummary")
        or product.get("reviewsSummary")
        or product.get("reviews")
    )
    if isinstance(review_summary, dict):
        for key in ("averageRating", "rating", "count", "total"):
            value = review_summary.get(key)
            if value not in (None, ""):
                return str(value)
    if context.get("isRatingEnabled") is False:
        return "not shown"
    return None


def _zalando_append_product(
    product: dict[str, Any], *, context: dict[str, Any], items: list[dict[str, Any]]
) -> None:
    sku = product.get("sku")
    if not sku and isinstance(product.get("id"), str) and "::" in product["id"]:
        sku = product["id"].rsplit("::", 1)[-1]
    if not sku:
        return
    name = _compact_api_text(product.get("name"), max_length=280)
    uri = product.get("uri")
    raw_brand_data = product.get("brand")
    brand_data: dict[str, Any] = (
        raw_brand_data if isinstance(raw_brand_data, dict) else {}
    )
    brand = _compact_api_text(
        brand_data.get("name") or product.get("brandName"), max_length=120
    )
    price = _zalando_price_text(product)
    sizes: list[str] = []
    for key in ("simples", "simplesWithStock"):
        raw_sizes = product.get(key)
        if not isinstance(raw_sizes, list):
            continue
        for simple in raw_sizes:
            if isinstance(simple, dict) and simple.get("size") not in (None, ""):
                size = str(simple["size"])
                if size not in sizes:
                    sizes.append(size)
    rating = _zalando_rating_text(product, context)
    raw_color_data = product.get("color")
    color_data: dict[str, Any] = (
        raw_color_data if isinstance(raw_color_data, dict) else {}
    )
    item = {
        "sku": str(sku),
        "name": name,
        "brand": brand,
        "price": price,
        "sizes": sizes,
        "rating": rating,
        "url": uri,
        "color": _compact_api_text(color_data.get("label"), max_length=80),
    }
    if not (name or brand or price or sizes or uri):
        return
    items.append(
        {key: value for key, value in item.items() if value not in (None, "", [])}
    )


def _compact_zalando_listing_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact Zalando listing hydration into ranked product cards."""

    if not _is_zalando_listing_url(url) or not isinstance(data, str):
        return None
    items: list[dict[str, Any]] = []
    for payload in _zalando_hydration_payloads(data):
        for context, value in _zalando_graphql_entries(payload):
            _zalando_walk_products(value, context=context, items=items)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        sku = str(item.get("sku") or "")
        if not sku or sku in seen:
            continue
        seen.add(sku)
        deduped.append(item)
        if len(deduped) >= 30:
            break
    if not deduped:
        return None
    text = _strip_html(data[:300000])
    total_match = re.search(r"\b(\d[\d,.]*)\s+items\b", text, flags=re.IGNORECASE)
    result: dict[str, Any] = {
        "zalandoListing": True,
        "url": url,
        "items": deduped,
    }
    if total_match:
        result["totalItems"] = total_match.group(1)
    return result


def _zalando_extract_detail_name(data: str) -> str | None:
    for pattern in (
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r"<title[^>]*>(.*?)</title>",
        r"<h1[^>]*>(.*?)</h1>",
    ):
        match = re.search(pattern, data, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        name = _compact_api_text(
            html.unescape(_strip_html(match.group(1))), max_length=220
        )
        if name:
            return name
    return None


def _zalando_extract_labeled_text(
    text: str, label: str, stop_labels: list[str]
) -> str | None:
    stop_pattern = "|".join(
        re.escape(stop_label) for stop_label in stop_labels if stop_label != label
    )
    pattern = rf"{re.escape(label)}\s*:?\s*(.+?)(?:\s+(?:{stop_pattern})\s*:|\s+Details\s*:|\s+Size\s+guide\b|$)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    value = _compact_api_text(match.group(1), max_length=220)
    if not value or value.lower() in {"not specified", "details"}:
        return None
    return value


def _compact_zalando_detail_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact Zalando product detail HTML into material and rating fields."""

    if not _is_zalando_product_detail_url(url) or not isinstance(data, str):
        return None
    text = _compact_api_text(_strip_html(data[:400000]), max_length=120000)
    if not text:
        return None
    material_labels = [
        "Outer fabric material",
        "Fabric",
        "Lining",
        "Padding type",
        "Care instructions",
        "Contains non-textile parts of animal origin",
    ]
    materials: dict[str, str] = {}
    for label in material_labels:
        value = _zalando_extract_labeled_text(text, label, material_labels)
        if value:
            materials[label] = value
    rating = None
    for pattern in (
        r"(\d(?:[.,]\d)?)\s*out of\s*5",
        r"Average rating\s*:?\s*(\d(?:[.,]\d)?)",
        r"(\d(?:[.,]\d)?)\s*/\s*5",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            rating = match.group(1).replace(",", ".")
            break
    if not materials and rating is None:
        return None
    result: dict[str, Any] = {
        "zalandoProductDetail": True,
        "url": url,
        "rating": rating or "not shown",
    }
    name = _zalando_extract_detail_name(data)
    if name:
        result["name"] = name
    if materials:
        result["materials"] = materials
    return result


def _compact_cqvip_search_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact CQVIP search JSON into ranked literature rows."""

    if not _is_cqvip_newsite_api(url) or not isinstance(data, dict):
        return None
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(payload, dict):
        return None
    rows = payload.get("rows") or payload.get("records")
    if not isinstance(rows, list):
        return None

    items: list[dict[str, Any]] = []
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        raw_journal = row.get("journalInfo")
        journal: dict[str, Any] = raw_journal if isinstance(raw_journal, dict) else {}
        authors: list[str] = []
        for author in row.get("authorInfo") or []:
            if isinstance(author, dict) and author.get("name"):
                authors.append(str(author["name"]))
            if len(authors) >= 8:
                break
        raw_range_codes = journal.get("range")
        range_codes = raw_range_codes if isinstance(raw_range_codes, list) else []
        raw_range_next = journal.get("rangeNext")
        range_next = raw_range_next if isinstance(raw_range_next, list) else []
        range_names = []
        raw_range_info = journal.get("rangeInfo")
        range_info = raw_range_info if isinstance(raw_range_info, list) else []
        for info in range_info:
            if not isinstance(info, dict):
                continue
            name = (
                info.get("abbrNameVersion")
                or info.get("abbrName")
                or info.get("fullNameVersion")
                or info.get("fullName")
            )
            if name:
                range_names.append(str(name))
            if len(range_names) >= 8:
                break
        is_core = (
            journal.get("isCore") == 1
            or "BDHX" in range_codes
            or any(str(code).startswith("BDHX") for code in range_next)
        )
        item = {
            "id": row.get("id"),
            "title": row.get("title"),
            "authors": authors,
            "journal": journal.get("name")
            or row.get("mediaName")
            or row.get("sourceName"),
            "year": row.get("year") or journal.get("year"),
            "pubDate": row.get("pubDate"),
            "byRefCnt": row.get("byRefCnt"),
            "isCore": is_core,
            "coreRanges": range_names,
        }
        items.append(
            {key: value for key, value in item.items() if value not in (None, "", [])}
        )

    result: dict[str, Any] = {
        "cqvipSearchResult": True,
        "url": url,
        "total": payload.get("total") or payload.get("count"),
        "items": items,
    }
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def _summarize_cqvip_search_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("cqvipSearchResult"):
        return None
    chunks = ["cqvip_search"]
    if data.get("total") not in (None, ""):
        chunks.append(f"total={data['total']}")
    items = data.get("items")
    if isinstance(items, list) and items:
        row_chunks = []
        for index, item in enumerate(items[:8], start=1):
            if not isinstance(item, dict):
                continue
            parts = [f"#{index}"]
            for label, key in (
                ("title", "title"),
                ("journal", "journal"),
                ("year", "year"),
                ("cited", "byRefCnt"),
                ("core", "isCore"),
            ):
                value = item.get(key)
                if value not in (None, ""):
                    parts.append(f"{label}={value}")
            authors = item.get("authors")
            if isinstance(authors, list) and authors:
                parts.append(
                    "authors=" + ",".join(str(author) for author in authors[:5])
                )
            row_chunks.append("; ".join(parts))
        if row_chunks:
            chunks.append("items=" + " | ".join(row_chunks))
    return "; ".join(chunks)[:4000]


def _docin_match_text(pattern: str, data: str, *, max_length: int = 240) -> str | None:
    match = re.search(pattern, data, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return _compact_api_text(_strip_html(match.group(1)), max_length=max_length)


def _docin_match_int(pattern: str, data: str) -> int | None:
    text = _docin_match_text(pattern, data, max_length=32)
    if not text:
        return None
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def _docin_attr(attrs: str, name: str) -> str | None:
    match = re.search(
        rf"\b{re.escape(name)}=[\"'](?P<value>.*?)[\"']",
        attrs,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    return html.unescape(match.group("value")).strip()


def _docin_infer_format(title: str | None, block: str) -> str | None:
    candidates = [title or "", block[:2000]]
    for source in candidates:
        match = re.search(
            r"\.(docx?|xlsx?|pptx?|pdf|txt|zip|rar)\b",
            _strip_html(source),
            flags=re.IGNORECASE,
        )
        if match:
            return f".{match.group(1).lower()}"
    return None


def _docin_extract_snippet(block: str) -> str | None:
    summary = _docin_match_text(
        r'<dd[^>]*class=["\'][^"\']*\bsummary\b[^"\']*["\'][^>]*>(.*?)</dd>',
        block,
        max_length=420,
    )
    if summary:
        return summary
    return _compact_api_text(_strip_html(block), max_length=260) or None


def _compact_docin_search_data(url: str, data: Any) -> dict[str, Any] | None:
    """Parse Docin search-result HTML into candidate document links."""

    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    if (
        not hostname.endswith("docin.com")
        or parsed_url.path != "/search.do"
        or not isinstance(data, str)
    ):
        return None

    query_params = parse_qs(parsed_url.query)
    query = query_params.get("nkey", [""])[0]
    result: dict[str, Any] = {
        "docinSearchResult": True,
        "url": url,
    }
    if query:
        result["query"] = query

    filters: dict[str, Any] = {}
    dt = query_params.get("dt", [""])[0]
    if dt == "3":
        filters["format"] = "ppt"
    elif dt:
        filters["dt"] = dt
    od = query_params.get("od", [""])[0]
    if od == "2":
        filters["sort"] = "most_read"
    elif od:
        filters["od"] = od
    numpage = query_params.get("numpage", [""])[0]
    if numpage == "2":
        filters["pageRangeHint"] = "9-100"
    elif numpage:
        filters["numpage"] = numpage
    year_type = query_params.get("yearType", [""])[0]
    if year_type == "1":
        filters["yearBucket"] = "current_calendar_year"
    elif year_type == "2":
        filters["yearBucket"] = "previous_calendar_year"
    elif year_type:
        filters["yearType"] = year_type
    if filters:
        result["filters"] = filters

    total = _docin_match_int(r"找到相关结果约?\s*([\d,]+)\s*个", data)
    if total is not None:
        result["totalCount"] = total

    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    anchor_re = re.compile(
        r"<a\b(?P<attrs>[^>]*)\bhref=[\"'](?P<href>/p-(?P<id>\d+)\.html)[\"'][^>]*>(?P<body>.*?)</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in anchor_re.finditer(data):
        doc_id = match.group("id")
        if doc_id in seen_ids:
            continue
        seen_ids.add(doc_id)

        block_start = data.rfind("<dl", 0, match.start())
        if block_start == -1:
            block_start = max(0, match.start() - 1200)
        block_end = data.find("</dl>", match.end())
        if block_end == -1:
            block_end = min(len(data), match.end() + 1800)
        else:
            block_end += len("</dl>")
        block = data[block_start:block_end]

        attrs = match.group("attrs")
        title = _docin_attr(attrs, "title") or _compact_api_text(
            _strip_html(match.group("body")), max_length=260
        )
        item: dict[str, Any] = {
            "rank": len(items) + 1,
            "id": doc_id,
            "url": urljoin(url, match.group("href")),
        }
        if title:
            item["title"] = title
        page_count = _docin_match_int(
            r'<span[^>]*class=["\'][^"\']*\bpageno\b[^"\']*["\'][^>]*>(.*?)</span>',
            block,
        )
        if page_count is not None:
            item["pageCount"] = page_count
        file_format = _docin_infer_format(title, block)
        if file_format:
            item["format"] = file_format
        upload_time = _docin_match_text(
            r"(20\d{2}-\d{1,2}-\d{1,2})", block, max_length=20
        )
        if upload_time:
            item["uploadTimeHint"] = upload_time
        snippet = _docin_extract_snippet(block)
        if snippet:
            item["snippet"] = snippet

        items.append(item)
        if len(items) >= 20:
            break

    if items:
        result["items"] = items
    return result if len(result) > 2 else None


def _compact_docin_detail_data(url: str, data: Any) -> dict[str, Any] | None:
    """Parse Docin document detail HTML without relying on the heavy reader DOM."""

    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    if (
        not hostname.endswith("docin.com")
        or not re.search(r"/p-\d+\.html$", parsed_url.path)
        or not isinstance(data, str)
    ):
        return None
    doc_id_match = re.search(r"/p-(\d+)\.html$", parsed_url.path)
    result: dict[str, Any] = {
        "docinDocumentDetail": True,
        "url": url,
    }
    if doc_id_match:
        result["id"] = doc_id_match.group(1)

    if "errornoAudit" in data or "审核中" in data:
        result["unavailable_reason"] = "审核中"

    title = _docin_match_text(
        r'<span[^>]*class=["\'][^"\']*\bdoc_title\b[^"\']*["\'][^>]*>(.*?)</span>', data
    )
    if not title:
        title = _docin_match_text(r"<title>(.*?)</title>", data)
        if title:
            title = re.sub(r"\s*-\s*豆丁网\s*$", "", title).strip()
    if title:
        result["title"] = title

    read_count = _docin_match_int(
        r'data-tips=["\']阅读["\'][^>]*>\s*<em>(.*?)</em>\s*阅读', data
    )
    if read_count is not None:
        result["readCount"] = read_count
    page_count = _docin_match_int(
        r"文档页数\s*[:：]?\s*</dt>\s*<dd[^>]*>\s*<span>(.*?)</span>\s*页", data
    )
    if page_count is None:
        page_count = _docin_match_int(
            r'<span[^>]*class=["\'][^"\']*info_txt[^"\']*["\'][^>]*>\s*<em>(.*?)</em>\s*页',
            data,
        )
    if page_count is not None:
        result["pageCount"] = page_count
    file_format = _docin_match_text(
        r"文档格式\s*[:：]?\s*</dt>\s*<dd[^>]*>(.*?)</dd>", data, max_length=32
    )
    if file_format:
        result["format"] = file_format.lower().replace(" ", "")
    file_size = _docin_match_text(
        r'id=["\']doc_info_detail_size["\'][^>]*>(.*?)</span>', data, max_length=40
    )
    if file_size:
        result["fileSize"] = file_size
    upload_time = _docin_match_text(
        r"上传于\s*(\d{4}-\d{2}-\d{2})", data, max_length=20
    )
    if not upload_time:
        upload_time = _docin_match_text(
            r"分享于\s*(\d{4}-\d{2}-\d{2}(?:\s+\d{1,2}:\d{2})?)", data, max_length=32
        )
    if upload_time:
        result["uploadTime"] = upload_time
    comment_count = _docin_match_int(
        r'id=["\']showComm["\'][^>]*>.*?<span>(.*?)</span>', data
    )
    if comment_count is not None:
        result["commentCount"] = comment_count
    favorite_count = _docin_match_int(
        r"收藏人数\s*[:：]?\s*</dt>\s*<dd[^>]*>\s*<em>(.*?)</em>", data
    )
    if favorite_count is not None:
        result["favoriteCount"] = favorite_count
    up_count = _docin_match_int(r'id=["\']showlab["\'][^>]*>(.*?)</span>', data)
    down_count = _docin_match_int(r'id=["\']steponlab["\'][^>]*>(.*?)</span>', data)
    if up_count is not None:
        result["upVotes"] = up_count
    if down_count is not None:
        result["downVotes"] = down_count
    category_match = re.search(
        r"文档分类\s*[:：]?\s*</dt>\s*<dd[^>]*>(.*?)</dd>",
        data,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if category_match:
        links = re.findall(
            r"<a\b[^>]*>(.*?)</a>",
            category_match.group(1),
            flags=re.IGNORECASE | re.DOTALL,
        )
        categories = [
            _compact_api_text(_strip_html(link), max_length=80) for link in links
        ]
        categories = [category for category in categories if category]
        if categories:
            result["categories"] = categories

    return result if len(result) > 2 else None


def _gamespot_text(value: Any, *, max_length: int = 260) -> str | None:
    if isinstance(value, dict):
        value = (
            value.get("rendered")
            or value.get("raw")
            or value.get("name")
            or value.get("title")
            or value.get("text")
        )
    return _compact_api_text(value, max_length=max_length)


def _gamespot_self_href(item: dict[str, Any]) -> str | None:
    links = item.get("_links")
    if not isinstance(links, dict):
        return None
    self_links = links.get("self")
    if not isinstance(self_links, list):
        return None
    for link in self_links:
        if isinstance(link, dict) and isinstance(link.get("href"), str):
            return link["href"]
    return None


def _compact_gamespot_search_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact GameSpot WP REST search results into candidate review rows."""

    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    if (
        not hostname.endswith("gamespot.com")
        or parsed_url.path != "/wp-json/wp/v2/search"
        or not isinstance(data, list)
    ):
        return None

    query_params = parse_qs(parsed_url.query)
    result: dict[str, Any] = {
        "gamespotReviewSearch": True,
        "url": url,
    }
    query = query_params.get("search", [""])[0]
    if query:
        result["query"] = query
    subtype = query_params.get("subtype", [""])[0]
    if subtype:
        result["subtype"] = subtype

    items: list[dict[str, Any]] = []
    for raw_item in data[:20]:
        if not isinstance(raw_item, dict):
            continue
        item_url = raw_item.get("url")
        api_url = _gamespot_self_href(raw_item)
        item: dict[str, Any] = {
            "rank": len(items) + 1,
        }
        for target, source in (
            ("id", "id"),
            ("type", "type"),
            ("subtype", "subtype"),
        ):
            value = raw_item.get(source)
            if value not in (None, ""):
                item[target] = value
        title = _gamespot_text(raw_item.get("title"))
        if title:
            item["title"] = title
        if isinstance(item_url, str) and item_url:
            item["url"] = item_url
        if api_url:
            item["apiUrl"] = api_url
        if item.get("title") or item.get("url") or item.get("apiUrl"):
            items.append(item)

    if items:
        result["items"] = items
    return result if len(result) > 2 else None


def _gamespot_walk_scalars(value: Any, *, depth: int = 0) -> list[tuple[str, Any]]:
    if depth > 8:
        return []
    if isinstance(value, dict):
        pairs: list[tuple[str, Any]] = []
        for key, nested in value.items():
            if isinstance(nested, (dict, list)):
                pairs.extend(_gamespot_walk_scalars(nested, depth=depth + 1))
            else:
                pairs.append((str(key), nested))
        return pairs
    if isinstance(value, list):
        pairs = []
        for item in value[:20]:
            pairs.extend(_gamespot_walk_scalars(item, depth=depth + 1))
        return pairs
    return []


def _gamespot_find_scalar(data: dict[str, Any], key_patterns: tuple[str, ...]) -> Any:
    for key, value in _gamespot_walk_scalars(data):
        lower_key = key.lower()
        if any(pattern in lower_key for pattern in key_patterns) and value not in (
            None,
            "",
            [],
        ):
            return value
    return None


def _gamespot_score_from_text(text: str) -> str | None:
    patterns = (
        r'"ratingValue"\s*:\s*"?(\d+(?:\.\d+)?)"?',
        r'"reviewRating"\s*:\s*\{[^}]*"ratingValue"\s*:\s*"?(\d+(?:\.\d+)?)"?',
        r"\b(?:score|rating)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:/|out of)\s*10\b",
        r"\b(\d+(?:\.\d+)?)\s*(?:/|out of)\s*10\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)
    return None


def _gamespot_clean_section_item(value: str) -> str | None:
    value = re.split(
        r"\b(?:Like|Share|Related Tags|Follow Us|Copy link|Facebook|Bluesky)\b",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    value = re.sub(r"\s+", " ", value).strip(" :-")
    if len(value) <= 3:
        return None
    if re.fullmatch(r"[A-Za-z]{1,3}", value):
        return None
    return _compact_api_text(value, max_length=180)


def _gamespot_section_items_from_html(
    html_text: str, start_label: str, stop_labels: tuple[str, ...]
) -> list[str]:
    start_match = re.search(
        rf"(?:>|^)\s*{re.escape(start_label)}\s*(?:<|$)",
        html_text,
        flags=re.IGNORECASE,
    )
    if not start_match:
        return []
    section = html_text[start_match.end() :]
    stop_positions = [
        match.start()
        for label in stop_labels
        if (
            match := re.search(
                rf"(?:>|^)\s*{re.escape(label)}\s*(?:<|$)",
                section,
                flags=re.IGNORECASE,
            )
        )
    ]
    if stop_positions:
        section = section[: min(stop_positions)]
    items = []
    for raw_item in re.findall(
        r"<li\b[^>]*>(.*?)</li>",
        section,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        item = _gamespot_clean_section_item(_strip_html(raw_item))
        if item:
            items.append(item)
        if len(items) >= 8:
            break
    return items


def _gamespot_section_items(
    text: str, start_label: str, stop_labels: tuple[str, ...]
) -> list[str]:
    stop_pattern = "|".join(re.escape(label) for label in stop_labels)
    match = re.search(
        rf"\b{re.escape(start_label)}\b\s*(.*?)(?:\b(?:{stop_pattern})\b|$)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []
    section = match.group(1)
    section = re.sub(r"\s+", " ", section).strip(" :-")
    if not section:
        return []
    raw_items = re.split(r"\s*(?:[•*]|\n|-{2,}|;\s*)\s*", section)
    items = []
    for raw_item in raw_items:
        item = _gamespot_clean_section_item(raw_item)
        if item and item.lower() not in {"the good", "the bad"}:
            items.append(item)
        if len(items) >= 8:
            break
    return items


def _gamespot_review_text_from_json(data: dict[str, Any]) -> str:
    parts = []
    for key in ("content", "excerpt"):
        value = data.get(key)
        if isinstance(value, dict):
            for nested_value in value.values():
                if isinstance(nested_value, (str, int, float)):
                    parts.append(str(nested_value))
        elif isinstance(value, str):
            parts.append(value)
    return _strip_html(" ".join(parts))


def _gamespot_review_html_from_json(data: dict[str, Any]) -> str:
    parts = []
    for key in ("content", "excerpt"):
        value = data.get(key)
        if isinstance(value, dict):
            rendered = value.get("rendered")
            if isinstance(rendered, str):
                parts.append(rendered)
        elif isinstance(value, str):
            parts.append(value)
    return " ".join(parts)


def _compact_gamespot_review_data(url: str, data: Any) -> dict[str, Any] | None:
    """Compact GameSpot review REST/HTML into scorecard-like fields."""

    parsed_url = urlsplit(url)
    hostname = parsed_url.hostname or ""
    if not hostname.endswith("gamespot.com"):
        return None

    is_rest_review = re.search(r"/wp-json/wp/v2/reviews/\d+/?$", parsed_url.path)
    is_review_html = "/reviews/" in parsed_url.path
    if not is_rest_review and not is_review_html:
        return None

    result: dict[str, Any] = {
        "gamespotReviewDetail": True,
        "url": url,
    }
    id_match = re.search(r"/(?:reviews/)?(\d+)(?:/?|$)", parsed_url.path)
    if id_match:
        result["id"] = id_match.group(1)

    if isinstance(data, dict):
        if data.get("id") not in (None, ""):
            result["id"] = data["id"]
        link = data.get("link")
        if isinstance(link, str) and link:
            result["articleUrl"] = link
        title = _gamespot_text(data.get("title"))
        if title:
            result["title"] = title
        for target, source in (("publishedAt", "date"), ("modifiedAt", "modified")):
            value = data.get(source)
            if isinstance(value, str) and value:
                result[target] = value
        embedded = data.get("_embedded")
        if isinstance(embedded, dict) and isinstance(embedded.get("author"), list):
            for author in embedded["author"]:
                if isinstance(author, dict):
                    name = _gamespot_text(author.get("name"), max_length=120)
                    if name:
                        result["reviewer"] = name
                        break
        if "reviewer" not in result:
            reviewer = _gamespot_find_scalar(
                data,
                ("author_name", "authorname", "byline", "reviewer"),
            )
            reviewer_text = _gamespot_text(reviewer, max_length=120)
            if reviewer_text:
                result["reviewer"] = reviewer_text

        score = _gamespot_find_scalar(
            data,
            ("review_score", "reviewscore", "score", "ratingvalue", "rating_value"),
        )
        score_text = _compact_api_text(score, max_length=32)
        html_text = _gamespot_review_html_from_json(data)
        text = _gamespot_review_text_from_json(data)
        if not score_text:
            score_text = _gamespot_score_from_text(json.dumps(data, ensure_ascii=False))
        if not score_text:
            score_text = _gamespot_score_from_text(text)
        if score_text:
            result["score"] = score_text
        cons_stop_labels = (
            "Verdict",
            "The Bottom Line",
            "About the Author",
            "Like",
            "Share",
            "Related Tags",
            "Follow Us",
        )
        pros = _gamespot_section_items_from_html(
            html_text, "The Good", ("The Bad", "Verdict", "The Bottom Line")
        ) or _gamespot_section_items(
            text, "The Good", ("The Bad", "Verdict", "The Bottom Line")
        )
        cons = _gamespot_section_items_from_html(
            html_text, "The Bad", cons_stop_labels
        ) or _gamespot_section_items(text, "The Bad", cons_stop_labels)
        if pros:
            result["pros"] = pros
        if cons:
            result["cons"] = cons
        verdict = _docin_match_text(
            r"\bVerdict\b\s*(.*?)(?:About the Author|$)", text, max_length=500
        )
        if verdict:
            result["verdict"] = verdict
        if text:
            result["textSnippet"] = text[:1200] + (
                f"... [{len(text)} chars total]" if len(text) > 1200 else ""
            )
        return result if len(result) > 2 else None

    if isinstance(data, str):
        text = _strip_html(data)
        title = _docin_match_text(r"<title[^>]*>(.*?)</title>", data, max_length=240)
        if title:
            result["title"] = title
        score = _gamespot_score_from_text(data) or _gamespot_score_from_text(text)
        if score:
            result["score"] = score
        reviewer = _docin_match_text(
            r"\bBy\s+([A-Z][A-Za-z .'\-]{2,80})\s+(?:on|/)",
            text,
            max_length=120,
        )
        if reviewer:
            result["reviewer"] = reviewer
        cons_stop_labels = (
            "Verdict",
            "The Bottom Line",
            "About the Author",
            "Like",
            "Share",
            "Related Tags",
            "Follow Us",
        )
        pros = _gamespot_section_items_from_html(
            data, "The Good", ("The Bad", "Verdict", "The Bottom Line")
        ) or _gamespot_section_items(
            text, "The Good", ("The Bad", "Verdict", "The Bottom Line")
        )
        cons = _gamespot_section_items_from_html(
            data, "The Bad", cons_stop_labels
        ) or _gamespot_section_items(text, "The Bad", cons_stop_labels)
        if pros:
            result["pros"] = pros
        if cons:
            result["cons"] = cons
        if text:
            result["textSnippet"] = text[:1200] + (
                f"... [{len(text)} chars total]" if len(text) > 1200 else ""
            )
        return result if len(result) > 2 else None

    return None


def _summarize_sina_hq_data(data: Any) -> str | None:
    if (
        not isinstance(data, dict)
        or not data.get("sinaHq")
        or not isinstance(data.get("quotes"), list)
    ):
        return None

    items = []
    for quote_item in data["quotes"][:10]:
        if not isinstance(quote_item, dict):
            continue
        chunks = []
        for label, key in (
            ("symbol", "symbol"),
            ("name", "name"),
            ("price", "price"),
            ("bid", "bid"),
            ("ask", "ask"),
            ("open", "open"),
            ("high", "high"),
            ("low", "low"),
            ("change", "change"),
            ("change_pct", "change_pct"),
            ("amplitude_pct", "amplitude_pct"),
            ("time", "time"),
            ("date", "date"),
            ("previous_close", "previous_close"),
        ):
            value = quote_item.get(key)
            if value not in (None, ""):
                chunks.append(f"{label}={value}")
        if chunks:
            items.append("; ".join(chunks))
    if not items:
        return None
    return ("sina_hq quotes: " + " | ".join(items))[:4000]


def _summarize_sina_forex_kline_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("sinaForexKline"):
        return None

    trend_raw = data.get("trend")
    trend: dict[str, Any] = trend_raw if isinstance(trend_raw, dict) else {}
    chunks = ["sina_forex_kline"]
    for label, key in (
        ("symbol", "symbol"),
        ("dayCount", "dayCount"),
    ):
        value = data.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    for label, key in (
        ("basis", "basis"),
        ("direction", "direction"),
        ("from", "fromDate"),
        ("to", "toDate"),
        ("firstClose", "firstClose"),
        ("lastClose", "lastClose"),
        ("change", "change"),
        ("changePct", "changePct"),
    ):
        value = trend.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    rows = data.get("lastSevenTradingDays")
    if isinstance(rows, list) and rows:
        row_summaries = []
        for index, row in enumerate(rows[-7:], 1):
            if not isinstance(row, dict):
                continue
            row_summaries.append(
                f"#{index} date={row.get('date')} close={row.get('close')}"
            )
        if row_summaries:
            chunks.append("recentCloses=" + " | ".join(row_summaries))
    return "; ".join(chunks)[:4000]


def _summarize_sina_gold_analysis_data(data: Any) -> str | None:
    if (
        not isinstance(data, dict)
        or not data.get("sinaGoldAnalysis")
        or not isinstance(data.get("items"), list)
    ):
        return None

    items = []
    for index, item in enumerate(data["items"][:8], 1):
        if not isinstance(item, dict):
            continue
        chunks = [f"#{index}"]
        for label, key in (
            ("title", "title"),
            ("stance", "stance"),
            ("url", "url"),
        ):
            value = item.get(key)
            if value not in (None, ""):
                chunks.append(f"{label}={value}")
        if len(chunks) > 1:
            items.append("; ".join(chunks))
    if not items:
        return None
    return ("sina_gold_analysis: " + " | ".join(items))[:4000]


def _summarize_you163_detail_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("you163ProductDetail"):
        return None
    chunks = ["you163_detail"]
    for label, key in (
        ("id", "id"),
        ("name", "name"),
        ("price", "retailPrice"),
        ("sellVolume", "sellVolume"),
        ("comments", "commentCount"),
        ("goodRate", "commentGoodRates"),
        ("material", "material"),
        ("thread_count", "thread_count"),
        ("url", "url"),
    ):
        value = data.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    attrs = data.get("attrs")
    if isinstance(attrs, dict) and attrs:
        chunks.append(
            "attrs="
            + ", ".join(f"{key}:{value}" for key, value in list(attrs.items())[:8])
        )
    specs = data.get("specs")
    if isinstance(specs, list) and specs:
        spec_chunks = []
        for spec in specs[:4]:
            if isinstance(spec, dict):
                spec_chunks.append(
                    f"{spec.get('name')}={','.join(str(value) for value in spec.get('values', [])[:6])}"
                )
        if spec_chunks:
            chunks.append("specs=" + "; ".join(spec_chunks))
    return "; ".join(chunks)[:4000]


def _summarize_you163_search_data(data: Any) -> str | None:
    if (
        not isinstance(data, dict)
        or not data.get("you163SearchResult")
        or not isinstance(data.get("items"), list)
    ):
        return None
    items = []
    for item in data["items"][:12]:
        if not isinstance(item, dict):
            continue
        chunks = []
        for label, key in (
            ("id", "id"),
            ("name", "name"),
            ("price", "retailPrice"),
            ("sellVolume", "sellVolume"),
            ("material", "material"),
            ("thread_count", "thread_count"),
            ("detail", "detail_url"),
        ):
            value = item.get(key)
            if value not in (None, ""):
                chunks.append(f"{label}={value}")
        if item.get("simpleDesc"):
            chunks.append(f"desc={item['simpleDesc']}")
        specs = item.get("specs")
        if isinstance(specs, list) and specs:
            spec_chunks = []
            for spec in specs[:3]:
                if isinstance(spec, dict):
                    spec_chunks.append(
                        f"{spec.get('name')}={','.join(str(value) for value in spec.get('values', [])[:5])}"
                    )
            if spec_chunks:
                chunks.append("specs=" + "; ".join(spec_chunks))
        if chunks:
            items.append("; ".join(chunks))
    if not items:
        return None
    prefix = "you163_search"
    if data.get("total") not in (None, ""):
        prefix += f" total={data['total']}"
    if data.get("page") not in (None, "") and data.get("totalPage") not in (None, ""):
        prefix += f" page={data['page']}/{data['totalPage']}"
    return (prefix + " items: " + " | ".join(items))[:4000]


def _summarize_you163_comment_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("you163CommentData"):
        return None
    chunks = ["you163_comments"]
    for label, key in (
        ("itemId", "itemId"),
        ("goodRate", "goodRate"),
        ("star", "star"),
        ("defaultGoodCount", "defaultGoodCount"),
        ("avgStar", "avgStar"),
        ("total", "total"),
        ("page", "page"),
    ):
        value = data.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    tags = data.get("tags")
    if isinstance(tags, list) and tags:
        tag_chunks = []
        for tag in tags[:8]:
            if isinstance(tag, dict) and tag.get("name"):
                if tag.get("count") not in (None, ""):
                    tag_chunks.append(f"{tag['name']}={tag['count']}")
                else:
                    tag_chunks.append(str(tag["name"]))
        if tag_chunks:
            chunks.append("tags=" + ", ".join(tag_chunks))
    keywords = data.get("keywords")
    if isinstance(keywords, list) and keywords:
        chunks.append("keywords=" + ", ".join(str(value) for value in keywords[:12]))
    comments = data.get("comments")
    if isinstance(comments, list) and comments:
        comment_chunks = []
        for comment in comments[:4]:
            if not isinstance(comment, dict):
                continue
            parts = []
            if comment.get("star") not in (None, ""):
                parts.append(f"star={comment['star']}")
            if comment.get("content"):
                parts.append(f"content={comment['content']}")
            if parts:
                comment_chunks.append("; ".join(parts))
        if comment_chunks:
            chunks.append("sample_comments=" + " | ".join(comment_chunks))
    return "; ".join(chunks)[:4000]


def _summarize_dangdang_detail_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("dangdangProductDetail"):
        return None
    chunks = ["dangdang_detail"]
    for label, key in (
        ("id", "id"),
        ("title", "title"),
        ("author", "author"),
        ("publisher", "publisher"),
        ("publishTime", "publishTime"),
        ("categoryPath", "categoryPath"),
        ("url", "url"),
    ):
        value = data.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    age_hints = data.get("ageHints")
    if isinstance(age_hints, list) and age_hints:
        chunks.append("ageHints=" + ",".join(str(value) for value in age_hints[:5]))
    return "; ".join(chunks)[:4000]


def _summarize_dangdang_comment_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("dangdangCommentData"):
        return None
    chunks = ["dangdang_comments"]
    for label, key in (
        ("productId", "productId"),
        ("mainProductId", "mainProductId"),
        ("goodRate", "goodRate"),
        ("total", "totalCommentCount"),
        ("good", "goodCommentCount"),
        ("neutral", "neutralCommentCount"),
        ("bad", "badCommentCount"),
        ("defaultGood", "defaultGoodCount"),
        ("avgScore", "averageScore"),
    ):
        value = data.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    return "; ".join(chunks)[:4000]


def _academia_view_sort_key(item: Any) -> float:
    if not isinstance(item, dict):
        return -1
    try:
        views = item.get("views")
        return float(views) if views not in (None, "") else -1
    except (TypeError, ValueError):
        return -1


def _summarize_academia_profile_works_data(data: Any) -> str | None:
    if (
        not isinstance(data, dict)
        or not data.get("academiaProfileWorks")
        or not isinstance(data.get("items"), list)
    ):
        return None
    chunks = ["academia_profile_works"]
    for label, key in (
        ("user_id", "userId"),
        ("offset", "offset"),
        ("per_page", "perPage"),
    ):
        value = data.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    chunks.append(f"items={len(data['items'])}")
    if data.get("viewsFetched"):
        chunks.append("views=merged")
        items = sorted(
            [item for item in data["items"] if isinstance(item, dict)],
            key=_academia_view_sort_key,
            reverse=True,
        )
        prefix = "top_by_views"
        limit = 8
    else:
        items = [item for item in data["items"] if isinstance(item, dict)]
        prefix = "items"
        limit = 20
    item_chunks = []
    for index, item in enumerate(items[:limit], 1):
        parts = [f"#{index}"]
        for label, key in (
            ("id", "id"),
            ("views", "views"),
            ("title", "title"),
            ("year", "year"),
            ("publication", "publication"),
        ):
            value = item.get(key)
            if value not in (None, ""):
                parts.append(f"{label}={value}")
        abstract = _compact_api_text(
            item.get("abstract"), max_length=260 if data.get("viewsFetched") else 90
        )
        if abstract:
            parts.append(f"abstract={abstract}")
        item_chunks.append("; ".join(parts))
    if item_chunks:
        chunks.append(prefix + ": " + " | ".join(item_chunks))
    if not data.get("viewsFetched") and data.get("workIds"):
        chunks.append(
            "work_ids=" + ",".join(str(work_id) for work_id in data["workIds"][:60])
        )
    return "; ".join(chunks)[:4000]


def _summarize_academia_views_data(data: Any) -> str | None:
    if (
        not isinstance(data, dict)
        or not data.get("academiaWorkViews")
        or not isinstance(data.get("items"), list)
    ):
        return None
    items = []
    for item in data["items"][:20]:
        if not isinstance(item, dict):
            continue
        work_id = item.get("id")
        views = item.get("views")
        if work_id not in (None, "") and views not in (None, ""):
            items.append(f"{work_id}={views}")
    if not items:
        return None
    return ("academia_views " + " | ".join(items))[:4000]


def _summarize_zalando_listing_data(data: Any) -> str | None:
    if (
        not isinstance(data, dict)
        or not data.get("zalandoListing")
        or not isinstance(data.get("items"), list)
    ):
        return None
    chunks = ["zalando_listing"]
    if data.get("totalItems") not in (None, ""):
        chunks.append(f"total={data['totalItems']}")
    items = []
    for index, item in enumerate(data["items"][:10], 1):
        if not isinstance(item, dict):
            continue
        parts = [f"#{index}"]
        for label, key in (
            ("sku", "sku"),
            ("brand", "brand"),
            ("name", "name"),
            ("price", "price"),
            ("rating", "rating"),
            ("url", "url"),
        ):
            value = item.get(key)
            if value not in (None, ""):
                parts.append(f"{label}={value}")
        sizes = item.get("sizes")
        if isinstance(sizes, list) and sizes:
            parts.append("sizes=" + ",".join(str(size) for size in sizes[:12]))
        items.append("; ".join(parts))
    if items:
        chunks.append("items: " + " | ".join(items))
    return "; ".join(chunks)[:4000]


def _summarize_zalando_detail_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("zalandoProductDetail"):
        return None
    chunks = ["zalando_detail"]
    for label, key in (
        ("name", "name"),
        ("rating", "rating"),
        ("url", "url"),
    ):
        value = data.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    materials = data.get("materials")
    if isinstance(materials, dict) and materials:
        material_chunks = []
        for label, value in list(materials.items())[:6]:
            if value not in (None, ""):
                material_chunks.append(f"{label}: {value}")
        if material_chunks:
            chunks.append("materials=" + " | ".join(material_chunks))
    return "; ".join(chunks)[:4000]


def _summarize_docin_detail_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("docinDocumentDetail"):
        return None
    chunks = ["docin_detail"]
    for label, key in (
        ("id", "id"),
        ("title", "title"),
        ("pages", "pageCount"),
        ("upload", "uploadTime"),
        ("format", "format"),
        ("reads", "readCount"),
        ("comments", "commentCount"),
        ("favorites", "favoriteCount"),
        ("unavailable", "unavailable_reason"),
        ("url", "url"),
    ):
        value = data.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    categories = data.get("categories")
    if isinstance(categories, list) and categories:
        chunks.append("categories=" + "/".join(str(value) for value in categories[:4]))
    return "; ".join(chunks)[:4000]


def _summarize_docin_search_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("docinSearchResult"):
        return None
    chunks = ["docin_search"]
    for label, key in (
        ("query", "query"),
        ("total", "totalCount"),
        ("url", "url"),
    ):
        value = data.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    filters = data.get("filters")
    if isinstance(filters, dict) and filters:
        chunks.append(
            "filters="
            + ",".join(f"{key}:{value}" for key, value in list(filters.items())[:8])
        )
    items = data.get("items")
    if isinstance(items, list) and items:
        item_chunks = []
        for item in items[:12]:
            if not isinstance(item, dict):
                continue
            parts = []
            for label, key in (
                ("rank", "rank"),
                ("id", "id"),
                ("title", "title"),
                ("pages", "pageCount"),
                ("format", "format"),
                ("upload", "uploadTimeHint"),
                ("url", "url"),
            ):
                value = item.get(key)
                if value not in (None, ""):
                    parts.append(f"{label}={value}")
            snippet = item.get("snippet")
            if snippet:
                parts.append(f"snippet={_compact_api_text(snippet, max_length=180)}")
            if parts:
                item_chunks.append("; ".join(parts))
        if item_chunks:
            chunks.append("items=" + " | ".join(item_chunks))
    return "; ".join(chunks)[:4000]


def _summarize_gamespot_search_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("gamespotReviewSearch"):
        return None
    chunks = ["gamespot_review_search"]
    for label, key in (
        ("query", "query"),
        ("subtype", "subtype"),
        ("url", "url"),
    ):
        value = data.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    items = data.get("items")
    if isinstance(items, list) and items:
        item_chunks = []
        for item in items[:10]:
            if not isinstance(item, dict):
                continue
            parts = []
            for label, key in (
                ("rank", "rank"),
                ("id", "id"),
                ("title", "title"),
                ("url", "url"),
                ("api", "apiUrl"),
            ):
                value = item.get(key)
                if value not in (None, ""):
                    parts.append(f"{label}={value}")
            if parts:
                item_chunks.append("; ".join(parts))
        if item_chunks:
            chunks.append("items=" + " | ".join(item_chunks))
    return "; ".join(chunks)[:4000]


def _summarize_gamespot_review_data(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("gamespotReviewDetail"):
        return None
    chunks = ["gamespot_review_detail"]
    for label, key in (
        ("id", "id"),
        ("title", "title"),
        ("score", "score"),
        ("reviewer", "reviewer"),
        ("date", "publishedAt"),
        ("url", "articleUrl"),
    ):
        value = data.get(key)
        if value not in (None, ""):
            chunks.append(f"{label}={value}")
    for label, key in (("pros", "pros"), ("cons", "cons")):
        values = data.get(key)
        if isinstance(values, list) and values:
            chunks.append(f"{label}=" + "; ".join(str(value) for value in values[:6]))
    if data.get("verdict"):
        chunks.append(f"verdict={data['verdict']}")
    return "; ".join(chunks)[:4000]


def _summarize_api_data(url: str, data: Any, method: str = "GET") -> str:
    """Create a compact memory line so API results are not lost in flash mode."""

    youtube_summary = _summarize_youtube_search_data(data)
    if youtube_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {youtube_summary}"[
            :4000
        ]

    bilibili_summary = _summarize_bilibili_data(data)
    if bilibili_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {bilibili_summary}"[
            :4000
        ]

    suning_review_summary = _summarize_suning_review_data(data)
    if suning_review_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {suning_review_summary}"[
            :4000
        ]

    ctrip_train_summary = _summarize_ctrip_train_data(data)
    if ctrip_train_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {ctrip_train_summary}"[
            :4000
        ]

    eastmoney_quote_summary = _summarize_eastmoney_quote_data(data)
    if eastmoney_quote_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {eastmoney_quote_summary}"[
            :4000
        ]

    douban_summary = _summarize_douban_data(data)
    if douban_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {douban_summary}"[
            :4000
        ]

    three_dm_mod_summary = _summarize_3dm_mod_data(data)
    if three_dm_mod_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {three_dm_mod_summary}"[
            :4000
        ]

    sina_summary = _summarize_sina_hq_data(data)
    if sina_summary:
        return (
            f"Called API {method.upper()} {urlsplit(url).path or url} ; {sina_summary}"[
                :4000
            ]
        )

    sina_forex_kline_summary = _summarize_sina_forex_kline_data(data)
    if sina_forex_kline_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {sina_forex_kline_summary}"[
            :4000
        ]

    sina_gold_summary = _summarize_sina_gold_analysis_data(data)
    if sina_gold_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {sina_gold_summary}"[
            :4000
        ]

    you163_summary = _summarize_you163_detail_data(data)
    if you163_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {you163_summary}"[
            :4000
        ]

    you163_search_summary = _summarize_you163_search_data(data)
    if you163_search_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {you163_search_summary}"[
            :4000
        ]

    you163_comment_summary = _summarize_you163_comment_data(data)
    if you163_comment_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {you163_comment_summary}"[
            :4000
        ]

    dangdang_detail_summary = _summarize_dangdang_detail_data(data)
    if dangdang_detail_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {dangdang_detail_summary}"[
            :4000
        ]

    dangdang_comment_summary = _summarize_dangdang_comment_data(data)
    if dangdang_comment_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {dangdang_comment_summary}"[
            :4000
        ]

    academia_profile_summary = _summarize_academia_profile_works_data(data)
    if academia_profile_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {academia_profile_summary}"[
            :4000
        ]

    academia_views_summary = _summarize_academia_views_data(data)
    if academia_views_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {academia_views_summary}"[
            :4000
        ]

    zalando_listing_summary = _summarize_zalando_listing_data(data)
    if zalando_listing_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {zalando_listing_summary}"[
            :4000
        ]

    zalando_detail_summary = _summarize_zalando_detail_data(data)
    if zalando_detail_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {zalando_detail_summary}"[
            :4000
        ]

    docin_summary = _summarize_docin_detail_data(data)
    if docin_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {docin_summary}"[
            :4000
        ]

    docin_search_summary = _summarize_docin_search_data(data)
    if docin_search_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {docin_search_summary}"[
            :4000
        ]

    gamespot_search_summary = _summarize_gamespot_search_data(data)
    if gamespot_search_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {gamespot_search_summary}"[
            :4000
        ]

    gamespot_review_summary = _summarize_gamespot_review_data(data)
    if gamespot_review_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {gamespot_review_summary}"[
            :4000
        ]

    cqvip_summary = _summarize_cqvip_search_data(data)
    if cqvip_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {cqvip_summary}"[
            :4000
        ]

    archive_summary = _summarize_archive_data(data)
    if archive_summary:
        return f"Called API {method.upper()} {urlsplit(url).path or url} ; {archive_summary}"[
            :4000
        ]

    parsed_url = urlsplit(url)
    parts = [f"Called API {method.upper()} {parsed_url.path or url}"]
    if isinstance(data, dict):
        count = _first_present(data, ("itemCount", "total", "totalCount", "count"))
        if count is None and isinstance(data.get("data"), dict):
            count = _first_present(
                data["data"], ("itemCount", "total", "totalCount", "count")
            )
        if count is not None:
            parts.append(f"count={count}")
    detail = _extract_content_detail_for_api_memory(data)
    if detail:
        chunks = ["detail"]
        title = _first_present(detail, ("name", "title"))
        author = _first_present(detail, ("author", "byline"))
        source = _first_present(detail, ("source", "sourceName", "media"))
        published_at = _first_present(
            detail, ("pubTime", "publishTime", "publishedAt", "date")
        )
        if title:
            chunks.append(f"title={title}")
        if author:
            chunks.append(f"author={author}")
        if source:
            chunks.append(f"source={source}")
        if published_at:
            chunks.append(f"time={published_at}")
        parts.append("; ".join(chunks))
    products = _extract_products_for_api_memory(data)
    if products:
        summaries = []
        for index, product in enumerate(products[:10], 1):
            name = _first_present(product, ("name", "productName", "title"))
            brand = _first_present(product, ("brandName", "brand"))
            price = _compact_price(
                _first_present(product, ("price", "currentPrice", "productPrice"))
            )
            product_id = _first_present(
                product, ("id", "productId", "contId", "originalContId")
            )
            published_at = _first_present(
                product, ("pubTime", "pubTimeNew", "publishTime", "publishedAt", "date")
            )
            source = _first_present(product, ("source", "sourceName", "media"))
            if not source and isinstance(product.get("nodeInfo"), dict):
                source = _first_present(product["nodeInfo"], ("name", "shareName"))
            snippet = _compact_api_text(
                _first_present(product, ("summary", "desc", "description"))
            )
            item_url = _first_present(product, ("url", "link", "href"))
            if (
                not item_url
                and parsed_url.hostname
                and parsed_url.hostname.endswith("thepaper.cn")
                and product_id
            ):
                item_url = f"https://www.thepaper.cn/newsDetail_forward_{product_id}"
            variants = product.get("variants")
            sizes = []
            if isinstance(variants, list):
                for variant in variants:
                    if (
                        not isinstance(variant, dict)
                        or variant.get("isAvailable") is False
                    ):
                        continue
                    size = _first_present(
                        variant, ("displaySizeText", "brandSize", "size")
                    )
                    if size:
                        sizes.append(str(size))
                    if len(sizes) >= 6:
                        break
            chunks = [f"#{index}"]
            if product_id is not None:
                chunks.append(f"id={product_id}")
            if brand:
                chunks.append(f"brand={brand}")
            if name:
                chunks.append(f"name={name}")
            if price:
                chunks.append(f"price={price}")
            if published_at:
                chunks.append(f"time={published_at}")
            if source:
                chunks.append(f"source={source}")
            if item_url:
                chunks.append(f"url={item_url}")
            if snippet:
                chunks.append(f"snippet={snippet}")
            if sizes:
                chunks.append(f"sizes={', '.join(sizes)}")
            summaries.append("; ".join(chunks))
        parts.append("items: " + " | ".join(summaries))
    elif isinstance(data, dict):
        parts.append("keys=" + ",".join(list(data.keys())[:12]))
    return " ; ".join(parts)[:4000]


def _is_known_runtime_compact_api_data(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    for flag in (
        "youtubeSearchResults",
        "bilibiliVideoSearch",
        "bilibiliVideoDetail",
        "suningReviewSatisfy",
        "ctripTrainRoute",
        "eastmoneyQuote",
        "doubanSubjectSuggest",
        "doubanSubjectDetail",
        "doubanReviewList",
        "threeDmModSearch",
        "threeDmModDetail",
        "sinaHq",
        "sinaForexKline",
        "sinaGoldAnalysis",
        "you163ProductDetail",
        "you163SearchResult",
        "you163CommentData",
        "dangdangProductDetail",
        "dangdangCommentData",
        "academiaProfileWorks",
        "academiaWorkViews",
        "zalandoListing",
        "zalandoProductDetail",
        "docinSearchResult",
        "docinDocumentDetail",
        "gamespotReviewSearch",
        "gamespotReviewDetail",
        "cqvipSearchResult",
        "archiveMetadata",
        "archiveAdvancedSearch",
    ):
        if data.get(flag):
            return True
    return False


@dataclass(frozen=True)
class ApiObservation:
    """Compact API observation returned to an upper-layer browser agent."""

    ok: bool
    status: int | None
    data: Any
    pruned_json: str
    summary: str
    runtime_compact_state: bool
    full_response_chars: int


async def fetch_api_via_http(
    url: str, method: str, headers: dict[str, str], body: str | None
) -> dict[str, Any]:
    """Public wrapper for adapter-approved external HTTP fetches."""

    if not _is_public_http_url(url):
        raise ValueError("Adapter HTTP fetch only allows public http(s) URLs")
    return await _fetch_api_via_http(url, method, headers, body)


def normalize_request_body(url: str, body: str | None) -> str | None:
    """Normalize known request payloads before a runtime API call."""

    return _normalize_cqvip_search_body(url, body)


def compact_api_data(url: str, data: Any) -> Any:
    """Return known compact data for a URL/response, or the original data."""

    compactors = (
        _compact_youtube_search_data,
        _compact_cqvip_search_data,
        _compact_academia_profile_works_data,
        _compact_academia_views_data,
        _compact_dangdang_comment_data,
        _compact_dangdang_detail_data,
        _compact_you163_detail_data,
        _compact_you163_search_data,
        _compact_you163_comment_data,
        _compact_bilibili_data,
        _compact_suning_review_data,
        _compact_ctrip_train_data,
        _compact_eastmoney_quote_data,
        _compact_douban_suggest_data,
        _compact_douban_subject_detail_data,
        _compact_douban_review_data,
        _compact_3dm_mod_data,
        _compact_sina_hq_data,
        _compact_sina_forex_kline_data,
        _compact_sina_gold_analysis_data,
        _compact_docin_search_data,
        _compact_docin_detail_data,
        _compact_gamespot_search_data,
        _compact_gamespot_review_data,
        _compact_zalando_detail_data,
        _compact_zalando_listing_data,
        _compact_archive_data,
    )
    for compactor in compactors:
        compacted = compactor(url, data)
        if compacted is not None:
            return compacted
    if isinstance(data, str):
        next_data = _extract_next_data_from_html(data)
        if next_data is not None:
            return next_data
        html_data = _extract_readable_data_from_html(data, url)
        if html_data is not None:
            return html_data
    return data


def prune_api_data(data: Any) -> Any:
    """Prune deep API data for compact JSON display."""

    return _prune_deep_json(data)


def summarize_api_data(url: str, data: Any, method: str = "GET") -> str:
    """Create a compact memory line for API results."""

    return _summarize_api_data(url, data, method=method)


def is_known_runtime_compact_api_data(data: Any) -> bool:
    """Return whether compact data is a known answerable runtime state."""

    return _is_known_runtime_compact_api_data(data)


def build_api_observation(
    url: str,
    data: Any,
    *,
    method: str = "GET",
    ok: bool = True,
    status: int | None = None,
) -> ApiObservation:
    """Compact, prune, and summarize an API response for agent handoff."""

    compacted = compact_api_data(url, data)
    pruned = json.dumps(prune_api_data(compacted), ensure_ascii=False, indent=2)
    summary = summarize_api_data(url, compacted, method=method)
    return ApiObservation(
        ok=ok,
        status=status,
        data=compacted,
        pruned_json=pruned,
        summary=summary,
        runtime_compact_state=bool(
            summary and is_known_runtime_compact_api_data(compacted)
        ),
        full_response_chars=len(pruned),
    )


_BROWSER_USE_SERVICE_COMPAT_PRIVATE_NAMES = (
    "_MAX_API_RESPONSE_CHARS",
    "_is_public_http_url",
    "_site_suffix",
    "_same_site_or_subdomain",
    "_known_blocked_page_message",
    "_detect_known_blocked_page",
    "_is_cqvip_newsite_api",
    "_normalize_cqvip_search_body",
    "_prune_deep_json",
    "_first_present",
    "_compact_price",
    "_extract_products_for_api_memory",
    "_extract_content_detail_for_api_memory",
    "_compact_api_text",
    "_format_epoch_date",
    "_extract_jsonp_payload",
    "_extract_next_data_from_html",
    "_extract_js_object_assignment",
    "_extract_you163_json_data",
    "_looks_like_html",
    "_strip_html",
    "_extract_html_meta",
    "_extract_html_links",
    "_extract_readable_data_from_html",
    "_is_3dm_mod_api",
    "_compact_3dm_mod_item",
    "_compact_3dm_mod_data",
    "_summarize_3dm_mod_data",
    "_is_bilibili_api",
    "_compact_bilibili_video_item",
    "_compact_bilibili_data",
    "_summarize_bilibili_data",
    "YouTubeSearchRequest",
    "YouTubeSearchBatchAction",
    "YouTubeFetchedPage",
    "collect_youtube_search_results",
    "_compact_youtube_search_data",
    "_summarize_youtube_search_data",
    "_is_suning_review_satisfy_url",
    "_compact_suning_review_data",
    "_summarize_suning_review_data",
    "_is_ctrip_trainbooking_url",
    "_number_or_none",
    "_ctrip_second_class_price",
    "_compact_ctrip_train_item",
    "_ctrip_time_minutes",
    "_compact_ctrip_train_data",
    "_summarize_ctrip_train_item",
    "_summarize_ctrip_train_data",
    "_compact_eastmoney_quote_data",
    "_summarize_eastmoney_quote_data",
    "_is_douban_url",
    "_compact_douban_suggest_data",
    "_douban_info_block",
    "_douban_info_field",
    "_compact_douban_subject_detail_data",
    "_compact_douban_review_items",
    "_compact_douban_comment_items",
    "_compact_douban_review_data",
    "_summarize_douban_data",
    "_archive_scalar",
    "_archive_first_present",
    "_archive_duration_from_files",
    "_compact_archive_data",
    "_summarize_archive_data",
    "_sina_number",
    "_sina_quote_field",
    "_compact_sina_hq_data",
    "_compact_sina_forex_kline_data",
    "_sina_gold_analysis_stance",
    "_compact_sina_gold_analysis_data",
    "_compact_you163_detail_data",
    "_compact_you163_search_data",
    "_compact_you163_comment_data",
    "_dangdang_match_text",
    "_dangdang_clean_labeled_text",
    "_dangdang_percent",
    "_extract_dangdang_spu_info",
    "_compact_dangdang_detail_data",
    "_compact_dangdang_comment_data",
    "_is_academia_profile_works_api",
    "_is_academia_views_api",
    "_academia_extract_work_json",
    "_academia_html_text",
    "_academia_publication_year",
    "_compact_academia_profile_works_data",
    "_compact_academia_views_data",
    "_academia_views_url",
    "_merge_academia_view_counts",
    "_is_zalando_listing_url",
    "_is_zalando_product_detail_url",
    "_zalando_hydration_payloads",
    "_zalando_graphql_entries",
    "_zalando_walk_products",
    "_zalando_nested_texts",
    "_zalando_price_text",
    "_zalando_rating_text",
    "_zalando_append_product",
    "_compact_zalando_listing_data",
    "_zalando_extract_detail_name",
    "_zalando_extract_labeled_text",
    "_compact_zalando_detail_data",
    "_compact_cqvip_search_data",
    "_summarize_cqvip_search_data",
    "_docin_match_text",
    "_docin_match_int",
    "_compact_docin_search_data",
    "_compact_docin_detail_data",
    "_gamespot_text",
    "_gamespot_self_href",
    "_compact_gamespot_search_data",
    "_gamespot_walk_scalars",
    "_gamespot_find_scalar",
    "_gamespot_score_from_text",
    "_gamespot_section_items",
    "_gamespot_review_text_from_json",
    "_compact_gamespot_review_data",
    "_summarize_sina_hq_data",
    "_summarize_sina_forex_kline_data",
    "_summarize_sina_gold_analysis_data",
    "_summarize_you163_detail_data",
    "_summarize_you163_search_data",
    "_summarize_you163_comment_data",
    "_summarize_dangdang_detail_data",
    "_summarize_dangdang_comment_data",
    "_academia_view_sort_key",
    "_summarize_academia_profile_works_data",
    "_summarize_academia_views_data",
    "_summarize_zalando_listing_data",
    "_summarize_zalando_detail_data",
    "_summarize_docin_search_data",
    "_summarize_docin_detail_data",
    "_summarize_gamespot_search_data",
    "_summarize_gamespot_review_data",
    "_summarize_api_data",
    "_is_known_runtime_compact_api_data",
)

BROWSER_USE_SERVICE_COMPAT: dict[str, Any] = {
    name.removeprefix("_"): globals()[name]
    for name in _BROWSER_USE_SERVICE_COMPAT_PRIVATE_NAMES
}
BROWSER_USE_SERVICE_COMPAT["fetch_api_via_http"] = fetch_api_via_http
