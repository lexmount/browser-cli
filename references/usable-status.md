# Browser CLI Usable Status

Use this reference when starting from a fresh install, checking whether the
current mainline CLI is ready for browser work, or explaining which parts still
depend on browser.lexmount.cn.

## Current Baseline

- Mainline install is the supported trial path:
  `uv tool install --force git+https://github.com/lexmount/browser-cli.git`
- The current usable baseline starts at `browser-cli` version `0.2.0`.
- Commands emit JSON for normal output and argument errors.
- The short-term credential path is local shell environment variables:
  `LEXMOUNT_API_KEY` and `LEXMOUNT_PROJECT_ID`.
- China region defaults to `https://api.lexmount.cn`; most users do not need
  `LEXMOUNT_BASE_URL`.

## Readiness Checks

Run these commands before the first browser action in a thread:

```bash
browser-cli version
browser-cli auth status
browser-cli doctor --json
browser-cli doctor --smoke-session
```

Treat the install as ready for browser actions when:

- `doctor --json` returns `ok=true`, `failed=0`, and
  `ready_for_browser_actions=true`.
- `doctor --smoke-session` returns top-level
  `browser_smoke_session.status=pass`.
- `browser_smoke_session.created=true` and
  `browser_smoke_session.closed=true`.

If API connectivity is skipped, do not treat the install as proven ready for
browser sessions. If smoke creation succeeds but close fails, follow
`browser_smoke_session.fix.commands` to close the temporary session manually.

## Usable Now

- Session lifecycle: create, list, get, keepalive, and close.
- Context lifecycle: create, list, get, status, pick, delete, and metadata-based
  reuse with `available`, `locked`, and `unavailable` guidance.
- Browser actions for common agent tasks: navigation, snapshots, screenshots,
  selectors, semantic role/text/label actions, form fill/select/check actions,
  keyboard/mouse actions, storage, cookies, dialogs, frames, console/network
  diagnostics, and case-file execution.
- Agent discovery: `commands --workflow`, `action guide`, packaged references,
  packaged examples, and `case schema`.
- Setup helpers: `auth status`, `auth login`, `auth export-env`,
  `auth scopes`, `auth connect-requirements`, and `doctor`.

## Current Limits

- Users still need to obtain an API key and Project ID from
  `browser.lexmount.cn` and place them in a local shell.
- `auth login --device-code`, scoped token refresh, and revoke expose
  machine-readable contracts, but they are not the default runtime auth path
  until browser.lexmount.cn, the API, SDK, and browser gateway support the full
  bearer-token flow.
- Device-token metadata is safe to inspect, but browser actions still require
  env API-key credentials while `runtime_auth.usable` depends on env auth.
- Never paste API keys, access tokens, refresh tokens, or full direct browser
  connect URLs into chat, issues, commits, screenshots, or PR descriptions.

## browser.lexmount.cn Work Needed

The CLI already exposes the site contract for implementation planning:

```bash
browser-cli auth scopes --include-site-contract
browser-cli auth connect-requirements
browser-cli auth connect-requirements --checklist
browser-cli commands --workflow connect_from_codex_site_requirements
```

The browser site should provide:

- Project ID display and copy controls.
- Scoped agent API-key creation with permission review.
- Copyable install and local env blocks with secret-safety metadata.
- Doctor verification guidance for `browser-cli doctor --json` and
  `browser-cli doctor --smoke-session`.
- Key revoke, expire, rotate, and audit metadata.
- Device-code/OAuth endpoints.
- Runtime bearer-token support across browser.lexmount.cn, Lexmount API, the
  Python SDK, and the browser gateway.

Until those pieces land, use the manual env path in `docs/quickstart.md`.
