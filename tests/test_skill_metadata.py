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
    assert "auth status" in prompt
    assert "doctor" in prompt
    assert "context/session" in prompt
    assert "wait for roles or selectors" in prompt
    assert "browser actions" in prompt


def test_openai_yaml_stays_aligned_with_skill_name() -> None:
    skill_text = SKILL_MD.read_text()
    prompt = _quoted_yaml_value(OPENAI_YAML.read_text(), "default_prompt")

    assert "name: browser-cli" in skill_text
    assert "$browser-cli" in prompt
