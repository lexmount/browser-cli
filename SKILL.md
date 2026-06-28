---
name: browser-cli
description: Operate Lexmount remote browsers with browser-cli. Use when Codex or another agent needs to create, list, inspect, keep alive, or close browser sessions; manage persistent contexts, pick reusable contexts, or detect locked contexts; guide authentication with auth status/token-info/refresh/logout/connect-requirements/export-env/login; verify installation, environment, and API connectivity with doctor; discover installed commands with commands; validate or run JSON/YAML browser case files; open pages, read page info, wait for selectors/states/roles/URLs/load/network/text/form values/dialogs/frames/console entries/fetch-XHR entries, click/type/fill/select/check/hover/press/scroll, inspect/query forms/links/tables/lists/text/dialogs/frames/performance/network/console/outlines/accessibility/interactive elements, manage storage/cookies, navigate history, screenshot, evaluate JavaScript, snapshot pages, or verify credentials without custom Playwright.
---

# browser-cli

Use `browser-cli` as the primary interface for Lexmount browser automation.
Prefer CLI commands and JSON output over importing Python internals or writing
ad hoc Playwright scripts.

## Setup

Check that the CLI is available:

```bash
browser-cli --help
browser-cli --version
browser-cli commands --names-only
browser-cli commands --workflows-only
```

If it is not installed, install it with:

```bash
uv tool install git+https://github.com/lexmount/browser-cli.git
```

Require credentials in the local shell:

```bash
export LEXMOUNT_API_KEY="<api-key>"
export LEXMOUNT_PROJECT_ID="<project-id>"
```

Do not ask the user to paste secrets into chat. Direct the user to
`https://browser.lexmount.cn` for China region credentials. The China region
defaults to `https://api.lexmount.cn`; set `LEXMOUNT_BASE_URL` only when a
non-default API endpoint is needed.

Use local auth helpers instead of handling secrets in chat:

```bash
browser-cli auth status
browser-cli auth status --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth token-info --required-scope browser:actions
browser-cli auth refresh --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth logout --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth connect-requirements
browser-cli auth login
browser-cli auth login --open
browser-cli auth login --device-code
browser-cli auth export-env
```

When setup or auth is unclear, inspect the installed workflow contract first:

```bash
browser-cli commands --workflow setup_and_verify
browser-cli commands --workflow connect_from_codex_site_requirements
browser-cli commands --workflow connect_from_codex_auth
browser-cli commands --workflow device_code_auth
browser-cli commands --workflow scoped_token_lifecycle
```

When the task is to inspect or explain what browser.lexmount.cn must implement,
run `browser-cli auth connect-requirements` or
`browser-cli commands --workflow connect_from_codex_site_requirements` first.
Read `connect_from_codex.site_capability_status.missing`,
`required_device_code_endpoints`, `required_api_contract`,
`required_token_lifecycle`, `setup_blocks`, and `verification.doctor_command`.

When `auth login` returns `handoff`, use it as the setup contract: open
`connect_from_codex_url` or `login_url`, follow `copyable_commands`, require the
listed `local_env` variables in the user's local shell, and run the
`verification.doctor_command`. Check top-level `selected_flow`, `available`,
`manual_env_available`, and `device_code_available` before choosing a setup
path. Use `browser-cli auth login --open` or
`handoff.open_command` only when the user wants the local browser opened; then
inspect `open_result`. Follow `secret_policy`: never paste `LEXMOUNT_API_KEY`,
revealed export output, or full direct URLs into chat.
If the user asks for device-code login, run `browser-cli auth login --device-code`
and parse `available`, `reason`, `device_code.required_endpoints`, and
`fallback_handoff`; while `available` is false, guide the user through the
manual env fallback. Prefer
`browser-cli commands --workflow device_code_auth` when the task is to inspect
or explain the device-code authorization path.

`auth export-env` prints placeholders by default. With `--from-current`, it
still masks `LEXMOUNT_API_KEY` unless `--reveal-secrets` is explicitly used in
a trusted local terminal. Check top-level `usable` and `unusable_exports` before
treating returned `commands` as directly runnable.

`auth status` reports `auth_source`, `runtime_auth_usable`, and safe
`device_token` metadata when a local scoped-token credentials file exists or
`--credentials-file` is passed. When env credentials are incomplete, read
`missing_env` and the `fix` object instead of inventing setup steps. Use
`auth token-info --required-scope <scope>` to check scoped-token coverage. Use
`auth refresh --credentials-file <path>` to
inspect `refresh_needed`, `has_refresh_token`, `refresh_available`, `refreshed`,
and `reason`; remote refresh is pending until browser.lexmount.cn exposes the
refresh API. Use `auth logout --credentials-file <path>` to remove local
device-token metadata without changing environment variables; `--revoke` only
reports remote revoke pending until browser.lexmount.cn exposes the revoke API.
These commands never report access or refresh token values. Until bearer-token
runtime support lands, continue to require env API-key credentials for browser
actions when `runtime_auth_usable` is false.

