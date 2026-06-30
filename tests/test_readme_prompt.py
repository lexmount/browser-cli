from __future__ import annotations

import re
from pathlib import Path


README = Path(__file__).resolve().parents[1] / "README.md"
DOCS = Path(__file__).resolve().parents[1] / "docs"


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
    assert "browser-cli auth scopes --include-site-contract" in prompt
    assert "browser-cli auth connect-requirements" in prompt
    assert "browser-cli auth connect-requirements --checklist" in prompt
    assert "browser_site_contract.required_runtime_auth" in prompt
    assert "required_runtime_auth" in prompt
    assert "runtime auth 阻塞项已处理" in prompt
    assert "browser-cli auth login" in prompt
    assert "browser-cli auth login --device-code" in prompt
    assert "browser-cli auth login --device-code --wait" in prompt
    assert "available=false 时使用 manual env fallback" in prompt
    assert "browser-cli skill status" in prompt
    assert "browser-cli skill install --force" in prompt
    assert "browser-cli commands --workflows-only" in prompt
    assert "browser-cli reference list" in prompt
    assert "browser-cli reference get --id quickstart --metadata-only" in prompt
    assert "browser-cli reference get --id quickstart" in prompt
    assert "browser-cli reference get --id connect_from_codex --metadata-only" in prompt
    assert "browser-cli reference get --id connect_from_codex" in prompt
    assert "browser-cli reference get --id usable_status --metadata-only" in prompt
    assert "browser-cli reference get --id usable_status" in prompt
    assert "browser-cli reference get --id action_playbook --metadata-only" in prompt
    assert "browser-cli reference get --id action_playbook" in prompt
    assert "browser-cli example list" in prompt
    assert "browser-cli example get --id agent_playbook --metadata-only" in prompt
    assert (
        "browser-cli example get --id setup_verification_playbook --metadata-only"
        in prompt
    )
    assert "browser-cli example get --id page_inspection_case" in prompt
    assert "browser-cli example get --id form_fill_case" in prompt
    assert "browser-cli example get --id interactive_targeting_case" in prompt
    assert "browser-cli example get --id page_diagnostics_case" in prompt
    assert "browser-cli case schema" in prompt
    assert "browser-cli case scaffold --template page-inspection" in prompt
    assert "browser-cli case scaffold --template form-fill" in prompt
    assert "browser-cli case scaffold --template interactive-targeting" in prompt
    assert "browser-cli case scaffold --template page-diagnostics" in prompt
    assert "不要先写自定义 Playwright/JS" in prompt
    assert "browser-cli commands --workflow setup_and_verify" in prompt
    assert (
        "browser-cli commands --workflow connect_from_codex_site_requirements" in prompt
    )
    assert "browser-cli commands --workflow connect_from_codex_auth" in prompt
    assert "browser-cli commands --workflow device_code_auth" in prompt
    assert "browser-cli commands --workflow scoped_token_lifecycle" in prompt
    assert "browser-cli commands --workflow session_recovery" in prompt
    assert "browser-cli commands --workflow first_browser_task" in prompt
    assert "browser-cli commands --workflow agent_browser_primitives" in prompt
    assert "browser-cli commands --workflow one_off_page_task" in prompt
    assert "browser-cli commands --workflow case_file_task" in prompt
    assert "browser-cli commands --workflow persistent_login_state" in prompt
    assert "browser-cli commands --workflow form_interaction" in prompt
    assert "browser-cli commands --workflow interactive_targeting" in prompt
    assert "browser-cli commands --workflow content_extraction" in prompt
    assert "browser-cli commands --workflow browser_state_management" in prompt
    assert "browser-cli commands --workflow file_upload" in prompt
    assert "browser-cli commands --workflow dialog_frame_handling" in prompt
    assert "browser-cli commands --workflow navigation_flow" in prompt
    assert "browser-cli commands --workflow link_navigation" in prompt
    assert "browser-cli commands --workflow visual_capture" in prompt
    assert "browser-cli commands --workflow semantic_waits" in prompt
    assert "browser-cli commands --workflow menu_keyboard_flow" in prompt
    assert "browser-cli commands --workflow mouse_interaction" in prompt
    assert "browser-cli commands --workflow state_waits" in prompt
    assert "browser-cli commands --workflow page_diagnostics" in prompt
    assert "browser-cli action guide --names-only" in prompt
    assert (
        "browser-cli action observe --session-id <session_id> --surface interactive --surface text"
        in prompt
    )
    assert (
        'browser-cli action act --session-id <session_id> --kind click --role button --name "<name>"'
        in prompt
    )
    assert (
        "browser-cli action extract --session-id <session_id> --surface text --surface links --selector main"
        in prompt
    )
    assert "browser-cli action guide --task form_interaction" in prompt
    assert "browser-cli action guide --task interactive_targeting" in prompt
    assert "browser-cli action guide --task content_extraction" in prompt
    assert "browser-cli action guide --task browser_state_management" in prompt
    assert "browser-cli action guide --task file_upload" in prompt
    assert "browser-cli action guide --task dialog_frame_handling" in prompt
    assert "browser-cli action guide --task navigation_flow" in prompt
    assert "browser-cli action guide --task link_navigation" in prompt
    assert "browser-cli action guide --task visual_capture" in prompt
    assert "browser-cli action guide --task semantic_waits" in prompt
    assert "browser-cli action guide --task menu_keyboard_flow" in prompt
    assert "browser-cli action guide --task mouse_interaction" in prompt
    assert "browser-cli action guide --task state_waits" in prompt
    assert "browser-cli action guide --task page_diagnostics" in prompt
    assert "workflow.steps" in prompt
    assert "connect_from_codex.url" in prompt
    assert "browser-cli reference get --id skill_positioning --metadata-only" in prompt
    assert "browser-cli reference get --id skill_positioning" in prompt
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

    assert numbers == list(range(1, 21))


