# Persistent Context Playbook

Use this example when Codex or another agent needs to reuse login state,
cookies, or storage across browser sessions without guessing whether a context
is safe to mutate.

## 1. Inspect The Workflow

Start with the installed workflow and packaged guidance:

```bash
browser-cli commands --workflow persistent_login_state
browser-cli example get --id persistent_context_playbook --metadata-only
browser-cli auth status
browser-cli doctor --json
```

Proceed only when `doctor.ok=true`, `doctor.failed=0`, and
`runtime_auth.usable=true`. If `api_connectivity.status=skipped`, do not treat
live browser work as verified.

## 2. Dry-Run Context Selection

Before creating or mutating a session, ask browser-cli to explain which context
would be reused:

```bash
browser-cli context pick --metadata-json '{"purpose":"codex-login"}' --selection newest --create-if-missing --dry-run
```

Read these fields before acting:

- `availability`
- `reusable`
- `locked`
- `reuse_reason`
- `selection_strategy`
- `selection_summary.recommended_next_action`
- `selection_summary.decision_reason`
- `selection_summary.locked_matches`
- `selection_summary.reusable_matches`
- `selection_summary.metadata_mismatches`
- `selection_summary.availability_counts`
- `selection_summary.would_create`

Use `recommended_next_action` as the decision point. Reuse only when
`availability=available` and `reusable=true`. If `availability=locked`, wait,
pick another metadata filter, or ask the user before creating a competing
read-write session. If `availability=unavailable`, create a new context only
when the task still needs persistent login state.

## 3. List Candidate Contexts

When the dry-run decision is unclear, inspect the candidate pool:

```bash
browser-cli context list --metadata-json '{"purpose":"codex-login"}' --selection newest --include-reuse-state
```

Read:

- `reuse_state_included`
- `recommended_context_id`
- `reuse_candidates`
- `selection_summary.recommended_next_action`
- `selection_summary.locked_matches`
- `selection_summary.reusable_matches`
- `selection_summary.metadata_mismatches`
- `metadata_values_redacted`

Metadata values are redacted by design. Store only labels such as `purpose`,
`account_alias`, or `site`. Never store API keys, passwords, session secrets,
or one-time codes in context metadata.

## 4. Inspect A Known Context

If a context id comes from a previous run, user notes, or local registry state,
inspect it before reuse:

```bash
browser-cli context status --context-id <context_id>
```

Read:

- `status`
- `normalized_status`
- `availability`
- `reusable`
- `locked`
- `reuse_reason`
- `reuse`
- `context`

Prefer `availability` over raw status strings. Treat `locked=true` as busy, not
as a failure to override.

## 5. Create A Session With The Chosen Context

For login or setup work that should update cookies/storage, use `read_write`:

```bash
browser-cli session create --context-metadata-json '{"purpose":"codex-login"}' --context-selection newest --create-context-if-missing --context-mode read_write
```

For inspection that should not mutate login state, use `read_only` with an
existing context id:

```bash
browser-cli session create --context-id <context_id> --context-mode read_only
```

Read `context_reuse` from the session result:

- `context_reuse.selected`
- `context_reuse.created`
- `context_reuse.selection_strategy`
- `context_reuse.availability`
- `context_reuse.reusable`
- `context_reuse.locked`
- `context_reuse.reuse_reason`
- `context_reuse.selection_summary.recommended_next_action`

If `context_reuse.selected=false`, do not assume login state is present.
Capture a fresh page snapshot or ask the user to log in through the selected
context.

## 6. Verify And Clean Up

Use normal action workflows after the session is created:

```bash
browser-cli action page-info --session-id <session_id>
browser-cli action snapshot --session-id <session_id> --max-chars 4000
browser-cli action screenshot --session-id <session_id> --output context-check.png
```

Close temporary sessions even when keeping the persistent context:

```bash
browser-cli session close --session-id <session_id>
```

Delete a persistent context only when the user confirms the login state is no
longer needed:

```bash
browser-cli context delete --context-id <context_id>
```
