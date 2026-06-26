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
`secret_policy`, `agent_entrypoints`, and `agent_workflows`; `--names-only`
returns compact command names, and `--group <name>` filters by command group.
Unknown command groups fail as JSON with `error=unknown_group`,
`available_groups`, and a `fix` object with commands for inspecting valid
groups.
`--workflows-only` returns a compact payload with `workflow_count`,
`agent_workflows`, and `agent_entrypoints` without the large `commands` array.
`--workflow <id>` returns one workflow as `workflow_id` and `workflow`; unknown
workflow ids fail as JSON with `error=unknown_workflow`, `available_workflows`,
and a `fix` object with commands for inspecting valid workflows.
`agent_workflows` describes ordered setup, Connect from Codex auth, one-off
page, persistent login state, and form interaction steps with `command`, `read`,
`success_condition`, `on_failure_read`, and `cleanup` hints. Command entries may
expose `aliases` on canonical commands plus `alias_of` and `canonical_name` on
alias commands, so agents can map user-facing phrasing back to the preferred
action without parsing help text.
Workflow `read` arrays include current auth availability fields, export
usability fields, and context reuse availability fields when those values drive
the next agent decision.

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
- `auth login` reports top-level `flow`, `selected_flow`, `available`,
  `manual_env_available`, and `device_code_available` so agents can choose the
  currently usable setup path without inferring it from nested flow metadata.
- `auth export-env` reports top-level `usable` and `unusable_exports` so agents
  can distinguish directly runnable export commands from placeholder or masked
  commands.
- `auth refresh` may report `refresh_needed`, `has_refresh_token`,
  `refresh_available`, `refreshed`, and `reason`, but must not print token
  values. Until the remote refresh endpoint exists, it reports
  `refresh_available=false`.
- `doctor --smoke-session` may report a temporary `session_id` and cleanup
  status, but must not print direct connect URLs or token values.
- `doctor` reports `browser_cli.version_source` to show whether the browser-cli
  version came from installed package metadata or the package fallback.
- `doctor` reports a `command_catalog` check with `required_commands`,
  `missing_required_commands`, `required_workflows`,
  `missing_required_workflows`, `required_workflow_steps`,
  `missing_required_workflow_steps`, `schema_version`, `command_count`, and
  `workflow_count` so agents can detect an installed CLI that is too old for
  the Skill workflow or missing critical workflow steps such as cleanup. The
  required command set covers setup commands plus core browser actions such as
  press, hover, scroll, get-text, exists, select-option, check, uncheck,
  click-text, click-role, fill-label, accessibility snapshot, and
  interactive-only snapshot.
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
