# Setup Verification Playbook

Use this example when Codex or another agent needs to install `browser-cli`,
configure credentials safely, prove readiness, or repair a failed setup before
creating browser sessions.

## 1. Inspect The Installed CLI

Run command discovery first and parse JSON output:

```bash
browser-cli --version
browser-cli version
browser-cli commands --names-only
browser-cli commands --workflows-only
browser-cli reference list
browser-cli example list
```

Read the minimum setup references:

```bash
browser-cli reference get --id quickstart --metadata-only
browser-cli reference get --id quickstart
browser-cli reference get --id usable_status --metadata-only
browser-cli reference get --id usable_status
```

If the task is about browser.lexmount.cn implementation, also read:

```bash
browser-cli reference get --id connect_from_codex --metadata-only
browser-cli reference get --id connect_from_codex
```

## 2. Check Auth Without Revealing Secrets

Never ask the user to paste API keys, access tokens, refresh tokens, or full
direct connect URLs into chat.

```bash
browser-cli auth status
browser-cli auth scopes
browser-cli auth connect-requirements
browser-cli auth connect-requirements --checklist
```

Read these fields before choosing a setup path:

- `configured`
- `auth_source`
- `missing_env`
- `runtime_auth.usable`
- `runtime_auth.bearer_runtime.required_support`
- `repair_plan.connect_from_codex.url`
- `required_runtime_auth`
- `required_token_lifecycle`

## 3. Guide Manual Env Setup

When env credentials are missing, use the structured handoff instead of
inventing instructions:

```bash
browser-cli auth login
browser-cli auth export-env
```

Read `auth login` fields:

- `selected_flow`
- `manual_env_available`
- `device_code_available`
- `handoff.copyable_commands`
- `handoff.setup_blocks`
- `handoff.local_env`
- `handoff.verification.doctor_command`
- `handoff.secret_policy`

Read `auth export-env` fields:

- `usable`
- `unusable_exports`
- `safe_to_paste_in_chat`
- `local_shell_only`
- `contains_secret_values`
- `contains_secret_placeholders`
- `setup_block`
- `verification.doctor_command`

Only the user should place real `LEXMOUNT_API_KEY` and `LEXMOUNT_PROJECT_ID`
values in the local shell.

## 4. Handle Device-Code Requests

When the user explicitly wants device-code/OAuth, inspect the contract first:

```bash
browser-cli auth login --device-code
browser-cli commands --workflow device_code_auth
```

Read `available`, `reason`, `device_code`, `polling`, `credentials`,
`connect_from_codex.required_runtime_auth`, and `fallback_handoff`. While
`available=false`, use the manual env fallback. Use
`browser-cli auth login --device-code --wait` only after an endpoint is
configured and approval instructions are visible.

## 5. Verify Readiness

Run doctor before browser work:

```bash
browser-cli doctor --json
```

Proceed only when:

- `ok=true`
- `failed=0`
- `ready_for_browser_actions=true`

If `warnings > 0`, report `warning_checks` and follow `repair_plan.commands`,
`repair_plan.env`, `repair_plan.guidance`, and any per-check `fix` objects. If
`api_connectivity.status=skipped`, do not treat live browser work as verified.

For stronger proof, run:

```bash
browser-cli doctor --smoke-session
```

Smoke success means `browser_smoke_session.status=pass`, `created=true`, and
`closed=true`. If `created=true` and `closed=false`, run the manual
`session close` command from the check's `fix.commands`.

## 6. Start Browser Work

After doctor passes, inspect the task workflow before acting:

```bash
browser-cli commands --workflow first_browser_task
browser-cli commands --workflow one_off_page_task
browser-cli commands --workflow persistent_login_state
browser-cli action guide --task interactive_targeting
browser-cli reference get --id action_playbook
```

For a temporary smoke task:

```bash
browser-cli session create
browser-cli action open-url --session-id <session_id> --url https://example.com
browser-cli action page-info --session-id <session_id>
browser-cli action snapshot --session-id <session_id> --max-chars 4000
browser-cli session close --session-id <session_id>
```

For a login or reuse task, inspect context availability before mutating state:

```bash
browser-cli context pick --metadata-json '{"purpose":"login"}' --selection newest --create-if-missing --dry-run
browser-cli session create --context-metadata-json '{"purpose":"login"}' --context-selection newest --create-context-if-missing --context-mode read_write
browser-cli context status --context-id <context_id>
```

If `availability` is `locked` or `unavailable`, do not force reuse. Read
`selection_summary.recommended_next_action`,
`selection_summary.decision_reason`, `selection_summary.locked_matches`,
`would_create`, `metadata_diagnostics.missing_keys`,
`metadata_diagnostics.metadata_source`, and `local_registry` before deciding.
Metadata values are intentionally redacted; store labels such as purpose or
account alias, not API keys, passwords, or session secrets.

When the next browser action is unclear, choose a guide first and then use the
smallest matching command:

```bash
browser-cli action form-snapshot --session-id <session_id>
browser-cli action fill-label --session-id <session_id> --label "Email" --text user@example.com
browser-cli action fill-role --session-id <session_id> --role textbox --name "Email" --text user@example.com
browser-cli action fill --session-id <session_id> --selector "#email" --text user@example.com
browser-cli action clear-role --session-id <session_id> --role textbox --name "Search"
browser-cli action select-role --session-id <session_id> --role combobox --name "Plan" --value pro
browser-cli action check-role --session-id <session_id> --role checkbox --name "Agree"
browser-cli action wait-value-role --session-id <session_id> --role textbox --name "Email" --value user@example.com
browser-cli action blur-role --session-id <session_id> --role textbox --name "Email"
browser-cli action wait-state-role --session-id <session_id> --role button --name "Save" --state visible
browser-cli action interactive-snapshot --session-id <session_id>
browser-cli action accessibility-snapshot --session-id <session_id>
browser-cli action exists-role --session-id <session_id> --role button --name "Save"
browser-cli action get-text-role --session-id <session_id> --role heading --name "Dashboard"
browser-cli action bounding-box-role --session-id <session_id> --role button --name "Save"
browser-cli action hover-role --session-id <session_id> --role menuitem --name "Settings"
browser-cli action press-role --session-id <session_id> --role textbox --name "Search" --key Enter
browser-cli action scroll-into-view-role --session-id <session_id> --role button --name "Checkout"
browser-cli action drag-role-to-role --session-id <session_id> --source-role listitem --source-name "Todo" --target-role list --target-name "Done"
browser-cli action set-viewport --session-id <session_id> --width 1440 --height 1000
browser-cli action screenshot-selector --session-id <session_id> --selector ".receipt"
browser-cli action screenshot-role --session-id <session_id> --role dialog --name "Confirm"
browser-cli action get-attribute-role --session-id <session_id> --role link --name "Docs" --attribute href
browser-cli action wait-attribute-role --session-id <session_id> --role button --name "Save" --attribute aria-busy --value false
browser-cli action console-snapshot --session-id <session_id> --install-only
browser-cli action network-snapshot --session-id <session_id>
```

For runtime errors or fetch/XHR issues, run
`browser-cli commands --workflow page_diagnostics`, install console/network
capture, reproduce once, then read console and network snapshots. For visual
proof, set a stable viewport and prefer selector or role screenshots. For
navigation/readiness, use `page-info`, `wait-title`, `wait-role`, and
`click-role`; use `click-label` or `click-text` when visible labels or text are
the stable target. Use `wait-text` when waiting for text to disappear. Use
`press-key` for active/global shortcut keys.

Always close temporary sessions unless the user explicitly asks to keep them
open.
