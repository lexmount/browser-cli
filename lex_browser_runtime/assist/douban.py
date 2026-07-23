"""Douban-specific runtime completion contracts."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from lex_browser_runtime.assist.api import (
    _compact_api_text,
    _compact_douban_review_data,
    _compact_douban_subject_detail_data,
    _compact_douban_suggest_data,
)


@dataclass(frozen=True)
class RuntimeCompletion:
    """A deterministic runtime completion payload for high-volume extraction."""

    category: str
    filename: str
    payload: dict[str, Any]

    @property
    def content(self) -> str:
        """Return pretty JSON suitable for writing to the agent file system."""

        return json.dumps(self.payload, ensure_ascii=False, indent=2)


def detect_douban_batch_completion_intent(task: str) -> str | None:
    """Return the Douban batch runtime category covered by site contracts."""

    normalized = re.sub(r"\s+", " ", task or "").strip()
    lower = normalized.lower()
    if "douban" not in lower and "豆瓣" not in normalized:
        return None
    if "json" not in lower and "JSON" not in normalized:
        return None
    if re.search(
        r"Search for\s+\d+\s+different books on Douban",
        normalized,
        flags=re.IGNORECASE,
    ):
        return "douban_book_batch"
    if "book.douban.com" in lower and re.search(
        r"(reviews|书评)",
        normalized,
        flags=re.IGNORECASE,
    ):
        return "douban_book_batch"
    if "music.douban.com/subject/" in lower and re.search(
        r"(100\s+reviews|100\s+comments|100\s*条)",
        normalized,
        flags=re.IGNORECASE,
    ):
        return "douban_music_comment_batch"
    return None


def is_allowed_douban_url(url: str, *, required_host: str | None = None) -> bool:
    """Return whether a URL is an expected Douban HTTPS URL for runtime fetches."""

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = parsed.hostname or ""
    if required_host is not None:
        return host == required_host
    return host == "douban.com" or host.endswith(".douban.com")


def _runtime_client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=20.0,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        },
    )


def _get_json(client: httpx.Client, url: str, *, required_host: str) -> Any:
    if not is_allowed_douban_url(url, required_host=required_host):
        raise ValueError("Douban runtime URL is outside the allowed host")
    response = client.get(url)
    response.raise_for_status()
    if not is_allowed_douban_url(str(response.url), required_host=required_host):
        raise ValueError("Douban runtime response redirected outside the allowed host")
    return response.json()


def _get_text(client: httpx.Client, url: str, *, required_host: str) -> str:
    if not is_allowed_douban_url(url, required_host=required_host):
        raise ValueError("Douban runtime URL is outside the allowed host")
    response = client.get(url)
    response.raise_for_status()
    if not is_allowed_douban_url(str(response.url), required_host=required_host):
        raise ValueError("Douban runtime response redirected outside the allowed host")
    return response.text


def build_douban_book_batch_payload(task: str) -> dict[str, Any] | None:
    """Build a structured book batch payload from Douban APIs."""

    titles = re.findall(r'"([^"]+)"', task)
    if not titles:
        titles = re.findall(r"\u201c([^\u201d]+)\u201d", task)
    titles = [title.strip() for title in titles if title.strip()][:10]
    if not titles:
        return None

    books: list[dict[str, Any]] = []
    with _runtime_client() as client:
        for title in titles:
            suggest_url = f"https://book.douban.com/j/subject_suggest?q={quote(title)}"
            suggest_data = _get_json(
                client,
                suggest_url,
                required_host="book.douban.com",
            )
            suggest_compact = (
                _compact_douban_suggest_data(suggest_url, suggest_data) or {}
            )
            candidates = suggest_compact.get("items") or []
            if not candidates:
                books.append(
                    {"query": title, "error": "no Douban subject_suggest candidate"}
                )
                continue

            candidate = candidates[0]
            subject_url = str(candidate.get("url") or "")
            if not subject_url:
                books.append(
                    {
                        "query": title,
                        "candidate": candidate,
                        "error": "candidate missing subject URL",
                    }
                )
                continue
            if not is_allowed_douban_url(subject_url, required_host="book.douban.com"):
                books.append(
                    {
                        "query": title,
                        "candidate": candidate,
                        "error": "candidate subject URL is outside book.douban.com",
                    }
                )
                continue

            detail_html = _get_text(
                client,
                subject_url,
                required_host="book.douban.com",
            )
            detail = _compact_douban_subject_detail_data(subject_url, detail_html) or {}
            reviews_url = f"{subject_url.rstrip('/')}/reviews?sort=hotest"
            reviews_html = _get_text(
                client,
                reviews_url,
                required_host="book.douban.com",
            )
            reviews_compact = (
                _compact_douban_review_data(reviews_url, reviews_html) or {}
            )
            reviews = [
                {
                    "user": item.get("user"),
                    "title": item.get("title"),
                    "rating": item.get("rating"),
                    "usefulCount": item.get("usefulCount"),
                    "text": _compact_api_text(item.get("text"), 220),
                    "url": item.get("url"),
                }
                for item in (reviews_compact.get("items") or [])[:10]
                if isinstance(item, dict)
            ]

            books.append(
                {
                    "query": title,
                    "sourceUrl": subject_url,
                    "title": detail.get("title") or candidate.get("title"),
                    "author": detail.get("author") or candidate.get("author"),
                    "publisher": detail.get("publisher"),
                    "publishYear": detail.get("publishYear") or candidate.get("year"),
                    "rating": detail.get("rating"),
                    "ratingCount": detail.get("ratingCount"),
                    "isbn": detail.get("isbn"),
                    "reviews": reviews,
                }
            )
    return {"doubanBookBatch": True, "bookCount": len(books), "books": books}


def build_douban_music_comment_payload(task: str) -> dict[str, Any] | None:
    """Build a structured 100-comment music payload from Douban pages."""

    match = re.search(r"https://music\.douban\.com/subject/\d+/?", task)
    if not match:
        return None
    subject_url = match.group(0).rstrip("/") + "/"
    comments: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()

    with _runtime_client() as client:
        detail_html = _get_text(
            client,
            subject_url,
            required_host="music.douban.com",
        )
        detail = _compact_douban_subject_detail_data(subject_url, detail_html) or {}
        for start in range(0, 100, 10):
            comments_url = (
                f"{subject_url}comments/?start={start}&limit=20&status=P&sort=score"
            )
            comments_html = _get_text(
                client,
                comments_url,
                required_host="music.douban.com",
            )
            comments_compact = (
                _compact_douban_review_data(comments_url, comments_html) or {}
            )
            for item in comments_compact.get("items") or []:
                if not isinstance(item, dict):
                    continue
                key = (item.get("user"), item.get("text"))
                if key in seen:
                    continue
                seen.add(key)
                comments.append(
                    {
                        "index": len(comments) + 1,
                        "user": item.get("user"),
                        "date": item.get("date"),
                        "rating": item.get("rating"),
                        "text": _compact_api_text(item.get("text"), 180),
                        "sourceUrl": comments_url,
                    }
                )
                if len(comments) >= 100:
                    break
            if len(comments) >= 100:
                break

    return {
        "doubanMusicCommentBatch": True,
        "album": {
            "sourceUrl": subject_url,
            "title": detail.get("title"),
            "performer": detail.get("author"),
            "publisher": detail.get("publisher"),
            "releaseDate": detail.get("publishYear"),
            "rating": detail.get("rating"),
            "ratingCount": detail.get("ratingCount"),
            "isbnOrBarcode": detail.get("isbn"),
        },
        "reviewCount": len(comments),
        "reviews": comments,
    }


def build_douban_completion(task: str) -> RuntimeCompletion | None:
    """Return a deterministic Douban runtime completion when supported."""

    category = detect_douban_batch_completion_intent(task)
    if category == "douban_book_batch":
        payload = build_douban_book_batch_payload(task)
        filename = "douban_books_batch.json"
    elif category == "douban_music_comment_batch":
        payload = build_douban_music_comment_payload(task)
        filename = "douban_music_reviews_100.json"
    else:
        return None
    if not payload:
        return None
    return RuntimeCompletion(category=category, filename=filename, payload=payload)


async def build_douban_completion_async(task: str) -> RuntimeCompletion | None:
    """Async wrapper that keeps synchronous Douban HTTP work off the event loop."""

    return await asyncio.to_thread(build_douban_completion, task)
