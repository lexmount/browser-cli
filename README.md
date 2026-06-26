# browser-cli

`browser-cli` is a command-line interface for operating Lexmount remote browser
sessions from local shells, Codex, and other agents.

It owns command parsing, JSON output, installation docs, and agent-facing
ergonomics. Browser lifecycle and page action behavior stay in
`lex-browser-runtime`, so this project does not maintain a second copy of the
runtime implementation.

## Codex Install Prompt

Copy this prompt into Codex when you want Codex to install and configure the
CLI for you:

```text
请帮我安装并配置 Lexmount browser-cli，用于在 Codex 中操作 Lexmount 远程浏览器。

约束：
1. 不要让我把 API Key 或 Project ID 粘贴到聊天里。
2. 只指导我在本机 shell 中设置环境变量，或写入本机 shell 配置文件。
3. 不要把 API Key 输出到日志、README、提交记录或聊天回复里。

步骤：
1. 检查本机是否已经安装 uv：
   uv --version
2. 如果没有 uv，提示我先安装 uv：
   curl -LsSf https://astral.sh/uv/install.sh | sh
3. 安装 browser-cli：
   uv tool install git+https://github.com/lexmount/browser-cli.git
4. 运行：
   browser-cli auth status
5. 如果 auth status 显示缺少凭据，运行：
   browser-cli auth login
   或者在本机浏览器中打开授权页：
   browser-cli auth login --open
6. 如果我在联调未来的 Connect from Codex/device-code 流程，运行：
   browser-cli auth device-code
   如果输出 available=false，说明当前还需要 manual login/setup。
7. 如果我在实现 browser.lexmount.cn 的 Connect from Codex 页面，运行：
   browser-cli auth connect-spec
   并按输出的 page_sections、copy_blocks、device_code_contract 实现页面。
8. 引导我打开 https://browser.lexmount.cn 并登录账号。
9. 引导我在 browser.lexmount.cn 控制台中找到当前 Project ID，并创建或复制 API Key。
10. 引导我在本机 shell 中设置：
   export LEXMOUNT_API_KEY="<从 browser.lexmount.cn 获取的 API Key>"
   export LEXMOUNT_PROJECT_ID="<从 browser.lexmount.cn 获取的 Project ID>"
11. 可以运行下面命令生成本机 shell 配置片段，但不要把 revealed API Key 粘贴到聊天里：
   browser-cli auth export-env
12. 告诉我中国区默认会使用 https://api.lexmount.cn，通常不需要设置 LEXMOUNT_BASE_URL。
13. 如果我希望长期保存配置，引导我把这些 export 写入当前 shell 配置文件，例如 ~/.zshrc 或 ~/.bashrc。
14. 运行下面命令验证：
   browser-cli --help
   browser-cli auth status
   browser-cli direct-url
   browser-cli session list
15. 如果验证失败，请按顺序排查：
   - uv 是否可用
   - browser-cli 是否在 PATH 中
   - LEXMOUNT_API_KEY 是否已设置
   - LEXMOUNT_PROJECT_ID 是否已设置
   - 如果设置了 LEXMOUNT_BASE_URL，它是否为正确的 API endpoint

完成后告诉我：
- browser-cli 的安装路径
- 验证命令是否通过
- 我还需要手动做什么
```

## Manual Install

```bash
uv tool install git+https://github.com/lexmount/browser-cli.git
browser-cli --help
```

For local development:

```bash
uv sync --all-groups
uv run browser-cli --help
uv run pytest tests -q
uv run ruff format --check .
uv run ruff check .
```

## Credentials

`browser-cli` reads the same environment variables as `lex-browser-runtime`:

```bash
export LEXMOUNT_API_KEY="<api-key>"
export LEXMOUNT_PROJECT_ID="<project-id>"
```

Optional:

```bash
export LEXMOUNT_BASE_URL="https://api.lexmount.cn"
export LEXMOUNT_REGION="<region>"
```

Treat API keys as secrets. The CLI masks `api_key` in generated direct browser
URLs unless you pass an explicit reveal flag.

Auth helpers:

```bash
browser-cli auth status
browser-cli auth login
browser-cli auth login --open
browser-cli auth device-code
browser-cli auth connect-spec
browser-cli auth export-env
browser-cli auth export-env --reveal-secrets
browser-cli auth export-env --shell fish
browser-cli auth export-env --shell powershell
```

`auth status` reports whether the required env vars are configured without
printing API keys by default. `auth login` gives browser.lexmount.cn onboarding
steps, and `auth login --open` opens the Connect from Codex page in the local
browser. `auth device-code` returns a machine-readable future device-code/OAuth
contract. Until browser.lexmount.cn implements the required endpoints, it reports
`available: false` and agents should fall back to `auth login`.
`auth connect-spec` returns the machine-readable Connect from Codex page
requirements for browser.lexmount.cn, including Project ID display, scoped API
key creation, copyable install/env/verify blocks, doctor verification,
revoke/expiry controls, and the device-code endpoint contract. `auth export-env`
returns JSON containing shell lines; they are masked by default, and become
directly usable only with `--reveal-secrets` in a trusted local shell.

