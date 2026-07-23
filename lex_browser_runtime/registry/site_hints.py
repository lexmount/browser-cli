"""Load site hints and runtime strategies from YAML."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from lex_browser_runtime.registry.models import RuntimeStrategy, SiteNotice
from lex_browser_runtime.registry.utils import coerce_hint_list, root_domain

logger = logging.getLogger(__name__)


def _load_runtime_strategies(value: object, site_pattern: str) -> list[RuntimeStrategy]:
    if not isinstance(value, list):
        return []
    strategies: list[RuntimeStrategy] = []
    for raw_strategy in value:
        if not isinstance(raw_strategy, dict):
            continue
        payload = dict(raw_strategy)
        payload.setdefault("site_pattern", site_pattern)
        try:
            strategies.append(RuntimeStrategy(**payload))
        except Exception as exc:
            logger.debug(
                "Skipping malformed runtime strategy for %s: %s", site_pattern, exc
            )
    return strategies


def load_site_hints(path: str | Path | None) -> dict[str, SiteNotice]:
    """Load per-site hints and strategy metadata from a YAML file."""

    if not path:
        return {}
    hints_path = Path(path)
    if not hints_path.exists():
        logger.warning("Site hints file not found: %s", hints_path)
        return {}
    raw = yaml.safe_load(hints_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, SiteNotice] = {}
    for key, value in raw.items():
        site_pattern = str(key)
        if isinstance(value, dict):
            hints = coerce_hint_list(value.get("hints") or value.get("hint"))
            strategies = _load_runtime_strategies(value.get("strategies"), site_pattern)
        else:
            hints = coerce_hint_list(value)
            strategies = []
        if hints or strategies:
            result[root_domain(site_pattern)] = SiteNotice(
                hints=hints,
                strategies=strategies,
            )
    return result
