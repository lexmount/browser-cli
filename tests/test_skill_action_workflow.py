from __future__ import annotations

from pathlib import Path


SKILL_MD = Path(__file__).resolve().parents[1] / "SKILL.md"
ACTION_PLAYBOOK = (
    Path(__file__).resolve().parents[1] / "references" / "action-playbook.md"
)
PACKAGED_ACTION_PLAYBOOK = (
    Path(__file__).resolve().parents[1]
    / "browser_cli"
    / "agent_references"
    / "action-playbook.md"
)
USABLE_STATUS = (
    Path(__file__).resolve().parents[1] / "references" / "usable-status.md"
)
PACKAGED_USABLE_STATUS = (
    Path(__file__).resolve().parents[1]
    / "browser_cli"
    / "agent_references"
    / "usable-status.md"
)
SKILL_POSITIONING = (
    Path(__file__).resolve().parents[1] / "references" / "skill-positioning.md"
)
PACKAGED_SKILL_POSITIONING = (
    Path(__file__).resolve().parents[1]
    / "browser_cli"
    / "agent_references"
    / "skill-positioning.md"
)
QUICKSTART = Path(__file__).resolve().parents[1] / "references" / "quickstart.md"
PACKAGED_QUICKSTART = (
    Path(__file__).resolve().parents[1]
    / "browser_cli"
    / "agent_references"
    / "quickstart.md"
)
CONNECT_FROM_CODEX = (
    Path(__file__).resolve().parents[1] / "references" / "connect-from-codex.md"
)
PACKAGED_CONNECT_FROM_CODEX = (
    Path(__file__).resolve().parents[1]
    / "browser_cli"
    / "agent_references"
    / "connect-from-codex.md"
)


def _normalized_skill_text() -> str:
    return " ".join(SKILL_MD.read_text().split())


def _normalized_action_playbook_text() -> str:
    return " ".join(ACTION_PLAYBOOK.read_text().split())


def _normalized_skill_and_action_text() -> str:
    return " ".join([_normalized_skill_text(), _normalized_action_playbook_text()])


def test_skill_routes_action_details_to_reference() -> None:
    skill_text = SKILL_MD.read_text()
    normalized = _normalized_skill_text()
    action_text = _normalized_action_playbook_text()

    assert len(skill_text.splitlines()) < 500
    assert (
        "[references/action-playbook.md](references/action-playbook.md)" in skill_text
    )
    assert "Read that reference when selecting between semantic actions" in normalized
    assert "structured `result` fields" in normalized
    assert "`scaffold_templates`" in normalized
    assert "Action command examples" in action_text
    assert "Common task recipes" in action_text
    assert "Target Contract" in ACTION_PLAYBOOK.read_text()
    assert "browser-cli reference get --id action_playbook" in normalized


def test_packaged_action_playbook_matches_skill_reference() -> None:
    assert PACKAGED_ACTION_PLAYBOOK.read_text() == ACTION_PLAYBOOK.read_text()


def test_packaged_usable_status_matches_skill_reference() -> None:
    assert PACKAGED_USABLE_STATUS.read_text() == USABLE_STATUS.read_text()


def test_packaged_skill_positioning_matches_skill_reference() -> None:
    assert PACKAGED_SKILL_POSITIONING.read_text() == SKILL_POSITIONING.read_text()


def test_packaged_quickstart_matches_skill_reference() -> None:
    assert PACKAGED_QUICKSTART.read_text() == QUICKSTART.read_text()


def test_packaged_connect_from_codex_matches_skill_reference() -> None:
    assert PACKAGED_CONNECT_FROM_CODEX.read_text() == CONNECT_FROM_CODEX.read_text()


