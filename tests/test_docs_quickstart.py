from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
QUICKSTART = REPO_ROOT / "docs" / "quickstart.md"
README = REPO_ROOT / "README.md"
DOCS_INDEX = REPO_ROOT / "docs" / "README.md"
MVP_QUICKSTART = REPO_ROOT / "docs" / "mvp-quickstart.md"
USABLE_VERSION = REPO_ROOT / "docs" / "usable-version.md"


def test_stable_quickstart_is_linked_from_primary_docs() -> None:
    assert "[docs/quickstart.md](docs/quickstart.md)" in README.read_text()
    assert "[docs/usable-version.md](docs/usable-version.md)" in README.read_text()
    assert "[Quickstart](./quickstart.md)" in DOCS_INDEX.read_text()
    assert "[Usable version status](./usable-version.md)" in DOCS_INDEX.read_text()
    assert "[browser-cli quickstart](./quickstart.md)" in MVP_QUICKSTART.read_text()


def test_quickstart_covers_minimum_usable_flow() -> None:
    text = QUICKSTART.read_text()

    assert (
        "uv tool install --force git+https://github.com/lexmount/browser-cli.git"
        in text
    )
    assert "PR #69" not in text
    assert "codex/add-export-env-safety-metadata" not in text
    assert "LEXMOUNT_API_KEY" in text
    assert "LEXMOUNT_PROJECT_ID" in text
    assert "browser-cli reference get --id quickstart --metadata-only" in text
    assert "browser-cli reference get --id quickstart" in text
    assert "browser-cli skill status" in text
    assert "browser-cli skill install --force" in text
    assert "browser-cli auth status" in text
    assert "browser-cli doctor --json" in text
    assert "browser-cli doctor --smoke-session" in text
    assert "browser-cli session create" in text
    assert "browser-cli action open-url" in text
    assert "browser-cli action act --session-id <session_id>" in text
    assert "browser-cli context list" in text
    assert "browser-cli context status --context-id <context_id>" in text
    assert "browser-cli action guide --task interactive_targeting" in text
    assert "browser-cli case schema" in text


def test_usable_version_status_documents_trial_boundaries() -> None:
    text = USABLE_VERSION.read_text()

    assert "browser-cli` version `0.2.0" in text
    assert "browser-cli doctor --json" in text
    assert "browser-cli doctor --smoke-session" in text
    assert "browser_smoke_session.status=pass" in text
    assert "created=true" in text
    assert "closed=true" in text
    assert "LEXMOUNT_API_KEY" in text
    assert "LEXMOUNT_PROJECT_ID" in text
    assert "browser-cli auth connect-requirements --checklist" in text
    assert "Device-code/OAuth endpoints" in text
    assert "bearer-token support" in text
    assert "Do not paste API keys" in text
