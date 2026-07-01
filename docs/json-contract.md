# browser-cli JSON Contract

`browser-cli` is an agent-facing CLI. Agents should be able to run commands,
parse JSON, make decisions from stable fields, and avoid leaking secrets.

## Output Shape

Every command prints exactly one JSON object to stdout.

Successful commands include:

```json
{
  "ok": true,
  "command": "session.list"
}
```

Failed commands include:

```json
{
  "ok": false,
  "command": "session.list",
  "error": "configuration_error",
  "message": "missing credentials"
}
```

Stable fields:

- `ok`: boolean success flag.
- `command`: stable dotted command name, such as `session.create`.
- `error`: machine-readable error string on failures.
- `message`: human-readable failure detail on failures.

Command-specific fields may be added over time. Agents should check `ok` and
`command` first, then inspect command-specific fields.

`browser-cli --version` and `browser-cli version` both print the `version`
command JSON. The payload includes `package`, `version`, `version_source`,
`lex_browser_runtime_version`, `lex_browser_runtime_version_known`,
`python_version`, and `executable` so agents can verify local installation state
without parsing text output.

`browser-cli commands` is the machine-readable command discovery surface. It
returns `schema_version`, `groups`, `command_count`, `commands`, `json_output`,
`secret_policy`, `agent_references`, `agent_examples`, `agent_entrypoints`, and
`agent_workflows`; `--names-only` returns compact command names, and
`--group <name>` filters by command group.
Unknown command groups fail as JSON with `error=unknown_group`,
`available_groups`, and a `fix` object with commands for inspecting valid
groups.
`--workflows-only` returns a compact payload with `workflow_count`,
`agent_workflows`, `agent_references`, and `agent_entrypoints` without the large
`commands` array.
`--workflow <id>` returns one workflow as `workflow_id` and `workflow`; unknown
workflow ids fail as JSON with `error=unknown_workflow`, `available_workflows`,
and a `fix` object with commands for inspecting valid workflows.
`browser-cli action guide` is the compact machine-readable action selection
surface. It returns `schema_version`, `selection_policy`, and `tasks`; with
`--task <id>` it returns `guide.inspect_commands`,
`guide.preferred_commands`, `guide.fallback_commands`,
`guide.verify_commands`, `guide.read_fields`, and
`guide.custom_js_boundary` for that browser task. Unknown task ids fail as JSON
with `error=unknown_action_guide_task`, `available_tasks`, and a `fix` object
with commands for inspecting valid guide tasks.
`agent_references` describes optional Skill reference files such as
`references/connect-from-codex.md`, `references/quickstart.md`,
`references/skill-positioning.md`, `references/usable-status.md`, and
`references/action-playbook.md`, with `content_command`, `package_resource`,
`load_when`, `related_workflows`, `covers`, and `grep_patterns` so agents can
load setup, positioning, site-implementation, or action guidance only when
needed.
`browser-cli reference list` returns packaged reference metadata;
`browser-cli reference get --id skill_positioning` returns the installed Skill
positioning and cloud-browser comparison reference,
`browser-cli reference get --id connect_from_codex` returns the installed
browser.lexmount.cn implementation guide,
`browser-cli reference get --id quickstart` returns the installed minimum setup
and first browser task path,
`browser-cli reference get --id usable_status` returns the installed usable
baseline/status reference, and `browser-cli reference get --id action_playbook`
returns the installed action playbook as JSON.
`agent_examples` describes packaged common-task examples and case files.
`browser-cli example list` returns example metadata, and
`browser-cli example get --id setup_verification_playbook`,
`browser-cli example get --id auth_lifecycle_playbook`,
`browser-cli example get --id persistent_context_playbook`,
`browser-cli example get --id page_inspection_case`,
`browser-cli example get --id agent_primitives_case`,
`browser-cli example get --id content_extraction_case`,
`browser-cli example get --id interactive_targeting_case`, or
`browser-cli example get --id page_diagnostics_case` returns an installed
example case file or playbook as JSON.
`agent_workflows` describes ordered setup, Connect from Codex site requirements,
Connect from Codex auth, device-code auth, scoped token lifecycle, session
recovery, one-off page, case file task, persistent login state, form
interaction, interactive targeting, and page diagnostics steps with `command`,
`read`, `success_condition`,
`on_failure_read`, and `cleanup` hints.
Command entries may expose `aliases` on canonical commands plus `alias_of` and
`canonical_name` on alias commands, so agents can map user-facing phrasing back
to the preferred action without parsing help text.
Workflow `read` arrays include current auth availability fields, scope catalog
fields, export usability fields, and context reuse availability fields when
those values drive the next agent decision.
The setup workflow includes a `usable_status` reference inspection step,
`auth status`, `doctor --json`, and optional `doctor --smoke-session`; the
smoke step reads
`browser_smoke_session.status`, `browser_smoke_session.created`, and
`browser_smoke_session.closed` so agents can verify a temporary session can be
created and cleaned up. The same smoke result is also present in
`checks[]` with `name=browser_smoke_session` for generic doctor check parsers.
`doctor --json` also includes an `agent_prompt` check for the packaged
`browser_cli.agent_metadata:openai.yaml` prompt metadata. It reports
`display_name`, `short_description`, whether `default_prompt` is present,
`required_pattern_count`, `missing_patterns`, and `mismatched_fields`;
warnings use `fix.code=repair_packaged_agent_prompt`.
The scoped token lifecycle workflow includes token validity, scope coverage,
scope catalog lookup, refresh availability, browser readiness, and local logout/revoke-pending fields
without exposing token values.
The Connect from Codex site requirements workflow includes
`auth scopes --include-site-contract`, `auth connect-requirements`,
`browser_site_contract.scope_ui_fields`,
`browser_site_contract.browser_site_acceptance_tests`,
`connect_from_codex.site_capability_status.missing`,
`connect_from_codex.browser_site_acceptance_tests`,
`required_device_code_endpoints`, `required_api_contract`,
`required_token_lifecycle`, `required_runtime_auth`, `setup_blocks`,
`browser_site_acceptance_tests`, and verification commands so agents can
coordinate browser.lexmount.cn changes without pretending a user is logging in.
The device-code auth workflow includes `auth login --device-code`,
`device_code.required_endpoints`, `device_code.required_browser_site_support`,
`connect_from_codex.site_capability_status.missing`, and `fallback_handoff`
fields so agents can explain current browser.lexmount.cn gaps and fall back to
manual env setup.
The session recovery workflow includes active session listing, single-session
inspection, keepalive status, stale-session close, and replacement session
creation steps so agents can avoid leaking sessions or consuming quota.
The case file task workflow includes case command discovery, `case schema`
inspection, action-specific schema lookup, `form_fill_case`,
`content_extraction_case`, `interactive_targeting_case`, and
`page_diagnostics_case` example discovery,
optional page/form/content/interactive/diagnostic `case scaffold` generation, `scaffold_templates`,
case validation, and
`--close-created-session` case runs with `supported_actions`,
`required_fields`, `next_commands`, `events_path`, `artifacts_dir`, `session`,
and `steps` fields for repeatable smoke tests or regressions.
The interactive targeting workflow exposes `selection_order`,
`preferred_commands`, and `alternative_commands` so agents can choose
`wait-state-role`, `exists-role`, `get-text-role`, `bounding-box-role`,
`click-role`, `click-text`, or `click-index` from snapshot evidence instead of
writing JavaScript.
The mouse interaction workflow exposes role/name and selector `double-click`,
`double-click-role`, `right-click`, `right-click-role`, role/name
`drag-role-to-role`, and selector `drag-to` routes so agents can
open editors or context menus without custom event JavaScript.
The form interaction workflow exposes form snapshots, labeled and role/name
fill steps, role/name value read/wait verification, labeled select/check steps,
submit readiness, and verification fields so agents can complete forms without
custom JavaScript.
The page diagnostics workflow also exposes console/network capture steps and
visible-state fallback commands so agents can reproduce a suspected issue before
reading `result.entries`, `result.entry_count`, and masked diagnostic fields.
The form interaction, interactive targeting, and page diagnostics workflows
start with an `inspect_action_guide` step so agents read the compact task guide
before selecting first-class actions or considering custom JavaScript.

