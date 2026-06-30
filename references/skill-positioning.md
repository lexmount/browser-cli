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
  packaged setup-verification, page-inspection, page-diagnostics, form-fill, and interactive-targeting examples,
  auth status/login/export-env helpers, scoped-token metadata inspection,
  Connect from Codex site requirements, and doctor checks.
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
browser-cli reference get --id skill_positioning
browser-cli reference get --id usable_status
browser-cli auth status
browser-cli doctor --json
browser-cli commands --workflow first_browser_task
browser-cli action guide --task interactive_targeting
```

## Comparison: Browserbase Skills

Comparison source: [Browserbase Skills](https://github.com/browserbase/skills)
and its packaged [browser Skill](https://raw.githubusercontent.com/browserbase/skills/main/skills/browser/SKILL.md).

Browserbase is the closest public cloud-browser Skill reference for this
comparison. Its repository packages multiple agent skills around the `browse`
CLI, including browser automation, platform CLI workflows, functions, tracing,
cookie sync, fetch/search utilities, and browser-specific testing workflows.
The browser Skill gives agents a short default loop: open a page, snapshot page
state, act using element refs, confirm with another snapshot, and stop.

| Area | Browserbase Skill Shape | browser-cli Shape | Current Gap |
| --- | --- | --- | --- |
| Skill distribution | Marketplace/plugin install paths and an official skill collection. | Local CLI plus `SKILL.md`, README prompt, packaged references, and examples. | We do not yet provide a one-step Codex connector, plugin package, or skill marketplace install path. |
| First action path | A compact `browse open`, `browse snapshot`, interact, snapshot, stop loop. | `first_browser_task` gives agents a short doctor, open, inspect, act, verify/capture, close path while deeper workflows remain available. | We still need a single-command or natural-language wrapper for the simplest exploratory tasks. |
| Environment choice | The Skill explains local, remote Browserbase, and CDP-style modes and when to switch. | `browser-cli` is intentionally focused on Lexmount remote browsers plus persistent contexts. | We should state this boundary more clearly and expose platform capability signals when remote behavior is required. |
| Remote platform features | Browserbase advertises Identity, verified browsers, CAPTCHA solving, residential proxies, and session persistence. | Lexmount has remote sessions, contexts, action evidence, structured doctor checks, and case files. | browser.lexmount.cn should surface comparable capability labels, limits, region/proxy choices, and context persistence status if supported. |
| Auth UX | The Skill points users to account settings and uses environment credentials for remote sessions. | Manual env credentials work today; Connect from Codex, scoped tokens, and device-code contracts are documented but not runtime-default. | browser.lexmount.cn still needs project display, scoped key wizard, copyable env/install blocks, revoke/expire UI, and device-code/OAuth. |
| Determinism | Simple element-ref interaction is fast to explain; natural-language layers are concise but less auditable. | Explicit commands and JSON fields make behavior auditable, testable, and repairable. | We should add optional `observe`/`act`/`extract` wrappers that return the underlying deterministic plan and evidence. |
| Ecosystem breadth | The skill collection includes adjacent skills for tracing, cookie sync, fetch/search, testing, and automation improvement. | `browser-cli` packages references, examples, action guides, and case scaffolds in one CLI. | We need more packaged case examples and, later, companion skills for trace analysis, search/fetch, and site-specific workflows. |
| Diagnostics | The Browserbase ecosystem includes tracing and troubleshooting guidance. | `doctor`, repair plans, command catalog checks, page diagnostics, console/network snapshots, events, and artifacts are first-class. | Diagnostics are strong, but the first-step docs must stay short enough that agents actually choose them. |

## Defects To Fix Next

1. browser.lexmount.cn onboarding is still the largest product gap: Connect from
   Codex, Project ID display, scoped key creation, copyable env/install blocks,
   doctor verification, revoke/expire, and device-code/OAuth are not a single
   smooth path yet.
2. `browser-cli` has no one-step plugin/marketplace package, hosted MCP, local
   MCP adapter, or native Codex connector. The Skill works, but users still
   install a CLI and follow a prompt.
3. The `first_browser_task` workflow now gives agents a compact "open, inspect,
   act, verify, close" entrypoint, but it is still a workflow contract rather
   than a single natural-language action tool.
4. There is no simple `observe`/`act`/`extract` layer for tasks where a user
   expects natural language browser control. The deterministic command catalog
   is powerful, but verbose for quick exploratory work.
5. The product does not yet expose a clear remote capability matrix in the
   Skill: identity/session persistence, region/proxy options, anti-bot support,
   quotas, and browser availability should be visible from browser.lexmount.cn
   and `doctor`.
6. The runtime auth story still depends on env API-key credentials for browser
   actions. Device-token metadata, refresh, revoke, and scope checks exist, but
   bearer-token browser runtime support must land across the site, API, SDK, and
   gateway.
7. Skill docs are improving, but agents still need clearer first-step guidance:
   read `skill_positioning` and `usable_status`, run `auth status`, run
   `doctor`, then choose a workflow and action guide before custom code.

## Direction

Keep `browser-cli` as the deterministic, testable core. Add a thinner agent
interface on top later:

- A packaged Codex/plugin install path so users do not copy a long prompt.
- A hosted or local MCP adapter that maps simple tools to existing CLI commands.
- Optional `observe`, `act`, and `extract` wrappers that return the underlying
  command plan and evidence.
- A browser.lexmount.cn Connect from Codex flow that removes manual secret
  handling from normal onboarding.
- A browser.lexmount.cn capability panel that tells agents which remote browser
  features are enabled for the current project.
- More case-file examples for common agent tasks so Codex can run artifacts
  instead of inventing scripts.
