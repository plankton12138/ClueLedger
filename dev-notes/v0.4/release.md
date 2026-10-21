# 0.4 运行与验收说明

核对日期：2026-10-21。本文是 0.4 的运行与验收口径：怎么起、能做什么、哪些已验证、哪些没有。实施过程见[0.4 开发入口](README.md)，模块行为见[模块导航](modules/README.md)。

## 1. 运行

```powershell
uv run python -m hastings.server                 # 默认：打开最近使用的作品目录
uv run python -m hastings.server --workspace 目录  # 启动时打开指定作品
```

| 参数 | 说明 |
|---|---|
| `--workspace <目录>` | 启动时打开这个已有目录；不带时服务照常启动，由界面选择 |
| `--config <文件>` | 模型连接配置，默认 `./config.yaml`；缺失时没有连接，服务照常启动 |
| `--model <名称>` | 临时覆盖当前连接的模型名，不落盘 |
| `--state-dir <目录>` | 应用状态目录（最近目录、会话与事件），缺省 `~/.hastings`，不含正文 |
| `--static-dir <目录>` | 静态前端目录，缺省自动探测 `frontend/dist` |
| `--host` / `--port` | 默认 `127.0.0.1:8000` |

退出码：目录或前端目录不存在等环境故障 → 1；参数写错 → 2。

配置只有两个入口：命令行参数与 `config.yaml`。服务不读 `OPENAI_API_KEY`、`HASTINGS_STATE_DIR` 这一类进程环境变量。

## 2. 模型连接

模型来源只有一处：`config.yaml`（或 `--config` 指定的文件）。一个连接是作者自己的一个账号：

```yaml
providers:
  - name: conn_5a48402f
    display_name: 我的 DeepSeek
    catalog_id: deepseek
    base_url: https://api.deepseek.com/v1
    api: openai-completions
    models: [deepseek-flash, deepseek-v4-pro]
    default_model: deepseek-flash
    credential_ref: hastings:provider:conn_5a48402f:1f3a9c02
active_provider: conn_5a48402f
active_model: deepseek-flash
selection_mode: explicit
```

日常不需要手写这个文件：顶栏「模型设置」可以选择供应商、填 API Key、获取或手填模型、分别测试响应与工具调用，并切换当前连接与模型。保存写入的就是这份文件，重启后保留。字段、接口与失败行为见[模型连接](modules/model-connections.md)。

应用不读进程环境变量（`OPENAI_API_KEY` 之类）：同一份配置换台机器、换个终端结果相同。API Key 写入后由启动流程转入系统凭据存储（Windows 凭据管理器 / macOS Keychain），磁盘上只留 `credential_ref`；系统凭据存储不可用时降级为内存存储，界面提示「仅本次运行有效」，重启后需要重新填写。旧配置里的 `api_key_env` 会被拒绝并提示改成直填 `api_key`。

旧档案（只有 `default_model`、没有 `models`）照常可读；`providers: []` 是合法状态，删掉最后一个连接后服务仍然启动并保持「未选择」。没有可用连接时应用照常启动，发送消息的那一轮返回 `MODEL_NOT_CONFIGURED` 并说明怎么配。

不启动写作台也能核对「模型到底通不通」，它走同一条装配路径：

```powershell
uv run python scripts/check_model.py
```

## 3. 作品目录

```text
<作品目录>/
├─ .git/                 # 版本记录
├─ .gitignore            # 忽略 .obsidian/、.DS_Store、Thumbs.db、.hastings-*.tmp
├─ 正文/                 # 一章一个 Markdown 文件，文件名「四位序号 + 标题」
└─ 设定集/
   ├─ 角色小传.md
   ├─ 线索.md           # 读者视角可观察的信息
   └─ 真相.md           # 实际经过，含手法
```

界面不提供写正文的入口：修改来自对话里的 Agent，或应用之外的编辑器。外部保存后切回窗口或点「刷新」重新扫描。

作品位置不靠手输：点「选择作品文件夹…」由本机服务弹出系统文件夹对话框，选中的目录就是结果。新建作品分两步——选存放位置，再起一个作品名，最终目录是「位置 / 作品名」；存放位置的默认值是系统「文档」目录（Windows 取系统已知文件夹，会跟随 OneDrive 重定向；取不到则退回用户主目录）。对应接口 `GET /api/file-workspaces/default-location` 与 `POST /api/file-workspaces/pick-directory`。

