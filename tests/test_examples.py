from __future__ import annotations

from pathlib import Path

from lex_browser_runtime.browser.cases import validate_case_file


def test_example_case_files_validate() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    case_files = sorted((repo_root / "examples" / "cases").glob("*.yaml"))

    assert case_files
    for case_file in case_files:
        result = validate_case_file(case_file)
        assert result.valid, f"{case_file}: {result.errors}"
        assert result.step_count > 0