`context list --include-reuse-state` returns `reuse_candidates`,
`recommended_context_id`, `metadata_values_redacted=true`, and
`selection_summary` without mutating persistent contexts. `context pick` and
session context reuse also return `selection_summary` with stable
counts such as `checked`, `metadata_matches`, `metadata_mismatches`,
`reusable_matches`, `locked_matches`, `unavailable_matches`, `unknown_matches`,
`recommended_next_action`, `decision_reason`, and `would_create`. Agents should
prefer `recommended_next_action` over raw status strings when deciding whether
to reuse, create, wait, or adjust filters. `context pick --dry-run` must not
create a context.
Each `context pick` candidate also includes `metadata_diagnostics` with
`metadata_source`, `metadata_keys`, `filter_keys`, `matched_keys`,
`missing_keys`, `different_keys`, and `value_redacted=true`; agents can explain
metadata mismatches from keys only without exposing metadata values. When the
API returns empty context metadata, browser-cli may use its local
context-registry entry and report `metadata_source=local_registry`.
`context status`, selected `context pick` results, and session `context_reuse`
also expose top-level `availability`, `reusable`, `locked`, `normalized_status`,
and `reuse_reason` fields so agents can classify a selected persistent context
without digging into nested `reuse`.
`doctor --json` includes a `context_registry` check for the local persistent
context metadata cache. The check reports `path`, `path_source`, `exists`,
`parent_creatable`, `readable`, `writable`, `context_count`,
`scoped_context_count`, `metadata_context_count`, `project_id_present`, and
`metadata_values_redacted=true`. A missing registry is healthy when it can be
created; invalid JSON, a non-file path, or an unwritable registry produce a
warning with `fix.code=repair_context_registry`.