对话框在运行应用的这台机器上弹出：没有桌面会话（远程部署、无界面的服务器）时打不开，接口会明确失败并建议用 `--workspace <目录>` 启动。同一时刻只允许一个对话框，第二个请求以 `DIRECTORY_PICKER_BUSY_CONFLICT` 拒绝。

## 4. 已验收

| 项目 | 证据 |
|---|---|
| 目录规则、读写指纹、搜索、导入 | `uv run pytest tests/workspace -q`（229 项） |
| Git 初始化、基线、差异、提交、撤销、冲突与错误路径 | 同上 |
| 文件工具与轮次记录（含停止、中断、并发拒绝、模型报错） | 同上 |
| 四类协议的连接构造、选择规则、旧配置兼容与空配置 | 同上（`test_model_config.py`） |
| 连接增删改、凭据分步提交、版本冲突、删除当前连接 | 同上（`test_model_service.py`） |
| 三种列表策略、分页上限与失败分类、响应/工具测试 | 同上（`test_model_probe.py`，含本机受控 HTTP 服务） |
| 模型设置接口、脱敏视图、`If-Match`、受理后不受切换影响 | 同上（`test_model_api.py`） |
| 真实模型一句请求 | `uv run python scripts/check_model.py`：`deepseek-flash` 回复正常，`key：…114a` |
| 真实模型连接链路 | 真实发现（2 个模型）→ 响应测试通过 → 工具测试通过 → 新增连接 → 设为当前 → 一轮写作（`正文/0001 雨夜.md` 709 字节，一次 Git 提交）→ 删除临时连接 → 重启后选择保留；记录见[验证材料](artifacts/v04-9-verification.md) |
| 界面主链路 | `cd frontend && npx playwright test`（6 项，真服务 + Chromium；V04-4 建立，V04-9 未重跑） |
| 前端单元 | `cd frontend && npm test`（36 项，含模型设置客户端与状态） |
| 类型与静态检查 | `uv run mypy src/hastings`、`uv run ruff check src/hastings tests/workspace` |

## 5. 未验收与缺口

- **浏览器内未逐屏走查**：模型连接全链路在应用接口层用真实 DeepSeek 取证（见上表），界面由构建、状态与测试覆盖；没有在浏览器里从「打开设置」点到「看见新章节」的截图或录屏证据。
- **真实供应商只覆盖 DeepSeek**：Anthropic、Google 与其余 OpenAI 兼容服务的协议契约由受控传输验证，真实服务未逐家验证。
- **没有 token 级流式**：回复整条显示。
- **窄屏未适配**：固定三列，未迁移 0.3 的断点规则。
- **事件不跨进程广播**：SSE 轮询事件文件，多实例同时写同一作品不在本版范围。
- **撤销只覆盖最新一次提交**：非 HEAD 提交的撤销需在 Git 里手工处理。

## 6. 代码清理范围（V04-5）

0.3 的链路已删除：`domain/`（除仍在用的 `manuscript/diff.py` 与 `repository/serialization.py`）、`application/`（除 `errors.py` 与 `ports/credentials.py`）、`interfaces/`（除 `importers/manuscript_text.py`）、`infrastructure/`（除 `credentials.py`）、`agents/`、`bible/`、`tools/`、`prompts.py`、`session_store.py`、`rag/`、`manuscript/`、`settings.py`、`server/app.py`、`rest.py`、`v2.py`、`ws.py`、`registry.py`、`session.py`、`hastings_session.py`、`v2_agents.py`、`model_access.py`、`ollama.py`，以及对应的 `tests/` 与 `eval/` 驱动器。`hastings.__main__`（CLI 入口）随之退出。

仍在源码里的 0.3 组件：`frontend/src/components/ErrorBoundary.tsx`（新壳层在用）、`src/theme.ts`（Tailwind 取色）、`packaging/`（sidecar 打包规格，入口仍有效）。

0.3 文档保留为历史记录；其中描述的代码已不存在，不要据此推断可用能力。
