from __future__ import annotations

from pathlib import Path


DOC = Path(__file__).resolve().parents[1] / "docs" / "connect-from-codex.md"


def test_connect_from_codex_doc_matches_current_cli_contracts() -> None:
    text = DOC.read_text()

    assert "https://browser.lexmount.cn/connect/codex" in text
    assert "browser-cli auth login" in text
    assert "browser-cli auth login --open" in text
    assert "browser-cli auth token-info" in text
    assert "browser-cli auth logout" in text
    assert "open_result" in text
    assert "revoke_available=false" in text
    assert "browser-cli auth export-env" in text
    assert "browser-cli doctor --json" in text
    assert "browser-cli doctor --smoke-session" in text
    assert "browser_smoke_session" in text
    assert "ready_for_browser_actions" in text
    assert "repair_plan.commands" in text
    assert "repair_plan.env" in text
    assert "repair_plan.guidance" in text
    assert "browser-cli context pick --metadata-json" in text
    assert "browser-cli session create --context-metadata-json" in text
    assert 'availability: "available"' in text
    assert 'availability: "locked"' in text
    assert 'availability: "unavailable"' in text
    assert "context resolve" not in text
    assert "environment.configured" not in text


def test_connect_from_codex_doc_keeps_required_site_capabilities() -> None:
    text = DOC.read_text()

    required_phrases = [
        "Project ID with copy button",
        "Create scoped API key button",
        "Revoke button for each agent key",
        "Expiration display",
        "Copyable env block",
        "Safety reminder: paste into local shell, not chat",
        "verification commands",
        "requested scopes",
        "Device-code tokens should be scoped, time-limited",
    ]

    for phrase in required_phrases:
        assert phrase in text
