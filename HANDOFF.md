# browser-cli handoff

## 背景

`browser-cli` 是从 `website-skills/lex_browser_runtime/cli.py` 中抽出的独立命令行项目，用于在 Codex 或本机 shell 中操作 Lexmount 远程浏览器。

抽取边界：

- 本仓库负责 CLI 命令解析、参数校验、输出格式和 Python package/entrypoint。
- 浏览器 session、context、action、case 的真实运行逻辑继续由 `lex-browser-runtime` 提供。
- 不在本仓库复制 runtime 实现，避免 browser 操作逻辑出现两份真相源。
- research/observer 相关能力没有抽入本仓库。

## 当前代码结构

```text
browser-cli/
├── browser_cli/
│   ├── __init__.py
│   └── cli.py
├── tests/
│   └── test_cli.py
├── README.md
├── HANDOFF.md
└── pyproject.toml
```

关键文件：

- `browser_cli/cli.py`：所有 CLI 命令定义和命令处理入口。
- `tests/test_cli.py`：当前测试主要覆盖 direct-url 和 CLI 基础行为。
- `pyproject.toml`：包名、entrypoint、依赖和 dev 工具配置。
- `README.md`：用户安装、Codex install prompt、开发命令和常用命令。

## 依赖关系

当前依赖：

```toml
lex-browser-runtime[skill] @ git+https://github.com/lexmount/website-skills.git@main
```

原因：`lex-browser-runtime` 还没有稳定发布为可直接依赖的独立版本，所以先依赖 `website-skills@main` 中的 runtime package。

后续如果 `lex-browser-runtime` 发布到包索引，应把依赖从 git main 切到普通版本约束，例如：

```toml
lex-browser-runtime[skill]>=x.y.z
```

## 环境变量

CLI 读取的认证和环境配置与 `lex-browser-runtime` 保持一致：

- `LEXMOUNT_API_KEY`
- `LEXMOUNT_PROJECT_ID`
- `LEXMOUNT_BASE_URL`
- `LEXMOUNT_REGION`

用户侧获取方式：

1. 登录 `https://browser.lexmount.cn`。
2. 在控制台获取 `API Key` 和 `Project ID`。
3. 在本机 shell 设置环境变量，不要把密钥贴到聊天或公开渠道里。

示例：

```bash
export LEXMOUNT_API_KEY="<api-key>"
export LEXMOUNT_PROJECT_ID="<project-id>"
export LEXMOUNT_BASE_URL="https://api.lexmount.cn"
```

## 安装方式

用户安装：

```bash
uv tool install git+https://github.com/lexmount/browser-cli.git
browser-cli --help
```

本地开发：

```bash
uv sync --all-groups
uv run browser-cli --help
uv run pytest tests -q
uv run ruff format --check .
uv run ruff check .
```

项目明确使用 `uv` 管理依赖和执行命令。

## 命令面

主要命令：

```bash
browser-cli session create
browser-cli session list
browser-cli session get --session-id <session_id>
browser-cli session close --session-id <session_id>
browser-cli session keepalive --session-id <session_id>

browser-cli context create
browser-cli context list
browser-cli context get --context-id <context_id>
browser-cli context delete --context-id <context_id>

browser-cli action open-url --session-id <session_id> --url https://example.com
browser-cli action wait-selector --session-id <session_id> --selector <selector>
browser-cli action click --session-id <session_id> --selector <selector>
browser-cli action type --session-id <session_id> --selector <selector> --text <text>
browser-cli action screenshot --session-id <session_id>
browser-cli action eval --session-id <session_id> --script <javascript>
browser-cli action snapshot --session-id <session_id>

browser-cli case validate --file case.yaml
browser-cli case run --file case.yaml
browser-cli direct-url
```

兼容命令：

```bash
browser-cli prepare
browser-cli list-contexts
browser-cli close-session --session-id <session_id>
```

这些兼容命令来自原 CLI 使用习惯，后续可以保留，避免已有 prompt 或脚本断裂。

## 输出约定

CLI 输出 JSON，成功时包含：

```json
{
  "ok": true,
  "command": "session.create"
}
```

失败时包含：

```json
{
  "ok": false,
  "command": "session.create",
  "error": "configuration_error",
  "message": "..."
}
```

错误处理会优先识别 `lex-browser-runtime` 提供的结构化错误信息，然后再 fallback 到异常类名和异常文本。

## direct-url 安全处理

`direct-url` 会生成浏览器直连 URL。由于 URL 里可能包含 `api_key`，CLI 输出时会对 query 中的 `api_key` 做掩码：

```text
api_key=***
```

维护时不要移除这层掩码，避免命令输出泄露密钥。

## README 中的 Codex install prompt

README 已内置一段可复制到 Codex 客户端的安装 prompt。设计目标：

- 引导用户安装或检查 `uv`。
- 使用 `uv tool install git+https://github.com/lexmount/browser-cli.git` 安装 CLI。
- 引导用户登录 `browser.lexmount.cn` 获取 API Key 和 Project ID。
- 明确要求不要把密钥粘贴到聊天里，只在本机 shell 配置环境变量。
- 安装后用 `browser-cli --help` 和 `browser-cli direct-url` 验证。
- 失败时按 `uv`、PATH、环境变量、base URL 逐项排查。

## 开发和发布流程

推荐流程：

1. 基于最新 `main` 拉开发分支。
2. 修改 CLI 或 README。
3. 运行验证：

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest tests -q
uv run browser-cli --help >/tmp/browser-cli-help.txt
```

4. 如果变更命令参数或输出字段，同步更新 README 和本交接文档。
5. 创建 PR，PR 描述中列清楚命令面变化和验证命令。

## 已完成 PR

- PR #1：抽取浏览器操作 CLI 到独立 `browser-cli` 仓库。
- PR #2：补充 README 中的 Codex install prompt。

## 后续建议

- 等 `lex-browser-runtime` 独立发布后，切掉 `website-skills@main` git 依赖。
- 增加 session/context/action 子命令的单元测试，当前测试覆盖仍偏基础。
- 如果后续要做 Codex skill 包装，建议基于 README 的 install prompt 再封装一层 SKILL.md，但不要把用户 API Key 写入 skill 文件。
- 如果 CLI 输出要被上层 agent 程序消费，保持 JSON 输出字段向后兼容。
