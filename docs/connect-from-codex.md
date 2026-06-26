# Connect from Codex for browser.lexmount.cn

This document describes the browser.lexmount.cn changes that make
`browser-cli` and the future Codex skill easy to authorize without asking users
to paste secrets into chat.

## Goals

- Give users one clear page for connecting Lexmount Browser to Codex and other
  local agents.
- Show the active project, API key status, install commands, env commands, and
  verification commands in one copyable flow.
- Encourage scoped, revocable agent keys instead of long-lived general keys.
- Let `browser-cli auth login` evolve from manual guidance into a real
  device-code/OAuth authorization flow.
- Let `browser-cli doctor` prove that local setup, credentials, API reachability,
  project access, and browser session creation are healthy.

## Non-Goals

- Do not store API keys in the Codex skill directory.
- Do not require users to paste API keys, Project IDs, or tokens into Codex chat.
- Do not require browser.lexmount.cn to know the user's local shell type.
  The page can show a default `posix` block and let the CLI generate shell-specific
  output.
- Do not make the first version depend on a complete OAuth server. A scoped key
  wizard plus copyable env commands is a useful first milestone.

## Page

Add a dedicated page:

```text
https://browser.lexmount.cn/connect/codex
```

The page should require login and project selection. If no project is selected,
it should route the user through project selection before showing agent setup.

Recommended sections:

1. Project
   - Project name
   - Project ID with copy button
   - API host, normally `https://api.lexmount.cn`
   - Region indicator
2. Install
   - `uv tool install git+https://github.com/lexmount/browser-cli.git`
   - `browser-cli --help`
3. Authorize
   - Current agent key status
   - Create scoped API key button
   - Show created API key once with copy controls
   - Revoke button for each agent key
   - Expiration display and optional rotation action
4. Configure Local Shell
   - Copyable env block:
     `export LEXMOUNT_API_KEY=...`
     `export LEXMOUNT_PROJECT_ID=...`
     optional `export LEXMOUNT_BASE_URL=https://api.lexmount.cn`
   - Safety reminder: paste into local shell, not chat
   - Link to `browser-cli auth export-env` for shell-specific output
5. Verify
   - `browser-cli auth status`
   - `browser-cli doctor --json`
   - `browser-cli session list`
6. Persistent Login Contexts
   - Link to context docs
   - `browser-cli context pick --metadata-json '{"purpose":"codex-login"}' --create-if-missing`
   - `browser-cli session create --context-metadata-json '{"purpose":"codex-login"}' --create-context-if-missing --context-mode read_write`
   - Explain `available` versus `locked`

## Scoped Agent API Keys

The key wizard should create keys intended for local agents. Suggested fields:

- Name, defaulting to `Codex on <device name>`.
- Expiration, with presets such as 1 day, 7 days, 30 days, and no expiration if
  the account policy allows it.
- Permissions:
  - `browser.sessions:create`
  - `browser.sessions:list`
  - `browser.sessions:read`
  - `browser.sessions:close`
  - `browser.contexts:create`
  - `browser.contexts:list`
  - `browser.contexts:read`
  - `browser.contexts:delete`
- Optional action scope:
  - `browser.actions:run`
- Optional read-only mode for inspection agents:
  - sessions list/read
  - contexts list/read
  - snapshot/eval if the runtime supports per-action auth scopes

Keys should expose metadata for audit and cleanup:

- key id
- display name
- created time
- expiration time
- last used time
- last used IP or region, if available
- scopes
- status: active, expired, revoked

The full API key value should be shown only once after creation. Later page loads
should show only the key id, masked preview, status, scopes, and revoke/rotate
actions.

## Local CLI Contract

Current short-term CLI commands:

```bash
browser-cli auth status
browser-cli auth token-info
browser-cli auth refresh
browser-cli auth logout
browser-cli auth login
browser-cli auth login --open
browser-cli auth export-env
browser-cli doctor --json
browser-cli doctor --smoke-session
```

Expected behavior after the website page exists:

- `browser-cli auth login` prints
  `https://browser.lexmount.cn/connect/codex`; `browser-cli auth login --open`
  opens it in the local default browser and reports `open_result`.
- `browser-cli auth status` remains local and never calls the website unless a
  token-based flow is configured.
- `browser-cli auth token-info` remains local and reports safe scoped-token
  metadata plus scope checks without printing token values.
- `browser-cli auth refresh` remains local for now and reports
  `refresh_available=false`, `refreshed=false`, and an actionable `reason` until
  the website/API exposes token refresh.
