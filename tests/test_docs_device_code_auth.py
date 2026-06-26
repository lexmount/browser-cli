from __future__ import annotations

from pathlib import Path


DOC = Path(__file__).resolve().parents[1] / "docs" / "device-code-auth.md"


def test_device_code_doc_tracks_current_token_status_contract() -> None:
    text = DOC.read_text()
    normalized = " ".join(text.split())

    assert "Current CLI support" in text
    assert "browser-cli auth status" in text
    assert "browser-cli doctor" in text
    assert "LEXMOUNT_BROWSER_CREDENTIALS_FILE" in text
    assert "--credentials-file" in text
    assert "auth_source" in text
    assert "runtime_auth_usable" in text
    assert "device_token.valid" in text
    assert "device_token.refresh_needed" in text
    assert "Output never includes access or refresh token values" in text
    assert "browser actions still require env API-key credentials" in normalized
