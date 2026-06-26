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

`browser-cli commands` is the machine-readable command discovery surface. It
returns `schema_version`, `groups`, `command_count`, `commands`, `json_output`,
`secret_policy`, and `agent_entrypoints`; `--names-only` returns compact command
names, and `--group <name>` filters by command group.

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
  in `href` and `absolute_url`; `action table-snapshot` applies the same
  masking to links found inside table cells. Parse `href_masked` and
  `absolute_url_masked` before copying or reporting URLs.
- `auth status`, `auth token-info`, `auth refresh`, `auth logout`, and `doctor`
  may report local `device_token` metadata such as project id, token id, scopes,
  expiration, and refresh-needed state, but must never print access or refresh
  token values.
- `auth refresh` may report `refresh_needed`, `has_refresh_token`,
  `refresh_available`, `refreshed`, and `reason`, but must not print token
  values. Until the remote refresh endpoint exists, it reports
  `refresh_available=false`.
- `doctor --smoke-session` may report a temporary `session_id` and cleanup
  status, but must not print direct connect URLs or token values.
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
