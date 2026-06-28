# Agent Playbook

Use this playbook when turning `browser-cli` into a Codex skill or another
agent-facing browser tool.

## First Checks

Prefer local auth and doctor checks before writing browser code:

```bash
browser-cli --help
browser-cli commands --names-only
browser-cli commands --workflows-only
browser-cli commands --workflow setup_and_verify
browser-cli commands --workflow connect_from_codex_site_requirements
browser-cli commands --workflow connect_from_codex_auth
browser-cli commands --workflow device_code_auth
browser-cli commands --workflow scoped_token_lifecycle
browser-cli commands --workflow session_recovery
browser-cli commands --workflow case_file_task
browser-cli commands --workflow form_interaction
browser-cli commands --workflow interactive_targeting
browser-cli commands --workflow page_diagnostics
browser-cli auth status
browser-cli auth connect-requirements
browser-cli auth login
browser-cli auth export-env
browser-cli doctor --json
```

If credentials are missing, parse the `auth login` JSON and guide the user
through `handoff.connect_from_codex_url`, `handoff.copyable_commands`, and
`verification.doctor_command`; use
`browser-cli commands --workflow connect_from_codex_auth` as the machine-readable
auth setup path. Keep `LEXMOUNT_API_KEY`, revealed export output, and full
direct browser URLs out of chat.

If the task is to improve browser.lexmount.cn, read
`browser-cli commands --workflow connect_from_codex_site_requirements` and run
`browser-cli auth connect-requirements`. Report
`connect_from_codex.site_capability_status.missing`,
`required_device_code_endpoints`, `required_api_contract`,
`required_token_lifecycle`, and `setup_blocks` instead of inventing a site
checklist.

If the user asks for device-code authorization, read
`browser-cli commands --workflow device_code_auth` and run
`browser-cli auth login --device-code`. While it reports `available=false`, use
the `fallback_handoff` manual env path and report the missing
browser.lexmount.cn device-code endpoints.

When a local scoped token exists, use
`browser-cli commands --workflow scoped_token_lifecycle` before manual token
handling. Read `device_token.valid`, `scope_check.missing_scopes`,
`refresh_available`, `refreshed`, and `revoke_available`.

Run doctor before the first browser action, after credential changes, and when
session, context, or action commands fail for unclear reasons:

```bash
browser-cli auth status
browser-cli doctor --json
```

Use `ready_for_browser_actions` before starting browser work. If it is false,
follow `repair_plan.commands`, `repair_plan.env`, and `repair_plan.guidance`
instead of guessing setup repairs from raw error text.

Use command discovery before guessing new action names:

```bash
browser-cli commands --workflows-only
browser-cli commands --group action
browser-cli commands --group action --names-only
```

Read `agent_workflows`, `required_options`, `required_one_of`, and
`browser_target.exactly_one_of` from the catalog instead of parsing help text.
For each workflow step, follow its `read` array first; it names auth availability,
export usability, and context reuse fields that decide the next safe action.

When validating a fresh local setup, run the stronger live check:

```bash
browser-cli doctor --smoke-session
```

Treat `browser_smoke_session.status == "pass"` with `created=true` and
`closed=true` as proof that credentials can create and close a temporary browser
session. If the smoke session was created but not closed, follow the returned
`fix.commands` before creating more sessions.

Do not ask the user to paste API keys into chat. Direct them to
`https://browser.lexmount.cn` and keep secrets in the local shell.

Only run browser session commands after `ready_for_browser_actions` is true or
after you have reported the exact warning/failure checks and the user still
wants to proceed.

## One-Off Page Task

Use a temporary session and close it when finished:

```bash
browser-cli commands --workflow session_recovery
browser-cli commands --workflow one_off_page_task
browser-cli session create --browser-mode light
browser-cli action open-url --session-id <session_id> --url <url>
browser-cli action snapshot --session-id <session_id>
browser-cli action screenshot --session-id <session_id> --output /tmp/page.png
browser-cli session close --session-id <session_id>
```

When a previous session may still be active, follow `session_recovery` first:
list active sessions, inspect the candidate session, keep it alive only if it
is still needed, close stale sessions, then create a replacement session.

