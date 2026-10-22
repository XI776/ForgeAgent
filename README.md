# ForgeAgent

ForgeAgent 是一套从零实现的本地 AI Agent Harness。它提供 Electron 桌面工作区或浏览器 Web 工作区、Python Host、JSON-RPC 通信、长期运行管理、审批、自动化、交付物、记忆与扩展能力。

## 功能

- 对话与多步工具执行，支持流式回答、推理片段、工具状态、普通模式和无副作用计划模式
- 每个模型步骤的上下文预算检查、长工具结果压缩、滚动摘要，以及核心记忆/任务/Skill 上下文注入
- Run 生命周期、检查点、中断、恢复、用量统计、证据存档与完整 Trace；可按需查看持久化的模型输入和工具声明
- 敏感工具操作审批，电脑操作提供独立置顶审批窗口
- 一次性、间隔与 Cron 自动化
- 文件与 URL 交付物发布、预览和下载
- 长期记忆、核心记忆、运行后反思、容量维护、跨会话消息搜索，以及带步骤的持续任务
- Skill 安装、启停、删除、扩展预览导入和“重复成功任务 → 待审核 Skill 候选”学习闭环
- MCP Server 配置、启停、环境变量白名单、工作区约束与 Linux 沙箱
- OpenAI、DeepSeek、Qwen 和 Anthropic 模型配置

Host 暴露 `GET /health`、`WS /rpc`、Web UI 和 artifact 下载接口。业务操作均通过 JSON-RPC 完成。

## 环境要求

- Linux
- Python 3.11+
- Node.js 20+ 与 npm
- 可选：`bubblewrap`（Linux 工作区隔离；Docker 镜像内已安装）
- 可选：Docker 24+ 与 Docker Compose v2（容器部署）

## Linux 本地启动

```bash
./scripts/start.sh            # 启动 Host + Electron 桌面端
./scripts/start.sh web        # 构建 Web UI 并由 Host 托管，浏览器访问 http://127.0.0.1:8765
./scripts/start.sh serve      # 只启动 Host（无 UI）
```

脚本会自动创建 `backend/.venv`、安装依赖、在首次运行时从 `.env.example` 复制 `backend/.env`，并在缺少 `desktop/node_modules` 时执行 `npm ci`。

也可以手动启动：

```bash
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m forge_agent.server

# 另一个终端
cd desktop
npm ci
npm run electron:dev
```

首次运行在 `backend/.forgeagent` 创建数据库、设置、记忆、技能和交付物目录。在“设置”页面即可配置 Qwen/OpenAI/DeepSeek/Anthropic，也可以填写 `backend/.env`：

```dotenv
FORGEAGENT_PROVIDER=qwen
FORGEAGENT_MODEL=qwen-plus
FORGEAGENT_API_KEY=sk-你的密钥
FORGEAGENT_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

## Docker 部署（单容器 Web）

```bash
cp backend/.env.example backend/.env    # 首次运行；填入模型 API Key
docker compose up -d --build
```

打开 <http://127.0.0.1:8765> 使用 Web 工作区。常用命令：

```bash
docker compose logs -f
docker compose down
```

镜像同时包含 Python Host、构建后的 React Web UI、Node.js 22（供 `npx` 类 MCP Server 使用）、`git` 与 `bubblewrap`。运行数据保存在 `forgeagent-data` 命名卷的 `/data`（数据库、记忆、Skill、artifact、密钥），工作文件通过 `./workspace` 映射到容器的 `/workspace`。

- 容器以 uid 1000 运行。若宿主机用户不是 1000，请先 `sudo chown -R 1000:1000 workspace`，或调整镜像/Compose 的 `user` 与目录属主。
- 端口默认只绑定 `127.0.0.1`。**不要**直接改成 `0.0.0.0` 暴露公网：`/rpc` 没有内置认证，请在前面加带认证的反向代理。
- Host 使用内存态广播与审批等待器，只支持单副本运行，不要横向扩容。

## 沙箱与安全

Linux 上的进程隔离按以下顺序选择（`FORGEAGENT_SANDBOX_BACKEND`）：

| 值 | 行为 |
| --- | --- |
| `auto`（默认） | 优先使用 bubblewrap；不可用时，如果运行在容器内则以容器为边界；否则拒绝启动需要隔离的进程 |
| `bubblewrap` | 强制使用 bubblewrap；不可用时拒绝启动 |
| `container` | 只使用 Docker 容器边界；仅支持 `filesystem=workspace` + `network=allowed` |

bubblewrap 模式使用最小只读根（`/usr`、`/etc`、`/opt` 与基础链接），把工作区和独立的 sandbox HOME 挂载进沙箱；`filesystem=readonly` 只读挂载工作区；`network=denied` 使用独立 network namespace。宿主数据目录、项目根、`/home`、`/root` 不在沙箱根中，避免模型通过 Shell 读取 `secrets.json` 或项目配置。无法满足策略时进程不会启动（fail closed）。因此 bwrap 模式下 MCP Server 的可执行文件应位于 `/usr`、`/usr/local`、`/opt` 或工作区内；安装在 `~/.nvm`、`~/.local` 等家目录下的工具需要改用系统路径，或将对应 MCP 明确设为 `filesystem=host`（需人工审批）。

Docker Compose 默认通过 `security_opt`（`seccomp=unconfined`、`apparmor=unconfined`、`systempaths=unconfined`）允许容器内的非特权 bwrap 创建 user/mount/pid/network namespace，因此 `auto` 会使用 `linux-bubblewrap`：Shell 与 MCP 默认具备工作区文件系统隔离和 `network=denied`，无需再手动调整 MCP 策略。容器仍以 uid 1000 运行并丢弃全部 capabilities，bwrap 是每条命令的实际隔离边界；上述选项只放宽容器内创建 namespace 的限制。

如果宿主机内核禁止非特权 user namespace，bwrap 探测失败时会回退为容器边界：文件系统由容器挂载约束、网络由容器网络约束，此时 MCP 的 `network=denied` 无法启动，需要显式设为 `allowed`。可用 `FORGEAGENT_SANDBOX_BACKEND=container` 强制该模式。

### 网络与客户端

- 默认只监听 `127.0.0.1`。
- artifact 与 `/rpc` WebSocket 只接受 `FORGEAGENT_TRUSTED_CLIENTS` 中的客户端（IP/CIDR/主机名，默认 loopback）。Docker 映射端口时源地址是 Docker 网关，因此 Compose 显式加入私有网段。
- WebSocket 在握手前校验 `Origin`：允许无 Origin、`null`（Electron `file://`）、同源页面、loopback/Vite 开发页面，以及 `FORGEAGENT_ALLOWED_ORIGINS` 中显式列出的来源，防止跨站 WebSocket 劫持。

