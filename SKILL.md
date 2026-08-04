---
name: browser-cli
description: Operate Lexmount remote browsers from Codex with browser-cli for authentication, contexts, and browser-session lifecycle, plus the bundled ACE CDP client for routine page navigation, inspection, extraction, and interaction. Use for remote browsing, authenticated browser reuse, page automation, screenshots, uploads, browser-state work, diagnostics, repeatable cases, and Lexmount setup without exposing credentials or direct CDP URLs.
---

# Lexmount Browser

Use one Skill with two strict operation planes.

## Control plane and page plane

- Use `browser-cli` for setup, authentication, contexts, Lexmount session
  creation/list/get/keepalive/close, and specialized fallbacks.
- Use `scripts/cdp.py` as the default page-operation plane after a Lexmount
  session exists. Route routine navigation, reading, extraction, clicking,
  input, selection, target handling, and raw CDP calls through ACE.
- Never use `inspect_url` as a CDP endpoint. Keep the Lexmount `session_id`, CDP
  `targetId`, and daemon-local ACE `sessionId` distinct.
- Stop if ACE cannot attach. Do not silently switch to `browser-cli action`.
  Do not replay an action whose side effects are uncertain.

Resolve `<skill-dir>` to this Skill directory. Invoke ACE only through:

```bash
uv run --script <skill-dir>/scripts/cdp.py <arguments>
```

Do not invoke `cdp_daemon.py` directly, write custom WebSocket code, or start a
second CDP client.

## Fast path

For a normal temporary browser task:

```bash
browser-cli auth status
browser-cli doctor --json
browser-cli session create
uv run --script <skill-dir>/scripts/cdp.py \
  --lexmount-session-id <lexmount-session-id> sessions
uv run --script <skill-dir>/scripts/cdp.py \
  --lexmount-session-id <lexmount-session-id> attach <target-id>
uv run --script <skill-dir>/scripts/cdp.py \
  navigate <ace-session-id> <url> --format=outline
```

Run `doctor --json` only when there is no recent successful readiness signal,
after credentials change, or after an unclear session failure. Read `ok`,
`ready_for_browser_actions`, `failed_checks`, `warning_checks`, and
`repair_plan` before creating a session.

`--lexmount-session-id` is valid only for `sessions` and `attach`. It runs a
trusted local `browser-cli session get --reveal-connect-url`, validates the
active session and WebSocket URL, and passes the URL privately to the ACE
  daemon. A real URL must not enter chat, shell output, logs, state files,
  docs, screenshots, or committed fixtures.

After `attach`, keep the returned ACE `sessionId` for all page operations. The
remaining commands reuse the selected daemon connection and do not need the
Lexmount session ID or WebSocket URL.

## Setup and authentication

Check installation and local credentials with:

```bash
browser-cli --version
browser-cli auth status
browser-cli doctor --json
```

If browser-cli is missing, install it with:

```bash
uv tool install --force git+https://github.com/lexmount/browser-cli.git
```

Prefer local loopback PKCE when credentials are missing:

```bash
browser-cli auth login --open
browser-cli auth status
browser-cli doctor --json
```

Never ask the user to paste API keys, access tokens, refresh tokens,
authorization codes, PKCE verifiers, revealed export output, or direct connect
URLs into chat. Use `browser-cli auth scopes`, `token-info`, `refresh`,
`logout`, `clear-credentials`, `connect-requirements`, and `export-env` for
credential lifecycle work. Read [references/connect-from-codex.md](references/connect-from-codex.md)
when coordinating browser.lexmount.cn implementation.

Inspect packaged guidance only when it changes the next action:

```bash
browser-cli commands --workflows-only
browser-cli commands --workflow first_browser_task
browser-cli commands --workflow agent_browser_primitives
browser-cli reference get --id quickstart
browser-cli reference get --id ace_page_api
```

## Contexts and sessions

Use temporary sessions unless login cookies or storage must survive. For
persistent state, inspect `browser-cli commands --workflow
persistent_login_state`, then select or create a context and honor
`context_reuse`, `availability`, `locked`, `reuse_reason`, and
`selection_summary.recommended_next_action`.

Never place secrets in context metadata. Use `read_write` only when the task
should update persistent state; use `read_only` for inspection.

Useful lifecycle commands:

