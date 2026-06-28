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
`references/action-playbook.md`, with `content_command`, `package_resource`,
`load_when`, `related_workflows`, `covers`, and `grep_patterns` so agents can
load detailed action guidance only when needed. `browser-cli reference list`
returns packaged reference metadata, and
`browser-cli reference get --id action_playbook` returns the installed markdown
content as JSON.
`agent_examples` describes packaged common-task examples and case files.
`browser-cli example list` returns example metadata, and
`browser-cli example get --id page_inspection_case` returns an installed example
case file or playbook as JSON.
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
The scoped token lifecycle workflow includes token validity, scope coverage,
scope catalog lookup, refresh availability, browser readiness, and local logout/revoke-pending fields
without exposing token values.
The Connect from Codex site requirements workflow includes
`auth scopes --include-site-contract`, `auth connect-requirements`,
`browser_site_contract.scope_ui_fields`,
`connect_from_codex.site_capability_status.missing`,
`required_device_code_endpoints`, `required_api_contract`,
`required_token_lifecycle`, `setup_blocks`, and verification commands so
agents can coordinate browser.lexmount.cn changes without pretending a user is
logging in.
The device-code auth workflow includes `auth login --device-code`,
`device_code.required_endpoints`, `device_code.required_browser_site_support`,
`connect_from_codex.site_capability_status.missing`, and `fallback_handoff`
fields so agents can explain current browser.lexmount.cn gaps and fall back to
manual env setup.
The session recovery workflow includes active session listing, single-session
inspection, keepalive status, stale-session close, and replacement session
creation steps so agents can avoid leaking sessions or consuming quota.
The case file task workflow includes case command discovery, `case schema`
inspection, optional `case scaffold` generation, case validation, and
`--close-created-session` case runs with `supported_actions`,
`required_fields`, `next_commands`, `events_path`, `artifacts_dir`, `session`,
and `steps` fields for repeatable smoke tests or regressions.
The interactive targeting workflow exposes `selection_order`,
`preferred_commands`, and `alternative_commands` so agents can choose
`wait-state-role`, `exists-role`, `get-text-role`, `bounding-box-role`,
`click-role`, `click-text`, or `click-index` from snapshot evidence instead of
writing JavaScript.
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

`context pick` and session context reuse return `selection_summary` with stable
counts such as `checked`, `metadata_matches`, `metadata_mismatches`,
`reusable_matches`, `locked_matches`, `unavailable_matches`, `unknown_matches`,
`recommended_next_action`, `decision_reason`, and `would_create`. Agents should
prefer `recommended_next_action` over raw status strings when deciding whether
to reuse, create, wait, or adjust filters. `context pick --dry-run` must not
create a context.
`context status`, selected `context pick` results, and session `context_reuse`
also expose top-level `availability`, `reusable`, `locked`, `normalized_status`,
and `reuse_reason` fields so agents can classify a selected persistent context
without digging into nested `reuse`.

## Exit Codes

- Successful commands exit with code `0`.
- Failed runtime/configuration commands exit with a non-zero code.
- Agents should still parse stdout JSON for error details.

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
- When env API-key credentials are incomplete, `auth status` reports
  `missing_env` plus a `fix` object with safe Connect from Codex setup commands
  and no API key values.
- `auth scopes` reports the stable Connect from Codex scope catalog without
  credentials or secrets: `known_scopes`, `default_scopes`, `scopes`,
  `permission_count`, `risk`, `destructive`, `unknown_scopes`, and the
  repeatable `scope` query parameter. With `--include-site-contract`, it also
  reports `browser_site_contract.url`, `device_code_url`, `scope_ui_fields`,
  `required_query_parameters`, `site_capability_status`, and token lifecycle
  requirements for browser.lexmount.cn.
- `auth login` reports top-level `flow`, `selected_flow`, `available`,
  `manual_env_available`, and `device_code_available` so agents can choose the
  currently usable setup path without inferring it from nested flow metadata.