def test_skill_prefers_semantic_actions_before_eval() -> None:
    normalized = _normalized_action_playbook_text()

    assert "Inspect with `snapshot`, then `interactive-snapshot`" in normalized
    assert "interactive-only-snapshot` alias" in normalized
    assert "use `form-snapshot` before filling complex forms" in normalized
    assert "use `list-snapshot` before choosing from menus" in normalized
    assert "use `text-snapshot` for bounded visible text" in normalized
    assert "use `wait-dialog` or `dialog-snapshot` for modals" in normalized
    assert "use `wait-frame` or `frame-snapshot`" in normalized
    assert "For runtime errors, run `console-snapshot --install-only`" in normalized
    assert "Prefer semantic actions" in normalized
    assert "`wait-role` for async roles/names" in normalized
    assert "`wait-state-role`" in normalized
    assert "`get-attribute-role`" in normalized
    assert "`wait-attribute-role`" in normalized
    assert "`exists-role`, `get-text-role`, and `bounding-box-role`" in normalized
    assert "`click-label` for labeled controls" in normalized
    assert "`click-role` for known roles/names" in normalized
    assert "`click-text` for visible text" in normalized
    assert "`click-index` for a chosen repeated selector match" in normalized
    assert "`fill-label` for labeled text fields" in normalized
    assert "`fill-role` for writable role/name fields" in normalized
    assert "`focus-role`, `blur-role`, and `clear-role`" in normalized
    assert "`hover-role`" in normalized
    assert "`press-role`" in normalized
    assert "`scroll-into-view-role`" in normalized
    assert "`get-value-role`" in normalized
    assert "`wait-value-role`" in normalized
    assert "`select-label` or `select-role` for native selects" in normalized
    assert "`check-label`, `check-role`, or `uncheck-role`" in normalized
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
    assert "browser-cli commands --workflows-only" in normalized
    assert "browser-cli commands --workflow setup_and_verify" in normalized
    assert (
        "browser-cli commands --workflow connect_from_codex_site_requirements"
        in normalized
    )
    assert "browser-cli commands --workflow connect_from_codex_auth" in normalized
    assert "browser-cli commands --workflow device_code_auth" in normalized
    assert "browser-cli commands --workflow scoped_token_lifecycle" in normalized
    assert "browser-cli commands --workflow session_recovery" in normalized
    assert "browser-cli commands --workflow case_file_task" in normalized
    assert "browser-cli commands --workflow form_interaction" in normalized
    assert "browser-cli commands --workflow interactive_targeting" in normalized
    assert "browser-cli commands --workflow content_extraction" in normalized
    assert "browser-cli commands --workflow browser_state_management" in normalized
    assert "browser-cli commands --workflow file_upload" in normalized
    assert "browser-cli commands --workflow dialog_frame_handling" in normalized
    assert "browser-cli commands --workflow navigation_flow" in normalized
    assert "browser-cli commands --workflow link_navigation" in normalized
    assert "browser-cli commands --workflow visual_capture" in normalized
    assert "browser-cli commands --workflow semantic_waits" in normalized
    assert "browser-cli commands --workflow menu_keyboard_flow" in normalized
    assert "browser-cli commands --workflow mouse_interaction" in normalized
    assert "browser-cli commands --workflow state_waits" in normalized
    assert "browser-cli commands --workflow page_diagnostics" in normalized
    assert "browser-cli action guide --task <task>" in normalized
    assert "browser-cli action guide --names-only" in normalized
    assert "browser-cli action guide --task form_interaction" in normalized
    assert "browser-cli action guide --task interactive_targeting" in normalized
    assert "browser-cli action guide --task content_extraction" in normalized
    assert "browser-cli action guide --task browser_state_management" in normalized
    assert "browser-cli action guide --task file_upload" in normalized
    assert "browser-cli action guide --task dialog_frame_handling" in normalized
    assert "browser-cli action guide --task navigation_flow" in normalized
    assert "browser-cli action guide --task link_navigation" in normalized
    assert "browser-cli action guide --task visual_capture" in normalized
    assert "browser-cli action guide --task semantic_waits" in normalized
    assert "browser-cli action guide --task menu_keyboard_flow" in normalized
    assert "browser-cli action guide --task mouse_interaction" in normalized
    assert "browser-cli action guide --task state_waits" in normalized
    assert "browser-cli action guide --task page_diagnostics" in normalized
    assert "browser-cli commands --names-only" in normalized
    assert "browser-cli commands --group action" in normalized
    assert "`browser_target.exactly_one_of`" in normalized
    assert "browser-cli doctor --smoke-session" in normalized
    assert "browser-cli doctor --skip-api" in normalized
    assert (
        "If setup is uncertain, run `browser-cli commands --workflow setup_and_verify`"
        in normalized
    )
    assert (
        "then `browser-cli auth status` and `browser-cli doctor --json`" in normalized
    )
    assert "`--json` is accepted as a no-op compatibility flag" in normalized
    assert "at the top level and after subcommands" in normalized
    assert "`ok: true` and `failed: 0`" in normalized
    assert "`ok: false`: stop before creating sessions" in normalized
    assert "inspect `ready_for_browser_actions`, `failed_checks`" in normalized
    assert "If `browser_smoke_session` exists" in normalized
    assert "Prefer `repair_plan.commands`" in normalized
    assert "fall back to per-check `fix` objects" in normalized


