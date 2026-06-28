from __future__ import annotations

from pathlib import Path


DOC = Path(__file__).resolve().parents[1] / "docs" / "device-code-auth.md"


def test_device_code_doc_tracks_current_token_status_contract() -> None:
    text = DOC.read_text()
    normalized = " ".join(text.split())

    assert "Current CLI support" in text
    assert "browser-cli auth status" in text
    assert "browser-cli auth token-info" in text
    assert "browser-cli auth refresh" in text
    assert "browser-cli auth logout" in text
    assert "browser-cli auth connect-requirements" in text
    assert "browser-cli auth login --device-code" in text
    assert "POST /api/auth/device/code" in text
    assert "POST /api/auth/device/token" in text
    assert "reason=browser_site_endpoint_missing" in text
    assert "LEXMOUNT_BROWSER_DEVICE_CODE_BASE_URL" in text
    assert "--device-code-base-url <url>" in text
    assert "--wait" in text
    assert "browser-cli doctor" in text
    assert "browser-cli doctor --smoke-session" in text
    assert "LEXMOUNT_BROWSER_CREDENTIALS_FILE" in text
    assert "--credentials-file" in text
    assert "auth_source" in text
    assert "runtime_auth_usable" in text
    assert "runtime_auth.usable" in text
    assert "runtime_auth.bearer_runtime.required_support" in text
    assert "required_runtime_auth" in text
    assert "SDK bearer-token client construction" in text
    assert "device_token.valid" in text
    assert "device_token.refresh_needed" in text
    assert "scope_check.required_scopes" in text
    assert "scope_check.missing_scopes" in text
    assert "scope_check.satisfied" in text
    assert "has_refresh_token" in text
    assert "refresh_available=false" in text
    assert "refreshed" in text
    assert "deleted" in text
    assert "present_before" in text
    assert "present_after" in text
    assert "revoke_available=false" in text
    assert "creating and closing a temporary session" in text
    assert "Output never includes access or refresh token values" in text
    assert "browser actions still require env API-key credentials" in normalized
