# Auth Lifecycle Playbook

Use this example when Codex or another agent needs to guide a user from
browser.lexmount.cn authorization to a local shell that can run browser actions,
without copying secrets into chat.

## 1. Inspect The Auth Workflows

Start with the installed workflow contracts:

```bash
browser-cli commands --workflow connect_from_codex_auth
browser-cli commands --workflow device_code_auth
browser-cli commands --workflow scoped_token_lifecycle
browser-cli example get --id auth_lifecycle_playbook --metadata-only
```

Read every workflow step's `read` array before choosing a setup path. If the
task is to implement or diagnose browser.lexmount.cn itself, also inspect:

```bash
browser-cli commands --workflow connect_from_codex_site_requirements
browser-cli reference get --id connect_from_codex
browser-cli auth connect-requirements --checklist
browser-cli auth scopes --include-site-contract
```

## 2. Check Current Credentials

Use safe metadata commands first:

```bash
browser-cli auth status
browser-cli auth scopes
browser-cli auth token-info --required-scope browser.actions:run
```

Read:

- `configured`
- `auth_source`
- `runtime_auth_usable`
- `runtime_auth.usable`
- `runtime_auth.source`
- `runtime_auth.bearer_runtime.required_support`
- `missing_env`
- `device_token.present`
- `device_token.valid`
- `device_token.expired`
- `device_token.refresh_needed`
- `device_token.scopes`
- `scope_check.satisfied`
- `scope_check.missing_scopes`

Do not treat a saved device token as usable for browser actions while
`runtime_auth.usable=false`. Until bearer-token runtime support is available,
browser actions still need local env credentials.

## 3. Choose A Secret-Safe Login Path

Use manual env handoff as the stable fallback:

```bash
browser-cli auth login
```

Read:

- `selected_flow`
- `available`
- `manual_env_available`
- `device_code_available`
- `connect_from_codex.url`
- `connect_from_codex.requested_scope_details`
- `connect_from_codex.required_runtime_auth`
- `handoff.copyable_commands`
- `handoff.setup_blocks`
- `handoff.local_env`
- `handoff.verification.doctor_command`
- `handoff.secret_policy`

Use `browser-cli auth login --open` only when the user wants the local browser
opened from the terminal, then inspect `open_result`. Never paste the full
direct connect URL, API key, access token, refresh token, or revealed export
commands into chat.

## 4. Handle Device-Code Login

When the user asks for device-code login, inspect the device-code path:

```bash
browser-cli auth login --device-code
browser-cli commands --workflow device_code_auth
```

Read:

- `available`
- `reason`
- `device_code.available`
- `device_code.required_endpoints`
- `device_code.required_browser_site_support`
- `device_code.verification_uri`
- `device_code.verification_uri_complete`
- `device_code.user_code`
- `polling.requested`
- `polling.authenticated`
- `credentials.saved`
- `credentials.credentials_file`
- `credentials.device_token.valid`
- `fallback_flow`
- `fallback_handoff.setup_blocks`

Use `browser-cli auth login --device-code --wait` only after approval
instructions are visible and the endpoint is configured. If `available=false`,
guide the user through the manual env fallback from `fallback_handoff`.

## 5. Export Local Env Commands Safely

Use export-env to generate local-shell setup text:

```bash
browser-cli auth export-env
browser-cli auth export-env --from-current
```

Read:

- `usable`
- `unusable_exports`
- `safe_to_paste_in_chat`
- `local_shell_only`
- `contains_secret_values`
- `contains_secret_placeholders`
- `safety`
- `setup_block`
- `verification.doctor_command`

Only the user should run real export commands in the local shell. With
`--from-current`, `LEXMOUNT_API_KEY` remains masked unless
`--reveal-secrets` is explicitly used in a trusted terminal.

## 6. Refresh Or Remove Saved Tokens

For scoped token lifecycle work, use explicit credentials files when possible:

```bash
browser-cli auth refresh --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth logout --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth logout --credentials-file ~/.config/lexmount/browser-cli/credentials.json --revoke
```

Read refresh fields:

- `present`
- `valid`
- `refresh_needed`
- `has_refresh_token`
- `refresh_available`
- `refreshed`
- `reason`
- `refresh_endpoint`
- `remote_refresh.attempted`
- `remote_refresh.status_code`
- `remote_refresh.response_payload_source`
- `remote_refresh.response_summary`
- `credentials.saved`

Read logout fields:

- `deleted`
- `present_before`
- `present_after`
- `revoke_requested`
- `revoke_available`
- `revoked`
- `revoke_endpoint`
- `remote_revoke.attempted`
- `remote_revoke.status_code`
- `remote_revoke.token_type_hint`

`auth refresh` calls a remote refresh endpoint only when
`LEXMOUNT_BROWSER_TOKEN_BASE_URL` or `--token-base-url` is configured. Treat
`remote_revoke.revoked=false` as not confirmed. These commands do not print raw
access or refresh token values.

## 7. Verify Browser Readiness

After credentials change, run:

```bash
browser-cli doctor --json
```

Proceed with browser sessions only when:

- `ok=true`
- `failed=0`
- `ready_for_browser_actions=true`

If `api_connectivity.status=skipped`, do not treat live browser access as
verified. If doctor warns or fails, read `failed_checks`, `warning_checks`,
`repair_plan.commands`, `repair_plan.env`, `repair_plan.guidance`, and
`repair_plan.connect_from_codex.url`.

For stronger proof in a trusted local shell:

```bash
browser-cli doctor --smoke-session
```

## 8. browser.lexmount.cn Requirements

For a smooth Codex authorization flow, browser.lexmount.cn should provide:

- A Connect from Codex page that clearly shows the selected project, requested
  scopes, risk/destructive flags, expiration, and copyable local-shell setup.
- Scoped API-key issuance for the manual env fallback.
- Device-code endpoints for `POST /api/auth/device/code`,
  `POST /api/auth/device/token`, and visible approval instructions.
- Token lifecycle endpoints for `POST /api/auth/token/refresh` and
  `POST /api/auth/token/revoke`.
- A safe copy contract: placeholders are safe for chat, real secrets are local
  shell only, and full direct URLs are not shown in agent messages.
- A verification command that points back to `browser-cli doctor --json`.

Until those capabilities and bearer runtime support are complete, agents should
use the manual env fallback and report the missing site/runtime capability
fields instead of inventing a different login flow.