def test_skill_uses_auth_helpers_for_setup() -> None:
    normalized = _normalized_skill_text()

    assert (
        "guide authentication with auth status/scopes/token-info/refresh/logout/connect-requirements/export-env/login"
        in normalized
    )
    assert "browser-cli auth status" in normalized
    assert "browser-cli auth scopes" in normalized
    assert (
        "browser-cli auth scopes --scope browser:actions --include-site-contract"
        in normalized
    )
    assert "browser-cli auth token-info" in normalized
    assert "browser-cli auth refresh" in normalized
    assert "browser-cli auth logout" in normalized
    assert "browser-cli auth connect-requirements" in normalized
    assert "browser-cli auth connect-requirements --checklist" in normalized
    assert "browser-cli auth login" in normalized
    assert "browser-cli auth export-env" in normalized
    assert "browser-cli auth login --device-code" in normalized
    assert (
        "browser-cli commands --workflow connect_from_codex_site_requirements"
        in normalized
    )
    assert "browser-cli commands --workflow connect_from_codex_auth" in normalized
    assert "browser-cli commands --workflow device_code_auth" in normalized
    assert "`required_api_contract`" in normalized
    assert "`required_token_lifecycle`" in normalized
    assert "`required_runtime_auth`" in normalized
    assert "`browser_site_contract.scope_ui_fields`" in normalized
    assert "`browser_site_contract.browser_site_acceptance_tests`" in normalized
    assert (
        "When `auth login` returns `handoff`, use it as the setup contract"
        in normalized
    )
    assert "`connect_from_codex_url` or `login_url`" in normalized
    assert "Check top-level `selected_flow`, `available`" in normalized
    assert (
        "`copyable_commands`, `open_command`, `local_env`, `verification`, and"
        in normalized
    )
    assert "browser-cli auth login --open" in normalized
    assert "inspect `open_result`" in normalized
    assert (
        "parse `available`, `reason`, `device_code`, `polling`, `credentials`"
        in normalized
    )
    assert "raw device-code values" in normalized
    assert "inspect or explain the device-code authorization path" in normalized
    assert "manual env fallback" in normalized
    assert "`auth export-env` prints placeholders by default" in normalized
    assert "Check top-level `usable` and `unusable_exports`" in normalized
    assert "`safe_to_paste_in_chat`, `local_shell_only`" in normalized
    assert "`contains_secret_values`" in normalized
    assert "`contains_secret_placeholders`" in normalized
    assert "`verification.doctor_command`" in normalized
    assert "`auth_export_env_contract`" in normalized
    assert (
        "`auth status` reports `auth_source`, `runtime_auth_usable`, `runtime_auth`"
        in normalized
    )
    assert "`runtime_auth.bearer_runtime.required_support`" in normalized
    assert "When env credentials are incomplete, read `missing_env`" in normalized
    assert "and the `fix` object instead of inventing setup steps" in normalized
    assert "`auth scopes` to inspect known Connect from Codex scopes" in normalized
    assert "`permission_count`, `risk`, `destructive`, `unknown_scopes`" in normalized
    assert "Use `auth token-info --required-scope <scope>`" in normalized
    assert "Use `auth refresh --credentials-file <path>`" in normalized
    assert "`reason`, `refresh_endpoint`, and `remote_refresh`" in normalized
    assert "Use `auth logout --credentials-file <path>`" in normalized
    assert "when a token lifecycle base URL is configured" in normalized
    assert "For scoped token checks, refresh, or local logout" in normalized
    assert "browser-cli commands --workflow scoped_token_lifecycle" in normalized
    assert (
        "`refresh_available`, `refreshed`, `revoke_available`, and `warnings`"
        in normalized
    )
    assert (
        "`runtime_auth.usable`, `runtime_auth.bearer_runtime.required_support`"
        in normalized
    )
    assert "`device_token.valid`, `device_token.expired`" in normalized
    assert "and `scope_check`" in normalized
    assert "`refresh_needed`, `has_refresh_token`" in normalized
    assert "`remote_revoke`, and `warnings`" in normalized
    assert "Do not start browser actions from a device token" in normalized
    assert "`runtime_auth.usable` is false" in normalized
    assert "do not report API key values" in normalized