## Commands

Auth and credential guidance:

```bash
browser-cli auth status
browser-cli auth login
browser-cli auth login --open
browser-cli auth device-code
browser-cli auth connect-spec
browser-cli auth export-env
browser-cli auth export-env --include-base-url --reveal-secrets
```

Session management:

```bash
browser-cli session create
browser-cli session create --create-context
browser-cli session create --context-id <context_id> --context-mode read_write
browser-cli session list --status active
browser-cli session get --session-id <session_id>
browser-cli session close --session-id <session_id>
browser-cli session keepalive --session-id <session_id> --duration 60
```

Context management:

```bash
browser-cli context create
browser-cli context create --metadata-json '{"purpose":"codex"}'
browser-cli context list --limit 20
browser-cli context get --context-id <context_id>
browser-cli context delete --context-id <context_id>
```

Browser actions:

```bash
browser-cli action open-url --session-id <session_id> --url https://example.com
browser-cli action wait-selector --session-id <session_id> --selector "main"
browser-cli action click --session-id <session_id> --selector "button[type=submit]"
browser-cli action type --session-id <session_id> --selector "input[name=q]" --text "hello"
browser-cli action screenshot --session-id <session_id> --output /tmp/page.png
browser-cli action eval --session-id <session_id> --script "() => document.title"
browser-cli action snapshot --session-id <session_id> --max-chars 8000
```

Each action must receive exactly one browser target:

```bash
--session-id <session_id>
--connect-url <cdp_websocket_url>
--direct-url
```

By default, action output masks `api_key` inside resolved direct connect URLs.
Use `--reveal-connect-url` only for local debugging.

Case files and compatibility aliases:

```bash
browser-cli case validate --file case.yaml
browser-cli case run --file case.yaml
browser-cli direct-url
browser-cli prepare
browser-cli list-contexts
browser-cli close-session --session-id <session_id>
```

## JSON Output

All command output is JSON.

Successful commands include:

```json
{
  "ok": true,
  "command": "session.create"
}
```

Failed commands include:

```json
{
  "ok": false,
  "command": "session.create",
  "error": "configuration_error",
  "message": "..."
}
```

Agents should parse `ok`, `command`, and `error` first, then use
command-specific fields.

## Suggested Agent Workflow

For a new browser task, agents should prefer this sequence:

```bash
browser-cli auth status
browser-cli session create
browser-cli action open-url --session-id <session_id> --url <url>
browser-cli action snapshot --session-id <session_id>
browser-cli action click --session-id <session_id> --selector <selector>
browser-cli action type --session-id <session_id> --selector <selector> --text <text>
browser-cli action screenshot --session-id <session_id> --output /tmp/final.png
browser-cli session close --session-id <session_id>
```

Use `context create` plus `session create --context-id <context_id>` when login
state or cookies should survive between sessions.

## Codex Skill

This repository includes a starter [`SKILL.md`](./SKILL.md) so the project can
evolve into a Codex skill. The skill stays a thin wrapper around this CLI:

- `SKILL.md` should teach agents when to use browser sessions, contexts, and
  actions.
- The skill should install or verify `browser-cli`.
- The skill should never store API keys in the skill directory.
- The skill should keep using JSON command output instead of importing Python
  internals directly.

## Suggestions For browser.lexmount.cn

The smoothest onboarding path would be a dedicated "Connect from Codex" flow:

1. Add a console page that shows the current `Project ID`, API key status, and
   the exact `export ...` commands for the selected project.
2. Add a scoped API key wizard for agent use, with clear permissions, optional
   expiration, and one-click revoke.
3. Provide a copyable install block:
   `uv tool install git+https://github.com/lexmount/browser-cli.git`.
4. Add a "Verify CLI" section that tells users to run
   `browser-cli session list` after setting env vars.
5. Longer term, support device-code or OAuth-style authorization so Codex can
   ask the user to approve access in the browser and then receive a local,
   short-lived token without the user manually copying API keys.
6. Once that flow exists, wire `browser-cli auth login` to start the
   device-code/OAuth authorization instead of only returning manual guidance.
7. Implement the `browser-cli auth device-code` contract: issue
   `device_code`/`user_code`, return verification URLs, support token polling
   with pending/slow-down/expired/denied states, return scoped expiring
   credentials for the selected Project ID, and expose revoke/expiration
   controls.
8. Use `browser-cli auth connect-spec` as the implementation checklist for the
   Connect from Codex page. It returns required page sections, copy blocks,
   verification commands, scopes, security requirements, and device-code
   endpoint fields as JSON.
