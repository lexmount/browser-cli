from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _quoted_yaml_value(text: str, key: str) -> str:
    match = re.search(rf"^  {re.escape(key)}: \"(.*)\"$", text, re.MULTILINE)
    assert match, f"missing quoted interface.{key}"
    return match.group(1)


def test_openai_skill_metadata_is_agent_friendly() -> None:
    text = (REPO_ROOT / "agents" / "openai.yaml").read_text()

    assert text.startswith("interface:\n")
    assert "dependencies:" not in text
    assert "policy:" not in text

    assert _quoted_yaml_value(text, "display_name") == "Lexmount Browser CLI"

    short_description = _quoted_yaml_value(text, "short_description")
    assert 25 <= len(short_description) <= 64
    assert short_description == "Control Lexmount browsers from Codex"

    default_prompt = _quoted_yaml_value(text, "default_prompt")
    assert "$browser-cli" in default_prompt
    assert "Lexmount browser session" in default_prompt


def test_skill_name_matches_metadata_prompt() -> None:
    skill_text = (REPO_ROOT / "SKILL.md").read_text()
    name_match = re.search(r"^name: ([a-z0-9-]+)$", skill_text, re.MULTILINE)

    assert name_match
    assert (
        f"${name_match.group(1)}" in (REPO_ROOT / "agents" / "openai.yaml").read_text()
    )