def test_codex_install_prompt_mentions_project_and_key_validation() -> None:
    prompt = _codex_install_prompt()

    assert "Project ID" in prompt
    assert "LEXMOUNT_PROJECT_ID" in prompt
    assert "LEXMOUNT_API_KEY" in prompt
    assert "Project 是否和 LEXMOUNT_PROJECT_ID 一致" in prompt
    assert "API Key 是否已过期、被 revoke" in prompt
    assert "scoped API Key" in prompt
    assert "created=true、closed=true" in prompt


def test_readme_documents_doctor_connect_from_codex_blockers() -> None:
    text = README.read_text()

    assert "`repair_plan.connect_from_codex.required_runtime_auth`" in text
    assert "`auth_login_contract`" in text
    assert "`device_code_contract`" in text
    assert "`required_token_lifecycle`" in text
    assert "`site_capability_status`" in text
    assert "required device-code endpoints" in text
    assert "browser.lexmount.cn, SDK, API, and gateway blockers" in text


def test_readme_homepage_positions_skill_and_supported_operations() -> None:
    text = README.read_text()

    assert "## When To Use This Skill" in text
    assert "## Supported Operation Map" in text
    assert "Use `browser-cli` when Codex or another agent needs" in text
    assert "| Use `browser-cli` when | Use something else when |" in text
    assert "isolated Lexmount remote browser session" in text
    assert "Do not use this Skill for a local desktop app" in text
    assert "persistent_login_state" in text
    assert "navigation_flow" in text
    assert "agent_browser_primitives" in text
    assert "observe, act, extract, and verify" in text
    assert "action act` for deterministic click/fill/select/check/press/hover/scroll plans" in text
    assert "browser-cli action act --session-id <session_id>" in text
    assert "browser-cli action extract --session-id <session_id>" in text
    assert "interactive_targeting" in text
    assert "content_extraction" in text
    assert "visual_capture" in text
    assert "dialog_frame_handling" in text
    assert "page_diagnostics" in text
    assert "docs/skill-positioning.md" in text
    assert "Chrome" not in text
    assert "chrome" not in text
    assert "Chromium" not in text
    assert "chromium" not in text


def test_skill_positioning_doc_compares_browserbase_skills() -> None:
    text = (DOCS / "skill-positioning.md").read_text()
    normalized = " ".join(text.split())
    docs_index = (DOCS / "README.md").read_text()

    assert "Browserbase Skills" in text
    assert "https://github.com/browserbase/skills" in text
    assert "https://raw.githubusercontent.com/browserbase/skills/main/skills/browser/SKILL.md" in text
    assert "https://docs.browserbase.com/integrations/mcp/introduction" in text
    assert "https://docs.browserbase.com/integrations/mcp/setup" in text
    assert "browser-cli reference get --id skill_positioning" in text
    assert "short default loop" in text
    assert "element refs" in text
    assert "What Browserbase currently does better" in text
    assert "first-run path is shorter" in text
    assert "browse snapshot" in text
    assert "agent_browser_primitives" in text
    assert "browser-cli action observe --session-id <session_id>" in text
    assert 'browser-cli action act --session-id <session_id> --kind click --role button --name "<name>"' in text
    assert "browser-cli action extract --session-id <session_id>" in text
    assert "deterministic `action act` plans" in normalized
    assert "natural-language act still needs a wrapper above the CLI" in normalized
    assert "MCP-style or natural-language wrappers" in normalized
    assert "Current Gap" in text
    assert "plugin package" in text
    assert "capability panel" in text
    assert "Connect from Codex" in text
    assert "Skill positioning and cloud-browser comparison" in docs_index
