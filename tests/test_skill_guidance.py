from __future__ import annotations

from pathlib import Path


SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def _normalized_skill_text() -> str:
    return " ".join(SKILL_MD.read_text().split())


def test_skill_prefers_cli_workflow_over_custom_playwright() -> None:
    text = SKILL_MD.read_text()

    assert "Use `browser-cli` as the primary interface" in text
    assert "Prefer CLI commands and JSON output" in text
    assert "browser-cli commands --group action" in text
    assert "browser-cli action guide --task <task>" in text
    assert "before writing custom JavaScript" in text
    assert "ad hoc Playwright scripts" in text
    assert "Write custom Playwright only when the CLI cannot express the task" in text


def test_skill_guides_safe_one_off_sessions() -> None:
    text = SKILL_MD.read_text()
    normalized = _normalized_skill_text()

    assert "browser-cli session create" in text
    assert "browser-cli action open-url --session-id <session_id> --url <url>" in text
    assert "browser-cli action snapshot --session-id <session_id>" in text
    assert "browser-cli action wait-selector --session-id <session_id>" in text
    assert "browser-cli session close --session-id <session_id>" in text
    assert "Always close temporary sessions" in normalized


def test_skill_guides_context_reuse_modes() -> None:
    text = SKILL_MD.read_text()

    assert "browser-cli context create" in text
    assert "browser-cli session create --context-id <context_id>" in text
    assert "`read_write` for login/setup work" in text
    assert "`read_only` when inspecting an existing logged-in state" in text
    assert "Before deleting a context" in text


def test_skill_guides_json_failures_and_secret_hygiene() -> None:
    text = SKILL_MD.read_text()
    normalized = _normalized_skill_text()

    assert "parse the JSON error first" in text
    assert "`error`, `message`, and command-specific fields" in text
    assert "For `commands`, use the parser-backed catalog" in text
    assert "safe to include `--json` at the top level or after subcommands" in text
    assert "Do not paste API keys, Project IDs, or full direct" in text
    assert "Use reveal flags only for local debugging in a trusted shell" in normalized
