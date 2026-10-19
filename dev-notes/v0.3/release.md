# Hastings 0.3 运行与验收状态

包版本：`0.3.0`。当前默认入口为写作台，主要能力已实现，**完整验收尚未完成**。模块现状见[模块导航](modules/README.md)，逐项证据见[验收追踪](artifacts/v03-8.8-acceptance-tracking.md)。

本文的测试数字引用已保存的开发记录，本次文档核对未重跑测试或真实模型。当前限制直接写在 §5，不以历史“已交付”概括未接通的界面与执行链路。

## 1. 当前范围

无模型记录想法、导入预览、手工修改副本与跨章合并、候选预览采用、账本维护、背景装配、显式章摘要与账本建议、图谱、关联撤销和同作品内容恢复均有实现。

整体修订提供原文覆盖、预算、执行循环、暂存与统一发布接口；前端执行按钮和修正轮次触发点尚未接通。执行日志后端可用，前端面板尚未接通。

## 2. 运行说明

### 环境与模型

Python ≥3.12，所有 Python 命令经 `uv run`；前端 Node ≥18。

模型连接通过服务端配置与凭据存储管理。配置文件缺失、空文件或空档案等回退路径仍可读取 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`HASTINGS_MODEL`，未受专用开发开关限制；因此不能声称普通流程已完全禁止环境 Key 回退。未配置模型时服务可正常启动，非模型功能可用。

### 后端

在仓库根目录：

```powershell
uv run python -m hastings.server
uv run python -m hastings.server --v2-db data/v2.db
```

默认监听 `127.0.0.1:8000`。数据库默认复用 `data/v2.db` 或 `data/v2.sqlite`；都不存在时创建前者，两者同时存在须显式指定。启动日志打印实际路径。

启动恢复将执行中任务标中断或等待，旧自动队列隔离为 `historical_paused`；不自动重发模型调用。恢复摘要见 `GET /api/v2/recovery`。

### 前端

在 `frontend/`：

```powershell
npm install
npm run dev
npm run build
```

开发服务器将 `/api` 与 `/ws` 转发到后端；生产产物在 `frontend/dist`，由后端托管。开发聊天默认模拟，真实链路使用 `?chatMock=0`。

## 3. 验证方法与已有证据

仓库根目录：

```powershell
uv run pytest
uv run ruff check src/hastings tests
uv run mypy src/hastings
```

在 `frontend/`：

```powershell
npm test
npm run build
npm run test:e2e
```

真实模型评测方法见[评测导航](../../eval/README.md)。本地驱动需先安装可用模型：

```powershell
uv run python eval/v2/desk_driver.py --out eval/runs/<日期>-desk --model qwen3:8b --deep

远程 OpenAI 兼容端点（DeepSeek 等）：`--provider` 选 config.yaml 档案名，驱动会加载仓库根 `.env`：

```
uv run python eval/v2/desk_driver.py --out eval/runs/<日期>-desk-deepseek --provider default --deep
```

| 记录 | 已保存的结果 | 证据边界 |
|---|---|---|
| [执行循环取证](notes/v03-8.7-execution-loop.md) | 后端全量 1993 passed；本地模型报告 13 通过 + 2 证据项 | 其中包含环境失败与手动路径预算说明；不证明改写质量，也不证明远程服务可用 |
| [连贯场景补验](notes/v03-8.8-acceptance-scenarios.md) | 10 个用例通过 | 业务验收中 S5/S7 仍为部分覆盖；使用确定性模型替身 |
| [视觉走查](notes/v03-8.5-visual-walkthrough.md) | 37/37；前端单测 433 passed、构建通过 | 真实缩放与中文输入法以等价视口和合成事件代理 |
| [生产装配冒烟](notes/v03-8.3-production-startup-loop.md) | 同一装配链的创建、导入、修改、撤销、采用与恢复 | 同进程测试客户端；独立进程启动与请求日志见[整体调整面板](notes/v03-8.7-revision-panel.md) |
| [整体调整面板](notes/v03-8.7-revision-panel.md) | 真 uvicorn + DeepSeek 走查 8/8：开任务、预算停顿、继续、一次发布；前端 vitest 438、后端 1994 | 预算等待以可继续提示呈现；不证明改写质量 |

通过用例数量不等于所有产品验收条件通过。

## 4. 数据与兼容

作品内容备份：`GET /api/v2/projects/{id}/backup`；同作品恢复：`POST /api/v2/projects/{id}/restore`，存在未确认差异时返回 409。

备份包含正式正文、账本、约定、叙事结构与依赖边，不包含副本、任务暂存、操作记录、候选、执行日志和凭据。它不是数据库完整备份；覆盖与限制见[发布与恢复](modules/publish-import-recovery.md#3-备份与恢复)。

接口与存储格式版本独立于产品版本。旧自动任务不自动复活，旧数据保留不代表本版提供对应启动入口；兼容边界见[API 说明](modules/api-matrix-and-retirement.md)。

## 5. 剩余缺口与未验项

| 类型 | 当前情况 |
|---|---|
| 前端未接线 | 执行日志面板、旧逐段差异详情的当前界面承接 |
| 执行链路缺口 | 修正轮次 `start_repair` 没有执行触发点；执行是同步请求，没有逐章进度、请求进行中无法取消 |
| 上下文与摘要限制 | 全书概览生成、手工纠正与批量摘要入口不完整；披露检查依赖显式叙事位置且只做名称匹配 |
| 配置偏差 | 环境 Key 回退仍存在；删除连接后凭据引用清理未完成 |
| 产品范围待定 | V1 语料写接口仍可手工调用；节拍类型仍保留；保留 Job 角色的可达范围需与本版排除功能核对 |
| 运行时未验 | OpenAI 官方端点（DeepSeek 已实测）、真实浏览器缩放与中文输入法 |
| 验收覆盖边界 | 连贯场景 S5 的证据锚点失效、S7 的定位型披露拒绝由专项用例补充，不等于相同完整旅程已经跑通 |

## 6. 历史发布证据

开发阶段、改动与测试增量见[阶段交付记录](stage-delivery-record.md)及[执行循环笔记](notes/v03-8.7-execution-loop.md)。历史记录中的旧缺口、旧数量和修复过程不作为当前能力说明。
