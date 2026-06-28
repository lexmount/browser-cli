from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OPENAI_YAML = REPO_ROOT / "agents" / "openai.yaml"
SKILL_MD = REPO_ROOT / "SKILL.md"


def _quoted_yaml_value(text: str, key: str) -> str:
    prefix = f"  {key}: "
    for line in text.splitlines():
        if line.startswith(prefix):
            value = line.removeprefix(prefix)
            assert value.startswith('"')
            assert value.endswith('"')
            return value[1:-1]
    raise AssertionError(f"missing interface.{key}")


def test_openai_yaml_exposes_browser_cli_skill_metadata() -> None:
    text = OPENAI_YAML.read_text()

    assert text.startswith("interface:\n")
    assert _quoted_yaml_value(text, "display_name") == "Lexmount Browser CLI"
    assert (
        _quoted_yaml_value(text, "short_description")
        == "Control Lexmount browsers from Codex"
    )


def test_openai_yaml_default_prompt_matches_skill_workflow() -> None:
    prompt = _quoted_yaml_value(OPENAI_YAML.read_text(), "default_prompt")

    assert "$browser-cli" in prompt
    assert "version JSON" in prompt
    assert "auth status" in prompt
    assert "scopes" in prompt
    assert "refresh" in prompt
    assert "login" in prompt
    assert "connect-requirements" in prompt
    assert "device-code --wait only after approval instructions are visible" in prompt
    assert "doctor --json" in prompt
    assert "commands" in prompt
    assert "commands --workflow" in prompt
    assert "connect_from_codex_site_requirements" in prompt
    assert "device_code_auth" in prompt
    assert "scoped_token_lifecycle" in prompt
    assert "session_recovery" in prompt
    assert "case_file_task" in prompt
    assert "case schema" in prompt
    assert "scaffold/validate/run case files" in prompt
    assert "form_interaction" in prompt
    assert "interactive_targeting" in prompt
    assert "content_extraction" in prompt
    assert "browser_state_management" in prompt
    assert "file_upload" in prompt
    assert "dialog_frame_handling" in prompt
    assert "navigation_flow" in prompt
    assert "menu_keyboard_flow" in prompt
    assert "state_waits" in prompt
    assert "page_diagnostics" in prompt
    assert "workflow read arrays" in prompt
    assert "required_runtime_auth" in prompt
    assert "before treating scoped-token or device-code auth as usable" in prompt
    assert "reference get" in prompt
    assert "example get" in prompt
    assert "action guide" in prompt
    assert "custom JavaScript" in prompt
    assert "status-check/dry-run" in prompt
    assert "persistent contexts" in prompt
    assert "local_registry metadata diagnostics" in prompt
    assert "sessions/actions" in prompt
    assert "JSON output" in prompt
    assert "secrets" in prompt
    assert "chat" in prompt


def test_openai_yaml_stays_aligned_with_skill_name() -> None:
    skill_text = SKILL_MD.read_text()
    prompt = _quoted_yaml_value(OPENAI_YAML.read_text(), "default_prompt")

    assert "name: browser-cli" in skill_text
    assert "$browser-cli" in prompt
