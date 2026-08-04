from __future__ import annotations

from pathlib import Path


SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def test_skill_defines_browser_cli_control_and_ace_page_planes() -> None:
    text = SKILL_MD.read_text()

    assert "## Control plane and page plane" in text
    assert "browser-cli` for setup, authentication, contexts" in text
    assert "scripts/cdp.py` as the default page-operation plane" in text
    assert "routine navigation, reading, extraction, clicking" in text
    assert "Stop if ACE cannot attach" in text
    assert "Do not replay" in text


def test_skill_fast_path_uses_internal_lexmount_bridge() -> None:
    text = SKILL_MD.read_text()
    fast_path = text.split("## Fast path", 1)[1].split("## Setup", 1)[0]

    assert "browser-cli session create" in fast_path
    assert "--lexmount-session-id <lexmount-session-id> sessions" in fast_path
    assert "--lexmount-session-id <lexmount-session-id> attach <target-id>" in fast_path
    assert "navigate <ace-session-id> <url> --format=outline" in fast_path
    assert "browser-cli action" not in fast_path


def test_skill_distinguishes_all_three_session_identifiers() -> None:
    text = SKILL_MD.read_text()

    assert "Lexmount `session_id`" in text
    assert "CDP\n  `targetId`" in text
    assert "daemon-local ACE `sessionId`" in text
    assert "After `attach`, keep the returned ACE `sessionId`" in text


def test_skill_routes_only_specialized_operations_to_browser_cli_action() -> None:
    text = SKILL_MD.read_text()
    fallback = text.split("## Specialized browser-cli fallbacks", 1)[1]

    assert "Screenshots" in fallback
    assert "File upload" in fallback
    assert "Cookie" in fallback
    assert "Complex semantic waits" in fallback
    assert "page diagnostics" in fallback
    assert "Do not fall back after an ACE timeout" in fallback


def test_skill_documents_contexts_cleanup_and_secret_hygiene() -> None:
    text = SKILL_MD.read_text()

    assert "persistent_login_state" in text
    assert "availability" in text
    assert "locked" in text
    assert "read_write" in text
    assert "read_only" in text
    assert "reverse attachment order" in text
    assert "Never ask the user to paste API keys" in text
    assert "Do not\nuse `--reveal-connect-url` manually" in text