### 密钥

- 密钥保存在 `<data_dir>/settings/secrets.json`（目录 `0700`、文件 `0600`），持久化审批规则签名密钥保存为同目录的 `permission-signing-key`。
- 容器部署优先通过环境变量（`backend/.env`）注入密钥。
- 数据卷丢失会导致已保存的密钥与持久化审批规则失效。

搜索默认使用 DuckDuckGo；设置 `FORGEAGENT_TAVILY_API_KEY` 后，`FORGEAGENT_SEARCH_PROVIDER=auto` 优先使用 Tavily，遇到可恢复错误时回退。可显式选择 `tavily` 或 `duckduckgo`。HTTP 工具默认拒绝私网地址及重定向到私网的目标；只有明确设置 `FORGEAGENT_HTTP_ALLOW_PRIVATE_NETWORK=true` 才放行。用户级 Skill 默认保存在 `~/.forgeagent/skills`，可通过 `FORGEAGENT_USER_SKILLS_DIR` 指定。

## 配置项

所有配置使用 `FORGEAGENT_` 前缀，也可以继续读取旧的 `KORENEXT_` 前缀（同名项以新前缀优先）。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `FORGEAGENT_HOST` / `FORGEAGENT_PORT` | `127.0.0.1` / `8765` | Host 监听地址。Docker 内为 `0.0.0.0` |
| `FORGEAGENT_DATA_DIR` | `backend/.forgeagent` | 数据库、记忆、artifact、密钥 |
| `FORGEAGENT_WORKSPACE_DIR` | `backend/workspace` | Agent 工作区 |
| `FORGEAGENT_SANDBOX_HOME` | `<data_dir>/sandbox-home` | 沙箱子进程的 HOME |
| `FORGEAGENT_USER_SKILLS_DIR` | `~/.forgeagent/skills` | 用户级 Skill |
| `FORGEAGENT_WEB_DIR` | `../desktop/dist`（存在时） | 由 Host 托管的 Web UI |
| `FORGEAGENT_SANDBOX_BACKEND` | `auto` | `auto` / `bubblewrap` / `container` |
| `FORGEAGENT_TRUSTED_CLIENTS` | `127.0.0.0/8,::1/128,localhost,testclient` | 允许访问 artifact 与 RPC 的客户端 |
| `FORGEAGENT_ALLOWED_ORIGINS` | 空 | 额外允许的 WebSocket Origin |
| `FORGEAGENT_HTTP_ALLOW_PRIVATE_NETWORK` | `false` | 是否允许 HTTP 工具访问私网 |

## 验证

```bash
cd backend
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/python -m compileall -q forge_agent

cd ../desktop
npm test
npm run typecheck
npm run build
```

Docker 冒烟测试：

```bash
docker build -t forgeagent:test .
docker run --rm -p 127.0.0.1:18765:8765 forgeagent:test &
curl http://127.0.0.1:18765/health
curl http://127.0.0.1:18765/ | grep 'id="root"'
```

## 数据隔离与迁移

默认运行数据位于 `backend/.forgeagent`，工作文件位于 `backend/workspace`。可通过 `FORGEAGENT_DATA_DIR` 和 `FORGEAGENT_WORKSPACE_DIR` 覆盖。数据库升级前自动生成 `backup-v*` 快照并记录版本；旧的字段匹配授权规则保留但停用，需要重新审批。

升级已有安装时，首次启动会将旧数据从 `backend/.koreagent-next` 复制到 `backend/.forgeagent`，原目录保留作为备份。现有 `.env` 中的 `KORENEXT_` 配置仍可读取；新配置请使用 `FORGEAGENT_`，同名项以新前缀优先。系统密钥和会话固定状态也会继续读取旧值。

模型密钥不会进入数据库导出或日志。桌面端保存密钥时使用独立的 ForgeAgent 密钥命名空间；`.env`、数据库、虚拟环境、依赖与构建目录均被 Git 忽略。