For scoped token checks, refresh, or local logout, prefer the lifecycle workflow:

```bash
browser-cli commands --workflow scoped_token_lifecycle
```

Follow its `read` fields for `device_token.valid`, `scope_check.missing_scopes`,
`refresh_available`, `refreshed`, `revoke_available`, and `warnings`.

After credentials are configured, run:

```bash
browser-cli doctor --json
browser-cli doctor --smoke-session
```

Run `browser-cli doctor --json` before the first browser action in a thread,
after credential changes, or when a session/context/action command fails for an
unclear reason. Use `browser-cli doctor --smoke-session` only when you need
proof that credentials can create and close a temporary browser session. `--json`
is accepted as a no-op compatibility flag at the top level and after
subcommands; browser-cli output is always JSON. Parse the JSON before deciding
what to do:

- `ok: true` and `failed: 0`: continue with browser work.
- `ready_for_browser_actions: true`: browser sessions/actions can be attempted.
- `browser_smoke_session` with `status: "pass"`: a temporary browser session was
  created and closed.
- `browser_smoke_session` with `status: "fail"`: follow its `fix` commands,
  especially a manual `session close` command when `created` is true and
  `closed` is false.
- `command_catalog` with `status: "warn"`: inspect
  `missing_required_commands` and follow its `fix` guidance before relying on
  the full Skill workflow.
- `repair_plan`: prefer its aggregated `commands`, `env`, `guidance`, and
  `fixes` when explaining setup repair steps.
- `warnings > 0` or a check with `status: "warn"`: continue only after
  reporting warning check names and any `fix` guidance; warnings usually mean
  local installation/PATH hygiene rather than unusable credentials.
- `ok: false`: stop before creating sessions, inspect `checks` with
  `status: "fail"`, and follow each check's `fix` object when present.
- `api_connectivity` with `status: "skipped"`: do not treat live API access as
  verified.

Use `browser-cli doctor --skip-api` only for offline setup checks or when the
user explicitly asks to avoid a live API call. Do not treat a skipped API check
as proof that browser sessions will work.

Run `browser-cli commands --workflows-only` for a compact agent workflow map,
`browser-cli commands --workflow <id>` for one task path, and
`browser-cli commands --names-only` or `browser-cli commands --group action`
when the installed CLI version is uncertain or before writing custom JavaScript.
Use the catalog's `browser_target.exactly_one_of`, `required_options`,
`required_one_of`, `json_output`, `secret_policy`, `agent_entrypoints`, and
`agent_workflows` fields instead of parsing `--help` text. Follow each
workflow step's `read` array first; it names the auth availability, export
usability, and context reuse fields that drive the next decision.

## Workflow

If setup is uncertain, run `browser-cli commands --workflow setup_and_verify`,
then `browser-cli auth status` and `browser-cli doctor --json` before creating a
session. If credentials are missing, run
`browser-cli commands --workflow connect_from_codex_auth`, then
`browser-cli auth login` and guide the user to set local environment variables.
If the task is to coordinate browser.lexmount.cn changes, run
`browser-cli commands --workflow connect_from_codex_site_requirements` and
`browser-cli auth connect-requirements`.

If an existing session is stale, inactive, or possibly consuming quota, inspect
the recovery workflow before creating more sessions:

```bash
browser-cli commands --workflow session_recovery
```

Use its `read` fields for `sessions`, `session.status`, `final_status`,
`closed`, and replacement `session.session_id`.

For a one-off task:

```bash
browser-cli commands --workflow one_off_page_task
```

Then follow the returned steps, typically:

```bash
browser-cli session create
browser-cli action open-url --session-id <session_id> --url <url>
browser-cli action snapshot --session-id <session_id>
browser-cli action wait-selector --session-id <session_id> --selector <selector>
browser-cli session close --session-id <session_id>
```

For repeatable smoke tests, demos, or regression checks, prefer a case file
workflow before writing browser automation code:

```bash
browser-cli commands --workflow case_file_task
```

Validate first, then run with `--close-created-session`. Read `valid`,
`errors`, `step_count`, `events_path`, `artifacts_dir`, `session`, and `steps`.

