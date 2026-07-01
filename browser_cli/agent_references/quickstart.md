# Browser CLI Quickstart

This guide is the shortest path to a usable `browser-cli` install for Codex or
another agent. The current MVP uses `LEXMOUNT_API_KEY` and
`LEXMOUNT_PROJECT_ID` from your local shell. Do not paste real secrets into
chat, issues, commits, or PR descriptions.

## Install

For mainline/default-branch usage, install from GitHub:

```bash
uv tool install --force git+https://github.com/lexmount/browser-cli.git
```

Verify that command discovery returns JSON:

```bash
browser-cli --version
browser-cli version
browser-cli commands --names-only
browser-cli commands --workflows-only
browser-cli reference get --id quickstart --metadata-only
browser-cli skill status
```

After installation, agents can read this same minimum path from the packaged
reference:

```bash
browser-cli reference get --id quickstart
```

If `browser-cli skill status` reports `status` other than `current`, review
`stale_files` and `missing_files`, then refresh the local Codex Skill with:

```bash
browser-cli skill install --force
```

## Configure Credentials

Open [browser.lexmount.cn](https://browser.lexmount.cn), choose the project you
want Codex to control, and copy an API key plus Project ID. Put the real values
only in your local terminal:

```bash
export LEXMOUNT_API_KEY="<api-key-from-browser.lexmount.cn>"
export LEXMOUNT_PROJECT_ID="<project-id-from-browser.lexmount.cn>"
```

China users normally do not need `LEXMOUNT_BASE_URL`; the default API endpoint
is `https://api.lexmount.cn`.

To generate a safe local-shell template:

```bash
browser-cli auth export-env
```

To inspect whether credentials are configured without revealing secret values:

```bash
browser-cli auth status
```

## Verify Readiness

Run doctor before browser work:

```bash
browser-cli doctor --json
```

Ready for normal browser actions means:

- `ok=true`
- `failed=0`
- `ready_for_browser_actions=true`

Optionally run a live smoke session:

```bash
browser-cli doctor --smoke-session
```

Smoke success means `browser_smoke_session.status=pass`, with the temporary
session created and closed.

## First Browser Task

Inspect the shortest safe workflow before acting:

```bash
browser-cli commands --workflow first_browser_task
```

Create a session:

```bash
browser-cli session create
```

Save `session.session_id` or top-level `session_id` from the JSON output, then
use it in action commands:

```bash
browser-cli action open-url --session-id <session_id> --url https://example.com
browser-cli action page-info --session-id <session_id>
browser-cli action extract --session-id <session_id> --surface text --surface links --selector main
browser-cli action snapshot --session-id <session_id> --max-chars 4000
browser-cli action screenshot --session-id <session_id> --output /tmp/browser-cli-page.png
```

Close the session when done:

```bash
browser-cli session close --session-id <session_id>
```

## Persistent Login State

For sites that need login cookies or local storage, use a persistent context.
Start by inspecting reusable contexts without mutating anything:

```bash
browser-cli context list --metadata-json '{"purpose":"codex-login"}' --selection newest --include-reuse-state
```

Read these fields:

- `reuse_candidates`
- `recommended_context_id`
- `selection_summary.recommended_next_action`
- `selection_summary.reusable_matches`
- `selection_summary.locked_matches`
- `metadata_values_redacted`

Dry-run selection before creating a session:

```bash
browser-cli context pick --metadata-json '{"purpose":"codex-login"}' --selection newest --create-if-missing --dry-run
```

Create or reuse a context-backed session:

```bash
browser-cli session create --context-metadata-json '{"purpose":"codex-login"}' --context-selection newest --create-context-if-missing --context-mode read_write
```

If a context is busy, treat `availability=locked` or `locked=true` as a signal
to wait, choose another context, or create a new one.

## Agent Discovery And Command Selection

Agents should inspect machine-readable workflows before choosing actions:

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

Use the first-class observe, act, and extract primitives before choosing targets or custom JavaScript:

```bash
browser-cli action observe --session-id <session_id> --surface interactive --surface text
browser-cli action act --session-id <session_id> --kind click --role button --name "Submit"
browser-cli action act --session-id <session_id> --kind fill --label "Email" --value "me@example.com"
browser-cli action extract --session-id <session_id> --surface text --surface links --selector main
```

Before writing custom JavaScript, inspect the action guide:

```bash
browser-cli action guide --names-only
browser-cli action guide --task interactive_targeting
browser-cli action guide --task form_interaction
browser-cli action guide --task content_extraction
browser-cli action guide --task state_waits
```

Useful first-class actions include:

```bash
browser-cli action wait-role --session-id <session_id> --role button --name "Submit"
browser-cli action act --session-id <session_id> --kind click --role button --name "Submit"
browser-cli action act --session-id <session_id> --kind fill --label "Email" --value "me@example.com"
browser-cli action click-role --session-id <session_id> --role button --name "Submit"
browser-cli action click-text --session-id <session_id> --text "Submit"
browser-cli action fill-label --session-id <session_id> --label "Email" --text "me@example.com"
browser-cli action select-role --session-id <session_id> --role combobox --name "Plan" --option-label "Pro"
browser-cli action check-role --session-id <session_id> --role checkbox --name "Remember me"
browser-cli action get-text-role --session-id <session_id> --role alert --name "Saved"
browser-cli action exists-role --session-id <session_id> --role button --name "Submit"
browser-cli action drag-role-to-role --session-id <session_id> --source-role listitem --source-name "Todo" --target-role list --target-name "Done"
browser-cli action interactive-snapshot --session-id <session_id> --max-nodes 80
browser-cli action accessibility-snapshot --session-id <session_id> --max-nodes 120
```

For repeatable tasks, inspect and run case files:

```bash
browser-cli case schema
browser-cli case scaffold --template page-inspection --url https://example.com --output case.yaml
browser-cli case scaffold --template agent-primitives --output agent-primitives-case.yaml
browser-cli case scaffold --template content-extraction --output content-extraction-case.yaml
browser-cli case scaffold --template browser-state --output browser-state-case.yaml
browser-cli case scaffold --template navigation-flow --output navigation-case.yaml
browser-cli case scaffold --template interactive-targeting --output interactive-case.yaml
browser-cli case validate --file case.yaml
browser-cli case run --file case.yaml --close-created-session
```

## Troubleshooting

If a command fails, parse the JSON fields instead of scraping text:

- `ok`
- `error`
- `message`
- `fix.code`
- `fix.commands`
- `next_steps`

Common checks:

```bash
uv tool list
browser-cli auth status
browser-cli doctor --json
browser-cli session list --status active
browser-cli commands --group action --names-only
```

If credentials are missing or wrong:

```bash
browser-cli reference get --id usable_status --metadata-only
browser-cli reference get --id usable_status
browser-cli auth login
browser-cli auth export-env
browser-cli auth status
browser-cli doctor --json
```

If a live smoke created but did not close a session, inspect and close it:

```bash
browser-cli session list --status active
browser-cli session close --session-id <session_id>
```

## Current Limits

- The MVP credential path is manual local-shell env setup with API key and
  Project ID.
- `auth login --device-code`, scoped tokens, refresh, and revoke already expose
  machine-readable contracts, but they should become the default only after
  browser.lexmount.cn, the API, SDK, and browser gateway support the full
  runtime auth flow.
- Never paste revealed API keys, access tokens, refresh tokens, or direct
  connect URLs containing raw `api_key` into chat.
