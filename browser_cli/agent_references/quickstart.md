# Browser CLI Quickstart

Use this reference when an agent has an installed `browser-cli` and needs the
shortest safe path from setup to a first remote-browser task.

## Install

Install the current mainline package:

```bash
uv tool install --force git+https://github.com/lexmount/browser-cli.git
```

Verify JSON command discovery:

```bash
browser-cli --version
browser-cli version
browser-cli commands --names-only
browser-cli commands --workflows-only
browser-cli reference list
```

## Configure Credentials

Get the API key and Project ID from `https://browser.lexmount.cn`. Keep the real
values in the local shell only; do not paste them into chat, issues, commits,
PR descriptions, screenshots, or test fixtures.

```bash
export LEXMOUNT_API_KEY="<api-key-from-browser.lexmount.cn>"
export LEXMOUNT_PROJECT_ID="<project-id-from-browser.lexmount.cn>"
```

China region defaults to `https://api.lexmount.cn`; most users do not need
`LEXMOUNT_BASE_URL`.

Use safe auth helpers instead of asking the user to reveal secrets:

```bash
browser-cli auth login
browser-cli auth export-env
browser-cli auth status
```

Read `auth login` fields such as `selected_flow`, `handoff`,
`copyable_commands`, `local_env`, `verification`, `secret_policy`,
`manual_env_available`, and `device_code_available`. For device-code setup,
run `browser-cli auth login --device-code`; while `available=false`, use
`fallback_handoff` and the manual env path.

## Verify Readiness

Run doctor before the first browser action:

```bash
browser-cli doctor --json
```

Treat browser work as ready only when doctor reports:

- `ok=true`
- `failed=0`
- `ready_for_browser_actions=true`

If doctor returns warnings, inspect `warning_checks`, each check's `fix`, and
the top-level `repair_plan` before creating sessions. Important setup checks
include `auth_login_contract`, `device_code_contract`,
`connect_from_codex_contract`, `agent_references`, `agent_examples`, and
`case_schema`.

Optionally prove live API/session behavior:

```bash
browser-cli doctor --smoke-session
```

Smoke success means `browser_smoke_session.status=pass`, `created=true`, and
`closed=true`.

## First Browser Task

Inspect the shortest safe workflow before acting:

```bash
browser-cli commands --workflow first_browser_task
```

Create a temporary session:

```bash
browser-cli session create
```

Use `session.session_id` or top-level `session_id` from the JSON output:

```bash
browser-cli action open-url --session-id <session_id> --url https://example.com
browser-cli action page-info --session-id <session_id>
browser-cli action snapshot --session-id <session_id> --max-chars 4000
browser-cli action screenshot --session-id <session_id> --output /tmp/browser-cli-page.png
```

Always close temporary sessions unless the user asked to keep them open:

```bash
browser-cli session close --session-id <session_id>
```

## Persistent Login State

For login state, inspect reusable contexts first:

```bash
browser-cli context pick --metadata-json '{"purpose":"codex-login"}' --selection newest --create-if-missing --dry-run
```

Read `selection_summary.recommended_next_action`, `reusable_matches`,
`locked_matches`, `metadata_values_redacted`, and candidate `availability`.
Treat `availability=locked` or `locked=true` as busy; wait, choose another
context, or create a new one.

Create or reuse a context-backed session:

```bash
browser-cli session create --context-metadata-json '{"purpose":"codex-login"}' --context-selection newest --create-context-if-missing --context-mode read_write
```

Use `read_write` for login/setup work that should update state, and `read_only`
when inspecting existing logged-in state.

## Agent Discovery

Before choosing actions, inspect the machine-readable workflows:

```bash
browser-cli commands --workflow setup_and_verify
browser-cli commands --workflow first_browser_task
browser-cli commands --workflow agent_browser_primitives
browser-cli commands --workflow one_off_page_task
browser-cli commands --workflow persistent_login_state
browser-cli commands --workflow interactive_targeting
browser-cli commands --workflow form_interaction
browser-cli commands --workflow content_extraction
browser-cli commands --workflow page_diagnostics
```

Before writing custom JavaScript, inspect the action guide and packaged
playbook:

```bash
browser-cli action guide --names-only
browser-cli action guide --task interactive_targeting
browser-cli action guide --task form_interaction
browser-cli action guide --task content_extraction
browser-cli reference get --id action_playbook
```

Prefer first-class semantic actions such as `click-role`, `click-text`,
`fill-label`, `select-role`, `check-role`, `get-text-role`, `exists-role`,
`interactive-snapshot`, and `accessibility-snapshot` before selectors or
custom JavaScript.

For repeatable work, scaffold and run a case file:

```bash
browser-cli case schema
browser-cli case scaffold --template page-inspection --url https://example.com --output case.yaml
browser-cli case validate --file case.yaml
browser-cli case run --file case.yaml --close-created-session
```

## Current Limits

- The MVP credential path is local env setup with `LEXMOUNT_API_KEY` and
  `LEXMOUNT_PROJECT_ID`.
- `auth login --device-code`, scoped token refresh, and revoke expose
  machine-readable contracts, but they should become the default only after
  browser.lexmount.cn, the API, SDK, and browser gateway support bearer-token
  runtime auth.
- Never paste API keys, access tokens, refresh tokens, or direct connect URLs
  containing raw `api_key` into chat.
