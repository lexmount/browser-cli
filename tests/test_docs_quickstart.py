from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
QUICKSTART = REPO_ROOT / "docs" / "quickstart.md"
README = REPO_ROOT / "README.md"
DOCS_INDEX = REPO_ROOT / "docs" / "README.md"
MVP_QUICKSTART = REPO_ROOT / "docs" / "mvp-quickstart.md"


def test_stable_quickstart_is_linked_from_primary_docs() -> None:
    assert "[docs/quickstart.md](docs/quickstart.md)" in README.read_text()
    assert "[Quickstart](./quickstart.md)" in DOCS_INDEX.read_text()
    assert "[browser-cli quickstart](./quickstart.md)" in MVP_QUICKSTART.read_text()


def test_quickstart_covers_minimum_usable_flow() -> None:
    text = QUICKSTART.read_text()

    assert (
        "uv tool install --force git+https://github.com/lexmount/browser-cli.git"
        in text
    )
    assert "LEXMOUNT_API_KEY" in text
    assert "LEXMOUNT_PROJECT_ID" in text
    assert "browser-cli auth status" in text
    assert "browser-cli doctor --json" in text
    assert "browser-cli doctor --smoke-session" in text
    assert "browser-cli session create" in text
    assert "browser-cli action open-url" in text
    assert "browser-cli context pick" in text
    assert "browser-cli action guide --task interactive_targeting" in text
    assert "browser-cli case schema" in text