## Exit Codes

- Successful commands exit with code `0`.
- Failed runtime/configuration commands exit with a non-zero code.
- Agents should still parse stdout JSON for error details.
- Runtime/configuration failures may include a `fix` object with stable `code`,
  `commands`, `env`, and `guidance`, plus `next_steps` copied from the guidance.
  Agents should follow those fields before inventing retries.
- Missing Lexmount credential failures use `fix.code=configure_credentials`
  with `auth login`, `auth export-env`, `auth status`, and `doctor --json`
  commands so agents enter setup flow before retrying browser operations.
- If stdout is closed by a downstream pipe, the CLI exits with code `141`
  without printing a Python traceback.

## Secret Handling

The CLI must not print API keys by default.

Default behavior:

- `direct-url` masks the `api_key` query parameter.
- `action ... --direct-url` masks the resolved `connect_url` when it contains
  `api_key`.
- Diagnostic and auth commands should report whether credentials exist without
  printing secret values by default.
- Eval-backed DOM/form actions that inspect, snapshot, read, wait for, set, fill,
  or clear values mask sensitive fields by default when the element looks like a
  password, token, credential, secret, authorization, or API-key field. Parse
  `value_masked`, `previous_value_masked`, `requested_value_masked`,
  `text_masked`, and related `*_length` fields to tell whether `***` represents
  a hidden value rather than the literal page value.
- `action link-snapshot` masks sensitive URL query parameter values by default
  in `href` and `absolute_url`; `action table-snapshot` and
  `action list-snapshot`, `action dialog-snapshot`, `action frame-snapshot`,
  `action wait-frame`, and `action performance-snapshot` apply the same
  masking to links, frame URLs, and performance resource URLs found inside table
  cells, list items, dialog controls, frame metadata, or timing entries. Parse
  `href_masked`, `src_masked`, `frame_url_masked`, `name_masked`, and
  `absolute_url_masked` before copying or reporting URLs.
- `action network-snapshot` and `action wait-network` mask fetch/XHR URLs by
  default and do not capture request or response bodies. Parse `url_masked` and
  `absolute_url_masked` before copying or reporting network URLs; treat
  `request_has_body` only as a boolean hint.
- `action console-snapshot` and `action wait-console` mask token-like query
  parameters and key/value text in captured console/page-error entries and the
  reported page URL. Parse `text_masked`, `filename_masked`, and `url_masked`
  before copying console output, script filenames, or page URLs.
- `auth status`, `auth token-info`, `auth refresh`, `auth logout`, and `doctor`
  may report local `device_token` metadata such as project id, token id, scopes,
  expiration, and refresh-needed state, but must never print access or refresh
  token values.
