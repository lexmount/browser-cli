from __future__ import annotations

from pathlib import Path

from lex_browser_runtime.browser.cases import validate_case_file

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_example_case_files_validate() -> None:
    case_files = sorted((REPO_ROOT / "examples" / "cases").glob("*.yaml"))

    assert case_files
    for case_file in case_files:
        result = validate_case_file(case_file)
        assert result.valid, f"{case_file}: {result.errors}"
        assert result.step_count > 0


def test_agent_playbook_uses_current_context_and_doctor_contracts() -> None:
    text = (REPO_ROOT / "examples" / "agent-playbook.md").read_text()

    assert "browser-cli doctor --json" in text
    assert "browser-cli doctor --smoke-session" in text
    assert "ready_for_browser_actions" in text
    assert "browser_smoke_session.status" in text
    assert "`fix.commands`" in text
    assert "repair_plan.commands" in text
    assert "browser-cli context pick --metadata-json" in text
    assert "browser-cli session create --context-metadata-json" in text
    assert "`availability` is `locked` or `unavailable`" in text
    assert "wait-role" in text
    assert "click-role" in text
    assert "interactive-snapshot" in text
    assert "context resolve" not in text