def test_skill_uses_context_pick_for_persistent_login_state() -> None:
    normalized = _normalized_skill_text()

    assert "pick reusable contexts, or detect locked contexts" in normalized
    assert "browser-cli commands --workflow persistent_login_state" in normalized
    assert "browser-cli session create --context-metadata-json" in normalized
    assert "--create-context-if-missing" in normalized
    assert "Parse `context_reuse` from the session result" in normalized
    assert "`context_reuse.selected` is true" in normalized
    assert "Read top-level `availability`, `reusable`" in normalized
    assert "`normalized_status`, `selection_strategy`, and `reuse_reason`" in normalized
    assert "Prefer `availability` over raw status strings" in normalized
    assert "`available` can be reused" in normalized
    assert "`locked` means busy" in normalized
    assert "`unavailable` needs a different context" in normalized
    assert "browser-cli context pick --metadata-json" in normalized
    assert "browser-cli context status --context-id <context_id>" in normalized
    assert "candidates include `locked: true`" in normalized
    assert "`selection_summary`" in normalized
    assert "`locked_matches`" in normalized
    assert "`metadata_mismatches`" in normalized
    assert "`reusable_matches`" in normalized
    assert "`recommended_next_action`" in normalized
    assert "`decision_reason`" in normalized
    assert "`would_create`" in normalized
    assert "`metadata_diagnostics`" in normalized
    assert "values are redacted" in normalized
    assert "`local_registry`" in normalized
    assert "Never put API keys, passwords, or session secrets" in normalized
    assert (
        "`context pick --metadata-json <json> --selection newest --dry-run` before creating a session"
        in normalized
    )
    assert (
        "the workflow's optional `context status --context-id <context_id>` step"
        in normalized
    )


def test_skill_uses_json_argument_errors_for_command_repairs() -> None:
    normalized = _normalized_skill_text()

    assert "`argument_error`" in normalized
    assert "read the JSON `usage` field" in normalized
    assert "do not parse stderr" in normalized


def test_skill_uses_first_browser_workflow_before_manual_session_steps() -> None:
    normalized = _normalized_skill_text()

    assert "If an existing session is stale, inactive" in normalized
    assert "browser-cli commands --workflow session_recovery" in normalized
    assert "`sessions`, `session.status`, `final_status`" in normalized
    assert "For a first browser task" in normalized
    assert "browser-cli commands --workflow first_browser_task" in normalized
    assert "Then follow the returned steps" in normalized
    assert "For repeatable smoke tests, demos, or regression checks" in normalized
    assert "browser-cli commands --workflow case_file_task" in normalized
    assert "browser-cli case schema" in normalized
    assert "browser-cli case scaffold --template page-inspection" in normalized
    assert "browser-cli case scaffold --template form-fill" in normalized
    assert "browser-cli case scaffold --template interactive-targeting" in normalized
    assert "`supported_actions`, `required_fields`" in normalized
    assert "`page-info`, `wait-url`, `wait-title`, `wait-load-state`" in normalized
    assert (
        "`select-label`, `check-role`, `hover-role`, `press-role`, "
        "`scroll-into-view-role`" in normalized
    )
    assert (
        "`next_commands`, `events_path`, `artifacts_dir`, `session`, and `steps`"
        in normalized
    )
    assert "For form tasks, prefer the more specific form workflow" in normalized
    assert "browser-cli commands --workflow form_interaction" in normalized
    assert (
        "Follow the guide's `inspect_commands`, `preferred_commands`, "
        "`verify_commands`, and `custom_js_boundary`" in normalized
    )
    assert "then follow workflow `read` fields for `form-snapshot`" in normalized
    assert (
        "For visible labeled controls, buttons, links, menus, double-clicks,"
        in normalized
    )
    assert (
        "choose semantic actions such as `click-label`, `click-role`, and `click-text`"
        in normalized
    )
    assert "browser-cli commands --workflow interactive_targeting" in normalized
    assert "for mouse gestures use `mouse_interaction`" in normalized
    expected_mouse_actions = (
        "`double-click-role`, `right-click-role`, `drag-role-to-role`, "
        "`drag-to`, `double-click`, or `right-click`"
    )
    assert expected_mouse_actions in normalized
    assert "and `click-text` before selectors" in normalized
    assert "For page content extraction" in normalized
    assert "browser-cli commands --workflow content_extraction" in normalized
    assert "browser-cli action guide --task content_extraction" in normalized
    assert "browser-cli action extract --session-id <session_id>" in normalized
    assert "For browser state setup or cleanup" in normalized
    assert "browser-cli commands --workflow browser_state_management" in normalized
    assert "browser-cli action guide --task browser_state_management" in normalized
    assert "For file uploads" in normalized
    assert "browser-cli commands --workflow file_upload" in normalized
    assert "browser-cli action guide --task file_upload" in normalized
    assert "browser-cli commands --workflow dialog_frame_handling" in normalized
    assert "browser-cli action guide --task dialog_frame_handling" in normalized
    assert "browser-cli commands --workflow navigation_flow" in normalized
    assert "browser-cli action guide --task navigation_flow" in normalized
    assert "browser-cli commands --workflow link_navigation" in normalized
    assert "browser-cli action guide --task link_navigation" in normalized
    assert "browser-cli commands --workflow visual_capture" in normalized
    assert "browser-cli action guide --task visual_capture" in normalized
    assert "For visual evidence" in normalized
    assert "`screenshot-role`" in normalized
    assert "`screenshot-selector`" in normalized
    assert "For semantic readiness and deterministic state transitions" in normalized
    assert "browser-cli commands --workflow semantic_waits" in normalized
    assert "browser-cli action guide --task semantic_waits" in normalized
    assert "`wait-role`" in normalized
    assert "browser-cli commands --workflow menu_keyboard_flow" in normalized
    assert "browser-cli action guide --task menu_keyboard_flow" in normalized
    assert "browser-cli commands --workflow mouse_interaction" in normalized
    assert "browser-cli action guide --task mouse_interaction" in normalized
    assert "deterministic state transitions" in normalized
    assert "browser-cli commands --workflow state_waits" in normalized
    assert "browser-cli action guide --task state_waits" in normalized
    assert "For page failures, fetch/XHR issues, or runtime errors" in normalized
    assert "browser-cli commands --workflow page_diagnostics" in normalized
    assert "workflow's console, network, and visible-state steps" in normalized


