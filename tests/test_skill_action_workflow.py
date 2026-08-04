from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / "SKILL.md"
PACKAGED_SKILL_MD = ROOT / "browser_cli" / "agent_skill" / "SKILL.md"
ACTION_PLAYBOOK = ROOT / "references" / "action-playbook.md"
PACKAGED_ACTION_PLAYBOOK = (
    ROOT / "browser_cli" / "agent_references" / "action-playbook.md"
)
ACE_PAGE_API = ROOT / "references" / "ace-page-api.md"
PACKAGED_ACE_PAGE_API = (
    ROOT / "browser_cli" / "agent_references" / "ace-page-api.md"
)


def test_skill_is_progressive_and_routes_detailed_apis_to_references() -> None:
    text = SKILL_MD.read_text()

    assert len(text.splitlines()) < 500
    assert "[references/ace-page-api.md](references/ace-page-api.md)" in text
    assert "[references/action-playbook.md](references/action-playbook.md)" in text
    assert "browser-cli reference get --id ace_page_api" in text
    assert "specialized fallback" in text


def test_packaged_skill_and_references_match_repository_sources() -> None:
    assert PACKAGED_SKILL_MD.read_text() == SKILL_MD.read_text()
    assert PACKAGED_ACTION_PLAYBOOK.read_text() == ACTION_PLAYBOOK.read_text()
    assert PACKAGED_ACE_PAGE_API.read_text() == ACE_PAGE_API.read_text()


def test_action_playbook_is_explicitly_a_specialized_fallback() -> None:
    text = ACTION_PLAYBOOK.read_text()

    assert "specialized `browser-cli action` fallback plane" in text
    assert "bundled ACE" in text
    assert "For routine Lexmount page navigation" in text
    assert "browser-cli action guide --task" in text
    assert "screenshot" in text
    assert "set-file-input" in text
    assert "storage-get" in text
    assert "console-snapshot" in text


def test_skill_documents_verified_ace_actions_and_no_replay() -> None:
    text = SKILL_MD.read_text()

    assert "current node ID" in text
    assert "advertises the required action" in text
    assert "--jq-context" in text
    assert "--outline" in text
    assert "--diff" in text
    assert "perform.ok:true" in text
    assert "Do not replay" in text


def test_skill_documents_workbuddy_qr_handoff_without_using_inspect_as_cdp() -> None:
    text = SKILL_MD.read_text()

    assert "div.qrcode.force-light" in text
    assert "WorkBuddy sidebar browser" in text
    assert "inspect_url" in text
    assert "Never use `inspect_url` as a CDP endpoint" in text


def test_ace_page_reference_documents_private_initialization_and_new_targets() -> None:
    text = ACE_PAGE_API.read_text()

    assert "Lexmount Session Initialization" in text
    assert "private inherited pipe" in text
    assert "--reveal-connect-url" in text
    assert "--lexmount-session-id LEXMOUNT_SESSION_ID" in text
    assert "When `newTargetId` is present" in text
    assert "attach` selects" in text