- `auth connect-requirements` reports the browser.lexmount.cn `/connect/codex`
  implementation contract without credentials: `connect_from_codex.url`,
  `connect_from_codex.device_code_url`, `site_capabilities`,
  `site_capability_status`, `required_device_code_endpoints`,
  `required_api_contract`, `required_token_lifecycle`, `setup_blocks`, and
  verification commands.
- `action guide` reports compact action routes for `form_interaction`,
  `interactive_targeting`, `page_diagnostics`, and `state_waits`, including
  inspect, preferred, fallback, and verification commands plus the custom
  JavaScript boundary for each task. Page diagnostics can include
  `set-viewport` to stabilize responsive screenshots and layout checks.
- `auth export-env` reports top-level `usable` and `unusable_exports` so agents
  can distinguish directly runnable export commands from placeholder or masked
  commands.
- `auth refresh` may report `refresh_needed`, `has_refresh_token`,
  `refresh_available`, `refreshed`, and `reason`, but must not print token
  values. Until the remote refresh endpoint exists, it reports
  `refresh_available=false`.
- `reference list` and `reference get` expose packaged agent reference docs as
  JSON. `reference get --metadata-only` omits markdown content, and unknown ids
  fail as JSON with `error=unknown_reference` plus `available_references`.
- `example list` and `example get` expose packaged agent playbooks and case
  files as JSON. `example get --metadata-only` omits content, and unknown ids
  fail as JSON with `error=unknown_example` plus `available_examples`.
- `case schema` returns `supported_actions`, `required_fields`, per-action
  `actions`, top-level target/session schema, optional `--names-only`, and
  action-specific output with `--action`.
- `case scaffold` returns a valid starter case spec and serialized YAML/JSON
  content, can write it to `--output`, refuses to overwrite without
  `--overwrite`, and reports `next_commands` for validate/run.
- `doctor --smoke-session` may report a temporary `session_id` and cleanup
  status, but must not print direct connect URLs or token values.
- `doctor` reports `browser_cli.version_source` to show whether the browser-cli
  version came from installed package metadata or the package fallback.
- `doctor` reports an `agent_references` check with `required_references`,
  `missing_required_references`, `invalid_references`, and
  `checked_references`. Treat `status=warn` as a signal to run
  `browser-cli reference get --id action_playbook` or reinstall browser-cli
  before relying on the full Codex Skill action guidance.
- `doctor` reports an `agent_examples` check with `required_examples`,
  `missing_required_examples`, `invalid_examples`, and `checked_examples`.
  YAML case examples include `case_valid` and `case_errors`; treat
  `status=warn` as a signal to run `browser-cli example list`, inspect the
  invalid example, or reinstall browser-cli before relying on packaged
  playbooks or case examples.
- `doctor` reports a `command_catalog` check with `required_commands`,
  `missing_required_commands`, `required_workflows`,
  `missing_required_workflows`, `required_workflow_steps`,
  `missing_required_workflow_steps`, `schema_version`, `command_count`, and
  `workflow_count` so agents can detect an installed CLI that is too old for
  the Skill workflow or missing critical workflow steps such as cleanup. The
  required command set covers setup commands, reference/example discovery, case
  scaffold/validate/run, and core browser actions such as press, press-role,
  hover, hover-role, scroll, scroll-into-view-role, set-viewport,
  get-text, get-text-role, exists, exists-role, wait-state-role,
  get-attribute-role, wait-attribute-role, bounding-box-role,
  select-option, select-role, check, uncheck, check-role,
  uncheck-role, click-text, click-role,
  focus-role, fill-label, fill-role, get-value-role, wait-value-role,
  blur-role, clear-role,
  accessibility snapshot, and interactive-only snapshot.
- Credential-related `doctor` fixes and the aggregated `repair_plan` may report
  `connect_from_codex` with a safe `/connect/codex` URL, `open_command`,
  requested scopes, setup blocks, and verification commands. This handoff must
  not contain API key values or direct connect URLs.
- `auth logout` may report local credential file deletion metadata, but must
  not print token values or unset environment variables.

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