- `auth status` and `doctor` report `runtime_auth` so agents do not confuse
  local scoped-token metadata with browser-action readiness. Read
  `runtime_auth.usable`, `runtime_auth.source`,
  `runtime_auth.fallback_missing_env`,
  `runtime_auth.bearer_runtime.available`, and
  `runtime_auth.bearer_runtime.required_support`. Device-token runtime auth
  remains unavailable until the SDK accepts bearer tokens, the API accepts
  `Authorization: Bearer` for scoped browser permissions, and the browser
  gateway can authorize CDP websocket connections without an `api_key` query
  parameter.
- When env API-key credentials are incomplete, `auth status` reports
  `missing_env` plus a `fix` object that starts with
  `browser-cli reference get --id usable_status --metadata-only` and
  `browser-cli reference get --id usable_status`, followed by safe Connect from
  Codex setup commands and no API key values.
- `auth scopes` reports the stable Connect from Codex scope catalog without
  credentials or secrets: `known_scopes`, `default_scopes`, `scopes`,
  `permission_count`, `risk`, `destructive`, `unknown_scopes`, and the
  repeatable `scope` query parameter. With `--include-site-contract`, it also
  reports `browser_site_contract.url`, `device_code_url`, `scope_ui_fields`,
  `required_query_parameters`, `site_capability_status`,
  `browser_site_acceptance_tests`, and token lifecycle requirements for
  browser.lexmount.cn.
- `auth login` reports top-level `flow`, `selected_flow`, `available`,
  `manual_env_available`, and `device_code_available` so agents can choose the
  currently usable setup path without inferring it from nested flow metadata.
  For device-code mode, default output remains a manual fallback when no
  endpoint is configured; with an explicit endpoint it may also report
  `authenticated`, `credentials_saved`, `device_code.verification_uri_complete`,
  `polling`, and `credentials` without printing access, refresh, or raw
  device-code values.
- `auth connect-requirements` reports the browser.lexmount.cn `/connect/codex`
  implementation contract without credentials: `connect_from_codex.url`,
  `connect_from_codex.device_code_url`, `site_capabilities`,
  `site_capability_status`, `required_device_code_endpoints`,
  `required_api_contract`, `required_token_lifecycle`,
  `required_runtime_auth`, `setup_blocks`, `browser_site_acceptance_tests`, and
  verification commands.
- `auth login` nested `connect_from_codex` output may also include
  `required_runtime_auth` so agents can report browser.lexmount.cn, SDK, and
  gateway gaps from either the login handoff or the standalone requirements
  command.
- `action guide` reports compact action routes for `form_interaction`,
  `interactive_targeting`, `content_extraction`, `browser_state_management`,
  `file_upload`, `dialog_frame_handling`, `navigation_flow`,
  `link_navigation`, `visual_capture`, `semantic_waits`, `menu_keyboard_flow`,
  `mouse_interaction`, `page_diagnostics`, and `state_waits`, including inspect, preferred,
  fallback, and verification commands plus the custom JavaScript boundary for
  each task. Page diagnostics can include
  `set-viewport` to stabilize responsive screenshots and layout checks.
  Visual capture exposes `set-viewport`, `screenshot-role`,
  `screenshot-selector`, full-page `screenshot`, and bounded `text-snapshot`
  routes so agents can collect visual evidence before custom JavaScript.
  Semantic waits expose `wait-role`, `wait-text`, `wait-state-role`,
  `wait-attribute-role`, `wait-count`, and semantic verification routes so
  agents can avoid polling JavaScript for user-visible readiness predicates.
  Content extraction should expose first-class `outline-snapshot`,
  `text-snapshot`, `link-snapshot`, `table-snapshot`, `list-snapshot`,
  `accessibility-snapshot`, and bounded `snapshot` routes so agents can avoid
  page-specific scraping JavaScript for common read-only extraction tasks.
  Browser state management should expose first-class `storage-get`,
  `storage-set`, `storage-remove`, `storage-clear`, `wait-storage`,
  `cookie-get`, `cookie-set`, `cookie-delete`, `cookie-clear`, and
  `wait-cookie` routes for local/session storage and document.cookie-visible
  cookies.
  File upload should expose first-class `form-snapshot`, `inspect`, and
  `set-file-input` routes so agents can attach local files without clicking OS
  file pickers or writing upload-specific JavaScript.
  Dialog/frame handling should expose first-class `dialog-snapshot`,
  `wait-dialog`, `frame-snapshot`, and `wait-frame` routes so agents can handle
  modal prompts and embedded apps before custom JavaScript.
  Menu/keyboard flow should expose first-class `hover-role`, `focus-role`,
  `press-role`, `wait-attribute-role`, `list-snapshot`, and `press-key` routes
  so agents can handle menus, popovers, listboxes, and global shortcuts before
  custom JavaScript.
  Navigation flow should expose first-class `open-url`, `reload`, `go-back`,
  `go-forward`, `wait-url`, `wait-title`, and `wait-load-state` routes so
  agents can navigate and verify page transitions before custom JavaScript.
  Link navigation should expose first-class `link-snapshot`, `click-role`,
  `click-text`, `open-url`, `wait-url`, and `page-info` routes so agents can
  choose, inspect, activate, and verify links while honoring `href_masked` and
  `absolute_url_masked` before custom JavaScript.
  State waits should expose first-class `wait-load-state`, `wait-url`,
  `wait-state-role`, `wait-attribute-role`, `wait-network`, `wait-console`,
  `wait-storage`, and `wait-cookie` routes so agents can avoid sleeps and
  custom JavaScript for common asynchronous state transitions.
