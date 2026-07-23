"""Registry utility functions."""

from __future__ import annotations

import re
from urllib.parse import urlparse

SAFE_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
URL_TRAILING_PUNCTUATION = ".,;:!?，。；：！？"
DOMAIN_ALIASES: dict[str, tuple[str, ...]] = {
    "ebay.com": ("ebay",),
    "gamespot.com": ("gamespot", "game spot"),
    "le.com": ("le.com", "letv", "le tv", "乐视", "乐视视频"),
    "nih.gov": ("nih", "ncbi", "pmc", "pubmed central", "pubmed"),
}

TWO_PART_TLDS = {
    "co.uk",
    "co.jp",
    "co.nz",
    "co.za",
    "co.kr",
    "co.in",
    "com.au",
    "com.br",
    "com.cn",
    "com.hk",
    "com.tw",
    "com.ar",
    "com.mx",
    "com.sg",
    "org.uk",
    "gov.uk",
    "net.au",
    "net.cn",
    "org.cn",
    "gov.cn",
}


def root_domain(url_or_domain: str) -> str:
    """Extract a registrable root domain from a URL or hostname."""

    host = url_or_domain.strip().lower().rstrip(".")
    if "://" in url_or_domain:
        host = (urlparse(url_or_domain).hostname or url_or_domain).strip().lower()
        host = host.rstrip(".")
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_PART_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def safe_domain_name(domain: str) -> str:
    """Return a root domain that is safe for use as a filename."""

    domain_name = root_domain(domain)
    if not SAFE_DOMAIN_RE.fullmatch(domain_name) or any(
        not part for part in domain_name.split(".")
    ):
        raise ValueError(f"Invalid adapter domain: {domain!r}")
    return domain_name


def safe_host_name(url_or_domain: str) -> str:
    """Return the exact host name when a subdomain-specific adapter exists."""

    host = url_or_domain.strip().lower().rstrip(".")
    if "://" in url_or_domain:
        host = (urlparse(url_or_domain).hostname or "").strip().lower().rstrip(".")
    if "/" in host:
        host = host.split("/", 1)[0]
    if not SAFE_DOMAIN_RE.fullmatch(host) or any(not part for part in host.split(".")):
        raise ValueError(f"Invalid adapter domain: {url_or_domain!r}")
    return host


def task_domains(task: str) -> set[str]:
    """Extract exact hosts and root domains from explicit URLs in a task."""

    domains: set[str] = set()
    for raw_url in re.findall(r"https?://[^\s\"'>)]+", task or ""):
        url = raw_url.rstrip(URL_TRAILING_PUNCTUATION)
        host = urlparse(url).hostname or ""
        if host:
            domains.add(host.lower().rstrip("."))
            domains.add(root_domain(host))
    return {domain for domain in domains if domain}


def domain_aliases(domain: str) -> set[str]:
    """Return conservative textual aliases that can safely identify a known site."""

    root = root_domain(domain)
    aliases = {root}
    label = root.split(".", 1)[0]
    if len(label) >= 4:
        aliases.add(label)
        aliases.add(label.replace("-", " "))
    aliases.update(DOMAIN_ALIASES.get(root, ()))
    return {alias.lower() for alias in aliases if alias}


def contains_alias(task: str, alias: str) -> bool:
    """Return whether *alias* appears as a standalone site token in *task*."""

    normalized_task = (task or "").lower()
    normalized_alias = alias.lower().strip()
    if not normalized_alias:
        return False
    if "." in normalized_alias or any(
        "\u4e00" <= char <= "\u9fff" for char in normalized_alias
    ):
        return normalized_alias in normalized_task
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])",
            normalized_task,
        )
    )


def task_notice_domains(task: str, known_domains: set[str]) -> set[str]:
    """Match known site notices from explicit URLs plus conservative site aliases."""

    domains = set(task_domains(task))
    for domain in known_domains:
        root = root_domain(domain)
        if root in domains:
            continue
        if any(contains_alias(task, alias) for alias in domain_aliases(root)):
            domains.add(root)
    return domains


def coerce_hint_list(value: object) -> list[str]:
    """Coerce a YAML hint value into a normalized list."""

    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