For forms, read the dedicated workflow before choosing fields:

```bash
browser-cli commands --workflow form_interaction
browser-cli action form-snapshot --session-id <session_id> --selector form
browser-cli action fill-label --session-id <session_id> --label "Email" --text "me@example.com"
browser-cli action click-role --session-id <session_id> --role button --name "Submit"
```

For visible buttons, links, menus, and repeated controls, read the targeting
workflow before writing selectors:

```bash
browser-cli commands --workflow interactive_targeting
browser-cli action interactive-snapshot --session-id <session_id> --max-nodes 80
browser-cli action accessibility-snapshot --session-id <session_id> --max-nodes 120
browser-cli action click-role --session-id <session_id> --role button --name "Submit"
browser-cli action click-text --session-id <session_id> --text "Submit"
```

For page failures, runtime errors, or fetch/XHR issues, install diagnostics
before reproducing the suspected action:

```bash
browser-cli commands --workflow page_diagnostics
browser-cli action console-snapshot --session-id <session_id> --install-only
browser-cli action network-snapshot --session-id <session_id> --install-only
# Run the action under investigation, then read both buffers.
browser-cli action console-snapshot --session-id <session_id> --max-entries 50
browser-cli action network-snapshot --session-id <session_id> --max-entries 50
```

## Persistent Login State

Use a context only when cookies, local storage, or login state should survive:

```bash
browser-cli commands --workflow persistent_login_state
browser-cli context create --metadata-json '{"purpose":"login"}'
browser-cli session create --context-id <context_id> --context-mode read_write
```

When reusing persistent login state by metadata, prefer:

```bash
browser-cli context status --context-id <context_id>
browser-cli context pick --metadata-json '{"purpose":"login"}' --create-if-missing --dry-run
browser-cli context pick --metadata-json '{"purpose":"login"}' --create-if-missing
browser-cli session create --context-metadata-json '{"purpose":"login"}' --create-context-if-missing --context-mode read_write
```

Do not reuse a context whose `availability` is `locked` or `unavailable` for a
new read/write session. Close the session that holds it, or create a new
context. Use `context status` first when a context id comes from notes, user
input, or a previous run.
Use the dry-run output to read
`selection_summary.recommended_next_action` first, then explain
`selection_summary.decision_reason`, `selection_summary.locked_matches`,
`selection_summary.reusable_matches`, and `would_create` before mutating
persistent login state.

## Case Files

Use case files when the task is repeatable or should leave artifacts:

```bash
browser-cli commands --workflow case_file_task
browser-cli case validate --file examples/cases/page-inspection.yaml
browser-cli case run --file examples/cases/page-inspection.yaml --close-created-session
```

Case files are good for smoke tests, regression checks, and demos because they
produce structured JSON summaries and event logs. Read `valid`, `errors`,
`step_count`, `events_path`, `artifacts_dir`, `session`, and `steps`.

## Action Selection

Prefer built-in actions over writing JavaScript:

```bash
browser-cli action open-url --session-id <session_id> --url <url>
browser-cli action wait-title --session-id <session_id> --title <title-or-fragment>
browser-cli action wait-selector --session-id <session_id> --selector <selector>
browser-cli action click --session-id <session_id> --selector <selector>
browser-cli action type --session-id <session_id> --selector <selector> --text <text>
browser-cli action page-info --session-id <session_id>
browser-cli action snapshot --session-id <session_id>
```

When expanded action commands are available, use them for common browser
operations such as reading page info, checking existence, reading text,
waiting on title changes, waiting for text to disappear, scrolling, selecting
options, checking boxes, hovering, pressing selector keys, and sending
active/global shortcut keys.

Prefer `browser-cli commands --workflow interactive_targeting` and semantic
actions such as `wait-role`, `click-role`, `click-text`,
`fill-label`, `select-label`, `check-label`, `interactive-snapshot`, and
`accessibility-snapshot` before writing page-specific JavaScript.

Use `action eval` only when the CLI does not yet expose the browser operation as
a command.
