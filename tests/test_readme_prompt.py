from __future__ import annotations

import re
from pathlib import Path


README = Path(__file__).resolve().parents[1] / "README.md"


def _codex_install_prompt() -> str:
    text = README.read_text()
    start = text.index("## Codex Install Prompt")
    end = text.index("## Manual Install")
    return text[start:end]


def _codex_install_prompt_steps() -> str:
    prompt = _codex_install_prompt()
    start = prompt.index("步骤：")
    end = prompt.index("完成后告诉我：")
    return prompt[start:end]


def test_codex_install_prompt_keeps_secrets_out_of_chat() -> None:
    prompt = _codex_install_prompt()

    assert "不要让我把 API Key 或 Project ID 粘贴到聊天里" in prompt
    assert "revealed secret 永远不要复制到聊天" in prompt
    assert "不要复述任何 secret 的真实值" in prompt
    assert (
        "不要在聊天回复、日志、README、测试、提交记录或 PR 描述里输出 API Key" in prompt
    )


def test_codex_install_prompt_points_to_browser_console_and_auth_helpers() -> None:
    prompt = _codex_install_prompt()

    assert "https://browser.lexmount.cn" in prompt
    assert "https://browser.lexmount.cn/connect/codex" in prompt
    assert "browser-cli --version" in prompt
    assert "browser-cli version" in prompt
    assert "browser-cli auth status" in prompt
    assert "browser-cli auth connect-requirements" in prompt
    assert "browser-cli auth login" in prompt
    assert "browser-cli commands --workflows-only" in prompt
    assert "browser-cli reference list" in prompt
    assert "browser-cli reference get --id action_playbook --metadata-only" in prompt
    assert "browser-cli reference get --id action_playbook" in prompt
    assert "不要先写自定义 Playwright/JS" in prompt
    assert "browser-cli commands --workflow setup_and_verify" in prompt
    assert (
        "browser-cli commands --workflow connect_from_codex_site_requirements" in prompt
    )
    assert "browser-cli commands --workflow connect_from_codex_auth" in prompt
    assert "browser-cli commands --workflow device_code_auth" in prompt
    assert "browser-cli commands --workflow scoped_token_lifecycle" in prompt
    assert "browser-cli commands --workflow session_recovery" in prompt
    assert "browser-cli commands --workflow one_off_page_task" in prompt
    assert "browser-cli commands --workflow case_file_task" in prompt
    assert "browser-cli commands --workflow persistent_login_state" in prompt
    assert "browser-cli commands --workflow form_interaction" in prompt
    assert "browser-cli commands --workflow interactive_targeting" in prompt
    assert "browser-cli commands --workflow page_diagnostics" in prompt
    assert "workflow.steps" in prompt
    assert "connect_from_codex.url" in prompt
    assert "browser-cli auth export-env" in prompt
    assert "browser-cli auth export-env --reveal-secrets" in prompt
    assert "browser-cli doctor --json" in prompt
    assert "browser-cli doctor --smoke-session" in prompt
    assert "ready_for_browser_actions=true" in prompt
    assert "browser_smoke_session.status 应该是 pass" in prompt
    assert "browser-cli session list" in prompt


def test_codex_install_prompt_has_sequential_steps() -> None:
    steps = _codex_install_prompt_steps()
    numbers = [int(match) for match in re.findall(r"(?m)^(\d+)\. ", steps)]

    assert numbers == list(range(1, 20))


def test_codex_install_prompt_mentions_project_and_key_validation() -> None:
    prompt = _codex_install_prompt()

    assert "Project ID" in prompt
    assert "LEXMOUNT_PROJECT_ID" in prompt
    assert "LEXMOUNT_API_KEY" in prompt
    assert "Project 是否和 LEXMOUNT_PROJECT_ID 一致" in prompt
    assert "API Key 是否已过期、被 revoke" in prompt
    assert "scoped API Key" in prompt
    assert "created=true、closed=true" in prompt