def test_skill_documents_failure_payload_masking() -> None:
    normalized = _normalized_skill_and_action_text()

    assert "Failure messages and payloads mask `api_key`" in normalized
    assert "token-like query parameters" in normalized
    assert "the current `LEXMOUNT_API_KEY` value" in normalized
    assert "fields that look like password, token, credential, secret" in normalized
    assert "`requested_value_masked`" in normalized
    assert "`text_masked`" in normalized
    assert "do not ask the user to paste the real value into chat" in normalized


def test_skill_lists_selector_and_input_actions() -> None:
    normalized = _normalized_action_playbook_text()

    for action in (
        "`exists`",
        "`exists-role`",
        "`page-info`",
        "`set-viewport`",
        "`screenshot-selector`",
        "`screenshot-role`",
        "`get-text`",
        "`get-text-role`",
        "`count`",
        "`wait-count`",
        "`wait-state`",
        "`wait-state-role`",
        "`query`",
        "`inspect`",
        "`get-attribute`",
        "`get-attribute-role`",
        "`wait-attribute`",
        "`wait-attribute-role`",
        "`wait-selector`",
        "`wait-title`",
        "`wait-load-state`",
        "`wait-network-idle`",
        "`wait-text`",
        "`wait-role`",
        "`click`",
        "`type`",
        "`focus`",
        "`focus-role`",
        "`get-value`",
        "`get-value-role`",
        "`wait-value`",
        "`wait-value-role`",
        "`blur`",
        "`blur-role`",
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
        "`clear-role`",
        "`set-value`",
        "`set-file-input`",
        "`dispatch-event`",
        "`submit`",
        "`scroll-into-view`",
        "`scroll-into-view-role`",
        "`bounding-box`",
        "`bounding-box-role`",
        "`select-option`",
        "`select-label`",
        "`select-role`",
        "`check`",
        "`uncheck`",
        "`check-label`",
        "`check-role`",
        "`uncheck-label`",
        "`uncheck-role`",
        "`hover`",
        "`hover-role`",
        "`press`",
        "`press-role`",
        "`press-key`",
        "`link-snapshot`",
        "`table-snapshot`",
        "`list-snapshot`",
        "`text-snapshot`",
        "`dialog-snapshot`",
        "`wait-dialog`",
        "`frame-snapshot`",
        "`wait-frame`",
        "`performance-snapshot`",
        "`network-snapshot`",
        "`wait-network`",
        "`console-snapshot`",
        "`wait-console`",
        "`outline-snapshot`",
        "`form-snapshot`",
        "`click-index`",
        "`fill`",
        "`fill-role`",
    ):
        assert action in normalized