- `auth export-env` reports top-level `usable`, `unusable_exports`,
  `contains_secret_values`, `contains_secret_placeholders`,
  `safe_to_paste_in_chat`, `local_shell_only`, `secret_env`, `safety`,
  `setup_block`, and `verification` so agents can distinguish directly
  runnable local-shell export commands from placeholder, masked, or secret
  output before copying or running them.
- `doctor` includes an `auth_export_env_contract` check so missing or invalid
  `auth export-env` safety metadata becomes an actionable warning instead of an
  agent-side guess.
- `doctor` includes an `auth_login_contract` check so missing or invalid
  `auth login` handoff metadata becomes actionable before agents guide setup.
  It reports `required_handoff_fields`, `missing_handoff_fields`,
  `required_setup_blocks`, `missing_setup_blocks`, `invalid_fields`,
  `setup_block_ids`, `copyable_commands`, `local_env_names`, `verification`,
  `secret_policy`, `connect_from_codex_url`, and `missing_runtime_auth`.
- `doctor` includes a `device_code_contract` check so missing or invalid
  `auth login --device-code` pending/fallback metadata becomes actionable. It
  reports `missing_device_code_fields`,
  `missing_required_device_code_endpoints`,
  `missing_required_browser_site_support`, `fallback_handoff_setup_block_ids`,
  `missing_fallback_setup_blocks`, `site_capability_status`, and
  `missing_runtime_auth`.
- `doctor` includes a `connect_from_codex_contract` check so missing
  browser-site capabilities, `browser_site_acceptance_tests`, token lifecycle,
  runtime auth, or device-code API contract fields become actionable warnings
  before browser.lexmount.cn implementers rely on stale setup guidance.
- `auth refresh` may report `refresh_needed`, `has_refresh_token`,
  `refresh_available`, `refreshed`, `reason`,
  `token_lifecycle_base_url_source`, `refresh_endpoint`, `remote_refresh`, and
  refreshed `credentials` metadata, but must not print token values. Without a
  configured token lifecycle endpoint, it reports `refresh_available=false`.
  With `--token-base-url`, `LEXMOUNT_BROWSER_TOKEN_BASE_URL`, or
  `LEXMOUNT_BROWSER_DEVICE_CODE_BASE_URL`, it may call
  `POST /api/auth/token/refresh`; `remote_refresh` may report `attempted`,
  `ok`, `status_code`, `error`, `message`, `endpoint`,
  `response_payload_source`, `response_summary`, and `saved`. The CLI sends
  `grant_type=refresh_token`, `credential_kind`, `project_id`, `token_id`, and
  `requested_scopes`; refresh responses may return token fields at the top level
  or under `token`, `device_token`, `credential`, or `credentials`, with
  camelCase token keys normalized before saving.
