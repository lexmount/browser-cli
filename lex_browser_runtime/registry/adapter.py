"""Adapter registry for known runtime API/page contracts."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from lex_browser_runtime.registry.models import (
    AdapterEndpoint,
    CapabilityMatch,
    SiteNotice,
)
from lex_browser_runtime.registry.site_hints import load_site_hints
from lex_browser_runtime.registry.utils import (
    root_domain,
    safe_domain_name,
    safe_host_name,
    task_domains,
    task_notice_domains,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent / "data"
_DEFAULT_ADAPTERS_DIR = _DATA_DIR / "adapters"
_DEFAULT_SITE_HINTS_PATH = _DATA_DIR / "site_hints.yaml"
_ADAPTERS_DIR_ENV = "LEX_BROWSER_RUNTIME_ADAPTERS_DIR"
_SITE_HINTS_PATH_ENV = "LEX_BROWSER_RUNTIME_SITE_HINTS_PATH"
_DISABLE_DEFAULT_REGISTRY_ENV = "LEX_BROWSER_RUNTIME_DISABLE_DEFAULT_REGISTRY"


def default_adapters_dir() -> Path | None:
    """Return the packaged adapter registry directory when available."""

    return _DEFAULT_ADAPTERS_DIR if _DEFAULT_ADAPTERS_DIR.exists() else None


def default_site_hints_path() -> Path | None:
    """Return the packaged site-hints registry file when available."""

    return _DEFAULT_SITE_HINTS_PATH if _DEFAULT_SITE_HINTS_PATH.exists() else None


def _default_registry_disabled() -> bool:
    return os.getenv(_DISABLE_DEFAULT_REGISTRY_ENV, "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _resolve_adapters_dir(adapters_dir: str | Path | None) -> Path | None:
    if adapters_dir:
        return Path(adapters_dir)
    env_value = os.getenv(_ADAPTERS_DIR_ENV)
    if env_value:
        return Path(env_value)
    if _default_registry_disabled():
        return None
    return default_adapters_dir()


def _resolve_site_hints_path(site_hints_path: str | Path | None) -> Path | None:
    if site_hints_path:
        return Path(site_hints_path)
    env_value = os.getenv(_SITE_HINTS_PATH_ENV)
    if env_value:
        return Path(env_value)
    if _default_registry_disabled():
        return None
    return default_site_hints_path()


class AdapterRegistry:
    """Load adapters and site hints from data files."""

    def __init__(
        self,
        adapters_dir: str | Path | None = None,
        site_hints_path: str | Path | None = None,
    ) -> None:
        self._adapters_dir = _resolve_adapters_dir(adapters_dir)
        self._site_hints_path = _resolve_site_hints_path(site_hints_path)

    @property
    def adapters_dir(self) -> Path | None:
        """Return the adapter directory, if configured."""

        return self._adapters_dir

    @property
    def site_hints_path(self) -> Path | None:
        """Return the site-hints file, if configured."""

        return self._site_hints_path

    def _domain_file(self, domain: str) -> Path:
        if self._adapters_dir is None:
            raise ValueError("adapters_dir is not configured")
        return self._adapters_dir / f"{safe_domain_name(domain)}.json"

    def _candidate_files(self, url_or_domain: str) -> list[Path]:
        if self._adapters_dir is None:
            return []
        paths: list[Path] = []
        try:
            host_name = safe_host_name(url_or_domain)
            paths.append(self._adapters_dir / f"{host_name}.json")
        except ValueError:
            pass
        root_path = self._domain_file(url_or_domain)
        if root_path not in paths:
            paths.append(root_path)
        return paths

    def _load_path(self, path: Path, default_domain: str) -> dict[str, object]:
        if not path.exists():
            return {"domain": root_domain(default_domain), "endpoints": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to parse adapter file %s: %s", path, exc)
            return {"domain": root_domain(default_domain), "endpoints": []}
        if not isinstance(payload, dict):
            return {"domain": root_domain(default_domain), "endpoints": []}
        return payload

    def find(self, url_or_domain: str) -> list[AdapterEndpoint]:
        """Return known endpoints for exact-host and root-domain matches."""

        endpoints: list[AdapterEndpoint] = []
        seen_patterns: set[str] = set()
        for path in self._candidate_files(url_or_domain):
            data = self._load_path(path, url_or_domain)
            raw_endpoints = data.get("endpoints", [])
            if not isinstance(raw_endpoints, list):
                continue
            for raw in raw_endpoints:
                if not isinstance(raw, dict):
                    continue
                try:
                    endpoint = AdapterEndpoint(**raw)
                except Exception as exc:
                    logger.debug("Skipping malformed endpoint in %s: %s", path, exc)
                    continue
                if endpoint.url_pattern in seen_patterns:
                    continue
                endpoints.append(endpoint)
                seen_patterns.add(endpoint.url_pattern)
        return endpoints

    def find_matching(self, url: str, method: str = "GET") -> list[AdapterEndpoint]:
        """Return adapters whose method and URL pattern match a concrete URL."""

        normalized_method = method.upper()
        matches = [
            endpoint
            for endpoint in self.find(url)
            if endpoint.method.upper() == normalized_method and endpoint.matches(url)
        ]
        if matches:
            return matches
        for endpoints in self.load_all().values():
            matches.extend(
                endpoint
                for endpoint in endpoints
                if endpoint.method.upper() == normalized_method
                and endpoint.matches(url)
            )
        return matches

    def load_all(self) -> dict[str, list[AdapterEndpoint]]:
        """Load every adapter file under the configured directory."""

        result: dict[str, list[AdapterEndpoint]] = {}
        if self._adapters_dir is None or not self._adapters_dir.exists():
            return result
        for path in self._adapters_dir.glob("*.json"):
            data = self._load_path(path, path.stem)
            raw_endpoints = data.get("endpoints", [])
            endpoints: list[AdapterEndpoint] = []
            if isinstance(raw_endpoints, list):
                for raw in raw_endpoints:
                    if not isinstance(raw, dict):
                        continue
                    try:
                        endpoints.append(AdapterEndpoint(**raw))
                    except Exception as exc:
                        logger.debug("Skipping malformed endpoint in %s: %s", path, exc)
            result[path.stem] = endpoints
        return result

    def load_site_notices(self) -> dict[str, SiteNotice]:
        """Load the configured site-hint database."""

        return load_site_hints(self._site_hints_path)

    def match(self, *, task: str = "", url: str | None = None) -> CapabilityMatch:
        """Match adapters and site notices for a task and optional URL."""

        site_notices = load_site_hints(self._site_hints_path)
        domains = set(task_domains(task))
        domains.update(task_notice_domains(task, set(site_notices)))
        if url:
            domains.add(root_domain(url))
            try:
                domains.add(safe_host_name(url))
            except ValueError:
                pass

        adapters: list[AdapterEndpoint] = []
        seen_patterns: set[str] = set()
        matched_notices = {}

        for domain in sorted(domains):
            for endpoint in self.find(domain):
                if endpoint.url_pattern not in seen_patterns:
                    adapters.append(endpoint)
                    seen_patterns.add(endpoint.url_pattern)
            root = root_domain(domain)
            notice = site_notices.get(root)
            if notice is not None:
                matched_notices[root] = notice

        return CapabilityMatch(
            domains=sorted(domains),
            adapters=adapters,
            site_notices=matched_notices,
        )
