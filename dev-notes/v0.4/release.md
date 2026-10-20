# 0.4 运行与验收说明

核对日期：2026-10-20。本文是 0.4 的运行与验收口径：怎么起、能做什么、哪些已验证、哪些没有。实施过程见[阶段记录](notes/README.md)，模块行为见[模块导航](modules/README.md)。

## 1. 运行

```powershell
uv run python -m hastings.server                 # 默认：打开最近使用的作品目录
uv run python -m hastings.server --workspace 目录  # 启动时打开指定作品
```

| 参数 | 说明 |
|---|---|
| `--workspace <目录>` | 启动时打开这个已有目录；不带时服务照常启动，由界面选择 |
| `--config <文件>` | 模型来源配置，默认 `./config.yaml`；缺失时回落环境变量 |
| `--model <名称>` | 覆盖启动档案的模型名 |
| `--static-dir <目录>` | 静态前端目录，缺省自动探测 `frontend/dist` |
| `--host` / `--port` | 默认 `127.0.0.1:8000` |

环境变量 `HASTINGS_STATE_DIR` 覆盖应用状态目录，默认 `~/.hastings`，存放最近目录、会话与事件；不含正文。

退出码：目录或前端目录不存在等环境故障 → 1；参数写错 → 2。

## 2. 模型来源

模型档案从 `config.yaml`（或环境变量）读取：

```yaml
providers:
  - name: default
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
    default_model: gpt-4o-mini
```

未配置时应用照常启动，发送消息的那一轮返回 `MODEL_NOT_CONFIGURED` 并说明怎么配。凭据优先走系统凭据存储，不可用时回落内存存储（重启后失效）。0.4 没有模型设置界面。

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

## 4. 已验收

| 项目 | 证据 |
|---|---|
| 目录规则、读写指纹、搜索、导入 | `uv run pytest tests/workspace -q`（130 项） |
| Git 初始化、基线、差异、提交、撤销、冲突与错误路径 | 同上 |
| 文件工具与轮次记录（含停止、中断、并发拒绝） | 同上 |
| 界面主链路 | `cd frontend && npx playwright test`（5 项，真服务 + Chromium） |
| 前端单元 | `cd frontend && npm test` |
| 类型与静态检查 | `uv run mypy src/hastings`、`uv run ruff check src/hastings tests/workspace` |

## 5. 未验收与缺口

- **真实模型未取证**：全部轮次用脚本化假 provider 验证。「作者一句写第一章 → 文件 + Git 记录」在浏览器里经真实模型完成一轮，仍需用真实凭据与临时作品单独验证。
- **没有 token 级流式**：回复整条显示。
- **没有模型设置界面**：只能改 `config.yaml` 或环境变量。
- **窄屏未适配**：固定三列，未迁移 0.3 的断点规则。
- **事件不跨进程广播**：SSE 轮询事件文件，多实例同时写同一作品不在本版范围。
- **撤销只覆盖最新一次提交**：非 HEAD 提交的撤销需在 Git 里手工处理。

## 6. 代码清理范围（V04-5）

0.3 的链路已删除：`domain/`（除仍在用的 `manuscript/diff.py` 与 `repository/serialization.py`）、`application/`（除 `errors.py` 与 `ports/credentials.py`）、`interfaces/`（除 `importers/manuscript_text.py`）、`infrastructure/`（除 `credentials.py`）、`agents/`、`bible/`、`tools/`、`prompts.py`、`session_store.py`、`rag/`、`manuscript/`、`settings.py`、`server/app.py`、`rest.py`、`v2.py`、`ws.py`、`registry.py`、`session.py`、`hastings_session.py`、`v2_agents.py`、`model_access.py`、`ollama.py`，以及对应的 `tests/` 与 `eval/` 驱动器。`hastings.__main__`（CLI 入口）随之退出。

仍在源码里的 0.3 组件：`frontend/src/components/ErrorBoundary.tsx`（新壳层在用）、`src/theme.ts`（Tailwind 取色）、`packaging/`（sidecar 打包规格，入口仍有效）。

0.3 文档保留为历史记录；其中描述的代码已不存在，不要据此推断可用能力。