For form tasks, prefer the more specific form workflow:

```bash
browser-cli commands --workflow form_interaction
```

Follow its `read` fields for `form-snapshot`, `fill-label`, `select-label`,
`check-label`, `wait-role`, `click-role`, and verification steps before falling
back to custom JavaScript.

For visible buttons, links, menus, and repeated controls, prefer the interactive
targeting workflow before writing selectors or JavaScript:

```bash
browser-cli commands --workflow interactive_targeting
```

Read `interactive-snapshot` and optional `accessibility-snapshot` fields, then
choose `click-role`, `click-text`, or `click-index` from the workflow's
preferred commands and verify with `page-info`, `wait-url`, or `wait-text`.

For page failures, fetch/XHR issues, or runtime errors, prefer the diagnostic
workflow before writing custom probes:

```bash
browser-cli commands --workflow page_diagnostics
```

Install console/network capture before reproducing the issue, then read the
workflow's console, network, and visible-state steps.

Use persistent contexts only when cookies, login state, or storage should
survive across sessions:

```bash
browser-cli commands --workflow persistent_login_state
```

Then use the returned context-selection steps, typically:

```bash
browser-cli context create
browser-cli context status --context-id <context_id>
browser-cli session create --context-id <context_id> --context-mode read_write
browser-cli session create --context-metadata-json '{"purpose":"codex-login"}' --create-context-if-missing --context-mode read_write
browser-cli context pick --metadata-json '{"purpose":"codex-login"}' --create-if-missing --dry-run
```

Use `read_write` for login/setup work that should update cookies or storage. Use
`read_only` when inspecting an existing logged-in state. Before deleting a context,
confirm that the task no longer needs its login state.

Parse `context_reuse` from the session result. Reuse only when
`context_reuse.selected` is true. Read top-level `availability`, `reusable`,
`locked`, `normalized_status`, and `reuse_reason`. Prefer `availability` over
raw status strings: `available` can be reused, `locked` means busy, and
`unavailable` needs a different context. If candidates include `locked: true`,
report that a busy context was skipped. Inspect `selection_summary` for
`locked_matches`, `metadata_mismatches`, `reusable_matches`,
`recommended_next_action`, `decision_reason`, and `would_create`. Prefer
`recommended_next_action` when deciding whether to reuse, create, wait, or
adjust filters. Use
`context pick --metadata-json <json> --dry-run` before creating a session when
you need to explain context reuse or avoid mutating persistent login state. Use
the workflow's optional `context status --context-id <context_id>` step before
reuse whenever a specific context id came from older notes, user input, or a
previous run.

If a command fails, parse the JSON error first. For configuration or credential
errors, stop browser work and guide the user to configure local environment
variables. For missing selectors, take a fresh snapshot or screenshot before
choosing another selector.

Write custom Playwright only when the CLI cannot express the task and explain
why the CLI was insufficient.

Always close sessions created for temporary automation unless the user asks to
keep them open. Always close temporary sessions.

## Commands

Authentication:

```bash
browser-cli auth status
browser-cli auth status --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth token-info --required-scope browser:actions
browser-cli auth refresh --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth logout --credentials-file ~/.config/lexmount/browser-cli/credentials.json
browser-cli auth connect-requirements
browser-cli auth login
browser-cli auth login --open
browser-cli auth login --device-code
browser-cli auth export-env
browser-cli auth export-env --from-current --include-base-url
```

Diagnostics:

```bash
browser-cli --version
browser-cli version
browser-cli commands
browser-cli commands --names-only
browser-cli commands --group action
browser-cli commands --workflows-only
browser-cli commands --workflow setup_and_verify
browser-cli commands --workflow connect_from_codex_site_requirements
browser-cli commands --workflow connect_from_codex_auth
browser-cli commands --workflow device_code_auth
browser-cli commands --workflow scoped_token_lifecycle
browser-cli commands --workflow session_recovery
browser-cli commands --workflow one_off_page_task
browser-cli commands --workflow case_file_task
browser-cli commands --workflow persistent_login_state
browser-cli commands --workflow form_interaction
browser-cli commands --workflow interactive_targeting
browser-cli commands --workflow page_diagnostics
browser-cli doctor
browser-cli doctor --json
browser-cli doctor --smoke-session
browser-cli doctor --skip-api
```

Session lifecycle:

```bash
browser-cli session create
browser-cli session create --context-metadata-json '{"purpose":"codex-login"}' --create-context-if-missing
browser-cli session list
browser-cli session get --session-id <session_id>
browser-cli session close --session-id <session_id>
browser-cli session keepalive --session-id <session_id>
```

