# Agent Playbook

Use this playbook when turning `browser-cli` into a Codex skill or another
agent-facing browser tool.

## First Checks

Prefer local auth and doctor checks before writing browser code:

```bash
browser-cli --help
browser-cli commands --names-only
browser-cli auth status
browser-cli auth login
browser-cli auth export-env
browser-cli doctor --json
```

If credentials are missing, parse the `auth login` JSON and guide the user
through `handoff.connect_from_codex_url`, `handoff.copyable_commands`, and
`verification.doctor_command`. Keep `LEXMOUNT_API_KEY`, revealed export output,
and full direct browser URLs out of chat.

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
browser-cli commands --group action
browser-cli commands --group action --names-only
```

Read `required_options`, `required_one_of`, and `browser_target.exactly_one_of`
from the catalog instead of parsing help text.

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
browser-cli session create --browser-mode light
browser-cli action open-url --session-id <session_id> --url <url>
browser-cli action snapshot --session-id <session_id>
browser-cli action screenshot --session-id <session_id> --output /tmp/page.png
browser-cli session close --session-id <session_id>
```

## Persistent Login State

Use a context only when cookies, local storage, or login state should survive:

```bash
browser-cli context create --metadata-json '{"purpose":"login"}'
browser-cli session create --context-id <context_id> --context-mode read_write
```

When reusing persistent login state by metadata, prefer:

```bash
browser-cli context pick --metadata-json '{"purpose":"login"}' --create-if-missing --dry-run
browser-cli context pick --metadata-json '{"purpose":"login"}' --create-if-missing
browser-cli session create --context-metadata-json '{"purpose":"login"}' --create-context-if-missing --context-mode read_write
```

Do not reuse a context whose `availability` is `locked` or `unavailable` for a
new read/write session. Close the session that holds it, or create a new
context.
Use the dry-run output to read
`selection_summary.recommended_next_action` first, then explain
`selection_summary.decision_reason`, `selection_summary.locked_matches`,
`selection_summary.reusable_matches`, and `would_create` before mutating
persistent login state.

## Case Files

Use case files when the task is repeatable or should leave artifacts:

```bash
browser-cli case validate --file examples/cases/page-inspection.yaml
browser-cli case run --file examples/cases/page-inspection.yaml --close-created-session
```

Case files are good for smoke tests, regression checks, and demos because they
produce structured JSON summaries and event logs.

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

Prefer semantic actions such as `wait-role`, `click-role`, `click-text`,
`fill-label`, `select-label`, `check-label`, `interactive-snapshot`, and
`accessibility-snapshot` before writing page-specific JavaScript.

Use `action eval` only when the CLI does not yet expose the browser operation as
a command.