- `reference list` and `reference get` expose packaged agent reference docs as
  JSON. `reference get --metadata-only` omits markdown content, and unknown ids
  fail as JSON with `error=unknown_reference` plus `available_references`.
- `example list` and `example get` expose packaged agent playbooks and case
  files as JSON. `example get --metadata-only` omits content, and unknown ids
  fail as JSON with `error=unknown_example` plus `available_examples`.
- `case schema` returns `supported_actions`, `required_fields`, per-action
  `actions`, top-level target/session schema, optional `--names-only`, and
  action-specific output with `--action`. Supported case actions include the
  original page actions plus agent primitives and semantic form/targeting
  actions such as `observe`, `act`, `extract`, `fill`, `fill-label`,
  `fill-role`, `click-label`, `click-role`, `click-text`, `wait-text`,
  `get-value-role`, `get-text-role`, `exists-role`, `select-label`,
  `select-role`, `check-role`, `uncheck-role`, `hover-role`, `press-role`,
  `scroll-into-view-role`, `form-snapshot`, `interactive-snapshot`, and
  `accessibility-snapshot`, plus navigation/status checks such as `page-info`,
  `wait-url`, `wait-title`, and `wait-load-state`.
- `case scaffold` returns a valid starter case spec and serialized YAML/JSON
  content, can write it to `--output`, refuses to overwrite without
  `--overwrite`, and reports `next_commands` for validate/run.
- `case run` masks direct `connect_url` values in stdout and its event log by
  default when they contain `api_key` or the current local API key.
- `doctor --smoke-session` reports `browser_smoke_session` at the top level and
  in `checks[]`; it may include a temporary `session_id` and cleanup status,
  but must not print direct connect URLs or token values.
- `doctor` reports `browser_cli.version_source` to show whether the browser-cli
  version came from installed package metadata or the package fallback.
- `doctor` reports an `agent_references` check with `required_references`,
  `missing_required_references`, `invalid_references`, and
  `checked_references`. Treat `status=warn` as a signal to run
  `browser-cli reference get --id skill_positioning`,
  `browser-cli reference get --id connect_from_codex`,
  `browser-cli reference get --id quickstart`,
  `browser-cli reference get --id usable_status`,
  `browser-cli reference get --id action_playbook`, or reinstall browser-cli
  before relying on the full Codex Skill setup or action guidance.
- `doctor` reports an `agent_examples` check with `required_examples`,
  `missing_required_examples`, `invalid_examples`, and `checked_examples`.
  YAML case examples include `case_valid` and `case_errors`; treat
  `status=warn` as a signal to run `browser-cli example list`, inspect
  `browser-cli example get --id setup_verification_playbook`,
  `browser-cli example get --id auth_lifecycle_playbook`,
  `browser-cli example get --id persistent_context_playbook`, or the
  invalid example, or reinstall browser-cli before relying on packaged
  playbooks or case examples.
- `doctor` reports a `command_catalog` check with `required_commands`,
  `missing_required_commands`, `required_workflows`,
  `missing_required_workflows`, `required_workflow_steps`,
  `missing_required_workflow_steps`, `invalid_workflow_command_references`,
  `required_agent_entrypoints`, `missing_required_agent_entrypoints`,
  `invalid_agent_entrypoint_command_references`, `schema_version`,
  `command_count`, `workflow_count`, and `agent_entrypoint_count` so agents can
  detect an installed CLI that is too old for the Skill workflow, missing
  critical workflow steps such as cleanup, or referencing commands absent from
  the parser-backed catalog. The required command set covers setup commands, reference/example discovery, case
  scaffold/validate/run, and core browser actions such as act, press, press-role,
  press-key,
  hover, hover-role, scroll, scroll-into-view, scroll-into-view-role, set-viewport,
  reload, go-back, go-forward,
  screenshot-selector, screenshot-role,
  wait-url, wait-title, wait-load-state, wait-network-idle,
  get-text, get-text-role, exists, exists-role, count, wait-count,
  wait-state, wait-state-role, query, inspect,
  get-attribute, get-attribute-role, wait-attribute, wait-attribute-role, bounding-box, bounding-box-role,
  select-option, select-label, select-role, check, uncheck, check-label,
  observe, act, extract, check-role, uncheck-label, uncheck-role, click-label, click-text, click-role, click-index,
  double-click, double-click-role, drag-role-to-role, drag-to, right-click, right-click-role,
  focus, focus-role, fill, fill-label, fill-role, get-value, get-value-role,
  wait-value, wait-value-role,
  link-snapshot, table-snapshot, list-snapshot, text-snapshot, dialog-snapshot,
  wait-dialog, frame-snapshot, wait-frame, performance-snapshot, network-snapshot,
  wait-network, console-snapshot, wait-console, outline-snapshot,
  storage-get, storage-set, storage-remove, storage-clear, wait-storage,
  cookie-get, cookie-set, cookie-delete, cookie-clear, wait-cookie, wait-text,
  wait-role, blur, blur-role,
  clear, clear-role, set-value, set-file-input, dispatch-event, submit,
  form-snapshot,
  accessibility snapshot, and interactive-only snapshot.