Context lifecycle:

```bash
browser-cli context create
browser-cli context list
browser-cli context get --context-id <context_id>
browser-cli context status --context-id <context_id>
browser-cli context pick --metadata-json '{"purpose":"codex-login"}'
browser-cli context pick --metadata-json '{"purpose":"codex-login"}' --create-if-missing --dry-run
browser-cli context pick --metadata-json '{"purpose":"codex-login"}' --create-if-missing
browser-cli context delete --context-id <context_id>
```

Browser action details live in [references/action-playbook.md](references/action-playbook.md).
Read that reference when selecting between semantic actions, diagnosing
fetch/XHR or console failures, handling forms, menus, dialogs, frames, storage,
cookies, or deciding whether custom JavaScript is necessary. It contains action
examples, structured `result` fields, action-specific masking rules, common
task recipes, and the browser target contract.

Core action rules:

- Prefer built-in CLI actions over writing custom JavaScript.
- Inspect first with `snapshot`, `interactive-snapshot`, `accessibility-snapshot`,
  `form-snapshot`, `list-snapshot`, `text-snapshot`, `dialog-snapshot`,
  `frame-snapshot`, or `outline-snapshot` when page structure is unclear.
- Prefer semantic actions such as `wait-role`, `click-role`, `click-text`,
  `click-index`, `fill-label`, `select-label`, and `check-label` before raw
  selectors when the page provides visible labels or accessibility names.
- Use selector actions such as `exists`, `count`, `wait-state`, `query`,
  `inspect`, `get-attribute`, `wait-text`, `get-text`, `click`, `type`,
  `set-value`, `select-option`, `check`, and `uncheck` only when a stable
  selector is known.
- For page failures, run `browser-cli commands --workflow page_diagnostics`;
  install console/network capture before reproducing the issue.
- Parse structured result fields and `*_masked` flags before concluding that an
  action changed the page or before reporting values.
- Use `eval` only for page-local work not covered by a first-class action, and
  keep the expression small.

## Output

Parse command output as JSON. Check `ok` first, then inspect `command`,
`error`, `message`, and command-specific fields. Do not log revealed API keys.
It is safe to include `--json` at the top level or after subcommands because it
does not change output.
For `commands`, use the parser-backed catalog to discover installed commands and
options before guessing. Prefer `--workflows-only` or `--workflow <id>` for
agent task flows, `--names-only` for quick availability checks, and `--group action`
before choosing browser actions.
Do not paste API keys, Project IDs, or full direct connect URLs into chat, docs,
commits, screenshots, or test fixtures. By default, browser direct URLs are
masked. Use reveal flags only for local debugging in a trusted shell.
Failure messages and payloads mask `api_key`, token-like query parameters, and
the current `LEXMOUNT_API_KEY` value.
If `error` is `argument_error`, read the JSON `usage` field and rerun a
corrected command; do not parse stderr.
For `auth`, report credential presence, missing variables, and next steps; do
not report API key values. For `auth login`, prefer the `handoff` object's
`copyable_commands`, `open_command`, `local_env`, `verification`, and
`secret_policy` fields. If `--open` was used, inspect `open_result`; if
`opened` is false, show the returned URL or fallback login guidance without
blocking on the local browser.
For device-token metadata in `auth status`, `auth token-info`, `auth refresh`,
`auth logout`, or `doctor`, report `auth_source`, `runtime_auth_usable`,
`device_token.valid`, `device_token.expired`, `device_token.refresh_needed`,
`device_token.scopes`, and `scope_check`; do not report token values. For
`auth refresh`, report `refresh_needed`, `has_refresh_token`,
`refresh_available`, `refreshed`, and `reason`; it does not call a remote
refresh endpoint yet. For `auth logout`, report `deleted`, `present_before`,
`present_after`,
`revoke_requested`, `revoke_available`, and `warnings`; it does not unset env
vars. Do not start browser actions from a device token while
`runtime_auth_usable` is false.
For `auth export-env`, use placeholders or masked commands unless the user
explicitly asked to reveal secrets locally.
For `doctor`, inspect `ready_for_browser_actions`, `failed_checks`,
`warning_checks`, `skipped_checks`, and `repair_plan` first. Report failed or
warning check names without revealing API keys. If `browser_smoke_session`
exists, report whether it created and closed the temporary session and follow
its manual close command when cleanup failed. Prefer `repair_plan.commands`,
`repair_plan.env`, and `repair_plan.guidance`; fall back to per-check `fix`
objects only when needed.
