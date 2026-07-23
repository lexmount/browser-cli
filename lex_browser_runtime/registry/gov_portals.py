"""Government portal data loading and matching for policy research."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any
from urllib.parse import urlparse

from lex_browser_runtime.registry.utils import safe_host_name


GOV_PORTALS_RESOURCE = "data/gov_portals.json"


@dataclass(frozen=True, slots=True)
class GovPortalRecord:
    """One packaged local government portal record."""

    scope: str
    province: str
    level: str
    area: str
    name: str
    url: str

    @property
    def host(self) -> str:
        """Return the normalized host used for site-restricted search."""

        return urlparse(self.url).netloc.lower().removeprefix("www.")

    @property
    def source_id(self) -> str:
        """Return a deterministic research source id for this portal."""

        return f"gov-policy-{safe_host_name(self.url)}"


def _record_from_payload(payload: dict[str, Any]) -> GovPortalRecord:
    return GovPortalRecord(
        scope=str(payload["scope"]),
        province=str(payload["province"]),
        level=str(payload["level"]),
        area=str(payload["area"]),
        name=str(payload["name"]),
        url=_normalize_portal_url(str(payload["url"])),
    )


def _normalize_portal_url(url: str) -> str:
    normalized = url.strip().replace("：", ":")
    for duplicate_prefix in ("http://http://", "https://https://"):
        if normalized.startswith(duplicate_prefix):
            normalized = normalized.removeprefix(
                duplicate_prefix.split("://", 1)[0] + "://"
            )
    return normalized


@lru_cache(maxsize=1)
def load_gov_portals() -> tuple[GovPortalRecord, ...]:
    """Load packaged local government portal records."""

    resource = resources.files("lex_browser_runtime.registry").joinpath(
        GOV_PORTALS_RESOURCE
    )
    payload = json.loads(resource.read_text(encoding="utf-8"))
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("gov_portals.json must contain a records list")
    return tuple(_record_from_payload(record) for record in records)


def gov_portal_query_terms(record: GovPortalRecord) -> set[str]:
    """Return conservative text terms that identify one portal's region."""

    terms = {record.province, record.area, record.name}
    suffixes = (
        "人民政府门户网站",
        "人民政府门户",
        "人民政府",
        "政府门户网站",
        "政府门户",
        "政府在线",
        "门户网站",
        "政务网",
        "官网",
        "省",
        "市",
        "区",
        "县",
        "旗",
        "盟",
        "州",
        "地区",
    )
    for value in tuple(terms):
        stripped = value
        changed = True
        while changed:
            changed = False
            for suffix in suffixes:
                if stripped.endswith(suffix) and len(stripped) > len(suffix) + 1:
                    stripped = stripped[: -len(suffix)]
                    if len(stripped) >= 2:
                        terms.add(stripped)
                    changed = True
        if len(stripped) >= 2:
            terms.add(stripped)
    return {term for term in terms if len(term) >= 2}


def _match_score(query: str, record: GovPortalRecord) -> int:
    score = 0
    area_terms = gov_portal_query_terms(record)
    matched_area = any(term in query for term in area_terms)
    matched_province = record.province in query
    if not matched_area and not matched_province:
        return 0
    if matched_area:
        score += 100
    if matched_province:
        score += 30
    if record.scope == "city_official":
        score += 20
    if "城市本级" in record.level:
        score += 15
    if "省级" in record.level:
        score += 10
    return score


def match_gov_portals(query: str, *, limit: int = 10) -> list[GovPortalRecord]:
    """Return the best matching government portals for a policy query."""

    scored: list[tuple[int, int, GovPortalRecord]] = []
    for index, record in enumerate(load_gov_portals()):
        if not record.host:
            continue
        score = _match_score(query, record)
        if score <= 0:
            continue
        scored.append((score, -index, record))

    scored.sort(reverse=True)
    selected: list[GovPortalRecord] = []
    seen_source_ids: set[str] = set()
    for _, _, record in scored:
        if record.source_id in seen_source_ids:
            continue
        seen_source_ids.add(record.source_id)
        selected.append(record)
        if len(selected) >= limit:
            break
    return selected