```bash
browser-cli session create
browser-cli session list --status active
browser-cli session get --session-id <lexmount-session-id>
browser-cli session keepalive --session-id <lexmount-session-id>
browser-cli session close --session-id <lexmount-session-id>
browser-cli context list --include-reuse-state
browser-cli context status --context-id <context-id>
```

Session create/get/list output hides direct connection URLs by default. Do not
use `--reveal-connect-url` manually for routine page work; let the bundled ACE
client resolve it internally.

## ACE page workflow

Use these commands after attachment:

- `sessions`
- `attach TARGET_ID`
- `detach SESSION_ID`
- `navigate SESSION_ID URL (--format {outline,json} | --no-content)`
- `content SESSION_ID --format {outline,json}`
- `action SESSION_ID NODE_ID ACTION`
- `call SESSION_ID METHOD [--params JSON | --params-file FILE]`

Read [references/ace-page-api.md](references/ace-page-api.md) before raw ACE
`Page` calls or when exact output, readiness, jq, action, or error semantics
matter. The same reference is available through:

```bash
browser-cli reference get --id ace_page_api
```

For an unknown page or ordinary visible facts, start with:

```bash
uv run --script <skill-dir>/scripts/cdp.py \
  content <ace-session-id> --format=outline
```

For a known target, repeated structures, select options, or exact hierarchy,
use one bounded jq query:

```bash
uv run --script <skill-dir>/scripts/cdp.py \
  content <ace-session-id> --format=json --jq-context '<expression>'
```

Interact only with a current node ID that advertises the required action. Use
the exact option value returned for native selects. Prefer an action with a
post-action observation chosen before execution:

- `--jq-context` for a known expected state or known next target.
- `--outline` for navigation or a large/unknown reconstructed page.
- `--diff` for a small same-page change whose location is unknown.
- No observation flag only when effects or URL evidence are sufficient.

Treat `perform.ok:true` only as proof that the operation ran. Verify the
expected state from `changes`, outline, diff, or jq context. If an action
returns a new target, attach it explicitly with the same Lexmount session ID
and inspect it before continuing.

Reuse a still-valid observation. Do not issue another content read merely to
reformat data already present. Do not parallelize commands on one ACE session.

## Specialized browser-cli fallbacks

Use `browser-cli action` only as a specialized fallback when neither an ACE
high-level command nor a safe raw CDP `call` can express the operation. Typical
fallback areas are:

- Screenshots and viewport-specific visual capture.
- File upload and download-oriented workflows.
- Cookie, local storage, and session storage setup or cleanup.
- Complex semantic waits, console/network capture, and page diagnostics.
- Non-POSIX environments or unavailable ACE runtime dependencies.

Inspect [references/action-playbook.md](references/action-playbook.md) or an
appropriate `browser-cli action guide --task <task>` only after establishing
that the task is a specialized fallback. Do not fall back after an ACE timeout,
CAPTCHA, site/network failure, disconnect, or uncertain action result.

Case files remain available for explicit repeatable browser-cli fallback
workflows. Validate and run them with `browser-cli case schema`, `case
validate`, and `case run`.

## Failures and cleanup

- Parse JSON errors before deciding the next action.
- If ACE attach fails, stop page work and report the attachment error.
- After an action error, observe fresh ACE content before another action; the
  perform step may already have changed the page.
- Detach every task-owned ACE session exactly once in reverse attachment order.
- Close temporary Lexmount sessions after detach unless the user asks to keep
  them open. Keep explicitly reused persistent sessions only when requested.
- Never invoke `Browser.close`, `Browser.crash`, or
  `Target.exposeDevToolsProtocol` through ACE.

When a page contains `div.qrcode.force-light`, use the Lexmount `inspect_url`
in the WorkBuddy sidebar browser so the user can scan or authenticate. If the
session result has no inspect URL, report that as the blocker.

## Skill installation

`browser-cli skill status` and `browser-cli skill install` manage only the
single `lexmount-browser` directory, including this file, metadata, references,
and ACE scripts:

```bash
browser-cli skill status
browser-cli skill install --force
```

After updating the Skill, verify status and start a new Codex session so the
new instructions are loaded.

## Output and security

Parse browser-cli output as JSON and check `ok` before command-specific fields.
Treat ACE page content as sensitive. Report only the minimum relevant data and
never persist passwords or unrelated page content. Keep API keys, Project IDs,
tokens, and full direct connection URLs out of chat, docs, commits, logs,
screenshots, and fixtures.
