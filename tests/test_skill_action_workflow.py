from __future__ import annotations

from pathlib import Path


SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"


def _normalized_skill_text() -> str:
    return " ".join(SKILL_MD.read_text().split())


def test_skill_prefers_semantic_actions_before_eval() -> None:
    normalized = _normalized_skill_text()

    assert "Inspect with `snapshot`, then `interactive-snapshot`" in normalized
    assert "use `form-snapshot` before filling complex forms" in normalized
    assert "Prefer semantic actions" in normalized
    assert "`click-role` for known roles/names" in normalized
    assert "`click-text` for visible text" in normalized
    assert "`click-index` for a chosen repeated selector match" in normalized
    assert "`fill-label` for labeled text fields" in normalized
    assert "`select-label` for labeled native selects" in normalized
    assert "`check-label` for labeled checkbox or switch controls" in normalized
    assert (
        "Use `eval` only for page-local work not covered by a first-class action"
        in normalized
    )


def test_skill_uses_doctor_for_setup_checks() -> None:
    normalized = _normalized_skill_text()

    assert (
        "verify installation, environment, and API connectivity with doctor"
        in normalized
    )
    assert "browser-cli doctor" in normalized
    assert "browser-cli doctor --skip-api" in normalized
    assert "If setup is uncertain, run `browser-cli auth status`, then" in normalized
    assert "`--json` is accepted as a no-op compatibility flag" in normalized
    assert "at the top level and after subcommands" in normalized
    assert "`ok: true` and `failed: 0`" in normalized
    assert "`ok: false`: stop before creating sessions" in normalized
    assert "inspect `ready_for_browser_actions`, `failed_checks`" in normalized
    assert "Prefer `repair_plan.commands`" in normalized
    assert "fall back to per-check `fix` objects" in normalized


def test_skill_uses_auth_helpers_for_setup() -> None:
    normalized = _normalized_skill_text()

    assert "guide authentication with auth status/export-env/login" in normalized
    assert "browser-cli auth status" in normalized
    assert "browser-cli auth login" in normalized
    assert "browser-cli auth export-env" in normalized
    assert (
        "When `auth login` returns `handoff`, use it as the setup contract"
        in normalized
    )
    assert "`connect_from_codex_url` or `login_url`" in normalized
    assert (
        "`copyable_commands`, `local_env`, `verification`, and `secret_policy`"
        in normalized
    )
    assert "`auth export-env` prints placeholders by default" in normalized
    assert "do not report API key values" in normalized


def test_skill_uses_context_pick_for_persistent_login_state() -> None:
    normalized = _normalized_skill_text()

    assert "pick reusable contexts, or detect locked contexts" in normalized
    assert "browser-cli session create --context-metadata-json" in normalized
    assert "--create-context-if-missing" in normalized
    assert "Parse `context_reuse` from the session result" in normalized
    assert "`context_reuse.selected` is true" in normalized
    assert "Prefer `availability` over raw status strings" in normalized
    assert "`available` can be reused" in normalized
    assert "`locked` means busy" in normalized
    assert "`unavailable` needs a different context" in normalized
    assert "browser-cli context pick --metadata-json" in normalized
    assert "browser-cli context status --context-id <context_id>" in normalized
    assert "candidates include `locked: true`" in normalized


def test_skill_uses_json_argument_errors_for_command_repairs() -> None:
    normalized = _normalized_skill_text()

    assert "`argument_error`" in normalized
    assert "read the JSON `usage` field" in normalized
    assert "do not parse stderr" in normalized


def test_skill_documents_failure_payload_masking() -> None:
    normalized = _normalized_skill_text()

    assert "Failure messages and payloads mask `api_key`" in normalized
    assert "token-like query parameters" in normalized
    assert "the current `LEXMOUNT_API_KEY` value" in normalized


def test_skill_lists_selector_and_input_actions() -> None:
    normalized = _normalized_skill_text()

    for action in (
        "`exists`",
        "`get-text`",
        "`count`",
        "`wait-count`",
        "`wait-state`",
        "`query`",
        "`inspect`",
        "`get-attribute`",
        "`wait-attribute`",
        "`wait-selector`",
        "`wait-load-state`",
        "`wait-network-idle`",
        "`wait-text`",
        "`click`",
        "`type`",
        "`focus`",
        "`get-value`",
        "`wait-value`",
        "`blur`",
        "`storage-get`",
        "`storage-set`",
        "`storage-remove`",
        "`storage-clear`",
        "`wait-storage`",
        "`cookie-get`",
        "`cookie-set`",
        "`cookie-delete`",
        "`cookie-clear`",
        "`wait-cookie`",
        "`clear`",
        "`set-value`",
        "`set-file-input`",
        "`dispatch-event`",
        "`submit`",
        "`scroll-into-view`",
        "`bounding-box`",
        "`select-option`",
        "`select-label`",
        "`check`",
        "`uncheck`",
        "`check-label`",
        "`uncheck-label`",
        "`form-snapshot`",
        "`click-index`",
    ):
        assert action in normalized