- `doctor` reports an `action_guides` check with
  `required_action_guides`, `missing_required_action_guides`,
  `required_guide_fields`, `invalid_action_guides`,
  `invalid_guide_command_references`, `schema_version`, and `guide_count` so
  agents can detect an installed CLI whose task-specific guides are too old or
  reference commands missing from the parser-backed catalog before custom
  JavaScript.
- `doctor` reports a `case_schema` check with `required_case_actions`,
  `required_case_scaffold_templates`, `missing_required_case_actions`,
  `missing_supported_actions`, `missing_action_schemas`,
  `missing_case_scaffold_templates`, `checked_case_scaffold_templates`,
  `invalid_case_scaffold_templates`, `invalid_action_schemas`, `schema_version`,
  `action_count`, and `supported_action_count` so agents can detect an
  installed CLI whose case runner or packaged starter cases are too old for
  repeatable semantic, storage/cookie, content, interactive-targeting, and
  diagnostic smoke tests.
- Credential-related `doctor` fixes and the aggregated `repair_plan` may report
  `connect_from_codex` with safe `/connect/codex` URLs, `open_command`,
  `device_code_url`, requested scopes, `site_capability_status`,
  `required_token_lifecycle`, `required_runtime_auth`, setup blocks,
  browser-site requirements, and verification commands. Their command lists
  should point agents at the packaged `usable_status` reference before auth
  setup so current usable boundaries are explicit. This handoff must not contain
  API key values or direct connect URLs.
- `auth logout` may report local credential file deletion metadata,
  `revoke_endpoint`, `remote_revoke`, and `revoked`, but must not print token
  values or unset environment variables. With `--revoke` and a configured token
  lifecycle endpoint, it may call `POST /api/auth/token/revoke`; without one,
  it reports `revoke_available=false`. `remote_revoke` may report
  `token_type_hint` and endpoint `revoked` confirmation; explicit
  `revoked=false` must not be treated as a successful remote revoke.

Explicit reveal behavior:

- `direct-url --reveal-url` may print the full URL.
- `action ... --reveal-connect-url` may print the full resolved connect URL.
- Auth helpers may use `--reveal-secrets` only in a trusted local shell.

Agents must not ask users to paste revealed secrets into chat, logs, docs, or
commits.

## Error Names

Known error names include:

- `configuration_error`: missing or invalid local configuration.
- `browser_parallel_limit_reached`: active session quota or parallel limit.
- `BrowserRuntimeError`: local runtime validation or browser runtime failure.
- SDK-normalized Lexmount API errors when the runtime exposes structured error
  payloads.

Agents should treat unknown `error` values as retryable only when the surrounding
workflow makes retry safe.

## Action Target Contract

Browser actions must receive exactly one target:

```bash
--session-id <session_id>
--connect-url <cdp_websocket_url>
--direct-url
```

Passing none or more than one target returns a JSON error. Prefer `--session-id`
for normal workflows because it avoids printing direct browser URLs.
The `commands` catalog marks action entries with `browser_target.exactly_one_of`
so agents can discover this requirement without parsing help text.

## Compatibility

New commands may add fields, but should not remove or rename the stable fields
above without a major compatibility decision.

Tests in `tests/test_json_contract.py` guard the core shape and masking
invariants.