- `browser-cli auth logout` remains local, removes fallback device-token
  metadata, and reports `revoke_available=false` when `--revoke` is requested
  until the website/API exposes remote revoke.
- `browser-cli auth export-env` remains local and masks secrets by default.
- `browser-cli doctor --json` checks local env, package availability, API
  connectivity, and optionally session creation when the user opts in.
- `browser-cli doctor --smoke-session` creates and closes a temporary browser
  session after API connectivity passes and reports `browser_smoke_session`.

The page should display the same command names so users and agents follow one
workflow.

## Device-Code/OAuth Flow

Longer term, add an authorization flow so Codex can ask the user to approve
access in the browser while the CLI receives a scoped local token.

Suggested high-level protocol:

1. CLI calls `POST /v1/auth/device/code` with:
   - client name, such as `browser-cli`
   - requested scopes
   - optional device name
2. API returns:
   - `device_code`
   - `user_code`
   - `verification_uri`
   - `verification_uri_complete`
   - `expires_in`
   - `interval`
3. CLI prints the URL and code, and optionally opens the browser.
4. User approves on browser.lexmount.cn.
5. CLI polls `POST /v1/auth/device/token`.
6. API returns a short-lived access token and optional refresh token.
7. CLI stores the token in the user's local keychain or an explicit config path,
   not in the Codex skill directory.

Required device-code states:

- pending
- approved
- denied
- expired
- rate_limited

The approval page should show the requested scopes, project, expiration, device
name, and how to revoke later.

## Doctor Integration

The Connect from Codex page should include a "Verify CLI" section with:

```bash
browser-cli doctor --json
browser-cli doctor --smoke-session
```

The page can explain expected success criteria:

- top-level `ok` is true
- `ready_for_browser_actions` is true for live browser work
- `failed_checks` and `warning_checks` are empty, or warning checks have been
  acknowledged
- `repair_plan.required` is false
- API connectivity succeeds
- Project ID matches the selected project
- API key has required scopes
- Optional browser smoke test reports `browser_smoke_session.status=pass` after
  creating and closing a session

If doctor fails, the page and support docs should point users to
`repair_plan.commands`, `repair_plan.env`, and `repair_plan.guidance` instead of
asking them to interpret raw check details.

For copy/paste UX, keep doctor output in the terminal. The page should not ask
users to upload doctor JSON unless an explicit support flow sanitizes secrets.

## Context Reuse UX

For persistent login state, the page should explain:

```bash
browser-cli context pick --metadata-json '{"purpose":"codex-login"}' --create-if-missing
browser-cli session create --context-metadata-json '{"purpose":"codex-login"}' --create-context-if-missing --context-mode read_write
```

If the website can show contexts, it should distinguish:

- `availability: "available"`: can be reused now
- `availability: "locked"`: currently attached to an active session
- `availability: "unavailable"`: select or create a different context
- deleted or expired states, if the API supports them later

Useful actions:

- copy context id
- copy session command
- show active session that locked the context, if known
- close active session, if permissions allow it
- create a new context
- delete unused context

## Security Requirements

- Never render a full API key after the one-time creation display.
- All copy buttons for secrets should be explicit and visually different from
  copy buttons for non-secret IDs.
- Warn users not to paste revealed secrets into chat, logs, docs, or commits.
- Prefer scoped keys with expiration.
- Support revoke and rotation from the same page.
- Log agent key creation, use, revocation, and expiration.
- Device-code tokens should be scoped, time-limited, and locally stored outside
  repo and skill directories.

## Milestones

1. Static Connect from Codex page
   - project id
   - install commands
   - env commands
   - verification commands
2. Scoped agent key wizard
   - create key
   - one-time reveal
   - revoke
   - expiration
   - scopes
3. Doctor-aware setup
   - page text aligned with `browser-cli doctor --json`
   - troubleshooting table driven by `repair_plan` for missing env, auth
     failure, executable PATH warnings, and quota/parallel limit
4. Context reuse support
   - explain `context pick` and metadata-based `session create`
   - show available/locked/unavailable contexts if backend exposes them
5. Device-code/OAuth
   - CLI starts auth flow
   - browser approval page
   - scoped local token
   - revoke/expire support

## Open Questions

- Should agent keys be represented as API keys, OAuth clients, or both?
- Which scopes are required by the first public `browser-cli` release?
- Should context delete be included in the default agent scope or require an
  explicit destructive permission?
- Where should CLI token storage live on macOS, Linux, and Windows?
- Should `browser-cli doctor` offer an opt-in browser smoke test that creates and
  closes a real session?