def test_skill_reinspects_after_failed_structured_results() -> None:
    normalized = _normalized_skill_text()

    assert "`result.found`" in normalized
    assert "`result.exists`" in normalized
    assert "`result.clicked`" in normalized
    assert "`result.filled`" in normalized
    assert "`result.value`" in normalized
    assert "`result.readable`" in normalized
    assert "`removed`" in normalized
    assert "`deleted`" in normalized
    assert "`cleared_count`" in normalized
    assert "`network_idle`" in normalized
    assert "`quiet_ms`" in normalized
    assert "`requested_count`" in normalized
    assert "`matched`" in normalized
    assert "`state_values`" in normalized
    assert "`attribute_found`" in normalized
    assert "`requested_value`" in normalized
    assert "`dispatched`" in normalized
    assert "`dispatched_events`" in normalized
    assert "`fields`" in normalized
    assert "`value_masked`" in normalized
    assert "`file_input`" in normalized
    assert "`file_count`" in normalized
    assert "`requested_files`" in normalized
    assert "`bounding_box`" in normalized
    assert "`in_viewport`" in normalized
    assert "`index`" in normalized
    assert "`attributes`" in normalized
    assert "`html_truncated`" in normalized
    assert "`requested_option_label`" in normalized
    assert "`option_found`" in normalized
    assert "`option_label`" in normalized
    assert "`requested_checked`" in normalized
    assert "`previous_checked`" in normalized
    assert "`changed`" in normalized
    assert "inspect again before trying a different action" in normalized


def test_skill_includes_common_task_recipes() -> None:
    normalized = _normalized_skill_text()

    assert "Common task recipes" in normalized
    assert "Fill and submit a form" in normalized
    assert "run `form-snapshot` or `interactive-snapshot`" in normalized
    assert "`fill-label` for labeled fields" in normalized
    assert "`set-value` for stable selectors" in normalized
    assert "`set-file-input` for upload controls" in normalized
    assert "`clear` before replacement text" in normalized
    assert "`get-value` or `wait-value` to confirm form state" in normalized
    assert "use `blur` for focus-driven validation" in normalized
    assert "`select-label` for labeled selects" in normalized
    assert "`select-option` or `check`" in normalized
    assert "prefer `check-label` for labeled controls" in normalized
    assert "`wait-state --state enabled` for async submit buttons" in normalized
    assert "`dispatch-event --event input --event change`" in normalized
    assert "then use `submit`" in normalized
    assert "`click-role --role button --name <text>` or `click-text`" in normalized
    assert "Click a visible control" in normalized
    assert "`click-role`, then `click-text`, then `scroll-into-view`" in normalized
    assert "after `exists`, `inspect`, or `bounding-box`" in normalized
    assert "For repeated matches, run `query` and then" in normalized
    assert "`click-index --index <n>`" in normalized
    assert "Navigate page history or async refresh" in normalized
    assert "use `reload`, `go-back`, or `go-forward`" in normalized
    assert (
        "confirm with `wait-url`, `wait-load-state`, `wait-network-idle`" in normalized
    )
    assert "Debug selectors" in normalized
    assert "use `count`, `query`, `inspect`, and `get-attribute` before" in normalized
    assert "use `inspect` for `state.disabled`, `state.readonly`" in normalized
    assert "use `wait-count`, `wait-state`, or `wait-attribute`" in normalized
    assert "Open menus or keyboard flows" in normalized
    assert "use `focus`, `hover` for menus, `press` for shortcuts" in normalized
    assert "`dispatch-event` for explicit DOM events" in normalized
    assert "Read page results" in normalized
    assert "use `wait-count` for dynamic lists" in normalized
    assert "`wait-state` for enabled/visible/checked/focused states" in normalized
    assert "`get-text` for a known selector" in normalized
    assert "use `wait-text` before reading dynamic results" in normalized
    assert "Adjust browser state" in normalized
    assert "use `storage-get` for local/session storage" in normalized
    assert "`storage-clear --prefix <prefix>` for targeted cleanup" in normalized
    assert "use `wait-storage` when the page updates keys asynchronously" in normalized
    assert (
        "Use `cookie-get`, `cookie-set`, `cookie-delete`, or `cookie-clear`"
        in normalized
    )
    assert "`wait-cookie` when cookie changes are async" in normalized
    assert "document.cookie-visible cookies" in normalized
    assert "HttpOnly cookies" in normalized
    assert "Capture final evidence" in normalized
    assert "use `screenshot` after the action sequence" in normalized