def test_skill_reinspects_after_failed_structured_results() -> None:
    normalized = _normalized_action_playbook_text()

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
    assert "`candidate_count`" in normalized
    assert "`candidates`" in normalized
    assert "`writable`" in normalized
    assert "`total_candidate_count`" in normalized
    assert "`requested_option_label`" in normalized
    assert "`option_found`" in normalized
    assert "`option_label`" in normalized
    assert "`requested_checked`" in normalized
    assert "`previous_checked`" in normalized
    assert "`changed`" in normalized
    assert "`links`" in normalized
    assert "`link_count`" in normalized
    assert "`href_masked`" in normalized
    assert "`absolute_url_masked`" in normalized
    assert "`same_origin`" in normalized
    assert "`external`" in normalized
    assert "`download`" in normalized
    assert "`tables`" in normalized
    assert "`table_count`" in normalized
    assert "`headers`" in normalized
    assert "`rows`" in normalized
    assert "`cells`" in normalized
    assert "`row_count`" in normalized
    assert "`cell_count`" in normalized
    assert "`lists`" in normalized
    assert "`list_count`" in normalized
    assert "`items`" in normalized
    assert "`item_count`" in normalized
    assert "`expanded`" in normalized
    assert "`texts`" in normalized
    assert "`text_count`" in normalized
    assert "`text_length`" in normalized
    assert "`text_truncated`" in normalized
    assert "`aria_live`" in normalized
    assert "`dialogs`" in normalized
    assert "`dialog_count`" in normalized
    assert "`total_dialog_count`" in normalized
    assert "`requested_text`" in normalized
    assert "`modal_only`" in normalized
    assert "`controls`" in normalized
    assert "`control_count`" in normalized
    assert "`controls_truncated`" in normalized
    assert "`modal`" in normalized
    assert "`frames`" in normalized
    assert "`frame_count`" in normalized
    assert "`total_frame_count`" in normalized
    assert "`src_masked`" in normalized
    assert "`frame_url_masked`" in normalized
    assert "`readable`" in normalized
    assert "`readable_only`" in normalized
    assert "`same_origin_only`" in normalized
    assert "`text_match`" in normalized
    assert "`read_error`" in normalized
    assert "`navigation`" in normalized
    assert "`resources`" in normalized
    assert "`resource_count`" in normalized
    assert "`initiator_types`" in normalized
    assert "`transfer_size`" in normalized
    assert "`response_status`" in normalized
    assert "`entries`" in normalized
    assert "`entry_count`" in normalized
    assert "`matched_count`" in normalized
    assert "`buffered_count`" in normalized
    assert "`source`" in normalized
    assert "`method`" in normalized
    assert "`requested_method`" in normalized
    assert "`status`" in normalized
    assert "`ok`" in normalized
    assert "`failed`" in normalized
    assert "`failed_only`" in normalized
    assert "`request_has_body`" in normalized
    assert "`duration_ms`" in normalized
    assert "`text_masked`" in normalized
    assert "`filename_masked`" in normalized
    assert "`url_masked`" in normalized
    assert "`timed_out`" in normalized
    assert "`requested_url`" in normalized
    assert "`url_match`" in normalized
    assert "`requested_source`" in normalized
    assert "`requested_status`" in normalized
    assert "`requested_level`" in normalized
    assert "`after_index`" in normalized
    assert "`headings`" in normalized
    assert "`landmarks`" in normalized
    assert "`outline_count`" in normalized
    assert "`heading_count`" in normalized
    assert "`landmark_count`" in normalized
    assert "`node_type`" in normalized
    assert "`level`" in normalized
    assert "`code`" in normalized
    assert "`target`" in normalized
    assert "`target_info`" in normalized
    assert "`modifiers`" in normalized
    assert "`events`" in normalized
    assert "`keydown_accepted`" in normalized
    assert "`ready_state`" in normalized
    assert "`visibility_state`" in normalized
    assert "`viewport`" in normalized
    assert "`scroll`" in normalized
    assert "`body_text_length`" in normalized
    assert "`html_length`" in normalized
    assert "`language`" in normalized
    assert "`referrer`" in normalized
    assert "`requested_title`" in normalized
    assert "`case_sensitive`" in normalized
    assert "inspect again before trying a different action" in normalized


