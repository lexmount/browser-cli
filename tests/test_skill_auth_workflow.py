from __future__ import annotations

from pathlib import Path


SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def _normalized_skill_text() -> str:
    return " ".join(SKILL_MD.read_text().split())


def test_skill_auth_flow_starts_with_status_json() -> None:
    text = SKILL_MD.read_text()
    normalized = _normalized_skill_text()

    assert "browser-cli auth status" in text
    assert "parse JSON" in normalized
    assert "If `configured` is true" in text
    assert "`missing` includes `LEXMOUNT_API_KEY` or `LEXMOUNT_PROJECT_ID`" in text


def test_skill_auth_flow_handles_login_open_safely() -> None:
    normalized = _normalized_skill_text()

    assert "browser-cli auth login" in normalized
    assert "browser-cli auth login --open" in normalized
    assert "only when it is appropriate to open the user's local browser" in normalized
    assert "otherwise show the returned `authorization_url`" in normalized


def test_skill_auth_flow_protects_export_env_secrets() -> None:
    normalized = _normalized_skill_text()

    assert "browser-cli auth export-env" in normalized
    assert "browser-cli auth export-env --reveal-secrets" in normalized
    assert "only in a trusted local shell" in normalized
    assert "`usable: false`, `masked: true`, or `contains_secrets: true`" in normalized
    assert "not to paste output into chat, logs, docs, tests, or commits" in normalized
