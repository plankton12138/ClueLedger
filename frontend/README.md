# Hastings 前端

当前默认入口为 **0.4 工作区**：作品是一个 Markdown 目录，界面只读文件、发起对话、查看 Git 改动与历史。产品目标见[产品设计](../product-design/README.md)，界面契约见[写作台界面](../dev-notes/v0.4/modules/desk.md)。

0.3 的写作台组件与状态仍在源码中（`components/desk/`、`state/v2*.ts`），默认页面已不引用，待 V04-5 清理；其说明在[0.3 模块文档](../dev-notes/v0.3/modules/desk-surface.md)。

## 运行

仓库根目录启动后端（默认打开最近使用的作品目录）：

```powershell
uv run python -m hastings.server
```

在本目录运行：

```powershell
npm install
npm run dev
npm run build
```

开发服务器将 `/api` 转发到本地 8000 端口。生产文件位于 `frontend/dist`，由后端托管。

## 当前可用流程

- **打开与新建**：顶栏「打开 / 新建」按本机路径操作，附最近打开列表；新建建立 `正文/`、`设定集/`、三份空设定与 Git 基线。
- **阅读**：左栏列正文各章与三份设定；中区是只读 Markdown 稿纸（不执行内嵌 HTML）。界面没有写正文的入口，修改走对话或外部编辑器。
- **搜索**：左栏逐行搜索正文与设定，结果含路径、行号与摘录。
- **对话**：右栏发送一轮请求；工具调用、回复与结果显示在同一个过程里。停止保留已写入文件；「撤销这次修改」「补记到历史」在结果卡上。
- **改动与历史**：抽屉显示未记录的改动、工作区差异、提交历史与某次提交的差异；可补记、可撤销最新一次提交。
- **外部编辑**：切回窗口或点「刷新」重新扫描目录；应用内没有文件监听。

## 模型

0.4 尚无模型设置界面：模型来源靠仓库根目录的 `config.yaml`（或环境变量），启动时可用 `--config` / `--model` 覆盖。未配置时应用照常启动，发送消息的那一轮会说明如何配置。模型接入界面属于后续阶段。

## 代码位置

| 位置 | 职责 |
|---|---|
| `src/App.tsx` | 挂载工作区壳层 |
| `src/components/workspace/` | 顶栏、章节目录、只读稿纸、对话栏、改动与历史抽屉、目录对话框 |
| `src/state/workspace.ts` | 目录、文件、对话、事件与 Git 的状态与动作 |
| `src/api/workspace.ts` | `/api/file-workspaces` 客户端与 SSE 解析 |
| `src/components/desk/`、`src/state/v2*.ts` | 0.3 写作台（默认页面已不引用，待清理） |

## 验证

```powershell
npm test
npm run build
node e2e/walkthrough-v04-workspace.mjs
```

浏览器走查需先按脚本头部说明启动后端；脚本自己通过真实接口准备作品，不调用模型，真实模型链路需单独取证。

本机沙箱下 vitest 偶发对系统临时目录 `ssr\*` 的 EPERM；把 `TMP`/`TEMP`/`TMPDIR` 指到仓库内的 `data/test-tmp/` 即可稳定运行。