def test_skill_includes_common_task_recipes() -> None:
    normalized = _normalized_action_playbook_text()

    assert "Common task recipes" in normalized
    assert "Fill and submit a form" in normalized
    assert "browser-cli commands --workflow form_interaction" in normalized
    assert "browser-cli commands --workflow interactive_targeting" in normalized
    assert "browser-cli commands --workflow content_extraction" in normalized
    assert "browser-cli commands --workflow browser_state_management" in normalized
    assert "browser-cli commands --workflow file_upload" in normalized
    assert "browser-cli commands --workflow dialog_frame_handling" in normalized
    assert "browser-cli commands --workflow navigation_flow" in normalized
    assert "browser-cli commands --workflow link_navigation" in normalized
    assert "browser-cli commands --workflow visual_capture" in normalized
    assert "browser-cli commands --workflow semantic_waits" in normalized
    assert "browser-cli commands --workflow menu_keyboard_flow" in normalized
    assert "browser-cli commands --workflow mouse_interaction" in normalized
    assert "browser-cli commands --workflow state_waits" in normalized
    assert "browser-cli commands --workflow page_diagnostics" in normalized
    assert "browser-cli action guide --task form_interaction" in normalized
    assert "browser-cli action guide --task interactive_targeting" in normalized
    assert "browser-cli action guide --task content_extraction" in normalized
    assert "browser-cli action guide --task browser_state_management" in normalized
    assert "browser-cli action guide --task file_upload" in normalized
    assert "browser-cli action guide --task dialog_frame_handling" in normalized
    assert "browser-cli action guide --task navigation_flow" in normalized
    assert "browser-cli action guide --task link_navigation" in normalized
    assert "browser-cli action guide --task visual_capture" in normalized
    assert "browser-cli action guide --task semantic_waits" in normalized
    assert "browser-cli action guide --task menu_keyboard_flow" in normalized
    assert "browser-cli action guide --task mouse_interaction" in normalized
    assert "browser-cli action guide --task state_waits" in normalized
    assert "browser-cli action guide --task page_diagnostics" in normalized
    assert "Extract page content or data" in normalized
    assert "`table-snapshot`" in normalized
    assert "Manage browser state" in normalized
    assert "`storage-set`" in normalized
    assert "`cookie-set`" in normalized
    assert "Upload files" in normalized
    assert "browser-cli commands --workflow file_upload" in normalized
    assert "Dialogs and frames" in normalized
    assert "browser-cli commands --workflow dialog_frame_handling" in normalized
    assert "browser-cli action guide --task dialog_frame_handling" in normalized
    assert "Navigate page history or async refresh" in normalized
    assert "browser-cli commands --workflow navigation_flow" in normalized
    assert "browser-cli action guide --task navigation_flow" in normalized
    assert "Link navigation" in normalized
    assert "browser-cli commands --workflow link_navigation" in normalized
    assert "browser-cli action guide --task link_navigation" in normalized
    assert "Capture visual evidence" in normalized
    assert "browser-cli commands --workflow visual_capture" in normalized
    assert "browser-cli action guide --task visual_capture" in normalized
    assert "Wait for semantic readiness" in normalized
    assert "browser-cli commands --workflow semantic_waits" in normalized
    assert "browser-cli action guide --task semantic_waits" in normalized
    assert "Menus and keyboard flows" in normalized
    assert "browser-cli commands --workflow menu_keyboard_flow" in normalized
    assert "browser-cli action guide --task menu_keyboard_flow" in normalized
    assert "Mouse gestures" in normalized
    assert "browser-cli commands --workflow mouse_interaction" in normalized
    assert "browser-cli action guide --task mouse_interaction" in normalized
    assert "`double-click-role`" in normalized
    assert "`right-click-role`" in normalized
    assert "`drag-role-to-role`" in normalized
    assert "`context_menu`" in normalized
    assert "Wait for deterministic state" in normalized
    assert "`wait-storage`" in normalized
    assert "run `form-snapshot` or `interactive-snapshot`" in normalized
    assert "use `outline-snapshot` for page structure" in normalized
    assert "`fill-label` for labeled fields" in normalized
    assert "`fill-role` for accessible role/name textboxes" in normalized
    assert "`fill` or `set-value` for stable selectors" in normalized
    assert "`set-file-input` for upload controls" in normalized
    assert "`clear-role` or `clear` before replacement text" in normalized
    assert (
        "`get-value-role`, `wait-value-role`, `get-value`, or `wait-value` to confirm form state"
        in normalized
    )
    assert "use `blur-role` or `blur` for focus-driven validation" in normalized
    assert "`select-label` or `select-role` for selects" in normalized
    assert "`select-option` or `check`" in normalized
    assert "prefer `check-label`, `check-role`, or `uncheck-role`" in normalized
    assert (
        "`wait-state-role --state enabled`, `wait-state --state enabled`, or"
        in normalized
    )
    assert "or `wait-role` for async submit buttons" in normalized
    assert "`dispatch-event --event input --event change`" in normalized
    assert "then use `submit`" in normalized
    assert "`click-role --role button --name <text>` or `click-text`" in normalized
    assert "Click a visible control" in normalized
    assert "use `wait-role` when the control appears asynchronously" in normalized
    assert (
        "`link-snapshot` when the task is to choose, inspect, or report navigation URLs"
        in normalized
    )
    assert (
        "use `list-snapshot` before choosing from menus, listboxes, task lists, or search results"
        in normalized
    )
    assert (
        "prefer `click-role`, then `click-text`, then `scroll-into-view`" in normalized
    )
    assert "use `exists-role`, `get-text-role`, or `bounding-box-role`" in normalized
    assert "after `exists`, `inspect`, or `bounding-box`" in normalized
    assert "For repeated matches, run `query` and then" in normalized
    assert "`click-index --index <n>`" in normalized
    assert (
        "`list-snapshot` for menu/listbox/search-result/task-list content" in normalized
    )
    assert (
        "`text-snapshot` for visible paragraphs, alerts, status messages, and bounded readable text"
        in normalized
    )
    assert "`table-snapshot` for HTML or ARIA table/report data" in normalized
    assert "`outline-snapshot` for headings and landmarks" in normalized
    assert "Navigate page history or async refresh" in normalized
    assert "use `open-url`, `reload`, `go-back`, or `go-forward`" in normalized
    assert "confirm with `page-info`, `wait-url`, `wait-title`" in normalized
    assert "`wait-network-idle`, `performance-snapshot`, `wait-text`" in normalized
    assert "Diagnose fetch/XHR calls" in normalized
    assert "`network-snapshot --install-only`" in normalized
    assert "read `network-snapshot`" in normalized
    assert "wait with `wait-network`" in normalized
    assert "Capture runtime errors" in normalized
    assert "`console-snapshot --install-only`" in normalized
    assert "read `console-snapshot`" in normalized
    assert "wait with `wait-console`" in normalized
    assert "Debug selectors" in normalized
    assert "use `count`, `query`, `inspect`, and `get-attribute` before" in normalized
    assert "use `inspect` for `state.disabled`, `state.readonly`" in normalized
    assert "use `wait-count`, `wait-state`, or `wait-attribute`" in normalized
    assert "Open menus or keyboard flows" in normalized
    assert "browser-cli commands --workflow menu_keyboard_flow" in normalized
    assert (
        "use `focus-role`, `hover-role`, `press-role`, or `scroll-into-view-role`"
        in normalized
    )
    assert "`wait-attribute-role` for `aria-expanded` or `aria-selected`" in normalized
    assert (
        "use `focus`, `hover`, or `press` for stable selector-scoped keys" in normalized
    )
    assert "`press-key` for active/global shortcuts" in normalized
    assert "`dispatch-event` for explicit DOM events" in normalized
    assert "`blur-role` or `blur`" in normalized
    assert "first read `dialog_frame_handling`" in normalized
    assert "run `wait-dialog` when the dialog appears asynchronously" in normalized
    assert "otherwise run `dialog-snapshot`, choose from `controls`" in normalized
    assert "run `wait-frame` when the frame appears asynchronously" in normalized
    assert "otherwise run `frame-snapshot` and parse `readable`" in normalized
    assert "Read page results" in normalized
    assert "use `page-info` for URL/title/readyState/viewport checks" in normalized
    assert "`set-viewport` before responsive screenshots" in normalized
    assert "`wait-title` for async title changes" in normalized
    assert "`wait-count` for dynamic lists" in normalized
    assert (
        "`wait-state-role` for semantic enabled/visible/checked/focused" in normalized
    )
    assert "`wait-state` for selector states" in normalized
    assert (
        "`get-attribute-role` and `wait-attribute-role` for semantic DOM attributes"
        in normalized
    )
    assert "`get-text-role` for semantic text checks" in normalized
    assert "`get-text` for a known selector" in normalized
    assert "use `wait-text` or `wait-role` before reading dynamic results" in normalized
    assert (
        "`wait-text --state absent` when loading, toast, or error text should disappear"
        in normalized
    )
    assert "Manage browser state" in normalized
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
    assert "use `set-viewport` when evidence needs a stable browser size" in normalized
    assert "`screenshot-role` for a semantic target" in normalized
    assert "`screenshot-selector` for a known panel/control" in normalized
    assert "`screenshot` for full viewport/page evidence" in normalized
