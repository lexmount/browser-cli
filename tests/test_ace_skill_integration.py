from __future__ import annotations

from importlib import resources as importlib_resources
import os
from pathlib import Path
import tomllib

from browser_cli.cli import _command_catalog


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = REPO_ROOT / "SKILL.md"
OPENAI_YAML = REPO_ROOT / "agents" / "openai.yaml"
ACE_REFERENCE = REPO_ROOT / "references" / "ace-page-api.md"
CDP = REPO_ROOT / "scripts" / "cdp.py"
DAEMON = REPO_ROOT / "scripts" / "cdp_daemon.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def test_single_skill_defines_the_control_and_page_planes() -> None:
    text = SKILL_MD.read_text()

    assert "Control plane and page plane" in text
    assert "browser-cli` for setup, authentication, contexts" in text
    assert "default page-operation plane" in text
    assert "--lexmount-session-id <lexmount-session-id> sessions" in text
    assert "--lexmount-session-id <lexmount-session-id> attach <target-id>" in text
    assert "Lexmount `session_id`" in text
    assert "CDP\n  `targetId`" in text
    assert "daemon-local ACE `sessionId`" in text
    assert "specialized fallback" in text
    assert "Do not replay" in text
    assert "references/ace-page-api.md" in text
    assert "browser-cli reference get --id ace_page_api" in text
    assert len(text.splitlines()) < 500


def test_single_skill_metadata_routes_routine_page_work_to_ace() -> None:
    text = OPENAI_YAML.read_text()

    assert 'display_name: "Lexmount Browser CLI"' in text
    assert (
        'short_description: "Control Lexmount sessions with bundled ACE page operations"'
        in text
    )
    assert "$browser-cli" in text
    assert "bundled ACE" in text
    assert "--lexmount-session-id" in text
    assert "routine page work" in text
    assert "specialized fallback" in text
    assert "secrets out of chat" in text
    assert "$ace-protocol" not in text


def test_ace_resources_are_part_of_the_browser_cli_skill_package() -> None:
    packaged_skill = (
        importlib_resources.files("browser_cli.agent_skill")
        .joinpath("SKILL.md")
        .read_text(encoding="utf-8")
    )
    packaged_cdp = (
        importlib_resources.files("browser_cli.agent_skill")
        .joinpath("scripts/cdp.py")
        .read_text(encoding="utf-8")
    )
    packaged_daemon = (
        importlib_resources.files("browser_cli.agent_skill")
        .joinpath("scripts/cdp_daemon.py")
        .read_text(encoding="utf-8")
    )
    packaged_reference = (
        importlib_resources.files("browser_cli.agent_references")
        .joinpath("ace-page-api.md")
        .read_text(encoding="utf-8")
    )

    assert packaged_skill == SKILL_MD.read_text()
    assert packaged_cdp == CDP.read_text()
    assert packaged_daemon == DAEMON.read_text()
    assert packaged_reference == ACE_REFERENCE.read_text()
    assert os.access(CDP, os.X_OK)
    assert os.access(DAEMON, os.X_OK)


def test_no_standalone_ace_protocol_skill_or_package_remains() -> None:
    assert not (REPO_ROOT / "ace_protocol").exists()
    assert not (REPO_ROOT / "ace-protocol").exists()
    assert not (REPO_ROOT / "ace_protocol" / "SKILL.md").exists()
    packaging = PYPROJECT.read_text()
    assert '"agent_skill/scripts/*.py"' in packaging
    assert "ace_protocol" not in packaging


def test_ace_dependencies_are_installed_with_browser_cli() -> None:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]

    assert "jq>=1.12.0" in project["dependencies"]
    assert "websockets>=15.0.1" in project["dependencies"]


def test_ace_reference_documents_private_lexmount_initialization() -> None:
    text = ACE_REFERENCE.read_text()

    assert "Lexmount Session Initialization" in text
    assert "--lexmount-session-id LEXMOUNT_SESSION_ID sessions" in text
    assert "--lexmount-session-id LEXMOUNT_SESSION_ID attach TARGET_ID" in text
    assert "private inherited pipe" in text
    assert "stdout, stderr, logs, state files, command arguments" in text


def test_default_workflows_use_ace_and_action_workflows_are_marked_fallback() -> None:
    workflows = _command_catalog()["agent_workflows"]

    for workflow_id in (
        "first_browser_task",
        "agent_browser_primitives",
        "one_off_page_task",
    ):
        workflow = workflows[workflow_id]
        assert workflow["page_operation_plane"] == "ace"
        assert workflow["browser_cli_action_policy"] == "specialized_fallback_only"
        assert "browser-cli action" not in str(workflow["steps"])

    for workflow_id in (
        "visual_capture",
        "file_upload",
        "browser_state_management",
        "page_diagnostics",
    ):
        workflow = workflows[workflow_id]
        assert workflow["page_operation_plane"] == "browser-cli"
        assert workflow["browser_cli_action_policy"] == "specialized_fallback_only"
