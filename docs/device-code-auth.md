# Device-Code Authorization for browser-cli

This document defines the long-term authorization flow for `browser-cli` and
Codex-style agents. It is intentionally backend-facing: browser.lexmount.cn and
the Lexmount API need to implement the protocol before the CLI can exchange a
browser approval for scoped local credentials.

## Why

The current setup flow relies on users copying `LEXMOUNT_API_KEY` and
`LEXMOUNT_PROJECT_ID` from browser.lexmount.cn into a trusted local shell. That
works, but it is not ideal for agents:

- Users may accidentally paste secrets into chat.
- Long-lived API keys are harder to scope, rotate, and revoke.
- Agents need a reliable way to request the minimum permissions for one project.
- Codex should be able to ask the user for approval in the browser and then
  continue locally without manual API key copying.

## Actors

- User: logged in to browser.lexmount.cn.
- CLI: `browser-cli auth login`, running locally.
- Agent: Codex or another local process that invokes `browser-cli`.
- Authorization service: browser.lexmount.cn / Lexmount API auth endpoints.
- Browser API: session, context, action, and diagnostics APIs.

## Scope Model

Recommended scopes:

```text
browser.sessions:create
browser.sessions:list
browser.sessions:read
browser.sessions:close
browser.contexts:create
browser.contexts:list
browser.contexts:read
browser.contexts:delete
browser.actions:run
browser.diagnostics:read
```

Default agent scope set:

```text
browser.sessions:create
browser.sessions:list
browser.sessions:read
browser.sessions:close
browser.contexts:create
browser.contexts:list
browser.contexts:read
browser.actions:run
browser.diagnostics:read
```

Destructive scope `browser.contexts:delete` should be opt-in.

## Device-Code Flow

### 1. Start

CLI request:

```http
POST /v1/auth/device/code
Content-Type: application/json
```

```json
{
  "client_name": "browser-cli",
  "client_version": "0.1.0",
  "device_name": "Codex workstation",
  "project_id": null,
  "requested_scopes": [
    "browser.sessions:create",
    "browser.sessions:list",
    "browser.sessions:read",
    "browser.sessions:close",
    "browser.contexts:create",
    "browser.contexts:list",
    "browser.contexts:read",
    "browser.actions:run",
    "browser.diagnostics:read"
  ],
  "audience": "lexmount-browser"
}
```

Response:

```json
{
  "device_code": "dc_...",
  "user_code": "ABCD-EFGH",
  "verification_uri": "https://browser.lexmount.cn/connect/codex",
  "verification_uri_complete": "https://browser.lexmount.cn/connect/codex?user_code=ABCD-EFGH",
  "expires_in": 600,
  "interval": 5
}
```

CLI behavior:

- Print JSON containing `verification_uri_complete`, `user_code`, `expires_at`,
  and requested scopes.
- In the current manual handoff, `auth login --open` opens the Connect from
  Codex URL. In the future device-code mode, the same flag should open
  `verification_uri_complete`.
- Poll only after displaying the approval instructions.

### 2. Browser Approval

The approval page should show:

- Project name and Project ID.
- Device name and CLI/client name.
- Requested scopes with readable descriptions.
- Expiration policy.
- Buttons: Approve, Deny.
- Link to existing API key/scoped token management.

If no project is selected, the page should make project selection part of the
approval flow. The resulting token must be bound to exactly one project.

### 3. Poll

CLI request:

```http
POST /v1/auth/device/token
Content-Type: application/json
```

```json
{
  "device_code": "dc_...",
  "client_name": "browser-cli"
}
```

Pending response:

```json
{
  "error": "authorization_pending",
  "message": "The user has not approved this device yet."
}
```

Successful response:

```json
{
  "access_token": "lxat_...",
  "refresh_token": "lxrt_...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "project_id": "project_uuid",
  "api_base_url": "https://api.lexmount.cn",
  "scopes": [
    "browser.sessions:create",
    "browser.sessions:list",
    "browser.sessions:read"
  ],
  "token_id": "tok_..."
}
```

Required error names:

- `authorization_pending`
- `slow_down`
- `access_denied`
- `expired_token`
- `invalid_device_code`
- `invalid_scope`
- `project_required`
- `rate_limited`

## Token Storage

The CLI must never store tokens in the Codex skill directory or repository.

Preferred storage:

- macOS: Keychain, service `lexmount-browser-cli`.
- Windows: Credential Manager.
- Linux: Secret Service when available.

