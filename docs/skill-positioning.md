# browser-cli Skill Positioning

This document explains when agents should use `browser-cli`, what the current
Skill supports, and where it still trails a polished cloud-browser agent
experience.

## Primary Use Case

Use `browser-cli` when an agent needs a Lexmount remote browser and should work
through deterministic JSON commands instead of writing browser automation code.
The Skill is strongest when the task needs reliable setup checks, explicit
session cleanup, safe secret handling, persistent login reuse, or repeatable
case files.

Use a local browser or computer-control tool instead when the task is about a
local app, a local browser tab, or a website that is already open in a local
Codex browser surface.

## Supported Today

- Setup and readiness: version JSON, command discovery, packaged references,
  packaged examples, auth status/login/export-env helpers, scoped-token
  metadata inspection, Connect from Codex site requirements, and doctor checks.
- Session lifecycle: create, list, inspect, keep alive, close, recover from
  stale sessions, and run smoke-session validation.
- Persistent contexts: create, list, inspect, pick, delete, reuse by metadata,
  and reason about `available`, `locked`, and `unavailable` candidates.
- Navigation and waits: open URL, reload, go back/forward, wait for URL, title,
  load state, network idle, selectors, roles, text, attributes, values, frames,
  dialogs, storage, cookies, console entries, and network requests.
- Inspection: page info, HTML/text snapshots, screenshots, outline,
  accessibility, interactive-only, form, link, table, list, dialog, frame,
  performance, network, and console snapshots.
- Interaction: click, click-label, click-text, click-role, click-index,
  double-click, right-click, hover, press, focus, blur, scroll, bounding box,
  drag, type, fill, clear, set value, file input, dispatch event, submit,
  select, check, and uncheck.
- Browser state: local/session storage and cookie get/set/remove/clear flows.
- Repeatable tasks: JSON/YAML case schema, scaffold, validate, run, artifacts,
  events, expectations, and automatic cleanup for created sessions.

## When Agents Should Start Here

Start with `browser-cli` when:

- The user asks Codex to browse, test, inspect, log in, fill a form, capture
  evidence, or automate a site through Lexmount.
- Credentials or readiness are uncertain and `doctor` can produce structured
  repair guidance.
- The task should be reproducible as a case file or evidence bundle.
- A persistent login context should be reused safely rather than recreated.
- The agent needs to avoid leaking API keys, tokens, or direct browser URLs.

Start with discovery before action:

```bash
browser-cli reference get --id usable_status
browser-cli auth status
browser-cli doctor --json
browser-cli commands --workflow one_off_page_task
browser-cli action guide --task interactive_targeting
```

## Comparison: Browserbase MCP Server

Comparison source: [Browserbase MCP Server](https://github.com/browserbase/mcp-server-browserbase)
and its linked [Browserbase MCP documentation](https://docs.browserbase.com/integrations/mcp/introduction).

Browserbase MCP is a useful reference point because it presents a cloud-browser
agent interface as a small MCP tool set. Its public README describes a hosted
MCP server plus a self-hostable version, and exposes six tools:
`start`, `end`, `navigate`, `act`, `observe`, and `extract`.

| Area | Browserbase MCP Shape | browser-cli Shape | Current Gap |
| --- | --- | --- | --- |
| Agent integration | Hosted MCP endpoint and local MCP package. | Local CLI plus Codex Skill instructions. | We do not yet provide a hosted MCP or one-step Codex connector. |
| Tool surface | Small natural-language tool set: start/end/navigate/act/observe/extract. | Many deterministic commands, workflows, guides, and case-file actions. | We need an optional natural-language layer for simple tasks without losing deterministic commands. |
| Setup | MCP client configuration with hosted or local transports. | `uv tool install`, local env credentials, `auth login`, and `doctor`. | Setup is more verbose until browser.lexmount.cn provides Connect from Codex and device-code authorization. |
| Auth UX | Project/API key env vars for self-hosting; hosted flow is externally managed. | Manual env path today, with scoped-token/device-code contracts exposed but not runtime-default. | browser.lexmount.cn still needs project display, scoped key wizard, copyable env/install blocks, revoke/expire UI, and device-code/OAuth. |
| Determinism | Natural-language `act` and `extract` can be concise but model-dependent. | Explicit commands and JSON fields make behavior auditable and testable. | We should keep deterministic commands as the core and add higher-level wrappers later. |
| Reproducibility | MCP tools are interactive; repeatable flows depend on the client. | Case schema, scaffold, validate, run, artifacts, and events are first-class. | We should surface case-file workflows more prominently in the Skill prompt. |
| Diagnostics | Cloud service and MCP errors are surfaced through tool results. | `doctor`, repair plans, command catalog checks, page diagnostics, console/network snapshots. | Diagnostics are strong, but the docs need shorter decision paths for agents. |

## Defects To Fix Next

1. browser.lexmount.cn onboarding is still the largest product gap: Connect from
   Codex, Project ID display, scoped key creation, copyable env/install blocks,
   doctor verification, revoke/expire, and device-code/OAuth are not a single
   smooth path yet.
2. `browser-cli` has no hosted MCP or native Codex connector. The Skill works,
   but users still install a CLI and follow a prompt.
3. There is no simple `act`/`observe`/`extract` layer for tasks where a user
   expects natural language browser control. The deterministic command catalog
   is powerful, but verbose.
4. The runtime auth story still depends on env API-key credentials for browser
   actions. Device-token metadata, refresh, revoke, and scope checks exist, but
   bearer-token browser runtime support must land across the site, API, SDK, and
   gateway.
5. Skill docs are improving, but agents still need clearer first-step guidance:
   read `usable_status`, run `auth status`, run `doctor`, then choose a workflow
   and action guide before custom code.

## Direction

Keep `browser-cli` as the deterministic, testable core. Add a thinner agent
interface on top later:

- A hosted or local MCP adapter that maps simple tools to existing CLI commands.
- Optional `observe`, `act`, and `extract` wrappers that return the underlying
  command plan and evidence.
- A browser.lexmount.cn Connect from Codex flow that removes manual secret
  handling from normal onboarding.
- More case-file examples for common agent tasks so Codex can run artifacts
  instead of inventing scripts.
