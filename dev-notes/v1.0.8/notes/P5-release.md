# P5 验收、清理与版本交付

日期：2026-11-02。对应 M6。

## 1. 版本

| 位置 | 值 |
|---|---|
| `pyproject.toml` | `1.0.8` |
| `src/hastings/__init__.py` | `1.0.8` |
| `frontend/package.json` | `1.0.8` |
| `frontend/package-lock.json` | 根项目两处 `1.0.8` |
| `uv.lock` | `hastings-ai` 为 `1.0.8` |

健康检查读取 `hastings.__version__`，随主包常量返回 1.0.8；`tests/workspace/test_release_recovery.py` 的版本断言同步更新。

## 2. 确定性验证

| 范围 | 结果 |
|---|---|
| 后端全量 | `uv run pytest -q`：1638 passed，1 warning，362.67s |
| 定向随章与焦点 | `test_chapter_context.py`、`test_discussion_focus.py`：12 passed |
| 前端类型与构建 | `npm run build`：tsc 通过，vite 构建通过 |
| 前端单元测试 | `npx vitest run`：35 个文件，351 passed |
| A20/A22 定向浏览器 | `large-workspace.spec.ts` + `aqua-layout.spec.ts`：3 passed，27.2s |
| Ruff / mypy | `ruff check src/hastings tests`、`mypy`：172 files 无错误 |

浏览器验收使用干净状态目录与独立端口，避免旧运行残留：

```
HASTINGS_E2E_PORT=8934
HASTINGS_E2E_DATA_DIR=data/test-tmp/e2e-v108-final
npm run test:e2e:local
```

结果：108 个用例中 107 passed、1 skipped，5.2 分钟；无失败。首次复用旧状态目录时出现 15 failed，个别夹具在 Windows 上写入共享 `conversations.json` 时遇到 `PermissionError`，模块库与构思页用例也受旧目录残留影响。单独用干净目录复验 `story-structure.spec.ts` 为 7 passed；完整复跑全部通过，说明失败来自旧状态目录与共享登记文件竞争，不是本版代码路径。

## 3. A20 与 A22 规模测量

样本：200 个构思节点、300 条关系、两章正文；进程内调用随章读取，不含 HTTP 与浏览器渲染。

| 指标 | 数值 |
|---|---|
| 结构提交（一次写入） | 86.5 ms |
| 当前方案读取 | 14.6 ms |
| 随章首页（50 条） | 15.8 ms，响应体 5.4 KB |
| 随章分页 | 100 条计划条目，2 页 |
| 浏览器首屏（`large-workspace.spec.ts`） | 318 ms；1 次聚合请求；0 次节点详情请求；响应体 2429 B；首屏 20 条，`truncated=true` |
| 320px 与 720px 视口 | 无横向溢出；720 CSS px 对应 1440 窗口 200% 浏览器缩放 |

浏览器首屏由 `frontend/e2e/large-workspace.spec.ts` 真实创建 200 节点 / 300 关系作品后测得，请求数和响应体来自 Playwright 监听，不是进程内估算。`aqua-layout.spec.ts` 补充 320px 与 720px 两档，写作页与模块库均检查发送 / 保存按钮可达且无横向溢出。本样本以计划章节而非正文绑定构造随章条目，测量的是聚合与分页路径；不代替真实作品的高密度出处场景。测量只说明当前规模下有界读取没有逐条全书请求，不作为其他硬件或更大作品的性能承诺。

## 3.1 在线模型验收

使用 `.env` 中的在线模型，低频单例执行；`pending_review` 由人工复核后重新评分。O01—O05 与 O11 通过：

| 用例 | 场景 | 运行目录 | 结果 |
|---|---|---|---|
| `v101_o01` | 空白谜题构思 | `context-20261102-141731-181382` | pass（27 个结构操作提交成功，真相保持未定） |
| `v101_o02` | 真实细节与误导 | `context-20261102-142225-645950` | pass（只读讨论，保留湿手套与搬尸真相） |
| `v101_o03` | 伪造证词 | `context-20261102-142403-554197` | pass（区分证词、误信与作者真相） |
| `v101_o04` | 倒叙与阅读顺序 | `context-20261102-142538-144985` | pass（实际事件顺序与揭示顺序分开） |
| `v101_o05` | 两条调查汇合 | `context-20261102-142638-047620` | pass（两条证据链汇合，明确不能证明搬运者与钟点） |
| `v101_o11` | 受限试读 | `context-20261102-143424-662218` | pass（仅截止前缀，0 次工具调用，未泄露后章解释） |

原 `v101_o06`「备选隔离与局部采用」无法在当前产品执行：1.0.1 期间的多方案能力已在 2026-10-30 按作者要求移除，模型不再获得 `create_story_variant` 和 `adopt_story_variant` 工具，页面与公开接口也已退出（见 [构思简化记录](../../v1.0.1/notes/2026-10-30-story-simplification.md)）。该用例已从 `v101` 套件退役，不以移除能力代替通过，也不重新引入备选功能。本版在线验收以 `v101_o01`—`v101_o05`、`v101_o11` 为证据，O06 位置的受限试读由 `v101_o11` 覆盖。

## 4. 清理

- 删除 `StoryCreationDialog`，构思空状态只进入对话。
- 删除前端仅旧表单使用的 `submitStoryChange`、`markSelection`、`acknowledge`、`submit` 与属性表单样式。
- 删除无界面消费者的 `addClueReference`；保留 `removeClueReference` 与已恢复草稿的线索引用展示。
- `POST /story/changes` 与 `POST /clues` 保留：无前端调用，但仍是测试、外部调用与对话工具共用的确定性 HTTP 写接口；撤销继续走 `POST /story/changes/{id}/undo`。
- 旧表单相关测试同步删除或改写；没有为兼容旧界面保留隐藏入口。

## 5. 未完成与边界

- 原 `v101_o06` 多方案用例随 1.0.1 的单方案简化退役，当前版本不提供创建、比较或采用备选的入口；相关证据与原因见上文第 3.1 节。
- 节点详情出处按绑定章节逐个读取，最多显示 8 章；更多章节提示「暂未显示」。
- 时间轴与关系图保持只读投影，不拖动、不补造时间或关系。
- 200% 缩放用 720 CSS px 视口等效验证，不覆盖不同浏览器或系统的实际缩放实现差异。

## 6. 工作树说明

- 浏览器用例会重写 `dev-notes/v0.8/artifacts` 与 `dev-notes/v1.0/artifacts` 下的跟踪截图。最后几轮使用临时截图目录，`clue-reference.spec.ts` 也改为尊重该环境变量；此前运行已经改写过的截图没有执行 `git restore`，不纳入本版代码交付。
- 根目录 `package.json`、`package-lock.json`、`release/` 与 `dev-notes/v1.0.6/anchor-model.html` 是开工前已有的未跟踪内容，本版没有修改。
- 根目录 `README.md` 与 `mynotes/` 未改。