Fallback storage:

```text
~/.config/lexmount/browser-cli/credentials.json
```

Fallback file requirements:

- Mode `0600` on POSIX.
- Contains only scoped tokens, not long-lived raw API keys.
- Records token id, project id, base URL, scopes, expiration, and created time.

Suggested local credential shape:

```json
{
  "kind": "device_token",
  "project_id": "project_uuid",
  "api_base_url": "https://api.lexmount.cn",
  "access_token": "lxat_...",
  "refresh_token": "lxrt_...",
  "expires_at": "2026-06-25T12:00:00Z",
  "scopes": ["browser.sessions:create"],
  "token_id": "tok_..."
}
```

Current CLI support:

- `browser-cli auth status`, `browser-cli auth token-info`,
  `browser-cli auth logout`, and
  `browser-cli doctor` read the fallback credentials file,
  `LEXMOUNT_BROWSER_CREDENTIALS_FILE`, or
  `--credentials-file`.
- Output includes safe metadata such as `auth_source`, `runtime_auth_usable`,
  `device_token.valid`, `device_token.expired`, `device_token.refresh_needed`,
  `device_token.scopes`, and `device_token.token_id`.
- `browser-cli auth token-info --required-scope <scope>` reports
  `scope_check.required_scopes`, `scope_check.missing_scopes`, and
  `scope_check.satisfied`.
- `browser-cli auth logout` removes the local fallback credentials file and
  reports `deleted`, `present_before`, `present_after`, `revoke_requested`, and
  `revoke_available`.
- `browser-cli auth logout --revoke` is accepted for forward compatibility but
  currently reports `revoke_available=false` until browser.lexmount.cn exposes
  remote token revoke.
- Output never includes access or refresh token values.
- Until browser API bearer-token support lands, `runtime_auth_usable` remains
  false for device tokens and browser actions still require env API-key
  credentials.
- `browser-cli doctor --smoke-session` currently validates env API-key browser
  runtime access by creating and closing a temporary session after API
  connectivity passes.

## CLI Commands

Future command behavior:

```bash
browser-cli auth login --open
browser-cli auth status
browser-cli auth token-info
browser-cli auth logout
browser-cli auth refresh
```

`auth status` should report:

- auth source: env vars, device token, or missing
- project id
- base URL
- expiration
- scopes
- whether a refresh is needed

It must mask token values.

`auth logout` currently removes local credentials. Remote revoke remains future
behavior:

```bash
browser-cli auth logout --revoke
```

## Doctor Integration

`browser-cli doctor --json` should detect the auth source:

- env API key
- device token
- missing

When a device token is active, doctor should check:

- token exists
- token not expired
- required scopes present
- API reachable
- project id matches token project

`doctor --smoke-session` currently validates env API-key credentials by
creating and closing a temporary browser session. Once bearer-token runtime
support lands, it should validate device-code tokens the same way.

## API Compatibility

Browser APIs should accept either:

- existing API key/project env configuration
- bearer token from device-code flow

Recommended request header:

```http
Authorization: Bearer lxat_...
```

The token should carry or resolve project id server-side. If APIs still require
project id for routing, accept an explicit project id only when it matches the
token's project.

## Revocation

Add token management to browser.lexmount.cn:

- list active agent/device tokens
- show device name, client, scopes, project, created time, expiration, last used
- revoke token
- rotate token
- filter by project

Revoked tokens must fail API calls with a structured auth error.

Suggested error:

```json
{
  "error": "token_revoked",
  "message": "This device token has been revoked."
}
```

## Security Requirements

- Device codes must expire quickly, recommended 10 minutes.
- User codes should be short enough to type but resistant to guessing.
- Polling must enforce `interval` and return `slow_down` when clients poll too
  fast.
- Access tokens should be short-lived.
- Refresh tokens should be revocable and bound to client/device metadata.
- Tokens must be scoped to one project.
- Destructive scopes must be visually emphasized in the approval UI.
- Approval and token events should be auditable.

## Implementation Milestones

1. Backend device-code endpoints with project-bound scoped token issuance.
2. browser.lexmount.cn approval UI under `/connect/codex`.
3. CLI `auth login --device-code` polling and local credential storage.
4. CLI `auth status/logout/token-info/refresh`.
5. Browser API bearer-token support.
6. `doctor` token/scopes/expiration checks.
7. Token management UI for revoke, rotate, expiration, and audit.
